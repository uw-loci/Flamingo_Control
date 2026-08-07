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


class TestReferenceMatchesPlacement(unittest.TestCase):
    """The transform anchor and the placement must use the SAME arithmetic.

    _load_stitched_into_voxel_storage places each channel at
    acquired_center_xyz_um(), but computed its reference stage position from
    origin_um read literally. For the reported file that put the reference at
    x = -4.809 mm and the data at x = +4.809 mm, so the volume jumped 9.6 mm in
    X the moment a real stage position arrived — data drawn in one place,
    anchored to another.
    """

    def _literal_center_mm(self, meta, shape, voxel):
        """What the load path used to compute (and still does with no frame)."""
        o_z, o_y, o_x = meta["origin_um"]
        v_z, v_y, v_x = voxel
        return (
            (o_x + shape[2] * v_x / 2) / 1000,
            (o_y + shape[1] * v_y / 2) / 1000,
            (o_z + shape[0] * v_z / 2) / 1000,
        )

    def test_a_negated_x_axis_makes_the_literal_origin_disagree(self):
        """The bug this guards: the two disagree, so they must not be mixed."""
        acquired = acquired_center_xyz_um(REPORTED, SHAPE, VOXEL)
        literal = self._literal_center_mm(REPORTED, SHAPE, VOXEL)

        self.assertAlmostEqual(acquired[0] / 1000, 4.809, places=3)
        self.assertAlmostEqual(literal[0], -4.809, places=3)
        # ~9.6 mm apart -- far more than any tolerance.
        self.assertGreater(abs(acquired[0] / 1000 - literal[0]), 9.0)

    def test_un_negated_axes_agree(self):
        """Y and Z were never negated, so both routes must land identically."""
        acquired = acquired_center_xyz_um(REPORTED, SHAPE, VOXEL)
        literal = self._literal_center_mm(REPORTED, SHAPE, VOXEL)

        self.assertAlmostEqual(acquired[1] / 1000, literal[1], places=6)
        self.assertAlmostEqual(acquired[2] / 1000, literal[2], places=6)


class TestPlacementConvention(unittest.TestCase):
    """Data is placed at the FOCAL POINT; the acquisition position is the anchor.

    This mirrors live tile acquisition exactly (tile_processing_worker:
    ``base_display = sample_region_center`` and the stage position enters only
    as ``pos - ref``). The goal is real-world coordinates for the data: an
    initial position, plus an affine driven by the stage position relative to
    the original stage position.

    Feeding the acquired stage centre in as the placement instead double-counts
    that offset AND treats a stage coordinate as a chamber one. The chamber is a
    fixed 14 mm window; the stage reaches 25 mm. A mosaic acquired at stage
    Y 18.8 mm then lands almost entirely outside the chamber before the stage
    has moved at all.
    """

    CHAMBER_ORIGIN = (12500.0, 0.0, 1000.0)  # display order (depth, vert, horiz) um
    CHAMBER_EXTENT = (13500.0, 14000.0, 11300.0)
    FOCAL_POINT = (6655.0, 7000.0, 19250.0)  # sample_region_center, (x, y, z) um

    def _fraction_inside(self, centre_xyz):
        import numpy as np

        from py2flamingo.views.sample_view import orient_stitched_volume
        from py2flamingo.visualization.axis_orientation import AxisOrientation

        ori = AxisOrientation.legacy(invert_x=True)
        _, wmin, wmax = orient_stitched_volume(
            np.zeros((2, 2, 2), dtype=np.uint16), SHAPE, VOXEL, centre_xyz, ori
        )
        origin = np.asarray(self.CHAMBER_ORIGIN)
        far = origin + np.asarray(self.CHAMBER_EXTENT)
        inside = np.maximum(np.minimum(wmax, far) - np.maximum(wmin, origin), 0.0)
        return float(np.prod(inside) / np.prod(wmax - wmin))

    def test_the_focal_point_keeps_the_volume_in_the_chamber(self):
        self.assertAlmostEqual(self._fraction_inside(self.FOCAL_POINT), 1.0, places=6)

    def test_the_acquired_stage_centre_would_push_it_out(self):
        """Why the acquired centre must NOT be used as the placement."""
        acquired = acquired_center_xyz_um(REPORTED, SHAPE, VOXEL)
        self.assertLess(self._fraction_inside(acquired), 0.10)


class TestWhichStagePositionIsTheAnchor(unittest.TestCase):
    """A mosaic spans many stage positions; exactly one anchors the transform.

    The choice is the stage position of the volume's geometric CENTRE
    (origin + extent/2). That is the position at which the middle of the mosaic
    was at the focal point, so returning the stage there re-centres the volume
    in the chamber -- consistent with what the live view painted in as the
    stage swept. Any other choice leaves the volume offset by a fixed error.
    """

    def test_the_anchor_is_the_centre_of_the_swept_stage_range(self):
        cx, cy, cz = acquired_center_xyz_um(REPORTED, SHAPE, VOXEL)

        # The stage ranges the mosaic actually swept, in mm.
        # X is negated in the file: origin.x = -8350 -> stage 1.269 .. 8.350
        ext_x = SHAPE[2] * VOXEL[2] / 1000  # 7.081 mm
        ext_y = SHAPE[1] * VOXEL[1] / 1000  # 11.154 mm
        ext_z = SHAPE[0] * VOXEL[0] / 1000  # 8.000 mm
        x_lo, x_hi = 8.350 - ext_x, 8.350
        y_lo, y_hi = 13.210, 13.210 + ext_y
        z_lo, z_hi = 13.000, 13.000 + ext_z

        self.assertAlmostEqual(cx / 1000, (x_lo + x_hi) / 2, places=3)
        self.assertAlmostEqual(cy / 1000, (y_lo + y_hi) / 2, places=3)
        self.assertAlmostEqual(cz / 1000, (z_lo + z_hi) / 2, places=3)

    def test_the_anchor_is_a_reachable_stage_position(self):
        """Sanity: an anchor outside the stage's travel cannot be a stage pos.

        The un-negated -4.809 mm failed this, which is what exposed the bug.
        """
        cx, cy, cz = acquired_center_xyz_um(REPORTED, SHAPE, VOXEL)

        self.assertGreater(cx / 1000, 1.0)  # stage X travel is 1.0 .. 12.31 mm
        self.assertLess(cx / 1000, 12.31)
        self.assertLess(cy / 1000, 25.0)  # y_stage_max_mm


if __name__ == "__main__":
    unittest.main()
