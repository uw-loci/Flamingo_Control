# ============================================================================
# src/py2flamingo/services/status_service.py
"""
MVC-compliant status service for querying microscope status.

This service uses the new Core/Models layers to query microscope state
following the MVC pattern.
"""

import logging
from typing import Dict, Any, Tuple, Optional
from datetime import datetime, timedelta


class StatusService:
    """
    Service for querying microscope status.

    This service handles status queries, ping checks, and position retrieval
    with caching to reduce network traffic.

    Attributes:
        connection_service: MVCConnectionService for sending commands
        logger: Logger instance
        cache: Status cache dictionary
        cache_ttl: Cache time-to-live in seconds
    """

    def __init__(self, connection_service: 'MVCConnectionService'):
        """
        Initialize status service with dependency injection.

        Args:
            connection_service: MVCConnectionService instance
        """
        self.connection_service = connection_service
        self.logger = logging.getLogger(__name__)

        # Cache configuration
        self._cache: Dict[str, Tuple[Any, datetime]] = {}
        self._cache_ttl: float = 1.0  # seconds

    def get_server_status(self) -> Dict[str, Any]:
        """
        Query server state from microscope.

        Returns:
            Dictionary with server status information

        Raises:
            RuntimeError: If not connected
            ConnectionError: If query fails
            TimeoutError: If query times out
        """
        from py2flamingo.models.command import StatusCommand
        from py2flamingo.core.tcp_protocol import CommandCode

        # Check cache
        cached = self._get_from_cache('server_status')
        if cached is not None:
            return cached

        if not self.connection_service.is_connected():
            raise RuntimeError("Not connected to microscope")

        try:
            # Create status command
            cmd = StatusCommand(
                code=CommandCode.CMD_SYSTEM_STATE_GET,
                query_type="server_status"
            )

            # Send command
            response = self.connection_service.send_command(cmd)

            # Parse response (simplified - actual parsing depends on protocol)
            status = {
                'state': 'unknown',
                'response_size': len(response) if response else 0,
                'timestamp': datetime.now().isoformat()
            }

            # Cache result
            self._put_in_cache('server_status', status)

            self.logger.info(f"Server status: {status}")
            return status

        except ConnectionError as e:
            self.logger.error(f"Connection error during status query: {e}")
            raise
        except TimeoutError as e:
            self.logger.error(f"Timeout during status query: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to get server status: {e}")
            raise

    def ping(self) -> bool:
        """
        Check if server responds (lightweight status check).

        Returns:
            True if server responds, False otherwise

        Raises:
            RuntimeError: If not connected
        """
        if not self.connection_service.is_connected():
            raise RuntimeError("Not connected to microscope")

        try:
            # Try to get server status
            status = self.get_server_status()

            # If we got a response, server is responding
            return status.get('response_size', 0) > 0

        except (ConnectionError, TimeoutError) as e:
            self.logger.warning(f"Ping failed: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Ping error: {e}")
            return False

    def get_position(self) -> Tuple[float, float, float]:
        """
        Get current XYZ position from microscope.

        Returns:
            Tuple of (x, y, z) coordinates in mm

        Raises:
            RuntimeError: If not connected
            ConnectionError: If query fails
            ValueError: If position data is invalid
        """
        from py2flamingo.models.command import Command
        from py2flamingo.core.tcp_protocol import CommandCode

        # Check cache
        cached = self._get_from_cache('position')
        if cached is not None:
            return cached

        if not self.connection_service.is_connected():
            raise RuntimeError("Not connected to microscope")

        try:
            # Create position get command
            cmd = Command(code=CommandCode.CMD_STAGE_POSITION_GET)

            # Send command
            response = self.connection_service.send_command(cmd)

            # Parse response (simplified - actual parsing depends on protocol)
            # For now, return mock position
            # TODO: Decode response based on protocol
            if not response:
                raise ValueError("Empty response from position query")

            position = (0.0, 0.0, 0.0)  # Mock position

            # Cache result
            self._put_in_cache('position', position)

            self.logger.debug(f"Position: {position}")
            return position

        except ConnectionError as e:
            self.logger.error(f"Connection error during position query: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to get position: {e}")
            raise

    def clear_cache(self) -> None:
        """Clear all cached status data."""
        self._cache.clear()
        self.logger.debug("Status cache cleared")

    def _get_from_cache(self, key: str) -> Optional[Any]:
        """
        Get value from cache if not expired.

        Args:
            key: Cache key

        Returns:
            Cached value or None if expired/missing
        """
        if key in self._cache:
            value, timestamp = self._cache[key]
            age = (datetime.now() - timestamp).total_seconds()

            if age < self._cache_ttl:
                self.logger.debug(f"Cache hit: {key} (age: {age:.2f}s)")
                return value
            else:
                self.logger.debug(f"Cache expired: {key} (age: {age:.2f}s)")
                del self._cache[key]

        return None

    def _put_in_cache(self, key: str, value: Any) -> None:
        """
        Put value in cache with current timestamp.

        Args:
            key: Cache key
            value: Value to cache
        """
        self._cache[key] = (value, datetime.now())
        self.logger.debug(f"Cached: {key}")
