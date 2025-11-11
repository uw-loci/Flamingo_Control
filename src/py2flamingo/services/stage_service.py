"""
Stage subsystem service for Flamingo microscope.

Handles all stage-related commands including position queries,
movement control, and motion monitoring.
"""

import socket
from typing import Optional, Dict, Any

from py2flamingo.services.microscope_command_service import MicroscopeCommandService
from py2flamingo.models.microscope import Position
from py2flamingo.core.tcp_protocol import CommandDataBits


class StageCommandCode:
    """Stage subsystem command codes from CommandCodes.h (0x6000 range)."""

    # Query commands
    POSITION_GET = 24584  # 0x6008
    MOTION_STOPPED = 24592  # 0x6010

    # Movement commands
    POSITION_SET = 24580  # 0x6004
    POSITION_SET_SLIDER = 24581  # 0x6005 - from GUI slider
    POSITION_SET_TEXT = 24582  # 0x6006 - from GUI text entry


class AxisCode:
    """
    Stage axis codes for movement commands.

    From oldcodereference/microscope_connect.py lines 108-111:
    - move_axis(..., 1, x)  # X-axis
    - move_axis(..., 2, y)  # Y-axis
    - move_axis(..., 3, z)  # Z-axis
    - move_axis(..., 4, r)  # Rotation
    """
    X_AXIS = 1
    Y_AXIS = 2
    Z_AXIS = 3
    ROTATION = 4


