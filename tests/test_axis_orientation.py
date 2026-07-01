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


if __name__ == "__main__":
    unittest.main()
