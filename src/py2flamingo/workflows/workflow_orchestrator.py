"""Workflow Orchestrator - Core business logic for workflow management.

This module handles the core workflow business logic, coordinating
between different components and managing workflow lifecycle.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..core.errors import FlamingoError
from ..models.data.workflow import (
    ExperimentSettings,
    IlluminationSettings,
    StackSettings,
    TileSettings,
    TimeLapseSettings,
    Workflow,
    WorkflowState,
    WorkflowStep,
    WorkflowType,
)
from ..models.hardware.stage import Position

logger = logging.getLogger(__name__)


@dataclass
class WorkflowConfiguration:
    """Configuration for workflow orchestrator."""

    default_laser_channel: str = "Laser 3 488 nm"
    default_laser_power_mw: float = 5.0
    default_save_directory: Path = Path("data/workflows")
    default_exposure_ms: float = 10.0
    default_z_step_um: float = 1.0
    default_tile_overlap_percent: float = 10.0
    enable_auto_save: bool = True
    enable_progress_callbacks: bool = True
    max_workflow_history: int = 100


class WorkflowOrchestrationError(FlamingoError):
    """Raised when workflow orchestration fails."""

    pass


class WorkflowOrchestrator:
    """Orchestrates workflow operations and business logic.

    This class coordinates between the repository (storage),
    validator (validation), and executor (execution) to provide
    comprehensive workflow management.
    """

    def __init__(self, config: Optional[WorkflowConfiguration] = None):
        """Initialize the orchestrator.

        Args:
            config: Optional configuration
        """
        self.config = config or WorkflowConfiguration()
        self._active_workflows: Dict[str, Workflow] = {}
        self._workflow_callbacks: Dict[str, List[Callable]] = {
            "started": [],
            "completed": [],
            "error": [],
            "progress": [],
            "step_completed": [],
        }
        self._workflow_defaults: Dict[str, Any] = {}
        self._initialize_defaults()

    def _initialize_defaults(self):
        """Initialize default workflow parameters."""
        self._workflow_defaults = {
            "laser_channel": self.config.default_laser_channel,
            "laser_power_mw": self.config.default_laser_power_mw,
            "exposure_ms": self.config.default_exposure_ms,
            "z_step_um": self.config.default_z_step_um,
            "tile_overlap_percent": self.config.default_tile_overlap_percent,
            "save_directory": self.config.default_save_directory,
        }

    # ==================== Workflow Creation ====================

    def create_from_dict(self, workflow_dict: Dict[str, Any]) -> Workflow:
        """Create a workflow from dictionary representation.

        Args:
            workflow_dict: Dictionary with workflow parameters

        Returns:
            Workflow object

        Raises:
            WorkflowOrchestrationError: If creation fails
        """
        try:
            # Determine workflow type from dictionary
            workflow_type = self._determine_workflow_type(workflow_dict)

            # Extract positions
            start_pos = self._extract_position(workflow_dict.get("Start Position", {}))
            end_pos = self._extract_position(workflow_dict.get("End Position"))

            # Create base workflow
            workflow = Workflow(
                workflow_type=workflow_type,
                name=workflow_dict.get("name", "Custom Workflow"),
                start_position=start_pos,
                end_position=end_pos,
            )

            # Apply illumination settings
            if "Illumination Source" in workflow_dict:
                workflow.illumination = self._parse_illumination(
                    workflow_dict["Illumination Source"]
                )

            # Apply stack settings
            if "Stack Settings" in workflow_dict:
                workflow.stack_settings = self._parse_stack_settings(
                    workflow_dict["Stack Settings"]
                )

            # Apply experiment settings
            if "Experiment Settings" in workflow_dict:
                workflow.experiment_settings = self._parse_experiment_settings(
                    workflow_dict["Experiment Settings"]
                )

            # Apply any additional settings
            self._apply_additional_settings(workflow, workflow_dict)

            logger.info(f"Created workflow from dictionary: {workflow.name}")
            return workflow

        except Exception as e:
            raise WorkflowOrchestrationError(f"Failed to create workflow: {e}")

    def _determine_workflow_type(self, workflow_dict: Dict[str, Any]) -> WorkflowType:
        """Determine workflow type from dictionary."""
        # Check for explicit type
        if "workflow_type" in workflow_dict:
            return WorkflowType(workflow_dict["workflow_type"])

        # Infer from settings
        stack_settings = workflow_dict.get("Stack Settings", {})
        if stack_settings.get("Number of planes", 1) > 1:
            return WorkflowType.ZSTACK

        # Check for tile settings
        if "Tile Settings" in workflow_dict:
            return WorkflowType.TILE

        # Check for time-lapse
        if "Time Settings" in workflow_dict:
            return WorkflowType.TIME_LAPSE

        # Default to snapshot
        return WorkflowType.SNAPSHOT

    def _extract_position(
        self, pos_dict: Optional[Dict[str, Any]]
    ) -> Optional[Position]:
        """Extract position from dictionary."""
        if not pos_dict:
            return None

        return Position(
            x=float(pos_dict.get("X (mm)", 0)),
            y=float(pos_dict.get("Y (mm)", 0)),
            z=float(pos_dict.get("Z (mm)", 0)),
            r=float(pos_dict.get("Angle (degrees)", 0)),
        )

    def _parse_illumination(self, illum_dict: Dict[str, str]) -> IlluminationSettings:
        """Parse illumination settings from dictionary."""
        settings = IlluminationSettings()

        for source, value_str in illum_dict.items():
            if "LED" in source:
                # Parse LED settings
                parts = value_str.split()
                if len(parts) >= 2:
                    settings.led_channel = source
                    settings.led_intensity_percent = float(parts[0])
                    settings.led_enabled = bool(int(parts[1]))
            elif "Laser" in source:
                # Parse laser settings
                parts = value_str.split()
                if len(parts) >= 2:
                    settings.laser_channel = source
                    settings.laser_power_mw = float(parts[0])
                    settings.laser_enabled = bool(int(parts[1]))

        return settings

    def _parse_stack_settings(self, stack_dict: Dict[str, Any]) -> StackSettings:
        """Parse stack settings from dictionary."""
        return StackSettings(
            num_planes=int(stack_dict.get("Number of planes", 1)),
            z_step_um=float(stack_dict.get("Change in Z axis (mm)", 0.01))
            * 1000,  # Convert to um
            z_velocity_mm_s=float(stack_dict.get("Z stage velocity (mm/s)", "0.4")),
            bidirectional=stack_dict.get("Bidirectional", "false").lower() == "true",
        )

    def _parse_experiment_settings(
        self, exp_dict: Dict[str, Any]
    ) -> ExperimentSettings:
        """Parse experiment settings from dictionary."""
        save_format = exp_dict.get("Save image data", "NotSaved")
        save_data = save_format != "NotSaved"

        return ExperimentSettings(
            save_data=save_data,
            save_format=save_format.lower() if save_data else "tiff",
            save_directory=Path(exp_dict.get("Save image directory", "data")),
            comment=exp_dict.get("Comments", ""),
            max_projection_display=exp_dict.get(
                "Display max projection", "true"
            ).lower()
            == "true",
            display_during_acquisition=exp_dict.get(
                "Work flow live view enabled", "false"
            ).lower()
            == "true",
        )

    def _apply_additional_settings(
        self, workflow: Workflow, workflow_dict: Dict[str, Any]
    ):
        """Apply any additional settings from dictionary."""
        # Apply metadata
        if "metadata" in workflow_dict:
            workflow.metadata.update(workflow_dict["metadata"])

        # Apply multi-position settings
        if "positions" in workflow_dict:
            for pos_data in workflow_dict["positions"]:
                if isinstance(pos_data, dict):
                    workflow.positions.append(self._extract_position(pos_data))
                elif isinstance(pos_data, Position):
                    workflow.positions.append(pos_data)

        # Apply channels
        if "channels" in workflow_dict:
            workflow.channels = workflow_dict["channels"]

    # ==================== Workflow Management ====================

    def update_workflow_progress(
        self, workflow: Workflow, step_index: int, status: str = "completed"
    ) -> None:
        """Update workflow progress.

        Args:
            workflow: Workflow being executed
            step_index: Index of completed step
            status: Status of the step
        """
        if step_index < len(workflow.steps):
            step = workflow.steps[step_index]

            if status == "completed":
                step.mark_completed()
                workflow.images_acquired += 1
                workflow.current_step_index = step_index + 1

                # Trigger step callback
                self._trigger_callback("step_completed", workflow, step)

            elif status == "error":
                step.mark_error("Step failed")
                workflow.mark_error(f"Failed at step {step_index}")

                # Trigger error callback
                self._trigger_callback("error", workflow, step)

        # Update workflow state if all steps complete
        if workflow.current_step_index >= len(workflow.steps):
            workflow.mark_completed()
            self._trigger_callback("completed", workflow)

        # Trigger progress callback
        self._trigger_callback("progress", workflow)

    # ==================== Workflow Optimization ====================

    # ==================== Callbacks ====================

    def register_callback(self, event: str, callback: Callable):
        """Register a callback for workflow events.

        Args:
            event: Event type ('started', 'completed', 'error', 'progress')
            callback: Callback function
        """
        if event in self._workflow_callbacks:
            self._workflow_callbacks[event].append(callback)
            logger.debug(f"Registered callback for {event}")

    def unregister_callback(self, event: str, callback: Callable):
        """Unregister a callback.

        Args:
            event: Event type
            callback: Callback to remove
        """
        if event in self._workflow_callbacks:
            if callback in self._workflow_callbacks[event]:
                self._workflow_callbacks[event].remove(callback)

    def _trigger_callback(self, event: str, workflow: Workflow, *args):
        """Trigger callbacks for an event."""
        if self.config.enable_progress_callbacks:
            for callback in self._workflow_callbacks.get(event, []):
                try:
                    callback(workflow, *args)
                except Exception as e:
                    logger.error(f"Callback error for {event}: {e}")

    # ==================== Configuration ====================

    def set_default_laser(self, laser_channel: str, power_mw: float):
        """Set default laser settings."""
        self._workflow_defaults["laser_channel"] = laser_channel
        self._workflow_defaults["laser_power_mw"] = power_mw
        logger.info(f"Set default laser: {laser_channel} at {power_mw} mW")

    def set_default_save_directory(self, directory: Path):
        """Set default save directory."""
        self.config.default_save_directory = Path(directory)
        self._workflow_defaults["save_directory"] = self.config.default_save_directory

    def get_configuration(self) -> Dict[str, Any]:
        """Get current configuration."""
        return {
            "defaults": self._workflow_defaults,
            "config": {
                "default_laser_channel": self.config.default_laser_channel,
                "default_laser_power_mw": self.config.default_laser_power_mw,
                "default_save_directory": str(self.config.default_save_directory),
                "default_exposure_ms": self.config.default_exposure_ms,
                "default_z_step_um": self.config.default_z_step_um,
                "default_tile_overlap_percent": self.config.default_tile_overlap_percent,
                "enable_auto_save": self.config.enable_auto_save,
                "enable_progress_callbacks": self.config.enable_progress_callbacks,
            },
            "active_workflows": len(self._active_workflows),
        }

    # ==================== Utilities ====================

    def reset(self):
        """Reset orchestrator to initial state."""
        self._active_workflows.clear()
        self._workflow_callbacks = {k: [] for k in self._workflow_callbacks}
        self._initialize_defaults()
        logger.info("Orchestrator reset to initial state")
