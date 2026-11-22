"""After a move, the position is what the hardware said, or it is unknown.

Until 2026-11-22 ``PositionController``:

* never armed the MotionTracker before sending a move, so a short move's
  STAGE_MOTION_STOPPED could arrive before the wait began and be discarded;
  the wait then sat out its 10 s timeout (the race ``MotionTracker.arm``
  documents);
* replaced every axis it failed to read back with the TARGET and still logged
  "Position confirmed from hardware";
* re-guessed an unknown position from the home position in ScopeSettings.txt;
* wrote ``_current_position`` from three threads with no lock.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_motion_wait_honest.py -q
"""

from __future__ import annotations

import logging
import threading
import time
from unittest.mock import MagicMock, patch

from py2flamingo.models.microscope import Position
from py2flamingo.services.stage_service import AxisCode
from tests.synthetic_scope_case import SyntheticScopeCase


def _controller(connection):
    from py2flamingo.controllers.position_controller import PositionController

    config = MagicMock()
    config.get_stage_limits.return_value = {
        a: {"min": -100.0, "max": 400.0} for a in ("x", "y", "z", "r")
    }
    config.get_position_history_max_size.return_value = 10
    return PositionController(connection, config_service=config)


class TestArmedBeforeTheMove(SyntheticScopeCase):
    def test_a_fast_stop_is_not_missed(self):
        from py2flamingo.controllers.motion_tracker import MotionTracker

        pc = _controller(self.connection)
        self.assertIsNotNone(pc._motion_tracker)
        start = self.scope.stage_position()["x"]

        # The wait thread starts late (GUI load, scheduling): the stop of a
        # tiny move is already in by then. Armed, it was buffered; unarmed,
        # it was dropped and the wait ran to its 10 s timeout.
        original = MotionTracker.wait_for_motion_complete

        def late(tracker, *a, **k):
            time.sleep(0.3)
            return original(tracker, *a, **k)

        with patch.object(MotionTracker, "wait_for_motion_complete", late):
            t0 = time.monotonic()
            pc.move_x(start + 0.001)
            self.assertTrue(pc.wait_for_movement_complete(timeout=6.0))
            elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 6.0)
        self.assertTrue(pc.position_verified)
        self.assertAlmostEqual(pc.get_current_position().x, start + 0.001, places=4)


class _Tracker:
    def __init__(self, completes):
        self.completes = completes
        self.calls = []

    def arm(self):
        self.calls.append("arm")

    def disarm(self):
        self.calls.append("disarm")

    def cancel_wait(self):
        pass

    def wait_for_motion_complete(self, timeout=10.0, **k):
        return self.completes


def _pc_with(tracker):
    connection = MagicMock()
    connection.is_connected.return_value = False  # no real tracker built
    pc = _controller(connection)
    pc._motion_tracker = tracker
    pc._current_position = Position(x=1.0, y=2.0, z=3.0, r=0.0)
    connection.is_connected.return_value = True
    connection.has_async_reader = True
    return pc, connection


def test_tracker_is_armed_before_the_move_is_sent():
    from py2flamingo.services import stage_command_dedup

    stage_command_dedup.reset()
    order = []
    tracker = _Tracker(True)
    tracker.arm = lambda: order.append("arm")
    pc, connection = _pc_with(tracker)
    connection.send_command_async.side_effect = lambda *a, **k: order.append("send")
    try:
        pc._move_axis(1, 5.0, "X-axis")
    except RuntimeError:
        pass  # no reply from the mock: the order is what is tested
    assert order == ["arm", "send"]


def test_a_failed_send_disarms():
    from py2flamingo.services import stage_command_dedup

    stage_command_dedup.reset()
    tracker = _Tracker(True)
    pc, connection = _pc_with(tracker)
    connection.send_command_async.side_effect = OSError("gone")
    try:
        pc._move_axis(1, 6.0, "X-axis")
    except RuntimeError:
        pass
    assert tracker.calls == ["arm", "disarm"]


