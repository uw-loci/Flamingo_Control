"""
Callback listener for handling unsolicited messages from microscope.

This module provides a background thread that continuously monitors the
command socket for unsolicited messages (callbacks) from the microscope,
such as motion-stopped notifications, system state changes, etc.
"""

import socket
import struct
import threading
import logging
from typing import Optional, Callable, Dict
from queue import Queue, Empty


class CallbackListener:
    """
    Background thread that listens for unsolicited messages from microscope.

    Unsolicited messages are commands sent by the microscope without being
    requested, such as:
    - STAGE_MOTION_STOPPED (0x6010 / 24592)
    - SYSTEM_STATE updates
    - Error notifications

    These messages use the same 128-byte protocol format as command responses.
    """

    # Known unsolicited message codes
    STAGE_MOTION_STOPPED = 24592  # 0x6010

    def __init__(self, command_socket: socket.socket):
        """
        Initialize callback listener.

        Args:
            command_socket: Socket to listen on for callbacks
        """
        self.command_socket = command_socket
        self.logger = logging.getLogger(__name__)

        # Threading control
        self._stop_event = threading.Event()
        self._listener_thread: Optional[threading.Thread] = None

        # Callback handlers: {command_code: handler_function}
        self._handlers: Dict[int, Callable] = {}

        # Queue for callbacks (optional - for testing/debugging)
        self._callback_queue: Queue = Queue()

    def register_handler(self, command_code: int, handler: Callable) -> None:
        """
        Register a handler function for a specific callback command.

        Args:
            command_code: Command code to handle (e.g., 24592 for motion stopped)
            handler: Callable that takes parsed response dict
        """
        self._handlers[command_code] = handler
        self.logger.info(f"Registered handler for command 0x{command_code:04X} ({command_code})")

    def unregister_handler(self, command_code: int) -> None:
        """
        Unregister a handler for a command code.

        Args:
            command_code: Command code to unregister
        """
        if command_code in self._handlers:
            del self._handlers[command_code]
            self.logger.info(f"Unregistered handler for command {command_code}")

    def start(self) -> None:
        """Start the callback listener thread."""
        if self._listener_thread and self._listener_thread.is_alive():
            self.logger.warning("Callback listener already running")
            return

        self._stop_event.clear()
        self._listener_thread = threading.Thread(
            target=self._listen_loop,
            name="CallbackListener",
            daemon=True
        )
        self._listener_thread.start()
        self.logger.info("Callback listener thread started")

    def stop(self, timeout: float = 2.0) -> None:
        """
        Stop the callback listener thread.

        Args:
            timeout: Maximum time to wait for thread to stop
        """
        if not self._listener_thread or not self._listener_thread.is_alive():
            return

        self.logger.info("Stopping callback listener...")
        self._stop_event.set()

        # Wait for thread to finish
        self._listener_thread.join(timeout=timeout)

        if self._listener_thread.is_alive():
            self.logger.warning("Callback listener thread did not stop gracefully")
        else:
            self.logger.info("Callback listener stopped")

    def _listen_loop(self) -> None:
        """
        Main listening loop - runs in background thread.

        Continuously reads from socket and dispatches callbacks to handlers.
        """
        self.logger.info("Callback listener loop started")

        # Set socket to non-blocking with short timeout so we can check stop_event
        self.command_socket.settimeout(0.5)

        while not self._stop_event.is_set():
            try:
                # Try to receive a message (128 bytes)
                data = self._receive_message(128)

                if data:
                    # Parse the message
                    parsed = self._parse_response(data)

                    # Dispatch to handler if registered
                    command_code = parsed['command_code']
                    self.logger.debug(
                        f"Received unsolicited message: 0x{command_code:04X} ({command_code})"
                    )

                    # Add to queue for debugging
                    self._callback_queue.put(parsed)

                    # Call registered handler if exists
                    if command_code in self._handlers:
                        try:
                            self._handlers[command_code](parsed)
                        except Exception as e:
                            self.logger.error(
                                f"Error in callback handler for {command_code}: {e}",
                                exc_info=True
                            )
                    else:
                        self.logger.debug(f"No handler registered for command {command_code}")

            except socket.timeout:
                # Timeout is expected - allows us to check stop_event
                continue

            except Exception as e:
                if not self._stop_event.is_set():
                    self.logger.error(f"Error in callback listener loop: {e}", exc_info=True)
                # Brief sleep before retry to avoid tight loop on persistent errors
                self._stop_event.wait(0.1)

        self.logger.info("Callback listener loop exited")

    def _receive_message(self, size: int) -> Optional[bytes]:
        """
        Receive message from socket.

        Args:
            size: Number of bytes to receive

        Returns:
            Bytes received or None if timeout/error
        """
        try:
            data = b''
            while len(data) < size:
                chunk = self.command_socket.recv(size - len(data))
                if not chunk:
                    # Socket closed
                    return None
                data += chunk
            return data

        except socket.timeout:
            # Timeout is expected
            return None

        except Exception as e:
            if not self._stop_event.is_set():
                self.logger.debug(f"Receive error: {e}")
            return None

    def _parse_response(self, response: bytes) -> Dict:
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

        # Get addDataBytes field
        add_data_bytes = struct.unpack('<I', response[48:52])[0]

        # Get data buffer (72 bytes)
        data_buffer = response[52:124]

        # Get end marker
        end_marker = struct.unpack('<I', response[124:128])[0]

        return {
            'start_marker': start_marker,
            'command_code': command_code,
            'status_code': status_code,
            'params': params,
            'value': value,
            'add_data_bytes': add_data_bytes,
            'data_buffer': data_buffer,
            'end_marker': end_marker,
            'raw': response
        }
