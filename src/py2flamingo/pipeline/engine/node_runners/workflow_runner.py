"""
WorkflowRunner — executes a Workflow node through the workflow facade.

Config:
    template_file: str — path to workflow template .txt file
    use_input_position: bool — override workflow position with input port value
    auto_z_range: bool — auto Z-range from detected object bounding box
    auto_tiling: bool — auto-compute tiling grid from object XY extent vs. FOV
    buffer_percent: float — BBox buffer percentage for Z-range and XY tiling

Inputs:
    trigger — execution ordering
    position — optional stage position override (x, y, z, r)
    z_range — optional object whose bounding box sets the Z range / tiling

Outputs:
    volume — 3D data produced (if available from voxel storage)
    file_path — path to saved workflow data
    completed — trigger for downstream nodes

The facade (``workflow_facade`` service) works on workflow text:
``load_workflow(path) -> bytes`` and ``start_workflow(bytes) -> bool``, as
``services.workflow_service.MVCWorkflowService`` does. The overrides edit that
text (``pipeline/engine/workflow_overrides.py``) and every Start/End position
is checked against the stage limits before anything is sent. A facade must
also say it can report when the workflow has finished
(``reports_workflow_completion``); the live one cannot yet, so the node
refuses to start a workflow on the microscope rather than wait 30 minutes and
then stop it.
"""

import logging
import math
import time
from pathlib import Path
from typing import Any, Optional

from py2flamingo.pipeline.engine.base_runner import AbstractNodeRunner
from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.workflow_overrides import (
    WorkflowEdit,
    WorkflowOverrideError,
    position_from_input,
)
from py2flamingo.pipeline.models.pipeline import Pipeline, PipelineNode
from py2flamingo.pipeline.models.port_types import PortType

logger = logging.getLogger(__name__)


