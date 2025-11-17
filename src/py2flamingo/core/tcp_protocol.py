"""
TCP Protocol encoding and decoding for Flamingo microscope communication.

This module handles the binary protocol format used to communicate with the
Flamingo microscope control system. The protocol uses a fixed 128-byte command
structure with start/end markers.

Protocol Structure (128 bytes total):
    - Start marker: 0xF321E654 (4 bytes, uint32)
    - Command code: (4 bytes, uint32)
    - Status: (4 bytes, uint32)
    - Hardware ID: (4 bytes, int32) - params[3]
    - Subsystem ID: (4 bytes, int32) - params[4]
    - Client ID: (4 bytes, int32) - params[5]
    - int32Data0: (4 bytes, int32) - params[0]
    - int32Data1: (4 bytes, int32) - params[1]
    - int32Data2: (4 bytes, int32) - params[2]
    - cmdDataBits0: (4 bytes, int32) - params[6]
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
    Command data bits flags for the cmdBits6 (params[6]) parameter field.

    These are bit flags that can be combined using bitwise OR (|) operations.
    From CommandCodes.h enum COMMAND_DATA_BITS.

    Usage Examples:
        # For query commands that need a response:
        params[6] = CommandDataBits.TRIGGER_CALL_BACK

        # For Z-stack workflow with max projection saved to disk:
        params[6] = (CommandDataBits.STAGE_ZSWEEP |
                     CommandDataBits.MAX_PROJECTION |
                     CommandDataBits.SAVE_TO_DISK)

        # For multi-position timelapse with buffered positions:
        params[6] = (CommandDataBits.STAGE_POSITIONS_IN_BUFFER |
                     CommandDataBits.SAVE_TO_DISK |
                     CommandDataBits.EXPERIMENT_TIME_REMAINING)
    """

    # === Response Control ===
    # Trigger callback/response from microscope (CRITICAL for query/GET commands)
    # Without this flag, query commands receive no response and timeout
    # USE FOR: All *_GET commands (STAGE_POSITION_GET, CAMERA_IMAGE_SIZE_GET, etc.)
    TRIGGER_CALL_BACK = 0x80000000

    # === Workflow/Experiment Flags ===
    # Request/indicate experiment time remaining information
    # USE FOR: Long-running workflows, timelapse experiments
    EXPERIMENT_TIME_REMAINING = 0x00000001

    # Stage positions are buffered for multi-position acquisition
    # USE FOR: Workflows with multiple XYZ positions (multi-well plates, tiles, etc.)
    STAGE_POSITIONS_IN_BUFFER = 0x00000002

    # Compute Maximum Intensity Projection from Z-stack
    # USE FOR: Z-stack workflows when you want MIP instead of full stack
    # NOTE: Old code used this extensively for sample finding
    MAX_PROJECTION = 0x00000004

    # Save acquired images to disk (vs. only sending to live view)
    # USE FOR: Actual experiments/acquisitions (not live preview)
    SAVE_TO_DISK = 0x00000008

    # Don't send stage position updates to client during movement
    # USE FOR: Rapid multi-position movements to reduce network traffic
    STAGE_NOT_UPDATE_CLIENT = 0x00000010

    # Indicates a Z-sweep/Z-stack operation
    # USE FOR: Workflows that acquire multiple Z planes
    # COMBINE WITH: MAX_PROJECTION for MIP, SAVE_TO_DISK for saving
    STAGE_ZSWEEP = 0x00000020


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

        # DEBUG: Log complete command details for debug commands
        import logging
        logger = logging.getLogger(__name__)

        # List of commands to log in detail (stage, camera, system, laser, LED)
        debug_commands = [
            24580, 24584,  # STAGE_POSITION_SET, STAGE_POSITION_GET
            12327, 12343,  # CAMERA_IMAGE_SIZE_GET, CAMERA_PIXEL_FIELD_OF_VIEW_GET
            40967,         # SYSTEM_STATE_GET
            8193, 8196, 8199,  # LASER_LEVEL_SET, LASER_ENABLE_PREVIEW, LASER_DISABLE_ALL
            16385, 16386, 16387,  # LED_SET, LED_PREVIEW_ENABLE, LED_PREVIEW_DISABLE
            28676, 28678,  # ILLUMINATION_LEFT_ENABLE, ILLUMINATION_RIGHT_ENABLE
        ]

        if code in debug_commands:
            logger.info(f"[TX] ========== SENDING COMMAND ==========")
            logger.info(f"[TX] Command Code: {code} (0x{code:04X})")
            logger.info(f"[TX] Status: {status}")
            logger.info(f"[TX] Parameters:")
            logger.info(f"[TX]   params[0] (int32Data0): {params[0]} (0x{params[0]:08X}) [bytes 24-27]")
            logger.info(f"[TX]   params[1] (int32Data1): {params[1]} (0x{params[1]:08X}) [bytes 28-31]")
            logger.info(f"[TX]   params[2] (int32Data2): {params[2]} (0x{params[2]:08X}) [bytes 32-35]")
            logger.info(f"[TX]   params[3] (hardwareID): {params[3]} (0x{params[3]:08X}) [bytes 12-15]")
            logger.info(f"[TX]   params[4] (subsystemID): {params[4]} (0x{params[4]:08X}) [bytes 16-19]")
            logger.info(f"[TX]   params[5] (clientID): {params[5]} (0x{params[5]:08X}) [bytes 20-23]")
            logger.info(f"[TX]   params[6] (cmdDataBits0): {params[6]} (0x{params[6]:08X}) [bytes 36-39]")
            logger.info(f"[TX] Value (double): {value}")
            logger.info(f"[TX] Additional Data Size: {additional_data_size}")
            # Show first 32 bytes of data field if non-zero
            data_preview = data[:32] if len(data) >= 32 else data
            data_str = ' '.join(f'{b:02X}' for b in data_preview)
            logger.info(f"[TX] Data (first 32 bytes): {data_str}")
            logger.info(f"[TX] Start Marker: 0x{self.START_MARKER:08X}")
            logger.info(f"[TX] End Marker: 0x{self.END_MARKER:08X}")

        # Pack command structure
        # CRITICAL: Field order must match C++ SCommand struct!
        # Order: start, code, status, hardwareID, subsystemID, clientID,
        #        int32Data0, int32Data1, int32Data2, cmdDataBits0, value, addDataBytes, buffer, end
        try:
            command_bytes = self.COMMAND_STRUCT.pack(
                self.START_MARKER,  # Start marker
                code,               # Command code
                status,             # Status
                params[3],          # hardwareID (was params[0])
                params[4],          # subsystemID (was params[1])
                params[5],          # clientID (was params[2])
                params[0],          # int32Data0 (was params[3]) - LASER INDEX GOES HERE!
                params[1],          # int32Data1 (was params[4])
                params[2],          # int32Data2 (was params[5])
                params[6],          # cmdDataBits0 (unchanged)
                value,              # value (double)
                additional_data_size,  # addDataBytes (size of additional file data)
                data,               # data (72 bytes)
                self.END_MARKER     # End marker
            )
        except struct.error as e:
            raise ValueError(f"Failed to pack command structure: {e}")

        # DEBUG: Log packed bytes verification for debug commands
        if code in debug_commands:
            logger.info(f"[TX] --- Packed Bytes Verification ---")
            logger.info(f"[TX] Bytes 0-3 (Start): 0x{struct.unpack('I', command_bytes[0:4])[0]:08X}")
            logger.info(f"[TX] Bytes 4-7 (Code): {struct.unpack('I', command_bytes[4:8])[0]} (0x{struct.unpack('I', command_bytes[4:8])[0]:04X})")
            logger.info(f"[TX] Bytes 8-11 (Status): {struct.unpack('I', command_bytes[8:12])[0]}")
            logger.info(f"[TX] Bytes 12-15 (hardwareID): {struct.unpack('I', command_bytes[12:16])[0]} (0x{struct.unpack('I', command_bytes[12:16])[0]:08X})")
            logger.info(f"[TX] Bytes 16-19 (subsystemID): {struct.unpack('I', command_bytes[16:20])[0]} (0x{struct.unpack('I', command_bytes[16:20])[0]:08X})")
            logger.info(f"[TX] Bytes 20-23 (clientID): {struct.unpack('I', command_bytes[20:24])[0]} (0x{struct.unpack('I', command_bytes[20:24])[0]:08X})")
            logger.info(f"[TX] Bytes 24-27 (int32Data0): {struct.unpack('I', command_bytes[24:28])[0]} (0x{struct.unpack('I', command_bytes[24:28])[0]:08X})")
            logger.info(f"[TX] Bytes 28-31 (int32Data1): {struct.unpack('I', command_bytes[28:32])[0]} (0x{struct.unpack('I', command_bytes[28:32])[0]:08X})")
            logger.info(f"[TX] Bytes 32-35 (int32Data2): {struct.unpack('I', command_bytes[32:36])[0]} (0x{struct.unpack('I', command_bytes[32:36])[0]:08X})")
            logger.info(f"[TX] Bytes 36-39 (cmdDataBits0): {struct.unpack('I', command_bytes[36:40])[0]} (0x{struct.unpack('I', command_bytes[36:40])[0]:08X})")
            logger.info(f"[TX] Bytes 40-47 (Value): {struct.unpack('d', command_bytes[40:48])[0]}")
            logger.info(f"[TX] Bytes 48-51 (addDataBytes): {struct.unpack('I', command_bytes[48:52])[0]}")
            logger.info(f"[TX] Bytes 124-127 (End): 0x{struct.unpack('I', command_bytes[124:128])[0]:08X}")
            logger.info(f"[TX] ======================================")

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

        # DEBUG: Log complete response details for debug commands
        import logging
        logger = logging.getLogger(__name__)

        # List of commands to log in detail (stage, camera, system, laser, LED)
        debug_commands = [
            24580, 24584,  # STAGE_POSITION_SET, STAGE_POSITION_GET
            12327, 12343,  # CAMERA_IMAGE_SIZE_GET, CAMERA_PIXEL_FIELD_OF_VIEW_GET
            40967, 40962,  # SYSTEM_STATE_GET, SYSTEM_STATE_IDLE
            24592,         # STAGE_MOTION_STOPPED
            8193, 8196, 8199,  # LASER_LEVEL_SET, LASER_ENABLE_PREVIEW, LASER_DISABLE_ALL
            16385, 16386, 16387,  # LED_SET, LED_PREVIEW_ENABLE, LED_PREVIEW_DISABLE
            28676, 28678,  # ILLUMINATION_LEFT_ENABLE, ILLUMINATION_RIGHT_ENABLE
        ]

        if code in debug_commands:
            logger.info(f"[RX] ========== RECEIVED RESPONSE ==========")
            logger.info(f"[RX] Command Code: {code} (0x{code:04X})")
            logger.info(f"[RX] Status: {status}")
            logger.info(f"[RX] Parameters (RAW from bytes, not yet re-mapped):")
            logger.info(f"[RX]   params[0] (bytes 12-15): {params[0]} (0x{params[0]:08X}) = hardwareID")
            logger.info(f"[RX]   params[1] (bytes 16-19): {params[1]} (0x{params[1]:08X}) = subsystemID")
            logger.info(f"[RX]   params[2] (bytes 20-23): {params[2]} (0x{params[2]:08X}) = clientID")
            logger.info(f"[RX]   params[3] (bytes 24-27): {params[3]} (0x{params[3]:08X}) = int32Data0")
            logger.info(f"[RX]   params[4] (bytes 28-31): {params[4]} (0x{params[4]:08X}) = int32Data1")
            logger.info(f"[RX]   params[5] (bytes 32-35): {params[5]} (0x{params[5]:08X}) = int32Data2")
            logger.info(f"[RX]   params[6] (bytes 36-39): {params[6]} (0x{params[6]:08X}) = cmdDataBits0")
            logger.info(f"[RX] Value (double): {value}")
            logger.info(f"[RX] Reserved Field (addDataBytes): {reserved}")
            # Show first 32 bytes of data field
            data_preview = data_field[:32] if isinstance(data_field, bytes) and len(data_field) >= 32 else data_field
            if isinstance(data_preview, bytes):
                data_str = ' '.join(f'{b:02X}' for b in data_preview)
                logger.info(f"[RX] Data (first 32 bytes): {data_str}")
            else:
                logger.info(f"[RX] Data: {data_preview}")
            logger.info(f"[RX] Start Marker: 0x{start_marker:08X} {'✓' if start_marker == self.START_MARKER else '✗ INVALID'}")
            logger.info(f"[RX] End Marker: 0x{end_marker:08X} {'✓' if end_marker == self.END_MARKER else '✗ INVALID'}")
            logger.info(f"[RX] Packet Valid: {valid}")

            # Log packed bytes verification
            logger.info(f"[RX] --- Packed Bytes Verification ---")
            logger.info(f"[RX] Bytes 0-3 (Start): 0x{struct.unpack('I', data[0:4])[0]:08X}")
            logger.info(f"[RX] Bytes 4-7 (Code): {struct.unpack('I', data[4:8])[0]} (0x{struct.unpack('I', data[4:8])[0]:04X})")
            logger.info(f"[RX] Bytes 8-11 (Status): {struct.unpack('I', data[8:12])[0]}")
            logger.info(f"[RX] Bytes 12-15 (hardwareID): {struct.unpack('I', data[12:16])[0]} (0x{struct.unpack('I', data[12:16])[0]:08X})")
            logger.info(f"[RX] Bytes 16-19 (subsystemID): {struct.unpack('I', data[16:20])[0]} (0x{struct.unpack('I', data[16:20])[0]:08X})")
            logger.info(f"[RX] Bytes 20-23 (clientID): {struct.unpack('I', data[20:24])[0]} (0x{struct.unpack('I', data[20:24])[0]:08X})")
            logger.info(f"[RX] Bytes 24-27 (int32Data0): {struct.unpack('I', data[24:28])[0]} (0x{struct.unpack('I', data[24:28])[0]:08X})")
            logger.info(f"[RX] Bytes 28-31 (int32Data1): {struct.unpack('I', data[28:32])[0]} (0x{struct.unpack('I', data[28:32])[0]:08X})")
            logger.info(f"[RX] Bytes 32-35 (int32Data2): {struct.unpack('I', data[32:36])[0]} (0x{struct.unpack('I', data[32:36])[0]:08X})")
            logger.info(f"[RX] Bytes 36-39 (cmdDataBits0): {struct.unpack('I', data[36:40])[0]} (0x{struct.unpack('I', data[36:40])[0]:08X})")
            logger.info(f"[RX] Bytes 40-47 (Value): {struct.unpack('d', data[40:48])[0]}")
            logger.info(f"[RX] Bytes 48-51 (addDataBytes): {struct.unpack('I', data[48:52])[0]}")
            logger.info(f"[RX] Bytes 124-127 (End): 0x{struct.unpack('I', data[124:128])[0]:08X}")
            logger.info(f"[RX] ==========================================")

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


