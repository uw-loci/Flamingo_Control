"""The emergency stop must name the axes it halts.

The server runs a stage command only for axis 1-4 or STAGE_AXIS_ALL (0xFF);
axis 0 is STAGE_AXIS_NONE and is dropped before any stage sees it
(StageControl::exeCommandValidAxis, PIStageBase::halt, the same in 3.0.0).
Until 2026-11-19 the emergency stop sent HALT with int32Data0 = 0, so it
reached the wire and stopped nothing.
"""

import unittest
from unittest.mock import MagicMock

from py2flamingo.core.command_codes import StageCommands
from py2flamingo.services.stage_service import AxisCode


class TestEmergencyStopAxis(unittest.TestCase):
    def test_halt_goes_to_every_axis(self):
        from py2flamingo.controllers.position_controller import PositionController

        connection = MagicMock()
        connection.is_connected.return_value = True
        controller = PositionController(connection, config_service=MagicMock())
        controller.emergency_stop()

        sent = [c.args[0] for c in connection.send_command.call_args_list]
        halts = [c for c in sent if getattr(c, "code", None) == StageCommands.HALT]
        self.assertEqual(len(halts), 1)
        params = halts[0].parameters["params"]
        self.assertEqual(params[3], AxisCode.ALL)  # int32Data0
        self.assertEqual(AxisCode.ALL, 0xFF)

    def test_halt_is_0x6003_not_home(self):
        self.assertEqual(int(StageCommands.HALT), 0x6003)


if __name__ == "__main__":
    unittest.main()
