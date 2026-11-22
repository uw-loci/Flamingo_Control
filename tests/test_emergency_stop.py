"""The emergency stop: sent at once, honest about it, latched, and obeyed everywhere.

Until 2026-11-22:

* HALT went through ``send_command``, which first tried to reconnect a dead
  socket and then waited up to 3 s for a reply, and any error was logged and
  swallowed: ``emergency_stop()`` returned None, so nobody could tell the
  operator HALT was not sent.
* The Stage Control view cleared the stop by itself after 2 s.
* The stop flag lived on ``PositionController``; ``StageService.move_to_position``
  (the LED overview and the workflow queue) never saw it.
* A running server workflow, the queue and the LED overview kept going.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_emergency_stop.py -q
"""

from __future__ import annotations

import logging
import unittest
from unittest.mock import MagicMock

import pytest

from py2flamingo.core.command_codes import CameraCommands, StageCommands
from py2flamingo.services import stage_motion_gate as gate
from py2flamingo.services.stage_service import AxisCode, StageService
from tests.synthetic_scope_case import SyntheticScopeCase, wait_until


def _controller(connection):
    from py2flamingo.controllers.position_controller import PositionController

    return PositionController(connection, config_service=MagicMock())


class TestHaltIsSentAndReported(SyntheticScopeCase):
    def test_halt_and_workflow_stop_reach_the_scope(self):
        result = _controller(self.connection).emergency_stop()
        self.assertTrue(result.halt_sent)
        self.assertTrue(result.workflow_stop_sent)
        self.assertTrue(wait_until(lambda: self.scope.received(StageCommands.HALT)))
        self.assertEqual(self.scope.received(StageCommands.HALT)[0].axis, 0xFF)
        self.assertTrue(
            wait_until(lambda: self.scope.received(CameraCommands.WORKFLOW_STOP))
        )

    def test_a_running_server_workflow_is_stopped(self):
        self.scope.workflow_running = True
        _controller(self.connection).emergency_stop()
        self.assertTrue(wait_until(lambda: not self.scope.workflow_running))

    def test_halt_not_sent_is_reported_and_no_reconnect_is_tried(self):
        connection = MagicMock()
        connection._command_socket = None
        result = _controller(connection).emergency_stop()
        self.assertFalse(result.halt_sent)
        connection.reconnect_last.assert_not_called()
        connection.send_command.assert_not_called()
        # Latched all the same: nothing may move until an operator clears it.
        self.assertTrue(gate.is_emergency_stop_active())

    def test_a_send_error_is_reported(self):
        connection = MagicMock()
        connection._command_socket.sendall.side_effect = BrokenPipeError("gone")
        result = _controller(connection).emergency_stop()
        self.assertFalse(result.halt_sent)
        connection.reconnect_last.assert_not_called()


class TestTheStopIsLatchedForEveryMovePath(SyntheticScopeCase):
    def test_stage_service_refuses_moves_until_cleared(self):
        controller = _controller(self.connection)
        controller.emergency_stop()
        with self.assertRaises(gate.EmergencyStopActiveError):
            StageService(self.connection).move_to_position(AxisCode.X_AXIS, 6.0)
        self.scope.clear_received()

        controller.clear_emergency_stop()
        StageService(self.connection).move_to_position(AxisCode.X_AXIS, 6.0)
        self.assertTrue(
            wait_until(
                lambda: self.scope.received(StageCommands.POSITION_SET)
                or self.scope.received(StageCommands.POSITION_SET_MOVE)
            )
        )

    def test_position_controller_refuses_moves(self):
        controller = _controller(self.connection)
        controller.emergency_stop()
        with self.assertRaises(RuntimeError):
            controller._move_axis(1, 6.0, "X")

    def test_a_new_controller_does_not_clear_the_latch(self):
        _controller(self.connection).emergency_stop()
        _controller(self.connection)
        self.assertTrue(gate.is_emergency_stop_active())


