"""Workflow text files: parse (parse_workflow_file).

The TCP/protocol tests that used to live here went with the legacy
ConnectionService and TCPClient; the live protocol is tested in
test_tcp_protocol.py and against the synthetic
microscope.
"""

import os
import tempfile
import unittest


class TestWorkflowParsing(unittest.TestCase):
    """Test workflow file parsing and generation."""

    def test_parse_workflow_file_with_unclosed_sections(self):
        """Sections a file leaves open still come out one per name.

        (The "<Work Flow Type>" line followed by a bare value is not a format
        any server writes; it is read as an empty section.)"""
        workflow_text = """<Workflow Settings>
<Work Flow Type>
Stack
<Start Position>
X (mm) = 10.0
Y (mm) = 20.0
Z (mm) = 5.0
Angle (degrees) = 0.0
<End Position>
X (mm) = 10.0
Y (mm) = 20.0
Z (mm) = 15.0
Angle (degrees) = 0.0
<Stack Settings>
Number of planes = 100
Change in Z axis (mm) = 0.1
<Experiment Settings>
Save image data = Tiff
Comments = Test workflow
</Workflow Settings>"""

        # Write to temp file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(workflow_text)
            temp_file = f.name

        try:
            # Import and test
            from py2flamingo.utils.workflow_parser import parse_workflow_file

            result = parse_workflow_file(temp_file)

            # Verify structure
            self.assertEqual(result["Work Flow Type"], {})
            self.assertEqual(float(result["Start Position"]["X (mm)"]), 10.0)
            self.assertEqual(float(result["End Position"]["Z (mm)"]), 15.0)
            self.assertEqual(int(result["Stack Settings"]["Number of planes"]), 100)
            self.assertEqual(result["Experiment Settings"]["Save image data"], "Tiff")

        finally:
            os.unlink(temp_file)


if __name__ == "__main__":
    unittest.main()
