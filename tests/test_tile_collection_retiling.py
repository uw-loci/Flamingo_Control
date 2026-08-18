"""Choosing a different acquisition AOI must change the grid, not just the crop.

The overview and the acquisition do not see the same field. LED transmission
fills the sensor; the light sheet does not fill it vertically. So re-imaging at
the overview's tile centres leaves gaps wherever the sheet cannot illuminate
what the LED could see — and the overview's spacing, computed for a field the
laser does not have, is meaningless.

This is also the coupling that let a requested 20% overlap reach the stage as
0.25%: the overview grid was a one-way door into acquisition and nothing
re-derived the spacing.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \\
        tests/test_tile_collection_retiling.py -q
"""

import pytest


def _fov_mm(pixels=2048):
    from py2flamingo.configs.config_loader import get_hardware_config

    return pixels * get_hardware_config().effective_pixel_size_um / 1000.0


def _tile(x, y, ix, iy, z_min=14.0, z_max=18.0):
    from py2flamingo.models.data.overview_results import TileResult

    return TileResult(
        x=x,
        y=y,
        z=(z_min + z_max) / 2,
        tile_x_idx=ix,
        tile_y_idx=iy,
        z_stack_min=z_min,
        z_stack_max=z_max,
    )


class _Base:
    """One QApplication and one dialog per class.

    Constructing this dialog per-test and letting Qt collect it segfaults
    pytest even under QT_QPA_PLATFORM=offscreen; see testing-status.md.
    """

    TILES = "grid"

    @classmethod
    def _tiles(cls):
        fov = _fov_mm()
        if cls.TILES == "grid":
            return [
                _tile(4.0 + i * fov, 12.0 + j * fov, i, j)
                for i in range(3)
                for j in range(3)
            ]
        # An L: the top-right corner of the 3x3 block left unselected.
        keep = [(0, 0), (1, 0), (2, 0), (0, 1), (0, 2), (1, 1)]
        return [_tile(4.0 + i * fov, 12.0 + j * fov, i, j) for i, j in keep]

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
            left_tiles=cls._tiles(),
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

    def _retile(self, *, aoi=(2048, 1024), overlap=20.0, on=True):
        dlg = self._dlg
        dlg._camera_panel._aoi_width, dlg._camera_panel._aoi_height = aoi
        dlg._retile_overlap_spin.setValue(overlap)
        dlg._retile_checkbox.setChecked(on)
        dlg._apply_retiling()
        return dlg


class TestTheAcquisitionFieldDrivesTheGrid(_Base):
    def test_a_short_sheet_needs_more_rows_than_the_overview_had(self):
        dlg = self._retile(aoi=(2048, 1024))
        assert dlg._left_plan.geometry.tiles_y > dlg._left_plan.geometry.tiles_x

    def test_the_effective_tiles_are_the_planned_ones(self):
        # Everything downstream -- the size estimate, the ETA, the workflows
        # themselves -- reads these lists. If the plan stopped here it would be
        # a preview of a grid nobody collects.
        dlg = self._retile()
        assert len(dlg._left_tiles) == dlg._left_plan.acquisition_tiles
        assert len(dlg._left_tiles) != len(dlg._overview_left_tiles)

    def test_the_requested_overlap_is_what_the_step_delivers(self):
        for requested in (0.0, 10.0, 20.0, 35.0):
            g = self._retile(overlap=requested)._left_plan.geometry
            assert (g.fov_x_mm - g.step_x_mm) / g.fov_x_mm * 100 == pytest.approx(
                requested, abs=0.01
            )
            assert (g.fov_y_mm - g.step_y_mm) / g.fov_y_mm * 100 == pytest.approx(
                requested, abs=0.01
            )

    def test_a_square_aoi_gives_a_squarer_grid(self):
        g = self._retile(aoi=(2048, 2048))._left_plan.geometry
        assert g.tiles_x == g.tiles_y

    def test_more_overlap_costs_more_tiles(self):
        few = self._retile(overlap=0.0)._left_plan.acquisition_tiles
        many = self._retile(overlap=35.0)._left_plan.acquisition_tiles
        assert many > few

    def test_changing_the_aoi_alone_replans(self):
        # The user's instruction: the AOI comes from the camera panel, and the
        # plan recomputes on every change.
        wide = self._retile(aoi=(2048, 2048))._left_plan.acquisition_tiles
        short = self._retile(aoi=(2048, 1024))._left_plan.acquisition_tiles
        assert short > wide


