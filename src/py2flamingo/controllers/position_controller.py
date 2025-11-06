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
from py2flamingo.core.tcp_protocol import CommandDataBits

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

    def move_rotation(self, rotation_degrees: float) -> None:
        """
        Move only the rotation axis to the specified angle.

        This is the safest movement as rotation doesn't risk hitting
        the chamber walls. The stage must be within the chamber bounds
        in X, Y, Z before rotating.

        Args:
            rotation_degrees: Target rotation angle in degrees (0-360)

        Raises:
            ValueError: If rotation is out of bounds
            RuntimeError: If not connected or movement fails
        """
        # Validate rotation bounds
        if not 0 <= rotation_degrees <= 360:
            error_msg = f"Rotation {rotation_degrees}° is outside valid range [0, 360]"
            self.logger.error(error_msg)
            raise ValueError(error_msg)

        # Try to acquire movement lock (non-blocking)
        if not self._movement_lock.acquire(blocking=False):
            error_msg = "Movement already in progress"
            self.logger.warning(error_msg)
            raise RuntimeError(error_msg)

        try:
            # Check connection
            if not self.connection.is_connected():
                error_msg = "Not connected to microscope"
                self.logger.error(error_msg)
                raise RuntimeError(error_msg)

            self.logger.info(f"Moving rotation to {rotation_degrees:.2f}°")

            # Move only the rotation axis
            self._move_axis(self.axis.R, rotation_degrees, "Rotation")

            # Update tracked position
            if self._current_position:
                self._current_position = Position(
                    x=self._current_position.x,
                    y=self._current_position.y,
                    z=self._current_position.z,
                    r=rotation_degrees
                )
                self.logger.info(f"Position updated: Rotation = {rotation_degrees:.2f}°")

        finally:
            # Always release the lock
            self._movement_lock.release()

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

    def _receive_full_bytes(self, sock: socket.socket, expected_size: int, timeout: float = 5.0) -> bytes:
        """
        Receive exact number of bytes from socket.

        Args:
            sock: Socket to read from
            expected_size: Exact number of bytes to read
            timeout: Timeout in seconds

        Returns:
            Bytes read from socket

        Raises:
            socket.timeout: If timeout expires
            socket.error: If socket error occurs
        """
        import time
        data = b''
        start_time = time.time()
        original_timeout = sock.gettimeout()

        try:
            sock.settimeout(timeout)

            while len(data) < expected_size:
                if time.time() - start_time > timeout:
                    raise socket.timeout(f"Timeout reading {expected_size} bytes (got {len(data)})")

                remaining = expected_size - len(data)
                chunk = sock.recv(remaining)
                if not chunk:
                    raise socket.error(f"Connection closed after {len(data)}/{expected_size} bytes")
                data += chunk

        finally:
            sock.settimeout(original_timeout)

        return data

    def debug_query_command(self, command_code: int, command_name: str) -> dict:
        """
        Send a command and return parsed response for debugging.

        This method sends commands using the proper queue-based communication
        system (like the old code), avoiding race conditions with the listener thread.

        Args:
            command_code: The command code to send
            command_name: Human-readable name for logging/display

        Returns:
            Dictionary containing parsed response:
                - 'success': bool - Whether query succeeded
                - 'command_code': int - Command code sent
                - 'command_name': str - Command name
                - 'raw_response': bytes/Any - Raw response data from queue
                - 'parsed': dict - Parsed response structure
                - 'error': str - Error message if failed
                - 'timeout_explanation': str - Explanation if timeout

        Note:
            This method uses the queue-based communication system. The command is
            sent via the command queue, and the response is received via the
            other_data queue (populated by the listener thread).
        """
        import struct
        import time
        from queue import Empty

        if not self.connection.is_connected():
            return {
                'success': False,
                'command_code': command_code,
                'command_name': command_name,
                'error': 'Not connected to microscope'
            }

        try:
            self.logger.info(f"Sending {command_name} (code {command_code}) directly via socket...")

            # MVCConnectionService doesn't use background threads, so direct socket access is safe
            # Encode command with TRIGGER_CALL_BACK flag to get response
            # params[6] (cmdBits6) must be set to 0x80000000 to trigger microscope response
            cmd_bytes = self.connection.encoder.encode_command(
                code=command_code,
                status=0,
                params=[0, 0, 0, 0, 0, 0, CommandDataBits.TRIGGER_CALL_BACK],
                value=0.0,
                data=b''
            )

            # Get command socket from connection service
            command_socket = self.connection._command_socket
            if command_socket is None:
                return {
                    'success': False,
                    'command_code': command_code,
                    'command_name': command_name,
                    'error': 'Command socket not available - not connected?'
                }

            # Send command
            command_socket.sendall(cmd_bytes)
            self.logger.info("Command sent, waiting for response...")

            # Read 128-byte acknowledgment
            ack_response = self._receive_full_bytes(command_socket, 128, timeout=3.0)
            self.logger.info(f"Received 128-byte acknowledgment")

            # Parse the 128-byte response
            if len(ack_response) < 4:
                return {
                    'success': False,
                    'command_code': command_code,
                    'command_name': command_name,
                    'error': f'Response too short: {len(ack_response)} bytes'
                }

            # Unpack binary protocol structure
            start_marker = struct.unpack('<I', ack_response[0:4])[0]
            response_code = struct.unpack('<I', ack_response[4:8])[0]
            status_code = struct.unpack('<I', ack_response[8:12])[0]

            # Unpack 7 parameters
            params = []
            for i in range(7):
                offset = 12 + (i * 4)
                param = struct.unpack('<i', ack_response[offset:offset+4])[0]
                params.append(param)

            # Unpack value (double)
            value = struct.unpack('<d', ack_response[40:48])[0]

            # Get addDataBytes field
            add_data_bytes = struct.unpack('<I', ack_response[48:52])[0]

            # Read additional data if specified (CRITICAL for buffer management)
            additional_data = b''
            if add_data_bytes > 0:
                self.logger.info(f"Reading {add_data_bytes} additional bytes from socket...")
                try:
                    additional_data = self._receive_full_bytes(command_socket, add_data_bytes, timeout=3.0)
                    self.logger.info(f"Successfully read {len(additional_data)} additional bytes")
                except (socket.timeout, TimeoutError) as e:
                    self.logger.warning(f"Timeout reading additional data: {e}")
                except Exception as e:
                    self.logger.error(f"Error reading additional data: {e}")

            # Get data section (72 bytes)
            data_field = ack_response[52:124]

            # Try to decode data field as string
            try:
                data_tail_str = data_field.rstrip(b'\x00').decode('utf-8', errors='replace')
            except:
                data_tail_str = '<binary data>'

            # Try to decode additional data as string
            additional_data_str = ''
            if additional_data:
                try:
                    additional_data_str = additional_data.rstrip(b'\x00').decode('utf-8', errors='replace')
                except:
                    additional_data_str = '<binary data>'

            self.logger.info(f"Parsed response: code={response_code}, status={status_code}, value={value}, addDataBytes={add_data_bytes}")

            # Create parsed structure
            parsed = {
                'response_type': 'Binary Protocol',
                'start_marker': f'0x{start_marker:08X}',
                'command_code': response_code,
                'status_code': status_code,
                'params': params,
                'value': value,
                'reserved': add_data_bytes,
                'data_tail_string': data_tail_str,
                'additional_data': additional_data,  # Raw bytes
                'additional_data_string': additional_data_str,  # Decoded string
                'full_data': f"Binary protocol response",
                'data_length': 128 + len(additional_data)
            }

            return {
                'success': True,
                'command_code': command_code,
                'command_name': command_name,
                'raw_response': ack_response,
                'parsed': parsed,
                'interpretation': self._interpret_command_response(parsed, command_code, command_name)
            }

        except (socket.timeout, TimeoutError) as e:
            self.logger.error(f"Timeout waiting for response from {command_name}")
            return {
                'success': False,
                'command_code': command_code,
                'command_name': command_name,
                'error': 'timeout',
                'timeout_explanation': (
                    f"No response from microscope after sending {command_name} (code {command_code}).\n\n"
                    "This likely means:\n"
                    "1. Command is NOT IMPLEMENTED in microscope firmware\n"
                    "2. Command is defined in CommandCodes.h but never used\n"
                    "3. Microscope ignores unknown/unimplemented commands\n\n"
                    "Try other commands to see which ones are actually implemented."
                )
            }
        except Exception as e:
            self.logger.error(f"Failed to query {command_name}: {e}", exc_info=True)
            return {
                'success': False,
                'command_code': command_code,
                'command_name': command_name,
                'error': f'Communication error: {str(e)}'
            }

    def _interpret_command_response(self, parsed: dict, command_code: int, command_name: str) -> str:
        """
        Interpret what a command response contains.

        Args:
            parsed: Parsed response dictionary
            command_code: Command code that was sent
            command_name: Human-readable command name

        Returns:
            Human-readable interpretation string
        """
        interpretation_lines = []

        interpretation_lines.append("RESPONSE ANALYSIS:")
        interpretation_lines.append(f"  Command sent: {command_name} (code {command_code})")

        # Check if we have full data
        full_data = parsed.get('full_data', '')
        data_length = parsed.get('data_length', 0)

        if full_data and data_length > 0 and not full_data.startswith('<'):
            interpretation_lines.append(f"\n  ✓ Received text response: {data_length} characters")
            interpretation_lines.append(f"  Data preview: {repr(full_data[:100])}")

            # Check if this looks like settings data
            if '<Type>' in full_data or 'Filter wheel' in full_data or 'Stage limits' in full_data:
                interpretation_lines.append("\n  ⚠ UNEXPECTED BEHAVIOR DETECTED:")
                interpretation_lines.append("  ⚠ Command STAGE_POSITION_GET returned SETTINGS data!")
                interpretation_lines.append(f"  ⚠ This is the same data returned by SCOPE_SETTINGS_LOAD (code 4105)")
                interpretation_lines.append("\n  This response contains:")
                if 'Filter wheel' in full_data:
                    interpretation_lines.append("    - Filter wheel configuration")
                if 'Stage limits' in full_data:
                    interpretation_lines.append("    - Stage limits and home position")
                if '<Type>' in full_data:
                    interpretation_lines.append("    - Microscope type and name")
                if 'LED settings' in full_data:
                    interpretation_lines.append("    - LED settings")
                interpretation_lines.append("\n  But it does NOT contain:")
                interpretation_lines.append("    - Current X, Y, Z, R position coordinates")
                interpretation_lines.append("    - Any real-time position feedback")
        else:
            interpretation_lines.append(f"\n  Response type: {parsed.get('response_type', 'Unknown')}")
            interpretation_lines.append(f"  Data available: {data_length} characters")

        interpretation_lines.append("\n  CONCLUSION:")

        # Provide specific conclusions based on command type
        if command_code == 40967:  # SYSTEM_STATE_GET
            status_code = parsed.get('status_code', 0)
            params = parsed.get('params', [])

            interpretation_lines.append(f"  ✓ SYSTEM_STATE_GET is IMPLEMENTED and working!")
            interpretation_lines.append(f"\n  System State Interpretation:")
            interpretation_lines.append(f"    Status Code: {status_code}")

            if status_code == 1:
                interpretation_lines.append(f"    → System is IDLE (ready for commands)")
            elif status_code == 0:
                interpretation_lines.append(f"    → System is BUSY (executing command)")
            else:
                interpretation_lines.append(f"    → Unknown status: {status_code}")

            if len(params) > 3 and params[3] != 0:
                state_code = params[3]
                interpretation_lines.append(f"\n    State Code: {state_code}")
                if state_code == 40962:
                    interpretation_lines.append(f"    → SYSTEM_STATE_IDLE (40962)")
                else:
                    interpretation_lines.append(f"    → Unknown state code")

            interpretation_lines.append("\n  This command successfully queries system state!")
            interpretation_lines.append("  Use this to check if microscope is ready for commands.")

        elif command_code == 12327:  # CAMERA_IMAGE_SIZE_GET
            params = parsed.get('params', [])
            interpretation_lines.append(f"  ✓ CAMERA_IMAGE_SIZE_GET query")
            interpretation_lines.append(f"\n  Camera Image Size:")
            if len(params) > 0 and params[0] != 0:
                # Old code used received[7] which would be params[4]
                # But let's check all params for non-zero values
                interpretation_lines.append(f"    Parameters: {params}")
                interpretation_lines.append(f"    → Image size info returned in parameters")
            else:
                interpretation_lines.append(f"    Parameters: {params}")
                interpretation_lines.append(f"    Note: Check which parameter field contains image size")

        elif command_code == 12343:  # CAMERA_PIXEL_FIELD_OF_VIEW_GET
            value = parsed.get('value', 0.0)
            interpretation_lines.append(f"  ✓ CAMERA_PIXEL_FIELD_OF_VIEW_GET query")
            interpretation_lines.append(f"\n  Pixel Field of View:")
            interpretation_lines.append(f"    Value: {value}")
            if value > 0:
                interpretation_lines.append(f"    → Pixel FOV = {value} (likely in micrometers)")
            else:
                interpretation_lines.append(f"    Note: Value is zero - check if command is implemented")

        elif command_code == 12293:  # CAMERA_WORK_FLOW_STOP
            status_code = parsed.get('status_code', 0)
            interpretation_lines.append(f"  ✓ CAMERA_WORK_FLOW_STOP command")
            interpretation_lines.append(f"\n  Workflow Stop:")
            interpretation_lines.append(f"    Status: {status_code}")
            interpretation_lines.append(f"    → Command sent to stop any running workflow")
            interpretation_lines.append(f"    → Safe to test (stops acquisition if running)")

        elif command_code == 24592:  # STAGE_MOTION_STOPPED
            interpretation_lines.append(f"  Testing STAGE_MOTION_STOPPED command...")
            interpretation_lines.append("  This should indicate if stage has finished moving.")

        elif command_code == 4103:  # COMMON_SCOPE_SETTINGS
            interpretation_lines.append(f"  Testing COMMON_SCOPE_SETTINGS command...")
            interpretation_lines.append("  Different from _LOAD, might query without writing file.")

        elif command_code == self.COMMAND_CODES_STAGE_POSITION_GET:
            interpretation_lines.append("  STAGE_POSITION_GET does NOT return current stage position.")
            interpretation_lines.append("  Instead, it returns microscope configuration settings.")
            interpretation_lines.append("\n  Without position feedback:")
            interpretation_lines.append("  - Software must track position locally (can drift)")
            interpretation_lines.append("  - Cannot detect manual stage movement")
            interpretation_lines.append("  - Cannot verify movements completed successfully")
        elif full_data and data_length > 0:
            interpretation_lines.append(f"  {command_name} returned {data_length} characters of data.")
            if '<Type>' in full_data or 'Filter wheel' in full_data:
                interpretation_lines.append("  Response contains microscope configuration/settings data.")
            else:
                interpretation_lines.append("  Response type unclear - review full data above.")
        else:
            interpretation_lines.append(f"  {command_name} returned limited or no data.")
            interpretation_lines.append("  Check if command is implemented and what it should return.")

        return '\n'.join(interpretation_lines)

    def debug_save_settings(self, settings_data: bytes) -> dict:
        """
        Test SCOPE_SETTINGS_SAVE command by sending settings file to microscope.

        This replicates the old code's handle_scope_settings_save() function
        which sends a settings file to the microscope using text_to_nuc pattern.

        Args:
            settings_data: Settings file content as bytes

        Returns:
            Dictionary with success status and message
        """
        if not self.connection.is_connected():
            return {
                'success': False,
                'error': 'Not connected to microscope'
            }

        try:
            from py2flamingo.models.command import Command

            COMMAND_CODES_COMMON_SCOPE_SETTINGS_SAVE = 4104

            self.logger.info(f"Sending SCOPE_SETTINGS_SAVE with {len(settings_data)} bytes of data")

            # Create command with file size in addDataBytes field
            cmd_bytes = self.connection.encoder.encode_command(
                code=COMMAND_CODES_COMMON_SCOPE_SETTINGS_SAVE,
                status=0,
                params=[0, 0, 0, 0, 0, 0, 0],
                value=0.0,
                data=b'',  # Don't send data in command structure
                additional_data_size=len(settings_data)  # Tell microscope data is coming
            )

            # Send command followed by file data
            command_socket = self.connection._command_socket
            if command_socket is None:
                return {
                    'success': False,
                    'error': 'Command socket not available'
                }

            command_socket.sendall(cmd_bytes)
            command_socket.sendall(settings_data)

            self.logger.info("Command and data sent, waiting for acknowledgment...")

            # Read 128-byte acknowledgment
            try:
                ack = self._receive_full_bytes(command_socket, 128, timeout=5.0)
                self.logger.info("Received acknowledgment")

                # Parse acknowledgment
                import struct
                start_marker = struct.unpack('<I', ack[0:4])[0]
                response_code = struct.unpack('<I', ack[4:8])[0]
                status_code = struct.unpack('<I', ack[8:12])[0]

                self.logger.info(f"Acknowledgment: marker=0x{start_marker:08X}, "
                               f"code={response_code}, status={status_code}")

                if start_marker == 0xF321E654:
                    return {
                        'success': True,
                        'message': f"Microscope acknowledged settings save.\n"
                                 f"Status code: {status_code}\n"
                                 f"Response code: {response_code}"
                    }
                else:
                    return {
                        'success': False,
                        'error': f'Invalid response marker: 0x{start_marker:08X}'
                    }

            except (socket.timeout, TimeoutError):
                return {
                    'success': False,
                    'error': 'Timeout waiting for acknowledgment (command may not be implemented)'
                }

        except Exception as e:
            self.logger.error(f"Failed to save settings: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }
