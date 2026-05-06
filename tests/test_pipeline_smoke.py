"""Smoke test: load + run every shipped pipeline JSON headlessly.

Loops over both ``docs/sample_pipelines/*.json`` (the human-authored examples
people copy from) and ``tests/fixtures/pipelines/*.json`` (the fixtures the
unit tests use), running each via ``run_pipeline_headless`` with hardware
NodeTypes (WORKFLOW, POST_PROCESSING) replaced with no-op runners.

This is the highest-leverage test in the suite — one failure indicates either
a fixture drift or a real regression in the load/validate/run path.

Negative-path fixtures (12_invalid_type, 13_cycle, 14_missing_required) are
explicitly excluded — they intentionally fail validation and are exercised by
tests/test_pipeline_persistence.py.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TESTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parent
_SRC = _REPO_ROOT / "src"
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from pipeline_helpers import make_bright_volume  # noqa: E402

from py2flamingo.pipeline.headless_services import (  # noqa: E402
    build_headless_services,
    run_pipeline_headless,
)
from py2flamingo.pipeline.models.pipeline import NodeType, Pipeline  # noqa: E402

FIXTURES_DIR = _TESTS_DIR / "fixtures" / "pipelines"
SAMPLES_DIR = _REPO_ROOT / "docs" / "sample_pipelines"

# Negative-path fixtures intentionally fail validation; excluded from smoke.
NEGATIVE_FIXTURES = {
    "12_invalid_type.json",
    "13_cycle.json",
    "14_missing_required.json",
}

# NodeTypes that need real hardware or heavy deps; replace with NoOp.
DEFAULT_SKIP = {NodeType.WORKFLOW, NodeType.POST_PROCESSING}


def _gather_pipelines():
    paths = []
    if FIXTURES_DIR.exists():
        paths.extend(
            p
            for p in sorted(FIXTURES_DIR.glob("*.json"))
            if p.name not in NEGATIVE_FIXTURES
        )
    if SAMPLES_DIR.exists():
        paths.extend(sorted(SAMPLES_DIR.glob("*.json")))
    return paths


def _maybe_substitute_paths(pipeline: Pipeline, image_path: str) -> None:
    """Patch placeholder paths in node configs.

    Fixtures 08 (OverviewAnalysis) and 09 (PostProcessing) ship with
    ``__SUBSTITUTED_AT_RUNTIME__`` in their config because their target paths
    only exist during the test run.
    """
    for node in pipeline.nodes.values():
        for key in ("image_path", "acquisition_dir"):
            if node.config.get(key) == "__SUBSTITUTED_AT_RUNTIME__":
                node.config[key] = image_path


class TestPipelineSmoke(unittest.TestCase):
    """Parametric: one assertion per discovered pipeline JSON."""

    @classmethod
    def setUpClass(cls):
        # Build once: a tiny test image used by OVERVIEW_ANALYSIS fixtures.
        cls._tmp = tempfile.TemporaryDirectory(prefix="pipeline_smoke_")
        cls._tmp_path = Path(cls._tmp.name)
        # 64×64 image with a bright square aligned to 4×4 tile boundaries —
        # matches TestOverviewAnalysisRunner._2d_image in test_pipeline_runners.
        img = np.full((64, 64), 30, dtype=np.uint8)
        img[16:48, 16:48] = 200
        cls._image_path = cls._tmp_path / "overview.npy"
        np.save(str(cls._image_path), img)
        # PostProcessing's acquisition_dir must exist as a directory; the
        # NodeType is no-op'd via skip_node_types, but the path is still
        # written into config so a separate validation path doesn't trip.
        cls._acq_dir = cls._tmp_path / "fake_acq"
        cls._acq_dir.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_smoke_runs_every_pipeline(self):
        pipelines = _gather_pipelines()
        self.assertGreater(
            len(pipelines), 0, "no pipelines found in fixtures or samples"
        )
        failures = []
        for path in pipelines:
            with self.subTest(pipeline=path.name):
                try:
                    self._run_one(path)
                except AssertionError as e:
                    failures.append(f"{path.name}: {e}")
        if failures:
            self.fail("\n  ".join(["smoke failures:"] + failures))

    def _run_one(self, path: Path) -> None:
        data = json.loads(path.read_text())
        p = Pipeline.from_dict(data)
        # Sample pipelines may use template_file = "" with WORKFLOW; our
        # default skip set NoOps WORKFLOW so the missing template doesn't
        # fail. Sample pipelines may also reference SAMPLE_VIEW_DATA — that
        # needs voxel storage, which we provide.
        _maybe_substitute_paths(p, str(self._acq_dir))
        # OVERVIEW_ANALYSIS uses image_path; substitute again with the actual
        # image path (separate from acquisition_dir).
        for node in p.nodes.values():
            if node.node_type == NodeType.OVERVIEW_ANALYSIS:
                node.config["image_path"] = str(self._image_path)

        # Provide volumes for all 8 channels — sample pipelines like
        # detect_and_reimage_ch3.json read SAMPLE_VIEW_DATA from a specific
        # non-zero channel (channel 3 in that case).
        bright = make_bright_volume(bright_count=2)
        services = build_headless_services(
            volumes={ch: bright for ch in range(8)},
        )
        run = run_pipeline_headless(
            p,
            services=services,
            skip_node_types=DEFAULT_SKIP,
            raise_on_error=False,
        )
        self.assertTrue(
            run.succeeded,
            f"errors: {run.errors}\nnode_states: {run.node_states}",
        )


if __name__ == "__main__":
    unittest.main()
