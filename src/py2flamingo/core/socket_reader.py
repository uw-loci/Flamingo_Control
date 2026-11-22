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
from enum import IntEnum
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Optional,
    Union,
)

from py2flamingo.core.command_codes import (  # noqa: F401 - re-exported
    FIXED,
    UNSOLICITED_COMMANDS,
)
from py2flamingo.core.frames import ParsedMessage, parse_frame

logger = logging.getLogger(__name__)

#: How long the reader waits for the next byte of a message's additional data.
ADDITIONAL_DATA_TIMEOUT_S = 10.0


class ProtocolCommands(IntEnum):
    """Command codes the reader names in its own log lines.

    Values are the one table's (core/command_codes.FIXED, from the server
    header). Names for received codes come from
    ``command_codes.command_name``, which also knows the COMMON block of the
    connected build; this enum is kept for the code that names members.
    """

    STAGE_HOME = FIXED["STAGE_HOME"]
    STAGE_HALT = FIXED["STAGE_HALT"]
    STAGE_POSITION_SET_MOVE = FIXED["STAGE_POSITION_SET"]  # the move
    STAGE_POSITION_SET_SLIDER = FIXED["STAGE_POSITION_SET_SLIDER"]
    STAGE_VELOCITY_SET = FIXED["STAGE_VELOCITY_SET"]
    STAGE_POSITION_GET = FIXED["STAGE_POSITION_GET"]
    STAGE_VELOCITY_GET = FIXED["STAGE_VELOCITY_GET"]
    STAGE_WAIT_FOR_MOTION = FIXED["STAGE_WAIT_FOR_MOTION_TO_STOP"]
    STAGE_MOTION_STOPPED = FIXED["STAGE_MOTION_STOPPED"]  # unsolicited

    SYSTEM_STATE_GET = FIXED["SYSTEM_STATE_GET"]
    SYSTEM_STATE_IDLE = FIXED["SYSTEM_STATE_IDLE"]

    CAMERA_EXPOSURE_SET = FIXED["CAMERA_EXPOSURE_SET"]
    CAMERA_STACK_COMPLETE = FIXED["CAMERA_STACK_COMPLETE"]
    CAMERA_EXPOSURE_GET = FIXED["CAMERA_EXPOSURE_GET"]
    CAMERA_WORKFLOW_START = FIXED["CAMERA_WORK_FLOW_START"]
    CAMERA_WORKFLOW_STOP = FIXED["CAMERA_WORK_FLOW_STOP"]
    CAMERA_SNAPSHOT = FIXED["CAMERA_SNAPSHOT_GET"]
    CAMERA_LIVE_VIEW_START = FIXED["CAMERA_LIVE_VIEW_START"]
    CAMERA_LIVE_VIEW_STOP = FIXED["CAMERA_LIVE_VIEW_STOP"]
    CAMERA_IMAGE_SIZE_GET = FIXED["CAMERA_IMAGE_SIZE_GET"]
    CAMERA_PIXEL_FIELD_OF_VIEW_GET = FIXED["CAMERA_PIXEL_FIELD_OF_VIEW_GET"]

    LASER_LEVEL_SET = FIXED["LASER_LEVEL_SET"]
    # Also sent as unsolicited status during a workflow
    LASER_LEVEL_GET = FIXED["LASER_LEVEL_GET"]
    LASER_PREVIEW_ENABLE = FIXED["LASER_ENABLE_PREVIEW"]
    # 0x2005: the server's LASER_ENABLE_LINE (was named LASER_PREVIEW_DISABLE)
    LASER_ENABLE_LINE = FIXED["LASER_ENABLE_LINE"]
    LASER_ALL_DISABLE = FIXED["LASER_DISABLE_ALL"]

    LED_SET = FIXED["LED_SET"]
    LED_ENABLE = FIXED["LED_PREVIEW_ENABLE"]
    LED_DISABLE = FIXED["LED_PREVIEW_DISABLE"]

    # The server's SERVO block (0x5001/0x5002 are TRIGGER codes)
    FILTER_POSITION_SET = FIXED["SERVO_FILTER_WHEEL_POSITION_SET"]
    FILTER_MOTION_STOPPED = FIXED["SERVO_FILTER_WHEEL_MOTION_STOPPED"]

    ILLUMINATION_LEFT_ENABLE = FIXED["ILLUMINATION_CONTROL_LEFT_ENABLED"]
    ILLUMINATION_LEFT_DISABLE = FIXED["ILLUMINATION_CONTROL_LEFT_DISABLED"]
    ILLUMINATION_RIGHT_ENABLE = FIXED["ILLUMINATION_CONTROL_RIGHT_ENABLED"]
    ILLUMINATION_RIGHT_DISABLE = FIXED["ILLUMINATION_CONTROL_RIGHT_DISABLED"]


