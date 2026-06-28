"""Loading a workflow.txt into the Workflow tab populates the panels.

The Workflow tab uses workflow.txt as its single format: ``parse_workflow_file``
turns a file into the section-keyed dict, ``infer_workflow_type`` recovers the
type, and ``WorkflowView.set_workflow_dict`` translates each section into the
panel's real input shape. This checks the round-trip on the repo's sample files
and the type inference.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TESTS_DIR = Path(__file__).resolve().parent
_ROOT = _TESTS_DIR.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from py2flamingo.utils.workflow_parser import (  # noqa: E402
    infer_workflow_type,
    parse_workflow_file,
)


class TestInferType(unittest.TestCase):
    def test_stack_option_mapping(self):
        self.assertEqual(
            infer_workflow_type({"Stack Settings": {"Stack option": "ZStack"}}),
            "zstack",
        )
        self.assertEqual(
            infer_workflow_type({"Stack Settings": {"Stack option": "Tile"}}), "tile"
        )
        self.assertEqual(
            infer_workflow_type({"Stack Settings": {"Stack option": "OPT"}}),
            "multi_angle",
        )

    def test_none_stack_is_snapshot_or_timelapse(self):
        self.assertEqual(
            infer_workflow_type({"Stack Settings": {"Stack option": "None"}}),
            "snapshot",
        )
        self.assertEqual(
            infer_workflow_type(
                {
                    "Stack Settings": {"Stack option": "None"},
                    "Experiment Settings": {"Duration (dd:hh:mm:ss)": "0:01:00:00"},
                }
            ),
            "time_lapse",
        )


class TestLoadIntoView(unittest.TestCase):
    """Parse a real sample file and apply it to a live WorkflowView."""

    @classmethod
    def setUpClass(cls):
        from unittest.mock import MagicMock

        from PyQt5.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])
        from py2flamingo.views.workflow_view import WorkflowView

        cls._WorkflowView = WorkflowView
        cls._MagicMock = MagicMock

    def _load(self, filename):
        path = _ROOT / "workflows" / filename
        if not path.exists():
            self.skipTest(f"sample workflow {filename} not present")
        view = self._WorkflowView(controller=self._MagicMock())
        parsed = parse_workflow_file(path)
        wtype = infer_workflow_type(parsed)
        view.set_workflow_dict(parsed, wtype)
        return parsed, wtype, view, view.get_workflow_dict()

    def test_zstack_sample_roundtrips_core_values(self):
        parsed, wtype, view, out = self._load("ZStack.txt")

        # Type recovered and applied.
        self.assertEqual(view.get_current_workflow_type(), wtype)

        # Positions are exact (float round-trip).
        self.assertAlmostEqual(
            float(out["Start Position"]["Z (mm)"]),
            float(parsed["Start Position"]["Z (mm)"]),
            places=4,
        )
        self.assertAlmostEqual(
            float(out["End Position"]["Z (mm)"]),
            float(parsed["End Position"]["Z (mm)"]),
            places=4,
        )

        # Exposure carries a thousands comma in the file ("9,002"); it must parse.
        exp_in = parsed["Experiment Settings"].get("Exposure time (us)", "")
        if exp_in:
            self.assertGreater(
                float(out["Experiment Settings"]["Exposure time (us)"]), 0
            )

        # AOI preserved.
        self.assertEqual(
            int(out["Camera Settings"]["AOI width"]),
            int(parsed["Camera Settings"]["AOI width"]),
        )

        # Plane count is recomputed from range/spacing for consistency, so it is
        # close to (not necessarily identical to) the stored value.
        self.assertAlmostEqual(
            int(out["Stack Settings"]["Number of planes"]),
            int(parsed["Stack Settings"]["Number of planes"]),
            delta=3,
        )

    def test_save_after_load_produces_text(self):
        from py2flamingo.utils.workflow_parser import dict_to_workflow_text

        _parsed, _wtype, _view, out = self._load("ZStack.txt")
        text = dict_to_workflow_text(out)
        self.assertIn("Experiment Settings", text)
        self.assertIn("Start Position", text)


if __name__ == "__main__":
    unittest.main()
