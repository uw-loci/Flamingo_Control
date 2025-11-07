"""
TCP Protocol encoding and decoding for Flamingo microscope communication.

This module handles the binary protocol format used to communicate with the
Flamingo microscope control system. The protocol uses a fixed 128-byte command
structure with start/end markers.

Protocol Structure (128 bytes total):
    - Start marker: 0xF321E654 (4 bytes, uint32)
    - Command code: (4 bytes, uint32)
    - Status: (4 bytes, uint32)
    - Command bits 0-6: (7 x 4 bytes, uint32)
    - Value: (8 bytes, double)
    - Reserved: (4 bytes, uint32)
    - Data: (72 bytes, padded with null bytes)
    - End marker: 0xFEDC4321 (4 bytes, uint32)
"""

import struct
from typing import List, Dict, Any, Optional


class CommandCode:
    """Command codes for microscope operations."""

    CMD_SCOPE_SETTINGS_LOAD = 4105
    CMD_WORKFLOW_START = 12292
    CMD_WORKFLOW_STOP = 12293
    CMD_STAGE_POSITION_GET = 24584
    CMD_STAGE_POSITION_SET = 24580
    CMD_SYSTEM_STATE_GET = 40967
    CMD_SYSTEM_STATE_IDLE = 40962


class CommandDataBits:
    """
    Command data bits flags for the cmdBits6 parameter field.

    These flags control command behavior, particularly response handling.
    From CommandCodes.h enum COMMAND_DATA_BITS.
    """

    # Trigger callback/response from microscope (CRITICAL for query commands)
    TRIGGER_CALL_BACK = 0x80000000

    # Other flags from CommandCodes.h
    EXPERIMENT_TIME_REMAINING = 0x00000001
    STAGE_POSITIONS_IN_BUFFER = 0x00000002
    MAX_PROJECTION = 0x00000004


class ProtocolEncoder:
    """
    Encodes commands into the Flamingo microscope binary protocol format.

    The protocol uses a fixed 128-byte structure with start and end markers
    to ensure data integrity.

    Example:
        >>> encoder = ProtocolEncoder()
        >>> cmd_bytes = encoder.encode_command(
        ...     code=CommandCode.CMD_WORKFLOW_START,
        ...     status=0,
        ...     params=[0, 0, 0, 0, 0, 0, 0],
        ...     value=0.0,
        ...     data=b'test'
        ... )
        >>> len(cmd_bytes)
        128
    """

    # Protocol markers
    START_MARKER = 0xF321E654
    END_MARKER = 0xFEDC4321

    # Command structure format
    # Format: I=uint32 (4 bytes), d=double (8 bytes), 72s=72 bytes string
    COMMAND_STRUCT = struct.Struct("I I I I I I I I I I d I 72s I")

    # Expected size of encoded command
    COMMAND_SIZE = 128

    def encode_command(
        self,
        code: int,
        status: int = 0,
        params: Optional[List[int]] = None,
        value: float = 0.0,
        data: bytes = b'',
        additional_data_size: int = 0
    ) -> bytes:
        """
        Encode a command into the binary protocol format.

        Args:
            code: Command code (see CommandCode constants)
            status: Status field (default: 0)
            params: List of 7 parameter integers for cmdBits0-6 (default: [0]*7)
            value: Double precision floating point value (default: 0.0)
            data: Data payload, max 72 bytes (default: empty)
            additional_data_size: Size of additional data to be sent after command
                                 (used for file transfers, sets addDataBytes field)

        Returns:
            128-byte command structure as bytes

        Raises:
            ValueError: If parameters are invalid

        Example:
            >>> encoder = ProtocolEncoder()
            >>> cmd = encoder.encode_command(CommandCode.CMD_WORKFLOW_STOP)
            >>> len(cmd)
            128
        """
        # Validate and prepare parameters
        if params is None:
            params = [0] * 7
        elif len(params) < 7:
            # Pad with zeros if too short
            params = list(params) + [0] * (7 - len(params))
        elif len(params) > 7:
            raise ValueError(f"Too many parameters: expected 7, got {len(params)}")

        # Validate command code
        if not isinstance(code, int) or code < 0:
            raise ValueError(f"Invalid command code: {code}")

        # Validate status
        if not isinstance(status, int) or status < 0:
            raise ValueError(f"Invalid status: {status}")

        # Validate value
        if not isinstance(value, (int, float)):
            raise ValueError(f"Invalid value type: {type(value)}")
        value = float(value)

        # Prepare data field (must be exactly 72 bytes)
        if isinstance(data, str):
            data = data.encode('utf-8')

        if not isinstance(data, bytes):
            raise ValueError(f"Data must be bytes or str, got {type(data)}")

        # Truncate or pad to 72 bytes
        if len(data) > 72:
            data = data[:72]
        else:
            data = data.ljust(72, b'\x00')

        # DEBUG: Log command details for movement commands
        import logging
        logger = logging.getLogger(__name__)
        if code in [24580, 24584]:  # STAGE_POSITION_SET or STAGE_POSITION_GET
            logger.info(f"[PROTOCOL ENCODER] Encoding command {code}")
            logger.info(f"[PROTOCOL ENCODER]   params array: {params}")
            logger.info(f"[PROTOCOL ENCODER]   params[0] (cmdBits0): {params[0]} (0x{params[0]:08X})")
            logger.info(f"[PROTOCOL ENCODER]   params[6] (cmdBits6): {params[6]} (0x{params[6]:08X})")
            logger.info(f"[PROTOCOL ENCODER]   value: {value}")

        # Pack command structure
        try:
            command_bytes = self.COMMAND_STRUCT.pack(
                self.START_MARKER,  # Start marker
                code,               # Command code
                status,             # Status
                params[0],          # cmdBits0
                params[1],          # cmdBits1
                params[2],          # cmdBits2
                params[3],          # cmdBits3
                params[4],          # cmdBits4
                params[5],          # cmdBits5
                params[6],          # cmdBits6
                value,              # value (double)
                additional_data_size,  # addDataBytes (size of additional file data)
                data,               # data (72 bytes)
                self.END_MARKER     # End marker
            )
        except struct.error as e:
            raise ValueError(f"Failed to pack command structure: {e}")

        # DEBUG: Log packed bytes for movement commands
        if code in [24580, 24584]:  # STAGE_POSITION_SET or STAGE_POSITION_GET
            # cmdBits0 is at bytes 12-15 (4th uint32 field)
            cmdBits0_bytes = command_bytes[12:16]
            cmdBits0_value = struct.unpack('I', cmdBits0_bytes)[0]

            # cmdBits6 is at bytes 36-39 (10th uint32 field)
            cmdBits6_bytes = command_bytes[36:40]
            cmdBits6_value = struct.unpack('I', cmdBits6_bytes)[0]

            logger.info(f"[PROTOCOL ENCODER] Packed bytes verification:")
            logger.info(f"[PROTOCOL ENCODER]   cmdBits0 at bytes 12-15: 0x{cmdBits0_value:08X}")
            logger.info(f"[PROTOCOL ENCODER]   cmdBits6 at bytes 36-39: 0x{cmdBits6_value:08X}")

        # Verify size
        if len(command_bytes) != self.COMMAND_SIZE:
            raise ValueError(
                f"Encoded command has wrong size: "
                f"expected {self.COMMAND_SIZE}, got {len(command_bytes)}"
            )

        return command_bytes


