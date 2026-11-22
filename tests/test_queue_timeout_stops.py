"""A workflow that times out is stopped, and the queue waits for idle or stops.

Until 2026-11-22, after its 30 min timeout the queue sent no WORKFLOW_STOP,
started the next workflow while the server might still be acquiring, and at
the end drove the stage back to its start. Closing the main window mid-queue
killed the queue thread without asking.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_queue_timeout_stops.py -q
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from py2flamingo.core.command_codes import CameraCommands, StageCommands
from py2flamingo.services.workflow_queue_service import (
    WorkflowQueueItem,
    WorkflowQueueService,
)
from tests.synthetic_scope_case import SyntheticScopeCase, wait_until


class _Controller:
    def __init__(self):
        self.stops = 0

    def stop_workflow(self):
        self.stops += 1
        return True, "stopped"


class _Conn:
    """Answers SYSTEM_STATE_GET with a fixed idle flag."""

    def __init__(self, idle):
        self.idle = idle

    def query_system_state(self, timeout=3.0):
        return {"state": 0xA002 if self.idle else 0xA005, "is_idle": self.idle}


def _queue(idle_after_stop, n=3):
    q = WorkflowQueueService(_Controller(), connection_service=_Conn(idle_after_stop))
    q.STOP_IDLE_TIMEOUT = 0.3
    q.STOP_IDLE_POLL_INTERVAL = 0.05
    q.MIN_INTER_WORKFLOW_DELAY = 0.0
    q._register_callbacks = lambda: None
    q._unregister_callbacks = lambda: None
    q._return_to_origin_after_queue = True
    q._capture_origin_position = lambda: None
    q.returned = []
    q._return_to_origin = lambda: q.returned.append(True)
    q._queue = [WorkflowQueueItem(file_path=Path(f"wf{i}.txt")) for i in range(n)]
    q.sent = []

    def run_one(item):
        q.sent.append(item.file_path.name)
        q._last_wait_timed_out = False
        if item.file_path.name == "wf0.txt":
            q._last_wait_timed_out = True
            return False, "Timeout after 1800.0s"
        return True, None

    q._execute_single_workflow = run_one
    # No reconnect pause in these tests.
    q._workflow_controller._ensure_connected = lambda: (True, "ok")
    q.signals = {"completed": 0, "cancelled": 0, "failed": [], "summary": []}
    q.queue_completed.connect(lambda: q.signals.__setitem__("completed", 1))
    q.queue_cancelled.connect(lambda: q.signals.__setitem__("cancelled", 1))
    q.queue_failed.connect(q.signals["failed"].append)
    q.queue_summary.connect(lambda ok, bad: q.signals["summary"].append((ok, bad)))
    return q


@pytest.fixture
def no_sleep(monkeypatch):
    """Skip the queue's fixed pauses (reconnect settle, inter-workflow)."""
    import py2flamingo.services.workflow_queue_service as mod

    monkeypatch.setattr(mod, "time", MagicMock(wraps=mod.time, sleep=lambda s: None))


def test_no_idle_after_stop_stops_the_queue(no_sleep):
    q = _queue(idle_after_stop=False)
    q._is_running = True
    q._execute_queue()
    assert q._workflow_controller.stops == 1  # WORKFLOW_STOP was sent
    assert q.sent == ["wf0.txt"]  # the next workflow was NOT sent
    assert q.returned == []  # and the stage was not driven back
    assert q.signals["completed"] == 0
    assert q.signals["cancelled"] == 1
    assert len(q.signals["failed"]) == 1
    assert "did not report idle" in q.signals["failed"][0]


def test_idle_after_stop_continues_and_counts_the_failure(no_sleep):
    q = _queue(idle_after_stop=True)
    q._is_running = True
    q._execute_queue()
    assert q._workflow_controller.stops == 1
    assert q.sent == ["wf0.txt", "wf1.txt", "wf2.txt"]
    assert q.signals["completed"] == 1
    assert q.signals["summary"] == [(2, 1)]
    assert q.failed_count == 1


def test_without_a_connection_idle_is_not_assumed():
    q = WorkflowQueueService(_Controller(), connection_service=None)
    q.STOP_IDLE_TIMEOUT = 0.2
    assert q._stop_and_confirm_idle() is False


class TestAgainstTheSyntheticScope(SyntheticScopeCase):
    def test_stop_then_idle_from_the_server(self):
        from py2flamingo.services import safe_state

        class _RealStop:
            def __init__(self, connection):
                self.connection = connection

            def stop_workflow(self):
                return safe_state.stop_workflow(self.connection), ""

        self.scope.workflow_running = True
        q = WorkflowQueueService(
            _RealStop(self.connection), connection_service=self.connection
        )
        q.STOP_IDLE_POLL_INTERVAL = 0.1
        q.STOP_IDLE_TIMEOUT = 5.0
        self.assertTrue(q._stop_and_confirm_idle())
        self.assertTrue(self.scope.received(CameraCommands.WORKFLOW_STOP))


# ------------------------------------------------------------ closing the window


def _bare_window(app):
    from PyQt5.QtWidgets import QApplication

    global _APP
    _APP = QApplication.instance() or QApplication([])
    from py2flamingo.main_window import MainWindow

    window = MainWindow.__new__(MainWindow)
    window.app = app
    window._geometry_manager = None
    window.camera_live_viewer = None
    window.image_controls_window = None
    return window


_APP = None


class TestCloseAsksFirst(SyntheticScopeCase):
    def _close(self, monkeypatch_answer):
        from PyQt5.QtWidgets import QApplication, QMessageBox

        from py2flamingo.main_window import MainWindow

        app = MagicMock(sample_view=None, connection_service=self.connection)
        app.workflow_queue_service.is_running = True
        app.is_acquisition_in_progress = False
        window = _bare_window(app)
        event = MagicMock()
        asked = []
        question, quit_ = QMessageBox.question, QApplication.quit
        QMessageBox.question = staticmethod(
            lambda *a, **k: asked.append(a) or monkeypatch_answer
        )
        QApplication.quit = staticmethod(lambda: None)
        try:
            MainWindow.closeEvent(window, event)
        finally:
            QMessageBox.question, QApplication.quit = question, quit_
        return asked, event

    def test_no_keeps_the_window_and_sends_nothing(self):
        from PyQt5.QtWidgets import QMessageBox

        self.scope.lasers_on = {2}
        asked, event = self._close(QMessageBox.No)
        self.assertEqual(len(asked), 1)
        event.ignore.assert_called_once()
        event.accept.assert_not_called()
        self.assertEqual(self.scope.lasers_on, {2})
        self.assertEqual(self.scope.received(StageCommands.HALT), [])

    def test_yes_stops_everything(self):
        from PyQt5.QtWidgets import QMessageBox

        from py2flamingo.services import stage_motion_gate

        cancelled = []
        stage_motion_gate.register_stop_hook("queue", lambda: cancelled.append(1))
        self.scope.lasers_on = {2}
        asked, event = self._close(QMessageBox.Yes)
        event.accept.assert_called_once()
        self.assertEqual(cancelled, [1])
        self.assertTrue(wait_until(lambda: self.scope.received(StageCommands.HALT)))
        self.assertTrue(
            wait_until(lambda: self.scope.received(CameraCommands.WORKFLOW_STOP))
        )
        self.assertTrue(wait_until(lambda: not self.scope.lasers_on))
