"""Tile overlap must reach the arithmetic that positions tiles.

`acquisition_profile_generator` — the Union Thresholder path, and the one that
produces per-tile variable Z depths — wrote `step = fov_mm` in two places and
had **no overlap parameter at all**. Whatever a user set elsewhere in the app
could not reach it, so every profile it generated butted tiles edge to edge.

That shipped: a 97-tile brain acquisition (2026-08-08) measured **0.25% overlap
against 20% requested**, which no amount of registration can rescue — the
stitcher now refuses to register below 5%.

This is the FIFTH copy of the tile-step calculation to be found wrong. The last
test here sweeps for a sixth.

Run: python3 -m pytest tests/test_profile_generator_overlap.py -q
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.utils.acquisition_profile_generator import (  # noqa: E402
    DEFAULT_OVERLAP_PERCENT,
    generate_tile_profile,
    tile_step_mm,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "py2flamingo"


class TestTileStep:
    def test_zero_overlap_steps_a_full_field(self):
        assert tile_step_mm(1.0726, 0.0) == pytest.approx(1.0726)

    def test_twenty_percent_steps_four_fifths_of_a_field(self):
        # The case that failed in production: 1024 px x 1.0475 µm.
        assert tile_step_mm(1.0726, 20.0) == pytest.approx(0.85808)

    def test_the_default_is_not_zero(self):
        # A caller that forgets must get a usable grid, not an unstitchable one.
        assert DEFAULT_OVERLAP_PERCENT >= 5.0
        assert tile_step_mm(1.0, DEFAULT_OVERLAP_PERCENT) < 1.0

    @pytest.mark.parametrize("overlap", [-10.0, 60.0, 100.0])
    def test_out_of_range_values_are_clamped_not_obeyed(self, overlap):
        step = tile_step_mm(1.0, overlap)
        assert 0.0 < step <= 1.0


def _flat_mask(shape=(20, 60, 60)):
    mask = np.zeros(shape, dtype=bool)
    mask[5:15, 10:50, 10:50] = True
    return mask


def _voxel_to_stage(vz, vy, vx):
    """1 voxel = 0.01 mm, axis-aligned, no inversion."""
    return (float(vx) * 0.01, float(vy) * 0.01, float(vz) * 0.01)


def _x_positions(profiles, angle=0.0):
    xs = sorted(
        {round(p.x, 6) for p in profiles if abs(p.rotation_angle - angle) < 1e-6}
    )
    return xs


def _min_gap(values):
    gaps = [b - a for a, b in zip(values, values[1:]) if b - a > 1e-9]
    return min(gaps) if gaps else None


class TestGenerateTileProfile:
    FOV = 0.20  # mm

    def _profiles(self, overlap):
        return generate_tile_profile(
            mask=_flat_mask(),
            voxel_to_stage_fn=_voxel_to_stage,
            fov_mm=self.FOV,
            voxel_size_mm=0.01,
            buffer_fraction=0.0,
            rotation_angles=[0.0],
            overlap_percent=overlap,
        )

    def test_the_requested_overlap_reaches_the_tile_positions(self):
        gap = _min_gap(_x_positions(self._profiles(20.0)))
        assert gap == pytest.approx(self.FOV * 0.8, rel=1e-6)

    def test_zero_overlap_still_steps_a_full_field(self):
        gap = _min_gap(_x_positions(self._profiles(0.0)))
        assert gap == pytest.approx(self.FOV, rel=1e-6)

    def test_more_overlap_means_more_tiles(self):
        # The regression in one line: before the fix these were equal.
        assert len(self._profiles(30.0)) > len(self._profiles(0.0))

    def test_the_achieved_overlap_matches_what_was_asked_for(self):
        for requested in (5.0, 10.0, 20.0, 35.0):
            gap = _min_gap(_x_positions(self._profiles(requested)))
            achieved = (self.FOV - gap) / self.FOV * 100.0
            assert achieved == pytest.approx(requested, abs=0.01), requested

    def test_rotated_angles_get_the_overlap_too(self):
        # A second copy of the step lived in the rotated generator; a fix that
        # only touched the straight one would pass every test above.
        profiles = generate_tile_profile(
            mask=_flat_mask(),
            voxel_to_stage_fn=_voxel_to_stage,
            fov_mm=self.FOV,
            voxel_size_mm=0.01,
            buffer_fraction=0.0,
            rotation_angles=[0.0, 90.0],
            tip_position=(0.3, 0.1),
            overlap_percent=25.0,
        )
        gap = _min_gap(_x_positions(profiles, angle=90.0))
        assert gap == pytest.approx(self.FOV * 0.75, rel=1e-6)

    def test_the_default_argument_produces_overlapping_tiles(self):
        profiles = generate_tile_profile(
            mask=_flat_mask(),
            voxel_to_stage_fn=_voxel_to_stage,
            fov_mm=self.FOV,
            voxel_size_mm=0.01,
            buffer_fraction=0.0,
        )
        assert _min_gap(_x_positions(profiles)) < self.FOV


class TestNoSixthCopy:
    """Sweep for another `step = fov` hiding somewhere.

    Fixing two copies and declaring it settled shipped the same 0%-overlap bug
    three times. The step calculation is allowed to exist in the shared helpers
    and nowhere else.
    """

    _ALLOWED = {
        "utils/tile_geometry.py",  # THE definition
        "utils/acquisition_profile_generator.py",  # tile_step_mm, delegates
        "workflows/led_2d_overview_workflow.py",  # _tile_step_mm, delegates
    }

    def test_no_module_assigns_a_step_equal_to_a_bare_fov(self):
        pattern = re.compile(r"^\s*step(?:_[xy])?(?:_mm)?\s*=\s*fov(?:_mm)?\s*$")
        offenders = []
        for path in SRC.rglob("*.py"):
            rel = path.relative_to(SRC).as_posix()
            if rel in self._ALLOWED:
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if pattern.match(line):
                    offenders.append(f"{rel}:{number}: {line.strip()}")
        assert not offenders, (
            "a tile step set to a bare field of view is a 0% overlap no matter "
            "what the user asked for:\n  " + "\n  ".join(offenders)
        )