# Helper functions for common response patterns
def extract_string_from_buffer(buffer: bytes) -> str:
    """
    Extract null-terminated string from buffer field.

    Args:
        buffer: 72-byte buffer from decoded command

    Returns:
        String with trailing nulls removed and decoded as UTF-8

    Example:
        >>> response = decoder.decode_command(data)
        >>> laser_power = extract_string_from_buffer(response['data'])
        >>> print(laser_power)  # "11.49"
    """
    # Find first null byte
    null_index = buffer.find(b'\x00')
    if null_index >= 0:
        buffer = buffer[:null_index]

    # Decode to string
    return buffer.decode('utf-8', errors='replace').strip()


def extract_position_from_double(response: Dict[str, Any]) -> float:
    """
    Extract stage position from doubleData field.

    Stage position commands (POSITION_SET, POSITION_GET) return the
    position value in the doubleData field.

    Args:
        response: Decoded command dictionary

    Returns:
        Position value in millimeters

    Example:
        >>> response = decoder.decode_command(data)
        >>> position = extract_position_from_double(response)
        >>> print(f"Stage at {position} mm")
    """
    return response['value']


def extract_multi_axis_positions(buffer: bytes) -> Dict[int, float]:
    """
    Extract multi-axis positions from buffer field.

    Some commands return positions for all axes in format:
    "1=X\\n2=Y\\n3=Z\\n"

    Args:
        buffer: 72-byte buffer from decoded command

    Returns:
        Dictionary mapping axis number (1=X, 2=Y, 3=Z) to position value

    Example:
        >>> response = decoder.decode_command(data)
        >>> positions = extract_multi_axis_positions(response['data'])
        >>> print(f"X={positions[1]}, Y={positions[2]}, Z={positions[3]}")
    """
    positions = {}
    text = extract_string_from_buffer(buffer)

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


