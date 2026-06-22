"""Tests for TilingPanel live FOV (camera tile size).

The tiling panel must derive its tile size (camera FOV) from the
calibration-aware hardware config, not a hardcoded objective-specific constant.
Otherwise scan-area estimates and — critically — two-point-mode tile counts are
wrong after an objective/tube change (e.g. the panel would generate ~5x too many
heavily-overlapping tiles at 5x while still defaulting to the old 16x FOV).

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_tiling_panel_fov.py -q
"""

import pytest
from PyQt5.QtWidgets import QApplication

import py2flamingo.configs.config_loader as config_loader
from py2flamingo.views.workflow_panels.tiling_panel import TilingPanel


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeHW:
    def __init__(self, fov_um):
        self.fov_um = fov_um


def test_reads_live_hardware_fov(qapp, monkeypatch):
    # Simulate the 5x objective: 1.3 um/px * 2048 px = 2662.4 um.
    monkeypatch.setattr(
        config_loader, "get_hardware_config", lambda *a, **k: _FakeHW(2662.4)
    )
    panel = TilingPanel()
    assert panel._current_tile_size_um() == pytest.approx(2662.4)


def test_explicit_override_wins(qapp, monkeypatch):
    monkeypatch.setattr(
        config_loader, "get_hardware_config", lambda *a, **k: _FakeHW(2662.4)
    )
    panel = TilingPanel()
    panel.set_tile_size(500.0)
    assert panel._current_tile_size_um() == pytest.approx(500.0)
    # Clearing the override (0/neg) resumes the live read.
    panel.set_tile_size(0)
    assert panel._current_tile_size_um() == pytest.approx(2662.4)


def test_falls_back_when_config_unavailable(qapp, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no config")

    monkeypatch.setattr(config_loader, "get_hardware_config", boom)
    panel = TilingPanel()
    # Static last-resort default, never a crash.
    assert panel._current_tile_size_um() == pytest.approx(520.0)


def test_two_point_tile_count_scales_with_fov(qapp, monkeypatch):
    # A 5 mm x 5 mm region at 10% overlap.
    # 5x FOV (2.6624 mm) -> few tiles; old 16x FOV (0.52 mm) -> many tiles.
    monkeypatch.setattr(
        config_loader, "get_hardware_config", lambda *a, **k: _FakeHW(2662.4)
    )
    panel = TilingPanel()
    panel._overlap.setValue(10.0)
    panel.set_from_positions(0.0, 5.0, 0.0, 5.0)
    tiles_5x = panel.get_tiles_x() * panel.get_tiles_y()

    panel.set_tile_size(520.0)  # pretend old objective
    panel.set_from_positions(0.0, 5.0, 0.0, 5.0)
    tiles_16x = panel.get_tiles_x() * panel.get_tiles_y()

    assert tiles_16x > tiles_5x  # stale small FOV over-tiles the same area
