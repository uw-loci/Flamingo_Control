"""The camera's reported rate is not the rate it delivers, and this proves it.

The numbers here are the high-magnification ASLM rig's real operating point: a
44364 ns line time over 2048 rows is a 90.86 ms sweep, which the camera reports
as 11.00 fps, while an external clock at 110 ms actually triggers it at 9.09.
"""

import pytest

from py2flamingo.models.frame_delivery import (
    DEAD_TIME_TOLERANCE,
    MIN_FRAMES_FOR_ESTIMATE,
    CheckStatus,
    DeliveryVerdict,
    FrameArrival,
    TimeSource,
    analyze,
    check_light_sheet,
    measure,
)

LINE_TIME_NS = 44364
ROWS = 2048
SWEEP_MS = ROWS * LINE_TIME_NS / 1e6  # 90.857 ms
CLOCK_MS = 110.0


def arrivals(period_ms, count=100, start=0, skip=()):
    """Frames at a fixed period, optionally with some frame numbers missing."""
    out = []
    for i in range(count):
        number = start + i
        if number in skip:
            continue
        out.append(
            FrameArrival(
                frame_number=number,
                server_timestamp_ms=int(round(i * period_ms)),
                local_time_s=i * period_ms / 1000.0,
            )
        )
    return out


class TestMeasurement:
    def test_too_few_frames_refuses_to_guess(self):
        report = analyze(
            arrivals(CLOCK_MS, count=MIN_FRAMES_FOR_ESTIMATE - 1),
            line_time_ns=LINE_TIME_NS,
            rows=ROWS,
        )
        assert report.verdict is DeliveryVerdict.TOO_FEW_FRAMES
        assert report.stats is None

    def test_period_comes_from_the_span_not_the_deltas(self):
        """Millisecond-quantised stamps still give a sub-0.1% period."""
        stats = measure(arrivals(CLOCK_MS, count=200))
        assert stats.trigger_period_ms == pytest.approx(CLOCK_MS, rel=1e-3)

    def test_prefers_the_server_clock_when_it_is_usable(self):
        assert measure(arrivals(CLOCK_MS)).source is TimeSource.SERVER

    def test_falls_back_to_local_time_when_stamps_are_never_populated(self):
        blank = [
            FrameArrival(frame_number=i, server_timestamp_ms=0, local_time_s=i * 0.110)
            for i in range(50)
        ]
        stats = measure(blank)
        assert stats.source is TimeSource.LOCAL
        assert stats.trigger_period_ms == pytest.approx(110.0, rel=1e-3)

    def test_falls_back_when_the_server_clock_runs_backwards(self):
        bad = arrivals(CLOCK_MS, count=40)
        bad[20] = FrameArrival(bad[20].frame_number, 1, bad[20].local_time_s)
        assert measure(bad).source is TimeSource.LOCAL

    def test_frame_numbers_that_never_advance_still_yield_a_period(self):
        """A server that leaves frame_number at 0 must not produce a zero span."""
        stuck = [
            FrameArrival(
                frame_number=0, server_timestamp_ms=int(i * 110), local_time_s=0
            )
            for i in range(30)
        ]
        stats = measure(stuck)
        assert stats.dropped == 0
        assert stats.trigger_period_ms == pytest.approx(110.0, rel=1e-3)


class TestExternalClock:
    def test_the_rig_operating_point_is_externally_clocked(self):
        report = analyze(arrivals(CLOCK_MS), line_time_ns=LINE_TIME_NS, rows=ROWS)
        assert report.verdict is DeliveryVerdict.EXTERNALLY_CLOCKED
        assert report.reported_fps == pytest.approx(11.006, abs=0.01)
        assert report.stats.trigger_fps == pytest.approx(9.091, abs=0.01)

    def test_dead_fraction_is_the_whole_error(self):
        report = analyze(arrivals(CLOCK_MS), line_time_ns=LINE_TIME_NS, rows=ROWS)
        assert report.dead_fraction == pytest.approx(0.174, abs=0.002)
        # Same number as the Z-step error, which is the point.
        assert report.z_step_error_percent == pytest.approx(21.07, abs=0.1)

    def test_duplicate_count_scales_with_plane_count(self):
        """Why the tail is the same integer on every tile and differs per run."""
        report = analyze(arrivals(CLOCK_MS), line_time_ns=LINE_TIME_NS, rows=ROWS)
        assert report.duplicate_frames(500) == 87
        assert report.duplicate_frames(1000) == 174
        assert report.duplicate_frames(0) == 0

    def test_objects_measure_short_in_z_not_long(self):
        report = analyze(arrivals(CLOCK_MS), line_time_ns=LINE_TIME_NS, rows=ROWS)
        assert report.apparent_z_scale() == pytest.approx(0.826, abs=0.002)
        assert report.apparent_z_scale() < 1.0

    def test_a_smaller_dead_time_is_proportionally_smaller(self):
        """100 ms clock (VC period) is roughly half the error of 110 ms."""
        report = analyze(arrivals(100.0), line_time_ns=LINE_TIME_NS, rows=ROWS)
        assert report.dead_fraction == pytest.approx(0.091, abs=0.003)


