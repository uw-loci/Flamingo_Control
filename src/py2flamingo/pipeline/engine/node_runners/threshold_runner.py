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

        # Filter channel thresholds by enabled channels
        all_thresholds = config.get('channel_thresholds', {})
        enabled_channels = config.get('enabled_channels', None)
        if enabled_channels is not None:
            if isinstance(enabled_channels, list):
                enabled_channels = set(enabled_channels)
            filtered_thresholds = {
                ch: val for ch, val in all_thresholds.items()
                if ch in enabled_channels or int(ch) in enabled_channels
            }
        else:
            filtered_thresholds = all_thresholds

        # Build settings from config
        settings = ThresholdSettings(
            channel_thresholds=filtered_thresholds,
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

        # Build voxel_to_stage coordinate transform from coordinate_config
        coord_config = context.get_service('coordinate_config')
        voxel_to_stage_fn = None
        voxel_size_um = tuple(config.get('voxel_size_um', [50.0, 50.0, 50.0]))

        if coord_config:
            try:
                stage = coord_config['stage_control']
                display = coord_config['display']
                voxel_um = display.get('voxel_size_um', [50.0, 50.0, 50.0])
                voxel_size_um = tuple(voxel_um)
                z_range = stage['z_range_mm']
                y_range = stage['y_range_mm']
                x_range = stage['x_range_mm']
                invert_x = stage.get('invert_x_default', False)

                def voxel_to_stage(z_vox, y_vox, x_vox,
                                   _z_range=z_range, _y_range=y_range,
                                   _x_range=x_range, _voxel_um=voxel_um,
                                   _invert_x=invert_x):
                    z_mm = _z_range[0] + z_vox * _voxel_um[0] / 1000.0
                    y_mm = _y_range[1] - y_vox * _voxel_um[1] / 1000.0  # Y inverted
                    if _invert_x:
                        x_mm = _x_range[1] - x_vox * _voxel_um[2] / 1000.0
                    else:
                        x_mm = _x_range[0] + x_vox * _voxel_um[2] / 1000.0
                    return (x_mm, y_mm, z_mm)

                voxel_to_stage_fn = voxel_to_stage
            except (KeyError, TypeError) as e:
                logger.warning(f"Could not build voxel_to_stage transform: {e}")

        # Run analysis
        result = self._service.analyze(
            volumes=volumes,
            settings=settings,
            voxel_size_um=voxel_size_um,
            voxel_to_stage_fn=voxel_to_stage_fn,
        )

        # Set outputs
        self._set_output(node, context, 'objects', PortType.OBJECT_LIST, result.objects)
        if result.combined_mask is not None:
            self._set_output(node, context, 'mask', PortType.VOLUME, result.combined_mask)
        self._set_output(node, context, 'count', PortType.SCALAR, result.object_count)

        logger.info(
            f"Threshold analysis complete: {result.object_count} objects detected"
        )
