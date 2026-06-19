"""Tests for the phantom test-dataset generators (py2flamingo.testing).

Covers the two `py2flamingo-pipeline collect` modes:
  * stitched  → small OME-TIFF + pipeline JSON, loadable + runnable.
  * raw       → native acquisition folder discoverable by discover_tiles
                (and, when the stitching backend is installed, stitchable).

Raw-mode tests pass a small frame_size so they stay fast and tiny — discovery
only depends on folder names, the Workflow.txt Z range, and the P-number in the
.raw filename, not on the raw byte count.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TESTS_DIR = Path(__file__).resolve().parent
_SRC = _TESTS_DIR.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from py2flamingo.pipeline.headless_io import load_volumes  # noqa: E402
from py2flamingo.pipeline.headless_services import (  # noqa: E402
    build_headless_services,
    run_pipeline_headless,
)
from py2flamingo.pipeline.models.pipeline import Pipeline  # noqa: E402
from py2flamingo.testing.phantom_dataset import (  # noqa: E402
    make_phantom_volume,
    write_raw_acquisition,
    write_stitched_dataset,
)

try:
    import multiview_stitcher  # noqa: F401

    _HAS_STITCHER = True
except Exception:
    _HAS_STITCHER = False


class TestPhantomVolume(unittest.TestCase):
    def test_shape_dtype_deterministic(self):
        a = make_phantom_volume((4, 32, 32), seed=1)
        b = make_phantom_volume((4, 32, 32), seed=1)
        self.assertEqual(a.shape, (4, 32, 32))
        self.assertEqual(a.dtype, np.uint16)
        np.testing.assert_array_equal(a, b)  # deterministic per seed
        self.assertGreater(int(a.max()), int(a.min()))  # has bright fibers


class TestRawAcquisition(unittest.TestCase):
    def test_discoverable(self):
        from py2flamingo.stitching.pipeline import discover_tiles

        with tempfile.TemporaryDirectory() as d:
            acq = write_raw_acquisition(
                Path(d) / "acq",
                grid=(2, 2),
                n_planes=3,
                channels=(1,),
                frame_size=(64, 64),  # small: keeps the test tiny/fast
            )
            tiles = discover_tiles(acq)
            self.assertEqual(len(tiles), 4)
            for t in tiles:
                self.assertEqual(t.n_planes, 3)
                self.assertEqual(t.channels, [1])
            # Tiles span a 2x2 grid with distinct X/Y stage coords.
            xs = sorted({round(t.x_mm, 3) for t in tiles})
            ys = sorted({round(t.y_mm, 3) for t in tiles})
            self.assertEqual(len(xs), 2)
            self.assertEqual(len(ys), 2)

    def test_raw_bytes_match_planes_and_frame(self):
        with tempfile.TemporaryDirectory() as d:
            acq = write_raw_acquisition(
                Path(d) / "acq",
                grid=(1, 1),
                n_planes=2,
                channels=(1, 2),
                frame_size=(32, 48),
            )
            raws = list(acq.rglob("*.raw"))
            self.assertEqual(len(raws), 2)  # one per channel
            for r in raws:
                self.assertEqual(r.stat().st_size, 2 * 32 * 48 * 2)  # planes*H*W*2

    def test_v020_geometry_metadata(self):
        """ScopeSettings.txt + AOI let v0.2.0 auto-derive pixel/frame size."""
        from py2flamingo.stitching.pipeline import (
            read_objective_magnification,
            suggested_pixel_size_um,
        )

        with tempfile.TemporaryDirectory() as d:
            acq = write_raw_acquisition(
                Path(d) / "acq",
                grid=(1, 1),
                n_planes=2,
                channels=(1,),
                frame_size=(128, 128),
                pixel_size_um=0.406,
            )
            self.assertTrue((acq / "ScopeSettings.txt").exists())
            # objective mag = sensor_pitch / pixel_size; pixel round-trips.
            self.assertAlmostEqual(
                read_objective_magnification(acq), 6.5 / 0.406, places=2
            )
            self.assertAlmostEqual(suggested_pixel_size_um(acq), 0.406, places=3)
            # AOI present in Workflow.txt.
            wf = next(acq.glob("X*/Workflow.txt")).read_text()
            self.assertIn("AOI width = 128", wf)
            self.assertIn("AOI height = 128", wf)

    @unittest.skipUnless(_HAS_STITCHER, "multiview-stitcher backend not installed")
    def test_full_stitch_when_backend_available(self):
        from py2flamingo.stitching.pipeline import StitchingConfig, StitchingPipeline

        with tempfile.TemporaryDirectory() as d:
            acq = write_raw_acquisition(
                Path(d) / "acq", grid=(2, 1), n_planes=2, channels=(1,)
            )
            cfg = StitchingConfig.with_yaml_defaults()
            cfg.skip_registration = True  # stage-position fusion; no phase corr
            cfg.output_format = "ome-tiff"
            out = StitchingPipeline(cfg).run(acq, acq / "stitched")
            tifs = [
                p
                for p in (acq / "stitched").rglob("*")
                if p.suffix in (".tif", ".tiff")
            ]
            self.assertTrue(tifs, msg=f"no stitched TIFF produced (out={out})")
            vols = load_volumes(tifs[0])
            self.assertTrue(vols)


class TestStitchedDataset(unittest.TestCase):
    def test_loads_and_runs_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            paths = write_stitched_dataset(
                Path(d) / "ds", shape=(4, 64, 64), channels=(0, 1)
            )
            self.assertTrue(paths["volume"].exists())
            self.assertTrue(paths["pipeline"].exists())

            volumes = load_volumes(paths["volume"])
            self.assertEqual(sorted(volumes), [0, 1])

            import json

            pipeline = Pipeline.from_dict(json.loads(paths["pipeline"].read_text()))
            services = build_headless_services(volumes=volumes)
            run = run_pipeline_headless(pipeline, services=services)
            self.assertTrue(run.succeeded, msg=str(run.errors))


if __name__ == "__main__":
    unittest.main()
