"""
Unit tests for utils layer parsers (metadata_parser and workflow_parser).

Tests both parsers with real project files and edge cases.
"""
import unittest
from pathlib import Path
from unittest.mock import patch, mock_open
import tempfile
import os

from py2flamingo.utils.metadata_parser import (
    parse_metadata_file,
    validate_metadata_file,
    extract_connection_info,
    _find_microscope_address
)
from py2flamingo.utils.workflow_parser import (
    parse_workflow_file,
    validate_workflow,
    get_workflow_preview,
    read_workflow_as_bytes,
    get_workflow_summary
)
from py2flamingo.models.connection import ConnectionConfig


class TestMetadataParser(unittest.TestCase):
    """Test metadata_parser module."""

    def setUp(self):
        """Set up test fixtures."""
        # Use actual test file from project
        self.test_metadata_path = Path("microscope_settings/FlamingoMetaData_test.txt")

    def test_parse_metadata_file_with_real_file(self):
        """Test parsing actual FlamingoMetaData_test.txt file."""
        if not self.test_metadata_path.exists():
            self.skipTest(f"Test file not found: {self.test_metadata_path}")

        config = parse_metadata_file(self.test_metadata_path)

        # Verify it returns a ConnectionConfig
        self.assertIsInstance(config, ConnectionConfig)

        # Verify expected values from test file (localhost:53717)
        self.assertEqual(config.ip_address, "127.0.0.1")
        self.assertEqual(config.port, 53717)
        self.assertEqual(config.live_port, 53718)  # port + 1

    def test_parse_metadata_file_nonexistent(self):
        """Test that parsing non-existent file raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError) as ctx:
            parse_metadata_file("nonexistent_file.txt")

        self.assertIn("not found", str(ctx.exception).lower())

    def test_parse_metadata_file_malformed(self):
        """Test that malformed file raises ValueError."""
        # Create temporary malformed file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("This is not a valid metadata file\n")
            f.write("No microscope address here\n")
            temp_path = f.name

        try:
            with self.assertRaises(ValueError) as ctx:
                parse_metadata_file(temp_path)

            self.assertIn("Microscope address", str(ctx.exception))
        finally:
            os.unlink(temp_path)

    def test_parse_metadata_file_invalid_ip(self):
        """Test that invalid IP address raises ValueError."""
        # Create temporary file with invalid IP
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("<Instrument>\n")
            f.write("Microscope address = 999.999.999.999 53717\n")
            f.write("</Instrument>\n")
            temp_path = f.name

        try:
            with self.assertRaises(ValueError) as ctx:
                parse_metadata_file(temp_path)

            # Should fail validation
            self.assertTrue(
                "Invalid" in str(ctx.exception) or "validation" in str(ctx.exception).lower()
            )
        finally:
            os.unlink(temp_path)

    def test_parse_metadata_file_invalid_port(self):
        """Test that invalid port raises ValueError."""
        # Create temporary file with invalid port
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("<Instrument>\n")
            f.write("Microscope address = 192.168.1.1 99999\n")  # Port > 65535
            f.write("</Instrument>\n")
            temp_path = f.name

        try:
            with self.assertRaises(ValueError) as ctx:
                parse_metadata_file(temp_path)

            self.assertIn("range", str(ctx.exception).lower())
        finally:
            os.unlink(temp_path)

    def test_extract_connection_info_valid(self):
        """Test extracting connection info from valid line."""
        ip, port = extract_connection_info("192.168.1.1 53717")

        self.assertEqual(ip, "192.168.1.1")
        self.assertEqual(port, 53717)

    def test_extract_connection_info_localhost(self):
        """Test extracting connection info for localhost."""
        ip, port = extract_connection_info("127.0.0.1 53717")

        self.assertEqual(ip, "127.0.0.1")
        self.assertEqual(port, 53717)

    def test_extract_connection_info_empty_line(self):
        """Test that empty line raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            extract_connection_info("")

        self.assertIn("empty", str(ctx.exception).lower())

    def test_extract_connection_info_invalid_format(self):
        """Test that invalid format raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            extract_connection_info("192.168.1.1")  # Missing port

        self.assertIn("format", str(ctx.exception).lower())

    def test_extract_connection_info_invalid_ip_format(self):
        """Test that invalid IP format raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            extract_connection_info("invalid.ip.address 53717")

        self.assertIn("IP address", str(ctx.exception))

    def test_extract_connection_info_invalid_port_format(self):
        """Test that non-numeric port raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            extract_connection_info("192.168.1.1 notaport")

        self.assertIn("port", str(ctx.exception).lower())

    def test_extract_connection_info_port_out_of_range(self):
        """Test that port out of range raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            extract_connection_info("192.168.1.1 0")  # Port 0

        self.assertIn("range", str(ctx.exception).lower())

        with self.assertRaises(ValueError) as ctx:
            extract_connection_info("192.168.1.1 70000")  # Port > 65535

        self.assertIn("range", str(ctx.exception).lower())

    def test_validate_metadata_file_valid(self):
        """Test validating a valid metadata file."""
        if not self.test_metadata_path.exists():
            self.skipTest(f"Test file not found: {self.test_metadata_path}")

        valid, errors = validate_metadata_file(self.test_metadata_path)

        self.assertTrue(valid)
        self.assertEqual(errors, [])

    def test_validate_metadata_file_nonexistent(self):
        """Test validating non-existent file."""
        valid, errors = validate_metadata_file("nonexistent.txt")

        self.assertFalse(valid)
        self.assertTrue(len(errors) > 0)
        self.assertIn("not found", errors[0].lower())

    def test_validate_metadata_file_malformed(self):
        """Test validating malformed file."""
        # Create temporary malformed file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("No valid content here\n")
            temp_path = f.name

        try:
            valid, errors = validate_metadata_file(temp_path)

            self.assertFalse(valid)
            self.assertTrue(len(errors) > 0)
        finally:
            os.unlink(temp_path)

    def test_find_microscope_address(self):
        """Test the internal _find_microscope_address function."""
        # Test nested structure
        data = {
            "Instrument": {
                "Type": {
                    "Microscope address": "192.168.1.1 53717"
                }
            }
        }

        result = _find_microscope_address(data)
        self.assertEqual(result, "192.168.1.1 53717")

    def test_find_microscope_address_not_found(self):
        """Test _find_microscope_address when key not present."""
        data = {
            "Instrument": {
                "Type": {
                    "Other field": "value"
                }
            }
        }

        result = _find_microscope_address(data)
        self.assertEqual(result, "")


