"""The acquisition AOI and overlap are chosen once, over the image, and verified.

They fix the tile grid the run images and cannot be changed afterwards, so they
belong in the overview window — the only place the choice can be seen against
the sample it applies to — and they must be confirmed before anything is
collected. The collection dialog shows them read-only.

Three things are load-bearing and each has a test here:

* **Collect is gated.** Not on the selection alone: on the selection AND an
  explicit tick.
* **The tick does not survive a change.** Verifying settings you then edited is
  not verification, and neither is verifying a grid for a different selection.
* **The camera AOI is locked downstream.** It is what reaches the workflow, so
  a grid spaced for one field and collected with another leaves gaps the grid
  says are not there.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \\
        tests/test_acquisition_tiling_gate.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _fov_mm(pixels=2048):
    from py2flamingo.configs.config_loader import get_hardware_config

    return pixels * get_hardware_config().effective_pixel_size_um / 1000.0


def _tiles(n_x=3, n_y=3):
    from py2flamingo.models.data.overview_results import TileResult

    fov = _fov_mm()
    return [
        TileResult(
            x=4.0 + i * fov,
            y=12.0 + j * fov,
            z=16.0,
            tile_x_idx=i,
            tile_y_idx=j,
            z_stack_min=14.0,
            z_stack_max=18.0,
        )
        for i in range(n_x)
        for j in range(n_y)
    ]


class _WindowBase:
    """One QApplication and one window per class; see testing-status.md."""

    @classmethod
    def setup_class(cls):
        pytest.importorskip("PyQt5")
        from PyQt5.QtWidgets import QApplication

        from py2flamingo.views.dialogs.led_2d_overview_result import (
            LED2DOverviewResultWindow,
        )

        cls._qapp = QApplication.instance() or QApplication([])
        cls._win = LED2DOverviewResultWindow()
        panel = cls._win.left_panel
        panel.set_image(np.zeros((300, 300), dtype=np.uint16), tiles_x=3, tiles_y=3)
        panel.set_tile_stride(100, 100, 100, 100)
        tiles = _tiles()
        panel.set_tile_results(tiles)
        panel.set_tile_coordinates(
            [(t.x, t.y, t.tile_x_idx, t.tile_y_idx) for t in tiles]
        )
        panel._selected_tiles = {(t.tile_x_idx, t.tile_y_idx) for t in tiles}

    @classmethod
    def teardown_class(cls):
        win = getattr(cls, "_win", None)
        if win is not None:
            win.deleteLater()
            cls._win = None


class TestCollectIsGatedOnAnExplicitTick(_WindowBase):
    def test_selecting_tiles_is_not_enough(self):
        win = self._win
        win._verify_cb.setChecked(False)
        win._on_selection_changed()
        assert not win.collect_btn.isEnabled()

    def test_verifying_enables_it(self):
        win = self._win
        win._on_selection_changed()
        win._verify_cb.setChecked(True)
        assert win.collect_btn.isEnabled()

    def test_the_disabled_button_says_what_is_missing(self):
        win = self._win
        win._verify_cb.setChecked(False)
        win._update_collect_button()
        assert "verified" in win.collect_btn.toolTip() or "checked" in (
            win.collect_btn.toolTip()
        )

    def test_verifying_without_a_selection_does_not_enable_it(self):
        win = self._win
        selected = set(win.left_panel._selected_tiles)
        win.left_panel._selected_tiles = set()
        try:
            win._verify_cb.setChecked(True)
            win._update_collect_button()
            assert not win.collect_btn.isEnabled()
        finally:
            win.left_panel._selected_tiles = selected


class TestTheTickDoesNotSurviveAChange(_WindowBase):
    def test_changing_the_overlap_unticks_it(self):
        win = self._win
        win._verify_cb.setChecked(True)
        win._overlap_spin.setValue(win._overlap_spin.value() + 5.0)
        assert not win._verify_cb.isChecked()
        assert not win.collect_btn.isEnabled()

    def test_changing_the_aoi_unticks_it(self):
        win = self._win
        win._verify_cb.setChecked(True)
        win._aoi_combo.setCurrentIndex(
            (win._aoi_combo.currentIndex() + 1) % win._aoi_combo.count()
        )
        assert not win._verify_cb.isChecked()

    def test_changing_the_selection_unticks_it(self):
        # A tick made against a different set of tiles is not a check of this
        # one: the grid, its count and its stage-limit violations all change.
        win = self._win
        win._verify_cb.setChecked(True)
        win.left_panel._selected_tiles = {(0, 0)}
        win._on_selection_changed()
        assert not win._verify_cb.isChecked()


class TestItBlinksUntilVerified(_WindowBase):
    def test_it_is_blinking_while_unverified(self):
        win = self._win
        win._verify_cb.setChecked(False)
        win._on_verify_toggled()
        assert win._blink_timer.isActive()

    def test_it_stops_the_moment_it_is_ticked(self):
        win = self._win
        win._verify_cb.setChecked(True)
        assert not win._blink_timer.isActive()

    def test_the_alert_styling_actually_changes(self):
        win = self._win
        win._paint_tiling_panel(alert=True)
        alert = win._tiling_group.styleSheet()
        win._paint_tiling_panel(alert=False)
        assert alert != win._tiling_group.styleSheet()
        assert "e67e22" in alert


class TestThePlanIsShownBeforeItIsPaidFor(_WindowBase):
    def test_the_label_names_the_acquisition_field(self):
        win = self._win
        win._on_tiling_changed()
        assert "Acquisition field" in win._tiling_plan_label.text()

    def test_it_reports_the_tile_count_the_grid_produces(self):
        win = self._win
        win.left_panel._selected_tiles = {
            (t.tile_x_idx, t.tile_y_idx) for t in _tiles()
        }
        win._on_tiling_changed()
        assert "acquisition tile(s)" in win._tiling_plan_label.text()

    def test_a_low_overlap_is_called_out(self):
        # Below ~5% the stitcher has nothing to register on, and it cannot be
        # fixed after the run.
        win = self._win
        win._overlap_spin.setValue(1.0)
        assert "register on" in win._tiling_plan_label.text()

    def test_a_normal_overlap_is_not(self):
        win = self._win
        win._overlap_spin.setValue(20.0)
        assert "register on" not in win._tiling_plan_label.text()


class TestTheTargetFramesAreDrawn(_WindowBase):
    def test_frames_are_handed_to_the_panel(self):
        win = self._win
        win.left_panel._selected_tiles = {
            (t.tile_x_idx, t.tile_y_idx) for t in _tiles()
        }
        win._show_frames_cb.setChecked(True)
        win._refresh_target_frames()
        assert win.left_panel._target_frames

    def test_they_are_in_the_acquisition_field_not_the_overviews(self):
        # The whole point of drawing them: a short field looks nothing like the
        # square grid underneath.
        win = self._win
        win._select_aoi(2048, 1024)
        win._refresh_target_frames()
        _x, _y, w_mm, h_mm = win.left_panel._target_frames[0]
        assert h_mm == pytest.approx(w_mm / 2, rel=0.01)

    def test_turning_them_off_clears_them(self):
        win = self._win
        win._show_frames_cb.setChecked(False)
        assert win.left_panel._target_frames == []


class TestTheStageToPixelMapping(_WindowBase):
    """Fitted from the tile coordinates, not assumed.

    Display X may be inverted and the server lays tile positions by stepping
    DOWNWARD from a start corner, so either axis can run either way. A fit from
    real (mm, pixel) pairs gets every combination right without encoding any.
    """

    def test_a_tile_centre_maps_onto_its_own_square(self):
        panel = self._win.left_panel
        fit = panel._stage_to_px()
        assert fit is not None
        (sx, bx), (sy, by) = fit
        tiles = _tiles()
        for tile in tiles:
            px = bx + sx * tile.x
            py = by + sy * tile.y
            expected_x = tile.tile_x_idx * 100 + 50
            expected_y = tile.tile_y_idx * 100 + 50
            assert px == pytest.approx(expected_x, abs=1.0)
            assert py == pytest.approx(expected_y, abs=1.0)

    def test_an_inverted_x_display_flips_the_scale(self):
        from py2flamingo.views.dialogs.led_2d_overview_result import ImagePanel

        panel = ImagePanel()
        panel.set_image(np.zeros((300, 300), dtype=np.uint16), tiles_x=3, tiles_y=3)
        panel.set_tile_stride(100, 100, 100, 100)
        tiles = _tiles()
        coords = [(t.x, t.y, t.tile_x_idx, t.tile_y_idx) for t in tiles]
        panel.set_tile_coordinates(coords, invert_x=False)
        normal = panel._stage_to_px()[0][0]
        panel.set_tile_coordinates(coords, invert_x=True)
        inverted = panel._stage_to_px()[0][0]
        assert normal * inverted < 0
        panel.deleteLater()

    def test_no_coordinates_means_no_mapping_rather_than_a_guess(self):
        from py2flamingo.views.dialogs.led_2d_overview_result import ImagePanel

        panel = ImagePanel()
        assert panel._stage_to_px() is None
        panel.deleteLater()


class TestTheSettingsAreLockedDownstream:
    """Chosen and verified upstream, so the dialog shows them and does not
    let them move. The camera AOI is what reaches the workflow: a grid spaced
    for one field and collected with another leaves gaps the grid says are not
    there."""

    @classmethod
    def setup_class(cls):
        pytest.importorskip("PyQt5")
        from PyQt5.QtWidgets import QApplication

        from py2flamingo.views.dialogs.tile_collection_dialog import (
            TileCollectionDialog,
        )

        cls._qapp = QApplication.instance() or QApplication([])
        fov = _fov_mm()
        cls._dlg = TileCollectionDialog(
            left_tiles=_tiles(),
            right_tiles=[],
            left_rotation=0.0,
            right_rotation=90.0,
            overview_fov_mm=(fov, fov),
            acquisition_aoi_px=(2048, 512),
            acquisition_overlap_percent=25.0,
        )

    @classmethod
    def teardown_class(cls):
        dlg = getattr(cls, "_dlg", None)
        if dlg is not None:
            dlg.deleteLater()
            cls._dlg = None

    def test_the_overlap_is_the_one_that_was_verified(self):
        assert self._dlg._retile_overlap_spin.value() == pytest.approx(25.0)

    def test_the_overlap_cannot_be_edited(self):
        assert not self._dlg._retile_overlap_spin.isEnabled()

    def test_re_tiling_cannot_be_switched_off(self):
        assert self._dlg._retile_checkbox.isChecked()
        assert not self._dlg._retile_checkbox.isEnabled()

    def test_the_camera_aoi_is_the_verified_one(self):
        camera = self._dlg._camera_panel.get_settings()
        assert (camera["aoi_width"], camera["aoi_height"]) == (2048, 512)

    def test_the_camera_aoi_cannot_be_edited(self):
        assert self._dlg._camera_panel.aoi_locked
        assert not self._dlg._camera_panel._advanced_btn.isEnabled()

    def test_restoring_persisted_settings_cannot_replace_a_locked_aoi(self):
        # Dialog-state restore runs AFTER the lock, so without a guard in
        # set_settings the last run's AOI would quietly replace the one the
        # tile grid was just built and verified against.
        self._dlg._camera_panel.set_settings(
            {"aoi_width": 1024, "aoi_height": 1024, "exposure_us": 5000}
        )
        camera = self._dlg._camera_panel.get_settings()
        assert (camera["aoi_width"], camera["aoi_height"]) == (2048, 512)

    def test_the_grid_uses_the_locked_field(self):
        plan = self._dlg._left_plan
        assert plan is not None
        assert plan.geometry.fov_y_mm == pytest.approx(_fov_mm(512), rel=1e-6)

    def test_the_panel_says_where_the_settings_came_from(self):
        group = self._dlg._retile_checkbox.parentWidget()
        assert "verified" in group.title()


class TestAnUnlockedDialogStillWorks:
    """The other three entry points pass no plan and keep the editable panel."""

    @classmethod
    def setup_class(cls):
        pytest.importorskip("PyQt5")
        from PyQt5.QtWidgets import QApplication

        from py2flamingo.views.dialogs.tile_collection_dialog import (
            TileCollectionDialog,
        )

        cls._qapp = QApplication.instance() or QApplication([])
        fov = _fov_mm()
        cls._dlg = TileCollectionDialog(
            left_tiles=_tiles(),
            right_tiles=[],
            left_rotation=0.0,
            right_rotation=90.0,
            overview_fov_mm=(fov, fov),
        )

    @classmethod
    def teardown_class(cls):
        dlg = getattr(cls, "_dlg", None)
        if dlg is not None:
            dlg.deleteLater()
            cls._dlg = None

    def test_the_controls_are_editable(self):
        assert self._dlg._retile_overlap_spin.isEnabled()
        assert self._dlg._retile_checkbox.isEnabled()

    def test_the_camera_aoi_is_not_locked(self):
        assert not self._dlg._camera_panel.aoi_locked
        assert self._dlg._camera_panel._advanced_btn.isEnabled()


class TestTheSettingsAreRemembered:
    """Retyping the AOI and overlap every run invites a typo, and the cost of
    a typo here is a whole acquisition at the wrong tile spacing.

    The TICK is deliberately not remembered: it is a statement about this run,
    against this selection, and restoring it would mean the gate had been
    passed by a previous session.
    """

    @classmethod
    def setup_class(cls):
        pytest.importorskip("PyQt5")
        from PyQt5.QtWidgets import QApplication

        cls._qapp = QApplication.instance() or QApplication([])

    def _window(self, tmp_path, monkeypatch):
        from py2flamingo.services import window_geometry_manager as wgm
        from py2flamingo.views.dialogs.led_2d_overview_result import (
            LED2DOverviewResultWindow,
        )

        manager = wgm.WindowGeometryManager(str(tmp_path / "geometry.json"))
        monkeypatch.setattr(wgm, "_default_geometry_manager", manager, raising=False)
        window = LED2DOverviewResultWindow()
        return window, manager

    def test_the_aoi_and_overlap_come_back(self, tmp_path, monkeypatch):
        first, _ = self._window(tmp_path, monkeypatch)
        try:
            first._select_aoi(1024, 512)
            first._overlap_spin.setValue(32.0)
        finally:
            first.deleteLater()

        second, _ = self._window(tmp_path, monkeypatch)
        try:
            assert second._acquisition_aoi_px() == (1024, 512)
            assert second._acquisition_overlap_percent() == pytest.approx(32.0)
        finally:
            second.deleteLater()

    def test_the_verification_tick_is_not_restored(self):
        # Otherwise the gate would be satisfied by a decision made about a
        # different sample, on a different day.
        from py2flamingo.views.dialogs.led_2d_overview_result import (
            LED2DOverviewResultWindow,
        )

        window = LED2DOverviewResultWindow()
        try:
            assert not window._tiling_is_verified()
        finally:
            window.deleteLater()

    def test_an_aoi_this_build_no_longer_lists_is_offered_back(
        self, tmp_path, monkeypatch
    ):
        # The user chose it and the hardware accepted it. Substituting a
        # different frame silently would change what gets collected.
        from py2flamingo.services import window_geometry_manager as wgm
        from py2flamingo.views.dialogs.led_2d_overview_result import (
            LED2DOverviewResultWindow,
        )

        manager = wgm.WindowGeometryManager(str(tmp_path / "geometry.json"))
        manager.save_dialog_state(
            LED2DOverviewResultWindow.TILING_STATE_ID,
            {"aoi_px": [1600, 900], "overlap_percent": 12.0, "show_frames": True},
        )
        monkeypatch.setattr(wgm, "_default_geometry_manager", manager, raising=False)
        window = LED2DOverviewResultWindow()
        try:
            assert window._acquisition_aoi_px() == (1600, 900)
        finally:
            window.deleteLater()

    def test_a_nonsense_stored_overlap_falls_back_to_the_default(
        self, tmp_path, monkeypatch
    ):
        from py2flamingo.services import window_geometry_manager as wgm
        from py2flamingo.views.dialogs.led_2d_overview_result import (
            LED2DOverviewResultWindow,
        )

        manager = wgm.WindowGeometryManager(str(tmp_path / "geometry.json"))
        manager.save_dialog_state(
            LED2DOverviewResultWindow.TILING_STATE_ID, {"overlap_percent": 900.0}
        )
        monkeypatch.setattr(wgm, "_default_geometry_manager", manager, raising=False)
        window = LED2DOverviewResultWindow()
        try:
            assert 0.0 <= window._acquisition_overlap_percent() <= 50.0
        finally:
            window.deleteLater()
