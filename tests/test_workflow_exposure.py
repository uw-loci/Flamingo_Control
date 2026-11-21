"""The workflow exposure key is rewritten into the form the server reads.

The app keeps "Exposure time (us)" in microseconds; 3.0.0 reads only
"Exposure time (ns)" and later builds "(ms)", both in milliseconds. The
CTLSM1 verification run of 2026-11-21 asked 28 ms and the camera stayed at
25 ms because no server reads "(us)".
"""

import unittest

from py2flamingo.core import server_layout as sl
from py2flamingo.core.workflow_exposure import (
    MS_KEY,
    OLD_KEY,
    server_exposure_key,
    to_server_exposure,
    us_to_ms_text,
)

WORKFLOW = (
    "<Workflow Settings>\n"
    "  <Experiment Settings>\n"
    "    Plane spacing (um) = 2.5\n"
    "    Frame rate (f/s) = 40.0\n"
    "    Exposure time (us) = 24998\n"
    "  </Experiment Settings>\n"
    "  <Camera Settings>\n"
    "    Exposure time (us) = \n"
    "  </Camera Settings>\n"
    "</Workflow Settings>\n"
)


class TestExposureKey(unittest.TestCase):
    def test_each_build_gets_the_key_it_reads(self):
        self.assertEqual(server_exposure_key(sl.REFERENCE), OLD_KEY)
        self.assertEqual(server_exposure_key(sl.LAYOUT_2026_10_06), MS_KEY)
        self.assertEqual(server_exposure_key(sl.LAYOUT_2026_11_13), MS_KEY)

    def test_microseconds_become_whole_milliseconds(self):
        self.assertEqual(us_to_ms_text("24998"), "25")
        self.assertEqual(us_to_ms_text("9,002"), "9")
        self.assertEqual(us_to_ms_text("28000.0"), "28")
        self.assertEqual(us_to_ms_text("400"), "1")  # never rounded to 0
        self.assertEqual(us_to_ms_text(""), "")

    def test_the_text_is_rewritten_for_the_11_13_build(self):
        out = to_server_exposure(WORKFLOW.encode(), sl.LAYOUT_2026_11_13).decode()
        self.assertIn("    Exposure time (ms) = 25\n", out)
        self.assertIn("    Exposure time (ms) = \n", out)
        self.assertNotIn("(us)", out)
        # Everything else is untouched.
        self.assertIn("    Plane spacing (um) = 2.5\n", out)
        self.assertEqual(out.count("\n"), WORKFLOW.count("\n"))

    def test_the_text_is_rewritten_for_3_0_0(self):
        out = to_server_exposure(WORKFLOW, sl.REFERENCE)
        self.assertIn("    Exposure time (ns) = 25\n", out)
        self.assertNotIn("(us)", out)

    def test_text_already_in_a_server_key_is_left_alone(self):
        text = WORKFLOW.replace("(us) = 24998", "(ms) = 30").replace(
            "(us) = \n", "(ms) = \n"
        )
        self.assertEqual(to_server_exposure(text, sl.LAYOUT_2026_11_13), text)

    def test_crlf_line_endings_survive(self):
        text = WORKFLOW.replace("\n", "\r\n").encode()
        out = to_server_exposure(text, sl.LAYOUT_2026_11_13)
        self.assertIn(b"Exposure time (ms) = 25\r\n", out)


if __name__ == "__main__":
    unittest.main()
