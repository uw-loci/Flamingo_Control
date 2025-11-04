# ============================================================================
# src/py2flamingo/controllers/connection_controller.py
"""
Connection Controller for Flamingo MVC Architecture.

Orchestrates connection operations between UI and service layer.
Handles user actions related to connection management.
"""

import re
import logging
from typing import Tuple, Dict, Any, Optional
from pathlib import Path

from ..services import MVCConnectionService, ConfigurationManager
from ..models import ConnectionModel, ConnectionConfig, ConnectionState


class ConnectionController:
    """
    Controller for connection operations.

    Orchestrates connection UI interactions with the connection service layer.
    Provides user-friendly error messages and input validation.

    Attributes:
        _service: Connection service for network operations
        _model: Connection model for state management
        _logger: Logger instance
    """

    def __init__(self, service: MVCConnectionService, model: ConnectionModel,
                 config_manager: Optional[ConfigurationManager] = None):
        """
        Initialize controller with dependencies.

        Args:
            service: Connection service for network operations
            model: Connection model for state management
            config_manager: Optional configuration manager for saving configurations
        """
        self._service = service
        self._model = model
        self._config_manager = config_manager
        self._logger = logging.getLogger(__name__)

    def connect(self, ip: str, port: int) -> Tuple[bool, str]:
        """
        Attempt to connect to microscope.

        Validates input parameters, creates connection configuration,
        and attempts to establish connection via service layer.

        Args:
            ip: IP address (e.g., "127.0.0.1")
            port: Port number (1-65535)

        Returns:
            Tuple of (success, message):
                - (True, "Connected successfully") on success
                - (False, "Invalid IP address format") on validation error
                - (False, "Connection failed: timeout") on connection error
        """
        # Validate IP address
        if not ip or not isinstance(ip, str):
            return (False, "IP address cannot be empty")

        if not self._validate_ip(ip):
            return (False, f"Invalid IP address format. Expected: XXX.XXX.XXX.XXX, got: {ip}")

        # Validate port
        if not isinstance(port, int):
            return (False, f"Port must be an integer, got {type(port).__name__}")

        if not (1 <= port <= 65535):
            return (False, f"Port must be between 1 and 65535, got {port}")

        # Check if already connected
        if self._service.is_connected():
            return (False, "Already connected. Disconnect first before connecting again.")

        # Create config
        try:
            config = ConnectionConfig(
                ip_address=ip,
                port=port,
                live_port=port + 1,
                timeout=2.0
            )

            # Validate config
            valid, errors = config.validate()
            if not valid:
                error_msg = "; ".join(errors)
                return (False, f"Configuration error: {error_msg}")

        except Exception as e:
            self._logger.exception("Error creating connection config")
            return (False, f"Configuration error: {str(e)}")

        # Attempt connection
        try:
            self._service.connect(config)
            self._logger.info(f"Connected to {ip}:{port}")
            return (True, f"Connected to {ip}:{port}")

        except TimeoutError:
            self._logger.error(f"Connection timeout for {ip}:{port}")
            return (False, "Connection timeout. Is the server running?")

        except ConnectionRefusedError:
            self._logger.error(f"Connection refused by {ip}:{port}")
            return (False, f"Connection refused. Is the server listening on port {port}?")

        except OSError as e:
            self._logger.error(f"Network error: {e}")
            if "Network is unreachable" in str(e):
                return (False, f"Network unreachable. Check network connection.")
            elif "No route to host" in str(e):
                return (False, f"No route to host {ip}. Check IP address and network.")
            else:
                return (False, f"Network error: {str(e)}")

        except Exception as e:
            self._logger.exception("Unexpected error during connection")
            return (False, f"Unexpected error: {str(e)}")

    def disconnect(self) -> Tuple[bool, str]:
        """
        Disconnect from microscope.

        Returns:
            Tuple of (success, message):
                - (True, "Disconnected successfully") on success
                - (False, "Not connected") if already disconnected
                - (False, error message) on error
        """
        # Check if connected
        if not self._service.is_connected():
            return (False, "Not connected")

        try:
            self._service.disconnect()
            self._logger.info("Disconnected successfully")
            return (True, "Disconnected successfully")

        except Exception as e:
            self._logger.exception("Error during disconnect")
            return (False, f"Disconnect error: {str(e)}")

    def reconnect(self) -> Tuple[bool, str]:
        """
        Reconnect to microscope using last configuration.

        Returns:
            Tuple of (success, message):
                - (True, "Reconnected successfully") on success
                - (False, "No previous connection") if never connected
                - (False, error message) on error
        """
        # Check if we have a previous connection
        status = self._model.status
        if not status.ip or not status.port:
            return (False, "No previous connection to reconnect to")

        # Disconnect first if connected
        if self._service.is_connected():
            disconnect_result = self.disconnect()
            if not disconnect_result[0]:
                return (False, f"Failed to disconnect before reconnecting: {disconnect_result[1]}")

        # Reconnect using previous config
        try:
            self._service.reconnect()
            self._logger.info(f"Reconnected to {status.ip}:{status.port}")
            return (True, f"Reconnected to {status.ip}:{status.port}")

        except TimeoutError:
            self._logger.error("Reconnection timeout")
            return (False, "Reconnection timeout. Is the server running?")

        except ConnectionRefusedError:
            self._logger.error("Reconnection refused")
            return (False, "Reconnection refused. Is the server still running?")

        except Exception as e:
            self._logger.exception("Error during reconnect")
            return (False, f"Reconnection error: {str(e)}")

    def get_connection_status(self) -> Dict[str, Any]:
        """
        Get current connection status for UI display.

        Returns:
            Dictionary with connection state information:
                - 'connected': bool - Whether connected
                - 'state': str - Connection state name
                - 'ip': str or None - IP address if connected
                - 'port': int or None - Port number if connected
                - 'connected_at': datetime or None - Connection timestamp
                - 'last_error': str or None - Last error message
        """
        status = self._model.status

        return {
            'connected': self._service.is_connected(),
            'state': status.state.value if status.state else 'unknown',
            'ip': status.ip,
            'port': status.port,
            'connected_at': status.connected_at,
            'last_error': status.last_error
        }

    def handle_connection_error(self, error: Exception) -> str:
        """
        Convert exception to user-friendly error message.

        Args:
            error: Exception that occurred

        Returns:
            User-friendly error message string
        """
        error_type = type(error).__name__
        error_msg = str(error)

        # Map common errors to friendly messages
        if isinstance(error, TimeoutError):
            return "Connection timeout. Is the server running?"

        elif isinstance(error, ConnectionRefusedError):
            return "Connection refused. Is the server listening on the specified port?"

        elif isinstance(error, ConnectionResetError):
            return "Connection reset by server. The server may have crashed."

        elif isinstance(error, OSError):
            if "Network is unreachable" in error_msg:
                return "Network unreachable. Check your network connection."
            elif "No route to host" in error_msg:
                return "No route to host. Check IP address and network settings."
            elif "Address already in use" in error_msg:
                return "Port already in use. Try a different port or close other applications."
            else:
                return f"Network error: {error_msg}"

        elif isinstance(error, ValueError):
            return f"Invalid value: {error_msg}"

        else:
            # Generic error message
            self._logger.exception(f"Unhandled error type: {error_type}")
            return f"Error: {error_msg}"

    def test_connection(self, ip: str, port: int, timeout: float = 2.0) -> Tuple[bool, str]:
        """
        Test connection to microscope without establishing a persistent connection.

        Attempts to connect and immediately disconnect to verify the server
        is reachable. Useful for validating configurations before connecting.

        Args:
            ip: IP address to test
            port: Port number to test
            timeout: Connection timeout in seconds (default: 2.0)

        Returns:
            Tuple of (success, message):
                - (True, "Connection test successful") on success
                - (False, error message) on failure
        """
        import socket

        # Validate inputs
        if not ip or not isinstance(ip, str):
            return (False, "IP address cannot be empty")

        if not self._validate_ip(ip):
            return (False, f"Invalid IP address format: {ip}")

        if not isinstance(port, int) or not (1 <= port <= 65535):
            return (False, f"Invalid port number: {port}")

        # Test connection
        self._logger.info(f"Testing connection to {ip}:{port}")

        try:
            # Try to connect to command port
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)

            try:
                sock.connect((ip, port))
                sock.close()
                self._logger.info(f"Connection test successful: {ip}:{port}")
                return (True, f"Connection test successful! Server is reachable at {ip}:{port}")

            except socket.timeout:
                self._logger.warning(f"Connection test timeout: {ip}:{port}")
                return (False, f"Connection timeout. Server at {ip}:{port} is not responding.")

            except ConnectionRefusedError:
                self._logger.warning(f"Connection test refused: {ip}:{port}")
                return (False, f"Connection refused. Server is not listening on port {port}.")

            except OSError as e:
                self._logger.warning(f"Connection test OS error: {e}")
                if "Network is unreachable" in str(e):
                    return (False, "Network unreachable. Check network connection.")
                elif "No route to host" in str(e):
                    return (False, f"No route to host {ip}. Check IP address.")
                else:
                    return (False, f"Network error: {str(e)}")

            finally:
                try:
                    sock.close()
                except:
                    pass

        except Exception as e:
            self._logger.exception("Unexpected error during connection test")
            return (False, f"Test error: {str(e)}")

    def _validate_ip(self, ip: str) -> bool:
        """
        Validate IPv4 address format.

        Args:
            ip: IP address string to validate

        Returns:
            True if valid IPv4 format, False otherwise
        """
        # IPv4 regex pattern
        pattern = r'^(\d{1,3}\.){3}\d{1,3}$'

        if not re.match(pattern, ip):
            return False

        # Check each octet is 0-255
        try:
            octets = ip.split('.')
            return all(0 <= int(octet) <= 255 for octet in octets)
        except (ValueError, AttributeError):
            return False

    def save_configuration(self, name: str, ip: str, port: int) -> Tuple[bool, str]:
        """
        Save a connection configuration for future use.

        Creates a configuration file that will appear in the dropdown list
        after the next refresh or application restart.

        Args:
            name: Display name for the configuration (e.g., "N7-10GB")
            ip: IP address (e.g., "192.168.1.1")
            port: Port number (e.g., 53717)

        Returns:
            Tuple of (success, message):
                - (True, "Configuration saved successfully") on success
                - (False, error message) on failure

        Example:
            >>> controller.save_configuration("N7-10GB", "192.168.1.1", 53717)
            (True, "Configuration 'N7-10GB' saved successfully")
        """
        # Check if config manager is available
        if self._config_manager is None:
            return (False, "Configuration manager not available")

        # Validate inputs
        if not name or not name.strip():
            return (False, "Configuration name cannot be empty")

        if not ip or not isinstance(ip, str):
            return (False, "IP address cannot be empty")

        if not self._validate_ip(ip):
            return (False, f"Invalid IP address format: {ip}")

        if not isinstance(port, int) or not (1 <= port <= 65535):
            return (False, f"Invalid port number: {port}")

        # Save configuration via configuration manager
        try:
            success, message = self._config_manager.save_configuration(
                name=name.strip(),
                ip=ip,
                port=port
            )

            if success:
                self._logger.info(f"Saved configuration: {name}")
            else:
                self._logger.warning(f"Failed to save configuration '{name}': {message}")

            return (success, message)

        except Exception as e:
            self._logger.exception(f"Error saving configuration: {e}")
            return (False, f"Error: {str(e)}")

    def delete_configuration(self, name: str) -> Tuple[bool, str]:
        """
        Delete a saved configuration.

        Removes the configuration from persistent storage.

        Args:
            name: Name of the configuration to delete

        Returns:
            Tuple of (success, message):
                - (True, "Configuration deleted successfully") on success
                - (False, error message) on failure

        Example:
            >>> controller.delete_configuration("Old Config")
            (True, "Configuration 'Old Config' deleted successfully")
        """
        # Check if config manager is available
        if self._config_manager is None:
            return (False, "Configuration manager not available")

        # Validate input
        if not name or not name.strip():
            return (False, "Configuration name cannot be empty")

        # Delete configuration via configuration manager
        try:
            success, message = self._config_manager.delete_configuration(name.strip())

            if success:
                self._logger.info(f"Deleted configuration: {name}")
            else:
                self._logger.warning(f"Failed to delete configuration '{name}': {message}")

            return (success, message)

        except Exception as e:
            self._logger.exception(f"Error deleting configuration: {e}")
            return (False, f"Error: {str(e)}")
