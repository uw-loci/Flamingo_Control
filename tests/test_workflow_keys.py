"""The workflow readers read what each server build writes.

The server renamed workflow keys on 2026-10-06 (WorkflowSettings.h): "Left path
recording = true" for "Left path = ON 1", "ROI width" for "AOI width",
"Exposure time (ms)" for the 3.0.0 "Exposure time (ns)" (whose value is ms),
and each illumination source's recording as true/false instead of 1/0. The
synthetic microscope writes each build's acquisition the way its server does;
the readers must give the same answers for both.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from py2flamingo.core import server_layout as sl
from py2flamingo.core.workflow_exposure import to_server_exposure
from py2flamingo.testing import synthetic_microscope as synth
from py2flamingo.utils import tile_workflow_parser as twp
from py2flamingo.utils.workflow_keys import (
    find_value,
    path_recording,
    to_app_keys,
    workflow_sections,
)
from py2flamingo.utils.workflow_parser import parse_workflow_file

REPO = Path(__file__).resolve().parents[1]


def _app_workflow() -> str:
    """ZStack.txt, left path only, lasers 3 and 4 on, as the app sends it."""
    text = (REPO / "workflows" / "ZStack.txt").read_text(encoding="utf-8")
    text = text.replace("Right path = ON 1", "Right path = OFF 0")
    text = text.replace("Exposure time (us) = 9,002", "Exposure time (us) = 28000")
    return text


class _Acquisition:
    """The folder a synthetic server of ``layout`` writes for the workflow."""

    def __init__(self, layout):
        self.tmp = tempfile.TemporaryDirectory()
        scope = synth.SyntheticMicroscope(
            command_port=0, live_port=0, layout=layout, data_root=self.tmp.name
        )
        sent = to_server_exposure(_app_workflow(), layout=layout)
        scope._write_acquisition(sent, 4)
        (self.folder,) = Path(self.tmp.name).iterdir()
        self.workflow = self.folder / "Workflow.txt"
        self.text = self.workflow.read_text(encoding="utf-8")


class ReadersAgreeAcrossBuilds(unittest.TestCase):
    layouts = (sl.REFERENCE, sl.LAYOUT_2026_11_13)

    def setUp(self):
        self.acq = {}
        for layout in self.layouts:
            acq = _Acquisition(layout)
            self.addCleanup(acq.tmp.cleanup)
            self.acq[layout.name] = acq

    def test_the_new_build_writes_the_new_keys(self):
        new = self.acq[sl.LAYOUT_2026_11_13.name].text
        self.assertIn("Left path recording = true", new)
        self.assertIn("ROI width", new)
        self.assertIn("Exposure time (ms) = 28", new)
        self.assertIn("561 nm MLE = 5.88 true", new)
        old = self.acq[sl.REFERENCE.name].text
        self.assertIn("Left path = ON 1", old)
        self.assertIn("Exposure time (ns) = 28", old)

    def test_illumination_path(self):
        for layout, acq in self.acq.items():
            with self.subTest(layout=layout):
                self.assertEqual(
                    twp.read_illumination_path_from_workflow(acq.workflow),
                    (True, False),
                )

    def test_laser_channels(self):
        for layout, acq in self.acq.items():
            with self.subTest(layout=layout):
                self.assertEqual(
                    twp.read_laser_channels_from_workflow(acq.workflow), [2, 3]
                )

    def test_the_workflow_tab_loader(self):
        for layout, acq in self.acq.items():
            with self.subTest(layout=layout):
                wf = parse_workflow_file(acq.workflow)
                self.assertEqual(
                    wf["Experiment Settings"]["Exposure time (us)"], "28000"
                )
                self.assertEqual(wf["Camera Settings"]["AOI width"], "2048")
                self.assertEqual(wf["Illumination Path"]["Left path"], "ON 1")
                self.assertEqual(wf["Illumination Path"]["Right path"], "OFF 0")
                self.assertEqual(
                    wf["Illumination Source"]["Laser 3 3: 561 nm MLE"], "5.88 1"
                )
                for section in wf.values():
                    self.assertNotIn("Exposure time (ms)", section)
                    self.assertNotIn("Exposure time (ns)", section)
                    self.assertNotIn("ROI width", section)
                    self.assertNotIn("Left path recording", section)

    def test_the_per_stack_sidecar_reads_the_same(self):
        for layout, acq in self.acq.items():
            with self.subTest(layout=layout):
                (sidecar,) = acq.folder.glob("*_Settings.txt")
                self.assertEqual(
                    twp.read_illumination_path_from_workflow(sidecar), (True, False)
                )

    def test_the_tiff_size_estimate_reads_the_frame(self):
        from py2flamingo.services.tiff_size_validator import parse_workflow_file as est

        for layout, acq in self.acq.items():
            with self.subTest(layout=layout):
                estimate = est(acq.workflow)
                self.assertEqual(estimate.num_planes, 476)
                self.assertEqual(estimate.image_width, 2048)


class AliasRules(unittest.TestCase):
    def test_path_values_read_as_the_server_reads_them(self):
        for value, on in [
            ("ON 1", True),
            ("OFF 0", False),
            (" 0", False),
            ("true", True),
            ("false", False),
            ("TRUE", True),
            ("", False),
        ]:
            self.assertEqual(path_recording(value), on, value)

    def test_the_apps_own_key_wins(self):
        out = to_app_keys(
            {
                "Camera Settings": {
                    "Exposure time (us)": "5000",
                    "Exposure time (ms)": "7",
                    "AOI width": "1024",
                    "ROI width": "2048",
                }
            }
        )
        self.assertEqual(
            out["Camera Settings"],
            {"Exposure time (us)": "5000", "AOI width": "1024"},
        )

    def test_blank_exposure_stays_blank(self):
        out = to_app_keys({"Camera Settings": {"Exposure time (ms)": ""}})
        self.assertEqual(out["Camera Settings"], {"Exposure time (us)": ""})

    def test_find_value_is_file_order_at_any_depth(self):
        tree = workflow_sections(
            "<A>\n  k = 1\n  <B>\n    k = 2\n  </B>\n</A>\n<C>\n  j = 3\n</C>\n"
        )
        self.assertEqual(find_value(tree, "k"), "1")
        self.assertEqual(find_value(tree, "j"), "3")
        self.assertIsNone(find_value(tree, "missing"))


if __name__ == "__main__":
    unittest.main()
