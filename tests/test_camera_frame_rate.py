"""The acquisition frame rate does NOT scale with the AOI, and 0 is ignored.

Both defects reach the sample through
``zstack_panel: z_velocity = z_step_mm * frame_rate`` with a FIXED Z step. That
identity is the whole point: the acquisition frame rate *is* the stage speed.
Raising it does not collect the same stack faster, it drives the stage through
the sample faster.

* **The AOI must not raise it.** Between 2026-08-07 and 2026-08-09 the cap
  scaled with AOI rows, on the reasoning that a rolling-shutter sCMOS reads one
  row at a time so a 1024-row crop can run at ~2x. True of the sensor, wrong
  here: what the camera is *capable* of is not what the acquisition should
  *request*. Cropping to 1024 silently raised the requested rate to 80, doubled
  the Z sweep, and produced blurry stacks that cost real time to diagnose --
  nothing in the UI said the speed had changed. 40 fps is the standing
  configuration for motion and acquisition at every AOI.
* **A missing stored rate must not zero it.** ``workflow_view`` passes
  ``_num(frame_rate, 0.0)`` and the ``"frame_rate"`` key is ALWAYS present, so a
  workflow file omitting "Frame rate (f/s)" from both sections set the rate to
  0. That floored z_velocity at 0.001 mm/s -- a 100x slower sweep from a
  silently absent field.

The camera's true ceiling still exists as a capability figure
(``HardwareConfig.max_frame_rate_hz``); it just must not choose a rate.

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


class TestTheCapIsIndependentOfTheAoi:
    """Cropping the sensor must not change how fast the stage moves."""

    def test_full_frame_is_the_configured_rate(self, panel):
        panel._aoi_height = 2048
        assert panel._max_frame_rate() == pytest.approx(40.0)

    def test_half_height_does_not_double_it(self, panel):
        """The blur case: 1024x1024 used to read 80 fps and double the sweep."""
        panel._aoi_height = 1024
        assert panel._max_frame_rate() == pytest.approx(40.0)

    def test_quarter_height_does_not_quadruple_it(self, panel):
        panel._aoi_height = 512
        assert panel._max_frame_rate() == pytest.approx(40.0)

    def test_every_aoi_agrees(self, panel):
        rates = []
        for rows in (2048, 1024, 512, 256, 0):
            panel._aoi_height = rows
            rates.append(panel._max_frame_rate())
        assert len(set(rates)) == 1, f"AOI changed the acquisition cap: {rates}"
        assert rates[0] > 0


class TestTheDerivedRateIsCapped:
    def test_a_short_exposure_is_capped_at_40_whatever_the_aoi(self, panel):
        """5 ms is 200 fps uncapped. Cropping must not license 80."""
        for rows in (2048, 1024, 512):
            panel._aoi_height = rows
            panel.set_settings({"exposure_us": 5000.0})
            assert panel.get_settings()["frame_rate"] == pytest.approx(40.0), (
                f"AOI {rows} derived a rate above the acquisition cap; "
                f"z_velocity = z_step * frame_rate, so this is stage speed"
            )

    def test_a_long_exposure_is_not_raised_to_the_cap(self, panel):
        """100 ms is 10 fps; the cap bounds, it does not set."""
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

    def test_an_explicit_rate_still_wins(self, panel):
        """Deliberate is different from derived.

        The cap stops a cropped AOI *implying* 80. Someone who types 80 has
        chosen it, and a workflow file that records one must round-trip.
        """
        panel.set_settings({"exposure_us": 10_000.0, "frame_rate": 80.0})
        assert panel.get_settings()["frame_rate"] == pytest.approx(80.0)


class TestTheConsequenceForZVelocity:
    """Why any of this matters: the rate IS the stage sweep speed."""

    def test_z_velocity_scales_with_the_frame_rate(self, qapp):
        z = ZStackPanel()
        z.set_frame_rate(40.0)
        slow = z.get_settings().z_velocity_mm_s
        z.set_frame_rate(80.0)
        fast = z.get_settings().z_velocity_mm_s

        assert fast == pytest.approx(2 * slow), (
            "this coupling is the reason the cap must not track the AOI: "
            "doubling the rate doubles how fast the stage crosses the sample"
        )

    def test_a_zeroed_rate_would_have_crawled(self, qapp):
        """Documents the hazard the guard above prevents reaching this code."""
        z = ZStackPanel()
        z.set_frame_rate(40.0)
        normal = z.get_settings().z_velocity_mm_s
        z.set_frame_rate(0.0)

        assert z.get_settings().z_velocity_mm_s < normal / 10
