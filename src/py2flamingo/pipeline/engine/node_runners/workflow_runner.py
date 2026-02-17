"""
WorkflowRunner — executes a Workflow node by delegating to WorkflowFacade.

Config:
    workflow_type: str — type of workflow (e.g. 'zstack', 'tile_scan')
    template_file: str — path to workflow template file
    use_input_position: bool — override workflow position with input port value

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

        # Load or create workflow
        template_file = config.get('template_file')
        if template_file:
            workflow = workflow_facade.load_workflow(Path(template_file))
        else:
            workflow_type = config.get('workflow_type', 'zstack')
            workflow_dict = config.get('workflow_settings', {})
            if workflow_dict:
                workflow = workflow_facade.create_from_dict(workflow_dict)
            else:
                raise RuntimeError(
                    "Workflow node requires either template_file or workflow_settings"
                )

        # Override position from input port
        position_data = self._get_input(node, pipeline, context, 'position')
        if position_data is not None and config.get('use_input_position', True):
            # position_data may be a DetectedObject or a tuple (x, y, z, r)
            if hasattr(position_data, 'centroid_stage'):
                # DetectedObject — convert to Position
                sx, sy, sz = position_data.centroid_stage
                from py2flamingo.models import Position
                pos = Position(x=sx, y=sy, z=sz, r=0.0)
                workflow.position = pos
                logger.info(f"Overriding workflow position from DetectedObject: {pos}")
            elif isinstance(position_data, (tuple, list)) and len(position_data) >= 3:
                from py2flamingo.models import Position
                pos = Position(
                    x=position_data[0], y=position_data[1], z=position_data[2],
                    r=position_data[3] if len(position_data) > 3 else 0.0
                )
                workflow.position = pos
                logger.info(f"Overriding workflow position from tuple: {pos}")

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

        # Try to get volume data from voxel storage
        voxel_storage = context.get_service('voxel_storage')
        if voxel_storage:
            try:
                # Get the most recently captured channel data
                for ch_id in range(4):  # Try channels 0-3
                    vol = voxel_storage.get_display_volume(ch_id)
                    if vol is not None and vol.any():
                        self._set_output(
                            node, context, 'volume', PortType.VOLUME, vol
                        )
                        break
            except Exception as e:
                logger.debug(f"Could not retrieve volume after workflow: {e}")