class PendingRequest:
    """One caller waiting for a reply.

    ``codes`` are the reply codes that can answer it; ``predicate``, when
    given, must also accept the message (POSITION_GET, for example, takes
    only a reply that echoes its axis in int32Data0). The reply is put on
    an internal queue; :meth:`get` waits for it.
    """

    __slots__ = ("codes", "predicate", "_queue")

    def __init__(
        self,
        codes: FrozenSet[int],
        predicate: Optional[Callable[[ParsedMessage], bool]] = None,
    ):
        self.codes = codes
        self.predicate = predicate
        self._queue: "queue.Queue[ParsedMessage]" = queue.Queue(maxsize=1)

    def accepts(self, message: ParsedMessage) -> bool:
        if message.command_code not in self.codes:
            return False
        if self.predicate is None:
            return True
        try:
            return bool(self.predicate(message))
        except Exception as e:  # noqa: BLE001 - a bad predicate matches nothing
            logger.error(
                f"Reply predicate raised for 0x{message.command_code:04X}: {e}"
            )
            return False

    def deliver(self, message: ParsedMessage) -> None:
        self._queue.put_nowait(message)

    def get(self, timeout: Optional[float] = None) -> ParsedMessage:
        """Wait for the reply. Raises ``queue.Empty`` on timeout."""
        return self._queue.get(timeout=timeout)


