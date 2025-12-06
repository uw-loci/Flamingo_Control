"""
Motion tracking for stage movements.

Handles waiting for motion-stopped callbacks from the microscope
after sending movement commands.

ASYNC SUPPORT: When a connection with async reader is provided,
uses the callback dispatch queue instead of blocking socket reads.
"""

import queue
import socket
import struct
import logging
import threading
from typing import Optional, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from py2flamingo.core.tcp_connection import TCPConnection
    from py2flamingo.core.socket_reader import ParsedMessage


class MotionTracker:
    """
    Tracks stage motion completion by waiting for motion-stopped callbacks.

    After sending a movement command, the microscope moves the stage
    asynchronously and sends an unsolicited STAGE_MOTION_STOPPED (0x6010)
    message when complete.

    This class provides a way to wait for that callback without using
    hardcoded delays.

    Supports two modes:
    - Async mode: Uses callback dispatch queue (preferred)
    - Sync mode: Blocking socket reads (legacy fallback)
    """

    STAGE_MOTION_STOPPED = 24592  # 0x6010

    def __init__(
        self,
        command_socket: Optional[socket.socket] = None,
        connection: Optional["TCPConnection"] = None
    ):
        """
        Initialize motion tracker.

        Args:
            command_socket: Socket to read callbacks from (legacy mode)
            connection: TCPConnection with async reader (preferred mode)

        Note: Provide either command_socket OR connection, not both.
              If connection has async reader, it will be used.
        """
        self.command_socket = command_socket
        self.connection = connection
        self.logger = logging.getLogger(__name__)
        self._is_moving = False
        self._lock = threading.Lock()
        self._stop_waiting = False
        self._wait_thread: Optional[threading.Thread] = None

        # Async mode support
        self._callback_queue: Optional[queue.Queue] = None
        self._callback_registered = False

    def _use_async_mode(self) -> bool:
        """Check if async mode should be used."""
        return (self.connection is not None and
                hasattr(self.connection, 'has_async_reader') and
                self.connection.has_async_reader)

    def _setup_async_callback(self) -> None:
        """Register callback handler for STAGE_MOTION_STOPPED."""
        if not self._callback_registered and self._use_async_mode():
            self._callback_queue = queue.Queue(maxsize=10)
            self.connection.register_callback(
                self.STAGE_MOTION_STOPPED,
                self._on_motion_stopped_callback
            )
            self._callback_registered = True
            self.logger.info("Registered async callback for STAGE_MOTION_STOPPED")

    def _cleanup_async_callback(self) -> None:
        """Unregister callback handler."""
        if self._callback_registered and self.connection:
            try:
                self.connection.unregister_callback(
                    self.STAGE_MOTION_STOPPED,
                    self._on_motion_stopped_callback
                )
            except Exception:
                pass
            self._callback_registered = False

    def _on_motion_stopped_callback(self, message: "ParsedMessage") -> None:
        """
        Handle STAGE_MOTION_STOPPED callback from background reader.

        Args:
            message: ParsedMessage from socket_reader
        """
        try:
            if self._callback_queue:
                self._callback_queue.put_nowait(message)
                self.logger.debug(f"Queued STAGE_MOTION_STOPPED callback (status={message.status_code})")
        except queue.Full:
            self.logger.warning("Motion callback queue full - dropping message")

    def cancel_wait(self) -> None:
        """
        Cancel the current motion wait operation.

        This allows a new motion command to replace the current one,
        matching the C++ behavior of thread replacement.
        """
        self.logger.info("Cancelling current motion wait")
        self._stop_waiting = True

        # Wait for thread to finish if it exists
        if self._wait_thread and self._wait_thread.is_alive():
            self._wait_thread.join(timeout=0.5)

    def wait_for_motion_complete(self, timeout: float = 30.0, allow_cancel: bool = True) -> bool:
        """
        Wait for motion-stopped callback from microscope.

        This method blocks until either:
        - Motion-stopped callback (0x6010) is received with status=1
        - Timeout expires
        - Wait is cancelled by new motion command (if allow_cancel=True)

        Args:
            timeout: Maximum time to wait in seconds (default: 30s)
            allow_cancel: Whether this wait can be cancelled by new commands

        Returns:
            True if motion completed successfully, False if timeout or cancelled

        Raises:
            RuntimeError: If socket error occurs
        """
        self.logger.info(f"Waiting for motion complete (timeout={timeout}s, cancellable={allow_cancel})...")

        # Reset cancel flag
        self._stop_waiting = False

        # Choose async or sync mode
        if self._use_async_mode():
            return self._wait_async(timeout, allow_cancel)
        else:
            return self._wait_sync(timeout, allow_cancel)

    def _wait_async(self, timeout: float, allow_cancel: bool) -> bool:
        """
        Wait for motion complete using async callback queue.

        Args:
            timeout: Maximum time to wait in seconds
            allow_cancel: Whether this wait can be cancelled

        Returns:
            True if motion completed successfully
        """
        # Ensure callback is registered
        self._setup_async_callback()

        # Clear any stale callbacks from queue
        while not self._callback_queue.empty():
            try:
                stale = self._callback_queue.get_nowait()
                self.logger.debug(f"Discarded stale motion callback (status={stale.status_code})")
            except queue.Empty:
                break

        try:
            with self._lock:
                self._is_moving = True

            # Wait for callbacks with periodic cancel checks
            remaining = timeout
            poll_interval = 0.1  # Check cancel flag every 100ms

            while remaining > 0:
                # Check if wait was cancelled
                if allow_cancel and self._stop_waiting:
                    self.logger.info("Motion wait cancelled by new command (async)")
                    return False

                try:
                    # Wait for next callback
                    wait_time = min(poll_interval, remaining)
                    message = self._callback_queue.get(timeout=wait_time)

                    # Check status
                    if message.status_code == 1:
                        axis_info = message.int32_data0
                        self.logger.info(
                            f"Motion complete! Status={message.status_code} (1=success), "
                            f"axis={axis_info} (async)"
                        )
                        return True
                    else:
                        self.logger.warning(
                            f"STAGE_MOTION_STOPPED received with status={message.status_code} "
                            f"(not 1) - continuing to wait... (async)"
                        )
                        # Continue waiting

                except queue.Empty:
                    # No callback yet, update remaining time
                    remaining -= poll_interval

            # Timeout
            self.logger.debug(
                f"Timeout waiting for motion complete after {timeout}s (async) - "
                f"stage may have completed without callback"
            )
            return False

        finally:
            with self._lock:
                self._is_moving = False

    def _wait_sync(self, timeout: float, allow_cancel: bool) -> bool:
        """
        Wait for motion complete using blocking socket reads (legacy).

        Args:
            timeout: Maximum time to wait in seconds
            allow_cancel: Whether this wait can be cancelled

        Returns:
            True if motion completed successfully
        """
        if not self.command_socket:
            raise RuntimeError("No command socket available for sync mode")

        # Set socket timeout
        original_timeout = self.command_socket.gettimeout()
        self.command_socket.settimeout(timeout)

        try:
            with self._lock:
                self._is_moving = True

            # Keep reading messages until we get motion-stopped
            while True:
                # Check if wait was cancelled
                if allow_cancel and self._stop_waiting:
                    self.logger.info("Motion wait cancelled by new command")
                    return False
                try:
                    # Read 128-byte message
                    data = self._receive_full_message(128)

                    if not data:
                        self.logger.warning("Socket closed while waiting for motion complete")
                        return False

                    # Parse message
                    parsed = self._parse_response(data)
                    command_code = parsed['command_code']

                    self.logger.debug(
                        f"Received message while waiting: 0x{command_code:04X} ({command_code})"
                    )

                    # Check if this is the motion-stopped callback
                    if command_code == self.STAGE_MOTION_STOPPED:
                        status = parsed['status_code']
                        axis_info = parsed['params'][3] if len(parsed['params']) > 3 else None

                        if status == 1:
                            self.logger.info(f"Motion complete! Status={status} (1=success), axis={axis_info}")
                            return True
                        else:
                            self.logger.warning(
                                f"STAGE_MOTION_STOPPED received with status={status} (not 1) - "
                                f"motion may have failed, continuing to wait..."
                            )
                            continue

                    else:
                        self.logger.debug(f"Ignoring unexpected message {command_code}")

                except socket.timeout:
                    self.logger.debug(
                        f"Timeout waiting for motion complete after {timeout}s - "
                        f"stage may have completed without callback"
                    )
                    return False

        except Exception as e:
            self.logger.error(f"Error waiting for motion complete: {e}", exc_info=True)
            raise RuntimeError(f"Motion tracking error: {e}") from e

        finally:
            with self._lock:
                self._is_moving = False
            try:
                self.command_socket.settimeout(original_timeout)
            except:
                pass

    def is_moving(self) -> bool:
        """
        Check if stage is currently moving.

        Returns:
            True if waiting for motion complete
        """
        with self._lock:
            return self._is_moving

    def _receive_full_message(self, size: int) -> Optional[bytes]:
        """
        Receive full message from socket (sync mode only).

        Args:
            size: Number of bytes to receive

        Returns:
            Bytes received or None if socket closed
        """
        data = b''
        while len(data) < size:
            chunk = self.command_socket.recv(size - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def _parse_response(self, response: bytes) -> Dict[str, Any]:
        """
        Parse 128-byte protocol response (sync mode only).

        Args:
            response: 128-byte response from microscope

        Returns:
            Dict with parsed fields
        """
        if len(response) != 128:
            raise ValueError(f"Invalid response size: {len(response)} (expected 128)")

        start_marker = struct.unpack('<I', response[0:4])[0]
        command_code = struct.unpack('<I', response[4:8])[0]
        status_code = struct.unpack('<I', response[8:12])[0]

        params = []
        for i in range(7):
            offset = 12 + (i * 4)
            param = struct.unpack('<i', response[offset:offset+4])[0]
            params.append(param)

        value = struct.unpack('<d', response[40:48])[0]

        return {
            'start_marker': start_marker,
            'command_code': command_code,
            'status_code': status_code,
            'params': params,
            'value': value
        }
