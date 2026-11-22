"""
Unit tests for utils/workflow_parser.

Tests the parser with real project files and edge cases.
"""

import os
import tempfile
import unittest
from pathlib import Path

from py2flamingo.utils.workflow_parser import (
    get_workflow_preview,
    get_workflow_summary,
    parse_workflow_file,
    read_workflow_as_bytes,
    validate_workflow,
)


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
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
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
        text = workflow_bytes.decode("utf-8")
        self.assertIn("<Workflow Settings>", text)

    def test_read_workflow_as_bytes_nonexistent(self):
        """Test reading non-existent file as bytes."""
        with self.assertRaises(FileNotFoundError):
            read_workflow_as_bytes("nonexistent.txt")

    def test_read_workflow_as_bytes_too_large(self):
        """Test reading excessively large file raises ValueError."""
        # Create a temporary file that's too large
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".txt") as f:
            # Write more than 10MB (10MB = 10 * 1024 * 1024 bytes)
            # Write in chunks to avoid memory issues
            chunk_size = 1024 * 1024  # 1MB
            for _ in range(11):  # 11MB total
                f.write(b"x" * chunk_size)
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
        expected_keys = ["frame_rate", "exposure_time", "num_planes", "start_x"]
        for key in expected_keys:
            self.assertIn(key, summary)

    def test_get_workflow_summary_empty(self):
        """Test getting summary of empty workflow."""
        summary = get_workflow_summary({})

        # Should return empty or minimal dict
        self.assertIsInstance(summary, dict)


if __name__ == "__main__":
    unittest.main()