def test_a_dropped_duplicate_is_not_armed():
    from py2flamingo.services import stage_command_dedup

    stage_command_dedup.reset()
    tracker = _Tracker(True)
    pc, connection = _pc_with(tracker)
    with patch.object(stage_command_dedup, "should_send", return_value=False):
        pc._move_axis(1, 6.5, "X-axis")
    assert tracker.calls == []
    connection.send_command_async.assert_not_called()


def _run_wait(pc, moved_axes, stage):
    with patch("py2flamingo.services.stage_service.StageService", return_value=stage):
        pc._movement_lock.acquire()
        pc._wait_for_motion_complete_async(
            Position(x=9.0, y=2.0, z=3.0, r=0.0), moved_axes=moved_axes
        )
        assert pc.wait_for_movement_complete(timeout=10.0)


def test_a_failed_read_back_is_unknown_not_the_target(caplog):
    pc, _ = _pc_with(_Tracker(True))
    stage = MagicMock()
    stage.get_axis_position.return_value = None
    with caplog.at_level(logging.INFO):
        _run_wait(pc, [AxisCode.X_AXIS], stage)
    assert pc.get_current_position() is None  # not 9.0, and not re-guessed
    assert not pc.position_verified
    assert "UNKNOWN" in caplog.text
    assert "confirmed from hardware" not in caplog.text


def test_an_unconfirmed_stop_is_logged_as_such(caplog):
    pc, _ = _pc_with(_Tracker(False))  # no STAGE_MOTION_STOPPED
    stage = MagicMock()
    stage.get_axis_position.return_value = 8.5
    with caplog.at_level(logging.INFO):
        _run_wait(pc, [AxisCode.X_AXIS], stage)
    assert pc.get_current_position().x == 8.5
    assert not pc.position_verified
    assert "NOT confirmed" in caplog.text
    assert "confirmed from hardware" not in caplog.text


def test_a_confirmed_move_is_verified():
    pc, _ = _pc_with(_Tracker(True))
    stage = MagicMock()
    stage.get_axis_position.return_value = 8.75
    _run_wait(pc, [AxisCode.X_AXIS], stage)
    assert pc.get_current_position() == Position(x=8.75, y=2.0, z=3.0, r=0.0)
    assert pc.position_verified


def test_unknown_position_reads_all_axes_before_a_jog():
    pc, _ = _pc_with(_Tracker(True))
    pc._set_position_after_move(None, verified=False)
    stage = MagicMock()
    stage.get_position.return_value = Position(x=4.0, y=5.0, z=6.0, r=7.0)
    pc.move_x = MagicMock()
    with patch("py2flamingo.services.stage_service.StageService", return_value=stage):
        pc.jog_x(0.5)
    pc.move_x.assert_called_once_with(4.5)


def test_position_is_read_under_the_lock():
    pc, _ = _pc_with(_Tracker(True))
    got = []
    pc._position_lock.acquire()
    reader = threading.Thread(target=lambda: got.append(pc._current_position))
    reader.start()
    reader.join(timeout=0.2)
    assert reader.is_alive() and got == []  # blocked by the writer's lock
    pc._position_lock.release()
    reader.join(timeout=2.0)
    assert got and got[0].x == 1.0


def test_volume_scan_does_not_guess_the_rotation():
    # An unknown position used to become R = 0, rotating the sample unasked.
    import pytest

    from py2flamingo.workflows.volume_scan_workflow import VolumeScanWorkflow

    wf = VolumeScanWorkflow.__new__(VolumeScanWorkflow)
    wf.position_controller = MagicMock()
    wf.position_controller.get_current_position.return_value = None
    with pytest.raises(RuntimeError):
        wf._execute_position_scan(1.0, 2.0, 3.0, 4.0)
    wf.position_controller.move_to_position.assert_not_called()
