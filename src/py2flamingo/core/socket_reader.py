"""
Background Socket Reader for Flamingo Microscope Protocol.

This module provides a continuous background reader that drains the socket
and dispatches messages to appropriate queues. This prevents socket buffer
buildup and ensures unsolicited messages (like STAGE_MOTION_STOPPED) are
never missed.

Architecture:
    SocketReader (background thread)
        └── Continuously reads 128-byte messages
        └── Parses and routes to MessageDispatcher

    MessageDispatcher
        └── Routes responses to pending command queues
        └── Routes unsolicited messages to callback handlers
        └── Logs unhandled messages for debugging
"""

import logging
import queue
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set
from enum import IntEnum

logger = logging.getLogger(__name__)


class ProtocolCommands(IntEnum):
    """Known command codes from the Flamingo protocol (for socket reader use)."""
    # Stage commands
    STAGE_HOME = 0x6001  # 24577
    STAGE_HALT = 0x6002  # 24578
    STAGE_POSITION_SET_MOVE = 0x6004  # 24580
    STAGE_POSITION_SET = 0x6005  # 24581
    STAGE_VELOCITY_SET = 0x6006  # 24582
    STAGE_POSITION_GET = 0x6008  # 24584
    STAGE_SAVE_LOCATIONS_GET = 0x6009  # 24585
    STAGE_SAVE_LOCATIONS_SET = 0x600a  # 24586
    STAGE_WAIT_FOR_MOTION = 0x600f  # 24591
    STAGE_MOTION_STOPPED = 0x6010  # 24592 - UNSOLICITED CALLBACK

    # System commands
    SYSTEM_STATE_GET = 0xa007  # 40967
    SYSTEM_STATE_IDLE = 0xa002  # 40962
    SYSTEM_SCOPE_SETTINGS = 0x1007  # 4103

    # Camera commands
    CAMERA_EXPOSURE_SET = 0x3001  # 12289
    CAMERA_EXPOSURE_GET = 0x3002  # 12290
    CAMERA_WORKFLOW_START = 0x3004  # 12292
    CAMERA_WORKFLOW_STOP = 0x3005  # 12293
    CAMERA_SNAPSHOT = 0x3006  # 12294
    CAMERA_LIVE_VIEW_START = 0x3007  # 12295
    CAMERA_LIVE_VIEW_STOP = 0x3008  # 12296
    CAMERA_IMAGE_SIZE_GET = 0x3027  # 12327
    CAMERA_PIXEL_SIZE_GET = 0x3042  # 12354
    CAMERA_FIELD_OF_VIEW_GET = 0x3037  # 12343

    # Laser commands
    LASER_LEVEL_SET = 0x2001  # 8193
    LASER_PREVIEW_ENABLE = 0x2004  # 8196
    LASER_PREVIEW_DISABLE = 0x2005  # 8197
    LASER_ALL_DISABLE = 0x2007  # 8199

    # LED commands
    LED_SET = 0x4001  # 16385
    LED_ENABLE = 0x4002  # 16386
    LED_DISABLE = 0x4003  # 16387

    # Filter wheel
    FILTER_POSITION_SET = 0x5001  # 20481
    FILTER_POSITION_GET = 0x5002  # 20482

    # Illumination (TSPIM)
    ILLUMINATION_LEFT_ENABLE = 0x7004  # 28676
    ILLUMINATION_LEFT_DISABLE = 0x7005  # 28677
    ILLUMINATION_RIGHT_ENABLE = 0x7006  # 28678
    ILLUMINATION_RIGHT_DISABLE = 0x7007  # 28679


# Set of command codes that are unsolicited (sent by microscope without request)
UNSOLICITED_COMMANDS: Set[int] = {
    ProtocolCommands.STAGE_MOTION_STOPPED,
    # Add more unsolicited commands as discovered
}


@dataclass
class ParsedMessage:
    """Parsed 128-byte protocol message."""
    raw_data: bytes
    start_marker: int
    command_code: int
    status_code: int
    hardware_id: int
    subsystem_id: int
    client_id: int
    int32_data0: int  # Often axis or laser index
    int32_data1: int
    int32_data2: int
    cmd_data_bits: int
    value: float  # doubleData field
    additional_data_size: int
    data_field: bytes  # 72-byte data buffer
    end_marker: int
    timestamp: float = field(default_factory=time.time)

    @property
    def is_valid(self) -> bool:
        """Check if message has valid markers."""
        return (self.start_marker == 0xF321E654 and
                self.end_marker == 0xFEDC4321)

    @property
    def is_unsolicited(self) -> bool:
        """Check if this is an unsolicited callback message."""
        return self.command_code in UNSOLICITED_COMMANDS

    @property
    def command_name(self) -> str:
        """Get human-readable command name."""
        try:
            return ProtocolCommands(self.command_code).name
        except ValueError:
            return f"UNKNOWN_0x{self.command_code:04X}"


