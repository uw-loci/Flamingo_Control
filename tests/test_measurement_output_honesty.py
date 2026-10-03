"""Measured outputs must not lie about what they are.

Three faults, all of which produced a plausible-looking number where the honest
answer was "unknown":

* `elongation` was `float("inf")` for a one-voxel-thick object, which
  `json.dumps` writes as the bare literal `Infinity`. A strict JSON parser
  rejects the whole `--output-json` file over one such object.
* `centroid_stage` was `(0, 0, 0)` when no coordinate transform existed,
  indistinguishable from an object genuinely at the stage origin -- and
  WORKFLOW would drive there.
* OVERVIEW_ANALYSIS reduced a volume to its first plane with no log line, so a
  result from plane 0 of a 300-plane stack read exactly like a result about the
  stack.

Run: .venv/bin/python -m pytest tests/test_measurement_output_honesty.py -q
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.pipeline.services.threshold_analysis_service import (  # noqa: E402
    ThresholdAnalysisService,
    ThresholdSettings,
)


def _analyze(volume, **kw):
    settings = ThresholdSettings(channel_thresholds={0: 100}, **kw)
    return ThresholdAnalysisService().analyze({0: volume}, settings)


@pytest.fixture
def flat_plate():
    """One voxel thick, so its minor principal axis is zero."""
    v = np.zeros((4, 12, 12), dtype=np.uint8)
    v[0, 2:10, 2:10] = 255
    return v


@pytest.fixture
def blob():
    v = np.zeros((8, 12, 12), dtype=np.uint8)
    v[2:6, 3:9, 3:9] = 255
    return v


class TestElongationStaysValidJson:
    def test_a_flat_object_reports_no_elongation_rather_than_infinity(self, flat_plate):
        obj = _analyze(flat_plate).objects[0]
        assert obj.elongation is None

    def test_the_whole_object_list_survives_a_strict_json_parser(self, flat_plate):
        # allow_nan=False is what a non-Python consumer effectively enforces.
        payload = [o.to_dict() for o in _analyze(flat_plate).objects]
        json.dumps(payload, allow_nan=False)

    def test_an_unmeasurable_elongation_is_omitted_entirely(self, flat_plate):
        assert "elongation" not in _analyze(flat_plate).objects[0].to_dict()

    def test_a_normal_object_still_reports_a_number(self, blob):
        obj = _analyze(blob).objects[0]
        assert obj.elongation is not None and obj.elongation >= 1.0


class TestUnknownStageCoordinatesSaySo:
    def test_without_a_transform_the_flag_is_false(self, blob):
        # (0,0,0) is still the value, but it is now labelled as unknown.
        obj = _analyze(blob).objects[0]
        assert obj.stage_coords_available is False
        assert obj.centroid_stage == (0.0, 0.0, 0.0)

    def test_with_a_transform_the_flag_is_true(self, blob):
        result = ThresholdAnalysisService().analyze(
            {0: blob},
            ThresholdSettings(channel_thresholds={0: 100}),
            voxel_to_stage_fn=lambda z, y, x: (x * 2.0, y * 2.0, z * 2.0),
        )
        obj = result.objects[0]
        assert obj.stage_coords_available is True
        assert obj.centroid_stage != (0.0, 0.0, 0.0)

    def test_the_flag_round_trips_through_json(self, blob):
        from py2flamingo.pipeline.models.detected_object import DetectedObject

        obj = _analyze(blob).objects[0]
        assert DetectedObject.from_dict(obj.to_dict()).stage_coords_available is False

    def test_an_older_object_without_the_key_is_assumed_measured(self):
        # Pipelines saved before the field existed must keep working.
        from py2flamingo.pipeline.models.detected_object import DetectedObject

        d = {
            "label_id": 1,
            "centroid_voxel": [1.0, 2.0, 3.0],
            "centroid_stage": [4.0, 5.0, 6.0],
            "bounding_box": [[0, 2], [0, 2], [0, 2]],
            "volume_voxels": 8,
            "volume_mm3": 1.0,
        }
        assert DetectedObject.from_dict(d).stage_coords_available is True


class TestWorkflowRefusesAnUnknownPosition:
    def test_it_will_not_drive_to_the_origin(self, blob):
        """Driving to (0,0,0) is a real move to the corner of the chamber."""
        from py2flamingo.pipeline.engine.node_runners.workflow_runner import (
            WorkflowRunner,
        )

        obj = _analyze(blob).objects[0]
        assert obj.stage_coords_available is False

        source = Path(
            Path(__file__).resolve().parents[1]
            / "src/py2flamingo/pipeline/engine/node_runners/workflow_runner.py"
        ).read_text()
        # The guard must sit before the unpack, not after it.
        guard = source.index("stage_coords_available")
        unpack = source.index("sx, sy, sz = position_data.centroid_stage")
        assert guard < unpack
        assert WorkflowRunner is not None


class TestOverviewSaysWhenItDropsPlanes:
    def test_reducing_a_volume_to_one_plane_is_logged(self, tmp_path, caplog):
        tifffile = pytest.importorskip("tifffile")
        from py2flamingo.pipeline.builder import PipelineBuilder
        from py2flamingo.pipeline.headless_services import (
            build_headless_services,
            run_pipeline_headless,
        )
        from py2flamingo.pipeline.models.pipeline import NodeType

        path = tmp_path / "overview.tif"
        rng = np.random.default_rng(0)
        tifffile.imwrite(
            str(path),
            (rng.random((12, 64, 64)) * 255).astype(np.uint8),
            photometric="minisblack",
        )

        builder = PipelineBuilder("ov")
        builder.add(
            NodeType.OVERVIEW_ANALYSIS,
            tiles_x=4,
            tiles_y=4,
            image_path=str(path),
        )
        pipeline = builder.build(validate=False)

        with caplog.at_level(logging.WARNING):
            run_pipeline_headless(pipeline, services=build_headless_services())

        assert any("first plane" in r.getMessage() for r in caplog.records), [
            r.getMessage() for r in caplog.records
        ]

    def test_a_plain_2d_overview_logs_no_reduction(self, tmp_path, caplog):
        tifffile = pytest.importorskip("tifffile")
        from py2flamingo.pipeline.builder import PipelineBuilder
        from py2flamingo.pipeline.headless_services import (
            build_headless_services,
            run_pipeline_headless,
        )
        from py2flamingo.pipeline.models.pipeline import NodeType

        path = tmp_path / "flat.tif"
        rng = np.random.default_rng(1)
        tifffile.imwrite(str(path), (rng.random((64, 64)) * 255).astype(np.uint8))

        builder = PipelineBuilder("ov2")
        builder.add(
            NodeType.OVERVIEW_ANALYSIS, tiles_x=4, tiles_y=4, image_path=str(path)
        )
        pipeline = builder.build(validate=False)

        with caplog.at_level(logging.WARNING):
            run_pipeline_headless(pipeline, services=build_headless_services())

        assert not [r for r in caplog.records if "first plane" in r.getMessage()]
