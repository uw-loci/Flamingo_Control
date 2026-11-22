"""Lights off when the client lets go of the instrument: close, quit, disconnect, crash.

Until 2026-11-22 the only light-off on exit was ``camera_controller.stop_live_view``,
which returns at once unless live view is running: a laser switched on from the
Laser panel stayed on after the window closed. ``application.shutdown()`` and
``connection_service.disconnect()`` turned nothing off, no ``sys.excepthook``
was installed, and an exception while stopping the stream skipped the
light-off as well.

The rule tested here (``safe_state.leave_safe``):

* nothing running: lights off, no HALT;
* a client acquisition running: HALT, WORKFLOW_STOP and lights off;
* only a server workflow running: nothing sent, the workflow keeps its light.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_safe_state.py -q
"""

from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock

from py2flamingo.core.command_codes import CameraCommands, StageCommands
from py2flamingo.services import safe_state
from tests.synthetic_scope_case import SyntheticScopeCase, wait_until

LASER_DISABLE_ALL = 0x2007


class LitScopeCase(SyntheticScopeCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(safe_state.set_activity_probe, None)
        # Lit from the client's preview, live view off: the case that used to
        # stay lit after the window closed.
        self.scope.lasers_on = {2}
        self.scope.led_enabled = True

    def lights_out(self):
        return wait_until(
            lambda: not self.scope.lasers_on and not self.scope.led_enabled
        )


class TestLeaveSafe(LitScopeCase):
    def test_nothing_running_lights_off_no_halt(self):
        self.scope.live_view = True
        result = safe_state.leave_safe(self.connection, "test")
        self.assertTrue(result.ok)
        self.assertTrue(self.lights_out())
        self.assertTrue(wait_until(lambda: not self.scope.live_view))
        self.assertEqual(self.scope.received(StageCommands.HALT), [])
        self.assertEqual(self.scope.received(CameraCommands.WORKFLOW_STOP), [])

    def test_client_acquisition_halts_and_stops_the_workflow(self):
        safe_state.set_activity_probe(
            lambda: safe_state.Activity(client_acquisition=True, server_workflow=True)
        )
        safe_state.leave_safe(self.connection, "test")
        self.assertTrue(self.lights_out())
        self.assertTrue(wait_until(lambda: self.scope.received(StageCommands.HALT)))
        self.assertEqual(self.scope.received(StageCommands.HALT)[0].axis, 0xFF)
        self.assertTrue(
            wait_until(lambda: self.scope.received(CameraCommands.WORKFLOW_STOP))
        )

    def test_a_server_workflow_keeps_its_light(self):
        safe_state.set_activity_probe(lambda: safe_state.Activity(server_workflow=True))
        self.assertIsNone(safe_state.leave_safe(self.connection, "test"))
        self.assertEqual(self.scope.lasers_on, {2})
        self.assertEqual(self.scope.received(LASER_DISABLE_ALL), [])
        self.assertEqual(self.scope.received(StageCommands.HALT), [])

    def test_every_known_line_is_named(self):
        safe_state.set_activity_probe(
            lambda: safe_state.Activity(laser_lines=[1, 2, 3, 4, 5])
        )
        safe_state.leave_safe(self.connection, "test")
        self.assertTrue(
            wait_until(lambda: len(self.scope.received(LASER_DISABLE_ALL)) == 5)
        )

    def test_sent_once_per_socket(self):
        safe_state.leave_safe(self.connection, "close")
        self.assertTrue(self.lights_out())
        n = len(self.scope.received())
        self.assertIsNone(safe_state.leave_safe(self.connection, "quit"))
        self.assertEqual(len(self.scope.received()), n)

    def test_a_probe_that_raises_still_turns_lights_off(self):
        def bad():
            raise RuntimeError("no app")

        safe_state.set_activity_probe(bad)
        safe_state.leave_safe(self.connection, "test")
        self.assertTrue(self.lights_out())


class TestDisconnect(LitScopeCase):
    def test_disconnect_turns_the_lights_off_first(self):
        self.connection.disconnect()
        self.assertTrue(self.lights_out())


class TestExceptHook(LitScopeCase):
    def test_hook_logs_makes_safe_and_chains(self):
        chained = []
        saved = sys.excepthook
        sys.excepthook = lambda *a: chained.append(a[0])
        try:
            previous = safe_state.install_excepthook(lambda: self.connection)
            try:
                raise ValueError("boom")
            except ValueError:
                sys.excepthook(*sys.exc_info())
        finally:
            sys.excepthook = saved
        self.assertEqual(chained, [ValueError])
        self.assertIsNotNone(previous)
        self.assertTrue(self.lights_out())
        # The app keeps running, so a later close still sends its own.
        self.scope.lasers_on = {3}
        safe_state.leave_safe(self.connection, "close")
        self.assertTrue(self.lights_out())

    def test_hook_cancels_a_running_client_acquisition(self):
        from py2flamingo.services import stage_motion_gate

        cancelled = []
        stage_motion_gate.register_stop_hook("scan", lambda: cancelled.append(1))
        safe_state.set_activity_probe(
            lambda: safe_state.Activity(client_acquisition=True)
        )
        saved = sys.excepthook
        sys.excepthook = lambda *a: None
        try:
            safe_state.install_excepthook(lambda: self.connection)
            sys.excepthook(RuntimeError, RuntimeError("x"), None)
        finally:
            sys.excepthook = saved
        self.assertEqual(cancelled, [1])
        self.assertTrue(wait_until(lambda: self.scope.received(StageCommands.HALT)))


class TestMainWindowClose(LitScopeCase):
    def test_close_turns_lights_off_without_live_view(self):
        from PyQt5.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])  # noqa: F841
        from py2flamingo.main_window import MainWindow

        window = MainWindow.__new__(MainWindow)
        window.app = MagicMock(sample_view=None, connection_service=self.connection)
        window._geometry_manager = None
        window.camera_live_viewer = None
        window.image_controls_window = None
        quit_ = QApplication.quit
        QApplication.quit = staticmethod(lambda: None)
        try:
            MainWindow.closeEvent(window, MagicMock())
        finally:
            QApplication.quit = quit_
        self.assertTrue(self.lights_out())
        self.assertEqual(self.scope.received(StageCommands.HALT), [])


