"""The frame-rate ceiling scales with the AOI, and a missing rate is ignored.

Two defects, both reaching the acquisition through
``zstack_panel: z_velocity = z_step_mm * frame_rate``:

* the ceiling was a hardcoded ``40.0``, which is the FULL-FRAME figure. A
  rolling-shutter sCMOS reads one row at a time, so a 1024-row AOI runs at
  roughly twice that. Capping at 40 while the camera ran at 80 halved the Z
  sweep velocity for every cropped-AOI acquisition.
* ``workflow_view`` passes ``_num(frame_rate, 0.0)`` and the ``"frame_rate"``
  key is ALWAYS present, so a workflow file omitting "Frame rate (f/s)" from
  both sections set the rate to 0. That floored z_velocity at 0.001 mm/s — a
  100x slower sweep from a silently absent field.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \\
        tests/test_camera_frame_rate.py -q
"""

import pytest
from PyQt5.QtWidgets import QApplication

from py2flamingo.views.workflow_panels.camera_panel import CameraPanel
from py2flamingo.views.workflow_panels.zstack_panel import ZStackPanel


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def panel(qapp):
    return CameraPanel()


class TestTheCeilingTracksTheAoi:
    def test_full_frame_is_the_configured_rate(self, panel):
        panel._aoi_height = 2048
        assert panel._max_frame_rate() == pytest.approx(40.0)

    def test_half_height_doubles_it(self, panel):
        """The reported case: 1024x1024 for laser-line collection reads 80 fps."""
        panel._aoi_height = 1024
        assert panel._max_frame_rate() == pytest.approx(80.0)

    def test_quarter_height_quadruples_it(self, panel):
        panel._aoi_height = 512
        assert panel._max_frame_rate() == pytest.approx(160.0)

    def test_a_nonsense_aoi_does_not_divide_by_zero(self, panel):
        panel._aoi_height = 0
        assert panel._max_frame_rate() > 0


class TestTheDerivedRateIsCapped:
    def test_a_short_exposure_is_capped_at_the_aoi_ceiling(self, panel):
        """5 ms is 200 fps uncapped; the camera cannot deliver that."""
        panel._aoi_height = 2048
        panel.set_settings({"exposure_us": 5000.0})
        assert panel.get_settings()["frame_rate"] == pytest.approx(40.0)

        panel._aoi_height = 1024
        panel.set_settings({"exposure_us": 5000.0})
        assert panel.get_settings()["frame_rate"] == pytest.approx(80.0)

    def test_a_long_exposure_is_not_raised_to_the_ceiling(self, panel):
        """100 ms is 10 fps; cropping the sensor does not make it faster."""
        panel._aoi_height = 512
        panel.set_settings({"exposure_us": 100_000.0})
        assert panel.get_settings()["frame_rate"] == pytest.approx(10.0)


class TestAMissingStoredRateIsIgnored:
    def test_zero_keeps_the_exposure_derived_rate(self, panel):
        panel.set_settings({"exposure_us": 10_000.0, "frame_rate": 0.0})
        assert panel.get_settings()["frame_rate"] == pytest.approx(40.0)

    def test_negative_is_ignored_too(self, panel):
        panel.set_settings({"exposure_us": 10_000.0, "frame_rate": -5.0})
        assert panel.get_settings()["frame_rate"] == pytest.approx(40.0)

    def test_garbage_is_ignored_without_raising(self, panel):
        panel.set_settings({"exposure_us": 10_000.0, "frame_rate": "fast"})
        assert panel.get_settings()["frame_rate"] == pytest.approx(40.0)

    def test_a_real_stored_rate_still_wins(self, panel):
        """A workflow file that DOES specify a rate must still be honoured."""
        panel.set_settings({"exposure_us": 10_000.0, "frame_rate": 80.0})
        assert panel.get_settings()["frame_rate"] == pytest.approx(80.0)


class TestTheConsequenceForZVelocity:
    """Why any of this matters: the rate sets the stage sweep speed."""

    def test_z_velocity_scales_with_the_frame_rate(self, qapp):
        z = ZStackPanel()
        z.set_frame_rate(40.0)
        slow = z.get_settings().z_velocity_mm_s
        z.set_frame_rate(80.0)
        fast = z.get_settings().z_velocity_mm_s

        assert fast == pytest.approx(2 * slow)

    def test_a_zeroed_rate_would_have_crawled(self, qapp):
        """Documents the hazard the guard above prevents reaching this code."""
        z = ZStackPanel()
        z.set_frame_rate(40.0)
        normal = z.get_settings().z_velocity_mm_s
        z.set_frame_rate(0.0)

        assert z.get_settings().z_velocity_mm_s < normal / 10
