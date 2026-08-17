"""The overview grid and the acquisition grid are not the same grid.

LED transmission fills the whole sensor; the light sheet does not fill it
vertically. So the acquisition field is smaller than the overview field and
generally NOT square, and re-imaging at the overview's tile centres leaves gaps
wherever the laser cannot illuminate what the LED could see.

Treating them as one grid is also how a requested 20% overlap reached the stage
as 0.25%: the overview grid was a one-way door into acquisition and nothing
re-derived the spacing. `plan_acquisition_from_overview` uses the overview only
to bound a REGION, then tiles that region from the acquisition field and its own
overlap.

Run: python3 -m pytest tests/test_acquisition_plan_from_overview.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.utils.tile_geometry import (  # noqa: E402
    plan_acquisition_from_overview,
    selection_region_mm,
)

PIXEL_UM = 1.0475
OVERVIEW_FOV = 2048 * PIXEL_UM / 1000.0  # 2.1453 mm — LED fills the sensor
SHEET_FOV_Y = 1024 * PIXEL_UM / 1000.0  # 1.0726 mm — sheet is short vertically
LIMITS = {"x": {"min": 1.0, "max": 12.31}, "y": {"min": 0.0, "max": 30.0}}


def plan(centres, *, acq_x=OVERVIEW_FOV, acq_y=SHEET_FOV_Y, overlap=20.0, limits=None):
    return plan_acquisition_from_overview(
        centres,
        overview_fov_x_mm=OVERVIEW_FOV,
        overview_fov_y_mm=OVERVIEW_FOV,
        acquisition_fov_x_mm=acq_x,
        acquisition_fov_y_mm=acq_y,
        overlap_percent=overlap,
        stage_limits=limits,
    )


def _grid(nx, ny, pitch=OVERVIEW_FOV * 0.9, x0=2.0, y0=11.7):
    return [(x0 + i * pitch, y0 + j * pitch) for i in range(nx) for j in range(ny)]


class TestTheRegionUsesTileCentres:
    def test_the_region_runs_half_a_field_beyond_the_outer_centres(self):
        # Tile positions are centres, so the imaged span is wider than the
        # span of the centres. Getting this wrong clips the sample edge.
        lo, hi = selection_region_mm([2.0, 4.0], 1.0)
        assert (lo, hi) == pytest.approx((1.5, 4.5))

    def test_one_tile_still_covers_a_full_field(self):
        lo, hi = selection_region_mm([5.0], 2.0)
        assert (hi - lo) == pytest.approx(2.0)

    def test_no_tiles_is_not_an_exception(self):
        assert selection_region_mm([], 1.0) == (0.0, 0.0)


class TestTheAcquisitionFieldDrivesTheGrid:
    def test_a_short_sheet_needs_more_tiles_in_that_axis(self):
        # The whole point: the sheet covers half the height, so Y needs roughly
        # twice the tiles X does over a square region.
        result = plan(_grid(3, 3))
        assert result.geometry.tiles_y > result.geometry.tiles_x

    def test_the_acquisition_grid_is_not_the_overview_grid(self):
        # The regression this function exists to prevent.
        centres = _grid(3, 3)
        result = plan(centres)
        assert result.acquisition_tiles != len(centres)
        assert result.geometry.fov_y_mm == pytest.approx(SHEET_FOV_Y)

    def test_the_requested_overlap_is_what_the_step_delivers(self):
        for requested in (0.0, 10.0, 20.0, 35.0):
            g = plan(_grid(3, 3), overlap=requested).geometry
            achieved_x = (g.fov_x_mm - g.step_x_mm) / g.fov_x_mm * 100.0
            achieved_y = (g.fov_y_mm - g.step_y_mm) / g.fov_y_mm * 100.0
            assert achieved_x == pytest.approx(requested, abs=0.01)
            assert achieved_y == pytest.approx(requested, abs=0.01)

    def test_more_overlap_means_more_tiles(self):
        assert (
            plan(_grid(3, 3), overlap=35.0).acquisition_tiles
            > plan(_grid(3, 3), overlap=0.0).acquisition_tiles
        )

    def test_a_square_acquisition_field_gives_a_squarer_grid(self):
        square = plan(_grid(3, 3), acq_y=OVERVIEW_FOV).geometry
        assert square.tiles_x == square.tiles_y


class TestSmallSelections:
    def test_a_single_overview_tile_still_yields_tiles(self):
        result = plan([(5.0, 15.0)])
        assert result.acquisition_tiles >= 1
        assert result.geometry.tiles_y >= 2  # one LED field needs two sheet rows

    def test_a_region_narrower_than_one_field_collapses_to_one_tile(self):
        # Must not invert the span and produce a nonsense grid.
        result = plan([(5.0, 15.0)], acq_x=10.0, acq_y=10.0)
        assert result.acquisition_tiles == 1

    def test_that_single_tile_sits_in_the_middle_of_the_region(self):
        result = plan([(5.0, 15.0)], acq_x=10.0, acq_y=10.0)
        x, y = result.geometry.positions[0]
        assert x == pytest.approx(5.0, abs=1e-6)
        assert y == pytest.approx(15.0, abs=1e-6)


class TestStageLimits:
    def test_tiles_beyond_a_hard_limit_are_reported_not_dropped(self):
        # Silently dropping them would collect a smaller region than the user
        # selected, with nothing saying so.
        result = plan(_grid(3, 3, x0=11.5), limits=LIMITS)
        assert result.geometry.has_limit_errors
        assert any(v.axis == "x" for v in result.geometry.violations)

    def test_a_selection_inside_the_limits_reports_none(self):
        assert not plan(_grid(2, 2, x0=4.0, y0=12.0), limits=LIMITS).geometry.violations

    def test_absent_limits_are_not_treated_as_zero(self):
        assert not plan(_grid(2, 2), limits=None).geometry.violations

    def test_a_malformed_limits_dict_is_ignored_rather_than_fatal(self):
        assert not plan(_grid(2, 2), limits={"x": {"min": "nope"}}).geometry.violations


class TestTheDescription:
    def test_it_names_both_grids_and_both_fields(self):
        text = plan(_grid(3, 3)).describe()
        assert "overview tile(s)" in text and "acquisition tile(s)" in text
        assert "2.1453x1.0726" in text  # the non-square acquisition field
        assert "20.0/20.0% overlap" in text


class _OverviewTile:
    """Duck-type of TileResult: position plus the depth Collect Tiles measured."""

    def __init__(self, x, y, z_min, z_max):
        self.x, self.y = x, y
        self.z_stack_min, self.z_stack_max = z_min, z_max


class TestPerTileZSurvivesARegeneratedGrid:
    """Changing the AOI must not discard the depths the overview measured.

    Per-tile Z ranges are the product of Collect Tiles, and the Z edges are what
    the laser acquisition sweeps. A regenerated XY grid has none of the
    overview's tile indices, so the depths have to be carried across by
    footprint, not by index.
    """

    def _tiles(self):
        # Two overview tiles side by side at different depths.
        return [
            _OverviewTile(4.0, 12.0, 14.0, 18.0),
            _OverviewTile(4.0 + OVERVIEW_FOV, 12.0, 20.0, 26.0),
        ]

    def test_a_tile_inherits_the_depth_of_the_overview_tile_it_covers(self):
        result = plan(
            self._tiles(), acq_x=OVERVIEW_FOV, acq_y=OVERVIEW_FOV, overlap=0.0
        )
        leftmost = min(result.tiles, key=lambda t: t.x_mm)
        assert leftmost.z_min_mm == pytest.approx(14.0)
        assert leftmost.z_max_mm == pytest.approx(18.0)

    def test_a_tile_straddling_two_depths_spans_both(self):
        # The union, not one of them and not an average: cutting the stack short
        # where two depths disagree loses data exactly where the sample changes,
        # and it cannot be recovered without re-running the sample.
        result = plan(
            self._tiles(), acq_x=OVERVIEW_FOV, acq_y=OVERVIEW_FOV, overlap=0.0
        )
        straddlers = [t for t in result.tiles if t.source_tiles >= 2]
        assert straddlers, "expected at least one tile covering both overview tiles"
        for tile in straddlers:
            assert tile.z_min_mm == pytest.approx(14.0)
            assert tile.z_max_mm == pytest.approx(26.0)

    def test_every_planned_tile_gets_a_depth(self):
        result = plan(self._tiles())
        assert len(result.tiles) == result.acquisition_tiles
        assert all(t.z_max_mm > t.z_min_mm for t in result.tiles)

    def test_a_finer_acquisition_grid_still_inherits(self):
        # The real case: many small acquisition tiles under few overview tiles.
        result = plan(self._tiles(), overlap=20.0)
        assert all(t.z_from_overview for t in result.tiles)

    def test_tiles_with_no_overview_coverage_are_flagged_not_hidden(self):
        # Their depth is a fallback, not a measurement, and the Z edges drive
        # the laser sweep — so they must be nameable, not just counted.
        tiles = [_OverviewTile(4.0, 12.0, 14.0, 18.0)]
        result = plan_acquisition_from_overview(
            tiles,
            overview_fov_x_mm=OVERVIEW_FOV,
            overview_fov_y_mm=OVERVIEW_FOV,
            acquisition_fov_x_mm=0.2,
            acquisition_fov_y_mm=0.2,
            overlap_percent=0.0,
            z_min_mm=1.0,
            z_max_mm=2.0,
        )
        assert all(t.z_from_overview for t in result.tiles)
        assert result.tiles_without_overview_z == []

    def test_plain_centres_without_depth_fall_back_to_the_given_range(self):
        result = plan_acquisition_from_overview(
            [(4.0, 12.0)],
            overview_fov_x_mm=OVERVIEW_FOV,
            overview_fov_y_mm=OVERVIEW_FOV,
            acquisition_fov_x_mm=OVERVIEW_FOV,
            acquisition_fov_y_mm=OVERVIEW_FOV,
            overlap_percent=0.0,
            z_min_mm=3.0,
            z_max_mm=9.0,
        )
        assert result.tiles[0].z_min_mm == pytest.approx(3.0)
        assert result.tiles[0].z_max_mm == pytest.approx(9.0)
        assert result.tiles_without_overview_z == result.tiles
