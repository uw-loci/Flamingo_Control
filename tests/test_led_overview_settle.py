"""Unit tests for LED2DOverviewWorkflow._wait_for_axes_settled.

The fast-mode Z sweep grabs frames continuously, so the stage MUST have
physically arrived before imaging — otherwise the projection ingests frames
captured mid-move and adjacent tiles show duplicated/ghosted structure. These
tests exercise the pure polling logic with a fake stage service (no Qt event
loop, no hardware).

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_led_overview_settle.py -q
"""

from py2flamingo.workflows.led_2d_overview_workflow import LED2DOverviewWorkflow


class _FakeStage:
    """Returns a moving position that arrives at the target after N polls."""

    def __init__(self, schedule):
        # schedule: {axis: [pos_poll0, pos_poll1, ...]} consumed per query
        self._schedule = {a: list(v) for a, v in schedule.items()}
        self.calls = {a: 0 for a in schedule}

    def get_axis_position(self, axis):
        seq = self._schedule[axis]
        idx = min(self.calls[axis], len(seq) - 1)
        self.calls[axis] += 1
        return seq[idx]


def _bare_workflow():
    # Bypass __init__ (needs an app/QObject wiring) — we only test pure logic.
    wf = LED2DOverviewWorkflow.__new__(LED2DOverviewWorkflow)
    wf._cancelled = False
    # Minimal state for the live-position broadcast that settle now performs. With
    # no _app, _broadcast_stage_position returns early (no sample view), so the
    # poll logic under test runs unchanged.
    wf._app = None
    wf._movement_controller = None
    wf._last_xyz = [0.0, 0.0, 0.0]
    wf._last_pos_broadcast = 0.0
    wf._pos_broadcast_interval_s = 0.1
    return wf


def test_settles_once_all_axes_reach_target():
    wf = _bare_workflow()
    # X arrives on the 3rd poll, Y on the 2nd.
    stage = _FakeStage(
        {
            1: [5.0, 6.0, 7.5424],  # X target 7.5424
            2: [12.0, 12.445],  # Y target 12.445
        }
    )
    ok = wf._wait_for_axes_settled(
        stage,
        {1: 7.5424, 2: 12.445},
        tolerance_mm=0.01,
        timeout_s=2.0,
        poll_interval_s=0.0,
    )
    assert ok is True


def test_times_out_when_axis_never_arrives():
    wf = _bare_workflow()
    # X is stuck far from target forever.
    stage = _FakeStage({1: [0.0], 2: [12.445]})
    ok = wf._wait_for_axes_settled(
        stage,
        {1: 7.5424, 2: 12.445},
        tolerance_mm=0.01,
        timeout_s=0.2,
        poll_interval_s=0.0,
    )
    assert ok is False  # proceeds anyway, but reports not-settled


def test_tolerance_is_respected():
    wf = _bare_workflow()
    # Position is 8 um off — within the default 10 um window.
    stage = _FakeStage({3: [16.778 - 0.008]})
    ok = wf._wait_for_axes_settled(
        stage, {3: 16.778}, tolerance_mm=0.01, timeout_s=1.0, poll_interval_s=0.0
    )
    assert ok is True


def test_cancellation_aborts_wait():
    wf = _bare_workflow()
    wf._cancelled = True
    stage = _FakeStage({1: [0.0]})
    ok = wf._wait_for_axes_settled(
        stage, {1: 7.5424}, timeout_s=5.0, poll_interval_s=0.0
    )
    assert ok is False


def test_query_exception_does_not_crash():
    wf = _bare_workflow()

    class _Boom:
        def __init__(self):
            self.n = 0

        def get_axis_position(self, axis):
            self.n += 1
            if self.n < 3:
                raise RuntimeError("comm hiccup")
            return 7.5424

    ok = wf._wait_for_axes_settled(
        _Boom(), {1: 7.5424}, timeout_s=2.0, poll_interval_s=0.0
    )
    assert ok is True