class ProtocolDecoder:
    """
    Decodes commands from the Flamingo microscope binary protocol format.

    Validates the start and end markers to ensure data integrity.

    Example:
        >>> decoder = ProtocolDecoder()
        >>> encoder = ProtocolEncoder()
        >>> encoded = encoder.encode_command(CommandCode.CMD_WORKFLOW_START)
        >>> decoded = decoder.decode_command(encoded)
        >>> decoded['code']
        12292
    """

    # Protocol markers (same as encoder)
    START_MARKER = 0xF321E654
    END_MARKER = 0xFEDC4321

    # Command structure format
    COMMAND_STRUCT = struct.Struct("I I I I I I I I I I d I 72s I")

    # Expected size
    COMMAND_SIZE = 128

    def decode_command(self, data: bytes) -> Dict[str, Any]:
        """
        Decode a binary command into a dictionary.

        Args:
            data: 128-byte command structure

        Returns:
            Dictionary containing:
                - start_marker: Start marker value
                - code: Command code
                - status: Status field
                - params: List of 7 parameter values
                - value: Double precision value
                - reserved: Reserved field
                - data: Data payload (72 bytes, may contain null padding)
                - end_marker: End marker value
                - valid: Boolean indicating if markers are correct

        Raises:
            ValueError: If data is wrong size or cannot be unpacked

        Example:
            >>> decoder = ProtocolDecoder()
            >>> result = decoder.decode_command(command_bytes)
            >>> result['valid']
            True
        """
        # Validate size
        if len(data) != self.COMMAND_SIZE:
            raise ValueError(
                f"Invalid command size: expected {self.COMMAND_SIZE}, got {len(data)}"
            )

        # Unpack structure
        try:
            unpacked = self.COMMAND_STRUCT.unpack(data)
        except struct.error as e:
            raise ValueError(f"Failed to unpack command structure: {e}")

        # Extract fields
        start_marker = unpacked[0]
        code = unpacked[1]
        status = unpacked[2]
        params = list(unpacked[3:10])  # cmdBits0-6 (7 values)
        value = unpacked[10]
        reserved = unpacked[11]
        data_field = unpacked[12]
        end_marker = unpacked[13]

        # Validate markers
        valid = (
            start_marker == self.START_MARKER and
            end_marker == self.END_MARKER
        )

        return {
            'start_marker': start_marker,
            'code': code,
            'status': status,
            'params': params,
            'value': value,
            'reserved': reserved,
            'data': data_field,
            'end_marker': end_marker,
            'valid': valid
        }

    def validate_command(self, data: bytes) -> tuple[bool, str]:
        """
        Validate a command without full decoding.

        Args:
            data: 128-byte command structure

        Returns:
            Tuple of (is_valid, error_message)
            If valid, error_message is empty string.

        Example:
            >>> decoder = ProtocolDecoder()
            >>> valid, error = decoder.validate_command(command_bytes)
            >>> if not valid:
            ...     print(f"Invalid command: {error}")
        """
        # Check size
        if len(data) != self.COMMAND_SIZE:
            return False, f"Wrong size: expected {self.COMMAND_SIZE}, got {len(data)}"

        # Check markers
        try:
            start_marker = struct.unpack("I", data[:4])[0]
            end_marker = struct.unpack("I", data[-4:])[0]

            if start_marker != self.START_MARKER:
                return False, f"Invalid start marker: 0x{start_marker:08X}"

            if end_marker != self.END_MARKER:
                return False, f"Invalid end marker: 0x{end_marker:08X}"

            return True, ""

        except struct.error as e:
            return False, f"Failed to unpack: {e}"
