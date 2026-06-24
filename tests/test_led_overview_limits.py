"""Tests for LED2DOverviewWorkflow._fit_positions_to_limits.

When a requested overview tiling overruns a stage soft limit, the whole set is
shifted as a block to sit flush against the nearest reachable edge (limit minus
margin), preserving the requested span/coverage. If the span is larger than the
stage can travel at all, the helper reports fits=False so the caller aborts with
a warning dialog instead of commanding unsafe moves.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_led_overview_limits.py -q
"""

import pytest

from py2flamingo.workflows.led_2d_overview_workflow import LED2DOverviewWorkflow

_LIMITS = {
    "x": {"min": 1.0, "max": 12.31},
    "y": {"min": 5.0, "max": 25.0},
    "z": {"min": 12.5, "max": 26.0},
}
_MARGIN = 0.25  # default


def _bare(limits=_LIMITS):
    wf = LED2DOverviewWorkflow.__new__(LED2DOverviewWorkflow)
    wf._stage_limits_cache = limits
    return wf


def test_in_range_is_noop():
    wf = _bare()
    pos = [10.0, 12.0, 14.0]
    out, fits = wf._fit_positions_to_limits(pos, "y")
    assert fits is True
    assert out == pos


def test_overrun_high_shifts_block_down_preserving_span():
    wf = _bare()
    pos = [22.0, 24.0, 26.0]  # 26 > 25 - 0.25
    out, fits = wf._fit_positions_to_limits(pos, "y")
    assert fits is True
    # max sits at the usable edge (25 - 0.25), span preserved.
    assert out[-1] == pytest.approx(24.75)
    assert (out[-1] - out[0]) == pytest.approx(pos[-1] - pos[0])
    assert min(out) >= 5.0


def test_overrun_low_shifts_block_up():
    wf = _bare()
    pos = [4.0, 6.0, 8.0]  # 4 < 5 + 0.25
    out, fits = wf._fit_positions_to_limits(pos, "y")
    assert fits is True
    assert out[0] == pytest.approx(5.25)
    assert (out[-1] - out[0]) == pytest.approx(pos[-1] - pos[0])


def test_span_too_big_reports_not_fit_and_drops_unreachable():
    wf = _bare()
    pos = [0.0, 10.0, 20.0, 30.0]  # span 30 > usable 19.5
    out, fits = wf._fit_positions_to_limits(pos, "y")
    assert fits is False
    # Backstop: only reachable centers remain so nothing unsafe is commanded.
    assert all(5.25 - 1e-6 <= p <= 24.75 + 1e-6 for p in out)


def test_no_limits_returns_unchanged():
    wf = _bare(limits={})
    pos = [1.0, 99.0]
    out, fits = wf._fit_positions_to_limits(pos, "y")
    assert fits is True
    assert out == pos


def test_z_range_pair_shifts_together():
    wf = _bare()
    # Z limit [12.5, 26.0]; request a stack ending past the top.
    out, fits = wf._fit_positions_to_limits([20.0, 26.5], "z")
    assert fits is True
    assert out[-1] == pytest.approx(25.75)  # 26.0 - 0.25
    assert (out[-1] - out[0]) == pytest.approx(6.5)
