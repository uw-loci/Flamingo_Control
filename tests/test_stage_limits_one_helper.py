"""One stage-limit helper; each mover keeps its policy; NaN never gets through.

``PositionController.move_x/y/z`` and ``move_rotation`` refuse a target outside the soft limits;
``PositionController.move_to_position``, the movement controller and the jog
panel clamp. They each had their own min/max arithmetic, and the clamp let a
NaN target through unchanged (``min(max(nan, lo), hi)`` is nan).
"""

import math
import unittest
from unittest.mock import MagicMock

from py2flamingo.utils.stage_limits import clamp_to_limits, within_limits


class TheHelper(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(clamp_to_limits(5.0, 0.0, 10.0), 5.0)
        self.assertEqual(clamp_to_limits(-1.0, 0.0, 10.0), 0.0)
        self.assertEqual(clamp_to_limits(11.0, 0.0, 10.0), 10.0)
        self.assertEqual(clamp_to_limits(11.0, None, None), 11.0)
        self.assertEqual(clamp_to_limits(11.0, None, 10.0), 10.0)

    def test_clamp_refuses_non_finite(self):
        for bad in (math.nan, math.inf, -math.inf):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                clamp_to_limits(bad, 0.0, 10.0)

    def test_within(self):
        self.assertTrue(within_limits(0.0, 0.0, 10.0))
        self.assertTrue(within_limits(10.0, 0.0, 10.0))
        self.assertFalse(within_limits(10.001, 0.0, 10.0))
        self.assertFalse(within_limits(math.nan, 0.0, 10.0))


def _controller():
    from py2flamingo.controllers.position_controller import PositionController

    config = MagicMock()
    config.get_stage_limits.return_value = {
        a: {"min": 0.0, "max": 10.0} for a in ("x", "y", "z", "r")
    }
    config.get_position_history_max_size.return_value = 10
    connection = MagicMock()
    connection.is_connected.return_value = True
    return PositionController(connection, config_service=config)


class ThePolicies(unittest.TestCase):
    def test_single_axis_moves_refuse(self):
        pc = _controller()
        for move in (pc.move_x, pc.move_y, pc.move_z, pc.move_rotation):
            for bad in (10.5, -0.5, math.nan):
                with self.subTest(move=move.__name__, bad=bad):
                    with self.assertRaises(ValueError):
                        move(bad)

    def test_move_to_position_refuses_nan_before_anything_is_sent(self):
        from py2flamingo.models.microscope import Position

        pc = _controller()
        pc._move_axis = MagicMock()
        with self.assertRaises(ValueError):
            pc.move_to_position(Position(x=math.nan, y=1.0, z=1.0, r=0.0))
        pc._move_axis.assert_not_called()
        self.assertFalse(pc._movement_lock.locked())


if __name__ == "__main__":
    unittest.main()
