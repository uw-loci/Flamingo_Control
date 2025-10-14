"""
Unit tests for TCP protocol encoding and decoding.

Tests the ProtocolEncoder and ProtocolDecoder classes to ensure correct
binary protocol formatting for Flamingo microscope communication.
"""

import unittest
import struct
from py2flamingo.core.tcp_protocol import (
    ProtocolEncoder,
    ProtocolDecoder,
    CommandCode
)


class TestCommandCode(unittest.TestCase):
    """Test CommandCode constants."""

    def test_command_codes_defined(self):
        """Test that all command codes are defined."""
        self.assertEqual(CommandCode.CMD_SCOPE_SETTINGS_LOAD, 4105)
        self.assertEqual(CommandCode.CMD_WORKFLOW_START, 12292)
        self.assertEqual(CommandCode.CMD_WORKFLOW_STOP, 12293)
        self.assertEqual(CommandCode.CMD_STAGE_POSITION_GET, 24584)
        self.assertEqual(CommandCode.CMD_STAGE_POSITION_SET, 24580)
        self.assertEqual(CommandCode.CMD_SYSTEM_STATE_GET, 40967)
        self.assertEqual(CommandCode.CMD_SYSTEM_STATE_IDLE, 40962)


class TestProtocolEncoder(unittest.TestCase):
    """Test ProtocolEncoder class."""

    def setUp(self):
        """Set up test fixtures."""
        self.encoder = ProtocolEncoder()

    def test_encode_command_creates_128_bytes(self):
        """Test that encoded command is exactly 128 bytes."""
        cmd = self.encoder.encode_command(CommandCode.CMD_WORKFLOW_START)
        self.assertEqual(len(cmd), 128)

    def test_encode_command_has_correct_start_marker(self):
        """Test that encoded command has correct start marker."""
        cmd = self.encoder.encode_command(CommandCode.CMD_WORKFLOW_STOP)
        start_marker = struct.unpack("I", cmd[:4])[0]
        self.assertEqual(start_marker, ProtocolEncoder.START_MARKER)
        self.assertEqual(start_marker, 0xF321E654)

    def test_encode_command_has_correct_end_marker(self):
        """Test that encoded command has correct end marker."""
        cmd = self.encoder.encode_command(CommandCode.CMD_WORKFLOW_STOP)
        end_marker = struct.unpack("I", cmd[-4:])[0]
        self.assertEqual(end_marker, ProtocolEncoder.END_MARKER)
        self.assertEqual(end_marker, 0xFEDC4321)

    def test_encode_command_with_code(self):
        """Test that command code is encoded correctly."""
        code = CommandCode.CMD_WORKFLOW_START
        cmd = self.encoder.encode_command(code)

        # Extract command code (bytes 4-8)
        encoded_code = struct.unpack("I", cmd[4:8])[0]
        self.assertEqual(encoded_code, code)

    def test_encode_command_with_status(self):
        """Test that status field is encoded correctly."""
        cmd = self.encoder.encode_command(
            CommandCode.CMD_WORKFLOW_STOP,
            status=42
        )

        # Extract status (bytes 8-12)
        status = struct.unpack("I", cmd[8:12])[0]
        self.assertEqual(status, 42)

    def test_encode_command_with_parameters(self):
        """Test that parameter fields are encoded correctly."""
        params = [1, 2, 3, 4, 5, 6, 7]
        cmd = self.encoder.encode_command(
            CommandCode.CMD_WORKFLOW_START,
            params=params
        )

        # Extract parameters (bytes 12-40, 7 x 4-byte uints)
        for i, expected in enumerate(params):
            offset = 12 + (i * 4)
            param = struct.unpack("I", cmd[offset:offset+4])[0]
            self.assertEqual(param, expected)

    def test_encode_command_with_short_params_pads_with_zeros(self):
        """Test that short parameter list is padded with zeros."""
        params = [1, 2, 3]  # Only 3 params, need 7
        cmd = self.encoder.encode_command(
            CommandCode.CMD_WORKFLOW_START,
            params=params
        )

        # Extract all 7 parameters
        for i in range(7):
            offset = 12 + (i * 4)
            param = struct.unpack("I", cmd[offset:offset+4])[0]
            if i < 3:
                self.assertEqual(param, params[i])
            else:
                self.assertEqual(param, 0)

    def test_encode_command_with_too_many_params_raises_error(self):
        """Test that too many parameters raises ValueError."""
        params = [1, 2, 3, 4, 5, 6, 7, 8, 9]  # Too many
        with self.assertRaises(ValueError) as ctx:
            self.encoder.encode_command(
                CommandCode.CMD_WORKFLOW_START,
                params=params
            )
        self.assertIn("Too many parameters", str(ctx.exception))

    def test_encode_command_with_value(self):
        """Test that value field is encoded correctly."""
        value = 3.14159
        cmd = self.encoder.encode_command(
            CommandCode.CMD_STAGE_POSITION_SET,
            value=value
        )

        # Extract value (bytes 40-48, double)
        encoded_value = struct.unpack("d", cmd[40:48])[0]
        self.assertAlmostEqual(encoded_value, value, places=5)

    def test_encode_command_with_integer_value(self):
        """Test that integer value is converted to float."""
        value = 42
        cmd = self.encoder.encode_command(
            CommandCode.CMD_STAGE_POSITION_SET,
            value=value
        )

        # Extract value (bytes 40-48, double)
        encoded_value = struct.unpack("d", cmd[40:48])[0]
        self.assertAlmostEqual(encoded_value, 42.0, places=5)

    def test_encode_command_with_data_string(self):
        """Test that data field is encoded correctly."""
        data = b"test data"
        cmd = self.encoder.encode_command(
            CommandCode.CMD_WORKFLOW_START,
            data=data
        )

        # Extract data (bytes 52-124, 72 bytes)
        encoded_data = cmd[52:124]
        self.assertEqual(len(encoded_data), 72)
        self.assertTrue(encoded_data.startswith(data))

    def test_encode_command_with_str_data(self):
        """Test that string data is converted to bytes."""
        data = "test string"
        cmd = self.encoder.encode_command(
            CommandCode.CMD_WORKFLOW_START,
            data=data.encode('utf-8')
        )

        # Extract data
        encoded_data = cmd[52:124]
        self.assertTrue(encoded_data.startswith(data.encode('utf-8')))

    def test_encode_command_data_truncated_if_too_long(self):
        """Test that data longer than 72 bytes is truncated."""
        data = b"x" * 100  # Too long
        cmd = self.encoder.encode_command(
            CommandCode.CMD_WORKFLOW_START,
            data=data
        )

        # Extract data
        encoded_data = cmd[52:124]
        self.assertEqual(len(encoded_data), 72)
        self.assertEqual(encoded_data, b"x" * 72)

    def test_encode_command_data_padded_if_short(self):
        """Test that short data is padded with null bytes."""
        data = b"short"
        cmd = self.encoder.encode_command(
            CommandCode.CMD_WORKFLOW_START,
            data=data
        )

        # Extract data
        encoded_data = cmd[52:124]
        self.assertEqual(len(encoded_data), 72)
        self.assertTrue(encoded_data.startswith(data))
        # Rest should be null bytes
        self.assertEqual(encoded_data[len(data):], b'\x00' * (72 - len(data)))

    def test_encode_command_with_invalid_code_raises_error(self):
        """Test that invalid command code raises ValueError."""
        with self.assertRaises(ValueError):
            self.encoder.encode_command(-1)

    def test_encode_command_with_invalid_status_raises_error(self):
        """Test that invalid status raises ValueError."""
        with self.assertRaises(ValueError):
            self.encoder.encode_command(CommandCode.CMD_WORKFLOW_START, status=-1)

    def test_encode_command_with_invalid_data_type_raises_error(self):
        """Test that invalid data type raises ValueError."""
        with self.assertRaises(ValueError):
            self.encoder.encode_command(
                CommandCode.CMD_WORKFLOW_START,
                data=12345  # Not bytes or str
            )

    def test_encode_command_default_values(self):
        """Test that default values work correctly."""
        cmd = self.encoder.encode_command(CommandCode.CMD_WORKFLOW_START)

        # Should have status=0
        status = struct.unpack("I", cmd[8:12])[0]
        self.assertEqual(status, 0)

        # Should have params=[0]*7
        for i in range(7):
            offset = 12 + (i * 4)
            param = struct.unpack("I", cmd[offset:offset+4])[0]
            self.assertEqual(param, 0)

        # Should have value=0.0
        value = struct.unpack("d", cmd[40:48])[0]
        self.assertAlmostEqual(value, 0.0)

        # Should have empty data (all zeros)
        data = cmd[52:124]
        self.assertEqual(data, b'\x00' * 72)


