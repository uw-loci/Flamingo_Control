"""
Protocol Encoder for Flamingo Microscope TCP Communication.

This module provides encoding and decoding for the binary protocol used to communicate
with the Flamingo microscope control system. The protocol uses a fixed 128-byte command
structure with start/end markers for data integrity.

PROTOCOL STRUCTURE (128 bytes total):
=====================================
    Offset  Size  Type    Field Name       Description
    ------  ----  ------  ---------------  ------------------------------------
    0-3     4     uint32  startMarker      0xF321E654 (validation marker)
    4-7     4     uint32  cmd              Command code
    8-11    4     uint32  status           Status/error code
    12-15   4     uint32  int32Data0       Integer data 0 / cmdBits0
    16-19   4     uint32  int32Data1       Integer data 1 / cmdBits1
    20-23   4     uint32  int32Data2       Integer data 2 / cmdBits2
    24-27   4     uint32  hardwareID       Hardware ID / cmdBits3
    28-31   4     uint32  subsystemID      Subsystem ID / cmdBits4
    32-35   4     uint32  clientID         Client ID / cmdBits5
    36-39   4     uint32  cmdDataBits0     Command data bits / cmdBits6
    40-47   8     double  doubleData       Double precision floating point data
    48-51   4     uint32  addDataBytes     Additional data size (file transfers)
    52-123  72    bytes   buffer           String/binary data buffer
    124-127 4     uint32  endMarker        0xFEDC4321 (validation marker)

RESPONSE DATA FIELDS BY COMMAND TYPE:
======================================

Different commands return data in different fields. Understanding which field
contains the response data is critical for proper command/response handling.

1. STRING DATA IN BUFFER:
   Commands that return string data in the 72-byte buffer field:

   - LASER_LEVEL_SET/GET (0x2001): Returns laser power as string (e.g., "11.49")
   - STAGE_SAVE_LOCATIONS_GET: Returns positions as "1=X\\n2=Y\\n3=Z\\n"
   - CAMERA_NAME_GET: Returns camera name string
   - VERSION_GET: Returns version string

   Example from logs:
   ```
   cmd = 0x00002001 (set laser level)
   buffer = 11.49
   ```

2. DOUBLE DATA IN doubleData:
   Commands that return floating point data in the doubleData field:

   - STAGE_POSITION_GET (0x6008): Returns position for single axis
   - CAMERA_PIXEL_SIZE_GET: Returns pixel size in micrometers
   - CAMERA_FIELD_OF_VIEW_GET: Returns FOV in micrometers
   - STAGE_POSITION_SET (0x6005): Echoes back the position being set

   Example from logs:
   ```
   cmd = 0x00006005 (stage set position)
   int32Data0 = 1 (axis number)
   doubleData = 7.635 (position in mm)
   ```

3. INTEGER DATA IN int32Data0/1/2:
   Commands that return integer data in the int32Data fields:

   - SYSTEM_STATE_GET (0xa007): Returns state in int32Data0
     * 0 = IDLE
     * 1 = BUSY
     * 2 = ERROR
   - CAMERA_PARAMETERS_GET: Returns width, height, binning in int32Data0/1/2
   - LED_SELECTION_GET: Returns LED index in int32Data0

   Example from logs:
   ```
   cmd = 0x0000a007 (State get)
   int32Data0 = 0 (IDLE state)
   ```

CALLBACK FLAG (cmdDataBits0 = 0x80000000):
==========================================

The callback flag is CRITICAL for query (GET) commands. When set, the server
will send a response back to the client. Without this flag, GET commands will
not receive a response and will timeout.

ALWAYS USE FOR:
- All *_GET commands (position, state, parameters, etc.)
- Commands that expect a response or confirmation

Example from logs - ALL successful GET commands have this flag:
```
cmd = 0x00006008 (stage get position)
cmdDataBits0 = 0x00000000  <- NO CALLBACK FLAG, won't get response!

cmd = 0x00006005 (stage set position)
cmdDataBits0 = 0x80000000  <- CALLBACK FLAG SET, will get response
```

Usage Example:
    >>> from protocol_encoder import ProtocolEncoder, ProtocolDecoder
    >>> encoder = ProtocolEncoder()
    >>>
    >>> # Example 1: Query stage position (returns double in doubleData)
    >>> cmd = encoder.encode_command(
    ...     code=0x6008,                    # STAGE_POSITION_GET
    ...     params=[
    ...         1,                           # axis number (1=X, 2=Y, 3=Z)
    ...         0, 0, 0, 0, 0,
    ...         0x80000000                   # CALLBACK FLAG - REQUIRED!
    ...     ]
    ... )
    >>>
    >>> # Example 2: Set laser level (returns string in buffer)
    >>> cmd = encoder.encode_command(
    ...     code=0x2001,                     # LASER_LEVEL_SET
    ...     params=[
    ...         1,                           # laser index
    ...         0, 0, 0, 0, 0,
    ...         0x80000000                   # CALLBACK FLAG
    ...     ],
    ...     data=b"11.49"                    # power level as string
    ... )
    >>>
    >>> # Example 3: Set stage position (echoes back position in doubleData)
    >>> cmd = encoder.encode_command(
    ...     code=0x6005,                     # STAGE_POSITION_SET
    ...     params=[
    ...         1,                           # axis number
    ...         0, 0, 0, 0, 0,
    ...         0x80000000                   # CALLBACK FLAG
    ...     ],
    ...     value=7.635                      # position in mm
    ... )
"""

import struct
from typing import List, Optional, Dict, Any


