"""Production code never imports the tests package.

The Tools menu's 3D Voxel Test and Volume Scan items imported
``tests.test_3d_*``. That package is not shipped with the app, so the items
failed when the app ran from ``src/``, and importing those modules replaced
``sys.excepthook``, undoing the crash handler that turns the lights off.
"""

import ast
import os
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "py2flamingo"


def _imports_of_tests(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        elif (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", getattr(node.func, "id", ""))
            in ("import_module", "__import__")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names = [node.args[0].value]
        else:
            continue
        for name in names:
            if name == "tests" or name.startswith("tests."):
                found.append(f"{path.relative_to(SRC.parent)}:{node.lineno} {name}")
    return found


class TestSrcDoesNotImportTests(unittest.TestCase):
    def test_no_module_under_src_imports_the_tests_package(self):
        offenders = []
        for path in sorted(SRC.rglob("*.py")):
            offenders.extend(_imports_of_tests(path))
        self.assertEqual(offenders, [], "production code imports tests/")


class TestRetiredToolsMenuItems(unittest.TestCase):
    """The two retired items stay disabled, also after a connection."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication

        from py2flamingo.main_window import MainWindow

        cls._qapp = QApplication.instance() or QApplication([])
        cls._win = MainWindow(connection_view=None, workflow_view=None)

    @classmethod
    def tearDownClass(cls):
        cls._win.deleteLater()
        cls._win = None

    def test_items_stay_disabled_when_connected(self):
        self._win.update_menu_states(connected=True)
        self.assertFalse(self._win.voxel_test_action.isEnabled())
        self.assertFalse(self._win.volume_scan_action.isEnabled())
        # A normal Tools item does follow the connection.
        self.assertTrue(self._win.calibrate_action.isEnabled())

    def test_items_say_why(self):
        for action in (self._win.voxel_test_action, self._win.volume_scan_action):
            self.assertIn("Not available", action.toolTip())


if __name__ == "__main__":
    unittest.main()
