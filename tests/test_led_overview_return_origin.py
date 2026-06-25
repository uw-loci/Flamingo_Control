"""Unit tests for LED2DOverviewWorkflow origin capture / return-to-origin.

A scan should never leave the stage parked at the last tile: the workflow records
the live stage position *before* the first move and returns there at the end
(completion, cancel, or error). These tests exercise that logic with fake stage /
movement services (no Qt event loop, no hardware).

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_led_overview_return_origin.py -q
"""

from types import SimpleNamespace

import py2flamingo.services.stage_service as stage_mod
from py2flamingo.workflows.led_2d_overview_workflow import LED2DOverviewWorkflow


class _FakePosition:
    def __init__(self, x, y, z, r):
        self.x, self.y, self.z, self.r = x, y, z, r


class _FakeStageService:
    """Reports a fixed start position and settles immediately at any target."""

    instances = []

    def __init__(self, _conn):
        self.moves = []
        _FakeStageService.instances.append(self)

    def get_position(self):
        return _FakePosition(1.0, 2.0, 3.0, 4.0)

    def get_axis_position(self, axis):
        # Settle immediately wherever the last move commanded.
        for a, val in reversed(self.moves):
            if a == axis:
                return val
        return {1: 1.0, 2: 2.0, 3: 3.0}.get(axis, 0.0)

    def move_to_position(self, axis, val):
        self.moves.append((axis, val))


class _FakeSignal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _FakeMovementController:
    def __init__(self):
        self.position_changed = _FakeSignal()
        self.r_moves = []

    def move_absolute(self, axis, val):
        self.r_moves.append((axis, val))


def _bare_workflow():
    wf = LED2DOverviewWorkflow.__new__(LED2DOverviewWorkflow)
    wf._cancelled = False
    wf._movement_controller = None
    wf._last_xyz = [0.0, 0.0, 0.0]
    wf._last_pos_broadcast = 0.0
    wf._pos_broadcast_interval_s = 0.1
    wf._rotation_angles = [0.0]
    wf._current_rotation_idx = 0
    wf._origin_position = None
    wf._origin_restored = False
    mc = _FakeMovementController()
    wf._app = SimpleNamespace(
        connection_service=None,
        sample_view=SimpleNamespace(movement_controller=mc, camera_controller=object()),
        position_controller=None,
    )
    return wf


def setup_function(_):
    _FakeStageService.instances = []


def test_capture_origin_records_live_position(monkeypatch):
    monkeypatch.setattr(stage_mod, "StageService", _FakeStageService)
    wf = _bare_workflow()
    wf._capture_origin_position()
    assert wf._origin_position == (1.0, 2.0, 3.0, 4.0)
    assert wf._origin_restored is False


def test_capture_origin_handles_none(monkeypatch):
    class _NoPos(_FakeStageService):
        def get_position(self):
            return None

    monkeypatch.setattr(stage_mod, "StageService", _NoPos)
    wf = _bare_workflow()
    wf._capture_origin_position()
    assert wf._origin_position is None  # auto-return will be skipped


def test_return_to_origin_moves_all_axes_and_restores_rotation(monkeypatch):
    monkeypatch.setattr(stage_mod, "StageService", _FakeStageService)
    wf = _bare_workflow()
    wf._origin_position = (1.0, 2.0, 3.0, 4.0)

    wf._return_to_origin()

    mc = wf._app.sample_view.movement_controller
    # Rotation returned via movement_controller.
    assert mc.r_moves == [("r", 4.0)]
    # X/Y/Z all commanded back to the captured origin.
    moves = _FakeStageService.instances[-1].moves
    assert (1, 1.0) in moves and (2, 2.0) in moves and (3, 3.0) in moves
    # Final UI emit carries the restored x/y/z/r.
    assert mc.position_changed.emitted[-1] == (1.0, 2.0, 3.0, 4.0)
    assert wf._origin_restored is True


def test_return_to_origin_is_idempotent(monkeypatch):
    monkeypatch.setattr(stage_mod, "StageService", _FakeStageService)
    wf = _bare_workflow()
    wf._origin_position = (1.0, 2.0, 3.0, 4.0)

    wf._return_to_origin()
    first = len(_FakeStageService.instances)
    wf._return_to_origin()  # guard should make this a no-op
    assert len(_FakeStageService.instances) == first


def test_return_to_origin_without_capture_is_noop(monkeypatch):
    monkeypatch.setattr(stage_mod, "StageService", _FakeStageService)
    wf = _bare_workflow()  # _origin_position is None
    wf._return_to_origin()
    # No StageService constructed, nothing moved, but guard is set.
    assert _FakeStageService.instances == []
    assert wf._origin_restored is True
