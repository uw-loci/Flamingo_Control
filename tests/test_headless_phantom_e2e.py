"""End-to-end headless test: a data file → built pipeline → run → results.

Proves the loop the headless work is meant to enable: hand over a small image
file (a synthetic collagen phantom, or a synthesized stand-in), load it with
``headless_io.load_volumes``, author a pipeline with ``PipelineBuilder``, run it
with ``run_pipeline_headless``, and assert on the detected objects.

The real phantom generator lives outside this repo
(``QPSC_Project/tools/collagen-phantom-creation``) and pulls extra deps, so the
core tests synthesize an equivalent fiber-like volume in-process. One test
shells out to the real generator with ``--no-qc`` and is skipped when it (or its
deps) is unavailable.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TESTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import tifffile  # noqa: E402

from py2flamingo.pipeline.builder import (  # noqa: E402
    PipelineBuilder,
    make_template,
)
from py2flamingo.pipeline.headless_io import load_volumes  # noqa: E402
from py2flamingo.pipeline.headless_services import (  # noqa: E402
    build_headless_services,
    run_pipeline_headless,
)
from py2flamingo.pipeline.models.pipeline import NodeType  # noqa: E402

# Real generator location (best-effort; tests skip if absent).
_PHANTOM_TOOL = (
    Path.home()
    / "QPSC_Project"
    / "tools"
    / "collagen-phantom-creation"
    / "generate_phantoms.py"
)


def _make_fiber_volume(shape=(1, 96, 96), n_fibers=4, value=220) -> np.ndarray:
    """Synthesize a phantom-like uint8 volume: a few bright horizontal bands."""
    vol = np.zeros(shape, dtype=np.uint8)
    z = shape[0] // 2
    ys = np.linspace(10, shape[1] - 10, n_fibers).astype(int)
    for y in ys:
        vol[z, y - 1 : y + 2, 5 : shape[2] - 5] = value
    return vol


class TestHeadlessIOLoaders(unittest.TestCase):
    """load_volumes across the supported formats and channel layouts."""

    def test_npy_2d_gets_z_axis(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.npy"
            np.save(p, _make_fiber_volume()[0])  # 2-D (Y, X)
            vols = load_volumes(p)
            self.assertEqual(list(vols), [0])
            self.assertEqual(vols[0].ndim, 3)  # gained a Z axis

    def test_tiff_2d(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.ome.tif"
            tifffile.imwrite(p, _make_fiber_volume()[0])
            vols = load_volumes(p)
            self.assertEqual(vols[0].shape, (1, 96, 96))

    def test_tiff_multichannel_czyx(self):
        with tempfile.TemporaryDirectory() as d:
            arr = np.zeros((2, 1, 64, 64), np.uint8)
            arr[0, 0, 10:20, 5:50] = 200  # collagen
            arr[1, 0, 30:40, 30:40] = 150  # tumor
            p = Path(d) / "mc.tif"
            tifffile.imwrite(p, arr, metadata={"axes": "CZYX"})
            vols = load_volumes(p)
            self.assertEqual(sorted(vols), [0, 1])
            self.assertEqual(vols[0].shape, (1, 64, 64))

    def test_channel_selection(self):
        with tempfile.TemporaryDirectory() as d:
            arr = np.zeros((2, 1, 32, 32), np.uint8)
            p = Path(d) / "mc.tif"
            tifffile.imwrite(p, arr, metadata={"axes": "CZYX"})
            vols = load_volumes(p, channel=1)
            self.assertEqual(list(vols), [1])

    def test_unsupported_suffix_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.foo"
            p.write_text("nope")
            with self.assertRaises(ValueError):
                load_volumes(p)


class TestPhantomPipelineE2E(unittest.TestCase):
    """The full file → build → run → assert loop."""

    def _run_threshold(self, volumes):
        pipeline = make_template("threshold")
        services = build_headless_services(volumes=volumes)
        run = run_pipeline_headless(pipeline, services=services)
        self.assertTrue(run.succeeded, msg=str(run.errors))
        # The single THRESHOLD node should report a positive count.
        counts = [
            pv.data
            for pv in run.context.port_values.values()
            if pv.port_type.name == "SCALAR"
        ]
        self.assertTrue(counts and max(counts) > 0, msg=f"counts={counts}")
        return run

    def test_synthetic_volume_threshold(self):
        vol = _make_fiber_volume()
        self._run_threshold({0: vol})

    def test_load_then_run_from_tiff(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "phantom.ome.tif"
            tifffile.imwrite(p, _make_fiber_volume()[0])
            volumes = load_volumes(p)
            self._run_threshold(volumes)

    def test_builder_authored_pipeline(self):
        """Author a pipeline by hand (not a template) and run it."""
        b = PipelineBuilder("hand_authored")
        b.add(NodeType.THRESHOLD, channel_thresholds={0: 100}, min_object_size=4)
        pipeline = b.build()
        services = build_headless_services(volumes={0: _make_fiber_volume()})
        run = run_pipeline_headless(pipeline, services=services)
        self.assertTrue(run.succeeded, msg=str(run.errors))

    @unittest.skipUnless(_PHANTOM_TOOL.exists(), "QPSC phantom generator not present")
    def test_real_phantom_generator(self):
        """Best-effort: generate a real phantom with --no-qc and run on it."""
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(_PHANTOM_TOOL),
                    "--pattern",
                    "horizontal",
                    "--size",
                    "96",
                    "--no-qc",
                    "--out-dir",
                    d,
                ],
                capture_output=True,
                text=True,
            )
            tifs = list(Path(d).glob("*.ome.tif"))
            if not tifs:
                self.skipTest(
                    f"generator produced no TIFF (rc={proc.returncode}): "
                    f"{proc.stderr[-300:]}"
                )
            volumes = load_volumes(tifs[0])
            self._run_threshold(volumes)


class TestTemplates(unittest.TestCase):
    """make_template produces valid, runnable starter pipelines."""

    def test_all_templates_validate(self):
        from py2flamingo.pipeline.builder import list_templates

        for name in list_templates():
            pipeline = make_template(name)
            self.assertEqual(
                pipeline.validate(), [], msg=f"template {name} did not validate"
            )

    def test_stitch_template_acq_dir_optional(self):
        # POST_PROCESSING.acquisition_dir is required by default; the stitch
        # template marks it optional so a config-driven node validates.
        pipeline = make_template("stitch")
        node = next(iter(pipeline.nodes.values()))
        self.assertEqual(node.node_type, NodeType.POST_PROCESSING)
        port = node.get_input("acquisition_dir")
        self.assertFalse(port.required)


if __name__ == "__main__":
    unittest.main()
