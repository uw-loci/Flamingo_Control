"""Stage moves stay inside the server's own soft limits as well as the json.

CTLSM1, 2026-11-21: princess_settings.json allowed y from 1 and z from 8; the
server's soft limits are x 1..15, y 5..25, z 11..25. The server applies no
software limit to a position command, so the client has to.
"""

import json
import tempfile
import unittest
from pathlib import Path

from py2flamingo.services import server_stage_limits
from py2flamingo.services.microscope_settings_service import MicroscopeSettingsService

CTLSM1_SERVER = {
    "Stage limits": {
        "Soft limit min x-axis": "1",
        "Soft limit max x-axis": "14.999998",
        "Soft limit min y-axis": "5",
        "Soft limit max y-axis": "25",
        "Soft limit min z-axis": "11",
        "Soft limit max z-axis": "25",
        "Soft limit min r-axis": "-720",
        "Soft limit max r-axis": "720",
        "Hard limit min z-axis": "8",
    }
}
CTLSM1_JSON = {
    "x": {"min": 1.0, "max": 10.0},
    "y": {"min": 1.0, "max": 21.0},
    "z": {"min": 8.0, "max": 19.0},
    "r": {"min": -720.0, "max": 720.0},
}


class TestServerStageLimits(unittest.TestCase):
    def setUp(self):
        server_stage_limits.clear()
        self.addCleanup(server_stage_limits.clear)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name) / "microscope_settings"
        folder.mkdir()
        (folder / "princess_settings.json").write_text(
            json.dumps({"microscope_name": "princess", "stage_limits": CTLSM1_JSON})
        )
        self.service = MicroscopeSettingsService("princess", Path(self.tmp.name))

    def test_soft_not_hard_limits_are_read(self):
        found = server_stage_limits.parse(CTLSM1_SERVER)
        self.assertEqual(found["z"], (11.0, 25.0))
        self.assertEqual(found["y"], (5.0, 25.0))

    def test_without_server_limits_the_json_applies(self):
        self.assertEqual(self.service.get_stage_limits()["z"], CTLSM1_JSON["z"])

    def test_the_narrower_bound_applies_on_each_axis(self):
        server_stage_limits.set_from_settings(CTLSM1_SERVER)
        limits = self.service.get_stage_limits()
        self.assertEqual(limits["x"], {"min": 1.0, "max": 10.0})  # json max
        self.assertEqual(limits["y"], {"min": 5.0, "max": 21.0})  # server min
        self.assertEqual(limits["z"], {"min": 11.0, "max": 19.0})
        # Microscope Setup edits the json as it is.
        self.assertEqual(self.service.get_configured_stage_limits()["z"]["min"], 8.0)

    def test_clear_on_disconnect_returns_to_the_json(self):
        server_stage_limits.set_from_settings(CTLSM1_SERVER)
        server_stage_limits.clear()
        self.assertEqual(self.service.get_stage_limits()["y"]["min"], 1.0)

    def test_limits_that_do_not_overlap_keep_the_json(self):
        server_stage_limits.set_from_settings(
            {
                "Stage limits": {
                    "Soft limit min x-axis": "20",
                    "Soft limit max x-axis": "30",
                }
            }
        )
        limits, notes = server_stage_limits.narrow(CTLSM1_JSON)
        self.assertEqual(limits["x"], CTLSM1_JSON["x"])
        self.assertTrue(any("do not overlap" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
