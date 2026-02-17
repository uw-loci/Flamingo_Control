"""
ThresholdRunner — runs threshold analysis via ThresholdAnalysisService.

Config:
    channel_thresholds: dict — {channel_id: threshold_value}
    gauss_sigma: float — Gaussian smoothing sigma
    opening_enabled: bool — morphological opening
    opening_radius: int — opening structuring element radius
    min_object_size: int — minimum object size in voxels

Inputs:
    volume — 3D numpy array (or uses current viewer data if unconnected)

Outputs:
    objects — List[DetectedObject]
    mask — 3D boolean mask
    count — number of detected objects
"""

import logging
from typing import Dict

import numpy as np

from py2flamingo.pipeline.models.port_types import PortType
from py2flamingo.pipeline.models.pipeline import Pipeline, PipelineNode
from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.node_runners.base_runner import AbstractNodeRunner
from py2flamingo.pipeline.services.threshold_analysis_service import (
    ThresholdAnalysisService, ThresholdSettings,
)

logger = logging.getLogger(__name__)


class ThresholdRunner(AbstractNodeRunner):
    """Runs threshold analysis using ThresholdAnalysisService."""

    def __init__(self):
        self._service = ThresholdAnalysisService()

    def run(self, node: PipelineNode, pipeline: Pipeline,
            context: ExecutionContext) -> None:
        config = node.config

        # Build settings from config
        settings = ThresholdSettings(
            channel_thresholds=config.get('channel_thresholds', {}),
            gauss_sigma=config.get('gauss_sigma', 0.0),
            opening_enabled=config.get('opening_enabled', False),
            opening_radius=config.get('opening_radius', 1),
            min_object_size=config.get('min_object_size', 0),
        )

        # Get input volume(s)
        input_vol = self._get_input(node, pipeline, context, 'volume')

        volumes: Dict[int, np.ndarray] = {}

        if input_vol is not None:
            # Single volume from upstream — apply all configured thresholds to it
            if isinstance(input_vol, dict):
                volumes = input_vol
            else:
                # Use the first configured channel threshold, or ch 0
                for ch_id in settings.channel_thresholds:
                    volumes[ch_id] = input_vol
                if not volumes:
                    volumes[0] = input_vol
                    if not settings.channel_thresholds:
                        # Default threshold if none configured
                        settings.channel_thresholds[0] = config.get('default_threshold', 100)
        else:
            # Try to get volumes from voxel storage (for live 3D view data)
            voxel_storage = context.get_service('voxel_storage')
            if voxel_storage:
                for ch_id in settings.channel_thresholds:
                    try:
                        vol = voxel_storage.get_display_volume(ch_id)
                        if vol is not None:
                            volumes[ch_id] = vol
                    except Exception as e:
                        logger.warning(f"Could not get volume for ch {ch_id}: {e}")

        if not volumes:
            raise RuntimeError("No input volumes available for threshold analysis")

        # Get voxel size from config or context
        voxel_size_um = tuple(config.get('voxel_size_um', [50.0, 50.0, 50.0]))

        # Run analysis
        result = self._service.analyze(
            volumes=volumes,
            settings=settings,
            voxel_size_um=voxel_size_um,
        )

        # Set outputs
        self._set_output(node, context, 'objects', PortType.OBJECT_LIST, result.objects)
        if result.combined_mask is not None:
            self._set_output(node, context, 'mask', PortType.VOLUME, result.combined_mask)
        self._set_output(node, context, 'count', PortType.SCALAR, result.object_count)

        logger.info(
            f"Threshold analysis complete: {result.object_count} objects detected"
        )
