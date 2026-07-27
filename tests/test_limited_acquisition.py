"""Geometry core for smart limited acquisition.

Covers Mode A/B (position-based single-arm selection), Mode C1 (half-and-rotate),
and Mode C2 (integrated N-view sectoring). The N-view tests deliberately exercise
N in {2, 3, 4, 6} to prove the geometry is not hard-coded to 2 or 4.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from py2flamingo.utils.limited_acquisition import (  # noqa: E402
    angle_schedule_deg,
    assign_tiles_to_angles,
    choose_illumination_arms,
    plan_halfrotate_split,
    plan_multiview_acquisition,
    plan_multiview_sectors,
    sector_width_deg,
)


class TestChooseIlluminationArms(unittest.TestCase):
    FOV = 0.5

    def test_within_one_fov_keeps_both(self):
        for offset in (0.0, 0.25, 0.5):  # <= 1 FOV
            sel = choose_illumination_arms(10.0 + offset, 10.0, self.FOV)
            self.assertTrue(sel.left_on and sel.right_on, f"offset {offset}")

    def test_far_positive_x_is_right_only(self):
        # Right arm at +X (default): a tile well past +1 FOV -> right only.
        sel = choose_illumination_arms(11.0, 10.0, self.FOV)
        self.assertFalse(sel.left_on)
        self.assertTrue(sel.right_on)

    def test_far_negative_x_is_left_only(self):
        sel = choose_illumination_arms(9.0, 10.0, self.FOV)
        self.assertTrue(sel.left_on)
        self.assertFalse(sel.right_on)

    def test_rig_sign_flip_swaps_near_arm(self):
        # If the right arm is actually at -X, the far-+X tile becomes left-only.
        sel = choose_illumination_arms(
            11.0, 10.0, self.FOV, right_arm_at_positive_x=False
        )
        self.assertTrue(sel.left_on)
        self.assertFalse(sel.right_on)

    def test_threshold_is_exclusive_boundary(self):
        # Exactly 1 FOV -> both; a hair beyond -> single side.
        self.assertTrue(
            choose_illumination_arms(10.5, 10.0, self.FOV).left_on
            and choose_illumination_arms(10.5, 10.0, self.FOV).right_on
        )
        beyond = choose_illumination_arms(10.5001, 10.0, self.FOV)
        self.assertFalse(beyond.left_on)

    def test_margin_fovs_parameter(self):
        # With a 2-FOV margin, a 1.5-FOV offset still collects both.
        sel = choose_illumination_arms(10.75, 10.0, self.FOV, margin_fovs=2.0)
        self.assertTrue(sel.left_on and sel.right_on)

    def test_invalid_fov_raises(self):
        with self.assertRaises(ValueError):
            choose_illumination_arms(1.0, 0.0, 0.0)

    def test_exactly_one_of_two_arms_when_limited(self):
        # Photodose/disk savings only happen if exactly one arm fires off-center.
        sel = choose_illumination_arms(12.0, 10.0, self.FOV)
        self.assertNotEqual(sel.left_on, sel.right_on)


class TestSectorSchedule(unittest.TestCase):
    def test_sector_width(self):
        self.assertAlmostEqual(sector_width_deg(2), 180.0)
        self.assertAlmostEqual(sector_width_deg(4), 90.0)
        self.assertAlmostEqual(sector_width_deg(3), 120.0)
        self.assertAlmostEqual(sector_width_deg(6), 60.0)

    def test_schedule_spacing_arbitrary_n(self):
        for n in (2, 3, 4, 6, 8):
            angles = angle_schedule_deg(n)
            self.assertEqual(len(angles), n)
            # consecutive spacing equals 360/n (compared as angular equivalence,
            # so +180 and -180 count as equal)
            step = 360.0 / n
            for k in range(n):
                diff = (angles[k] - k * step) % 360.0
                self.assertTrue(
                    abs(diff) < 1e-6 or abs(diff - 360.0) < 1e-6,
                    f"n={n} k={k}: {angles[k]} not {k * step} (mod 360)",
                )

    def test_n_angles_must_be_positive(self):
        with self.assertRaises(ValueError):
            sector_width_deg(0)


class TestMultiviewSectorPlan(unittest.TestCase):
    def test_two_angles_are_180_apart_half_each(self):
        plans = plan_multiview_sectors(2, good_direction_deg=45.0)
        self.assertEqual(len(plans), 2)
        self.assertAlmostEqual(plans[0].angle_deg, 0.0)
        self.assertAlmostEqual(abs(plans[1].angle_deg), 180.0)
        for p in plans:
            self.assertAlmostEqual(p.sector_half_width_deg, 90.0)  # 360/2/2

    def test_four_angles_are_quadrants(self):
        plans = plan_multiview_sectors(4, good_direction_deg=45.0)
        self.assertEqual(len(plans), 4)
        for p in plans:
            self.assertAlmostEqual(p.sector_half_width_deg, 45.0)  # 360/4/2

    def test_first_sector_center_is_good_direction(self):
        # At angle 0 the collected wedge is centered on the good direction itself.
        plans = plan_multiview_sectors(4, good_direction_deg=30.0)
        self.assertAlmostEqual(plans[0].sector_center_deg, 30.0)

    def test_arbitrary_n_not_hardcoded(self):
        for n in (3, 5, 6, 7):
            plans = plan_multiview_sectors(n)
            self.assertEqual(len(plans), n)
            self.assertAlmostEqual(plans[0].sector_half_width_deg, 180.0 / n)

    def test_halfrotate_is_two_view_case(self):
        a = plan_halfrotate_split(good_direction_deg=45.0, overlap_deg=5.0)
        b = plan_multiview_sectors(2, good_direction_deg=45.0, overlap_deg=5.0)
        self.assertEqual([p.angle_deg for p in a], [p.angle_deg for p in b])
        self.assertEqual(
            [p.sector_center_deg for p in a], [p.sector_center_deg for p in b]
        )


class TestAssignTilesToAngles(unittest.TestCase):
    def _ring(self, n_points, radius=1.0, cx=0.0, cz=0.0):
        import math

        pts = []
        for i in range(n_points):
            ang = math.radians(360.0 * i / n_points)
            pts.append((cx + radius * math.cos(ang), cz + radius * math.sin(ang)))
        return pts

    def test_every_angle_index_present(self):
        tiles = self._ring(12)
        assign = assign_tiles_to_angles(tiles, 4, (0.0, 0.0))
        self.assertEqual(set(assign.keys()), {0, 1, 2, 3})

    def test_two_view_partitions_the_circle(self):
        # No overlap: each tile assigned to exactly one of the two halves.
        tiles = self._ring(36)
        assign = assign_tiles_to_angles(tiles, 2, (0.0, 0.0), overlap_deg=0.0)
        counts = sum(len(v) for v in assign.values())
        self.assertEqual(counts, len(tiles))  # no double-counting without overlap

    def test_four_view_covers_all_tiles(self):
        tiles = self._ring(40)
        assign = assign_tiles_to_angles(tiles, 4, (0.0, 0.0))
        covered = set()
        for idxs in assign.values():
            covered.update(idxs)
        self.assertEqual(covered, set(range(len(tiles))))

    def test_overlap_makes_boundary_tiles_double_assigned(self):
        tiles = self._ring(36)
        none = assign_tiles_to_angles(tiles, 4, (0.0, 0.0), overlap_deg=0.0)
        wide = assign_tiles_to_angles(tiles, 4, (0.0, 0.0), overlap_deg=15.0)
        total_none = sum(len(v) for v in none.values())
        total_wide = sum(len(v) for v in wide.values())
        self.assertGreater(total_wide, total_none)

    def test_tile_on_rotation_center_assigned_everywhere(self):
        tiles = [(5.0, 5.0)]  # exactly the center
        assign = assign_tiles_to_angles(tiles, 4, (5.0, 5.0))
        for k in range(4):
            self.assertIn(0, assign[k])

    def test_tile_assigned_to_expected_quadrant(self):
        # Good dir 45, rotation_sign +1: a tile at azimuth 45 belongs to angle 0.
        tiles = [(1.0, 1.0)]  # azimuth 45 deg
        assign = assign_tiles_to_angles(tiles, 4, (0.0, 0.0), good_direction_deg=45.0)
        self.assertIn(0, assign[0])


class TestPlanMultiviewAcquisition(unittest.TestCase):
    def _ring_tiles(self, n, radius=2.0):
        import math

        tiles = []
        for i in range(n):
            ang = math.radians(360.0 * i / n)
            x = radius * math.cos(ang)
            z = radius * math.sin(ang)
            tiles.append((x, 5.0, z, z - 0.5, z + 0.5))  # (x, y, z, z_min, z_max)
        return tiles

    def test_two_angle_covers_all_tiles_once(self):
        tiles = self._ring_tiles(12)
        plan = plan_multiview_acquisition(tiles, 2, (0.0, 0.0), overlap_deg=0.0)
        # Every source tile planned exactly once across the two angles.
        self.assertEqual(sorted(p.source_index for p in plan), list(range(12)))
        self.assertEqual({p.angle_deg for p in plan}, {0.0, 180.0})

    def test_four_angles_present(self):
        tiles = self._ring_tiles(16)
        plan = plan_multiview_acquisition(tiles, 4, (0.0, 0.0))
        self.assertEqual({p.angle_index for p in plan}, {0, 1, 2, 3})

    def test_angle_zero_keeps_position(self):
        tiles = [(1.0, 5.0, 1.0, 0.5, 1.5)]  # azimuth 45 -> angle-0 sector
        plan = plan_multiview_acquisition(tiles, 4, (0.0, 0.0), good_direction_deg=45.0)
        p0 = [p for p in plan if p.angle_index == 0][0]
        self.assertAlmostEqual(p0.x, 1.0)
        self.assertAlmostEqual(p0.z, 1.0)

    def test_z_span_preserved_and_recentered(self):
        tiles = [(1.0, 5.0, 1.0, 0.5, 1.5)]
        plan = plan_multiview_acquisition(tiles, 4, (0.0, 0.0))
        for p in plan:
            self.assertAlmostEqual(p.z_max - p.z_min, 1.0)  # span kept
            self.assertAlmostEqual((p.z_min + p.z_max) / 2, p.z)  # centered on z

    def test_overlap_increases_total(self):
        tiles = self._ring_tiles(24)
        none = plan_multiview_acquisition(tiles, 4, (0.0, 0.0), overlap_deg=0.0)
        wide = plan_multiview_acquisition(tiles, 4, (0.0, 0.0), overlap_deg=15.0)
        self.assertGreater(len(wide), len(none))

    def test_rotation_applied_at_180(self):
        # A tile at azimuth 180 (x=-2) belongs to the angle-180 sector; acquiring
        # it there rotates its stage position 180° about center -> x=+2.
        tiles = [(-2.0, 5.0, 0.0, -0.5, 0.5)]  # azimuth 180
        plan = plan_multiview_acquisition(tiles, 2, (0.0, 0.0), good_direction_deg=0.0)
        p180 = [p for p in plan if abs(p.angle_deg) == 180.0]
        self.assertTrue(p180)
        self.assertAlmostEqual(p180[0].x, 2.0, places=6)


if __name__ == "__main__":
    unittest.main()
