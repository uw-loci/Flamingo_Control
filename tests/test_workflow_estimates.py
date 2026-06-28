"""Workflow Check-Stack estimates + illumination loading from a scope file.

Covers two bugs found loading a real rig Workflow.txt:
* ``_calculate_estimates`` ignored the tile count (data size ~N× too small) and
  treated "Change in Z axis (mm)" (the total range) as the per-plane step
  (inflating the Z travel ~100× -> absurd acquisition times).
* The illumination panel matched lasers by an exact key ("Laser 1 405 nm") that
  never equals the microscope's key ("Laser 1 1: 405 nm MLE"), so no laser was
  applied on load.
"""

from __future__ import annotations

import logging
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402

from py2flamingo.controllers.workflow_controller import WorkflowController  # noqa: E402


def _make_controller():
    wc = WorkflowController.__new__(WorkflowController)
    wc._logger = logging.getLogger("test")
    wc._timing_service = None
    return wc


class TestEstimates(unittest.TestCase):
    def _tile_wf(self):
        return {
            "Stack Settings": {
                "Number of planes": "476",
                "Change in Z axis (mm)": "1.19",
                "Z stage velocity (mm/s)": "0.4",
                "Stack option": "Tile",
                "Stack option settings 1": "5",
                "Stack option settings 2": "5",
            },
            "Camera Settings": {
                "AOI width": "1024",
                "AOI height": "1024",
                "Exposure time (us)": "",
            },
            "Experiment Settings": {"Exposure time (us)": "9,002"},
            "Illumination Source": {
                "Laser 1 1: 405 nm MLE": "0.00 0",
                "Laser 3 3: 561 nm MLE": "5.88 1",
                "Laser 4 4: 640 nm MLE": "8.01 1",
            },
        }

    def test_tile_count_and_channels_multiply_images(self):
        e = _make_controller()._calculate_estimates(self._tile_wf())
        # 476 planes * 2 channels * 25 tiles
        self.assertEqual(e["num_tiles"], 25)
        self.assertEqual(e["num_channels"], 2)
        self.assertEqual(e["total_images"], 476 * 2 * 25)

    def test_data_size_realistic(self):
        e = _make_controller()._calculate_estimates(self._tile_wf())
        # 1024*1024*2 bytes * 23800 images ~= 46.5 GB (was ~1.6 GB before).
        self.assertGreater(e["data_size_gb"], 40)
        self.assertLess(e["data_size_gb"], 55)

    def test_z_range_is_the_stored_range_not_planes_times_range(self):
        e = _make_controller()._calculate_estimates(self._tile_wf())
        self.assertAlmostEqual(e["z_range_um"], 1190.0, places=0)  # not 565,250
        self.assertAlmostEqual(e["z_step_um"], 2.5, places=2)

    def test_time_is_sane(self):
        e = _make_controller()._calculate_estimates(self._tile_wf())
        # Minutes, not the old ~9.5 hours.
        self.assertLess(e["acquisition_time"], 3600)

    def test_timepoints_multiply(self):
        wf = self._tile_wf()
        wf["Stack Settings"]["Stack option"] = "None"
        wf["Experiment Settings"]["Duration (dd:hh:mm:ss)"] = "00:00:10:00"
        wf["Experiment Settings"]["Interval (dd:hh:mm:ss)"] = "00:00:01:00"
        e = _make_controller()._calculate_estimates(wf)
        self.assertEqual(e["num_timepoints"], 11)  # 600s / 60s + 1


def test_lasers_matched_by_slot_number(qtbot):
    """Scope-format laser keys ("Laser 3 3: 561 nm MLE") apply by slot number."""
    from py2flamingo.views.workflow_panels import IlluminationPanel

    panel = IlluminationPanel()
    qtbot.addWidget(panel)
    panel.set_settings_from_workflow_dict(
        {
            "Laser 1 1: 405 nm MLE": "0.00 0",
            "Laser 3 3: 561 nm MLE": "5.88 1",
            "Laser 4 4: 640 nm MLE": "8.01 1",
        }
    )
    out = panel.get_workflow_illumination_dict()
    enabled = {k for k, v in out.items() if str(v).strip().endswith(" 1")}
    # The 561 and 640 lasers (slots 3 and 4) must be enabled; 405 (slot 1) off.
    assert any("561" in k for k in enabled), enabled
    assert any("640" in k for k in enabled), enabled
    assert not any("405" in k for k in enabled), enabled


if __name__ == "__main__":
    unittest.main()