class TestProtocolDecoder(unittest.TestCase):
    """Test ProtocolDecoder class."""

    def setUp(self):
        """Set up test fixtures."""
        self.encoder = ProtocolEncoder()
        self.decoder = ProtocolDecoder()

    def test_decode_command_returns_dict(self):
        """Test that decode returns a dictionary."""
        cmd = self.encoder.encode_command(CommandCode.CMD_WORKFLOW_START)
        result = self.decoder.decode_command(cmd)
        self.assertIsInstance(result, dict)

    def test_decode_command_has_required_keys(self):
        """Test that decoded command has all required keys."""
        cmd = self.encoder.encode_command(CommandCode.CMD_WORKFLOW_START)
        result = self.decoder.decode_command(cmd)

        required_keys = [
            'start_marker', 'code', 'status', 'params',
            'value', 'reserved', 'data', 'end_marker', 'valid'
        ]
        for key in required_keys:
            self.assertIn(key, result)

    def test_decode_command_validates_markers(self):
        """Test that decoder validates start and end markers."""
        cmd = self.encoder.encode_command(CommandCode.CMD_WORKFLOW_START)
        result = self.decoder.decode_command(cmd)

        self.assertEqual(result['start_marker'], ProtocolDecoder.START_MARKER)
        self.assertEqual(result['end_marker'], ProtocolDecoder.END_MARKER)
        self.assertTrue(result['valid'])

    def test_decode_command_extracts_code(self):
        """Test that command code is extracted correctly."""
        code = CommandCode.CMD_WORKFLOW_STOP
        cmd = self.encoder.encode_command(code)
        result = self.decoder.decode_command(cmd)

        self.assertEqual(result['code'], code)

    def test_decode_command_extracts_status(self):
        """Test that status is extracted correctly."""
        cmd = self.encoder.encode_command(
            CommandCode.CMD_WORKFLOW_START,
            status=99
        )
        result = self.decoder.decode_command(cmd)

        self.assertEqual(result['status'], 99)

    def test_decode_command_extracts_params(self):
        """Test that parameters are extracted correctly."""
        params = [10, 20, 30, 40, 50, 60, 70]
        cmd = self.encoder.encode_command(
            CommandCode.CMD_WORKFLOW_START,
            params=params
        )
        result = self.decoder.decode_command(cmd)

        self.assertEqual(result['params'], params)

    def test_decode_command_extracts_value(self):
        """Test that value is extracted correctly."""
        value = 2.71828
        cmd = self.encoder.encode_command(
            CommandCode.CMD_STAGE_POSITION_SET,
            value=value
        )
        result = self.decoder.decode_command(cmd)

        self.assertAlmostEqual(result['value'], value, places=5)

    def test_decode_command_extracts_data(self):
        """Test that data field is extracted correctly."""
        data = b"test payload"
        cmd = self.encoder.encode_command(
            CommandCode.CMD_WORKFLOW_START,
            data=data
        )
        result = self.decoder.decode_command(cmd)

        # Data is 72 bytes, padded with nulls
        self.assertEqual(len(result['data']), 72)
        self.assertTrue(result['data'].startswith(data))

    def test_decode_command_with_invalid_size_raises_error(self):
        """Test that wrong size raises ValueError."""
        invalid_cmd = b"x" * 64  # Wrong size
        with self.assertRaises(ValueError) as ctx:
            self.decoder.decode_command(invalid_cmd)
        self.assertIn("Invalid command size", str(ctx.exception))

    def test_decode_command_with_invalid_start_marker(self):
        """Test that invalid start marker is detected."""
        cmd = self.encoder.encode_command(CommandCode.CMD_WORKFLOW_START)

        # Corrupt start marker
        corrupted = b'\x00\x00\x00\x00' + cmd[4:]

        result = self.decoder.decode_command(corrupted)
        self.assertFalse(result['valid'])

    def test_decode_command_with_invalid_end_marker(self):
        """Test that invalid end marker is detected."""
        cmd = self.encoder.encode_command(CommandCode.CMD_WORKFLOW_START)

        # Corrupt end marker
        corrupted = cmd[:-4] + b'\x00\x00\x00\x00'

        result = self.decoder.decode_command(corrupted)
        self.assertFalse(result['valid'])

    def test_decode_encode_roundtrip(self):
        """Test that encoding and then decoding preserves data."""
        code = CommandCode.CMD_WORKFLOW_START
        status = 42
        params = [1, 2, 3, 4, 5, 6, 7]
        value = 3.14159
        data = b"roundtrip test"

        cmd = self.encoder.encode_command(
            code=code,
            status=status,
            params=params,
            value=value,
            data=data
        )

        result = self.decoder.decode_command(cmd)

        self.assertTrue(result['valid'])
        self.assertEqual(result['code'], code)
        self.assertEqual(result['status'], status)
        self.assertEqual(result['params'], params)
        self.assertAlmostEqual(result['value'], value, places=5)
        self.assertTrue(result['data'].startswith(data))

    def test_validate_command_accepts_valid_command(self):
        """Test that validate_command accepts valid commands."""
        cmd = self.encoder.encode_command(CommandCode.CMD_WORKFLOW_START)
        valid, error = self.decoder.validate_command(cmd)

        self.assertTrue(valid)
        self.assertEqual(error, "")

    def test_validate_command_rejects_wrong_size(self):
        """Test that validate_command rejects wrong size."""
        invalid_cmd = b"x" * 64
        valid, error = self.decoder.validate_command(invalid_cmd)

        self.assertFalse(valid)
        self.assertIn("Wrong size", error)

    def test_validate_command_rejects_bad_start_marker(self):
        """Test that validate_command rejects bad start marker."""
        cmd = self.encoder.encode_command(CommandCode.CMD_WORKFLOW_START)
        corrupted = b'\x00\x00\x00\x00' + cmd[4:]

        valid, error = self.decoder.validate_command(corrupted)

        self.assertFalse(valid)
        self.assertIn("Invalid start marker", error)

    def test_validate_command_rejects_bad_end_marker(self):
        """Test that validate_command rejects bad end marker."""
        cmd = self.encoder.encode_command(CommandCode.CMD_WORKFLOW_START)
        corrupted = cmd[:-4] + b'\x00\x00\x00\x00'

        valid, error = self.decoder.validate_command(corrupted)

        self.assertFalse(valid)
        self.assertIn("Invalid end marker", error)


if __name__ == '__main__':
    unittest.main()
