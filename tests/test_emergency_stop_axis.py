"""The emergency stop must name the axes it halts.

The server runs a stage command only for axis 1-4 or STAGE_AXIS_ALL (0xFF);
axis 0 is STAGE_AXIS_NONE and is dropped before any stage sees it
(StageControl::exeCommandValidAxis, PIStageBase::halt, the same in 3.0.0).
Until 2026-11-19 the emergency stop sent HALT with int32Data0 = 0, so it
reached the wire and stopped nothing.

Checked against the synthetic microscope, which drops HALT for axis 0 as the
server does.
"""

import unittest
from unittest.mock import MagicMock

from py2flamingo.core.command_codes import StageCommands
from py2flamingo.services.stage_service import AxisCode
from tests.synthetic_scope_case import SyntheticScopeCase, wait_until


class TestEmergencyStopAxis(SyntheticScopeCase):
    def test_halt_goes_to_every_axis(self):
        from py2flamingo.controllers.position_controller import PositionController

        controller = PositionController(self.connection, config_service=MagicMock())
        controller.emergency_stop()

        self.assertTrue(
            wait_until(lambda: self.scope.received(StageCommands.HALT)),
            "HALT never reached the scope",
        )
        halts = self.scope.received(StageCommands.HALT)
        self.assertEqual(len(halts), 1)
        self.assertEqual(halts[0].axis, AxisCode.ALL)  # int32Data0
        self.assertEqual(AxisCode.ALL, 0xFF)

    def test_halt_is_0x6003_not_home(self):
        self.assertEqual(int(StageCommands.HALT), 0x6003)


if __name__ == "__main__":
    unittest.main()