class TestStopLiveViewLightsOff(unittest.TestCase):
    def test_lights_off_even_when_stopping_the_stream_raises(self):
        from py2flamingo.controllers.camera_controller import (
            CameraController,
            CameraState,
        )

        cc = CameraController.__new__(CameraController)
        cc._state = CameraState.LIVE_VIEW
        cc._display_timer = MagicMock()
        cc._live_view_watchdog = MagicMock()
        cc.camera_service = MagicMock()
        cc.camera_service.stop_live_view_streaming.side_effect = OSError("socket")
        cc.laser_led_controller = MagicMock()
        cc.logger = MagicMock()
        cc.error_occurred = MagicMock()
        self.assertFalse(cc.stop_live_view())
        cc.laser_led_controller.disable_all_light_sources.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class TestApplicationActivity(unittest.TestCase):
    def _app(self, acquiring=False, queue_running=False, executing=False):
        from py2flamingo.application import FlamingoApplication

        app = FlamingoApplication.__new__(FlamingoApplication)
        app._acquisition_in_progress = acquiring
        app.workflow_queue_service = MagicMock(is_running=queue_running)
        app.workflow_controller = MagicMock(is_executing=executing)
        app.laser_led_service = MagicMock()
        app.laser_led_service.laser_lines_to_disable.return_value = [1, 2, 3]
        return app

    def test_idle(self):
        a = self._app().safety_activity()
        self.assertFalse(a.client_acquisition or a.server_workflow)
        self.assertEqual(a.laser_lines, [1, 2, 3])

    def test_client_acquisition_and_queue_count_as_client(self):
        self.assertTrue(self._app(acquiring=True).safety_activity().client_acquisition)
        self.assertTrue(
            self._app(queue_running=True).safety_activity().client_acquisition
        )

    def test_server_workflow(self):
        a = self._app(executing=True).safety_activity()
        self.assertTrue(a.server_workflow and not a.client_acquisition)
