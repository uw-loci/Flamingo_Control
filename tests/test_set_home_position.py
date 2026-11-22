"""Set Home Position writes the four Home lines and reports success.

Until 2026-11-22 it called ``dict_to_text(settings, path)``; that function
(since removed as unused) took one argument and returned text, so every attempt raised and the Stage
Control view always showed "Failed to set home position".
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from py2flamingo.models.microscope import Position

SETTINGS = (
    "# Rendered by Flamingo_Control from the server's system metadata document\r\n"
    "<Type>\r\n"
    "  Microscope name = n7\r\n"
    "</Type>\r\n"
    "<Stage limits>\r\n"
    "  Soft limit min x-axis = 1.0\r\n"
    "  Home x-axis = 5.0\r\n"
    "  Home y-axis = 6.0\r\n"
    "</Stage limits>\r\n"
)


class TestSetHomePosition(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, cwd)
        Path("microscope_settings").mkdir()
        self.path = Path("microscope_settings") / "ScopeSettings.txt"
        self.path.write_bytes(SETTINGS.encode())

    def _controller(self):
        from py2flamingo.controllers.position_controller import PositionController

        pc = PositionController(MagicMock(), config_service=MagicMock())
        pc.is_position_within_bounds = lambda pos: (True, [])
        return pc

    def test_writes_file_and_returns_true(self):
        pc = self._controller()
        ok = pc.set_home_position(Position(x=7.25, y=8.5, z=9.125, r=45.0))
        self.assertIs(ok, True)

        home = pc.get_home_position()
        self.assertAlmostEqual(home.x, 7.25)
        self.assertAlmostEqual(home.y, 8.5)
        self.assertAlmostEqual(home.z, 9.125)
        self.assertAlmostEqual(home.r, 45.0)

    def test_rest_of_file_is_kept(self):
        self._controller().set_home_position(Position(x=7.0, y=8.0, z=9.0, r=0.0))
        text = self.path.read_bytes().decode()
        self.assertTrue(text.startswith("# Rendered by Flamingo_Control"))
        self.assertIn("  Microscope name = n7\r\n", text)
        self.assertIn("  Soft limit min x-axis = 1.0\r\n", text)
        self.assertIn("  Home x-axis = 7.000000\r\n", text)
        # Z and R were missing and are added inside the section.
        stage = text.split("<Stage limits>")[1].split("</Stage limits>")[0]
        self.assertIn("Home z-axis = 9.000000", stage)
        self.assertIn("Home r-axis = 0.00", stage)
        self.assertEqual(text.count("Home x-axis"), 1)

    def test_out_of_bounds_is_refused(self):
        pc = self._controller()
        pc.is_position_within_bounds = lambda pos: (False, ["X out of range"])
        with self.assertRaises(ValueError):
            pc.set_home_position(Position(x=99.0, y=0.0, z=0.0, r=0.0))
        self.assertEqual(self.path.read_bytes().decode(), SETTINGS)


if __name__ == "__main__":
    unittest.main()