class TestWorkflowParser(unittest.TestCase):
    """Test workflow_parser module."""

    def setUp(self):
        """Set up test fixtures."""
        # Use actual workflow files from project
        self.snapshot_path = Path("workflows/Snapshot.txt")
        self.zstack_path = Path("workflows/ZStack.txt")

    def test_parse_workflow_file_snapshot(self):
        """Test parsing actual Snapshot.txt workflow file."""
        if not self.snapshot_path.exists():
            self.skipTest(f"Test file not found: {self.snapshot_path}")

        workflow = parse_workflow_file(self.snapshot_path)

        # Verify it returns a dictionary
        self.assertIsInstance(workflow, dict)

        # Verify expected sections exist
        self.assertIn("Experiment Settings", workflow)
        self.assertIn("Stack Settings", workflow)
        self.assertIn("Start Position", workflow)

    def test_parse_workflow_file_zstack(self):
        """Test parsing actual ZStack.txt workflow file."""
        if not self.zstack_path.exists():
            self.skipTest(f"Test file not found: {self.zstack_path}")

        workflow = parse_workflow_file(self.zstack_path)

        # Verify it returns a dictionary
        self.assertIsInstance(workflow, dict)

        # Verify it has content
        self.assertTrue(len(workflow) > 0)

    def test_parse_workflow_file_nonexistent(self):
        """Test parsing non-existent file raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError) as ctx:
            parse_workflow_file("nonexistent_workflow.txt")

        self.assertIn("not found", str(ctx.exception).lower())

    def test_parse_workflow_file_empty(self):
        """Test parsing empty file raises ValueError."""
        # Create empty temporary file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            temp_path = f.name

        try:
            with self.assertRaises(ValueError) as ctx:
                parse_workflow_file(temp_path)

            self.assertIn("empty", str(ctx.exception).lower())
        finally:
            os.unlink(temp_path)

    def test_validate_workflow_valid(self):
        """Test validating a valid workflow dictionary."""
        if not self.snapshot_path.exists():
            self.skipTest(f"Test file not found: {self.snapshot_path}")

        workflow = parse_workflow_file(self.snapshot_path)
        valid, errors = validate_workflow(workflow)

        # Should be valid or have only minor warnings
        self.assertTrue(valid or len(errors) < 3)

    def test_validate_workflow_empty(self):
        """Test validating empty workflow dictionary."""
        valid, errors = validate_workflow({})

        self.assertFalse(valid)
        self.assertTrue(len(errors) > 0)
        self.assertIn("empty", errors[0].lower())

    def test_validate_workflow_missing_sections(self):
        """Test validating workflow with missing sections."""
        workflow = {
            "Experiment Settings": {}
            # Missing other required sections
        }

        valid, errors = validate_workflow(workflow)

        self.assertFalse(valid)
        self.assertTrue(any("Missing" in err for err in errors))

    def test_get_workflow_preview(self):
        """Test getting workflow preview."""
        if not self.snapshot_path.exists():
            self.skipTest(f"Test file not found: {self.snapshot_path}")

        preview = get_workflow_preview(self.snapshot_path, max_lines=5)

        # Verify we got a string
        self.assertIsInstance(preview, str)

        # Verify it has content
        self.assertTrue(len(preview) > 0)

        # Verify it starts with expected content
        self.assertIn("<Workflow Settings>", preview)

    def test_get_workflow_preview_with_truncation(self):
        """Test workflow preview with truncation."""
        if not self.snapshot_path.exists():
            self.skipTest(f"Test file not found: {self.snapshot_path}")

        preview = get_workflow_preview(self.snapshot_path, max_lines=3)

        # Should have truncation indicator
        self.assertIn("more lines", preview)

    def test_get_workflow_preview_nonexistent(self):
        """Test preview of non-existent file raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            get_workflow_preview("nonexistent.txt")

    def test_read_workflow_as_bytes(self):
        """Test reading workflow as bytes."""
        if not self.snapshot_path.exists():
            self.skipTest(f"Test file not found: {self.snapshot_path}")

        workflow_bytes = read_workflow_as_bytes(self.snapshot_path)

        # Verify we got bytes
        self.assertIsInstance(workflow_bytes, bytes)

        # Verify it has content
        self.assertTrue(len(workflow_bytes) > 0)

        # Verify it's valid UTF-8
        text = workflow_bytes.decode('utf-8')
        self.assertIn("<Workflow Settings>", text)

    def test_read_workflow_as_bytes_nonexistent(self):
        """Test reading non-existent file as bytes."""
        with self.assertRaises(FileNotFoundError):
            read_workflow_as_bytes("nonexistent.txt")

    def test_read_workflow_as_bytes_too_large(self):
        """Test reading excessively large file raises ValueError."""
        # Create a temporary file that's too large
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.txt') as f:
            # Write more than 10MB (10MB = 10 * 1024 * 1024 bytes)
            # Write in chunks to avoid memory issues
            chunk_size = 1024 * 1024  # 1MB
            for _ in range(11):  # 11MB total
                f.write(b'x' * chunk_size)
            temp_path = f.name

        try:
            with self.assertRaises(ValueError) as ctx:
                read_workflow_as_bytes(temp_path)

            self.assertIn("too large", str(ctx.exception).lower())
        finally:
            os.unlink(temp_path)

    def test_get_workflow_summary(self):
        """Test getting workflow summary."""
        if not self.snapshot_path.exists():
            self.skipTest(f"Test file not found: {self.snapshot_path}")

        workflow = parse_workflow_file(self.snapshot_path)
        summary = get_workflow_summary(workflow)

        # Verify we got a dictionary
        self.assertIsInstance(summary, dict)

        # Verify expected keys
        expected_keys = ['frame_rate', 'exposure_time', 'num_planes', 'start_x']
        for key in expected_keys:
            self.assertIn(key, summary)

    def test_get_workflow_summary_empty(self):
        """Test getting summary of empty workflow."""
        summary = get_workflow_summary({})

        # Should return empty or minimal dict
        self.assertIsInstance(summary, dict)


