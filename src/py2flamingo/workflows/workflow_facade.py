"""Workflow Facade - Single API entry point for all workflow operations.

This module provides a unified interface for workflow management,
consolidating all workflow operations into a single, clean API.
"""

import logging
from typing import Optional, List, Dict, Any, Union
from pathlib import Path
from datetime import datetime

from ..models.data.workflow import (
    Workflow, WorkflowType, WorkflowState, WorkflowStep,
    IlluminationSettings, StackSettings, TileSettings,
    TimeLapseSettings, ExperimentSettings
)
from ..models.hardware.stage import Position
from ..core.errors import FlamingoError


logger = logging.getLogger(__name__)


class WorkflowError(FlamingoError):
    """Base exception for workflow-related errors."""
    pass


class WorkflowValidationError(WorkflowError):
    """Raised when workflow validation fails."""
    pass


class WorkflowExecutionError(WorkflowError):
    """Raised when workflow execution fails."""
    pass


class WorkflowFacade:
    """Single entry point for all workflow operations.

    This facade provides a unified interface for:
    - Creating and configuring workflows
    - Loading and saving workflow files
    - Validating workflows
    - Executing workflows
    - Monitoring workflow progress
    - Managing workflow history
    """

    def __init__(self):
        """Initialize the workflow facade with all necessary components."""
        # These will be initialized lazily to avoid circular dependencies
        self._orchestrator = None
        self._repository = None
        self._validator = None
        self._executor = None
        self._current_workflow: Optional[Workflow] = None
        self._workflow_history: List[Workflow] = []

    def _ensure_components(self):
        """Lazily initialize components."""
        if self._orchestrator is None:
            from .workflow_orchestrator import WorkflowOrchestrator
            self._orchestrator = WorkflowOrchestrator()

        if self._repository is None:
            from .workflow_repository import WorkflowRepository
            self._repository = WorkflowRepository()

        if self._validator is None:
            from .workflow_validator import WorkflowValidator
            self._validator = WorkflowValidator()

        if self._executor is None:
            from .workflow_executor import WorkflowExecutor
            self._executor = WorkflowExecutor()

    # ==================== Workflow Creation ====================

    def create_snapshot(self, position: Position,
                       laser_channel: Optional[str] = None,
                       laser_power: float = 5.0,
                       save_data: bool = False) -> Workflow:
        """Create a simple snapshot workflow.

        Args:
            position: Position for snapshot
            laser_channel: Laser channel to use
            laser_power: Laser power in mW
            save_data: Whether to save acquired data

        Returns:
            Configured snapshot workflow

        Example:
            >>> facade = WorkflowFacade()
            >>> pos = Position(x=10, y=20, z=5, r=0)
            >>> workflow = facade.create_snapshot(pos, laser_power=10.0)
        """
        self._ensure_components()

        workflow = Workflow.create_snapshot(
            position=position,
            laser_channel=laser_channel or "Laser 3 488 nm",
            laser_power=laser_power
        )

        workflow.experiment_settings.save_data = save_data

        logger.info(f"Created snapshot workflow at position {position}")
        return workflow

    def create_zstack(self, position: Position,
                     num_planes: int,
                     z_step_um: float,
                     laser_channel: Optional[str] = None,
                     laser_power: float = 5.0) -> Workflow:
        """Create a z-stack workflow.

        Args:
            position: Starting position
            num_planes: Number of z-planes
            z_step_um: Step size in micrometers
            laser_channel: Laser channel to use
            laser_power: Laser power in mW

        Returns:
            Configured z-stack workflow
        """
        self._ensure_components()

        workflow = Workflow(
            workflow_type=WorkflowType.ZSTACK,
            name="Z-Stack",
            start_position=position,
            illumination=IlluminationSettings(
                laser_channel=laser_channel or "Laser 3 488 nm",
                laser_power_mw=laser_power,
                laser_enabled=True
            ),
            stack_settings=StackSettings(
                num_planes=num_planes,
                z_step_um=z_step_um
            ),
            experiment_settings=ExperimentSettings()
        )

        logger.info(f"Created z-stack workflow: {num_planes} planes, {z_step_um}μm step")
        return workflow

    def create_tile_scan(self, start_position: Position,
                        num_tiles_x: int,
                        num_tiles_y: int,
                        tile_size_mm: float,
                        overlap_percent: float = 10.0) -> Workflow:
        """Create a tile scan workflow.

        Args:
            start_position: Top-left corner position
            num_tiles_x: Number of tiles in X
            num_tiles_y: Number of tiles in Y
            tile_size_mm: Size of each tile in mm
            overlap_percent: Overlap between tiles

        Returns:
            Configured tile scan workflow
        """
        self._ensure_components()

        workflow = Workflow(
            workflow_type=WorkflowType.TILE,
            name="Tile Scan",
            start_position=start_position,
            tile_settings=TileSettings(
                num_tiles_x=num_tiles_x,
                num_tiles_y=num_tiles_y,
                tile_size_x_mm=tile_size_mm,
                tile_size_y_mm=tile_size_mm,
                overlap_percent=overlap_percent
            ),
            experiment_settings=ExperimentSettings()
        )

        logger.info(f"Created tile scan: {num_tiles_x}x{num_tiles_y} tiles")
        return workflow

    def create_time_lapse(self, position: Position,
                         num_timepoints: int,
                         interval_seconds: float) -> Workflow:
        """Create a time-lapse workflow.

        Args:
            position: Acquisition position
            num_timepoints: Number of time points
            interval_seconds: Interval between acquisitions

        Returns:
            Configured time-lapse workflow
        """
        self._ensure_components()

        workflow = Workflow(
            workflow_type=WorkflowType.TIME_LAPSE,
            name="Time Lapse",
            start_position=position,
            time_lapse_settings=TimeLapseSettings(
                num_timepoints=num_timepoints,
                interval_seconds=interval_seconds
            ),
            experiment_settings=ExperimentSettings()
        )

        logger.info(f"Created time-lapse: {num_timepoints} points, {interval_seconds}s interval")
        return workflow

    def create_from_dict(self, workflow_dict: Dict[str, Any]) -> Workflow:
        """Create a workflow from a dictionary representation.

        Args:
            workflow_dict: Dictionary containing workflow parameters

        Returns:
            Workflow object

        Raises:
            WorkflowValidationError: If dictionary is invalid
        """
        self._ensure_components()
        return self._orchestrator.create_from_dict(workflow_dict)

    # ==================== File Operations ====================

    def load_workflow(self, file_path: Union[str, Path]) -> Workflow:
        """Load a workflow from a file.

        Args:
            file_path: Path to workflow file

        Returns:
            Loaded workflow

        Raises:
            FileNotFoundError: If file doesn't exist
            WorkflowValidationError: If file content is invalid
        """
        self._ensure_components()

        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Workflow file not found: {file_path}")

        try:
            workflow = self._repository.load(file_path)
            self._validator.validate(workflow)
            logger.info(f"Loaded workflow from {file_path}")
            return workflow
        except Exception as e:
            raise WorkflowValidationError(f"Failed to load workflow: {e}")

    def save_workflow(self, workflow: Workflow,
                     file_path: Optional[Union[str, Path]] = None) -> Path:
        """Save a workflow to a file.

        Args:
            workflow: Workflow to save
            file_path: Optional file path (auto-generated if not provided)

        Returns:
            Path where workflow was saved

        Raises:
            WorkflowValidationError: If workflow is invalid
        """
        self._ensure_components()

        # Validate before saving
        self._validator.validate(workflow)

        # Save to repository
        saved_path = self._repository.save(workflow, file_path)
        logger.info(f"Saved workflow to {saved_path}")
        return saved_path

    def list_saved_workflows(self, directory: Optional[Union[str, Path]] = None) -> List[Path]:
        """List all saved workflow files.

        Args:
            directory: Directory to search (default: workflows directory)

        Returns:
            List of workflow file paths
        """
        self._ensure_components()
        return self._repository.list_workflows(directory)

    def get_workflow_templates(self) -> Dict[str, Workflow]:
        """Get available workflow templates.

        Returns:
            Dictionary of template name to workflow
        """
        self._ensure_components()
        return self._repository.get_templates()

    # ==================== Validation ====================

    def validate_workflow(self, workflow: Workflow) -> bool:
        """Validate a workflow.

        Args:
            workflow: Workflow to validate

        Returns:
            True if valid

        Raises:
            WorkflowValidationError: If validation fails with details
        """
        self._ensure_components()
        return self._validator.validate(workflow)

    def check_hardware_compatibility(self, workflow: Workflow) -> Dict[str, bool]:
        """Check if workflow is compatible with current hardware.

        Args:
            workflow: Workflow to check

        Returns:
            Dictionary of component -> compatibility status
        """
        self._ensure_components()
        return self._validator.check_hardware_compatibility(workflow)

    # ==================== Execution ====================

    def start_workflow(self, workflow: Workflow,
                      dry_run: bool = False) -> bool:
        """Start executing a workflow.

        Args:
            workflow: Workflow to execute
            dry_run: If True, simulate execution without hardware control

        Returns:
            True if workflow started successfully

        Raises:
            WorkflowExecutionError: If execution fails
        """
        self._ensure_components()

        # Validate first
        self.validate_workflow(workflow)

        # Check if another workflow is running
        if self._current_workflow and self._current_workflow.state == WorkflowState.EXECUTING:
            raise WorkflowExecutionError("Another workflow is already running")

        # Start execution
        try:
            success = self._executor.start(workflow, dry_run=dry_run)
            if success:
                self._current_workflow = workflow
                self._workflow_history.append(workflow)
                logger.info(f"Started workflow: {workflow.name}")
            return success
        except Exception as e:
            raise WorkflowExecutionError(f"Failed to start workflow: {e}")

    def stop_workflow(self) -> bool:
        """Stop the currently executing workflow.

        Returns:
            True if workflow was stopped
        """
        self._ensure_components()

        if not self._current_workflow:
            logger.warning("No workflow to stop")
            return False

        success = self._executor.stop()
        if success:
            self._current_workflow.mark_error("User cancelled")
            logger.info("Workflow stopped by user")

        return success

    def pause_workflow(self) -> bool:
        """Pause the currently executing workflow.

        Returns:
            True if workflow was paused
        """
        self._ensure_components()

        if not self._current_workflow:
            return False

        return self._executor.pause()

    def resume_workflow(self) -> bool:
        """Resume a paused workflow.

        Returns:
            True if workflow was resumed
        """
        self._ensure_components()

        if not self._current_workflow:
            return False

        return self._executor.resume()

    # ==================== Monitoring ====================

    def get_current_workflow(self) -> Optional[Workflow]:
        """Get the currently executing/loaded workflow.

        Returns:
            Current workflow or None
        """
        return self._current_workflow

    def get_workflow_status(self) -> Optional[WorkflowState]:
        """Get the status of the current workflow.

        Returns:
            Workflow state or None
        """
        if self._current_workflow:
            return self._current_workflow.state
        return None

    def get_workflow_progress(self) -> float:
        """Get the progress of the current workflow.

        Returns:
            Progress percentage (0-100) or 0 if no workflow
        """
        if self._current_workflow:
            return self._current_workflow.get_progress()
        return 0.0

    def get_current_step(self) -> Optional[WorkflowStep]:
        """Get the currently executing workflow step.

        Returns:
            Current step or None
        """
        if self._current_workflow:
            return self._current_workflow.get_current_step()
        return None

    def get_workflow_history(self, limit: int = 10) -> List[Workflow]:
        """Get recent workflow history.

        Args:
            limit: Maximum number of workflows to return

        Returns:
            List of recent workflows (newest first)
        """
        return list(reversed(self._workflow_history[-limit:]))

    def get_workflow_statistics(self) -> Dict[str, Any]:
        """Get statistics about workflow execution.

        Returns:
            Dictionary with statistics
        """
        self._ensure_components()
        return self._executor.get_statistics()

    # ==================== Configuration ====================

    def set_default_laser(self, laser_channel: str, power_mw: float) -> None:
        """Set default laser settings for new workflows.

        Args:
            laser_channel: Default laser channel
            power_mw: Default power in milliwatts
        """
        self._ensure_components()
        self._orchestrator.set_default_laser(laser_channel, power_mw)

    def set_default_save_directory(self, directory: Union[str, Path]) -> None:
        """Set default save directory for workflow data.

        Args:
            directory: Default save directory
        """
        self._ensure_components()
        self._orchestrator.set_default_save_directory(directory)

    def get_configuration(self) -> Dict[str, Any]:
        """Get current workflow configuration.

        Returns:
            Configuration dictionary
        """
        self._ensure_components()
        return self._orchestrator.get_configuration()

    # ==================== Utility Methods ====================

    def estimate_workflow_duration(self, workflow: Workflow) -> float:
        """Estimate how long a workflow will take.

        Args:
            workflow: Workflow to estimate

        Returns:
            Estimated duration in seconds
        """
        return workflow.estimate_duration()

    def estimate_data_size(self, workflow: Workflow,
                          bytes_per_image: int = 4_000_000) -> float:
        """Estimate data size for a workflow.

        Args:
            workflow: Workflow to estimate
            bytes_per_image: Estimated bytes per image

        Returns:
            Estimated size in GB
        """
        total_images = workflow.calculate_total_images()
        total_bytes = total_images * bytes_per_image
        return total_bytes / (1024 ** 3)

    def clear_history(self) -> None:
        """Clear workflow history."""
        self._workflow_history.clear()
        logger.info("Cleared workflow history")

    def reset(self) -> None:
        """Reset the facade to initial state."""
        self._current_workflow = None
        self._workflow_history.clear()
        if self._executor:
            self._executor.reset()
        logger.info("Workflow facade reset")

    # ==================== Context Manager Support ====================

    def __enter__(self):
        """Context manager entry."""
        self._ensure_components()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensure cleanup."""
        if self._current_workflow and self._current_workflow.state == WorkflowState.EXECUTING:
            self.stop_workflow()
        return False