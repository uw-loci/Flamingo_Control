# src/py2flamingo/controllers/position_controller.py

"""
Controller for microscope position management.

This controller handles all position-related operations including
movement, validation, and position tracking.
"""
import logging
import socket
import threading
from typing import List, Optional, Callable
from dataclasses import dataclass

from py2flamingo.models.microscope import Position, MicroscopeState
from py2flamingo.services.connection_service import ConnectionService
from py2flamingo.core.queue_manager import QueueManager
from py2flamingo.core.events import EventManager

@dataclass
class AxisCode:
    """Axis codes for stage movement commands."""
    X = 1
    Y = 2
    Z = 3
    R = 4

class PositionController:
    """
    Controller for managing microscope stage positions.

    This controller tracks position locally since the microscope hardware
    does not report current position. Position is initialized from the
    home position in settings and updated after each movement.

    Attributes:
        connection_service: Service for microscope communication
        logger: Logger instance
        axis: Axis codes for movement commands
        _current_position: Tracked current position (not queried from hardware)
        _movement_lock: Lock to prevent concurrent movement commands
    """

    # Command codes from command_list.txt
    COMMAND_CODES_STAGE_POSITION_SET = 24580
    COMMAND_CODES_STAGE_POSITION_GET = 24584  # Note: Returns settings, not position

    def __init__(self, connection_service):
        """
        Initialize the position controller.

        Args:
            connection_service: MVCConnectionService for microscope communication
        """
        self.connection = connection_service
        self.logger = logging.getLogger(__name__)
        self.axis = AxisCode()

        # Local position tracking (microscope doesn't report current position)
        self._current_position: Optional[Position] = None
        self._movement_lock = threading.Lock()

        # Try to initialize position from microscope settings
        self._initialize_position()

    def _initialize_position(self) -> None:
        """
        Initialize tracked position from microscope home position in settings.

        This queries the microscope settings and extracts the home position
        to use as the initial tracked position. If settings are unavailable,
        defaults to (0, 0, 0, 0).
        """
        try:
            # Get settings from connection service if available
            if self.connection.is_connected():
                # Try to get settings - this may fail if not yet initialized
                try:
                    from py2flamingo.utils.file_handlers import text_to_dict
                    from pathlib import Path

                    settings_path = Path('microscope_settings') / 'ScopeSettings.txt'
                    if settings_path.exists():
                        settings = text_to_dict(str(settings_path))

                        # Extract home position from Stage limits section
                        if 'Stage limits' in settings:
                            stage_limits = settings['Stage limits']
                            x = float(stage_limits.get('Home x-axis', 0))
                            y = float(stage_limits.get('Home y-axis', 0))
                            z = float(stage_limits.get('Home z-axis', 0))
                            r = float(stage_limits.get('Home r-axis', 0))

                            self._current_position = Position(x=x, y=y, z=z, r=r)
                            self.logger.info(f"Initialized position from home: X={x:.3f}, Y={y:.3f}, Z={z:.3f}, R={r:.1f}°")
                            return
                except Exception as e:
                    self.logger.debug(f"Could not load home position from settings: {e}")

            # Fallback to origin if settings unavailable
            self._current_position = Position(x=0.0, y=0.0, z=0.0, r=0.0)
            self.logger.warning("Position initialized to origin (0, 0, 0, 0) - settings unavailable")

        except Exception as e:
            self.logger.error(f"Error initializing position: {e}")
            self._current_position = Position(x=0.0, y=0.0, z=0.0, r=0.0)

    def go_to_position(self, position: Position,
                      validate: bool = True,
                      callback: Optional[Callable[[str], None]] = None) -> None:
        """
        Move microscope to specified position.

        This method uses a lock to prevent concurrent movement commands.
        After successful movement, it updates the tracked position.

        Args:
            position: Target position
            validate: Whether to validate position before movement
            callback: Optional callback for progress updates

        Raises:
            ValueError: If position is invalid or wrong type
            RuntimeError: If not connected, movement in progress, or movement fails
        """
        # Validate position parameter type
        if not isinstance(position, Position):
            error_msg = f"position must be Position instance, got {type(position)}"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        # Try to acquire movement lock (non-blocking)
        if not self._movement_lock.acquire(blocking=False):
            error_msg = "Movement already in progress - cannot send concurrent position commands"
            self.logger.warning(error_msg)
            raise RuntimeError(error_msg)

        # Save original position for rollback
        original_position = self._current_position
        movement_started = False

        try:
            # Check connection
            if not self.connection.is_connected():
                error_msg = "Not connected to microscope"
                self.logger.error(error_msg)
                raise RuntimeError(error_msg)

            # Validate position if requested
            if validate:
                try:
                    self._validate_position(position)
                except ValueError as e:
                    self.logger.error(f"Position validation failed: {e}")
                    raise

            self.logger.info(
                f"Moving to position: X={position.x:.3f}, Y={position.y:.3f}, "
                f"Z={position.z:.3f}, R={position.r:.1f}°"
            )

            movement_started = True

            # Send movement commands for each axis
            # Track which axes succeeded for rollback
            moved_axes = []
            try:
                self._move_axis(self.axis.X, position.x, "X-axis")
                moved_axes.append('X')

                self._move_axis(self.axis.Z, position.z, "Z-axis")
                moved_axes.append('Z')

                self._move_axis(self.axis.R, position.r, "Rotation")
                moved_axes.append('R')

                self._move_axis(self.axis.Y, position.y, "Y-axis")  # Y-axis last as in original
                moved_axes.append('Y')

            except Exception as e:
                self.logger.error(
                    f"Movement failed on or after {moved_axes[-1] if moved_axes else 'start'}: {e}"
                )
                self.logger.warning(
                    f"Position tracking may be inconsistent. Successfully moved axes: {moved_axes}"
                )
                # Don't update position - leave at original or partially moved state
                raise RuntimeError(f"Movement failed: {e}") from e

            # Wait for movement to complete
            # TODO: Replace with actual position confirmation from hardware
            import time
            time.sleep(0.5)

            # Only update position if all movements succeeded
            self._current_position = position
            self.logger.info(
                f"Movement complete. Position updated to: X={position.x:.3f}, "
                f"Y={position.y:.3f}, Z={position.z:.3f}, R={position.r:.1f}°"
            )

            # Call callback if provided (catch errors to prevent lock issues)
            if callback:
                try:
                    callback("Movement complete")
                except Exception as e:
                    self.logger.error(f"Callback error (movement still succeeded): {e}")

        except Exception as e:
            # Log the error with context
            if movement_started:
                self.logger.error(
                    f"Movement error - position may be inconsistent: {e}",
                    exc_info=True
                )
            else:
                self.logger.error(f"Movement failed before starting: {e}")
            raise

        finally:
            # Always release the lock
            self._movement_lock.release()
    
    def go_to_xyzr(self, xyzr: List[float], **kwargs) -> None:
        """
        Move to position specified as list (backward compatibility).
        
        This method provides backward compatibility with the original
        go_to_XYZR function from microscope_connect.py.
        
        Args:
            xyzr: List of [x, y, z, r] coordinates
            **kwargs: Additional arguments passed to go_to_position
        """
        # Original comment from microscope_connect.py:
        # Unpack the provided XYZR coordinates, r is in degrees, other values are in mm
        x, y, z, r = xyzr
        
        position = Position(x=float(x), y=float(y), z=float(z), r=float(r))
        self.go_to_position(position, **kwargs)
    
    def _move_axis(self, axis_code: int, value: float, axis_name: str) -> None:
        """
        Move a specific axis to the specified value.

        This is the refactored version of move_axis from microscope_connect.py.

        Args:
            axis_code: The code of the axis to move (1-4)
            value: The value to move the axis to (mm or degrees)
            axis_name: Human-readable axis name for logging

        Raises:
            ValueError: If axis_code or value is invalid
            RuntimeError: If command fails or response invalid
            ConnectionError: If communication fails
        """
        # Validate inputs
        if not isinstance(axis_code, int) or axis_code not in [1, 2, 3, 4]:
            error_msg = f"Invalid axis_code {axis_code}, must be 1-4"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        try:
            value_float = float(value)
        except (ValueError, TypeError) as e:
            error_msg = f"Invalid value for {axis_name}: {value} - must be numeric"
            self.logger.error(error_msg)
            raise ValueError(error_msg) from e

        self.logger.debug(f"Moving {axis_name} (axis {axis_code}) to {value_float}")

        try:
            # The old system sent [axis_code, 0, 0, value] as command_data
            # In the protocol: params[0]=axis_code, value=position_value
            from py2flamingo.models.command import Command

            cmd = Command(
                code=self.COMMAND_CODES_STAGE_POSITION_SET,
                parameters={
                    'params': [axis_code, 0, 0, 0, 0, 0, 0],  # 7 params, first is axis code
                    'value': value_float  # Position value
                }
            )

            response_bytes = self.connection.send_command(cmd)

            # Validate response
            if response_bytes is None:
                error_msg = f"No response received for {axis_name} movement"
                self.logger.error(error_msg)
                raise RuntimeError(error_msg)

            if len(response_bytes) == 0:
                error_msg = f"Empty response received for {axis_name} movement"
                self.logger.error(error_msg)
                raise RuntimeError(error_msg)

            # TODO: Parse response_bytes for error codes
            # For now, just verify we got a response
            self.logger.debug(
                f"{axis_name} move command completed - received {len(response_bytes)} byte response"
            )

        except ValueError as e:
            self.logger.error(f"Invalid command parameters for {axis_name}: {e}")
            raise

        except socket.error as e:
            error_msg = f"Communication error moving {axis_name}: {e}"
            self.logger.error(error_msg)
            raise RuntimeError(error_msg) from e

        except Exception as e:
            error_msg = f"Failed to move {axis_name}: {e}"
            self.logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

    
    def _validate_position(self, position: Position) -> None:
        """
        Validate that position is within stage limits.
        
        Args:
            position: Position to validate
            
        Raises:
            ValueError: If position is outside limits
        """
        # Get stage limits from configuration
        from py2flamingo.services.configuration_service import ConfigurationService
        config_service = ConfigurationService()
        limits = config_service.get_stage_limits()
        
        # Check each axis
        axes = [
            ('x', position.x, limits['x']),
            ('y', position.y, limits['y']),
            ('z', position.z, limits['z']),
            ('r', position.r, limits['r'])
        ]
        
        for axis_name, value, axis_limits in axes:
            if not (axis_limits['min'] <= value <= axis_limits['max']):
                raise ValueError(
                    f"{axis_name.upper()}-axis position {value} is outside limits "
                    f"[{axis_limits['min']}, {axis_limits['max']}]"
                )
    
    def get_current_position(self) -> Optional[Position]:
        """
        Get current tracked position.

        Note: The microscope hardware does not report current position.
        This method returns the locally tracked position which is updated
        after each successful movement command. Position is initialized from
        the home position in microscope settings.

        Returns:
            Optional[Position]: Current tracked position, or None if not initialized
        """
        if self._current_position is None:
            self.logger.warning("Position not yet initialized")
            # Try to initialize now
            self._initialize_position()

        if self._current_position:
            self.logger.debug(
                f"Current position: X={self._current_position.x:.3f}, "
                f"Y={self._current_position.y:.3f}, "
                f"Z={self._current_position.z:.3f}, "
                f"R={self._current_position.r:.1f}°"
            )

        return self._current_position

    def debug_query_position_response(self) -> dict:
        """
        Query STAGE_POSITION_GET and return parsed response for debugging.

        This method sends the STAGE_POSITION_GET command and parses the
        full response to show what data the microscope actually returns.
        Useful for demonstrating to maintainers what data is available.

        Returns:
            Dictionary containing parsed response:
                - 'success': bool - Whether query succeeded
                - 'raw_response': bytes - Raw response data
                - 'parsed': dict - Parsed response structure
                - 'error': str - Error message if failed

        Note:
            This is a diagnostic/debug method. The microscope does NOT
            return current position via this command - it returns settings.
        """
        import struct

        if not self.connection.is_connected():
            return {
                'success': False,
                'error': 'Not connected to microscope'
            }

        try:
            from py2flamingo.models.command import Command

            self.logger.info("Sending STAGE_POSITION_GET for debug query...")

            cmd = Command(
                code=self.COMMAND_CODES_STAGE_POSITION_GET,
                parameters={'params': [0, 0, 0, 0, 0, 0, 0], 'value': 0.0}
            )

            response_bytes = self.connection.send_command(cmd)

            # Parse response structure
            if len(response_bytes) < 128:
                return {
                    'success': False,
                    'error': f'Response too short: {len(response_bytes)} bytes',
                    'raw_response': response_bytes
                }

            # Protocol structure: START(4) + CODE(4) + STATUS(4) + PARAMS(28) + VALUE(8) + DATA(80)
            try:
                start_marker = struct.unpack('<I', response_bytes[0:4])[0]
                command_code = struct.unpack('<I', response_bytes[4:8])[0]
                status_code = struct.unpack('<I', response_bytes[8:12])[0]

                # Unpack 7 parameters
                params = []
                for i in range(7):
                    offset = 12 + (i * 4)
                    param = struct.unpack('<i', response_bytes[offset:offset+4])[0]
                    params.append(param)

                # Unpack value (double)
                value = struct.unpack('<d', response_bytes[40:48])[0]

                # Get data section (80 bytes - this is only the TAIL of the full data!)
                data_tail = response_bytes[48:128]

                # Try to decode the tail as string
                try:
                    data_tail_str = data_tail.rstrip(b'\x00').decode('utf-8', errors='replace')
                except:
                    data_tail_str = '<binary data>'

                # Check if microscope wrote data to a file (like SCOPE_SETTINGS does)
                full_data_str = None
                import time
                from pathlib import Path
                from py2flamingo.utils.file_handlers import text_to_dict

                # Wait a moment for file to be written
                time.sleep(0.3)

                # Check common locations for position/settings files
                possible_files = [
                    Path('microscope_settings') / 'StagePosition.txt',
                    Path('microscope_settings') / 'ScopeSettings.txt',
                    Path('microscope_settings') / 'PositionSettings.txt',
                ]

                for file_path in possible_files:
                    if file_path.exists():
                        try:
                            # Try to read as text
                            with open(file_path, 'r') as f:
                                full_data_str = f.read()
                            self.logger.info(f"Found data file: {file_path} ({len(full_data_str)} chars)")
                            break
                        except Exception as e:
                            self.logger.debug(f"Could not read {file_path}: {e}")

                if full_data_str is None:
                    # No file found, use what we have
                    full_data_str = f"<No file found - only header data available>\n\nLast 80 bytes from response:\n{data_tail_str}"
                    self.logger.warning("STAGE_POSITION_GET did not write to expected file locations")

                parsed = {
                    'start_marker': f'0x{start_marker:08X}',
                    'command_code': command_code,
                    'status_code': status_code,
                    'params': params,
                    'value': value,
                    'data_tail_string': data_tail_str,
                    'data_tail_hex': data_tail[:40].hex() if len(data_tail) > 0 else '',
                    'full_data': full_data_str,
                    'data_length': len(full_data_str) if full_data_str else 0
                }

                self.logger.info(f"STAGE_POSITION_GET response parsed successfully")
                self.logger.debug(f"Response: {parsed}")

                return {
                    'success': True,
                    'raw_response': response_bytes,
                    'parsed': parsed,
                    'interpretation': self._interpret_position_response(parsed)
                }

            except Exception as e:
                return {
                    'success': False,
                    'error': f'Failed to parse response: {e}',
                    'raw_response': response_bytes
                }

        except Exception as e:
            self.logger.error(f"Failed to query position: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    def _interpret_position_response(self, parsed: dict) -> str:
        """
        Interpret what the position query response contains.

        Args:
            parsed: Parsed response dictionary

        Returns:
            Human-readable interpretation string
        """
        interpretation_lines = []

        interpretation_lines.append("RESPONSE ANALYSIS:")
        interpretation_lines.append(f"  Command Code: {parsed['command_code']} (expected: {self.COMMAND_CODES_STAGE_POSITION_GET})")
        interpretation_lines.append(f"  Status: {parsed['status_code']} (0 = success)")
        interpretation_lines.append(f"  Value field: {parsed['value']}")
        interpretation_lines.append(f"  Params: {parsed['params']}")

        # Check if we have full data from file
        full_data = parsed.get('full_data', '')
        data_tail = parsed.get('data_tail_string', '')

        if full_data and not full_data.startswith('<No file found'):
            interpretation_lines.append(f"\n  ✓ Full data retrieved from file ({parsed.get('data_length', 0)} chars)")
            interpretation_lines.append(f"  Data preview: {repr(full_data[:100])}")
            interpretation_lines.append("\n  ⚠ NOTE: This appears to be settings/configuration data,")
            interpretation_lines.append("  ⚠       NOT current position coordinates!")
        elif data_tail and len(data_tail.strip()) > 0:
            interpretation_lines.append(f"\n  ⚠ Only partial data available (80-byte tail from protocol)")
            interpretation_lines.append(f"  Data tail: {repr(data_tail[:100])}")
        else:
            interpretation_lines.append("\n  Data section is empty or binary")

        interpretation_lines.append("\n  CONCLUSION:")
        interpretation_lines.append("  This command does NOT return current stage position.")
        interpretation_lines.append("  The microscope does not report actual position via this command.")
        interpretation_lines.append("\n  Without position feedback:")
        interpretation_lines.append("  - Software must track position locally (can drift)")
        interpretation_lines.append("  - Cannot detect manual stage movement")
        interpretation_lines.append("  - Cannot verify movements completed successfully")

        return '\n'.join(interpretation_lines)
