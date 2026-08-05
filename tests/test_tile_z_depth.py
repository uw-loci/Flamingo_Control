"""Z-depth selection for tile collection.

Covers the two states offered after selecting tiles:

* per-tile Z from the acquisition (or the 90° intersection), and
* one Z range typed by the user and applied to every tile,

plus the "acquired Z" summary shown next to the manual fields, the
subfolder-layout reader that supplies it, and the LED 2D overview's
skip-the-second-90°-view quick-test option.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest

from py2flamingo.models.mip_overview import read_tile_z_range
from py2flamingo.utils.tile_z_range import summarize_acquired_z


@dataclass
class _Tile:
    """Minimal stand-in for TileResult / MIPTileResult."""

    x: float = 0.0
    y: float = 0.0
    z_stack_min: float = 0.0
    z_stack_max: float = 0.0


# --------------------------------------------------------------------------- #
# summarize_acquired_z
# --------------------------------------------------------------------------- #
def test_no_tiles_have_a_recorded_range():
    """z_stack_min == z_stack_max means "not recorded", not a zero-depth stack."""
    assert summarize_acquired_z([]) is None
    assert summarize_acquired_z([_Tile(), _Tile()]) is None


def test_uniform_range_reports_the_single_value():
    tiles = [_Tile(z_stack_min=12.5, z_stack_max=14.2) for _ in range(4)]
    z_min, z_max, uniform = summarize_acquired_z(tiles)
    assert (z_min, z_max) == (12.5, 14.2)
    assert uniform is True


def test_varying_ranges_report_the_enclosing_span():
    tiles = [
        _Tile(z_stack_min=12.5, z_stack_max=14.2),
        _Tile(z_stack_min=13.0, z_stack_max=15.1),
    ]
    z_min, z_max, uniform = summarize_acquired_z(tiles)
    assert (z_min, z_max) == (12.5, 15.1)
    assert uniform is False


def test_float_noise_still_counts_as_uniform():
    """Parsed metadata jitter well below a stage step must not read as "varies"."""
    tiles = [
        _Tile(z_stack_min=12.5, z_stack_max=14.2),
        _Tile(z_stack_min=12.50000001, z_stack_max=14.19999999),
    ]
    assert summarize_acquired_z(tiles)[2] is True


def test_tiles_without_a_range_are_ignored_not_fatal():
    tiles = [_Tile(), _Tile(z_stack_min=1.0, z_stack_max=2.0), _Tile()]
    assert summarize_acquired_z(tiles) == (1.0, 2.0, True)


def test_reversed_range_is_normalised():
    """A stack collected top-down still reports (low, high)."""
    assert summarize_acquired_z([_Tile(z_stack_min=9.0, z_stack_max=7.0)]) == (
        7.0,
        9.0,
        True,
    )


# --------------------------------------------------------------------------- #
# read_tile_z_range (subfolder layout)
# --------------------------------------------------------------------------- #
def _write_settings(folder: Path, z_start: float, z_end: float) -> None:
    (folder / "S000_t000000_X000_Y000_C02_Settings.txt").write_text(
        "<Start Position>\n"
        "  X (mm) = 6.430\n  Y (mm) = 18.140\n"
        f"  Z (mm) = {z_start}\n"
        "</Start Position>\n"
        "<End Position>\n"
        f"  Z (mm) = {z_end}\n"
        "</End Position>\n"
    )


def test_read_tile_z_range_from_settings_companion(tmp_path):
    folder = tmp_path / "20260307_041426_SmallTile3_X6.43_Y18.14"
    folder.mkdir()
    _write_settings(folder, 12.5, 14.2)
    assert read_tile_z_range(folder) == (12.5, 14.2)


def test_read_tile_z_range_without_settings_returns_none(tmp_path):
    folder = tmp_path / "X1.00_Y2.00"
    folder.mkdir()
    assert read_tile_z_range(folder) is None


def test_read_tile_z_range_zero_depth_returns_none(tmp_path):
    """A start == end acquisition carries no usable range."""
    folder = tmp_path / "X1.00_Y2.00"
    folder.mkdir()
    _write_settings(folder, 5.0, 5.0)
    assert read_tile_z_range(folder) is None


# --------------------------------------------------------------------------- #
# TileCollectionDialog Z depth modes
# --------------------------------------------------------------------------- #
@pytest.fixture
def qapp():
    pytest.importorskip("PyQt5")
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _dialog(qapp, tiles, right_tiles=()):
    from py2flamingo.views.dialogs.tile_collection_dialog import (
        TileCollectionDialog,
    )

    return TileCollectionDialog(
        left_tiles=list(tiles),
        right_tiles=list(right_tiles),
        left_rotation=0.0,
        right_rotation=90.0,
    )


def _tile_results(z_min=12.5, z_max=14.2, count=3):
    from py2flamingo.models.data.overview_results import TileResult

    return [
        TileResult(
            x=1.0 + i,
            y=2.0,
            z=(z_min + z_max) / 2,
            tile_x_idx=i,
            tile_y_idx=0,
            z_stack_min=z_min,
            z_stack_max=z_max,
        )
        for i in range(count)
    ]


def test_manual_z_overrides_every_tile(qapp):
    tiles = _tile_results()
    dialog = _dialog(qapp, tiles)

    # Default: per-tile Z, no override in play.
    assert dialog._z_override_range() is None
    assert dialog._get_z_range_for_tile(tiles[0]) == (12.5, 14.2)

    dialog._z_manual_radio.setChecked(True)
    dialog._z_start_spin.setValue(11.0)
    dialog._z_end_spin.setValue(12.0)

    assert dialog._z_override_range() == (11.0, 12.0)
    assert all(dialog._get_z_range_for_tile(t) == (11.0, 12.0) for t in tiles)
    assert dialog._get_representative_z_range() == (11.0, 12.0)


def test_manual_z_accepts_reversed_entry(qapp):
    """Typing end < start still yields an ascending range, not a negative one."""
    dialog = _dialog(qapp, _tile_results())
    dialog._z_manual_radio.setChecked(True)
    dialog._z_start_spin.setValue(14.0)
    dialog._z_end_spin.setValue(13.0)
    assert dialog._z_override_range() == (13.0, 14.0)


def test_acquired_z_seeds_the_range_without_a_scan_config(qapp):
    """MIP Overview passes no ScanConfiguration; the tiles' own Z must be used
    rather than the made-up 10 mm fallback (which mis-sizes the estimate)."""
    dialog = _dialog(qapp, _tile_results())
    assert dialog._get_representative_z_range() == (12.5, 14.2)
    assert "12.5000 → 14.2000 mm" in dialog._z_acquired_label.text()


def test_tiles_without_recorded_z_keep_the_safe_fallback(qapp):
    from py2flamingo.models.data.overview_results import TileResult

    dialog = _dialog(
        qapp, [TileResult(x=1.0, y=2.0, z=0.0, tile_x_idx=0, tile_y_idx=0)]
    )
    assert dialog._get_representative_z_range() == (0.0, 10.0)
    assert "not recorded" in dialog._z_acquired_label.text()


# --------------------------------------------------------------------------- #
# LED 2D overview: skip the second 90° view
# --------------------------------------------------------------------------- #
def _scan_config(single_rotation: bool):
    from py2flamingo.views.dialogs.led_2d_overview_dialog import (
        BoundingBox,
        ScanConfiguration,
    )

    return ScanConfiguration(
        bounding_box=BoundingBox(
            x_min=1.0, x_max=2.0, y_min=5.0, y_max=6.0, z_min=12.0, z_max=13.0
        ),
        starting_r=15.0,
        led_name="led_red",
        led_intensity=20.0,
        single_rotation=single_rotation,
    )


@pytest.mark.parametrize(
    "single_rotation, expected",
    [(True, [15.0]), (False, [15.0, 105.0])],
)
def test_single_rotation_config_controls_scanned_angles(
    single_rotation, expected, monkeypatch
):
    """The quick-test flag skips R+90 even when the tip IS calibrated."""
    pytest.importorskip("PyQt5")
    from py2flamingo.workflows import led_2d_overview_workflow as mod

    monkeypatch.setattr(
        mod.LED2DOverviewWorkflow, "_calculate_actual_fov", lambda self: 0.4
    )
    monkeypatch.setattr(
        mod.LED2DOverviewWorkflow, "_get_tip_position", lambda self: (3.0, 9.0)
    )
    monkeypatch.setattr(
        mod.LED2DOverviewWorkflow, "_load_invert_x_setting", lambda self: False
    )

    workflow = mod.LED2DOverviewWorkflow.__new__(mod.LED2DOverviewWorkflow)
    mod.LED2DOverviewWorkflow.__init__(
        workflow, config=_scan_config(single_rotation), app=None
    )
    assert workflow._rotation_angles == expected
