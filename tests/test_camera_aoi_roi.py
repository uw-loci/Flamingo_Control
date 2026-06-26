"""Tests for CameraService centered-square AOI (hardware ROI) setting.

The live AOI is a real PCO hardware ROI. This firmware (per
oldcodereference/CommandCodes_Reference.txt, which matches our active
functions/command_list.txt) exposes only LEFT/TOP edges and enforces a symmetric
ROI, so left+top define a centered square. These tests exercise the edge math and
the send/confirm flow with a fake command layer (no hardware).

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_camera_aoi_roi.py -q
"""

import logging

from py2flamingo.services.camera_service import CameraCommandCode, CameraService


def _bare_service():
    svc = CameraService.__new__(CameraService)
    svc.logger = logging.getLogger("test.camera")
    return svc


# ---- edge math -------------------------------------------------------------


def test_full_frame_is_edge_one():
    assert CameraService._centered_roi_edges(2048, 2048) == (1, 1)


def test_half_frame_centered():
    assert CameraService._centered_roi_edges(2048, 1024) == (513, 513)


def test_quarter_frame_centered():
    assert CameraService._centered_roi_edges(2048, 512) == (769, 769)


# ---- set flow --------------------------------------------------------------


def test_out_of_range_rejected():
    svc = _bare_service()
    assert svc.set_centered_square_aoi(0)["success"] is False
    assert svc.set_centered_square_aoi(4096, sensor_px=2048)["success"] is False


def test_sends_left_then_top_with_edge_in_int32data0(monkeypatch):
    svc = _bare_service()
    sent = []

    def _fake_send(command_code, command_name, params=None, value=0.0):
        sent.append((command_code, params[3]))
        return {"success": True}

    monkeypatch.setattr(svc, "_send_command", _fake_send)
    monkeypatch.setattr(svc, "get_image_size", lambda: (1024, 1024))

    res = svc.set_centered_square_aoi(1024, sensor_px=2048)

    assert sent[0] == (CameraCommandCode.ROI_LEFT_SET, 513)
    assert sent[1] == (CameraCommandCode.ROI_TOP_SET, 513)
    assert res["success"] is True
    assert res["applied"] is True
    assert (res["width"], res["height"]) == (1024, 1024)


def test_left_failure_aborts_before_top(monkeypatch):
    svc = _bare_service()
    calls = []

    def _fake_send(command_code, command_name, params=None, value=0.0):
        calls.append(command_code)
        return {"success": False, "error": "nope"}

    monkeypatch.setattr(svc, "_send_command", _fake_send)
    res = svc.set_centered_square_aoi(1024, sensor_px=2048)

    assert res["success"] is False
    assert calls == [CameraCommandCode.ROI_LEFT_SET]  # top never sent


def test_readback_mismatch_flags_not_applied(monkeypatch):
    svc = _bare_service()
    monkeypatch.setattr(svc, "_send_command", lambda *a, **k: {"success": True})
    # Camera reports it stayed full-frame -> applied False (different semantics).
    monkeypatch.setattr(svc, "get_image_size", lambda: (2048, 2048))
    res = svc.set_centered_square_aoi(1024, sensor_px=2048)
    assert res["success"] is True
    assert res["applied"] is False
