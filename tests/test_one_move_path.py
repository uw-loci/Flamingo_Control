"""Every stage move goes through StageService.move_to_position.

Until 2026-11-22 ``PositionController._move_axis`` built and sent its own
POSITION_SET frame through ``connection.send_command``, next to
``StageService.move_to_position`` (the workflow queue, the LED overview).
Two move implementations each applied the motion gate and the duplicate
suppression in their own way; the 11-20 rig timeout came from two paths
sending the same move differently. Now the controller checks the limits and
hands the move to the service, which gates, deduplicates and sends.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from py2flamingo.core.command_codes import StageCommands
from py2flamingo.services import stage_command_dedup
from py2flamingo.services import stage_motion_gate as gate
from py2flamingo.services.stage_service import StageService
from tests.synthetic_scope_case import SyntheticScopeCase, wait_until


def _controller(connection):
    from py2flamingo.controllers.position_controller import PositionController

    config = MagicMock()
    config.get_stage_limits.return_value = {
        a: {"min": -100.0, "max": 400.0} for a in ("x", "y", "z", "r")
    }
    config.get_position_history_max_size.return_value = 10
    return PositionController(connection, config_service=config)


class TestOneMovePath(SyntheticScopeCase):
    def setUp(self):
        super().setUp()
        stage_command_dedup.reset()
        self.addCleanup(stage_command_dedup.reset)

    def _moves(self):
        return [
            (c.params, c.value) for c in self.scope.received(StageCommands.POSITION_SET)
        ]

    def test_the_controller_sends_the_frame_the_service_sends(self):
        StageService(self.connection).move_to_position(1, 6.25)
        self.assertTrue(wait_until(lambda: len(self._moves()) == 1))
        pc = _controller(self.connection)
        pc.move_x(6.5)
        self.assertTrue(wait_until(lambda: len(self._moves()) == 2))
        (service_params, _), (controller_params, value) = self._moves()
        self.assertEqual(controller_params, service_params)
        self.assertEqual(controller_params[3], 1)
        self.assertEqual(controller_params[6], 0x80000000)
        self.assertEqual(value, 6.5)
        pc.wait_for_movement_complete(timeout=5.0)

    def test_the_controller_move_is_the_service_move(self):
        pc = _controller(self.connection)
        with patch.object(
            StageService, "move_to_position", autospec=True, return_value=True
        ) as move:
            pc._move_axis(2, 13.0, "Y-axis")
        self.assertEqual(move.call_count, 1)
        self.assertEqual(move.call_args.args[1:3], (2, 13.0))

    def test_the_gate_refuses_a_controller_move_and_nothing_is_sent(self):
        pc = _controller(self.connection)
        gate.latch_emergency_stop()
        try:
            with self.assertRaises(gate.EmergencyStopActiveError):
                pc._move_axis(1, 7.0, "X-axis")
        finally:
            gate.clear_emergency_stop()
        self.assertEqual(self._moves(), [])

    def test_a_duplicate_is_dropped_once_not_twice(self):
        # The service suppresses a repeat of the move in flight; a second
        # suppression in the controller would drop the first move itself.
        pc = _controller(self.connection)
        before = stage_command_dedup.suppressed_count()
        pc._move_axis(1, 7.5, "X-axis")
        pc._move_axis(1, 7.5, "X-axis")
        self.assertTrue(wait_until(lambda: len(self._moves()) >= 1))
        self.assertEqual(len(self._moves()), 1)
        self.assertEqual(stage_command_dedup.suppressed_count() - before, 1)
