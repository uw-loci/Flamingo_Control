"""The Z sweep must not travel further than the range it is sweeping.

Measured on the rig (`flamingo_20260817_143602.log`, 140 tiles): Z travel is
**10.0 s of the 16.28 s tile — 61%** — because every tile traverses the whole
bounding-box depth at the stage's 1 mm/s. That makes the travel distance the
number worth guarding, and it is not the same as the plane count.

Two facts these pin:

1. `max_planes=1` used to return `z_min` in BOTH directions. `reverse()` on a
   one-element list is a no-op, so the caller's serpentine still alternated
   `z_start` to `z_max` and every other tile drove the full depth to the top and
   straight back down to capture — 20 mm of travel on a 10 mm box. The fastest
   setting on the spinbox was the slowest thing the scan could do.
2. Subsampling always spans the full range, so **cutting the plane count does
   not cut the travel**. That is why `max_z_planes` is nearly useless as a speed
   control, and the test says so out loud rather than leaving the next person to
   rediscover it.

Run: python3 -m pytest tests/test_led_overview_z_travel.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.workflows.led_2d_overview_workflow import (  # noqa: E402
    LED2DOverviewWorkflow,
)

Z_MIN, Z_MAX, Z_STEP = 14.21, 24.21, 0.25
DEPTH = Z_MAX - Z_MIN  # 10.0 mm, the real bounding box from the 08-17 run


def sweep(max_planes, ascending=True):
    return LED2DOverviewWorkflow._z_sweep_positions(
        Z_MIN, Z_MAX, Z_STEP, ascending=ascending, max_planes=max_planes
    )


def travel_mm(max_planes, ascending=True):
    """Distance the stage covers walking the sweep, ignoring how it got there."""
    positions = sweep(max_planes, ascending)
    return sum(abs(b - a) for a, b in zip(positions, positions[1:]))


class TestASinglePlaneIsTheCentre:
    def test_one_plane_sits_mid_range_not_on_the_bottom_edge(self):
        # Least worst-case defocus. z_min is the edge of the box.
        (only,) = sweep(1)
        assert only == pytest.approx((Z_MIN + Z_MAX) / 2, abs=Z_STEP)

    def test_one_plane_is_the_same_in_both_directions(self):
        # It has to be: a symmetric single point gives the serpentine nothing to
        # alternate between, which is what stops the travel doubling.
        assert sweep(1, ascending=True) == sweep(1, ascending=False)

    def test_one_plane_costs_no_sweep_travel(self):
        assert travel_mm(1) == pytest.approx(0.0)

    def test_the_start_of_an_ascending_and_descending_sweep_agree_at_one_plane(self):
        # The caller sets z_start from positions[0]. If these differed, alternate
        # tiles would drive to the far end of the box and back.
        assert sweep(1, ascending=True)[0] == sweep(1, ascending=False)[0]


class TestTheTilePlanCannotDisagreeWithItself:
    """z_start and the sweep come from one function, so they cannot diverge.

    This is where the 20 mm travel actually happened: the sweep said "centre"
    while the caller independently said "z_max". Testing `_z_sweep_positions`
    alone could never catch it — both directions returned the same single
    position, which looks perfectly correct in isolation.
    """

    @staticmethod
    def _plan(planes, ascending):
        return LED2DOverviewWorkflow._tile_z_plan(
            Z_MIN, Z_MAX, Z_STEP, ascending, planes
        )

    @pytest.mark.parametrize("planes", [1, 2, 3, 10])
    @pytest.mark.parametrize("ascending", [True, False])
    def test_the_start_is_the_sweeps_own_first_position(self, planes, ascending):
        z_start, z_values = self._plan(planes, ascending)
        assert z_start == z_values[0]

    @pytest.mark.parametrize("planes", [1, 2, 3, 10])
    def test_a_serpentine_run_never_exceeds_one_depth_per_tile(self, planes):
        """Walk several tiles the way the scan does and total the Z distance.

        Includes the repositioning move between tiles, which is exactly the
        term the old code got wrong.
        """
        here = None
        total = 0.0
        for tile in range(6):
            z_start, z_values = self._plan(planes, ascending=(tile % 2 == 0))
            if here is not None:
                total += abs(z_start - here)  # reposition between tiles
            total += sum(abs(b - a) for a, b in zip(z_values, z_values[1:]))
            here = z_values[-1]
        assert total <= 6 * DEPTH + 1e-9, (
            f"{planes} plane(s): {total:.1f} mm over 6 tiles, "
            f"budget {6 * DEPTH:.1f} mm — the sweep is repositioning between tiles"
        )

    def test_a_single_plane_costs_no_z_travel_at_all(self):
        # The whole point of centring it: nothing to sweep, nothing to reposition.
        here = None
        total = 0.0
        for tile in range(6):
            z_start, z_values = self._plan(1, ascending=(tile % 2 == 0))
            if here is not None:
                total += abs(z_start - here)
            total += sum(abs(b - a) for a, b in zip(z_values, z_values[1:]))
            here = z_values[-1]
        assert total == pytest.approx(0.0)


class TestSerpentineEndpoints:
    @pytest.mark.parametrize("planes", [2, 3, 5, 10])
    def test_a_descending_sweep_starts_where_an_ascending_one_ends(self, planes):
        # The whole point of the serpentine: no full-stack reset between tiles.
        assert sweep(planes, ascending=False)[0] == sweep(planes, ascending=True)[-1]

    @pytest.mark.parametrize("planes", [2, 3, 5, 10, 41])
    def test_no_sweep_travels_further_than_the_range(self, planes):
        assert travel_mm(planes) <= DEPTH + 1e-9


class TestPlaneCountIsNotASpeedControl:
    """Cutting planes does not cut travel — the subsample spans the full range.

    Recorded because the setting reads like a speed knob and is not one: on the
    measured tile, going 10 -> 3 planes saves 3.3 s of the 16.3 and leaves the
    10 s traverse untouched. Reducing the RANGE is what saves time.
    """

    @pytest.mark.parametrize("planes", [2, 3, 5, 10, 41])
    def test_travel_is_the_full_depth_for_any_multi_plane_sweep(self, planes):
        assert travel_mm(planes) == pytest.approx(DEPTH, abs=Z_STEP)

    def test_halving_the_planes_does_not_halve_the_travel(self):
        assert travel_mm(5) == pytest.approx(travel_mm(10), abs=Z_STEP)


class TestSweepShape:
    @pytest.mark.parametrize("planes", [1, 2, 3, 10])
    def test_the_requested_count_is_honoured(self, planes):
        assert len(sweep(planes)) == planes

    def test_uncapped_sweeps_every_step(self):
        assert len(sweep(None)) == int(DEPTH / Z_STEP) + 1

    def test_a_zero_depth_range_yields_one_position(self):
        # z_min == z_max must not produce an empty sweep.
        positions = LED2DOverviewWorkflow._z_sweep_positions(
            5.0, 5.0, Z_STEP, ascending=True, max_planes=10
        )
        assert positions == [5.0]

    @pytest.mark.parametrize("planes", [2, 3, 10])
    def test_multi_plane_sweeps_still_span_the_range(self, planes):
        positions = sweep(planes)
        assert positions[0] == pytest.approx(Z_MIN)
        assert positions[-1] == pytest.approx(Z_MAX, abs=Z_STEP)
