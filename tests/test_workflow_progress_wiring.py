"""Persistent acquisition-progress monitoring for direct Workflow-tab runs.

A workflow started directly from the Workflow tab does not go through the queue,
so its progress callbacks were never registered. WorkflowQueueService now offers
a persistent progress listener that is registered on connect and coordinates with
the queue's own callback (no double-register, no teardown).
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from PyQt5.QtWidgets import QApplication  # noqa: E402

from py2flamingo.services.workflow_queue_service import (  # noqa: E402
    UI_SET_GAUGE_VALUE,
    WorkflowQueueService,
)

_app = QApplication.instance() or QApplication([])


class _FakeConn:
    def __init__(self):
        self.registered = []  # (code, handler)
        self.unregistered = []

    def register_callback(self, code, handler):
        self.registered.append((code, handler))

    def unregister_callback(self, code, handler):
        self.unregistered.append((code, handler))


def _make_service(conn):
    svc = WorkflowQueueService.__new__(WorkflowQueueService)
    # Minimal attrs used by the methods under test.
    svc._connection_service = conn
    svc._progress_monitoring = False
    svc._workflow_running = False
    svc._queue = []
    svc._current_index = -1
    return svc


class TestProgressMonitoring(unittest.TestCase):
    def test_register_sets_flag_and_registers_gauge_callback(self):
        conn = _FakeConn()
        svc = _make_service(conn)
        svc.register_progress_monitoring()
        self.assertTrue(svc._progress_monitoring)
        codes = [c for c, _ in conn.registered]
        self.assertIn(UI_SET_GAUGE_VALUE, codes)

    def test_queue_register_skips_gauge_when_persistent(self):
        conn = _FakeConn()
        svc = _make_service(conn)
        svc.register_progress_monitoring()
        conn.registered.clear()
        # The queue path must NOT re-register the gauge callback.
        svc._register_callbacks()
        self.assertNotIn(UI_SET_GAUGE_VALUE, [c for c, _ in conn.registered])

    def test_queue_unregister_keeps_persistent_gauge(self):
        conn = _FakeConn()
        svc = _make_service(conn)
        svc.register_progress_monitoring()
        svc._unregister_callbacks()
        # The persistent gauge callback must survive a queue run's teardown.
        self.assertNotIn(UI_SET_GAUGE_VALUE, [c for c, _ in conn.unregistered])

    def test_stop_clears_flag_and_unregisters(self):
        conn = _FakeConn()
        svc = _make_service(conn)
        svc.register_progress_monitoring()
        svc.stop_progress_monitoring()
        self.assertFalse(svc._progress_monitoring)
        self.assertIn(UI_SET_GAUGE_VALUE, [c for c, _ in conn.unregistered])

    def test_progress_callback_emits_workflow_progress(self):
        conn = _FakeConn()
        svc = _make_service(conn)
        WorkflowQueueService.__init__  # ensure QObject signals exist
        # _on_progress_update needs the Qt signal; re-init via the real __init__.
        real = WorkflowQueueService(
            workflow_controller=MagicMock(), connection_service=conn
        )
        seen = []
        real.workflow_progress.connect(lambda a, e: seen.append((a, e)))
        msg = MagicMock()
        msg.int32_data0 = 7
        msg.int32_data1 = 20
        real._on_progress_update(msg)
        self.assertEqual(seen, [(7, 20)])


if __name__ == "__main__":
    unittest.main()