class TestTurningItOffRestoresTheSelection(_Base):
    def test_the_original_tiles_come_back_untouched(self):
        dlg = self._retile(on=True)
        assert len(dlg._left_tiles) != len(dlg._overview_left_tiles)
        dlg = self._retile(on=False)
        assert dlg._left_tiles == dlg._overview_left_tiles

    def test_the_selection_is_never_mutated(self):
        before = list(self._dlg._overview_left_tiles)
        self._retile(on=True)
        self._retile(aoi=(1024, 1024), overlap=5.0)
        self._retile(on=False)
        assert self._dlg._overview_left_tiles == before

    def test_the_label_says_the_positions_are_the_overviews(self):
        text = self._retile(on=False)._retile_label.text()
        assert "overview's own positions" in text
        assert "gapped" in text


class TestDepthsSurviveTheNewGrid(_Base):
    def test_every_regenerated_tile_carries_a_depth(self):
        dlg = self._retile()
        assert all(t.z_stack_max > t.z_stack_min for t in dlg._left_tiles)

    def test_the_depth_is_the_overviews_not_a_default(self):
        dlg = self._retile()
        assert all(t.z_stack_min == pytest.approx(14.0) for t in dlg._left_tiles)
        assert all(t.z_stack_max == pytest.approx(18.0) for t in dlg._left_tiles)

    def test_indices_are_renumbered_for_the_new_grid(self):
        # Reusing the overview's indices is how per-tile Z ranges would attach
        # to the wrong tiles: a different field gives a different number of
        # tiles in different places.
        dlg = self._retile()
        g = dlg._left_plan.geometry
        assert max(t.tile_y_idx for t in dlg._left_tiles) == g.tiles_y - 1
        assert {(t.tile_x_idx, t.tile_y_idx) for t in dlg._left_tiles}.__len__() == len(
            dlg._left_tiles
        )

    def test_the_z_range_reaches_the_workflows(self):
        dlg = self._retile()
        for tile in dlg._left_tiles:
            z_min, z_max = dlg._get_z_range_for_tile(tile)
            assert z_max > z_min


class TestASparseSelectionIsNotFilledIn(_Base):
    TILES = "L"

    def test_tiles_over_the_unselected_corner_are_dropped(self):
        dlg = self._retile(aoi=(2048, 2048), overlap=0.0)
        assert dlg._left_plan.dropped_tiles > 0
        assert dlg._left_plan.acquisition_tiles < dlg._left_plan.grid_tiles

    def test_every_collected_tile_covers_something_selected(self):
        dlg = self._retile(aoi=(2048, 2048), overlap=0.0)
        assert all(t.covers_tiles > 0 for t in dlg._left_plan.tiles)

    def test_the_label_reports_what_was_dropped(self):
        text = self._retile(aoi=(2048, 2048), overlap=0.0)._retile_label.text()
        assert "outside the selection" in text


class TestTheLabelIsHonestAboutWhatItKnows(_Base):
    def test_it_names_both_fields_and_the_aoi_behind_them(self):
        text = self._retile()._retile_label.text()
        assert "Acquisition field" in text and "overview field" in text
        assert "2048 x 1024 px" in text

    def test_it_says_where_the_overview_field_came_from(self):
        # A caller that measured it and a fallback that assumed the full sensor
        # are not interchangeable: the field bounds the region collected.
        assert "measured by the overview" in self._retile()._retile_label.text()

    def test_an_unknown_pixel_size_disables_retiling_rather_than_guessing(self):
        dlg = self._dlg
        original = dlg._effective_pixel_size_um
        dlg._effective_pixel_size_um = lambda: None
        try:
            dlg._apply_retiling()
            assert not dlg._retile_checkbox.isEnabled()
            assert "Cannot size the acquisition field" in dlg._retile_label.text()
            # And it falls back to collecting what was selected, not nothing.
            assert dlg._left_tiles == dlg._overview_left_tiles
        finally:
            dlg._effective_pixel_size_um = original
            dlg._apply_retiling()


class TestStageLimits(_Base):
    def test_violations_are_reported_not_dropped(self):
        # Silently dropping them would collect a smaller region than the user
        # selected, with nothing saying so.
        dlg = self._dlg

        class _Settings:
            is_configured = True

            @staticmethod
            def get_stage_limits():
                return {"x": {"min": 4.5, "max": 6.0}, "y": {"min": 0.0, "max": 30.0}}

        dlg._stage_limits = _Settings.get_stage_limits
        try:
            dlg._apply_retiling()
            assert dlg._left_plan.geometry.violations
            assert "outside the stage" in dlg._retile_label.text()
        finally:
            del dlg._stage_limits
            dlg._apply_retiling()

    def test_an_unconfigured_microscope_yields_no_limits(self):
        # The placeholder limits are WIDER than the instrument, so reporting
        # "no violations" against them is worse than reporting nothing.
        assert self._dlg._stage_limits() is None