class StageService(MicroscopeCommandService):
    """
    Service for stage operations on Flamingo microscope.

    Provides high-level methods for stage control including position
    queries and movement commands.

    Example:
        >>> stage = StageService(connection)
        >>> stage.move_to_position(AxisCode.Y_AXIS, 10.5)  # Move Y to 10.5mm
        >>> # Wait for motion to complete, then query position
        >>> position = stage.get_position()
    """

    def get_axis_position(self, axis: int) -> Optional[float]:
        """
        Query position of a single axis from hardware.

        IMPORTANT: Stage position queries require specifying which axis in params[3] (int32Data0):
        - params[3] = 1 for X-axis
        - params[3] = 2 for Y-axis
        - params[3] = 3 for Z-axis
        - params[3] = 4 for R-axis (rotation)

        Args:
            axis: Axis code (AxisCode.X_AXIS, Y_AXIS, Z_AXIS, or ROTATION)

        Returns:
            Position value for the axis in millimeters, or None if command times out

        Raises:
            RuntimeError: If communication fails

        Example:
            >>> x_pos = stage_service.get_axis_position(AxisCode.X_AXIS)
            >>> if x_pos is not None:
            >>>     print(f"X position: {x_pos}")
        """
        axis_names = {1: 'X', 2: 'Y', 3: 'Z', 4: 'R'}
        axis_name = axis_names.get(axis, f'Unknown({axis})')

        self.logger.info(f"Querying {axis_name}-axis position from hardware...")

        # CRITICAL: params[3] (int32Data0) must specify the axis to query
        result = self._query_command(
            StageCommandCode.POSITION_GET,
            f"STAGE_POSITION_GET_{axis_name}",
            params=[
                0,     # params[0] (hardwareID) - not used
                0,     # params[1] (subsystemID) - not used
                0,     # params[2] (clientID) - not used
                axis,  # params[3] (int32Data0) = axis code (1=X, 2=Y, 3=Z, 4=R)
                0,     # params[4] (int32Data1)
                0,     # params[5] (int32Data2)
                0      # params[6] will be set to TRIGGER_CALL_BACK by _query_command
            ]
        )

        if not result['success']:
            if result.get('error') == 'timeout':
                self.logger.warning(f"{axis_name}-axis POSITION_GET timed out")
                return None
            raise RuntimeError(f"Failed to get {axis_name} position: {result.get('error', 'Unknown error')}")

        # Position is returned in the 72-byte data buffer (bytes 52-123 of response)
        # Try to parse as double (8 bytes)
        import struct
        raw_response = result.get('raw_response', b'')
        if len(raw_response) >= 60:  # At least 52 + 8 bytes
            try:
                # Position at start of data buffer (offset 52)
                position = struct.unpack('<d', raw_response[52:60])[0]
                self.logger.info(f"{axis_name}-axis position: {position}")
                return float(position)
            except Exception as e:
                self.logger.error(f"Failed to parse position from data buffer: {e}")
                return None
        else:
            self.logger.error(f"Response too short to contain position data")
            return None

    def get_position(self) -> Optional[Position]:
        """
        Query current stage position from hardware for all axes.

        Queries each axis individually (X, Y, Z, R) as querying all at once (0xFF) doesn't work.

        Returns:
            Position object with x, y, z, r coordinates in millimeters, or None if any axis query times out

        Raises:
            RuntimeError: If communication fails

        Example:
            >>> pos = stage_service.get_position()
            >>> if pos:
            >>>     print(f"Stage at X={pos.x}, Y={pos.y}, Z={pos.z}, R={pos.r}")
        """
        self.logger.info("Querying all axis positions from hardware...")

        # Query each axis individually (0xFF doesn't work, must query one at a time)
        x_pos = self.get_axis_position(AxisCode.X_AXIS)
        if x_pos is None:
            return None

        y_pos = self.get_axis_position(AxisCode.Y_AXIS)
        if y_pos is None:
            return None

        z_pos = self.get_axis_position(AxisCode.Z_AXIS)
        if z_pos is None:
            return None

        r_pos = self.get_axis_position(AxisCode.ROTATION)
        if r_pos is None:
            return None

        # Create Position object with all axes
        position = Position(x=x_pos, y=y_pos, z=z_pos, r=r_pos)
        self.logger.info(f"Complete stage position: {position}")

        return position

    def move_to_position(self, axis: int, position_mm: float) -> None:
        """
        Move stage to absolute position on specified axis.

        This command returns immediately while stage moves asynchronously.
        Use motion monitoring to detect completion.

        Args:
            axis: Axis code (AxisCode.X_AXIS, Y_AXIS, Z_AXIS, ROTATION)
            position_mm: Target position in millimeters

        Raises:
            RuntimeError: If command fails or microscope not connected

        Example:
            >>> stage_service.move_to_position(AxisCode.Y_AXIS, 7.635)
            >>> # Stage begins moving asynchronously
            >>> # Wait for motion stopped callback or poll is_motion_stopped()
        """
        self.logger.info(f"Moving axis {axis} to {position_mm} mm...")

        result = self._send_movement_command(
            StageCommandCode.POSITION_SET_SLIDER,  # Use slider variant from logs
            "STAGE_POSITION_SET",
            axis=axis,
            position_mm=position_mm
        )

        if not result['success']:
            raise RuntimeError(f"Failed to move stage: {result.get('error', 'Unknown error')}")

        self.logger.info(f"Stage movement command sent (axis {axis} → {position_mm} mm)")
        self.logger.info("Motion is asynchronous - use motion monitoring to detect completion")

    def is_motion_stopped(self) -> Optional[bool]:
        """
        Query if stage motion has stopped.

        Note: The microscope may also send unsolicited motion-stopped callbacks.

        Returns:
            True if stopped, False if moving, None if command times out

        Raises:
            RuntimeError: If communication fails

        Example:
            >>> stopped = stage_service.is_motion_stopped()
            >>> if stopped:
            >>>     print("Stage has stopped")
        """
        self.logger.info("Querying motion stopped status...")

        result = self._query_command(
            StageCommandCode.MOTION_STOPPED,
            "STAGE_MOTION_STOPPED"
        )

        if not result['success']:
            if result.get('error') == 'timeout':
                self.logger.warning("STAGE_MOTION_STOPPED timed out")
                return None
            raise RuntimeError(f"Failed to query motion: {result.get('error', 'Unknown error')}")

        # Parse motion status from response
        # TODO: Determine response format
        return None

    def _send_movement_command(self, command_code: int, command_name: str,
                               axis: int, position_mm: float) -> Dict[str, Any]:
        """
        Send stage movement command.

        Args:
            command_code: Command code from StageCommandCode
            command_name: Human-readable command name for logging
            axis: Axis code (0-3)
            position_mm: Target position in millimeters

        Returns:
            Dict with 'success' and optional 'error'
        """
        if not self.connection.is_connected():
            return {
                'success': False,
                'error': 'Not connected to microscope'
            }

        try:
            # Encode command with movement parameters
            # params[3] (int32Data0) = axis code, doubleData = position
            cmd_bytes = self.connection.encoder.encode_command(
                code=command_code,
                status=0,
                params=[
                    0,     # params[0] (hardwareID) - not used
                    0,     # params[1] (subsystemID) - not used
                    0,     # params[2] (clientID) - not used
                    axis,  # params[3] (int32Data0) = axis code (1=X, 2=Y, 3=Z, 4=R)
                    0,     # params[4] (int32Data1)
                    0,     # params[5] (int32Data2)
                    CommandDataBits.TRIGGER_CALL_BACK  # params[6] = flag
                ],
                value=position_mm,  # doubleData = position in mm
                data=b''
            )

            # Get command socket
            command_socket = self.connection._command_socket
            if command_socket is None:
                return {
                    'success': False,
                    'error': 'Command socket not available'
                }

            # Send command
            command_socket.sendall(cmd_bytes)
            self.logger.debug(f"Sent {command_name} (axis={axis}, position={position_mm}mm)")

            # Read acknowledgment (movement commands return immediately)
            ack_response = self._receive_full_bytes(command_socket, 128, timeout=3.0)

            # Parse response
            parsed = self._parse_response(ack_response)

            return {
                'success': True,
                'parsed': parsed
            }

        except (socket.timeout, TimeoutError) as e:
            self.logger.error(f"Timeout sending {command_name}")
            return {
                'success': False,
                'error': 'timeout'
            }
        except Exception as e:
            self.logger.error(f"Error in {command_name}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
