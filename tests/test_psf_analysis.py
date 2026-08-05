"""Tests for the psf_analysis package: recover known FWHM from bead phantoms.

Fully hardware-free. Generates Gaussian beads of known FWHM
(``phantom_dataset.make_bead_volume`` / ``write_bead_dataset``), runs
``PSFAnalysisService``, and asserts the recovered per-axis FWHM matches the
ground truth, plus that edge / crowded beads are rejected.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

_TESTS_DIR = Path(__file__).resolve().parent
_SRC = _TESTS_DIR.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from py2flamingo.psf_analysis import (  # noqa: E402
    PSFAnalysisService,
    PSFSettings,
    load_volume,
)
from py2flamingo.testing.phantom_dataset import (  # noqa: E402
    make_bead_volume,
    write_bead_dataset,
)


class TestPSFRecovery(unittest.TestCase):
    def test_recovers_known_fwhm(self):
        voxel = (2.0, 0.4, 0.4)  # (z, y, x) µm — anisotropic, like Flamingo
        fwhm = (8.0, 1.6, 2.0)  # distinct per axis to catch axis mix-ups
        vol, beads = make_bead_volume(
            (40, 220, 220), voxel_size_um=voxel, fwhm_um=fwhm, n_beads=6, seed=3
        )
        self.assertGreaterEqual(len(beads), 4)

        result = PSFAnalysisService().analyze(
            vol, voxel_size_um=voxel, settings=PSFSettings(window_um=8.0)
        )
        self.assertGreaterEqual(result.n_accepted, 4)

        summary = result.summary()
        # Recovered FWHM within 15% of ground truth for every axis.
        for axis, truth in zip(("x", "y", "z"), (fwhm[2], fwhm[1], fwhm[0])):
            mean = summary[f"fwhm_{axis}_um_mean"]
            self.assertIsNotNone(mean, f"no {axis} fit")
            self.assertAlmostEqual(
                mean,
                truth,
                delta=0.15 * truth,
                msg=f"axis {axis}: recovered {mean:.3f} vs truth {truth}",
            )

    def test_axes_are_distinguished(self):
        # A bead wider in Y than X must report fwhm_y > fwhm_x.
        voxel = (2.0, 0.4, 0.4)
        vol, _ = make_bead_volume(
            (40, 200, 200),
            voxel_size_um=voxel,
            fwhm_um=(8.0, 3.0, 1.2),
            n_beads=5,
            seed=1,
        )
        result = PSFAnalysisService().analyze(vol, voxel_size_um=voxel)
        s = result.summary()
        self.assertGreater(s["fwhm_y_um_mean"], s["fwhm_x_um_mean"])

    def test_edge_bead_rejected(self):
        # A single bead jammed against the edge should be rejected, not fit.
        voxel = (2.0, 0.4, 0.4)
        vol, _ = make_bead_volume(
            (30, 160, 160), voxel_size_um=voxel, n_beads=1, seed=0
        )
        # Force a bead into the corner.
        vol[2, 4, 4] = 60000
        result = PSFAnalysisService().analyze(
            vol, voxel_size_um=voxel, settings=PSFSettings(window_um=8.0)
        )
        corner = [
            b
            for b in result.beads
            if b.centroid_voxel[1] < 10 and b.centroid_voxel[2] < 10
        ]
        self.assertTrue(corner, "corner bead not detected")
        self.assertTrue(all(not b.accepted for b in corner))
        self.assertTrue(all(b.reject_reason == "edge" for b in corner))

    def test_single_plane_skips_z(self):
        # A 2-D-ish stack (few Z planes) should still fit X/Y, skip Z.
        voxel = (2.0, 0.4, 0.4)
        vol, _ = make_bead_volume(
            (3, 160, 160),
            voxel_size_um=voxel,
            fwhm_um=(4.0, 1.6, 1.6),
            n_beads=4,
            seed=2,
        )
        result = PSFAnalysisService().analyze(vol, voxel_size_um=voxel)
        self.assertGreaterEqual(result.n_accepted, 1)
        for b in result.accepted:
            self.assertIn("x", b.fits)
            self.assertNotIn("z", b.fits)


class TestPSFFileRoundTrip(unittest.TestCase):
    def test_load_volume_reads_voxel_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = write_bead_dataset(
                tmp,
                shape=(24, 200, 200),
                voxel_size_um=(3.0, 0.5, 0.5),
                fwhm_um=(9.0, 1.8, 1.8),
                n_beads=5,
                seed=4,
            )
            vol, (z, y, x) = load_volume(info["volume"])
            self.assertEqual(vol.ndim, 3)
            self.assertAlmostEqual(x, 0.5, places=4)
            self.assertAlmostEqual(y, 0.5, places=4)
            self.assertAlmostEqual(z, 3.0, places=4)

            result = PSFAnalysisService().analyze(
                vol, voxel_size_um=(z, y, x), settings=PSFSettings(window_um=9.0)
            )
            self.assertGreaterEqual(result.n_accepted, 3)
            self.assertAlmostEqual(result.summary()["fwhm_x_um_mean"], 1.8, delta=0.3)


if __name__ == "__main__":
    unittest.main()
