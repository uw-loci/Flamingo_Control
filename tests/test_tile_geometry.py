"""Tile-geometry parity with the server C++ (CheckStackTile.cpp).

Values are hand-derived from the server formula so a divergence in the Python
port (ceil vs floor, ROI = range + FOV, centered grid, overlap clamp, per-tile
hard-limit check) is caught here rather than only on the rig.
"""

from __future__ import annotations

import math

import pytest

from py2flamingo.utils.tile_geometry import (
    OVERLAP_PERCENT_MAX,
    client_tile_count_1d,
    compute_tile_geometry,
)

# Real rig FOV from the logs: 2048 px * 1.0475 um / 6.21x-ish -> 2.1454 mm.
FOV = 2.1454


class TestCountAndRoi:
    def test_basic_4x4_at_25pct(self):
        # start/end from the July-21 beads run; 25% overlap (the real
        # Workflow.txt value). roiDelta = range + FOV; tiles = ceil(roiDelta/step).
        g = compute_tile_geometry(
            start_x=5.707,
            end_x=9.500,
            start_y=21.398,
            end_y=25.000,
            start_z=16.106,
            end_z=20.969,
            fov_x_mm=FOV,
            fov_y_mm=FOV,
            x_overlap_percent=25.0,
            y_overlap_percent=25.0,
        )
        assert g.tiles_x == 4
        assert g.tiles_y == 4
        assert g.total_tiles == 16
        assert g.roi_delta_x_mm == pytest.approx(abs(9.500 - 5.707) + FOV, abs=1e-6)
        assert g.roi_delta_y_mm == pytest.approx(abs(25.0 - 21.398) + FOV, abs=1e-6)
        assert g.step_x_mm == pytest.approx(FOV * 0.75, abs=1e-6)
        assert g.delta_z_mm == pytest.approx(abs(16.106 - 20.969), abs=1e-6)
        assert len(g.positions) == 16

    def test_ceil_not_floor(self):
        # roiDelta/step = 2.82 -> ceil 3 (a floor+1 port would give 2).
        g = compute_tile_geometry(
            start_x=5.707,
            end_x=9.500,
            start_y=0.0,
            end_y=0.0,
            start_z=0.0,
            end_z=0.0,
            fov_x_mm=FOV,
            fov_y_mm=FOV,
            x_overlap_percent=2.0,
            y_overlap_percent=2.0,
        )
        expected = math.ceil((abs(9.5 - 5.707) + FOV) / (FOV * 0.98))
        assert g.tiles_x == expected == 3
        # start_y == end_y -> single row, full-FOV pitch.
        assert g.tiles_y == 1
        assert g.step_y_mm == pytest.approx(FOV, abs=1e-9)


class TestOverlapClamp:
    def test_overlap_clamped_to_50(self):
        g = compute_tile_geometry(
            0.0,
            5.0,
            0.0,
            5.0,
            0.0,
            1.0,
            FOV,
            FOV,
            x_overlap_percent=90.0,
            y_overlap_percent=90.0,
        )
        assert g.x_overlap_percent == OVERLAP_PERCENT_MAX == 50.0
        assert g.step_x_mm == pytest.approx(FOV * 0.5, abs=1e-9)

    def test_negative_overlap_clamped_to_zero(self):
        g = compute_tile_geometry(
            0.0,
            5.0,
            0.0,
            5.0,
            0.0,
            1.0,
            FOV,
            FOV,
            x_overlap_percent=-10.0,
            y_overlap_percent=-10.0,
        )
        assert g.x_overlap_percent == 0.0
        assert g.step_x_mm == pytest.approx(FOV, abs=1e-9)


class TestHardLimits:
    def test_corner_tile_past_y_limit_flags(self):
        # Position B at Y=25.0 == the Y hard-limit max. Because start/end are
        # tile centers and the grid is centered over the half-FOV-inflated ROI,
        # the top row's tile center lands ~0.34 mm past 25.0 -> violation.
        g = compute_tile_geometry(
            start_x=5.707,
            end_x=9.500,
            start_y=21.398,
            end_y=25.000,
            start_z=16.106,
            end_z=20.969,
            fov_x_mm=FOV,
            fov_y_mm=FOV,
            x_overlap_percent=25.0,
            y_overlap_percent=25.0,
            hard_limit_min_x=1.0,
            hard_limit_max_x=12.31,
            hard_limit_min_y=5.0,
            hard_limit_max_y=25.0,
        )
        assert g.has_limit_errors
        y_viol = [v for v in g.violations if v.axis == "y"]
        assert y_viol, "expected the top row to exceed the Y hard limit"
        assert y_viol[0].kind == "max"
        assert y_viol[0].position_mm > 25.0

    def test_no_violation_when_within_limits(self):
        g = compute_tile_geometry(
            start_x=5.0,
            end_x=8.0,
            start_y=10.0,
            end_y=13.0,
            start_z=0.0,
            end_z=1.0,
            fov_x_mm=FOV,
            fov_y_mm=FOV,
            x_overlap_percent=25.0,
            y_overlap_percent=25.0,
            hard_limit_min_x=1.0,
            hard_limit_max_x=12.31,
            hard_limit_min_y=5.0,
            hard_limit_max_y=25.0,
        )
        assert not g.has_limit_errors

    def test_no_limits_given_means_no_violations(self):
        g = compute_tile_geometry(
            5.707,
            9.5,
            21.398,
            25.0,
            16.106,
            20.969,
            FOV,
            FOV,
            25.0,
            25.0,
        )
        assert g.violations == []


class TestClientVsServerCounts:
    def test_client_count_is_floor_plus_one(self):
        # 3.793 / (2.1454*0.9) = 1.96 -> floor 1 -> +1 = 2.
        assert client_tile_count_1d(3.793, FOV, 10.0) == 2

    def test_client_and_server_can_differ(self):
        # Same corners/overlap: client floor+1 vs server ceil((range+FOV)/step).
        client = client_tile_count_1d(abs(9.5 - 5.707), FOV, 10.0)
        g = compute_tile_geometry(5.707, 9.5, 0.0, 0.0, 0.0, 0.0, FOV, FOV, 10.0, 10.0)
        assert client == 2
        assert g.tiles_x == 4  # server counts the half-FOV overhang + ceil
        assert client != g.tiles_x

    def test_zero_range_is_one_tile_both(self):
        assert client_tile_count_1d(0.0, FOV, 10.0) == 1
        g = compute_tile_geometry(5.0, 5.0, 0.0, 0.0, 0.0, 0.0, FOV, FOV, 10.0, 10.0)
        assert g.tiles_x == 1


class TestSingleTile:
    def test_single_position_is_one_tile(self):
        g = compute_tile_geometry(
            7.0,
            7.0,
            12.0,
            12.0,
            5.0,
            6.0,
            FOV,
            FOV,
            25.0,
            25.0,
        )
        assert g.tiles_x == 1 and g.tiles_y == 1
        assert g.total_tiles == 1
        assert g.positions == [g.positions[0]]
