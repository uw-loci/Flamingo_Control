"""The rolling shutter, the light sheet and the stage sweep are one system.

This package has no ASLM sheet control (confirmed 2026-08-31), and the
ScopeControl source that would show how the sheet is driven was not in the
version drop. What can be derived without it is the camera timing, which is what
couples every knob to every other one.

The model rests on one measured number. This project records 40 fps at 2048 rows
and 80 fps at 1024 rows; both give 12.207 us/row. The first test asserts that
derivation rather than the constant, so if someone changes the recorded operating
points the constant has to move with them.

Run: .venv/bin/python -m pytest tests/test_aslm_timing_model.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.models.aslm_timing import (  # noqa: E402
    DEFAULT_LINE_TIME_US,
    MIN_USEFUL_SECTIONING,
    SheetSyncStatus,
    evaluate,
)

# The shipped workflow template, verbatim from WORKFLOW_SETTINGS_OPTIONS.txt.
TEMPLATE_EXPOSURE_US = 24998
TEMPLATE_ROWS = 2048


def _run(**kw):
    base = dict(
        rows=TEMPLATE_ROWS,
        exposure_us=TEMPLATE_EXPOSURE_US,
        plane_spacing_um=10.0,
        z_range_mm=1.0,
        pixel_size_um=1.0475,
    )
    base.update(kw)
    return evaluate(**base)


class TestTheLineTimeIsMeasuredNotAssumed:
    def test_both_recorded_operating_points_give_the_same_row_period(self):
        # 40 fps at 2048 rows and 80 fps at 1024 rows are the two points this
        # project already records. Two independent measurements agreeing to
        # three decimals is a row period, not a coincidence.
        from_2048 = (1e6 / 40.0) / 2048
        from_1024 = (1e6 / 80.0) / 1024
        assert from_2048 == pytest.approx(from_1024, rel=1e-9)
        assert DEFAULT_LINE_TIME_US == pytest.approx(from_2048, rel=1e-9)

    def test_it_explains_the_aoi_frame_rate_ceiling(self):
        # The ceiling scaling with AOI height is not a separate fact to remember;
        # it falls out of reading one row at a time.
        full = _run(rows=2048, exposure_us=1.0)
        half = _run(rows=1024, exposure_us=1.0)
        assert half.camera.frame_rate_hz == pytest.approx(
            2 * full.camera.frame_rate_hz, rel=1e-6
        )


class TestSlitWidthIsExposure:
    def test_the_slit_is_exposure_divided_by_line_time(self):
        r = _run(exposure_us=1000.0)
        assert r.camera.slit_rows == pytest.approx(1000.0 / DEFAULT_LINE_TIME_US)

    def test_the_shipped_template_has_no_useful_slit(self):
        # 24998 us at 2048 rows exposes the whole sensor at once. Whatever the
        # sheet does, this acquisition gets no sectioning from the shutter.
        r = _run()
        assert not r.camera.is_slit
        assert r.camera.sectioning_factor == pytest.approx(1.0, abs=0.01)

    def test_a_slit_covering_almost_every_row_is_not_called_a_slit(self):
        # The trap this guards: slit_rows < rows is true at 2047/2048 and means
        # nothing. Usefulness, not the strict inequality, is the test.
        r = _run(exposure_us=TEMPLATE_EXPOSURE_US * 0.99)
        assert r.camera.slit_rows < r.camera.rows
        assert not r.camera.is_slit

    def test_halving_the_exposure_halves_the_slit(self):
        wide = _run(exposure_us=2000.0)
        narrow = _run(exposure_us=1000.0)
        assert narrow.camera.slit_rows == pytest.approx(wide.camera.slit_rows / 2)

    def test_the_slit_is_reported_in_sample_micrometres(self):
        # A row count cannot be compared to a depth of field; micrometres can.
        r = _run(exposure_us=1000.0, pixel_size_um=1.0475)
        assert r.sheet.slit_um == pytest.approx(r.camera.slit_rows * 1.0475)

    def test_sectioning_gain_is_the_inverse_of_the_light_kept(self):
        # One number read two ways: 25x thinner is 1/25th the photons.
        r = _run(exposure_us=1000.0)
        assert r.camera.duty_cycle == pytest.approx(
            1.0 / r.camera.sectioning_factor, rel=1e-6
        )


class TestFrameRateIsNotOneOverExposure:
    def test_below_readout_the_frame_rate_stops_depending_on_exposure(self):
        # The whole ASLM regime lives here, and it is where the client's
        # existing min(1/exposure, ceiling) would report 3000 fps.
        rates = {
            _run(exposure_us=e).camera.frame_rate_hz for e in (100, 300, 1000, 5000)
        }
        assert len(rates) == 1
        assert rates.pop() == pytest.approx(40.0, rel=1e-3)

    def test_above_readout_the_exposure_does_set_the_frame_rate(self):
        r = _run(exposure_us=50_000.0)
        assert r.camera.frame_rate_hz == pytest.approx(20.0, rel=1e-3)

    def test_exposure_and_readout_overlap_rather_than_adding(self):
        # A rolling shutter integrates while it reads. Summing them would
        # understate the frame rate by up to 2x.
        r = _run(exposure_us=25_000.0)
        summed = r.camera.exposure_us + r.camera.readout_us
        assert r.camera.frame_period_us == pytest.approx(25_000.0)
        assert r.camera.frame_period_us < summed


class TestFrameRateIsStageSpeed:
    def test_z_velocity_follows_the_frame_rate(self):
        r = _run(plane_spacing_um=10.0)
        assert r.z_velocity_mm_s == pytest.approx(0.010 * r.camera.frame_rate_hz)

    def test_the_2026_08_07_blur_is_reproduced_by_the_model(self):
        # Cropping to 1024 doubles what the camera CAN do. Taking that as the
        # acquisition rate doubles the sweep and blurs every stack.
        full = _run(rows=2048, exposure_us=1.0)
        cropped = _run(rows=1024, exposure_us=1.0)
        assert cropped.z_velocity_mm_s == pytest.approx(2 * full.z_velocity_mm_s)

    def test_capability_above_the_configured_rate_is_flagged_not_adopted(self):
        r = _run(rows=1024, exposure_us=1.0, configured_frame_rate_hz=40.0)
        assert any("stage speed" in w for w in r.warnings)

    def test_a_stage_that_cannot_keep_up_is_flagged(self):
        r = _run(exposure_us=1.0, plane_spacing_um=50.0, max_z_velocity_mm_s=1.0)
        assert any("cannot keep up" in w for w in r.warnings)


class TestWhatTheModelRefusesToGuess:
    def test_sheet_sync_defaults_to_unknown(self):
        r = _run(exposure_us=300.0)
        assert r.sync is SheetSyncStatus.UNKNOWN

    def test_an_open_slit_with_unknown_sync_says_so(self):
        # A narrow slit only pays off if the sheet tracks it. This package
        # cannot tell, and guessing would make the light budget a lie.
        r = _run(exposure_us=300.0)
        assert any("UNKNOWN" in w for w in r.warnings)

    def test_a_static_sheet_with_a_narrow_slit_is_called_waste(self):
        r = _run(exposure_us=300.0, sync=SheetSyncStatus.STATIC)
        assert any("no sectioning gain" in w for w in r.warnings)

    def test_no_sync_warning_when_there_is_no_slit_to_track(self):
        r = _run(sync=SheetSyncStatus.UNKNOWN)
        assert not any("UNKNOWN" in w for w in r.warnings)


class TestAcquisitionTotals:
    def test_planes_come_from_range_and_spacing(self):
        r = _run(z_range_mm=1.0, plane_spacing_um=10.0)
        assert r.plan.planes == 100

    def test_a_partial_plane_still_counts(self):
        r = _run(z_range_mm=1.0, plane_spacing_um=30.0)
        assert r.plan.planes == 34

    def test_stack_time_is_planes_over_frame_rate(self):
        r = _run(z_range_mm=1.0, plane_spacing_um=10.0)
        assert r.stack_seconds == pytest.approx(100 / 40.0, rel=1e-6)

    def test_overhead_is_added_per_stack_not_per_frame(self):
        bare = _run(z_range_mm=1.0, plane_spacing_um=10.0)
        with_overhead = _run(
            z_range_mm=1.0, plane_spacing_um=10.0, per_stack_overhead_s=3.0
        )
        assert with_overhead.stack_seconds == pytest.approx(bare.stack_seconds + 3.0)

    def test_total_multiplies_tiles_angles_and_channels(self):
        r = _run(z_range_mm=1.0, plane_spacing_um=10.0, tiles=32, angles=2, channels=3)
        assert r.plan.stacks == 192
        assert r.total_seconds == pytest.approx(192 * r.stack_seconds)


class TestTheConstantsAreHonest:
    def test_the_sectioning_threshold_is_a_usefulness_call_not_physics(self):
        # Documented as such; pinned so it cannot drift into looking physical.
        assert MIN_USEFUL_SECTIONING == 2.0

    def test_a_zero_exposure_does_not_divide_by_zero(self):
        r = _run(exposure_us=0.0)
        assert r.camera.duty_cycle == 0.0
        assert r.camera.frame_rate_hz > 0
