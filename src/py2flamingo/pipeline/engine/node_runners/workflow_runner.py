"""
WorkflowRunner — executes a Workflow node by delegating to WorkflowFacade.

Config:
    template_file: str — path to workflow template .txt file
    use_input_position: bool — override workflow position with input port value
    auto_z_range: bool — auto Z-range from detected object bounding box
    buffer_percent: float — BBox buffer percentage for Z-range

Inputs:
    trigger — execution ordering
    position — optional stage position override (x, y, z, r)

Outputs:
    volume — 3D data produced (if available from voxel storage)
    file_path — path to saved workflow data
    completed — trigger for downstream nodes
"""

import logging
import time
from pathlib import Path
from typing import Optional

from py2flamingo.pipeline.models.port_types import PortType
from py2flamingo.pipeline.models.pipeline import Pipeline, PipelineNode
from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.node_runners.base_runner import AbstractNodeRunner

logger = logging.getLogger(__name__)


class WorkflowRunner(AbstractNodeRunner):
    """Runs a workflow via WorkflowFacade and polls for completion."""

    # Max time to wait for workflow completion
    TIMEOUT_SECONDS = 1800  # 30 minutes
    POLL_INTERVAL = 1.0     # seconds between polls

    def run(self, node: PipelineNode, pipeline: Pipeline,
            context: ExecutionContext) -> None:
        config = node.config

        # Get services
        workflow_facade = context.get_service('workflow_facade')
        workflow_queue = context.get_service('workflow_queue_service')

        if not workflow_facade:
            raise RuntimeError("WorkflowFacade service not available in context")

        # Load workflow from template file
        template_file = config.get('template_file')

        # Legacy fallback: old pipelines may have config_mode='inline'
        config_mode = config.get('config_mode', 'template')
        if config_mode == 'inline':
            logger.warning(
                "Workflow node '%s' uses legacy inline mode which is no longer "
                "supported. Please re-configure using 'Configure Workflow...' "
                "button. Skipping execution.", node.name
            )
            self._set_output(node, context, 'completed', PortType.TRIGGER, True)
            return

        if not template_file:
            raise RuntimeError(
                "Workflow node requires a template file. Use 'Configure Workflow...' "
                "to create one."
            )

        workflow = workflow_facade.load_workflow(Path(template_file))

        # Override position from input port
        position_data = self._get_input(node, pipeline, context, 'position')
        if position_data is not None and config.get('use_input_position', True):
            # position_data may be a DetectedObject or a tuple (x, y, z, r)
            if hasattr(position_data, 'centroid_stage'):
                # DetectedObject — convert to Position
                sx, sy, sz = position_data.centroid_stage
                from py2flamingo.models import Position
                pos = Position(x=sx, y=sy, z=sz, r=0.0)
                workflow.start_position = pos
                logger.info(f"Overriding workflow position from DetectedObject: {pos}")
            elif isinstance(position_data, (tuple, list)) and len(position_data) >= 3:
                from py2flamingo.models import Position
                pos = Position(
                    x=position_data[0], y=position_data[1], z=position_data[2],
                    r=position_data[3] if len(position_data) > 3 else 0.0
                )
                workflow.start_position = pos
                logger.info(f"Overriding workflow position from tuple: {pos}")

        # Z-range override from DetectedObject bounding box
        if config.get('auto_z_range', False):
            # Prefer the same object from position input; fall back to z_range port
            detected_obj = None
            if position_data is not None and hasattr(position_data, 'bounding_box'):
                detected_obj = position_data
            else:
                z_range_input = self._get_input(node, pipeline, context, 'z_range')
                if z_range_input is not None and hasattr(z_range_input, 'bounding_box'):
                    detected_obj = z_range_input

            if detected_obj is not None:
                coord_config = context.get_service('coordinate_config')
                if coord_config:
                    try:
                        display_cfg = coord_config['display']
                        stage_cfg = coord_config['stage_control']
                        voxel_um = tuple(display_cfg.get('voxel_size_um', [50.0, 50.0, 50.0]))
                        z_range_mm = tuple(stage_cfg['z_range_mm'])
                        y_range_mm = tuple(stage_cfg['y_range_mm'])
                        x_range_mm = tuple(stage_cfg['x_range_mm'])
                        invert_x = stage_cfg.get('invert_x_default', False)

                        bb = detected_obj.bounding_box_mm(
                            voxel_size_um=voxel_um,
                            z_range_mm=z_range_mm,
                            y_range_mm=y_range_mm,
                            x_range_mm=x_range_mm,
                            invert_x=invert_x,
                        )

                        # Apply proportional buffer
                        z_extent = bb['z_max'] - bb['z_min']
                        buffer_frac = config.get('buffer_percent', 25.0) / 100.0
                        buffer_mm = z_extent * buffer_frac

                        z_bottom = max(z_range_mm[0], bb['z_min'] - buffer_mm)
                        z_top = min(z_range_mm[1], bb['z_max'] + buffer_mm)
                        total_z_um = (z_top - z_bottom) * 1000.0

                        # Set start Z to bottom of buffered region
                        workflow.start_position.z = z_bottom

                        # Update stack settings z_range_um → validate() recalculates num_planes
                        if workflow.stack_settings is None:
                            from py2flamingo.models.data.workflow import StackSettings
                            workflow.stack_settings = StackSettings()
                        workflow.stack_settings.z_range_um = total_z_um
                        workflow.stack_settings.validate()

                        logger.info(
                            f"Auto Z-range from bounding box: "
                            f"z={z_bottom:.3f}-{z_top:.3f} mm "
                            f"({total_z_um:.1f} um, "
                            f"{workflow.stack_settings.num_planes} planes, "
                            f"step={workflow.stack_settings.z_step_um:.1f} um)"
                        )
                    except (KeyError, TypeError, AttributeError) as e:
                        logger.warning(f"Could not apply auto Z-range: {e}")

        # Execute workflow
        logger.info(f"Starting workflow: {node.name}")
        success = workflow_facade.start_workflow(workflow)
        if not success:
            raise RuntimeError(f"Failed to start workflow for node '{node.name}'")

        # Poll for completion
        start_time = time.time()
        while True:
            if context.check_cancelled():
                workflow_facade.stop_workflow()
                raise RuntimeError("Pipeline cancelled during workflow execution")

            status = workflow_facade.get_workflow_status()
            if status is None:
                # Workflow finished (status cleared)
                break

            # Check for completion states
            status_name = status.name if hasattr(status, 'name') else str(status)
            if status_name in ('COMPLETED', 'IDLE', 'STOPPED'):
                break

            if status_name in ('ERROR', 'FAILED'):
                raise RuntimeError(f"Workflow failed with status: {status_name}")

            if time.time() - start_time > self.TIMEOUT_SECONDS:
                workflow_facade.stop_workflow()
                raise RuntimeError(
                    f"Workflow timed out after {self.TIMEOUT_SECONDS}s"
                )

            time.sleep(self.POLL_INTERVAL)

        logger.info(f"Workflow completed: {node.name}")

        # Set outputs
        self._set_output(node, context, 'completed', PortType.TRIGGER, True)

        # Try to get output file path
        current = workflow_facade.get_current_workflow()
        if current and hasattr(current, 'output_path'):
            self._set_output(
                node, context, 'file_path', PortType.FILE_PATH,
                str(current.output_path)
            )

        # Try to get volume data from voxel storage (all channels with data)
        voxel_storage = context.get_service('voxel_storage')
        if voxel_storage:
            try:
                volumes = {}
                for ch_id in range(4):
                    vol = voxel_storage.get_display_volume(ch_id)
                    if vol is not None and vol.any():
                        volumes[ch_id] = vol
                if volumes:
                    self._set_output(
                        node, context, 'volume', PortType.VOLUME, volumes
                    )
            except Exception as e:
                logger.debug(f"Could not retrieve volume after workflow: {e}")
