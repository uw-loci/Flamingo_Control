"""Regression test for MovementController.move_relative(verify=True).

The underlying position_controller jog is asynchronous: it sends the command and
a background thread releases the movement lock only after the hardware confirms
the new position. move_relative must therefore BLOCK when verify=True, otherwise a
caller stepping the stage in a loop (the XY Pixel Calibrator) hits "Movement
already in progress" and reads stale positions. These tests pin that behaviour
with a fake position controller (no Qt, no hardware).

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_move_relative_verify_waits.py -q
"""

from py2flamingo.controllers.movement_controller import MovementController


class _FakeSignal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _FakePositionController:
    def __init__(self):
        self.jogs = []
        self.waits = 0

    def jog_x(self, d):
        self.jogs.append(("x", d))

    def jog_y(self, d):
        self.jogs.append(("y", d))

    def jog_z(self, d):
        self.jogs.append(("z", d))

    def jog_rotation(self, d):
        self.jogs.append(("r", d))

    def wait_for_movement_complete(self, timeout=15.0):
        self.waits += 1
        return True


def _controller():
    # Bypass __init__ (QObject wiring); shadow the pyqtSignals with fakes so the
    # method can emit without a live QObject.
    mc = MovementController.__new__(MovementController)
    mc.motion_started = _FakeSignal()
    mc.motion_stopped = _FakeSignal()
    mc.error_occurred = _FakeSignal()
    mc._current_motion_axis = None
    mc.position_controller = _FakePositionController()
    return mc


def test_verify_true_waits_for_completion():
    mc = _controller()
    mc.move_relative("x", 0.166, verify=True)
    assert mc.position_controller.jogs == [("x", 0.166)]
    assert mc.position_controller.waits == 1  # blocked until the move finished


def test_verify_false_does_not_wait():
    mc = _controller()
    mc.move_relative("y", -0.333, verify=False)
    assert mc.position_controller.jogs == [("y", -0.333)]
    assert mc.position_controller.waits == 0  # fire-and-forget preserved


def test_default_is_verify_true():
    mc = _controller()
    mc.move_relative("x", 0.1)
    assert mc.position_controller.waits == 1
