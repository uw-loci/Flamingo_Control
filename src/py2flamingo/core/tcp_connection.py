"""
TCP Connection management for Flamingo microscope communication.

This module handles low-level socket operations for connecting to and
communicating with the Flamingo microscope control system. It manages
dual sockets (command port and live imaging port) and provides thread-safe
operations for sending and receiving data.

Supports two modes:
- Synchronous: Traditional blocking send/receive (for simple operations)
- Asynchronous: Background reader with message dispatcher (for concurrent ops)
"""

import socket
import logging
import threading
from typing import Callable, Tuple, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .socket_reader import CommandClient, MessageDispatcher, ParsedMessage


class TCPConnection:
    """
    Manages TCP socket connections to the Flamingo microscope.

    The Flamingo microscope uses two TCP ports:
    - Command port: For sending commands and receiving responses
    - Live port: Command port + 1, for receiving live imaging data

    This class provides thread-safe operations for managing both connections.

    Example:
        >>> connection = TCPConnection()
        >>> nuc_sock, live_sock = connection.connect("127.0.0.1", 53717)
        >>> connection.send_bytes(command_data)
        >>> response = connection.receive_bytes(128)
        >>> connection.disconnect()
    """

    def __init__(self, use_async_reader: bool = True):
        """
        Initialize TCP connection manager.

        Args:
            use_async_reader: If True, use background socket reader for
                             non-blocking command/response handling.
                             If False, use traditional synchronous I/O.
        """
        self._command_socket: Optional[socket.socket] = None
        self._live_socket: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._connected = False
        self.logger = logging.getLogger(__name__)

        # Connection info
        self._ip: Optional[str] = None
        self._port: Optional[int] = None

        # Async reader support
        self._use_async_reader = use_async_reader
        self._command_client: Optional["CommandClient"] = None

    def connect(
        self,
        ip: str,
        port: int,
        timeout: float = 2.0
    ) -> Tuple[socket.socket, socket.socket]:
        """
        Connect to microscope on both command and live imaging ports.

        Args:
            ip: Microscope IP address (e.g., "127.0.0.1" or "10.129.37.22")
            port: Command port number (typically 53717)
            timeout: Connection timeout in seconds (default: 2.0)

        Returns:
            Tuple of (command_socket, live_socket)

        Raises:
            ValueError: If IP address or port is invalid
            socket.timeout: If connection times out
            ConnectionRefusedError: If connection is refused
            OSError: For other socket errors

        Example:
            >>> connection = TCPConnection()
            >>> try:
            ...     cmd_sock, live_sock = connection.connect("127.0.0.1", 53717)
            ...     print("Connected successfully")
            ... except socket.timeout:
            ...     print("Connection timed out")
        """
        with self._lock:
            # Validate inputs
            self._validate_ip(ip)
            self._validate_port(port)

            # Disconnect if already connected
            if self._connected:
                self.logger.warning("Already connected. Disconnecting first.")
                self._disconnect_unsafe()

            try:
                # Connect to command port
                self.logger.info(f"Connecting to {ip}:{port} (command port)")
                self._command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._command_socket.settimeout(timeout)
                self._command_socket.connect((ip, port))
                self._command_socket.settimeout(None)  # Clear timeout after connection
                self.logger.info("Connected to command port")

                # Calculate live port (command port + 1)
                live_port = port + 1

                # Connect to live imaging port
                self.logger.info(f"Connecting to {ip}:{live_port} (live port)")
                self._live_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._live_socket.settimeout(timeout)
                self._live_socket.connect((ip, live_port))
                self._live_socket.settimeout(None)  # Clear timeout after connection
                self.logger.info("Connected to live imaging port")

                # CRITICAL: Flush stale data from buffers
                # Prevents first-command timeout caused by buffered responses
                self._flush_receive_buffer(self._command_socket)
                self._flush_receive_buffer(self._live_socket)

                # Store connection info
                self._ip = ip
                self._port = port
                self._connected = True

                # Start async reader if enabled
                if self._use_async_reader:
                    self._start_async_reader()

                return self._command_socket, self._live_socket

            except (socket.timeout, ConnectionRefusedError, OSError) as e:
                self.logger.error(f"Connection failed: {e}")
                # Clean up partial connections
                self._disconnect_unsafe()
                raise

    def _flush_receive_buffer(self, sock: socket.socket, timeout: float = 0.1) -> int:
        """
        Drain stale data from socket receive buffer.

        Clears any accumulated responses from connection handshake or previous sessions.
        Critical for preventing first-command timeout issues.

        Args:
            sock: Socket to flush
            timeout: Short timeout for non-blocking reads (default: 0.1s)

        Returns:
            Number of bytes flushed
        """
        if sock is None:
            return 0

        flushed = 0
        original_timeout = sock.gettimeout()

        try:
            sock.settimeout(timeout)

            while True:
                try:
                    data = sock.recv(4096)
                    if not data:
                        break
                    flushed += len(data)
                    self.logger.debug(f"Flushed {len(data)} bytes from buffer")
                except socket.timeout:
                    break  # No more data waiting
                except Exception as e:
                    self.logger.warning(f"Error during buffer flush: {e}")
                    break

        finally:
            sock.settimeout(original_timeout)

        if flushed > 0:
            self.logger.info(f"Flushed {flushed} stale bytes from receive buffer")

        return flushed

    def disconnect(self) -> None:
        """
        Close all socket connections cleanly.

        This method is thread-safe and can be called multiple times safely.

        Example:
            >>> connection.disconnect()
        """
        with self._lock:
            self._disconnect_unsafe()

    def _disconnect_unsafe(self) -> None:
        """
        Internal disconnect without locking (called when lock is already held).

        Not thread-safe - should only be called from methods that hold the lock.
        """
        # Stop async reader first (before closing socket)
        if self._command_client:
            try:
                self._command_client.stop()
                self.logger.info("Stopped async reader")
            except Exception as e:
                self.logger.error(f"Error stopping async reader: {e}")
            finally:
                self._command_client = None

        # Close command socket
        if self._command_socket:
            try:
                self._command_socket.close()
                self.logger.info("Closed command socket")
            except Exception as e:
                self.logger.error(f"Error closing command socket: {e}")
            finally:
                self._command_socket = None

        # Close live socket
        if self._live_socket:
            try:
                self._live_socket.close()
                self.logger.info("Closed live imaging socket")
            except Exception as e:
                self.logger.error(f"Error closing live socket: {e}")
            finally:
                self._live_socket = None

        # Clear connection state
        self._connected = False
        self._ip = None
        self._port = None

    def send_bytes(
        self,
        data: bytes,
        socket_type: str = "command"
    ) -> None:
        """
        Send bytes through the specified socket.

        Args:
            data: Bytes to send
            socket_type: Which socket to use - "command" or "live" (default: "command")

        Raises:
            ConnectionError: If not connected
            ValueError: If socket_type is invalid or data is not bytes
            OSError: If send fails

        Example:
            >>> connection.send_bytes(command_bytes, socket_type="command")
        """
        if not isinstance(data, bytes):
            raise ValueError(f"Data must be bytes, got {type(data)}")

        with self._lock:
            if not self._connected:
                raise ConnectionError("Not connected to microscope")

            # Select socket
            if socket_type == "command":
                sock = self._command_socket
            elif socket_type == "live":
                sock = self._live_socket
            else:
                raise ValueError(
                    f"Invalid socket_type: {socket_type}. Must be 'command' or 'live'"
                )

            if sock is None:
                raise ConnectionError(f"{socket_type} socket is not connected")

            try:
                sock.sendall(data)
                self.logger.debug(
                    f"Sent {len(data)} bytes on {socket_type} socket"
                )
            except OSError as e:
                self.logger.error(f"Failed to send data: {e}")
                # Connection likely broken
                self._connected = False
                raise

    def receive_bytes(
        self,
        size: int,
        socket_type: str = "command",
        timeout: Optional[float] = None
    ) -> bytes:
        """
        Receive bytes from the specified socket.

        Args:
            size: Number of bytes to receive
            socket_type: Which socket to use - "command" or "live" (default: "command")
            timeout: Optional timeout in seconds (None = blocking)

        Returns:
            Bytes received (may be less than size if connection closed)

        Raises:
            ConnectionError: If not connected
            ValueError: If socket_type is invalid or size is invalid
            socket.timeout: If timeout expires
            OSError: If receive fails

        Example:
            >>> response = connection.receive_bytes(128, timeout=1.0)
        """
        if not isinstance(size, int) or size <= 0:
            raise ValueError(f"Size must be positive integer, got {size}")

        with self._lock:
            if not self._connected:
                raise ConnectionError("Not connected to microscope")

            # Select socket
            if socket_type == "command":
                sock = self._command_socket
            elif socket_type == "live":
                sock = self._live_socket
            else:
                raise ValueError(
                    f"Invalid socket_type: {socket_type}. Must be 'command' or 'live'"
                )

            if sock is None:
                raise ConnectionError(f"{socket_type} socket is not connected")

            try:
                # Set timeout if specified
                if timeout is not None:
                    sock.settimeout(timeout)

                data = sock.recv(size)

                # Clear timeout
                if timeout is not None:
                    sock.settimeout(None)

                self.logger.debug(
                    f"Received {len(data)} bytes from {socket_type} socket"
                )
                return data

            except socket.timeout:
                self.logger.debug(f"Receive timeout on {socket_type} socket")
                # Clear timeout and re-raise
                if timeout is not None:
                    sock.settimeout(None)
                raise

            except OSError as e:
                self.logger.error(f"Failed to receive data: {e}")
                # Connection likely broken
                self._connected = False
                raise

    def receive_all_bytes(
        self,
        size: int,
        socket_type: str = "command",
        timeout: Optional[float] = None
    ) -> bytes:
        """
        Receive exact number of bytes from the specified socket.

        Unlike receive_bytes(), this method will continue receiving until
        exactly 'size' bytes have been received, or timeout/error occurs.
        This is critical for fixed-size protocol messages (e.g., 128-byte commands).

        Args:
            size: Exact number of bytes to receive
            socket_type: Which socket to use - "command" or "live" (default: "command")
            timeout: Optional timeout in seconds (None = blocking)

        Returns:
            Exactly 'size' bytes received

        Raises:
            ConnectionError: If not connected or connection closed prematurely
            ValueError: If socket_type is invalid or size is invalid
            socket.timeout: If timeout expires
            OSError: If receive fails

        Example:
            >>> # Receive complete 128-byte command response
            >>> response = connection.receive_all_bytes(128, timeout=2.0)
        """
        if not isinstance(size, int) or size <= 0:
            raise ValueError(f"Size must be positive integer, got {size}")

        with self._lock:
            if not self._connected:
                raise ConnectionError("Not connected to microscope")

            # Select socket
            if socket_type == "command":
                sock = self._command_socket
            elif socket_type == "live":
                sock = self._live_socket
            else:
                raise ValueError(
                    f"Invalid socket_type: {socket_type}. Must be 'command' or 'live'"
                )

            if sock is None:
                raise ConnectionError(f"{socket_type} socket is not connected")

            try:
                # Set timeout if specified
                if timeout is not None:
                    sock.settimeout(timeout)

                # Receive all bytes
                received = b''
                remaining = size

                while remaining > 0:
                    chunk = sock.recv(remaining)

                    if len(chunk) == 0:
                        # Connection closed
                        self.logger.error(
                            f"Connection closed while receiving data "
                            f"(got {len(received)}/{size} bytes)"
                        )
                        self._connected = False
                        raise ConnectionError(
                            f"Connection closed after receiving {len(received)}/{size} bytes"
                        )

                    received += chunk
                    remaining -= len(chunk)

                # Clear timeout
                if timeout is not None:
                    sock.settimeout(None)

                self.logger.debug(
                    f"Received {len(received)} bytes from {socket_type} socket"
                )
                return received

            except socket.timeout:
                self.logger.debug(
                    f"Receive timeout on {socket_type} socket "
                    f"(got {len(received)}/{size} bytes)"
                )
                # Clear timeout and re-raise
                if timeout is not None:
                    sock.settimeout(None)
                raise

            except OSError as e:
                self.logger.error(f"Failed to receive data: {e}")
                # Connection likely broken
                self._connected = False
                raise

    def check_for_unsolicited_message(
        self,
        socket_type: str = "command",
        timeout: float = 0.0
    ) -> Optional[bytes]:
        """
        Check for unsolicited messages (callbacks) from the microscope.

        The microscope can send unsolicited messages for events like:
        - Stage motion stopped (MOTION_STOPPED callback)
        - Acquisition complete
        - Error conditions

        This method does a non-blocking check (default timeout=0.0) to see
        if any data is available to read.

        Args:
            socket_type: Which socket to check - "command" or "live" (default: "command")
            timeout: Timeout in seconds (default: 0.0 for non-blocking check)

        Returns:
            Bytes received if message available, None if no message

        Raises:
            ConnectionError: If not connected
            ValueError: If socket_type is invalid
            OSError: If receive fails

        Example:
            >>> # Check for callbacks during long operation
            >>> import time
            >>> while stage_is_moving:
            ...     callback = connection.check_for_unsolicited_message(timeout=0.1)
            ...     if callback:
            ...         response = decoder.decode_command(callback)
            ...         if response['code'] == StageCommands.MOTION_STOPPED:
            ...             print("Stage motion completed")
            ...             break
            ...     time.sleep(0.1)
        """
        try:
            # Try to receive with very short timeout
            data = self.receive_bytes(
                size=128,  # Standard command size
                socket_type=socket_type,
                timeout=timeout
            )
            return data

        except socket.timeout:
            # No data available - this is normal
            return None

        except (ConnectionError, OSError) as e:
            # Real error - re-raise
            raise

    def is_connected(self) -> bool:
        """
        Check if both sockets are connected.

        Returns:
            True if both command and live sockets are connected

        Example:
            >>> if connection.is_connected():
            ...     connection.send_bytes(data)
        """
        with self._lock:
            return self._connected

    def get_connection_info(self) -> Tuple[Optional[str], Optional[int]]:
        """
        Get current connection information.

        Returns:
            Tuple of (ip_address, port) or (None, None) if not connected

        Example:
            >>> ip, port = connection.get_connection_info()
            >>> if ip:
            ...     print(f"Connected to {ip}:{port}")
        """
        with self._lock:
            return self._ip, self._port

    # ========== Async Reader Methods ==========

    def _start_async_reader(self) -> None:
        """
        Initialize and start the background socket reader.

        Called during connect() when use_async_reader=True.
        Must be called while holding _lock.
        """
        from .socket_reader import CommandClient

        self._command_client = CommandClient(self._command_socket)
        self._command_client.start()
        self.logger.info("Started async socket reader")

    @property
    def has_async_reader(self) -> bool:
        """Check if async reader is active."""
        return self._command_client is not None and self._command_client.is_running()

    @property
    def dispatcher(self) -> Optional["MessageDispatcher"]:
        """
        Get the message dispatcher for registering callbacks.

        Returns:
            MessageDispatcher if async reader is active, None otherwise
        """
        if self._command_client:
            return self._command_client.dispatcher
        return None

    def send_command_async(
        self,
        command_bytes: bytes,
        expected_response_code: int,
        timeout: float = 3.0
    ) -> Optional["ParsedMessage"]:
        """
        Send a command and wait for response using async reader.

        This method is only available when async reader is active.
        It sends the command and waits for the response via the
        background reader's dispatch queue.

        Args:
            command_bytes: 128-byte command to send
            expected_response_code: Command code expected in response
            timeout: Seconds to wait for response

        Returns:
            ParsedMessage response, or None on timeout

        Raises:
            RuntimeError: If async reader not active
            ConnectionError: If not connected
        """
        if not self._connected:
            raise ConnectionError("Not connected to microscope")

        if not self._command_client:
            raise RuntimeError("Async reader not active - use send_bytes/receive_bytes instead")

        return self._command_client.send_command(
            command_bytes, expected_response_code, timeout
        )

    def register_callback(
        self,
        command_code: int,
        handler: Callable[["ParsedMessage"], None]
    ) -> None:
        """
        Register a handler for unsolicited callback messages.

        This allows you to receive notifications when the microscope sends
        unsolicited messages like STAGE_MOTION_STOPPED.

        Args:
            command_code: Command code to handle (e.g., 0x6010 for motion stopped)
            handler: Function that takes ParsedMessage

        Raises:
            RuntimeError: If async reader not active
        """
        if not self._command_client:
            raise RuntimeError("Async reader not active - cannot register callbacks")

        self._command_client.register_callback(command_code, handler)

    def unregister_callback(
        self,
        command_code: int,
        handler: Callable[["ParsedMessage"], None]
    ) -> None:
        """Remove a callback handler."""
        if self._command_client:
            self._command_client.unregister_callback(command_code, handler)

    def get_async_stats(self) -> Optional[dict]:
        """
        Get statistics from the async reader.

        Returns:
            Dict with reader and dispatcher stats, or None if not active
        """
        if self._command_client:
            return self._command_client.get_stats()
        return None

    @staticmethod
    def _validate_ip(ip: str) -> None:
        """
        Validate IP address format.

        Args:
            ip: IP address string

        Raises:
            ValueError: If IP address is invalid
        """
        if not isinstance(ip, str) or not ip:
            raise ValueError(f"Invalid IP address: {ip}")

        # Simple validation - socket.connect will do thorough validation
        parts = ip.split('.')
        if len(parts) != 4:
            raise ValueError(f"Invalid IP address format: {ip}")

        try:
            for part in parts:
                num = int(part)
                if num < 0 or num > 255:
                    raise ValueError(f"Invalid IP address: {ip}")
        except ValueError:
            raise ValueError(f"Invalid IP address: {ip}")

    @staticmethod
    def _validate_port(port: int) -> None:
        """
        Validate port number.

        Args:
            port: Port number

        Raises:
            ValueError: If port is invalid
        """
        if not isinstance(port, int):
            raise ValueError(f"Port must be an integer, got {type(port)}")

        if port < 1 or port > 65535:
            raise ValueError(f"Port must be 1-65535, got {port}")

        # Warn about live port overflow
        if port > 65534:
            raise ValueError(
                f"Port {port} is too high - live port would be {port + 1} (>65535)"
            )
