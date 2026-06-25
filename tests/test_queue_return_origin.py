"""Unit tests for WorkflowQueueService origin capture / return-to-origin.

Tile collection (and any queued batch) runs each workflow on the firmware, which
drives the stage. To avoid leaving the stage parked at the last tile, the queue
records the live position before the first workflow and returns there once the
whole batch is idle. These tests exercise that logic with fake stage / controller
services (no Qt event loop, no hardware, no firmware).

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_queue_return_origin.py -q
"""

import py2flamingo.services.stage_service as stage_mod
from py2flamingo.services.workflow_queue_service import WorkflowQueueService


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
        return _FakePosition(10.0, 20.0, 30.0, 90.0)

    def get_axis_position(self, axis):
        for a, val in reversed(self.moves):
            if a == axis:
                return val
        return {1: 10.0, 2: 20.0, 3: 30.0, 4: 90.0}.get(axis)

    def move_to_position(self, axis, val):
        self.moves.append((axis, val))


class _DummyController:
    def stop_workflow(self):
        pass


def _make_service(conn="conn"):
    return WorkflowQueueService(_DummyController(), connection_service=conn)


def setup_function(_):
    _FakeStageService.instances = []


def test_capture_records_live_position(monkeypatch):
    monkeypatch.setattr(stage_mod, "StageService", _FakeStageService)
    svc = _make_service()
    svc._capture_origin_position()
    assert svc._origin_position == (10.0, 20.0, 30.0, 90.0)


def test_capture_without_connection_is_skipped(monkeypatch):
    monkeypatch.setattr(stage_mod, "StageService", _FakeStageService)
    svc = _make_service(conn=None)
    svc._capture_origin_position()
    assert svc._origin_position is None
    assert _FakeStageService.instances == []  # no stage service constructed


def test_return_moves_rotation_and_all_linear_axes(monkeypatch):
    monkeypatch.setattr(stage_mod, "StageService", _FakeStageService)
    svc = _make_service()
    svc._origin_position = (10.0, 20.0, 30.0, 90.0)

    svc._return_to_origin()

    moves = _FakeStageService.instances[-1].moves
    # Rotation (axis 4) plus X/Y/Z (1/2/3) all commanded back to the origin.
    assert (4, 90.0) in moves
    assert (1, 10.0) in moves and (2, 20.0) in moves and (3, 30.0) in moves


def test_return_without_capture_is_noop(monkeypatch):
    monkeypatch.setattr(stage_mod, "StageService", _FakeStageService)
    svc = _make_service()  # _origin_position is None
    svc._return_to_origin()
    assert _FakeStageService.instances == []  # nothing constructed, nothing moved


def test_return_disabled_flag_defaults_on():
    svc = _make_service()
    assert svc._return_to_origin_after_queue is True


def test_settle_returns_true_when_axes_reach_target(monkeypatch):
    monkeypatch.setattr(stage_mod, "StageService", _FakeStageService)
    svc = _make_service()
    stage = _FakeStageService("conn")
    stage.moves = [(1, 5.0), (2, 6.0), (3, 7.0)]
    ok = svc._wait_for_stage_settle(
        stage, {1: 5.0, 2: 6.0, 3: 7.0}, poll_interval_s=0.0, timeout_s=1.0
    )
    assert ok is True