class MessageDispatcher:
    """
    Routes parsed messages to appropriate handlers.

    - Command responses go to pending request queues
    - Unsolicited callbacks go to registered handlers
    - Unknown messages are logged
    """

    def __init__(self):
        self._lock = threading.Lock()

        # Pending requests: command_code -> Queue
        # When a command is sent, a queue is registered here
        # The background reader puts responses in the queue
        self._pending_requests: Dict[int, queue.Queue] = {}

        # Callback handlers: command_code -> list of handlers
        self._callback_handlers: Dict[int, List[Callable[[ParsedMessage], None]]] = {}

        # Queue for unsolicited messages (backup if no handler registered)
        self._unsolicited_queue: queue.Queue = queue.Queue(maxsize=100)

        # Statistics for debugging
        self._stats = {
            'messages_received': 0,
            'responses_dispatched': 0,
            'callbacks_dispatched': 0,
            'messages_dropped': 0,
        }

    def register_pending_request(self, command_code: int) -> queue.Queue:
        """
        Register a pending request and return a queue to wait on.

        Args:
            command_code: The command code we expect a response for

        Returns:
            Queue that will receive the response
        """
        response_queue = queue.Queue(maxsize=1)
        with self._lock:
            # If there's already a pending request for this code, log warning
            if command_code in self._pending_requests:
                logger.warning(f"Overwriting pending request for command 0x{command_code:04X}")
            self._pending_requests[command_code] = response_queue
        return response_queue

    def unregister_pending_request(self, command_code: int):
        """Remove a pending request (for cleanup on timeout)."""
        with self._lock:
            self._pending_requests.pop(command_code, None)

    def register_callback_handler(self, command_code: int,
                                   handler: Callable[[ParsedMessage], None]):
        """
        Register a handler for unsolicited callback messages.

        Args:
            command_code: Command code to handle (e.g., STAGE_MOTION_STOPPED)
            handler: Function that takes ParsedMessage
        """
        with self._lock:
            if command_code not in self._callback_handlers:
                self._callback_handlers[command_code] = []
            self._callback_handlers[command_code].append(handler)
            logger.info(f"Registered callback handler for 0x{command_code:04X}")

    def unregister_callback_handler(self, command_code: int,
                                     handler: Callable[[ParsedMessage], None]):
        """Remove a callback handler."""
        with self._lock:
            if command_code in self._callback_handlers:
                try:
                    self._callback_handlers[command_code].remove(handler)
                except ValueError:
                    pass

    def dispatch(self, message: ParsedMessage):
        """
        Route a message to the appropriate queue or handler.

        Args:
            message: Parsed message to dispatch
        """
        self._stats['messages_received'] += 1
        command_code = message.command_code

        with self._lock:
            # Check if this is a response to a pending request
            if command_code in self._pending_requests:
                try:
                    self._pending_requests[command_code].put_nowait(message)
                    self._stats['responses_dispatched'] += 1
                    # Remove from pending after delivering
                    del self._pending_requests[command_code]
                    logger.debug(f"Dispatched response for 0x{command_code:04X}")
                    return
                except queue.Full:
                    logger.error(f"Response queue full for 0x{command_code:04X}")

            # Check if this is an unsolicited callback with handlers
            if command_code in self._callback_handlers:
                handlers = self._callback_handlers[command_code].copy()

        # Call handlers outside the lock to prevent deadlocks
        if command_code in self._callback_handlers:
            for handler in handlers:
                try:
                    handler(message)
                    self._stats['callbacks_dispatched'] += 1
                except Exception as e:
                    logger.error(f"Callback handler error for 0x{command_code:04X}: {e}")
            return

        # No handler found - queue as unsolicited or log
        if message.is_unsolicited:
            try:
                self._unsolicited_queue.put_nowait(message)
                logger.debug(f"Queued unsolicited message 0x{command_code:04X}")
            except queue.Full:
                self._stats['messages_dropped'] += 1
                logger.warning(f"Unsolicited queue full, dropping 0x{command_code:04X}")
        else:
            # This could be a response that arrived after timeout
            logger.debug(f"Unhandled message 0x{command_code:04X} (late response?)")

    def get_stats(self) -> Dict[str, int]:
        """Get dispatch statistics."""
        return self._stats.copy()


