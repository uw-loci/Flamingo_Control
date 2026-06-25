"""Unit tests for LED2DOverviewWorkflow._broadcast_stage_position.

During an LED 2D overview the workflow drives the stage through StageService
directly (not movement_controller), so nothing else emits position updates while
scanning — leaving the Sample View sliders / 3D view frozen, unlike the C++ GUI
whose sliders track the stage live. _broadcast_stage_position re-emits
movement_controller.position_changed from the scan loop to restore that.

These tests exercise the pure broadcast logic with a fake movement controller
(no Qt event loop, no hardware).

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_led_overview_position_broadcast.py -q
"""

from py2flamingo.workflows.led_2d_overview_workflow import LED2DOverviewWorkflow


class _FakeSignal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _FakeMovementController:
    def __init__(self):
        self.position_changed = _FakeSignal()


def _workflow_with_mc():
    wf = LED2DOverviewWorkflow.__new__(LED2DOverviewWorkflow)
    wf._app = None
    wf._movement_controller = _FakeMovementController()  # skip _get_controllers
    wf._last_xyz = [0.0, 0.0, 0.0]
    wf._last_pos_broadcast = 0.0
    wf._pos_broadcast_interval_s = 0.1
    wf._rotation_angles = [0.0]
    wf._current_rotation_idx = 0
    return wf


def test_emits_full_xyzr():
    wf = _workflow_with_mc()
    wf._broadcast_stage_position(1.0, 2.0, 3.0, throttle=False, process_events=False)
    assert wf._movement_controller.position_changed.emitted == [(1.0, 2.0, 3.0, 0.0)]


def test_partial_update_keeps_other_axes():
    wf = _workflow_with_mc()
    wf._broadcast_stage_position(1.0, 2.0, 3.0, throttle=False, process_events=False)
    # Only Z changes; X/Y must hold their previous value (no jerk to 0).
    wf._broadcast_stage_position(z=9.0, throttle=False, process_events=False)
    assert wf._movement_controller.position_changed.emitted[-1] == (1.0, 2.0, 9.0, 0.0)


def test_throttle_suppresses_rapid_emits():
    wf = _workflow_with_mc()
    wf._broadcast_stage_position(z=1.0, throttle=False, process_events=False)
    # Immediately after, a throttled call within the interval is dropped.
    wf._broadcast_stage_position(z=2.0, throttle=True, process_events=False)
    assert len(wf._movement_controller.position_changed.emitted) == 1
    # ...but the remembered position is unchanged (the dropped value was not stored).
    assert wf._last_xyz[2] == 1.0


def test_rotation_angle_used_for_r():
    wf = _workflow_with_mc()
    wf._rotation_angles = [45.0, 135.0]
    wf._current_rotation_idx = 1
    wf._broadcast_stage_position(0.0, 0.0, 0.0, throttle=False, process_events=False)
    assert wf._movement_controller.position_changed.emitted[-1][3] == 135.0


def test_no_sample_view_does_not_crash():
    # No cached controller and no app -> _get_controllers raises -> early return.
    wf = LED2DOverviewWorkflow.__new__(LED2DOverviewWorkflow)
    wf._app = None
    wf._movement_controller = None
    wf._last_xyz = [0.0, 0.0, 0.0]
    wf._last_pos_broadcast = 0.0
    wf._pos_broadcast_interval_s = 0.1
    # Should not raise.
    wf._broadcast_stage_position(1.0, 2.0, 3.0, throttle=False, process_events=False)