class WorkflowRunner(AbstractNodeRunner):
    """Runs a workflow via the workflow facade and polls for completion."""

    # Max time to wait for workflow completion
    TIMEOUT_SECONDS = 1800  # 30 minutes
    POLL_INTERVAL = 1.0  # seconds between polls

    def run(
        self, node: PipelineNode, pipeline: Pipeline, context: ExecutionContext
    ) -> None:
        config = node.config

        # Get services
        workflow_facade = context.get_service("workflow_facade")

        if not workflow_facade:
            raise RuntimeError("WorkflowFacade service not available in context")

        # Load workflow from template file
        template_file = config.get("template_file")

        # Legacy fallback: old pipelines may have config_mode='inline'
        config_mode = config.get("config_mode", "template")
        if config_mode == "inline":
            logger.warning(
                "Workflow node '%s' uses legacy inline mode which is no longer "
                "supported. Please re-configure using 'Configure Workflow...' "
                "button. Skipping execution.",
                node.name,
            )
            self._set_output(node, context, "completed", PortType.TRIGGER, True)
            return

        if not template_file:
            raise RuntimeError(
                "Workflow node requires a template file. Use 'Configure Workflow...' "
                "to create one."
            )

        try:
            data = self._edited_workflow(
                node, pipeline, context, workflow_facade, template_file
            )
        except WorkflowOverrideError as e:
            raise RuntimeError(f"Workflow '{node.name}': {e}") from e

        if not getattr(workflow_facade, "reports_workflow_completion", False):
            raise RuntimeError(
                f"Workflow '{node.name}' was not started: the workflow service "
                "cannot report when a server workflow has finished, so the "
                f"pipeline would wait {self.TIMEOUT_SECONDS // 60} minutes and then "
                "stop the acquisition. Nothing was sent. Run the template from "
                "the Workflow tab instead (see docs/pipeline_json_format.md, "
                "WORKFLOW)."
            )

        if getattr(workflow_facade, "drives_stage", True):
            # A workflow moves the stage on the server: the emergency stop and
            # an unconfigured scope refuse it as they refuse every move.
            from py2flamingo.services.stage_motion_gate import ensure_motion_allowed

            ensure_motion_allowed()

        # Execute workflow
        logger.info(f"Starting workflow: {node.name}")
        success = workflow_facade.start_workflow(data)
        if not success:
            raise RuntimeError(f"Failed to start workflow for node '{node.name}'")

        # Poll for completion
        start_time = time.time()
        while True:
            if context.check_cancelled():
                workflow_facade.stop_workflow()
                raise RuntimeError("Pipeline canceled during workflow execution")

            status = workflow_facade.get_workflow_status()
            if status is None:
                # Workflow finished (status cleared)
                break

            # Check for completion states
            status_name = status.name if hasattr(status, "name") else str(status)
            if status_name in ("COMPLETED", "IDLE", "STOPPED"):
                break

            if status_name in ("ERROR", "FAILED"):
                raise RuntimeError(f"Workflow failed with status: {status_name}")

            if time.time() - start_time > self.TIMEOUT_SECONDS:
                workflow_facade.stop_workflow()
                raise RuntimeError(f"Workflow timed out after {self.TIMEOUT_SECONDS}s")

            time.sleep(self.POLL_INTERVAL)

        logger.info(f"Workflow completed: {node.name}")

        # Set outputs
        self._set_output(node, context, "completed", PortType.TRIGGER, True)

        # Try to get output file path
        get_current = getattr(workflow_facade, "get_current_workflow", None)
        current = get_current() if callable(get_current) else None
        if current and hasattr(current, "output_path"):
            self._set_output(
                node, context, "file_path", PortType.FILE_PATH, str(current.output_path)
            )

        # Try to get volume data from voxel storage (all channels with data)
        voxel_storage = context.get_service("voxel_storage")
        if voxel_storage:
            try:
                volumes = {}
                for ch_id in range(8):
                    vol = voxel_storage.get_display_volume(ch_id)
                    if vol is not None and vol.any():
                        volumes[ch_id] = vol
                if volumes:
                    self._set_output(node, context, "volume", PortType.VOLUME, volumes)
            except Exception as e:
                logger.debug(f"Could not retrieve volume after workflow: {e}")

    def _edited_workflow(
        self,
        node: PipelineNode,
        pipeline: Pipeline,
        context: ExecutionContext,
        workflow_facade: Any,
        template_file: str,
    ) -> bytes:
        """The template with the node's overrides applied and its positions
        checked: the bytes to send. Raises WorkflowOverrideError, having sent
        nothing, when an override cannot be applied or a position is outside
        the stage limits."""
        config = node.config
        workflow = WorkflowEdit.from_bytes(
            workflow_facade.load_workflow(Path(template_file))
        )

        # Override position from input port. Unconnected port: the template's
        # own position (documented: no effect while the port is unconnected).
        position_data = self._get_input(node, pipeline, context, "position")
        if position_data is not None and config.get("use_input_position", True):
            pos = position_from_input(position_data)
            workflow.move_start_to(*pos)
            logger.info(f"Overriding workflow start position from input: {pos}")

        auto_z = config.get("auto_z_range", False)
        auto_tiling = config.get("auto_tiling", False)
        if auto_z or auto_tiling:
            # Prefer the same object from position input; fall back to z_range port
            detected_obj = None
            if position_data is not None and hasattr(position_data, "bounding_box"):
                detected_obj = position_data
            else:
                z_range_input = self._get_input(node, pipeline, context, "z_range")
                if z_range_input is not None and hasattr(z_range_input, "bounding_box"):
                    detected_obj = z_range_input
            coord_config = context.get_service("coordinate_config")
            switches = " and ".join(
                label
                for on, label in (
                    (auto_z, "Auto Z-Range"),
                    (auto_tiling, "Auto Tiling"),
                )
                if on
            )
            if detected_obj is None or not coord_config:
                missing = (
                    "no object arrived on the position or z_range input"
                    if detected_obj is None
                    else "no coordinate configuration is available"
                )
                raise WorkflowOverrideError(
                    f"{switches} is on but {missing}, so the template would run "
                    "unchanged. Nothing was sent; wire an object in or untick it."
                )
            try:
                if auto_z:
                    self._apply_auto_z_range(
                        workflow, detected_obj, coord_config, config
                    )
                if auto_tiling:
                    self._apply_auto_tiling(
                        workflow, detected_obj, coord_config, config
                    )
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                raise WorkflowOverrideError(
                    f"could not apply {switches} ({type(e).__name__}: {e}); "
                    "nothing was sent."
                ) from e

        if getattr(workflow_facade, "drives_stage", True):
            workflow.check_within_limits(self._stage_limits(context))
        data = workflow.to_bytes()
        for note in workflow.notes:
            logger.info(f"Workflow '{node.name}' override: {note}")
        return data

    @staticmethod
    def _stage_limits(context: ExecutionContext) -> Optional[dict]:
        """{axis: {min, max}} from the application's limits (the settings json
        narrowed to the server's soft limits), or None when unknown."""
        limits = context.get_service("stage_limits")
        if limits:
            return limits
        controller = context.get_service("position_controller")
        if controller is not None and hasattr(controller, "get_stage_limits"):
            try:
                return controller.get_stage_limits()
            except Exception as e:  # noqa: BLE001 - unknown limits refuse below
                logger.warning(f"Could not read the stage limits: {e}")
        return None

    @staticmethod
    def _apply_auto_z_range(
        workflow: WorkflowEdit, detected_obj: Any, coord_config: dict, config: dict
    ) -> None:
        display_cfg = coord_config["display"]
        stage_cfg = coord_config["stage_control"]
        voxel_um = tuple(display_cfg.get("voxel_size_um", [50.0, 50.0, 50.0]))
        z_range_mm = tuple(stage_cfg["z_range_mm"])
        y_range_mm = tuple(stage_cfg["y_range_mm"])
        x_range_mm = tuple(stage_cfg["x_range_mm"])
        invert_x = stage_cfg.get("invert_x_default", False)

        bb = detected_obj.bounding_box_mm(
            voxel_size_um=voxel_um,
            z_range_mm=z_range_mm,
            y_range_mm=y_range_mm,
            x_range_mm=x_range_mm,
            invert_x=invert_x,
        )

        # Apply proportional buffer
        z_extent = bb["z_max"] - bb["z_min"]
        buffer_frac = config.get("buffer_percent", 25.0) / 100.0
        buffer_mm = z_extent * buffer_frac

        z_bottom = max(z_range_mm[0], bb["z_min"] - buffer_mm)
        z_top = min(z_range_mm[1], bb["z_max"] + buffer_mm)
        planes = workflow.set_z_range(z_bottom, z_top)
        logger.info(
            f"Auto Z-range from bounding box: z={z_bottom:.3f}-{z_top:.3f} mm "
            f"({planes} planes)"
        )

    @staticmethod
    def _apply_auto_tiling(
        workflow: WorkflowEdit, detected_obj: Any, coord_config: dict, config: dict
    ) -> None:
        display_cfg = coord_config["display"]
        voxel_um = tuple(display_cfg.get("voxel_size_um", [50.0, 50.0, 50.0]))

        # Get object XY extent
        x_extent, y_extent, _z_extent = detected_obj.extent_mm(voxel_um)

        # Get FOV from focus_frame config
        focus_cfg = coord_config.get("focus_frame", {})
        fov_x = focus_cfg.get("field_of_view_x_mm", 0.52)
        fov_y = focus_cfg.get("field_of_view_y_mm", 0.52)

        if x_extent <= fov_x and y_extent <= fov_y:
            # Object fits in single FOV — no XY tiling
            workflow.clear_tiling()
            logger.info(
                f"Auto-tiling: {x_extent:.3f}x{y_extent:.3f}mm object "
                f"fits in 1x1 tile (FOV={fov_x:.2f}x{fov_y:.2f}mm), "
                f"skipping XY tiling"
            )
            return

        # Object needs tiling — apply buffer and compute grid
        buffer_frac = config.get("buffer_percent", 25.0) / 100.0
        buffered_x = x_extent * (1 + 2 * buffer_frac)
        buffered_y = y_extent * (1 + 2 * buffer_frac)

        # Overlap from the template when it is a Tile workflow, else 10%
        overlap = workflow.template_overlap_percent()
        if overlap is None:
            overlap = 10.0
        from py2flamingo.utils.tile_geometry import tile_step_mm

        num_tiles_x = max(1, math.ceil(buffered_x / tile_step_mm(fov_x, overlap)))
        num_tiles_y = max(1, math.ceil(buffered_y / tile_step_mm(fov_y, overlap)))
        if not getattr(detected_obj, "stage_coords_available", True):
            raise WorkflowOverrideError(
                "Auto Tiling: the object has no stage coordinates (no coordinate "
                "config was available when it was detected), so there is no "
                "center to tile around; nothing was sent."
            )
        cx, cy, _cz = detected_obj.centroid_stage
        workflow.set_tiling(
            float(cx), float(cy), num_tiles_x, num_tiles_y, fov_x, fov_y, overlap
        )
        logger.info(
            f"Auto-tiling: {x_extent:.3f}x{y_extent:.3f}mm object "
            f"-> {num_tiles_x}x{num_tiles_y} tiles "
            f"(FOV={fov_x:.2f}x{fov_y:.2f}mm, overlap={overlap:.0f}%)"
        )
