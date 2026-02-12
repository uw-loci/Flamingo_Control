"""Position controller debug/diagnostic utilities.

Contains PositionDebugHelper for direct socket communication with
hardware diagnostics, extracted from position_controller.py.
"""

import logging
import socket
import struct
import time
from typing import Dict

from py2flamingo.core.tcp_protocol import CommandDataBits


logger = logging.getLogger(__name__)


class PositionDebugHelper:
    """Helper class for debug/diagnostic commands on position hardware.

    Handles direct socket communication for querying and saving
    hardware settings, used by the connection debug view.
    """

    # Command codes
    COMMAND_CODES_STAGE_POSITION_GET = 24584

    def __init__(self, connection_service):
        """Initialize the debug helper.

        Args:
            connection_service: MVCConnectionService for microscope communication
        """
        self.connection = connection_service
        self.logger = logging.getLogger(__name__)

    def _receive_full_bytes(self, sock: socket.socket, expected_size: int, timeout: float = 5.0) -> bytes:
        """Receive exact number of bytes from socket.

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
        """Send a command and return parsed response for debugging.

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

            # IMPORTANT: For STAGE_POSITION_GET, params[3] (int32Data0) must specify the axis
            # Query single axis (1=X, 2=Y, 3=Z, 4=R)
            params = [0, 0, 0, 0, 0, 0, CommandDataBits.TRIGGER_CALL_BACK]
            if command_code == 24584:  # STAGE_POSITION_GET
                params[3] = 1  # Query X-axis (1=X, 2=Y, 3=Z, 4=R)
                self.logger.info("STAGE_POSITION_GET: Setting params[3] (int32Data0) = 1 for X-axis")

            cmd_bytes = self.connection.encoder.encode_command(
                code=command_code,
                status=0,
                params=params,
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
        """Interpret what a command response contains.

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
        """Test SCOPE_SETTINGS_SAVE command by sending settings file to microscope.

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