class TestParserIntegration(unittest.TestCase):
    """Integration tests for both parsers working together."""

    def test_metadata_then_workflow(self):
        """Test loading metadata and then workflow (typical usage pattern)."""
        metadata_path = Path("microscope_settings/FlamingoMetaData_test.txt")
        workflow_path = Path("workflows/Snapshot.txt")

        if not metadata_path.exists() or not workflow_path.exists():
            self.skipTest("Test files not found")

        # Parse metadata
        config = parse_metadata_file(metadata_path)
        self.assertIsInstance(config, ConnectionConfig)

        # Parse workflow
        workflow = parse_workflow_file(workflow_path)
        self.assertIsInstance(workflow, dict)

        # Verify both succeeded
        self.assertEqual(config.ip_address, "127.0.0.1")
        self.assertTrue(len(workflow) > 0)

    def test_validate_before_parse(self):
        """Test validating files before parsing (defensive pattern)."""
        metadata_path = Path("microscope_settings/FlamingoMetaData_test.txt")

        if not metadata_path.exists():
            self.skipTest("Test file not found")

        # Validate first
        valid, errors = validate_metadata_file(metadata_path)
        self.assertTrue(valid, f"Validation failed: {errors}")

        # Then parse
        config = parse_metadata_file(metadata_path)
        self.assertIsInstance(config, ConnectionConfig)


if __name__ == '__main__':
    unittest.main()