class TestStopHooks(unittest.TestCase):
    def test_every_hook_runs_even_when_one_fails(self):
        called = []

        def bad():
            called.append("bad")
            raise RuntimeError("boom")

        gate.register_stop_hook("bad", bad)
        gate.register_stop_hook("good", lambda: called.append("good"))
        connection = MagicMock()
        result = _controller(connection).emergency_stop()
        self.assertEqual(sorted(called), ["bad", "good"])
        self.assertIn(("good", None), result.cancelled)
        self.assertIn(("bad", "boom"), result.cancelled)

    def test_queue_registers_while_running(self):
        from unittest.mock import patch

        from py2flamingo.services.workflow_queue_service import WorkflowQueueService

        queue = WorkflowQueueService.__new__(WorkflowQueueService)
        queue._is_running = False
        queue._queue = [object()]
        with patch("py2flamingo.services.workflow_queue_service.threading.Thread"):
            self.assertTrue(queue.start())
        self.assertIs(gate._stop_hooks["workflow queue"].__self__, queue)


# ------------------------------------------------------------------ the view


class _FakeMovement:
    def __init__(self, halt_sent):
        from py2flamingo.controllers.position_controller import EmergencyStopResult

        self._result = EmergencyStopResult(halt_sent=halt_sent, workflow_stop_sent=True)
        self.position_controller = MagicMock()
        type(self.position_controller)._emergency_stop_active = property(
            lambda _self: gate.is_emergency_stop_active()
        )
        self.position_controller.clear_emergency_stop.side_effect = (
            gate.clear_emergency_stop
        )

    def halt_motion(self):
        gate.latch_emergency_stop()
        return self._result


_APP = None


def _view(halt_sent):
    from PyQt5.QtWidgets import QApplication, QLabel, QPushButton

    global _APP
    _APP = QApplication.instance() or QApplication([])
    from py2flamingo.views.stage_control_view import StageControlView

    view = StageControlView.__new__(StageControlView)
    view.logger = logging.getLogger("test_estop_view")
    view.movement_controller = _FakeMovement(halt_sent)
    view.motion_status_label = QLabel()
    view.estop_btn = QPushButton()
    view.clear_estop_btn = QPushButton()
    view.clear_estop_btn.setVisible(False)
    return view


@pytest.fixture
def no_timers(monkeypatch):
    from PyQt5 import QtCore

    scheduled = []
    monkeypatch.setattr(
        QtCore.QTimer, "singleShot", lambda *a, **k: scheduled.append(a)
    )
    return scheduled


def test_view_shows_halt_not_sent(no_timers):
    view = _view(halt_sent=False)
    view._on_emergency_stop_clicked()
    assert "HALT NOT SENT" in view.motion_status_label.text()
    assert not view.clear_estop_btn.isHidden()


def test_view_does_not_clear_by_itself(no_timers):
    view = _view(halt_sent=True)
    view._on_emergency_stop_clicked()
    assert no_timers == []
    assert gate.is_emergency_stop_active()
    assert "EMERGENCY STOPPED" in view.motion_status_label.text()
    # A second press re-sends HALT; it never clears the stop.
    view._on_emergency_stop_clicked()
    assert gate.is_emergency_stop_active()
    # Motion-stopped after the halt does not paint over the stop.
    view._set_controls_enabled = lambda enabled: None
    view._on_motion_stopped("X")
    assert "EMERGENCY STOPPED" in view.motion_status_label.text()


def test_view_clears_only_after_confirmation(no_timers, monkeypatch):
    from PyQt5.QtWidgets import QMessageBox

    view = _view(halt_sent=True)
    view._on_emergency_stop_clicked()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    view._on_clear_emergency_stop_clicked()
    assert gate.is_emergency_stop_active()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    view._on_clear_emergency_stop_clicked()
    assert not gate.is_emergency_stop_active()
    assert view.clear_estop_btn.isHidden()
