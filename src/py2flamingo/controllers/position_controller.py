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

        This method sends any command code and shows what data the microscope
        returns. Useful for testing which commands are implemented and what
        data they provide.

        Args:
            command_code: The command code to send
            command_name: Human-readable name for logging/display

        Returns:
            Dictionary containing parsed response:
                - 'success': bool - Whether query succeeded
                - 'command_code': int - Command code sent
                - 'command_name': str - Command name
                - 'raw_response': bytes - Raw response data
                - 'parsed': dict - Parsed response structure
                - 'error': str - Error message if failed
                - 'timeout_explanation': str - Explanation if timeout

        Note:
            This is a diagnostic/debug method for testing command responses.
        """
        import struct
        import socket

        if not self.connection.is_connected():
            return {
                'success': False,
                'command_code': command_code,
                'command_name': command_name,
                'error': 'Not connected to microscope'
            }

        try:
            from py2flamingo.models.command import Command

            self.logger.info(f"Sending {command_name} (code {command_code}) for debug query...")

            # For debug query, we need to read ALL data from socket,
            # not just the standard 128 bytes
            cmd = Command(
                code=command_code,
                parameters={'params': [0, 0, 0, 0, 0, 0, 0], 'value': 0.0}
            )

            # Encode command
            cmd_bytes = self.connection.encoder.encode_command(
                code=cmd.code,
                status=0,
                params=cmd.parameters.get('params'),
                value=cmd.parameters.get('value', 0.0),
                data=b''
            )

            # Send command directly via socket
            command_socket = self.connection._command_socket
            if command_socket is None:
                return {
                    'success': False,
                    'error': 'Command socket not available'
                }

            command_socket.sendall(cmd_bytes)
            self.logger.info("Command sent, reading response...")

            # Pattern from old code: microscope sends data in two parts:
            # 1. 128-byte binary acknowledgment
            # 2. Additional text data (if any)

            # Read 128-byte acknowledgment first
            ack_response = self._receive_full_bytes(command_socket, 128, timeout=2.0)
            self.logger.info(f"Received 128-byte acknowledgment")

            # Check for additional data (like old code's bytes_waiting())
            import time
            import select

            time.sleep(0.1)  # Brief wait for additional data to arrive

            # Check if more data is waiting
            ready = select.select([command_socket], [], [], 0.1)
            additional_data = b''

            if ready[0]:
                # More data is available - read it all
                command_socket.settimeout(0.5)
                try:
                    while True:
                        chunk = command_socket.recv(4096)
                        if not chunk:
                            break
                        additional_data += chunk
                        self.logger.info(f"Received additional data chunk: {len(chunk)} bytes")
                        # Check if more data is waiting
                        ready = select.select([command_socket], [], [], 0.05)
                        if not ready[0]:
                            break
                except socket.timeout:
                    pass
                finally:
                    command_socket.settimeout(None)

                self.logger.info(f"Total additional data: {len(additional_data)} bytes")

            # Combine acknowledgment and additional data
            response_bytes = ack_response + additional_data
            self.logger.info(f"Total response: {len(response_bytes)} bytes (128 ack + {len(additional_data)} additional)")

            # Parse response based on structure
            # The 128-byte ack might be binary protocol, and additional_data might be text
            if len(ack_response) < 4:
                return {
                    'success': False,
                    'error': f'Acknowledgment too short: {len(ack_response)} bytes',
                    'raw_response': response_bytes
                }

            # Protocol structure: START(4) + CODE(4) + STATUS(4) + PARAMS(28) + VALUE(8) + DATA(80)
            # Expected start marker for binary protocol: 0xF321E654
            try:
                import time
                from pathlib import Path

                # Check if the 128-byte ack is binary protocol
                start_marker = struct.unpack('<I', ack_response[0:4])[0]
                is_binary_protocol = (start_marker == 0xF321E654)

                self.logger.info(f"Ack type: {'Binary protocol' if is_binary_protocol else 'Text data'} (marker: 0x{start_marker:08X})")
                self.logger.info(f"Additional data: {len(additional_data)} bytes")

                # Parse acknowledgment
                if is_binary_protocol:
                    # Binary protocol acknowledgment
                    command_code = struct.unpack('<I', ack_response[4:8])[0]
                    status_code = struct.unpack('<I', ack_response[8:12])[0]

                    # Unpack 7 parameters
                    params = []
                    for i in range(7):
                        offset = 12 + (i * 4)
                        param = struct.unpack('<i', ack_response[offset:offset+4])[0]
                        params.append(param)

                    # Unpack value (double)
                    value = struct.unpack('<d', ack_response[40:48])[0]

                    # Get data section (80 bytes from ack)
                    data_tail = ack_response[48:128]

                    # Try to decode the tail as string
                    try:
                        data_tail_str = data_tail.rstrip(b'\x00').decode('utf-8', errors='replace')
                    except:
                        data_tail_str = '<binary data>'

                    response_type = "Binary Protocol"
                else:
                    # Text acknowledgment
                    try:
                        ack_text = ack_response.decode('utf-8', errors='replace')
                    except:
                        ack_text = '<Could not decode as text>'

                    command_code = 0
                    status_code = 0
                    params = []
                    value = 0.0
                    data_tail_str = ack_text[-100:]
                    response_type = "Text Data"

                # Handle the full response data
                if len(additional_data) > 0:
                    # We have both ack (128 bytes) and additional data
                    # For text responses, both parts are text, so decode the COMPLETE response
                    try:
                        full_data_str = response_bytes.decode('utf-8', errors='replace')
                        # Strip any trailing binary garbage (protocol end markers, etc.)
                        full_data_str = full_data_str.rstrip('\x00\r\n')
                        # Find last '>' which should be the end of the XML-like structure
                        last_bracket = full_data_str.rfind('>')
                        if last_bracket != -1 and last_bracket > len(full_data_str) - 50:
                            # There's a '>' near the end, truncate any garbage after it
                            full_data_str = full_data_str[:last_bracket + 1]
                        self.logger.info(f"Decoded complete response: {len(full_data_str)} chars")
                    except:
                        full_data_str = f"<Could not decode {len(response_bytes)} bytes as text>"
                elif not is_binary_protocol:
                    # No additional data, ack itself was text (only 128 bytes total)
                    try:
                        full_data_str = ack_response.decode('utf-8', errors='replace').rstrip('\x00\r\n')
                    except:
                        full_data_str = ack_text
                else:
                    # Binary protocol with no additional data
                    full_data_str = f"<Binary protocol, no additional data>\n\n{data_tail_str}"

                parsed = {
                    'response_type': response_type,
                    'start_marker': f'0x{start_marker:08X}',
                    'command_code': command_code,
                    'status_code': status_code,
                    'params': params,
                    'value': value,
                    'data_tail_string': data_tail_str,
                    'full_data': full_data_str,
                    'data_length': len(full_data_str) if full_data_str else 0
                }

                self.logger.info(f"{command_name} response parsed successfully")
                self.logger.debug(f"Response: {parsed}")

                return {
                    'success': True,
                    'command_code': command_code,
                    'command_name': command_name,
                    'raw_response': response_bytes,
                    'parsed': parsed,
                    'interpretation': self._interpret_command_response(parsed, command_code, command_name)
                }

            except Exception as e:
                return {
                    'success': False,
                    'command_code': command_code,
                    'command_name': command_name,
                    'error': f'Failed to parse response: {e}',
                    'raw_response': response_bytes
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
                'error': str(e)
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