class MessageDispatcher:
    """
    Routes parsed messages to appropriate handlers.

    - A reply goes to the oldest pending request for its code that accepts
      it (a FIFO per code, each request with an optional match predicate)
    - Unsolicited callbacks go to registered handlers
    - Unknown messages are logged
    """

    def __init__(self):
        self._lock = threading.Lock()

        # Pending requests: command_code -> requests waiting on it, oldest
        # first. A request registered for several codes is in each list.
        self._pending_requests: Dict[int, List[PendingRequest]] = {}

        # Callback handlers: command_code -> list of handlers
        self._callback_handlers: Dict[int, List[Callable[[ParsedMessage], None]]] = {}

        # Queue for unsolicited messages (backup if no handler registered)
        self._unsolicited_queue: queue.Queue = queue.Queue(maxsize=100)

        # Statistics for debugging
        self._stats = {
            "messages_received": 0,
            "responses_dispatched": 0,
            "callbacks_dispatched": 0,
            "messages_dropped": 0,
        }

    def register_pending_request(
        self,
        command_code: Union[int, Iterable[int]],
        predicate: Optional[Callable[[ParsedMessage], bool]] = None,
    ) -> PendingRequest:
        """
        Register a pending request and return it to wait on (``.get(timeout)``).

        Several requests may wait on the same code at once: each reply goes
        to the oldest one that accepts it. Always pass the returned request
        to :meth:`unregister_pending_request` when done (a timeout leaves it
        registered otherwise).

        Args:
            command_code: The reply code expected, or several (any answers)
            predicate: Optional test the reply must also pass
        """
        codes = (
            frozenset({int(command_code)})
            if isinstance(command_code, int)
            else frozenset(int(c) for c in command_code)
        )
        request = PendingRequest(codes, predicate)
        with self._lock:
            for code in codes:
                self._pending_requests.setdefault(code, []).append(request)
        return request

    def unregister_pending_request(self, request: PendingRequest) -> None:
        """Remove one pending request (after its reply, or on timeout)."""
        with self._lock:
            self._remove_unlocked(request)

    def _remove_unlocked(self, request: PendingRequest) -> None:
        for code in request.codes:
            waiting = self._pending_requests.get(code)
            if not waiting:
                continue
            try:
                waiting.remove(request)
            except ValueError:
                pass
            if not waiting:
                del self._pending_requests[code]

    def pending_count(self, command_code: int) -> int:
        """How many requests wait on ``command_code`` (for tests and logs)."""
        with self._lock:
            return len(self._pending_requests.get(command_code, ()))

    def register_callback_handler(
        self, command_code: int, handler: Callable[[ParsedMessage], None]
    ):
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

    def unregister_callback_handler(
        self, command_code: int, handler: Callable[[ParsedMessage], None]
    ):
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
        self._stats["messages_received"] += 1
        command_code = message.command_code

        # Log laser/LED command responses at INFO level for debugging
        LASER_LED_COMMANDS = {
            0x2001,
            0x2002,
            0x2004,
            0x2005,
            0x2007,
            0x4001,
            0x4002,
            0x4003,
        }
        if command_code in LASER_LED_COMMANDS:
            logger.info(
                f"[RX] Laser/LED response received: code=0x{command_code:04X} ({message.command_name}), "
                f"status={message.status_code}, int32Data0={message.int32_data0}"
            )

        with self._lock:
            # Log pending requests when receiving laser commands (for debugging)
            if command_code in LASER_LED_COMMANDS:
                pending_codes = list(self._pending_requests.keys())
                logger.info(
                    f"[RX] Pending requests: {[f'0x{c:04X}' for c in pending_codes]}"
                )

            # A reply goes to the OLDEST pending request for its code that
            # accepts it, and only to that one.
            for request in self._pending_requests.get(command_code, ()):
                if request.accepts(message):
                    self._remove_unlocked(request)
                    try:
                        request.deliver(message)
                        self._stats["responses_dispatched"] += 1
                        logger.debug(
                            f"[RX] Dispatched response for 0x{command_code:04X} "
                            f"to waiting caller"
                        )
                        return
                    except queue.Full:
                        logger.error(f"Response queue full for 0x{command_code:04X}")
                    break

            # Check if this is an unsolicited callback with handlers
            if command_code in self._callback_handlers:
                handlers = self._callback_handlers[command_code].copy()

        # Call handlers outside the lock to prevent deadlocks
        if command_code in self._callback_handlers:
            for handler in handlers:
                try:
                    handler(message)
                    self._stats["callbacks_dispatched"] += 1
                except Exception as e:
                    logger.error(
                        f"Callback handler error for 0x{command_code:04X}: {e}"
                    )
            return

        # No handler found - queue as unsolicited or log
        if message.is_unsolicited:
            # High-frequency unsolicited messages flood the queue during
            # acquisition — drop silently when no callback is registered.
            # 0x9004 = progress bar update, 0x9003 = progress bar size,
            # 0x6010 = MOTION_STOPPED (stage moves between tiles)
            if command_code in (0x9003, 0x9004, 0x6010):
                self._stats["messages_dropped"] += 1
                return
            try:
                self._unsolicited_queue.put_nowait(message)
                logger.debug(f"Queued unsolicited message 0x{command_code:04X}")
            except queue.Full:
                self._stats["messages_dropped"] += 1
                logger.warning(f"Unsolicited queue full, dropping 0x{command_code:04X}")
        else:
            # This could be:
            # 1. Unsolicited status update during workflow execution (LED_DISABLE, LASER_LEVEL_GET)
            # 2. A response that arrived after timeout
            # Log at DEBUG - these are normal during workflow
            if command_code in LASER_LED_COMMANDS:
                logger.debug(
                    f"[RX] Unsolicited laser/LED status: 0x{command_code:04X} ({message.command_name})"
                )
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

    def __init__(
        self,
        command_socket: socket.socket,
        dispatcher: MessageDispatcher,
        on_closed: Optional[Callable[[str], None]] = None,
    ):
        """
        Initialize the socket reader.

        Args:
            command_socket: The command socket to read from
            dispatcher: MessageDispatcher to route messages
            on_closed: Optional callback invoked (with a reason string)
                when the reader detects the socket has been closed or
                encounters a fatal socket error. Fires once per reader
                lifetime. Allows higher layers to update connection
                state reactively without polling.
        """
        self._socket = command_socket
        self._dispatcher = dispatcher
        self._on_closed = on_closed
        self._closed_notified = False
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
            "messages_read": 0,
            "parse_errors": 0,
            "socket_errors": 0,
            "bytes_read": 0,
        }

    def _notify_closed(self, reason: str):
        """Invoke the on_closed callback once, swallowing any errors."""
        if self._closed_notified:
            return
        self._closed_notified = True
        if self._on_closed is not None:
            try:
                self._on_closed(reason)
            except Exception as e:
                logger.error(f"on_closed callback raised: {e}", exc_info=True)

    def start(self):
        """Start the background reader thread."""
        with self._lock:
            if self._running:
                logger.warning("SocketReader already running")
                return

            self._running = True
            self._thread = threading.Thread(
                target=self._read_loop, name="SocketReader", daemon=True
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
            logger.warning(
                f"SocketReader pause confirmation timeout after {wait_timeout}s"
            )
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
        self._resync_buffer = b""
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
                        # Socket closed by remote (OS keepalive detected dead peer,
                        # or server-initiated close)
                        logger.warning(
                            "SocketReader: connection closed by remote - reader stopping"
                        )
                        self._notify_closed("remote closed connection")
                        break

                    # Parse and dispatch
                    try:
                        message = self._parse_message(data)
                        if message.is_valid:
                            # Check for additional data that follows the 128-byte message
                            # This MUST be read before the next message or we'll lose sync
                            additional_data = None
                            if message.additional_data_size > 0:
                                additional_data = self._read_additional_data(
                                    message.additional_data_size
                                )
                                if additional_data:
                                    logger.debug(
                                        f"Read {len(additional_data)} additional bytes for "
                                        f"{message.command_name}"
                                    )

                            # Attach additional data to message if present
                            if additional_data:
                                message.additional_data = additional_data

                            self._dispatcher.dispatch(message)
                            self._stats["messages_read"] += 1
                            consecutive_invalid = 0  # Reset counter on valid message
                        else:
                            consecutive_invalid += 1
                            self._stats["parse_errors"] += 1

                            # Only log occasionally to avoid spam
                            if (
                                consecutive_invalid <= 3
                                or consecutive_invalid % 100 == 0
                            ):
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
                        self._stats["parse_errors"] += 1

                except socket.timeout:
                    # Normal timeout - just continue loop
                    continue

                except socket.error as e:
                    if self._running:
                        logger.error(f"Socket error in reader: {e}")
                        self._stats["socket_errors"] += 1
                        self._notify_closed(f"socket error: {e}")
                    break

                except Exception as e:
                    if self._running:
                        logger.error(f"Unexpected error in reader: {e}", exc_info=True)
                        self._notify_closed(f"unexpected error: {e}")
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
        START_MARKER_BYTES = struct.pack("<I", self.START_MARKER)  # 0xF321E654

        # Read up to 512 bytes looking for the start marker
        try:
            search_data = self._socket.recv(512)
            if not search_data:
                return False

            self._stats["bytes_read"] += len(search_data)

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
                additional = b""
                while len(additional) < bytes_needed:
                    chunk = self._socket.recv(bytes_needed - len(additional))
                    if not chunk:
                        return False
                    additional += chunk
                    self._stats["bytes_read"] += len(chunk)
                full_message = data_after_marker + additional
            else:
                full_message = data_after_marker[: self.MESSAGE_SIZE]

            # Verify this is a valid message
            message = self._parse_message(full_message)
            if message.is_valid:
                self._dispatcher.dispatch(message)
                self._stats["messages_read"] += 1
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
        data = b""
        while len(data) < self.MESSAGE_SIZE:
            try:
                remaining = self.MESSAGE_SIZE - len(data)
                chunk = self._socket.recv(remaining)

                if not chunk:
                    # Socket closed
                    return b""

                data += chunk
                self._stats["bytes_read"] += len(chunk)

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

        # The socket's 0.5 s timeout is for the shutdown check, not a limit
        # on a payload: a settings document or a drive list can take longer
        # to arrive in full on a busy server, and returning part of it would
        # cost frame sync. So a timeout only ends the read after
        # ADDITIONAL_DATA_TIMEOUT_S without a byte, or when stopping.
        data = b""
        last_byte = time.monotonic()
        try:
            while len(data) < size:
                remaining = size - len(data)
                try:
                    chunk = self._socket.recv(remaining)
                except socket.timeout:
                    if (
                        self._running
                        and time.monotonic() - last_byte < ADDITIONAL_DATA_TIMEOUT_S
                    ):
                        continue
                    raise

                if not chunk:
                    logger.warning(
                        f"Socket closed while reading additional data ({len(data)}/{size})"
                    )
                    return None

                data += chunk
                last_byte = time.monotonic()
                self._stats["bytes_read"] += len(chunk)

            return data

        except socket.timeout:
            logger.warning(f"Timeout reading additional data ({len(data)}/{size})")
            return data if data else None
        except Exception as e:
            logger.error(f"Error reading additional data: {e}")
            return None

    def _parse_message(self, data: bytes) -> ParsedMessage:
        """Parse one 128-byte message (``core.frames.parse_frame``)."""
        return parse_frame(data)

    def get_stats(self) -> Dict[str, int]:
        """Get reader statistics."""
        return self._stats.copy()


class CommandClient:
    """
    High-level client for sending commands and receiving responses.

    Uses the background SocketReader and MessageDispatcher for
    non-blocking command-response handling.
    """

    def __init__(
        self,
        command_socket: socket.socket,
        on_closed: Optional[Callable[[str], None]] = None,
        send_lock: Optional[threading.Lock] = None,
    ):
        """
        Initialize the command client.

        Args:
            command_socket: Socket connected to microscope command port
            on_closed: Optional callback invoked (with a reason string) when
                the background reader detects the socket has been closed.
            send_lock: The lock every write on this socket takes
                (``TCPConnection.send_lock``). A private one if not given.
        """
        self._socket = command_socket
        self._dispatcher = MessageDispatcher()
        self._reader = SocketReader(command_socket, self._dispatcher, on_closed)
        # Shared with TCPConnection.send_command_bytes: one writer.
        self._send_lock = send_lock or threading.Lock()

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

    def send_command(
        self,
        command_bytes: bytes,
        expected_response_code: Union[int, Iterable[int]],
        timeout: float = 3.0,
        predicate: Optional[Callable[[ParsedMessage], bool]] = None,
    ) -> Optional[ParsedMessage]:
        """
        Send a command and wait for its reply.

        The request is registered before the bytes go out, so a fast reply
        is not missed, and removed afterwards whatever happened.

        Args:
            command_bytes: The frame, and any payload that follows it
            expected_response_code: Reply code expected, or several
            timeout: Seconds to wait for the reply
            predicate: Optional test the reply must also pass (see
                MessageDispatcher.register_pending_request)

        Returns:
            ParsedMessage response, or None on timeout
        """
        request = self._dispatcher.register_pending_request(
            expected_response_code, predicate
        )
        try:
            # The one send lock (shared with TCPConnection.send_command_bytes)
            with self._send_lock:
                self._socket.sendall(command_bytes)
            try:
                return request.get(timeout=timeout)
            except queue.Empty:
                codes = ", ".join(f"0x{c:04X}" for c in sorted(request.codes))
                logger.warning(f"Timeout waiting for response to {codes}")
                return None
        finally:
            self._dispatcher.unregister_pending_request(request)

    def register_callback(
        self, command_code: int, handler: Callable[[ParsedMessage], None]
    ):
        """
        Register a handler for unsolicited callback messages.

        Args:
            command_code: Command code to handle
            handler: Function that takes ParsedMessage
        """
        self._dispatcher.register_callback_handler(command_code, handler)

    def unregister_callback(
        self, command_code: int, handler: Callable[[ParsedMessage], None]
    ):
        """Remove a callback handler."""
        self._dispatcher.unregister_callback_handler(command_code, handler)

    @property
    def dispatcher(self) -> MessageDispatcher:
        """Access the message dispatcher."""
        return self._dispatcher

    def get_stats(self) -> Dict[str, Any]:
        """Get combined statistics."""
        return {
            "reader": self._reader.get_stats(),
            "dispatcher": self._dispatcher.get_stats(),
        }
