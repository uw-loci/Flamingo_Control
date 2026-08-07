"""Stitched data goes where it was acquired — when the file says where that is.

Reported: a stitched volume loads dead centre, axis-aligned, and does not
reflect the acquisition. Three causes, two fixed here:

* the acquisition ANGLE (-147.37 deg in the reported file) was never read; the
  reference rotation used the VIEWER's current rotation instead;
* the world frame can NEGATE stage X (`reverse_x_tiles`), so `origin_um.x` for
  a mosaic over stage X 2.34-8.35 mm is **-8350** — read literally that is not
  a stage coordinate at all.

Agreed rule: with a frame descriptor, place at the acquired position; without
one, stay centred, because a position derived from an undescribed frame is a
guess.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.views.sample_view import (  # noqa: E402
    acquired_angle_deg,
    acquired_center_xyz_um,
)

SHAPE = (1600, 2662, 1690)  # Z, Y, X
VOXEL = (5.0, 4.19, 4.19)  # µm
REPORTED = {
    "origin_um": [13000.0, 13210.0, -8350.0],
    "angles_deg": [-147.37],
    "world_frame": {
        "x_axis_negated": True,
        "y_axis_negated": False,
        "acquisition_angle_deg": -147.37,
    },
}


class TestNoFrameDescriptorMeansStayCentred(unittest.TestCase):
    def test_metadata_without_world_frame_returns_none(self):
        self.assertIsNone(
            acquired_center_xyz_um({"origin_um": [1, 2, 3]}, SHAPE, VOXEL)
        )

    def test_empty_metadata_returns_none(self):
        self.assertIsNone(acquired_center_xyz_um({}, SHAPE, VOXEL))
        self.assertIsNone(acquired_center_xyz_um(None, SHAPE, VOXEL))

    def test_a_frame_without_an_origin_returns_none(self):
        self.assertIsNone(acquired_center_xyz_um({"world_frame": {}}, SHAPE, VOXEL))

    def test_malformed_values_return_none_rather_than_a_wrong_position(self):
        bad = {"origin_um": ["x", None, 3], "world_frame": {}}
        self.assertIsNone(acquired_center_xyz_um(bad, SHAPE, VOXEL))


class TestNegationIsUndone(unittest.TestCase):
    def test_a_negated_x_comes_back_positive(self):
        cx, _, _ = acquired_center_xyz_um(REPORTED, SHAPE, VOXEL)

        self.assertGreater(cx, 0, "no stage X is negative")

    def test_the_unnegated_axes_are_recovered_exactly(self):
        """Y and Z need no correction, so they pin the arithmetic."""
        _, cy, cz = acquired_center_xyz_um(REPORTED, SHAPE, VOXEL)

        # Tiles span Y 13.21-24.37 mm and Z 13-21 mm.
        self.assertAlmostEqual(cy / 1000.0, 18.79, places=2)
        self.assertAlmostEqual(cz / 1000.0, 17.0, places=2)

    def test_without_the_negation_flag_x_is_left_alone(self):
        meta = dict(REPORTED, world_frame={"x_axis_negated": False})
        cx, _, _ = acquired_center_xyz_um(meta, SHAPE, VOXEL)

        self.assertLess(cx, 0)  # believed as written

    def test_y_negation_is_handled_independently(self):
        meta = dict(REPORTED, world_frame={"y_axis_negated": True})
        _, cy, _ = acquired_center_xyz_um(meta, SHAPE, VOXEL)

        self.assertLess(cy, 0)


class TestAcquisitionAngle(unittest.TestCase):
    def test_the_reported_angle_is_recovered(self):
        self.assertAlmostEqual(acquired_angle_deg(REPORTED), -147.37)

    def test_it_falls_back_to_a_single_angles_deg_entry(self):
        """Files written before the frame descriptor still carry this."""
        self.assertAlmostEqual(acquired_angle_deg({"angles_deg": [-90.0]}), -90.0)

    def test_a_multi_angle_file_does_not_guess(self):
        self.assertIsNone(acquired_angle_deg({"angles_deg": [0.0, 90.0]}))

    def test_no_angle_recorded_returns_none(self):
        self.assertIsNone(acquired_angle_deg({}))

    def test_zero_is_a_real_angle_not_a_missing_one(self):
        self.assertEqual(acquired_angle_deg({"angles_deg": [0.0]}), 0.0)


if __name__ == "__main__":
    unittest.main()