class ProtocolEncoder:
    """
    Encodes commands into the Flamingo microscope binary protocol format.

    The protocol uses a fixed 128-byte structure with start and end markers
    to ensure data integrity.
    """

    # Protocol markers
    START_MARKER = 0xF321E654
    END_MARKER = 0xFEDC4321

    # Command structure format
    # Format: I=uint32 (4 bytes), d=double (8 bytes), 72s=72 bytes string
    COMMAND_STRUCT = struct.Struct("I I I I I I I I I I d I 72s I")

    # Expected size of encoded command
    COMMAND_SIZE = 128

    # Callback flag - REQUIRED for GET commands
    CALLBACK_FLAG = 0x80000000

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
            code: Command code (see command_codes.py)
            status: Status field (default: 0)
            params: List of 7 parameter integers:
                [0] int32Data0 / cmdBits0
                [1] int32Data1 / cmdBits1
                [2] int32Data2 / cmdBits2
                [3] hardwareID / cmdBits3
                [4] subsystemID / cmdBits4
                [5] clientID / cmdBits5
                [6] cmdDataBits0 / cmdBits6 (use CALLBACK_FLAG for GET commands)
            value: Double precision floating point value (default: 0.0)
            data: Data payload, max 72 bytes (default: empty)
            additional_data_size: Size of additional data to be sent after command

        Returns:
            128-byte command structure as bytes

        Raises:
            ValueError: If parameters are invalid

        Example:
            >>> encoder = ProtocolEncoder()
            >>> # Query stage position with callback
            >>> cmd = encoder.encode_command(
            ...     code=0x6008,
            ...     params=[1, 0, 0, 0, 0, 0, encoder.CALLBACK_FLAG]
            ... )
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

        # Pack command structure
        try:
            command_bytes = self.COMMAND_STRUCT.pack(
                self.START_MARKER,      # Start marker
                code,                   # Command code
                status,                 # Status
                params[0],              # int32Data0 / cmdBits0
                params[1],              # int32Data1 / cmdBits1
                params[2],              # int32Data2 / cmdBits2
                params[3],              # hardwareID / cmdBits3
                params[4],              # subsystemID / cmdBits4
                params[5],              # clientID / cmdBits5
                params[6],              # cmdDataBits0 / cmdBits6
                value,                  # doubleData
                additional_data_size,   # addDataBytes
                data,                   # buffer (72 bytes)
                self.END_MARKER         # End marker
            )
        except struct.error as e:
            raise ValueError(f"Failed to pack command structure: {e}")

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
                - int32Data0: Integer parameter 0
                - int32Data1: Integer parameter 1
                - int32Data2: Integer parameter 2
                - hardwareID: Hardware ID
                - subsystemID: Subsystem ID
                - clientID: Client ID
                - cmdDataBits0: Command data bits
                - doubleData: Double precision value
                - addDataBytes: Additional data size
                - buffer: Data payload (72 bytes, may contain null padding)
                - end_marker: End marker value
                - valid: Boolean indicating if markers are correct

        Raises:
            ValueError: If data is wrong size or cannot be unpacked

        Example:
            >>> decoder = ProtocolDecoder()
            >>> result = decoder.decode_command(response_bytes)
            >>> if result['valid']:
            ...     position = result['doubleData']
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
        int32Data0 = unpacked[3]
        int32Data1 = unpacked[4]
        int32Data2 = unpacked[5]
        hardwareID = unpacked[6]
        subsystemID = unpacked[7]
        clientID = unpacked[8]
        cmdDataBits0 = unpacked[9]
        doubleData = unpacked[10]
        addDataBytes = unpacked[11]
        buffer = unpacked[12]
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
            'int32Data0': int32Data0,
            'int32Data1': int32Data1,
            'int32Data2': int32Data2,
            'hardwareID': hardwareID,
            'subsystemID': subsystemID,
            'clientID': clientID,
            'cmdDataBits0': cmdDataBits0,
            'doubleData': doubleData,
            'addDataBytes': addDataBytes,
            'buffer': buffer,
            'end_marker': end_marker,
            'valid': valid
        }

    def extract_string_from_buffer(self, buffer: bytes) -> str:
        """
        Extract null-terminated string from buffer field.

        Args:
            buffer: 72-byte buffer from decoded command

        Returns:
            String with trailing nulls removed and decoded as UTF-8

        Example:
            >>> response = decoder.decode_command(data)
            >>> laser_power = decoder.extract_string_from_buffer(response['buffer'])
            >>> print(laser_power)  # "11.49"
        """
        # Find first null byte
        null_index = buffer.find(b'\x00')
        if null_index >= 0:
            buffer = buffer[:null_index]

        # Decode to string
        return buffer.decode('utf-8', errors='replace').strip()

    def extract_multi_axis_positions(self, buffer: bytes) -> Dict[int, float]:
        """
        Extract multi-axis positions from buffer field.

        The buffer contains positions in format: "1=X\\n2=Y\\n3=Z\\n"

        Args:
            buffer: 72-byte buffer from decoded command

        Returns:
            Dictionary mapping axis number to position value

        Example:
            >>> response = decoder.decode_command(data)
            >>> positions = decoder.extract_multi_axis_positions(response['buffer'])
            >>> print(positions)  # {1: 7.635, 2: 3.141, 3: 18.839}
        """
        positions = {}
        text = self.extract_string_from_buffer(buffer)

        for line in text.split('\n'):
            line = line.strip()
            if '=' in line:
                try:
                    axis_str, pos_str = line.split('=', 1)
                    axis = int(axis_str.strip())
                    position = float(pos_str.strip())
                    positions[axis] = position
                except (ValueError, IndexError):
                    # Skip malformed lines
                    continue

        return positions

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
