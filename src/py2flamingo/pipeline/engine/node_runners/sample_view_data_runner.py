"""
SampleViewDataRunner — reads current 3D viewer state as a pipeline source node.

Config:
    channel_0..channel_3: bool — which channels to include

Outputs:
    volume — Dict[int, np.ndarray] of selected channels with data
    position — (x, y, z, r) tuple of current stage position
    config — coordinate config dict (voxel_size_um, stage ranges, invert_x)
"""

import logging
from typing import Dict

import numpy as np

from py2flamingo.pipeline.models.port_types import PortType
from py2flamingo.pipeline.models.pipeline import Pipeline, PipelineNode
from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.node_runners.base_runner import AbstractNodeRunner

logger = logging.getLogger(__name__)


class SampleViewDataRunner(AbstractNodeRunner):
    """Source node that reads current 3D viewer volumes and position."""

    def run(self, node: PipelineNode, pipeline: Pipeline,
            context: ExecutionContext) -> None:
        config = node.config

        # Determine selected channels
        selected_channels = []
        for ch_id in range(4):
            if config.get(f'channel_{ch_id}', True):
                selected_channels.append(ch_id)

        if not selected_channels:
            raise RuntimeError("No channels selected in Sample View Data node")

        # Read volumes from voxel storage
        voxel_storage = context.get_service('voxel_storage')
        if not voxel_storage:
            raise RuntimeError("Voxel storage service not available — is the 3D viewer open?")

        volumes: Dict[int, np.ndarray] = {}
        for ch_id in selected_channels:
            try:
                if hasattr(voxel_storage, 'has_data') and not voxel_storage.has_data(ch_id):
                    continue
                vol = voxel_storage.get_display_volume(ch_id)
                if vol is not None and vol.any():
                    volumes[ch_id] = vol
            except Exception as e:
                logger.warning(f"Could not get volume for channel {ch_id}: {e}")

        if not volumes:
            raise RuntimeError("No volume data available in any selected channel")

        logger.info(f"Sample View Data: read {len(volumes)} channels: {list(volumes.keys())}")

        # Read current stage position
        position = (0.0, 0.0, 0.0, 0.0)
        position_controller = context.get_service('position_controller')
        if position_controller:
            try:
                pos = position_controller.get_current_position()
                if pos is not None:
                    if hasattr(pos, 'x'):
                        position = (pos.x, pos.y, pos.z, getattr(pos, 'r', 0.0))
                    elif isinstance(pos, (tuple, list)) and len(pos) >= 3:
                        position = (pos[0], pos[1], pos[2],
                                    pos[3] if len(pos) > 3 else 0.0)
            except Exception as e:
                logger.warning(f"Could not get current position: {e}")

        # Read coordinate config
        coord_config = context.get_service('coordinate_config')
        if not coord_config:
            coord_config = {}

        # Set outputs
        self._set_output(node, context, 'volume', PortType.VOLUME, volumes)
        self._set_output(node, context, 'position', PortType.POSITION, position)
        self._set_output(node, context, 'config', PortType.ANY, coord_config)

        logger.info(
            f"Sample View Data complete: {len(volumes)} channels, "
            f"position=({position[0]:.2f}, {position[1]:.2f}, {position[2]:.2f})"
        )
