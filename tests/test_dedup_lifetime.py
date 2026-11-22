"""The duplicate-move table forgets a move once that move can no longer be in flight.

``services/stage_command_dedup`` drops a move identical to the one still in
flight on that axis, because the server tears down its motion thread on an
identical repeat. Until 2026-11-22 the table was cleared only by a
``MotionTracker`` callback that registers on a tracker's first arm or wait, and
it was never reset on connect or disconnect. The workflow queue moves through
``StageService`` with no tracker, so after a queue's return-to-origin the next
queue run's identical return-to-origin was dropped as a "duplicate" of a move
that had finished minutes earlier, and the stage stayed at the last tile.

Now the connection owns one motion-stopped callback per connection, and the
table is also cleared on HALT, connect, disconnect and connection loss. A
repeat while the move is still running is still dropped.
"""

from __future__ import annotations

import threading

from py2flamingo.core.command_codes import StageCommands
from py2flamingo.models.connection import ConnectionConfig
from py2flamingo.services import safe_state, stage_command_dedup
from py2flamingo.services.stage_service import StageService
from tests.synthetic_scope_case import SyntheticScopeCase, wait_until

X_AXIS = 1


class TestDedupLifetime(SyntheticScopeCase):
    def setUp(self):
        super().setUp()
        stage_command_dedup.reset()
        self.addCleanup(stage_command_dedup.reset)

    def _sent_x_moves(self, value: float) -> int:
        return sum(
            1
            for c in self.scope.received(StageCommands.POSITION_SET)
            if c.params[3] == X_AXIS and abs(c.value - value) < 1e-6
        )

    def _motion_stopped_event(self) -> threading.Event:
        """Set when the client has dispatched the next STAGE_MOTION_STOPPED.

        Registered after the connection's own handler, and the reader calls
        handlers in order, so when this is set the connection's handler has run.
        """
        seen = threading.Event()
        self.connection.register_callback(
            StageCommands.MOTION_STOPPED, lambda _msg: seen.set()
        )
        return seen

    def _target_near(self, offset_mm: float) -> float:
        return round(self.scope.stage_position()["x"] + offset_mm, 3)

    def test_a_move_repeated_after_the_stage_stopped_is_sent(self):
        # The queue's return-to-origin: no MotionTracker anywhere.
        stopped = self._motion_stopped_event()
        target = self._target_near(0.2)
        service = StageService(self.connection)
        self.assertTrue(service.move_to_position(X_AXIS, target))
        self.assertTrue(stopped.wait(5.0), "the scope never reported the stop")
        self.assertTrue(
            service.move_to_position(X_AXIS, target),
            "a move to the same target after the stage stopped was dropped",
        )
        self.assertTrue(wait_until(lambda: self._sent_x_moves(target) == 2))

    def test_a_repeat_while_the_move_runs_is_still_dropped(self):
        # 10 mm at the synthetic scope's 2 mm/s: still moving for seconds.
        target = self._target_near(10.0)
        service = StageService(self.connection)
        self.assertTrue(service.move_to_position(X_AXIS, target))
        self.assertTrue(wait_until(lambda: self.scope.stage.moving))
        self.assertFalse(service.move_to_position(X_AXIS, target))
        self.assertTrue(wait_until(lambda: self._sent_x_moves(target) == 1))
        safe_state.halt_all_axes(self.connection)

    def test_halt_forgets_the_move_it_stopped(self):
        target = self._target_near(10.0)
        service = StageService(self.connection)
        self.assertTrue(service.move_to_position(X_AXIS, target))
        self.assertTrue(safe_state.halt_all_axes(self.connection))
        self.assertTrue(service.move_to_position(X_AXIS, target))
        safe_state.halt_all_axes(self.connection)

    def test_halt_clears_the_table_without_a_stop_report(self):
        # The scope reports no stop for a HALT of a stage that is not moving;
        # the HALT itself must still clear the table.
        stage_command_dedup.should_send(X_AXIS, 7.5)
        self.assertTrue(safe_state.halt_all_axes(self.connection))
        self.assertTrue(stage_command_dedup.should_send(X_AXIS, 7.5))

    def test_reconnect_forgets_a_move_whose_stop_was_never_seen(self):
        stage_command_dedup.should_send(X_AXIS, 7.5)  # stop lost with the link
        self.connection.disconnect()
        self.assertTrue(stage_command_dedup.should_send(X_AXIS, 7.5))
        stage_command_dedup.should_send(X_AXIS, 8.5)
        self.connection.connect(
            ConnectionConfig("127.0.0.1", self.scope.command_port, self.scope.live_port)
        )
        self.assertTrue(stage_command_dedup.should_send(X_AXIS, 8.5))

    def test_connection_loss_forgets_the_table(self):
        stage_command_dedup.should_send(X_AXIS, 7.5)
        self.connection._on_tcp_connection_lost("test: socket died")
        self.assertTrue(stage_command_dedup.should_send(X_AXIS, 7.5))

    def test_the_stop_after_a_reconnect_still_clears_it(self):
        # The reader (and its callback table) is new on every connect.
        self.connection.disconnect()
        self.connection.connect(
            ConnectionConfig("127.0.0.1", self.scope.command_port, self.scope.live_port)
        )
        stopped = self._motion_stopped_event()
        target = self._target_near(0.2)
        service = StageService(self.connection)
        self.assertTrue(service.move_to_position(X_AXIS, target))
        self.assertTrue(stopped.wait(5.0))
        self.assertTrue(service.move_to_position(X_AXIS, target))

    def test_one_handler_per_connection(self):
        def handlers():
            dispatcher = self.connection.tcp_connection.dispatcher
            return [
                h
                for h in dispatcher._callback_handlers.get(
                    StageCommands.MOTION_STOPPED, []
                )
                if getattr(h, "__func__", None)
                is type(self.connection)._on_motion_stopped
            ]

        self.assertEqual(len(handlers()), 1)
        self.connection.disconnect()
        self.connection.connect(
            ConnectionConfig("127.0.0.1", self.scope.command_port, self.scope.live_port)
        )
        self.assertEqual(len(handlers()), 1)
