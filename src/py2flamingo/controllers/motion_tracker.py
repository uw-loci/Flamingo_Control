"""
Motion tracking for stage movements.

Handles waiting for motion-stopped callbacks from the microscope
after sending movement commands.
"""

import socket
import struct
import logging
import threading
from typing import Optional, Dict, Any


class MotionTracker:
    """
    Tracks stage motion completion by waiting for motion-stopped callbacks.

    After sending a movement command, the microscope moves the stage
    asynchronously and sends an unsolicited STAGE_MOTION_STOPPED (0x6010)
    message when complete.

    This class provides a way to wait for that callback without using
    hardcoded delays.
    """

    STAGE_MOTION_STOPPED = 24592  # 0x6010

    def __init__(self, command_socket: socket.socket):
        """
        Initialize motion tracker.

        Args:
            command_socket: Socket to read callbacks from
        """
        self.command_socket = command_socket
        self.logger = logging.getLogger(__name__)
        self._is_moving = False
        self._lock = threading.Lock()
        self._stop_waiting = False  # Flag to cancel current wait
        self._wait_thread: Optional[threading.Thread] = None

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
        - Motion-stopped callback (0x6010) is received
        - Timeout expires
        - Wait is cancelled by new motion command (if allow_cancel=True)

        Args:
            timeout: Maximum time to wait in seconds (default: 30s)
            allow_cancel: Whether this wait can be cancelled by new commands

        Returns:
            True if motion completed, False if timeout or cancelled

        Raises:
            RuntimeError: If socket error occurs
        """
        self.logger.info(f"Waiting for motion complete (timeout={timeout}s, cancellable={allow_cancel})...")

        # Reset cancel flag
        self._stop_waiting = False

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
                        status = parsed['params'][0] if parsed['params'] else None
                        self.logger.info(
                            f"Motion complete! Status={status} (0=stopped)"
                        )
                        return True

                    else:
                        # Not the callback we're waiting for - log and continue
                        self.logger.debug(f"Ignoring unexpected message {command_code}")

                except socket.timeout:
                    self.logger.warning(f"Timeout waiting for motion complete after {timeout}s")
                    return False

        except Exception as e:
            self.logger.error(f"Error waiting for motion complete: {e}", exc_info=True)
            raise RuntimeError(f"Motion tracking error: {e}") from e

        finally:
            with self._lock:
                self._is_moving = False
            # Restore original timeout
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
        Receive full message from socket.

        Args:
            size: Number of bytes to receive

        Returns:
            Bytes received or None if socket closed
        """
        data = b''
        while len(data) < size:
            chunk = self.command_socket.recv(size - len(data))
            if not chunk:
                # Socket closed
                return None
            data += chunk
        return data

    def _parse_response(self, response: bytes) -> Dict[str, Any]:
        """
        Parse 128-byte protocol response.

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

        # Unpack 7 parameters
        params = []
        for i in range(7):
            offset = 12 + (i * 4)
            param = struct.unpack('<i', response[offset:offset+4])[0]
            params.append(param)

        # Unpack value (double)
        value = struct.unpack('<d', response[40:48])[0]

        return {
            'start_marker': start_marker,
            'command_code': command_code,
            'status_code': status_code,
            'params': params,
            'value': value
        }
