"""Tests for LED2DOverviewWorkflow._filter_positions_within_limit.

Out-of-range tile centers must be DROPPED, not commanded: the firmware clamps an
out-of-range move, so the stage stalls at the soft limit and every clamped tile
images the same spot — the duplicated rows at high Y the user observed.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_led_overview_limits.py -q
"""

from py2flamingo.workflows.led_2d_overview_workflow import LED2DOverviewWorkflow

_LIMITS = {
    "x": {"min": 1.0, "max": 12.31},
    "y": {"min": 5.0, "max": 25.0},
    "z": {"min": 12.5, "max": 26.0},
}


def _bare(limits=_LIMITS):
    wf = LED2DOverviewWorkflow.__new__(LED2DOverviewWorkflow)
    wf._stage_limits_cache = limits
    return wf


def test_drops_positions_above_y_limit():
    wf = _bare()
    ys = [12.0, 15.0, 24.0, 26.0, 28.66]  # last two exceed 25.0
    assert wf._filter_positions_within_limit(ys, "y", "Y") == [12.0, 15.0, 24.0]


def test_drops_positions_below_min():
    wf = _bare()
    xs = [0.5, 1.0, 5.0]  # 0.5 below the 1.0 x-min
    assert wf._filter_positions_within_limit(xs, "x", "X") == [1.0, 5.0]


def test_keeps_all_in_range():
    wf = _bare()
    xs = [2.0, 5.0, 8.0, 11.0]
    assert wf._filter_positions_within_limit(xs, "x", "X") == xs


def test_no_limits_returns_unchanged():
    wf = _bare(limits={})
    pos = [1.0, 99.0]
    assert wf._filter_positions_within_limit(pos, "y", "Y") == pos


def test_boundary_values_kept():
    wf = _bare()
    ys = [5.0, 25.0]  # exactly on the limits
    assert wf._filter_positions_within_limit(ys, "y", "Y") == [5.0, 25.0]
