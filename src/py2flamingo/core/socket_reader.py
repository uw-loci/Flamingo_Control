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
    CAMERA_EXPOSURE_GET = 0x300A  # 12298
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
    additional_data: Optional[bytes] = None  # Extra data following 128-byte message

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

        # Log laser/LED command responses at INFO level for debugging
        LASER_LED_COMMANDS = {0x2001, 0x2002, 0x2004, 0x2005, 0x2007, 0x4001, 0x4002, 0x4003}
        if command_code in LASER_LED_COMMANDS:
            logger.info(f"[RX] Laser/LED response received: code=0x{command_code:04X} ({message.command_name}), "
                       f"status={message.status_code}, int32Data0={message.int32_data0}")

        with self._lock:
            # Log pending requests when receiving laser commands (for debugging)
            if command_code in LASER_LED_COMMANDS:
                pending_codes = list(self._pending_requests.keys())
                logger.info(f"[RX] Pending requests: {[f'0x{c:04X}' for c in pending_codes]}")

            # Check if this is a response to a pending request
            if command_code in self._pending_requests:
                try:
                    self._pending_requests[command_code].put_nowait(message)
                    self._stats['responses_dispatched'] += 1
                    # Remove from pending after delivering
                    del self._pending_requests[command_code]
                    logger.info(f"[RX] Dispatched response for 0x{command_code:04X} to waiting caller")
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
            # Log at INFO for laser commands to help diagnose timing issues
            if command_code in LASER_LED_COMMANDS:
                logger.warning(f"[RX] Unhandled laser/LED message 0x{command_code:04X} - possible late response or no pending request")
            else:
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
        self._paused = False
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused initially
        self._paused_confirmed = threading.Event()
        self._paused_confirmed.set()  # Starts as "confirmed not paused"
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

    def pause(self, wait_timeout: float = 1.0):
        """
        Pause the reader to allow synchronous socket operations.

        The reader thread will stop reading from the socket until resume() is called.
        This blocks until the reader thread confirms it has paused.

        Args:
            wait_timeout: Max time to wait for reader to actually pause
        """
        with self._lock:
            if self._paused:
                return
            self._paused = True
            self._pause_event.clear()
            self._paused_confirmed.clear()  # Will be set by reader thread

        # Wait for the reader thread to confirm it's paused
        if not self._paused_confirmed.wait(timeout=wait_timeout):
            logger.warning(f"SocketReader pause confirmation timeout after {wait_timeout}s")
        else:
            logger.info("SocketReader paused for synchronous operation")

    def resume(self):
        """
        Resume the reader after a synchronous operation.

        Call this after completing synchronous socket operations to restart
        the background reading.
        """
        with self._lock:
            if not self._paused:
                return
            self._paused = False
            self._pause_event.set()
            logger.info("SocketReader resumed")

    def is_paused(self) -> bool:
        """Check if reader is paused."""
        return self._paused

    def _read_loop(self):
        """Main read loop - runs in background thread."""
        logger.info("SocketReader read loop starting")

        # Set socket timeout for graceful shutdown checks
        original_timeout = self._socket.gettimeout()
        self._socket.settimeout(0.5)  # 500ms timeout allows shutdown checks

        # Buffer for handling sync issues
        self._resync_buffer = b''
        consecutive_invalid = 0

        try:
            while self._running:
                # Check if paused - wait until resumed or stopped
                if not self._pause_event.is_set():
                    # Signal that we're now paused (not reading)
                    self._paused_confirmed.set()
                    # Wait for resume
                    if not self._pause_event.wait(timeout=0.5):
                        # Still paused, check if we should stop
                        if not self._running:
                            break
                        continue

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
                            # Check for additional data that follows the 128-byte message
                            # This MUST be read before the next message or we'll lose sync
                            additional_data = None
                            if message.additional_data_size > 0:
                                additional_data = self._read_additional_data(message.additional_data_size)
                                if additional_data:
                                    logger.debug(
                                        f"Read {len(additional_data)} additional bytes for "
                                        f"{message.command_name}"
                                    )

                            # Attach additional data to message if present
                            if additional_data:
                                message.additional_data = additional_data

                            self._dispatcher.dispatch(message)
                            self._stats['messages_read'] += 1
                            consecutive_invalid = 0  # Reset counter on valid message
                        else:
                            consecutive_invalid += 1
                            self._stats['parse_errors'] += 1

                            # Only log occasionally to avoid spam
                            if consecutive_invalid <= 3 or consecutive_invalid % 100 == 0:
                                logger.warning(
                                    f"Invalid message markers: start=0x{message.start_marker:08X}, "
                                    f"end=0x{message.end_marker:08X} (consecutive: {consecutive_invalid})"
                                )

                            # Try to resync if we get many consecutive invalid messages
                            if consecutive_invalid >= 5:
                                logger.info("Attempting to resync stream...")
                                if self._try_resync():
                                    consecutive_invalid = 0
                                    logger.info("Resync successful")

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

    def _try_resync(self) -> bool:
        """
        Attempt to resynchronize the message stream by finding the start marker.

        This is called when we receive multiple consecutive invalid messages,
        indicating we've lost sync with the 128-byte message boundaries.

        Returns:
            True if resync was successful, False otherwise
        """
        # Start marker bytes in little-endian
        START_MARKER_BYTES = struct.pack('<I', self.START_MARKER)  # 0xF321E654

        # Read up to 512 bytes looking for the start marker
        try:
            search_data = self._socket.recv(512)
            if not search_data:
                return False

            self._stats['bytes_read'] += len(search_data)

            # Look for start marker in the data
            marker_pos = search_data.find(START_MARKER_BYTES)
            if marker_pos == -1:
                logger.debug(f"Start marker not found in {len(search_data)} bytes")
                return False

            logger.debug(f"Found start marker at offset {marker_pos}")

            # Read the rest of the message (128 - 4 bytes for marker already found)
            # Plus account for data after the marker we already have
            data_after_marker = search_data[marker_pos:]
            bytes_needed = self.MESSAGE_SIZE - len(data_after_marker)

            if bytes_needed > 0:
                additional = b''
                while len(additional) < bytes_needed:
                    chunk = self._socket.recv(bytes_needed - len(additional))
                    if not chunk:
                        return False
                    additional += chunk
                    self._stats['bytes_read'] += len(chunk)
                full_message = data_after_marker + additional
            else:
                full_message = data_after_marker[:self.MESSAGE_SIZE]

            # Verify this is a valid message
            message = self._parse_message(full_message)
            if message.is_valid:
                self._dispatcher.dispatch(message)
                self._stats['messages_read'] += 1
                return True
            else:
                logger.debug("Resync found marker but message still invalid")
                return False

        except socket.timeout:
            return False
        except Exception as e:
            logger.error(f"Error during resync: {e}")
            return False

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

    def _read_additional_data(self, size: int) -> Optional[bytes]:
        """
        Read additional data that follows a 128-byte message.

        Some commands return extra data beyond the standard 128-byte response.
        This data MUST be read before the next message or we'll lose sync.

        Args:
            size: Number of additional bytes to read

        Returns:
            Additional data bytes, or None on error/timeout
        """
        if size <= 0:
            return None

        data = b''
        try:
            while len(data) < size:
                remaining = size - len(data)
                chunk = self._socket.recv(remaining)

                if not chunk:
                    logger.warning(f"Socket closed while reading additional data ({len(data)}/{size})")
                    return None

                data += chunk
                self._stats['bytes_read'] += len(chunk)

            return data

        except socket.timeout:
            logger.warning(f"Timeout reading additional data ({len(data)}/{size})")
            return data if data else None
        except Exception as e:
            logger.error(f"Error reading additional data: {e}")
            return None

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

    def pause(self):
        """Pause the reader for synchronous operations."""
        self._reader.pause()

    def resume(self):
        """Resume the reader after synchronous operations."""
        self._reader.resume()

    def is_paused(self) -> bool:
        """Check if reader is paused."""
        return self._reader.is_paused()

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
