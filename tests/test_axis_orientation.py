"""AxisOrientation reproduces the legacy convention and describes the new scope.

The legacy path is a no-regression contract: for any stage delta / position the
generalized orientation must produce EXACTLY the old hardcoded numbers
(delta offset ``[+dz, -dy, +/-dx]`` and the absolute ``physical_to_napari``
mapping). The new-scope path checks the 90-degrees-about-vertical permutation.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from py2flamingo.visualization.axis_orientation import (  # noqa: E402
    AxisEntry,
    AxisOrientation,
)


def _legacy_delta(dx, dy, dz, invert_x):
    dx_display = -dx if invert_x else dx
    return np.array([dz, -dy, dx_display], dtype=float)


def _legacy_absolute(x, y, z, ranges, vs_mm, invert_x, invert_z):
    # Mirrors PhysicalToNapariMapper.physical_to_napari (napari_x, napari_y, napari_z).
    xr, yr, zr = ranges["x"], ranges["y"], ranges["z"]
    x_eff = (xr[0] + xr[1] - x) if invert_x else x
    z_eff = (zr[0] + zr[1] - z) if invert_z else z
    napari_x = (x_eff - xr[0]) / vs_mm
    napari_y = (yr[1] - y) / vs_mm
    napari_z = (z_eff - zr[0]) / vs_mm
    return napari_x, napari_y, napari_z


class TestLegacyParity(unittest.TestCase):
    RANGES = {"x": (1.0, 12.31), "y": (0.0, 14.0), "z": (12.5, 26.0)}
    VS_MM = 0.05

    def test_delta_offset_matches_old_formula(self):
        for invert_x in (False, True):
            ori = AxisOrientation.legacy(invert_x=invert_x)
            for dx, dy, dz in [
                (0.1, 0, 0),
                (0, 0.2, 0),
                (0, 0, 0.3),
                (0.05, -0.1, 0.2),
            ]:
                got = ori.delta_offset(dx, dy, dz)
                exp = _legacy_delta(dx, dy, dz, invert_x)
                np.testing.assert_allclose(got, exp, atol=1e-12)

    def test_absolute_matches_physical_to_napari(self):
        for invert_x in (False, True):
            for invert_z in (False, True):
                ori = AxisOrientation.legacy(invert_x=invert_x, invert_z=invert_z)
                for x, y, z in [
                    (6.655, 7.0, 19.25),
                    (1.0, 0.0, 12.5),
                    (12.31, 14.0, 26.0),
                ]:
                    a0, a1, a2 = ori.absolute(x, y, z, self.RANGES, self.VS_MM)
                    nx, ny, nz = _legacy_absolute(
                        x, y, z, self.RANGES, self.VS_MM, invert_x, invert_z
                    )
                    # absolute() returns (depth=z, vertical=y, horizontal=x)
                    self.assertAlmostEqual(a0, nz, places=9)
                    self.assertAlmostEqual(a1, ny, places=9)
                    self.assertAlmostEqual(a2, nx, places=9)

    def test_from_config_without_block_is_legacy(self):
        ori = AxisOrientation.from_config({}, invert_x=True, invert_z=False)
        np.testing.assert_allclose(
            ori.delta_offset(0.1, 0.2, 0.3),
            _legacy_delta(0.1, 0.2, 0.3, invert_x=True),
            atol=1e-12,
        )


class TestNewScopeOrientation(unittest.TestCase):
    """90 deg about vertical: stage Z -> horizontal, stage X -> depth."""

    BLOCK = {
        "orientation": {
            "depth": {"stage": "x", "flip": False},
            "vertical": {"stage": "y", "flip": True},
            "horizontal": {"stage": "z", "flip": False},
        }
    }

    def test_permutation_swaps_x_and_z(self):
        ori = AxisOrientation.from_config(self.BLOCK)
        # A +Z stage move now shifts the HORIZONTAL display axis (index 2), not depth.
        off = ori.delta_offset(0.0, 0.0, 0.3)
        self.assertAlmostEqual(off[0], 0.0)  # depth unchanged by z
        self.assertAlmostEqual(off[2], 0.3)  # horizontal driven by z
        # A +X stage move now shifts DEPTH (index 0).
        off = ori.delta_offset(0.4, 0.0, 0.0)
        self.assertAlmostEqual(off[0], 0.4)
        self.assertAlmostEqual(off[2], 0.0)
        # Vertical still inverted.
        self.assertAlmostEqual(ori.delta_offset(0, 0.5, 0)[1], -0.5)

    def test_validate_rejects_duplicate_axis(self):
        bad = {
            "orientation": {
                "depth": {"stage": "x"},
                "vertical": {"stage": "y"},
                "horizontal": {"stage": "x"},  # duplicate
            }
        }
        with self.assertRaises(ValueError):
            AxisOrientation.from_config(bad)

    def test_axis_lookup_helpers(self):
        ori = AxisOrientation.from_config(self.BLOCK)
        self.assertEqual(ori.stage_axis_for(0), "x")
        self.assertEqual(ori.stage_axis_for(2), "z")
        self.assertEqual(ori.display_axis_for("z"), 2)
        self.assertEqual(ori.display_axis_for("x"), 0)


class TestInverseAndExtent(unittest.TestCase):
    RANGES = {"x": (1.0, 12.31), "y": (0.0, 14.0), "z": (12.5, 26.0)}
    VS_MM = 0.05

    def test_absolute_inverse_round_trip(self):
        for block in (
            None,
            {
                "orientation": {
                    "depth": {"stage": "x", "flip": False},
                    "vertical": {"stage": "y", "flip": True},
                    "horizontal": {"stage": "z", "flip": False},
                }
            },
        ):
            ori = (
                AxisOrientation.legacy(invert_x=True)
                if block is None
                else AxisOrientation.from_config(block)
            )
            for x, y, z in [(6.655, 7.0, 19.25), (3.2, 5.5, 15.0)]:
                a0, a1, a2 = ori.absolute(x, y, z, self.RANGES, self.VS_MM)
                xr, yr, zr = ori.inverse_absolute(a0, a1, a2, self.RANGES, self.VS_MM)
                self.assertAlmostEqual(xr, x, places=6)
                self.assertAlmostEqual(yr, y, places=6)
                self.assertAlmostEqual(zr, z, places=6)

    def test_order_by_display(self):
        legacy = AxisOrientation.legacy(invert_x=True)
        # depth=z, vertical=y, horizontal=x  ->  (z, y, x)
        self.assertEqual(legacy.order_by_display({"x": 1, "y": 2, "z": 3}), (3, 2, 1))
        new = AxisOrientation.from_config(
            {
                "orientation": {
                    "depth": {"stage": "x"},
                    "vertical": {"stage": "y"},
                    "horizontal": {"stage": "z"},
                }
            }
        )
        # depth=x, vertical=y, horizontal=z  ->  (x, y, z)
        self.assertEqual(new.order_by_display({"x": 1, "y": 2, "z": 3}), (1, 2, 3))

    def test_display_extent_permutes(self):
        from py2flamingo.visualization.axis_orientation import HORIZONTAL

        legacy = AxisOrientation.legacy(invert_x=True)
        new = AxisOrientation.from_config(
            {
                "orientation": {
                    "depth": {"stage": "x"},
                    "vertical": {"stage": "y"},
                    "horizontal": {"stage": "z"},
                }
            }
        )
        # Horizontal extent: legacy uses X range (11.31mm), new uses Z (13.5mm).
        self.assertEqual(
            legacy.display_extent(HORIZONTAL, self.RANGES, self.VS_MM),
            int((12.31 - 1.0) / 0.05),
        )
        self.assertEqual(
            new.display_extent(HORIZONTAL, self.RANGES, self.VS_MM),
            int((26.0 - 12.5) / 0.05),
        )


class TestTileWorldCoordsMath(unittest.TestCase):
    """The tile-worker world-coord math (base + M@cam + -M@delta) reproduces the
    old (Z,Y,X) formula for legacy, and permutes for the new scope."""

    SC = [6655.0, 7000.0, 19250.0]  # sample_region_center (X, Y, Z) µm

    @staticmethod
    def _new(ori, cx, cy, dx, dy, dz):
        M = ori.delta_offset_matrix()
        base = np.asarray(
            ori.order_by_display({"x": 6655.0, "y": 7000.0, "z": 19250.0}),
            dtype=float,
        )
        cam_stage = np.column_stack([cx, -cy, np.zeros(len(cx))])
        per_pixel_base = base + cam_stage @ M.T
        tile_disp = -(M @ np.array([dx * 1000.0, dy * 1000.0, dz * 1000.0]))
        return per_pixel_base + tile_disp  # (N, 3) depth,vert,horiz

    def test_legacy_matches_old_zyx_formula(self):
        cx = np.array([-100.0, 0.0, 250.0])
        cy = np.array([50.0, -30.0, 0.0])
        dx, dy, dz = 0.3, -0.2, 0.15  # mm
        for invert_x in (False, True):
            ori = AxisOrientation.legacy(invert_x=invert_x)
            new = self._new(ori, cx, cy, dx, dy, dz)
            # Old formula (world Z, Y, X):
            delta_x_storage = dx if invert_x else -dx
            camera_x_offset = -cx if invert_x else cx
            old_z = np.full(len(cx), self.SC[2] - dz * 1000)
            old_y = self.SC[1] + dy * 1000 + cy
            old_x = self.SC[0] + delta_x_storage * 1000 + camera_x_offset
            old = np.column_stack([old_z, old_y, old_x])
            np.testing.assert_allclose(new, old, atol=1e-6)

    def test_new_scope_puts_zstack_on_horizontal(self):
        ori = AxisOrientation.from_config(
            {
                "orientation": {
                    "depth": {"stage": "x", "flip": False},
                    "vertical": {"stage": "y", "flip": True},
                    "horizontal": {"stage": "z", "flip": False},
                }
            }
        )
        # A +Z (detection) stage step should move data along HORIZONTAL (axis 2),
        # not depth. Compare two Z positions, no camera offset, no x/y move.
        z = np.zeros(1)
        a = self._new(ori, z, z, 0.0, 0.0, 0.0)[0]
        b = self._new(ori, z, z, 0.0, 0.0, 0.10)[0]
        d = b - a
        self.assertAlmostEqual(d[0], 0.0)  # depth unchanged by a Z move
        self.assertAlmostEqual(d[1], 0.0)  # vertical unchanged
        self.assertLess(abs(d[2]), 101)  # horizontal moved (~ -100 µm, sign TBD)
        self.assertGreater(abs(d[2]), 99)


class TestMapperLegacyParity(unittest.TestCase):
    """PhysicalToNapariMapper (refactored) matches the old formulas exactly."""

    CFG = {
        "x_range_mm": [1.0, 12.31],
        "y_range_mm": [0.0, 14.0],
        "z_range_mm": [12.5, 26.0],
        "voxel_size_um": 50.0,
        "invert_x": True,
        "invert_z": False,
    }

    def _old(self, x, y, z):
        xr, yr, zr = (
            self.CFG["x_range_mm"],
            self.CFG["y_range_mm"],
            self.CFG["z_range_mm"],
        )
        vs = 0.05
        xe = xr[0] + xr[1] - x  # invert_x=True
        nx = int(round((xe - xr[0]) / vs))
        ny = int(round((yr[1] - y) / vs))
        nz = int(round((z - zr[0]) / vs))
        dims = (
            int((xr[1] - xr[0]) / vs),
            int((yr[1] - yr[0]) / vs),
            int((zr[1] - zr[0]) / vs),
        )
        clamp = lambda v, n: min(max(v, 0), n - 1)  # noqa: E731
        return (clamp(nx, dims[0]), clamp(ny, dims[1]), clamp(nz, dims[2]))

    def test_forward_and_roundtrip(self):
        from py2flamingo.visualization.coordinate_transforms import (
            PhysicalToNapariMapper,
        )

        m = PhysicalToNapariMapper(self.CFG)
        self.assertEqual(m.napari_dims, (226, 280, 270))
        for x, y, z in [(6.655, 7.0, 19.25), (1.0, 0.0, 12.5), (3.2, 5.5, 15.0)]:
            got = m.physical_to_napari(x, y, z)
            self.assertEqual(tuple(int(v) for v in got), self._old(x, y, z))


if __name__ == "__main__":
    unittest.main()