def extract_system_state(response: Dict[str, Any]) -> int:
    """
    Extract system state from int32Data0 field.

    System state values:
        0 = IDLE (ready for commands)
        1 = BUSY (processing)
        2 = ERROR (error condition)

    Args:
        response: Decoded command dictionary

    Returns:
        System state value (0=IDLE, 1=BUSY, 2=ERROR)

    Example:
        >>> response = decoder.decode_command(data)
        >>> state = extract_system_state(response)
        >>> if state == 0:
        ...     print("System is IDLE")
        >>> elif state == 1:
        ...     print("System is BUSY")
        >>> elif state == 2:
        ...     print("System has ERROR")
    """
    return response['params'][0]  # int32Data0


def extract_camera_dimensions(response: Dict[str, Any]) -> tuple[int, int, int]:
    """
    Extract camera image dimensions from int32Data fields.

    Camera dimension commands return:
        int32Data0 = image width (pixels)
        int32Data1 = image height (pixels)
        int32Data2 = binning factor

    Args:
        response: Decoded command dictionary

    Returns:
        Tuple of (width, height, binning)

    Example:
        >>> response = decoder.decode_command(data)
        >>> width, height, binning = extract_camera_dimensions(response)
        >>> print(f"Camera: {width}x{height}, binning={binning}")
    """
    params = response['params']
    width = params[0]   # int32Data0
    height = params[1]  # int32Data1
    binning = params[2] # int32Data2
    return width, height, binning


def extract_laser_power(response: Dict[str, Any]) -> float:
    """
    Extract laser power level from buffer field.

    Laser level commands return the power as a string in the buffer
    field (e.g., "11.49" for 11.49% power).

    Args:
        response: Decoded command dictionary

    Returns:
        Laser power level as percentage (0.0-100.0)

    Example:
        >>> response = decoder.decode_command(data)
        >>> power = extract_laser_power(response)
        >>> print(f"Laser at {power}% power")
    """
    power_str = extract_string_from_buffer(response['data'])
    try:
        return float(power_str)
    except ValueError:
        return 0.0


def is_callback_response(response: Dict[str, Any]) -> bool:
    """
    Check if response has the callback flag set.

    The callback flag (0x80000000 in cmdDataBits0) indicates this is
    a response to a command that requested a callback.

    Args:
        response: Decoded command dictionary

    Returns:
        True if callback flag is set, False otherwise

    Example:
        >>> response = decoder.decode_command(data)
        >>> if is_callback_response(response):
        ...     print("This is a callback response")
    """
    cmdDataBits0 = response['params'][6]  # cmdDataBits0 is params[6]
    CALLBACK_FLAG = 0x80000000
    return (cmdDataBits0 & CALLBACK_FLAG) != 0
