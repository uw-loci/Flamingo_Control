"""Tests for StageControlView._load_optics.

The stage control view's FOV drives jog step sizes. It must come from the
calibration-aware hardware config (calibration > scope > YAML), NOT a private
ScopeSettings.txt parser that recomputed FOV from hardcoded sensor/pixel
constants and a 25.0x default magnification (which ignored saved calibrations
and went stale on an objective swap).

We exercise _load_optics on a bare instance (no Qt UI / hardware) with a stubbed
hardware config.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_stage_control_optics.py -q
"""

import logging

import py2flamingo.configs.config_loader as config_loader
from py2flamingo.views.stage_control_view import StageControlView


class _StubHW:
    def __init__(self, fov_mm, mag, px_um, sensor_px, source):
        self.fov_mm = fov_mm
        self.system_magnification = mag
        self.effective_pixel_size_um = px_um
        self.sensor_width_px = sensor_px
        self.optics_source = source


def _bare_view():
    view = StageControlView.__new__(StageControlView)
    view.logger = logging.getLogger("test_stage_control")
    return view


def test_load_optics_pulls_fov_from_config(monkeypatch):
    # 5x objective with a measured calibration -> FOV 2.6624 mm.
    hw = _StubHW(
        fov_mm=2.6624,
        mag=5.0,
        px_um=1.3,
        sensor_px=2048,
        source="calibration",
    )
    monkeypatch.setattr(config_loader, "get_hardware_config", lambda *a, **k: hw)

    view = _bare_view()
    view._load_optics()

    assert view.fov_mm == 2.6624
    assert view.magnification == 5.0
    assert view.pixel_size_um == 1.3
    assert view.sensor_pixels == 2048


def test_calibration_changes_fov_and_thus_jog(monkeypatch):
    # A larger measured pixel size yields a larger FOV (and larger jog steps),
    # which the old recompute-from-magnification path could not represent.
    hw = _StubHW(fov_mm=5.32, mag=2.5, px_um=2.6, sensor_px=2048, source="calibration")
    monkeypatch.setattr(config_loader, "get_hardware_config", lambda *a, **k: hw)

    view = _bare_view()
    view._load_optics()
    assert view.fov_mm == 5.32

    # refresh_optics re-reads after the config changes (e.g. new calibration).
    hw2 = _StubHW(fov_mm=2.6624, mag=5.0, px_um=1.3, sensor_px=2048, source="scope")
    monkeypatch.setattr(config_loader, "get_hardware_config", lambda *a, **k: hw2)
    view.refresh_optics()
    assert view.fov_mm == 2.6624
