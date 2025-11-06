"""
Base service class for microscope command operations.

Provides common command sending, receiving, and parsing logic
shared across all subsystem services (Camera, Stage, Laser, etc.).
"""

import logging
import struct
import socket
from typing import Dict, Any, Optional, List

from py2flamingo.core.tcp_protocol import CommandDataBits


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

            # Get command socket
            command_socket = self.connection._command_socket
            if command_socket is None:
                return {
                    'success': False,
                    'error': 'Command socket not available'
                }

            # Send command
            command_socket.sendall(cmd_bytes)
            self.logger.debug(f"Sent {command_name} (code {command_code})")

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
        additional_data_size: int = 0
    ) -> Dict[str, Any]:
        """
        Send an action command (non-query).

        Similar to _query_command but for commands that perform actions
        rather than querying data.

        Args:
            command_code: Command code to send
            command_name: Human-readable command name for logging
            params: Optional list of parameters
            value: Optional double value
            data: Optional data payload (72 bytes max)
            additional_data_size: Size of additional data to follow

        Returns:
            Dict with 'success' and optional 'error'
        """
        # For now, action commands use same logic as queries
        # Both need TRIGGER_CALL_BACK flag and response handling
        return self._query_command(command_code, command_name, params, value)

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
            'end_marker': end_marker
        }
