"""
Stage subsystem service for Flamingo microscope.

Handles all stage-related commands including position queries,
movement control, and motion monitoring.
"""

from typing import Optional, Dict, Any

from py2flamingo.services.microscope_command_service import MicroscopeCommandService
from py2flamingo.models.microscope import Position


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

        # Parse the full response to check all fields
        parsed = result.get('parsed', {})
        raw_response = result.get('raw_response', b'')

        # Log the complete response for debugging
        if parsed:
            status_code = parsed.get('status_code', 0)
            params = parsed.get('params', [])

            # Log status and params to understand motion state indicators
            self.logger.debug(f"{axis_name}-axis response - Status: 0x{status_code:08X}, "
                            f"Params: {[f'0x{p:08X}' for p in params]}")

        # Position is returned in the doubleData field (bytes 40-47 of SCommand)
        # NOT in the data buffer - that's a common mistake!
        import struct
        if len(raw_response) >= 48:  # Need at least 48 bytes to read doubleData field
            try:
                # Position in doubleData field (bytes 40-47)
                # SCommand structure: [start(4) + cmd(4) + status(4) + params(28) + doubleData(8) + ...]
                position = struct.unpack('<d', raw_response[40:48])[0]

                # Check if stage is still moving based on response
                # We need to examine multiple indicators:
                # 1. Position value of 0.000 for X,Y,Z (which shouldn't normally be exactly 0)
                # 2. Status code that might indicate motion
                # 3. Parameters that might contain motion flags

                is_likely_moving = False
                motion_reason = ""

                # Check 1: Position is exactly 0.000 for axes that shouldn't be at origin
                if position == 0.0 and axis in [1, 2, 3]:  # X, Y, Z should never be exactly 0.0
                    is_likely_moving = True
                    motion_reason = "position=0.000"

                # Check 2: Examine status code (if non-zero might indicate busy/moving)
                if parsed and not is_likely_moving:
                    status_code = parsed.get('status_code', 0)
                    if status_code != 0:
                        # Log at DEBUG level - status 0x00000001 is common and usually means "ready"
                        # Only status 0x00000000 means "idle" - non-zero doesn't imply motion
                        self.logger.debug(
                            f"{axis_name}-axis status code: 0x{status_code:08X} (non-zero is normal)"
                        )
                        # Specific status codes might indicate motion - needs reverse engineering
                        # For now, log but don't assume motion just from non-zero status

                # Check 3: Examine parameters for motion flags
                if parsed and not is_likely_moving:
                    params = parsed.get('params', [])
                    # Check if any param has specific motion-indicating bits
                    # params[6] often contains flags (cmdDataBits0)
                    if len(params) > 6:
                        flags = params[6]
                        # Check for specific flag patterns that might indicate motion
                        # This needs more investigation of the protocol
                        if flags != 0 and flags != 0x80000000:  # 0x80000000 is TRIGGER_CALL_BACK
                            self.logger.debug(
                                f"{axis_name}-axis params[6] flags: 0x{flags:08X} - checking for motion indicators"
                            )

                if is_likely_moving:
                    self.logger.info(
                        f"{axis_name}-axis appears to be moving ({motion_reason}), will retry..."
                    )

                    # Keep retrying until we get a valid position or timeout
                    import time
                    max_retries = 6   # Up to 6 retries (reduced from 20 - movements take <1s)
                    retry_delay = 0.5  # 500ms between retries
                    total_timeout = 3.0  # Total max wait time of 3 seconds (reduced from 10)

                    for retry_count in range(1, max_retries + 1):
                        time.sleep(retry_delay)

                        retry_result = self._query_command(
                            StageCommandCode.POSITION_GET,
                            f"STAGE_POSITION_GET_{axis_name}_RETRY_{retry_count}",
                            params=[
                                0,     # params[0] (hardwareID) - not used
                                0,     # params[1] (subsystemID) - not used
                                0,     # params[2] (clientID) - not used
                                axis,  # params[3] (int32Data0) = axis code (1=X, 2=Y, 3=Z, 4=R)
                                0,     # params[4] (int32Data1)
                                0      # params[5] (int32Data2)
                                # params[6] will be set to TRIGGER_CALL_BACK by _query_command
                            ]
                        )

                        if retry_result and retry_result.get('success'):
                            retry_parsed = retry_result.get('parsed', {})
                            raw_response = retry_result.get('raw_response', b'')

                            # Log retry response details for investigation
                            if retry_parsed and retry_count == 1:  # Log details on first retry
                                retry_status = retry_parsed.get('status_code', 0)
                                retry_params = retry_parsed.get('params', [])
                                self.logger.debug(
                                    f"{axis_name}-axis retry response - Status: 0x{retry_status:08X}, "
                                    f"Params[6]: 0x{retry_params[6] if len(retry_params) > 6 else 0:08X}"
                                )

                            if len(raw_response) >= 48:
                                try:
                                    position = struct.unpack('<d', raw_response[40:48])[0]

                                    # For non-rotation axes, 0.000 likely means still moving
                                    if position != 0.0 or axis == 4:  # Accept 0.0 for rotation
                                        # CRITICAL: Apply safety check to retry results too!
                                        # Without this, garbage values during movement could pass through
                                        retry_valid_ranges = {
                                            1: (1.0, 12.31),    # X-axis limits (mm)
                                            2: (5.0, 25.0),     # Y-axis limits (mm)
                                            3: (12.5, 26.0),    # Z-axis limits (mm)
                                            4: (-720.0, 720.0)  # R-axis limits (degrees)
                                        }
                                        if axis in retry_valid_ranges:
                                            min_val, max_val = retry_valid_ranges[axis]
                                            if not (min_val <= position <= max_val):
                                                self.logger.warning(
                                                    f"{axis_name}-axis retry {retry_count}: position {position:.3f} is "
                                                    f"OUT OF RANGE ({min_val}-{max_val}) - likely garbage value, continuing retry..."
                                                )
                                                continue  # Keep retrying instead of returning bad value

                                        self.logger.info(
                                            f"{axis_name}-axis position after {retry_count} "
                                            f"retries ({retry_count * retry_delay:.1f}s): {position:.3f} mm"
                                        )
                                        return float(position)
                                    else:
                                        self.logger.debug(
                                            f"{axis_name}-axis retry {retry_count}: still moving (0.000)"
                                        )
                                except Exception as e:
                                    self.logger.warning(f"Error parsing retry {retry_count}: {e}")

                        # Check if we've exceeded total timeout
                        if retry_count * retry_delay >= total_timeout:
                            self.logger.warning(
                                f"{axis_name}-axis: Stage still indicating motion after {total_timeout}s - "
                                f"movement may be taking unusually long or there may be a communication issue"
                            )
                            break

                    # If we get here, all retries failed
                    self.logger.error(
                        f"{axis_name}-axis: Unable to get valid position after {max_retries} retries. "
                        f"Stage may still be moving or there may be a hardware issue."
                    )
                    return None

                # Log position from hardware - trust the hardware response
                # Stage limits vary by microscope and should not be hardcoded here
                self.logger.info(f"{axis_name}-axis position: {position:.3f} mm")
                return float(position)
            except Exception as e:
                self.logger.error(f"Failed to parse position from doubleData field: {e}")
                return None
        else:
            self.logger.error(f"Response too short ({len(raw_response)} bytes) to contain doubleData field")
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

        Uses the base class _send_command() which properly handles async reader
        integration when active.

        Args:
            command_code: Command code from StageCommandCode
            command_name: Human-readable command name for logging
            axis: Axis code (0-3)
            position_mm: Target position in millimeters

        Returns:
            Dict with 'success' and optional 'error'
        """
        # Use base class _send_command which handles async reader properly
        # params[3] (int32Data0) = axis code
        return self._send_command(
            command_code=command_code,
            command_name=command_name,
            params=[
                0,     # params[0] (hardwareID) - not used
                0,     # params[1] (subsystemID) - not used
                0,     # params[2] (clientID) - not used
                axis,  # params[3] (int32Data0) = axis code (1=X, 2=Y, 3=Z, 4=R)
                0,     # params[4] (int32Data1)
                0,     # params[5] (int32Data2)
                0      # params[6] will be set to TRIGGER_CALL_BACK by _send_command
            ],
            value=position_mm  # doubleData = position in mm
        )
