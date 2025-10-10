"""
Connection models for Py2Flamingo.

This module provides data structures and models for managing TCP/IP connections
to the Flamingo microscope system.

Classes:
    ConnectionConfig: Immutable configuration for a connection
    ConnectionState: Enumeration of connection states
    ConnectionStatus: Current status of a connection
    ConnectionModel: Observable model for connection state management
"""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, List, Optional, Tuple


# IP address validation pattern (IPv4)
_IPV4_PATTERN = re.compile(
    r'^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}'
    r'(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
)


@dataclass(frozen=True)
class ConnectionConfig:
    """
    Immutable configuration for a TCP/IP connection.

    Attributes:
        ip_address: IPv4 address of the microscope (e.g., "192.168.1.100")
        port: Main command port number (1-65535)
        live_port: Live imaging port number (defaults to port + 1)
        timeout: Connection timeout in seconds (default: 2.0)

    Example:
        >>> config = ConnectionConfig("192.168.1.100", 53717, 53718, 2.0)
        >>> valid, errors = config.validate()
        >>> if not valid:
        ...     print(f"Validation errors: {errors}")
    """

    ip_address: str
    port: int
    live_port: int = None  # Will default to port + 1 if not specified
    timeout: float = 2.0

    def __post_init__(self):
        """Set live_port to port + 1 if not specified."""
        if self.live_port is None:
            # Use object.__setattr__ since dataclass is frozen
            object.__setattr__(self, 'live_port', self.port + 1)

    def validate(self) -> Tuple[bool, List[str]]:
        """
        Validate the connection configuration.

        Returns:
            Tuple of (is_valid, list_of_error_messages)

        Validation rules:
            - IP address must be valid IPv4 format (xxx.xxx.xxx.xxx)
            - Port must be in range 1-65535
            - Live port must be in range 1-65535
            - Live port must be different from command port
            - Timeout must be positive

        Example:
            >>> config = ConnectionConfig("invalid", 99999, 99999, -1.0)
            >>> valid, errors = config.validate()
            >>> print(errors)
            ['Invalid IP address format: invalid',
             'Port out of range (1-65535): 99999',
             'Live port out of range (1-65535): 99999',
             'Timeout must be positive: -1.0']
        """
        errors = []

        # Validate IP address format
        if not _IPV4_PATTERN.match(self.ip_address):
            errors.append(f"Invalid IP address format: {self.ip_address}")

        # Validate port range
        if not (1 <= self.port <= 65535):
            errors.append(f"Port out of range (1-65535): {self.port}")

        # Validate live port range
        if not (1 <= self.live_port <= 65535):
            errors.append(f"Live port out of range (1-65535): {self.live_port}")

        # Validate ports are different
        if self.port == self.live_port:
            errors.append(f"Command port and live port must be different: {self.port}")

        # Validate timeout
        if self.timeout <= 0:
            errors.append(f"Timeout must be positive: {self.timeout}")

        return (len(errors) == 0, errors)


class ConnectionState(Enum):
    """
    Enumeration of possible connection states.

    States:
        DISCONNECTED: Not connected to microscope
        CONNECTING: Connection attempt in progress
        CONNECTED: Successfully connected to microscope
        ERROR: Connection error occurred
    """

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class ConnectionStatus:
    """
    Current status of a connection.

    Attributes:
        state: Current connection state
        ip: IP address if connected, None otherwise
        port: Port number if connected, None otherwise
        connected_at: Timestamp when connection was established, None if not connected
        last_error: Last error message if state is ERROR, None otherwise

    Example:
        >>> status = ConnectionStatus(
        ...     state=ConnectionState.CONNECTED,
        ...     ip="192.168.1.100",
        ...     port=53717,
        ...     connected_at=datetime.now(),
        ...     last_error=None
        ... )
    """

    state: ConnectionState
    ip: Optional[str] = None
    port: Optional[int] = None
    connected_at: Optional[datetime] = None
    last_error: Optional[str] = None


class ConnectionModel:
    """
    Observable model for connection state management.

    This class implements the Observer pattern, allowing UI components
    to register callbacks that are triggered when the connection status changes.

    Attributes:
        status: Current connection status (use property getter/setter)

    Methods:
        add_observer: Register a callback for status changes
        remove_observer: Unregister a callback

    Example:
        >>> model = ConnectionModel()
        >>>
        >>> def on_status_changed(status):
        ...     print(f"Connection state: {status.state.value}")
        >>>
        >>> model.add_observer(on_status_changed)
        >>>
        >>> # This will trigger the callback
        >>> model.status = ConnectionStatus(
        ...     state=ConnectionState.CONNECTED,
        ...     ip="192.168.1.100",
        ...     port=53717,
        ...     connected_at=datetime.now()
        ... )
    """

    def __init__(self):
        """Initialize the connection model with disconnected state."""
        self._status = ConnectionStatus(state=ConnectionState.DISCONNECTED)
        self._observers: List[Callable[[ConnectionStatus], None]] = []

    @property
    def status(self) -> ConnectionStatus:
        """
        Get the current connection status.

        Returns:
            Current ConnectionStatus object
        """
        return self._status

    @status.setter
    def status(self, new_status: ConnectionStatus) -> None:
        """
        Set the connection status and notify all observers.

        Args:
            new_status: New ConnectionStatus to set

        Note:
            All registered observers will be called with the new status.
        """
        self._status = new_status
        self._notify()

    def add_observer(self, callback: Callable[[ConnectionStatus], None]) -> None:
        """
        Register a callback to be notified of status changes.

        Args:
            callback: Function that takes a ConnectionStatus parameter

        Example:
            >>> def my_callback(status):
            ...     print(f"New state: {status.state}")
            >>> model.add_observer(my_callback)
        """
        if callback not in self._observers:
            self._observers.append(callback)

    def remove_observer(self, callback: Callable[[ConnectionStatus], None]) -> None:
        """
        Unregister a callback.

        Args:
            callback: The callback function to remove

        Note:
            If the callback is not registered, this is a no-op.
        """
        if callback in self._observers:
            self._observers.remove(callback)

    def _notify(self) -> None:
        """
        Notify all registered observers of the current status.

        This is called automatically when status is set via the property setter.
        """
        for observer in self._observers:
            observer(self._status)