class SocketReader:
    """
    Background thread that continuously reads from the command socket.

    This prevents socket buffer buildup and ensures all messages are
    captured, including unsolicited callbacks like STAGE_MOTION_STOPPED.
    """

    # Protocol constants
    MESSAGE_SIZE = 128
    START_MARKER = 0xF321E654
    END_MARKER = 0xFEDC4321

    def __init__(self, command_socket: socket.socket, dispatcher: MessageDispatcher):
        """
        Initialize the socket reader.

        Args:
            command_socket: The command socket to read from
            dispatcher: MessageDispatcher to route messages
        """
        self._socket = command_socket
        self._dispatcher = dispatcher
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Statistics
        self._stats = {
            'messages_read': 0,
            'parse_errors': 0,
            'socket_errors': 0,
            'bytes_read': 0,
        }

    def start(self):
        """Start the background reader thread."""
        with self._lock:
            if self._running:
                logger.warning("SocketReader already running")
                return

            self._running = True
            self._thread = threading.Thread(
                target=self._read_loop,
                name="SocketReader",
                daemon=True
            )
            self._thread.start()
            logger.info("SocketReader background thread started")

    def stop(self, timeout: float = 2.0):
        """
        Stop the background reader thread.

        Args:
            timeout: Seconds to wait for thread to stop
        """
        with self._lock:
            if not self._running:
                return
            self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("SocketReader thread did not stop cleanly")

        logger.info("SocketReader stopped")

    def is_running(self) -> bool:
        """Check if reader is running."""
        return self._running

    def _read_loop(self):
        """Main read loop - runs in background thread."""
        logger.info("SocketReader read loop starting")

        # Set socket timeout for graceful shutdown checks
        original_timeout = self._socket.gettimeout()
        self._socket.settimeout(0.5)  # 500ms timeout allows shutdown checks

        try:
            while self._running:
                try:
                    # Read exactly 128 bytes
                    data = self._receive_message()

                    if data is None:
                        # Timeout - check if we should continue
                        continue

                    if len(data) == 0:
                        # Socket closed
                        logger.error("Socket closed - reader stopping")
                        break

                    # Parse and dispatch
                    try:
                        message = self._parse_message(data)
                        if message.is_valid:
                            self._dispatcher.dispatch(message)
                            self._stats['messages_read'] += 1
                        else:
                            logger.warning(f"Invalid message markers: start=0x{message.start_marker:08X}, "
                                         f"end=0x{message.end_marker:08X}")
                            self._stats['parse_errors'] += 1
                    except Exception as e:
                        logger.error(f"Message parse error: {e}")
                        self._stats['parse_errors'] += 1

                except socket.timeout:
                    # Normal timeout - just continue loop
                    continue

                except socket.error as e:
                    if self._running:
                        logger.error(f"Socket error in reader: {e}")
                        self._stats['socket_errors'] += 1
                    break

                except Exception as e:
                    if self._running:
                        logger.error(f"Unexpected error in reader: {e}", exc_info=True)
                    break

        finally:
            # Restore original timeout
            try:
                self._socket.settimeout(original_timeout)
            except:
                pass
            logger.info(f"SocketReader read loop exiting. Stats: {self._stats}")

    def _receive_message(self) -> Optional[bytes]:
        """
        Receive exactly 128 bytes from the socket.

        Returns:
            128 bytes of data, empty bytes if socket closed, None on timeout
        """
        data = b''
        while len(data) < self.MESSAGE_SIZE:
            try:
                remaining = self.MESSAGE_SIZE - len(data)
                chunk = self._socket.recv(remaining)

                if not chunk:
                    # Socket closed
                    return b''

                data += chunk
                self._stats['bytes_read'] += len(chunk)

            except socket.timeout:
                if len(data) == 0:
                    # No data at all - just a timeout
                    return None
                # Partial data received, continue trying
                continue

        return data

    def _parse_message(self, data: bytes) -> ParsedMessage:
        """
        Parse 128-byte protocol message.

        Args:
            data: 128 bytes of raw message data

        Returns:
            ParsedMessage object
        """
        if len(data) != self.MESSAGE_SIZE:
            raise ValueError(f"Invalid message size: {len(data)}")

        # Unpack header fields
        start_marker = struct.unpack('<I', data[0:4])[0]
        command_code = struct.unpack('<I', data[4:8])[0]
        status_code = struct.unpack('<I', data[8:12])[0]

        # Unpack parameter fields (7 x 4 bytes = 28 bytes at offset 12-39)
        hardware_id = struct.unpack('<I', data[12:16])[0]
        subsystem_id = struct.unpack('<I', data[16:20])[0]
        client_id = struct.unpack('<I', data[20:24])[0]
        int32_data0 = struct.unpack('<i', data[24:28])[0]  # Signed - often axis
        int32_data1 = struct.unpack('<i', data[28:32])[0]
        int32_data2 = struct.unpack('<i', data[32:36])[0]
        cmd_data_bits = struct.unpack('<I', data[36:40])[0]

        # Unpack value (double at offset 40-47)
        value = struct.unpack('<d', data[40:48])[0]

        # Additional data size (at offset 48-51)
        additional_data_size = struct.unpack('<I', data[48:52])[0]

        # Data field (72 bytes at offset 52-123)
        data_field = data[52:124]

        # End marker (at offset 124-127)
        end_marker = struct.unpack('<I', data[124:128])[0]

        return ParsedMessage(
            raw_data=data,
            start_marker=start_marker,
            command_code=command_code,
            status_code=status_code,
            hardware_id=hardware_id,
            subsystem_id=subsystem_id,
            client_id=client_id,
            int32_data0=int32_data0,
            int32_data1=int32_data1,
            int32_data2=int32_data2,
            cmd_data_bits=cmd_data_bits,
            value=value,
            additional_data_size=additional_data_size,
            data_field=data_field,
            end_marker=end_marker
        )

    def get_stats(self) -> Dict[str, int]:
        """Get reader statistics."""
        return self._stats.copy()


