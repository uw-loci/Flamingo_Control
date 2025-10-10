"""
TCP Connection management for Flamingo microscope communication.

This module handles low-level socket operations for connecting to and
communicating with the Flamingo microscope control system. It manages
dual sockets (command port and live imaging port) and provides thread-safe
operations for sending and receiving data.
"""

import socket
import logging
import threading
from typing import Tuple, Optional


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

    def __init__(self):
        """Initialize TCP connection manager."""
        self._command_socket: Optional[socket.socket] = None
        self._live_socket: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._connected = False
        self.logger = logging.getLogger(__name__)

        # Connection info
        self._ip: Optional[str] = None
        self._port: Optional[int] = None

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

                # Store connection info
                self._ip = ip
                self._port = port
                self._connected = True

                return self._command_socket, self._live_socket

            except (socket.timeout, ConnectionRefusedError, OSError) as e:
                self.logger.error(f"Connection failed: {e}")
                # Clean up partial connections
                self._disconnect_unsafe()
                raise

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