class TestCameraAsMaster:
    def test_free_running_at_the_sweep_rate_is_matched(self):
        report = analyze(arrivals(SWEEP_MS), line_time_ns=LINE_TIME_NS, rows=ROWS)
        assert report.verdict is DeliveryVerdict.MATCHED
        assert report.dead_fraction < DEAD_TIME_TOLERANCE
        assert report.duplicate_frames(500) == 0

    def test_a_sub_tolerance_gap_is_not_worth_reporting(self):
        report = analyze(
            arrivals(SWEEP_MS * 1.005), line_time_ns=LINE_TIME_NS, rows=ROWS
        )
        assert report.verdict is DeliveryVerdict.MATCHED


class TestDroppedFrames:
    def test_missing_frame_numbers_outrank_the_clock_mismatch(self):
        """Irregular spacing cannot be corrected later, so it is the headline."""
        report = analyze(
            arrivals(CLOCK_MS, count=100, skip={17, 44, 45}),
            line_time_ns=LINE_TIME_NS,
            rows=ROWS,
        )
        assert report.verdict is DeliveryVerdict.DROPPING_FRAMES
        assert report.stats.dropped == 3

    def test_delivery_is_slower_than_trigger_when_frames_go_missing(self):
        stats = measure(arrivals(CLOCK_MS, count=100, skip={17, 44, 45}))
        assert stats.delivery_period_ms > stats.trigger_period_ms

    def test_trigger_period_survives_the_drops(self):
        """Frame numbers are what make the real clock recoverable."""
        stats = measure(arrivals(CLOCK_MS, count=100, skip={17, 44, 45}))
        assert stats.trigger_period_ms == pytest.approx(CLOCK_MS, rel=1e-3)


class TestImpossibleConfiguration:
    def test_faster_than_the_sweep_blames_the_inputs(self):
        """A rolling shutter cannot outrun its own readout."""
        report = analyze(arrivals(50.0), line_time_ns=LINE_TIME_NS, rows=ROWS)
        assert report.verdict is DeliveryVerdict.FASTER_THAN_SWEEP
        assert any("does not describe this camera" in w for w in report.warnings)

    def test_a_cropped_aoi_changes_the_sweep(self):
        """Half the rows, half the sweep — so 50 ms is fine at 1024 rows."""
        report = analyze(arrivals(50.0), line_time_ns=LINE_TIME_NS, rows=1024)
        assert report.verdict is not DeliveryVerdict.FASTER_THAN_SWEEP


class TestLightSheetReadback:
    STATE = {
        "readout_time_us": SWEEP_MS * 1000.0,
        "line_time_ns": SWEEP_MS * 1e6 / ROWS,
        "reported_fps": 11.0,
        "trigger_mode": 2,
    }

    def _checks(self, **kwargs):
        params = dict(
            rows=ROWS,
            state=self.STATE,
            requested_line_time_ns=float(LINE_TIME_NS),
            requested_exposure_lines=128,
            requested_delay_lines=0,
            requested_trigger_mode=2,
            requested_mode_on=True,
        )
        params.update(kwargs)
        return {c.setting: c for c in check_light_sheet(**params)}

    def test_line_time_round_trips_through_readout_time(self):
        check = self._checks()["Line time"]
        assert check.status is CheckStatus.MATCH
        assert check.reported == pytest.approx(LINE_TIME_NS, rel=1e-4)

    def test_a_line_time_that_did_not_take_is_caught(self):
        state = dict(self.STATE)
        state["line_time_ns"] = 12207.0  # fell back to free-running
        check = self._checks(state=state)["Line time"]
        assert check.status is CheckStatus.MISMATCH

    def test_trigger_mode_is_directly_verifiable(self):
        assert self._checks()["Trigger mode"].status is CheckStatus.MATCH

    def test_wrong_trigger_mode_is_caught_exactly(self):
        state = dict(self.STATE)
        state["trigger_mode"] = 0
        assert self._checks(state=state)["Trigger mode"].status is CheckStatus.MISMATCH

    @pytest.mark.parametrize(
        "setting",
        ["Exposure lines (slit width)", "Delay lines", "Light sheet mode"],
    )
    def test_write_only_settings_are_reported_as_unverifiable(self, setting):
        """They must appear in the report, not be silently omitted.

        A setting missing from a verification report reads as a setting that
        was fine. These three cannot be confirmed at all, and the report has to
        say so.
        """
        check = self._checks()[setting]
        assert check.status is CheckStatus.NO_READBACK
        assert check.note

    def test_a_camera_that_answers_nothing_is_not_reported_as_matching(self):
        blank = {
            "readout_time_us": None,
            "line_time_ns": None,
            "reported_fps": None,
            "trigger_mode": None,
        }
        checks = self._checks(state=blank)
        assert all(c.status is CheckStatus.NO_READBACK for c in checks.values())

    def test_settings_not_asked_for_are_skipped(self):
        checks = self._checks(requested_delay_lines=None)
        assert checks["Delay lines"].status is CheckStatus.NOT_REQUESTED