class CommandClient:
    """
    High-level client for sending commands and receiving responses.

    Uses the background SocketReader and MessageDispatcher for
    non-blocking command-response handling.
    """

    def __init__(self, command_socket: socket.socket):
        """
        Initialize the command client.

        Args:
            command_socket: Socket connected to microscope command port
        """
        self._socket = command_socket
        self._dispatcher = MessageDispatcher()
        self._reader = SocketReader(command_socket, self._dispatcher)
        self._send_lock = threading.Lock()  # Serialize command sends

    def start(self):
        """Start the background reader."""
        self._reader.start()

    def stop(self):
        """Stop the background reader."""
        self._reader.stop()

    def is_running(self) -> bool:
        """Check if client is running."""
        return self._reader.is_running()

    def send_command(self, command_bytes: bytes,
                     expected_response_code: int,
                     timeout: float = 3.0) -> Optional[ParsedMessage]:
        """
        Send a command and wait for response.

        Args:
            command_bytes: 128-byte command to send
            expected_response_code: Command code expected in response
            timeout: Seconds to wait for response

        Returns:
            ParsedMessage response, or None on timeout
        """
        # Register for response before sending
        response_queue = self._dispatcher.register_pending_request(expected_response_code)

        try:
            # Send command (serialized to prevent interleaving)
            with self._send_lock:
                self._socket.sendall(command_bytes)

            # Wait for response
            try:
                message = response_queue.get(timeout=timeout)
                return message
            except queue.Empty:
                logger.warning(f"Timeout waiting for response to 0x{expected_response_code:04X}")
                return None

        finally:
            # Clean up pending request
            self._dispatcher.unregister_pending_request(expected_response_code)

    def register_callback(self, command_code: int,
                          handler: Callable[[ParsedMessage], None]):
        """
        Register a handler for unsolicited callback messages.

        Args:
            command_code: Command code to handle
            handler: Function that takes ParsedMessage
        """
        self._dispatcher.register_callback_handler(command_code, handler)

    def unregister_callback(self, command_code: int,
                            handler: Callable[[ParsedMessage], None]):
        """Remove a callback handler."""
        self._dispatcher.unregister_callback_handler(command_code, handler)

    @property
    def dispatcher(self) -> MessageDispatcher:
        """Access the message dispatcher."""
        return self._dispatcher

    def get_stats(self) -> Dict[str, Any]:
        """Get combined statistics."""
        return {
            'reader': self._reader.get_stats(),
            'dispatcher': self._dispatcher.get_stats(),
        }
