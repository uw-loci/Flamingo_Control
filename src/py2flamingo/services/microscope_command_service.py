"""
Base service class for microscope command operations.

Provides common command sending, receiving, and parsing logic
shared across all subsystem services (Camera, Stage, Laser, etc.).

ENHANCED: This is now the single source of truth for ALL command operations,
providing compatibility methods for legacy code while maintaining a consistent
command sending implementation.

ASYNC SUPPORT: When the connection has an async reader active, commands are
sent and responses are received via the background reader's dispatch queues,
preventing socket buffer buildup and ensuring callbacks are never missed.
"""

import logging
import struct
import socket
from typing import Dict, Any, Optional, List, Union, TYPE_CHECKING

from py2flamingo.core.tcp_protocol import CommandDataBits, get_command_name

if TYPE_CHECKING:
    from py2flamingo.core.socket_reader import ParsedMessage


class MicroscopeCommandService:
    """
    Base class for microscope subsystem services.

    Handles low-level command encoding, socket communication, and response
    parsing. Subsystem services (CameraService, StageService, etc.) inherit
    from this class and implement domain-specific methods.
    """

    def __init__(self, connection):
        """
        Initialize command service.

        Args:
            connection: MVCConnectionService instance providing socket access
        """
        self.connection = connection
        self.logger = logging.getLogger(self.__class__.__name__)

    def _query_command(
        self,
        command_code: int,
        command_name: str,
        params: Optional[List[int]] = None,
        value: float = 0.0
    ) -> Dict[str, Any]:
        """
        Send a query command and return parsed response.

        Automatically adds TRIGGER_CALL_BACK flag and handles socket communication.
        Uses async reader when available for non-blocking operation.

        Args:
            command_code: Command code to send
            command_name: Human-readable command name for logging
            params: Optional list of parameters (params[6] will be set to TRIGGER_CALL_BACK)
            value: Optional double value

        Returns:
            Dict with 'success', 'parsed', 'raw_response', etc.
        """
        if not self.connection.is_connected():
            return {
                'success': False,
                'error': 'Not connected to microscope'
            }

        try:
            # Ensure params[6] has TRIGGER_CALL_BACK flag
            if params is None:
                params = [0] * 7
            elif len(params) < 7:
                params = list(params) + [0] * (7 - len(params))
            else:
                params = list(params)

            # Always set TRIGGER_CALL_BACK flag in params[6]
            params[6] = CommandDataBits.TRIGGER_CALL_BACK

            # Encode command
            cmd_bytes = self.connection.encoder.encode_command(
                code=command_code,
                status=0,
                params=params,
                value=value,
                data=b''
            )

            # Use get_command_name for better logging if command_name is generic
            if command_name == str(command_code) or 'COMMAND' in command_name.upper():
                command_name = get_command_name(command_code)

            # Check if async reader is available
            if hasattr(self.connection, 'has_async_reader') and self.connection.has_async_reader:
                return self._send_via_async_reader(
                    cmd_bytes, command_code, command_name, timeout=3.0
                )

            # Fall back to synchronous mode
            # Get command socket
            command_socket = self.connection._command_socket
            if command_socket is None:
                return {
                    'success': False,
                    'error': 'Command socket not available'
                }

            # Send command
            command_socket.sendall(cmd_bytes)
            self.logger.debug(f"Sent {command_name} (code {command_code:#06x})")

            # Read 128-byte response
            ack_response = self._receive_full_bytes(command_socket, 128, timeout=3.0)

            # Parse response
            parsed = self._parse_response(ack_response)

            # Read additional data if present (CRITICAL for buffer management)
            add_data_bytes = parsed['reserved']
            if add_data_bytes > 0:
                self.logger.debug(f"Reading {add_data_bytes} additional bytes...")
                additional_data = self._receive_full_bytes(command_socket, add_data_bytes, timeout=3.0)
                parsed['additional_data'] = additional_data

            return {
                'success': True,
                'parsed': parsed,
                'raw_response': ack_response
            }

        except (socket.timeout, TimeoutError) as e:
            self.logger.error(f"Timeout waiting for {command_name} response")
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

    def _send_command(
        self,
        command_code: int,
        command_name: str,
        params: Optional[List[int]] = None,
        value: float = 0.0,
        data: bytes = b'',
        additional_data_size: int = 0,
        wait_for_response: bool = True
    ) -> Dict[str, Any]:
        """
        Send an action command (non-query).

        Similar to _query_command but for commands that perform actions
        rather than querying data. Uses async reader when available.

        Args:
            command_code: Command code to send
            command_name: Human-readable command name for logging
            params: Optional list of parameters
            value: Optional double value
            data: Optional data payload (72 bytes max)
            additional_data_size: Size of additional data to follow
            wait_for_response: If False, send command without waiting for response
                              (fire-and-forget mode for laser commands that don't ACK)

        Returns:
            Dict with 'success' and optional 'error'
        """
        if not self.connection.is_connected():
            return {
                'success': False,
                'error': 'Not connected to microscope'
            }

        try:
            # Ensure params[6] has TRIGGER_CALL_BACK flag
            if params is None:
                params = [0] * 7
            elif len(params) < 7:
                params = list(params) + [0] * (7 - len(params))
            else:
                params = list(params)

            # Always set TRIGGER_CALL_BACK flag in params[6]
            params[6] = CommandDataBits.TRIGGER_CALL_BACK

            # Convert string data to bytes if needed
            if isinstance(data, str):
                data = data.encode('utf-8')

            # Encode command with data
            cmd_bytes = self.connection.encoder.encode_command(
                code=command_code,
                status=0,
                params=params,
                value=value,
                data=data,
                additional_data_size=additional_data_size
            )

            # Use get_command_name for better logging if command_name is generic
            if command_name == str(command_code) or 'COMMAND' in command_name.upper():
                command_name = get_command_name(command_code)

            # Check if async reader is available
            if hasattr(self.connection, 'has_async_reader') and self.connection.has_async_reader:
                return self._send_via_async_reader(
                    cmd_bytes, command_code, command_name, timeout=3.0,
                    wait_for_response=wait_for_response
                )

            # Fall back to synchronous mode
            # Get command socket
            command_socket = self.connection._command_socket
            if command_socket is None:
                return {
                    'success': False,
                    'error': 'Command socket not available'
                }

            # Send command
            command_socket.sendall(cmd_bytes)
            self.logger.debug(f"Sent {command_name} (code {command_code:#06x})")

            # Fire-and-forget mode: don't wait for response
            # Used for laser commands (0x20xx) that never send ACK responses
            if not wait_for_response:
                self.logger.debug(f"{command_name} sent (fire-and-forget, no response expected)")
                return {
                    'success': True,
                    'fire_and_forget': True
                }

            # Read 128-byte response
            ack_response = self._receive_full_bytes(command_socket, 128, timeout=3.0)

            # Parse response
            parsed = self._parse_response(ack_response)

            # Read additional data if present
            add_data_bytes = parsed['reserved']
            if add_data_bytes > 0:
                self.logger.debug(f"Reading {add_data_bytes} additional bytes...")
                additional_data = self._receive_full_bytes(command_socket, add_data_bytes, timeout=3.0)
                parsed['additional_data'] = additional_data

            return {
                'success': True,
                'parsed': parsed,
                'raw_response': ack_response
            }

        except (socket.timeout, TimeoutError) as e:
            self.logger.error(f"Timeout waiting for {command_name} response")
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

    def _send_workflow_command(self, cmd: 'Command', timeout: float = 5.0) -> bytes:
        """
        Send a workflow command with workflow data.

        CRITICAL WORKFLOW COMMAND STRUCTURE (discovered 2026-01-14):
        ============================================================
        The server expects a SPECIFIC command structure for workflows.
        This was determined by comparing server logs between Python (failed)
        and C++ GUI (succeeded). Reference: oldcodereference/tcpip_nuc.py

        Required header fields:
        - params[6] (cmdDataBits0) = 0x80000000 (TRIGGER_CALL_BACK flag)
        - additional_data_size = file_size (workflow file length in bytes)
        - data buffer = empty 72 bytes (NOT the file size!)

        The workflow file itself is sent as binary data AFTER the header.
        The file should be read as binary (preserve CRLF) and sent as-is.

        Server log comparison (ServerWorkflowResponse.txt):
        - FAILED: cmdDataBits0=0x00000000, additionalDataBytes=0 → "Work flow empty"
        - SUCCESS: cmdDataBits0=0x80000000, additionalDataBytes=2204 → workflow executes

        NOTE: Workflow commands are "fire and forget" - the server does not
        send a synchronous response. Status updates come asynchronously via
        LED_DISABLE (0x4003) and LASER_LEVEL_GET (0x2002) messages.

        Args:
            cmd: WorkflowCommand object with workflow_data attribute
            timeout: Not used (kept for API compatibility)

        Returns:
            Empty bytes (no response expected)

        Raises:
            RuntimeError: If not connected or send fails
        """
        import struct

        if not self.connection.is_connected():
            raise RuntimeError("Not connected to microscope")

        workflow_data = cmd.workflow_data
        file_size = len(workflow_data)
        command_code = cmd.code
        cmd_name = get_command_name(command_code)

        # Parse workflow for debugging info (not used in actual command)
        workflow_flags = self._parse_workflow_flags(workflow_data)
        self.logger.info(f"Sending workflow: {file_size} bytes (detected_flags=0x{workflow_flags:08X})")

        try:
            # Get command socket
            command_socket = self.connection._command_socket
            if command_socket is None:
                raise RuntimeError("Command socket not available")

            # Build params to match C++ GUI (from server log):
            # - params[0-5] = 0 (hardwareID, subsystemID, clientID, int32Data0-2)
            # - params[6] = 0x80000000 (cmdDataBits0 = TRIGGER_CALL_BACK flag!)
            # Server log shows C++ GUI uses cmdDataBits0 = 0x80000000, NOT 1!
            params = [0, 0, 0, 0, 0, 0, 0x80000000]  # TRIGGER_CALL_BACK

            # OLD CODE (tcpip_nuc.py lines 59-63):
            # - addDataBytes = fileBytes (file size goes HERE, not in buffer)
            # - buffer_72 = b"\0" * 72 (empty buffer!)
            empty_buffer = b'\x00' * 72

            # Encode command header with file size in addDataBytes field
            cmd_bytes = self.connection.encoder.encode_command(
                code=command_code,
                status=0,
                params=params,
                value=0.0,
                data=empty_buffer,  # Empty buffer, NOT file size!
                additional_data_size=file_size  # File size goes HERE!
            )

            # Send header
            command_socket.sendall(cmd_bytes)
            self.logger.debug(f"Sent workflow header for {cmd_name}")

            # Send workflow data
            command_socket.sendall(workflow_data)
            self.logger.info(f"Workflow sent: {file_size} bytes to {cmd_name}")

            # No response expected - workflow commands are fire-and-forget
            # Status updates come asynchronously via system state events
            return b''

        except Exception as e:
            self.logger.error(f"Error sending workflow: {e}", exc_info=True)
            raise

    def _parse_workflow_flags(self, workflow_data: bytes) -> int:
        """
        Parse workflow data to determine appropriate command flags.

        Examines the workflow file content to detect:
        - Stack option (ZStack, Tile, ZSweep, etc.)
        - Save settings (save to disk, MIP, etc.)

        Args:
            workflow_data: Workflow file content as bytes

        Returns:
            Integer flags for params[6] (cmdDataBits0)
        """
        import re

        flags = 0

        try:
            # Decode workflow text
            workflow_text = workflow_data.decode('utf-8', errors='replace')

            # Detect Stack option
            stack_match = re.search(r'Stack option\s*=\s*(\w+)', workflow_text)
            stack_option = stack_match.group(1).lower() if stack_match else 'none'

            # Detect save settings
            save_data_match = re.search(r'Save image data\s*=\s*(\w+)', workflow_text)
            save_data = save_data_match.group(1).lower() if save_data_match else 'notsaved'
            saving_to_disk = save_data not in ('notsaved', 'none', '')

            save_mip_match = re.search(r'Save max projection\s*=\s*(\w+)', workflow_text)
            save_mip = save_mip_match.group(1).lower() if save_mip_match else 'false'
            saving_mip = save_mip == 'true'

            # Set flags based on workflow type
            # Reference: CommandDataBits in tcp_protocol.py
            if stack_option in ('zstack', 'zstack movie', 'zsweep'):
                # Z-stack workflows: enable Z-sweep mode
                flags |= CommandDataBits.STAGE_ZSWEEP  # 0x00000020
                if saving_mip:
                    flags |= CommandDataBits.MAX_PROJECTION  # 0x00000004

            elif stack_option == 'tile':
                # Tile/mosaic workflows: positions buffered
                flags |= CommandDataBits.STAGE_POSITIONS_IN_BUFFER  # 0x00000002
                # Don't update client during rapid tile movements
                flags |= CommandDataBits.STAGE_NOT_UPDATE_CLIENT  # 0x00000010

            elif stack_option in ('opt', 'opt zstacks'):
                # OPT (Optical Projection Tomography) workflows
                flags |= CommandDataBits.STAGE_ZSWEEP  # 0x00000020
                if saving_mip:
                    flags |= CommandDataBits.MAX_PROJECTION  # 0x00000004

            elif stack_option == 'bidirectional':
                # Bidirectional scanning
                flags |= CommandDataBits.STAGE_ZSWEEP  # 0x00000020

            # Add save to disk flag if saving images
            if saving_to_disk:
                flags |= CommandDataBits.SAVE_TO_DISK  # 0x00000008

            # Always include experiment time remaining for progress tracking
            flags |= CommandDataBits.EXPERIMENT_TIME_REMAINING  # 0x00000001

            self.logger.debug(f"Workflow type: {stack_option}, saving: {saving_to_disk}, "
                            f"MIP: {saving_mip}, flags: 0x{flags:08X}")

        except Exception as e:
            self.logger.warning(f"Failed to parse workflow flags, using defaults: {e}")
            # Default: just experiment time remaining
            flags = CommandDataBits.EXPERIMENT_TIME_REMAINING

        return flags

    def _send_workflow_stop_command(self, cmd: 'Command', timeout: float = 3.0) -> bytes:
        """
        Send a workflow stop command.

        WORKFLOW_STOP requires special handling:
        - Does NOT use TRIGGER_CALL_BACK flag (unlike query commands)
        - Uses params[6] = 0 (no special flags needed)
        - Still expects a response

        Args:
            cmd: Command object with code=CMD_WORKFLOW_STOP
            timeout: Response timeout in seconds

        Returns:
            128-byte response from microscope

        Raises:
            RuntimeError: If not connected
            TimeoutError: If response timeout
        """
        if not self.connection.is_connected():
            raise RuntimeError("Not connected to microscope")

        command_code = cmd.code
        cmd_name = get_command_name(command_code)

        self.logger.info(f"Sending workflow stop command")

        try:
            # Get command socket
            command_socket = self.connection._command_socket
            if command_socket is None:
                raise RuntimeError("Command socket not available")

            # Encode command with params[6] = 0 (NOT TRIGGER_CALL_BACK)
            cmd_bytes = self.connection.encoder.encode_command(
                code=command_code,
                status=0,
                params=[0] * 7,  # No special flags for stop
                value=0.0,
                data=b''
            )

            # Send command
            command_socket.sendall(cmd_bytes)
            self.logger.debug(f"Sent {cmd_name}")

            # Wait for response
            response = self._receive_full_bytes(command_socket, 128, timeout=timeout)

            # Parse response
            parsed = self._parse_response(response)
            status = parsed.get('status_code', 0)

            # Handle any additional data
            add_data_bytes = parsed.get('reserved', 0)
            if add_data_bytes > 0:
                self.logger.debug(f"Reading {add_data_bytes} additional bytes from stop response...")
                additional_data = self._receive_full_bytes(command_socket, add_data_bytes, timeout=3.0)
                parsed['additional_data'] = additional_data

            self.logger.info(f"Workflow stop acknowledged (status={status})")
            return response

        except socket.timeout as e:
            self.logger.error(f"Timeout waiting for workflow stop response")
            raise TimeoutError(f"Timeout waiting for {cmd_name} response") from e
        except Exception as e:
            self.logger.error(f"Error sending workflow stop: {e}", exc_info=True)
            raise

    def _receive_full_bytes(self, sock: socket.socket, num_bytes: int, timeout: float = 3.0) -> bytes:
        """
        Receive exact number of bytes from socket.

        Args:
            sock: Socket to read from
            num_bytes: Number of bytes to read
            timeout: Timeout in seconds

        Returns:
            Bytes read from socket

        Raises:
            socket.timeout: If timeout occurs
            RuntimeError: If socket closes prematurely
        """
        sock.settimeout(timeout)
        data = b''
        while len(data) < num_bytes:
            chunk = sock.recv(num_bytes - len(data))
            if not chunk:
                raise RuntimeError(
                    f"Socket closed while reading "
                    f"(got {len(data)}/{num_bytes} bytes)"
                )
            data += chunk
        return data

    def _parse_response(self, response: bytes) -> Dict[str, Any]:
        """
        Parse 128-byte protocol response.

        Args:
            response: 128-byte response from microscope

        Returns:
            Dict with parsed fields:
                - start_marker: uint32
                - command_code: uint32 (response code)
                - status_code: uint32
                - params: list of 7 int32 values
                - value: double
                - reserved: uint32 (addDataBytes field)
                - data: bytes (72-byte buffer field, may contain response strings)
                - end_marker: uint32
        """
        if len(response) != 128:
            raise ValueError(f"Invalid response size: {len(response)} (expected 128)")

        start_marker = struct.unpack('<I', response[0:4])[0]
        response_code = struct.unpack('<I', response[4:8])[0]
        status_code = struct.unpack('<I', response[8:12])[0]

        # Unpack 7 parameters
        params = []
        for i in range(7):
            offset = 12 + (i * 4)
            param = struct.unpack('<i', response[offset:offset+4])[0]
            params.append(param)

        # Unpack value (double)
        value = struct.unpack('<d', response[40:48])[0]

        # Get addDataBytes field
        add_data_bytes = struct.unpack('<I', response[48:52])[0]

        # Extract 72-byte data buffer (bytes 52-123)
        # This is where responses like laser power strings are stored
        data_buffer = response[52:124]

        # Get end marker
        end_marker = struct.unpack('<I', response[124:128])[0]

        # Validate markers
        expected_start = 0xF321E654
        expected_end = 0xFEDC4321
        if start_marker != expected_start:
            self.logger.warning(
                f"Invalid start marker: 0x{start_marker:08X} "
                f"(expected 0x{expected_start:08X})"
            )
        if end_marker != expected_end:
            self.logger.warning(
                f"Invalid end marker: 0x{end_marker:08X} "
                f"(expected 0x{expected_end:08X})"
            )

        return {
            'start_marker': start_marker,
            'command_code': response_code,
            'status_code': status_code,
            'params': params,
            'value': value,
            'reserved': add_data_bytes,
            'data': data_buffer,  # ADDED: 72-byte buffer field
            'end_marker': end_marker
        }

    def _send_via_async_reader(
        self,
        cmd_bytes: bytes,
        command_code: int,
        command_name: str,
        timeout: float = 3.0,
        wait_for_response: bool = True
    ) -> Dict[str, Any]:
        """
        Send command via async reader and convert response.

        This method uses the background socket reader's dispatch queue
        for non-blocking command-response handling.

        Args:
            cmd_bytes: Encoded 128-byte command
            command_code: Command code for response matching
            command_name: Human-readable name for logging
            timeout: Response timeout in seconds
            wait_for_response: If False, send without waiting (fire-and-forget)

        Returns:
            Dict with 'success', 'parsed', 'raw_response' matching
            the synchronous _send_command format.
        """
        try:
            # Fire-and-forget mode: send without waiting for response
            if not wait_for_response:
                # Send directly via socket without registering for response
                command_socket = self.connection._command_socket
                if command_socket is None:
                    return {
                        'success': False,
                        'error': 'Command socket not available'
                    }
                command_socket.sendall(cmd_bytes)
                self.logger.debug(f"{command_name} sent (fire-and-forget via async path)")
                return {
                    'success': True,
                    'fire_and_forget': True
                }

            # Send via async reader and wait for response
            response = self.connection.send_command_async(
                cmd_bytes, command_code, timeout=timeout
            )

            if response is None:
                self.logger.error(f"Timeout waiting for {command_name} response (async)")
                return {
                    'success': False,
                    'error': 'timeout'
                }

            self.logger.debug(f"Received {command_name} response (async)")

            # Convert ParsedMessage to legacy dict format
            parsed = self._convert_parsed_message(response)

            # Include additional data if present (follows 128-byte message for some commands)
            # Concatenate it to raw_response so callers get the full response
            raw_response = response.raw_data
            if response.additional_data:
                raw_response = raw_response + response.additional_data
                self.logger.debug(f"{command_name} has {len(response.additional_data)} bytes additional data")

            return {
                'success': True,
                'parsed': parsed,
                'raw_response': raw_response,
                'additional_data': response.additional_data
            }

        except Exception as e:
            self.logger.error(f"Error in async {command_name}: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    def _convert_parsed_message(self, msg: "ParsedMessage") -> Dict[str, Any]:
        """
        Convert ParsedMessage from async reader to legacy dict format.

        Args:
            msg: ParsedMessage from socket_reader

        Returns:
            Dict matching the format from _parse_response()
        """
        # Build params list from individual fields
        params = [
            msg.hardware_id,
            msg.subsystem_id,
            msg.client_id,
            msg.int32_data0,
            msg.int32_data1,
            msg.int32_data2,
            msg.cmd_data_bits,
        ]

        result = {
            'start_marker': msg.start_marker,
            'command_code': msg.command_code,
            'status_code': msg.status_code,
            'params': params,
            'value': msg.value,
            'reserved': msg.additional_data_size,
            'data': msg.data_field,
            'end_marker': msg.end_marker
        }

        # Include additional data if present
        if hasattr(msg, 'additional_data') and msg.additional_data:
            result['additional_data'] = msg.additional_data

        return result

    # ============================================================================
    # COMPATIBILITY METHODS - For consolidating all send_command implementations
    # ============================================================================

    def send_command_raw(
        self,
        command_code: int,
        command_data: Optional[List] = None,
        wait_response: bool = False,
        timeout: float = 5.0
    ) -> Optional[bytes]:
        """
        Legacy compatibility method for direct command sending.

        This method provides backward compatibility for code that uses the
        old TCPClient.send_command() interface with a list of command data.

        Args:
            command_code: Command code to send
            command_data: Optional list with up to 10 elements:
                [0]: status (ignored, always 0)
                [1-7]: params (7 int32 values)
                [8]: value (double)
                [9]: data (string or bytes, up to 72 bytes)
            wait_response: If True, wait for and return response (default: False for legacy)
            timeout: Timeout in seconds if waiting for response

        Returns:
            None if not waiting for response (fire-and-forget mode)
            bytes (128-byte response) if wait_response=True

        Example:
            # Legacy usage
            service.send_command_raw(0x2001, [0,0,0,1,0,0,0,0,10.5,b""])
        """
        # Parse legacy command_data format
        if command_data:
            # Extract components from legacy format
            status = command_data[0] if len(command_data) > 0 else 0
            params = command_data[1:8] if len(command_data) > 7 else command_data[1:] if len(command_data) > 1 else []
            value = command_data[8] if len(command_data) > 8 else 0.0
            data = command_data[9] if len(command_data) > 9 else b''
        else:
            status = 0
            params = []
            value = 0.0
            data = b''

        # Convert data to bytes if it's a string
        if isinstance(data, str):
            data = data.encode('utf-8')

        # Get human-readable command name for logging
        cmd_name = get_command_name(command_code)
        self.logger.debug(f"send_command_raw: {cmd_name} ({command_code:#06x})")

        if wait_response:
            # Use existing _send_command for response handling
            result = self._send_command(
                command_code,
                cmd_name,
                params=params,
                value=value,
                data=data
            )
            return result['raw_response'] if result['success'] else None
        else:
            # Fire-and-forget mode - send without waiting for response
            try:
                # Ensure params is properly formatted
                if params is None:
                    params = [0] * 7
                elif len(params) < 7:
                    params = list(params) + [0] * (7 - len(params))
                else:
                    params = list(params[:7])  # Take only first 7

                # Encode command
                cmd_bytes = self.connection.encoder.encode_command(
                    code=command_code,
                    status=0,  # Always 0 for sending
                    params=params,
                    value=value,
                    data=data
                )

                # Get command socket
                command_socket = self.connection._command_socket
                if command_socket is None:
                    self.logger.error("Command socket not available")
                    return None

                # Send command (fire-and-forget)
                command_socket.sendall(cmd_bytes)
                self.logger.debug(f"Sent {cmd_name} (fire-and-forget)")
                return None

            except Exception as e:
                self.logger.error(f"Error sending {cmd_name}: {e}")
                return None

    def send_command(
        self,
        cmd: Union['Command', int],
        timeout: float = 5.0,
        command_data: Optional[List] = None
    ) -> bytes:
        """
        MVC-compatible command sending method.

        This method accepts either:
        1. A Command object (for MVC pattern)
        2. A command code int with optional command_data (for backward compatibility)

        Args:
            cmd: Command object or command code integer
            timeout: Response timeout in seconds
            command_data: Optional command data (only used if cmd is an int)

        Returns:
            128-byte response from microscope

        Raises:
            RuntimeError: If not connected
            ValueError: If command is invalid
            TimeoutError: If response timeout

        Example:
            # MVC usage with Command object
            from py2flamingo.models.command import Command
            response = service.send_command(Command(code=0x2001, parameters={'value': 10.5}))

            # Legacy usage with int
            response = service.send_command(0x2001, command_data=[0,0,0,1,0,0,0,0,10.5,b""])
        """
        # Handle Command object
        if hasattr(cmd, 'code'):
            # Check if this is a WorkflowCommand (with workflow data)
            if hasattr(cmd, 'workflow_data') and cmd.workflow_data is not None:
                # Special handling for WorkflowCommand
                return self._send_workflow_command(cmd, timeout)

            # Check if this is a workflow stop command
            # WORKFLOW_STOP should NOT use TRIGGER_CALL_BACK flag
            from py2flamingo.core.tcp_protocol import CommandCode
            if cmd.code == CommandCode.CMD_WORKFLOW_STOP:
                return self._send_workflow_stop_command(cmd, timeout)

            # Extract from Command object
            command_code = cmd.code
            params = cmd.parameters.get('params', [])
            value = cmd.parameters.get('value', 0.0)
            data = cmd.parameters.get('data', b'')

            # Get command name
            cmd_name = get_command_name(command_code)
            self.logger.debug(f"send_command (Command): {cmd_name} ({command_code:#06x})")

        # Handle integer command code
        elif isinstance(cmd, int):
            command_code = cmd

            # Parse legacy command_data if provided
            if command_data:
                params = command_data[1:8] if len(command_data) > 7 else command_data[1:] if len(command_data) > 1 else []
                value = command_data[8] if len(command_data) > 8 else 0.0
                data = command_data[9] if len(command_data) > 9 else b''
            else:
                params = []
                value = 0.0
                data = b''

            # Get command name
            cmd_name = get_command_name(command_code)
            self.logger.debug(f"send_command (int): {cmd_name} ({command_code:#06x})")

        else:
            raise ValueError(f"Invalid command type: {type(cmd)}")

        # Use existing _send_command for actual sending
        result = self._send_command(
            command_code,
            cmd_name,
            params=params,
            value=value,
            data=data if isinstance(data, bytes) else data.encode('utf-8') if isinstance(data, str) else b''
        )

        if result['success']:
            return result['raw_response']
        else:
            error_msg = result.get('error', 'Unknown error')
            if error_msg == 'timeout':
                raise TimeoutError(f"Timeout waiting for {cmd_name} response")
            elif error_msg == 'Not connected to microscope':
                raise RuntimeError(error_msg)
            else:
                raise ValueError(f"Command {cmd_name} failed: {error_msg}")

    def send_command_queued(
        self,
        command_code: int,
        command_data: Optional[List] = None
    ) -> None:
        """
        Queue-based command sending for asynchronous operation.

        This method is for backward compatibility with ConnectionService
        that uses queue-based command sending.

        Args:
            command_code: Command code to send
            command_data: Optional command data list

        Note:
            This requires the connection to have queue_manager and event_manager
            attributes for queue-based operation.
        """
        if hasattr(self.connection, 'queue_manager') and hasattr(self.connection, 'event_manager'):
            # Queue-based sending (for ConnectionService compatibility)
            queue_manager = self.connection.queue_manager
            event_manager = self.connection.event_manager

            # Put command in queue
            queue_manager.get_queue('command').put(command_code)

            # Put data if provided
            if command_data:
                queue_manager.get_queue('command_data').put(command_data)

            # Set send event
            event_manager.get_event('send').set()

            # Log the queued command
            cmd_name = get_command_name(command_code)
            self.logger.debug(f"Queued command: {cmd_name} ({command_code:#06x})")
        else:
            # Fall back to direct sending if no queue manager
            self.logger.warning("No queue manager available, using direct send")
            self.send_command_raw(command_code, command_data, wait_response=False)
