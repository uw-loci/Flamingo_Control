"""MovementController clamps jogs/absolute moves to the stage soft limits.

Previously an out-of-range jog or absolute move raised ValueError, which surfaced
from the jog/nudge/absolute UI controls as an unhandled error. Now linear moves
are clamped to the nearest valid position (a jog at the limit is ignored), so the
movement controls never throw on bounds. Exercised with a fake position
controller (no Qt event loop, no hardware).

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_movement_clamp_limits.py -q
"""

import logging

from py2flamingo.controllers.movement_controller import MovementController

_LIMITS = {
    "x": {"min": 5.0, "max": 25.0},
    "y": {"min": 5.0, "max": 25.0},
    "z": {"min": 0.0, "max": 30.0},
    "r": {"min": 0.0, "max": 360.0},
}


class _Pos:
    def __init__(self, x, y, z, r=0.0):
        self.x, self.y, self.z, self.r = x, y, z, r


class _FakeSignal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _FakePC:
    def __init__(self, pos):
        self._pos = pos
        self.jogs = []
        self.moves = []
        self.waits = 0

    def get_stage_limits(self):
        return _LIMITS

    def get_current_position(self):
        return self._pos

    def jog_x(self, d):
        self.jogs.append(("x", d))

    def jog_y(self, d):
        self.jogs.append(("y", d))

    def jog_z(self, d):
        self.jogs.append(("z", d))

    def jog_rotation(self, d):
        self.jogs.append(("r", d))

    def move_x(self, v):
        self.moves.append(("x", v))

    def move_y(self, v):
        self.moves.append(("y", v))

    def move_z(self, v):
        self.moves.append(("z", v))

    def move_rotation(self, v):
        self.moves.append(("r", v))

    def wait_for_movement_complete(self, timeout=15.0):
        self.waits += 1
        return True


def _mc(pos):
    mc = MovementController.__new__(MovementController)
    mc.logger = logging.getLogger("test.mc")
    mc.motion_started = _FakeSignal()
    mc.motion_stopped = _FakeSignal()
    mc.error_occurred = _FakeSignal()
    mc._current_motion_axis = None
    mc.position_controller = _FakePC(pos)
    return mc


# ---- relative jogs ---------------------------------------------------------


def test_jog_within_limits_unchanged():
    mc = _mc(_Pos(10.0, 10.0, 10.0))
    mc.move_relative("x", 1.0, verify=False)
    assert mc.position_controller.jogs == [("x", 1.0)]


def test_jog_overrun_clamped_to_edge():
    mc = _mc(_Pos(24.9, 10.0, 10.0))
    mc.move_relative("x", 0.5, verify=False)  # 24.9 + 0.5 = 25.4 -> clamp 25.0
    assert len(mc.position_controller.jogs) == 1
    axis, delta = mc.position_controller.jogs[0]
    assert axis == "x"
    assert abs(delta - 0.1) < 1e-6  # only move to the edge


def test_jog_at_limit_ignored_cleanly():
    mc = _mc(_Pos(25.0, 10.0, 10.0))
    result = mc.move_relative("x", 0.5, verify=False)
    assert result is True
    assert mc.position_controller.jogs == []  # no move issued
    assert mc.motion_started.emitted == []  # no spurious "moving" state


def test_negative_jog_clamped_to_min():
    mc = _mc(_Pos(5.1, 10.0, 10.0))
    mc.move_relative("x", -0.5, verify=False)  # -> 4.6 clamp 5.0
    axis, delta = mc.position_controller.jogs[0]
    assert abs(delta - (-0.1)) < 1e-6


# ---- absolute moves --------------------------------------------------------


def test_absolute_overrun_clamped():
    mc = _mc(_Pos(10.0, 10.0, 10.0))
    mc.move_absolute("y", 30.0, verify=False)  # y max 25
    assert mc.position_controller.moves == [("y", 25.0)]


def test_absolute_within_limits_unchanged():
    mc = _mc(_Pos(10.0, 10.0, 10.0))
    mc.move_absolute("z", 12.0, verify=False)
    assert mc.position_controller.moves == [("z", 12.0)]
