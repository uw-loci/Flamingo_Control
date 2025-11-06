"""
Stage subsystem service for Flamingo microscope.

Handles all stage-related commands including position queries,
movement control, and motion monitoring.
"""

from typing import Optional

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

    Based on microscope logs showing int32Data0 axis values.
    """
    X_AXIS = 0
    Y_AXIS = 1
    Z_AXIS = 2  # or 3 based on logs?
    ROTATION = 3  # or 2 based on logs?

    # Note: Logs show movement with int32Data0=1 (Y) and int32Data0=3 (Z)
    # Need to verify exact mapping with more testing


class StageService(MicroscopeCommandService):
    """
    Service for stage operations on Flamingo microscope.

    Provides high-level methods for stage control including position
    queries and movement commands.

    Note:
        Position feedback may not be available from hardware. Many microscopes
        require software-side position tracking. Use get_position() to check
        if hardware position feedback is implemented.

    Example:
        >>> stage = StageService(connection)
        >>> stage.move_to_position(AxisCode.Y_AXIS, 10.5)  # Move Y to 10.5mm
        >>> # Wait for motion to complete
    """

    def get_position(self) -> Optional[Position]:
        """
        Query current stage position from hardware.

        WARNING: This command may not be implemented in all microscope models.
        Many microscopes do not provide position feedback and require software
        to track position locally.

        Returns:
            Position object if hardware supports position feedback, None if not implemented

        Raises:
            RuntimeError: If communication fails (vs. command not implemented)

        Example:
            >>> pos = stage_service.get_position()
            >>> if pos:
            >>>     print(f"Stage at X={pos.x}, Y={pos.y}, Z={pos.z}")
            >>> else:
            >>>     print("Position feedback not available - using local tracking")
        """
        self.logger.info("Querying stage position from hardware...")

        result = self._query_command(
            StageCommandCode.POSITION_GET,
            "STAGE_POSITION_GET"
        )

        if not result['success']:
            if result.get('error') == 'timeout':
                self.logger.warning("STAGE_POSITION_GET timed out - position feedback not available")
                return None
            raise RuntimeError(f"Failed to get position: {result.get('error', 'Unknown error')}")

        # Parse position from response
        # TODO: Determine which fields contain position data
        # Based on logs, may be in params or additional data
        params = result['parsed']['params']
        self.logger.info(f"Position query response params: {params}")

        # For now, return None until we determine response format
        return None

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

        WARNING: This command may not be implemented. The microscope may
        send unsolicited motion-stopped callbacks instead.

        Returns:
            True if stopped, False if moving, None if command not implemented

        Raises:
            RuntimeError: If communication fails

        Example:
            >>> stopped = stage_service.is_motion_stopped()
            >>> if stopped is None:
            >>>     print("Use motion callbacks instead of polling")
            >>> elif stopped:
            >>>     print("Stage has stopped")
        """
        self.logger.info("Querying motion stopped status...")

        result = self._query_command(
            StageCommandCode.MOTION_STOPPED,
            "STAGE_MOTION_STOPPED"
        )

        if not result['success']:
            if result.get('error') == 'timeout':
                self.logger.warning("STAGE_MOTION_STOPPED timed out - use callbacks instead")
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
            # Based on logs: int32Data0 = axis, doubleData = position
            cmd_bytes = self.connection.encoder.encode_command(
                code=command_code,
                status=0,
                params=[
                    axis,  # Param[0] = axis (int32Data0 in logs)
                    0,     # Param[1] = unused (int32Data1)
                    0,     # Param[2] = unused (int32Data2)
                    0,     # Param[3]
                    0,     # Param[4]
                    0,     # Param[5]
                    CommandDataBits.TRIGGER_CALL_BACK  # Param[6] = flag
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
