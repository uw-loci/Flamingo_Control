"""Workflow Facade - Single API entry point for all workflow operations.

This module provides a unified interface for workflow management,
consolidating all workflow operations into a single, clean API.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

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

    def create_snapshot(
        self,
        position: Position,
        laser_channel: Optional[str] = None,
        laser_power: float = 5.0,
        save_data: bool = False,
    ) -> Workflow:
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
            laser_power=laser_power,
        )

        workflow.experiment_settings.save_data = save_data

        logger.info(f"Created snapshot workflow at position {position}")
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

    def start_workflow(self, workflow: Workflow, dry_run: bool = False) -> bool:
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
        if (
            self._current_workflow
            and self._current_workflow.state == WorkflowState.EXECUTING
        ):
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

    def get_current_step(self) -> Optional[WorkflowStep]:
        """Get the currently executing workflow step.

        Returns:
            Current step or None
        """
        if self._current_workflow:
            return self._current_workflow.get_current_step()
        return None

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
        if (
            self._current_workflow
            and self._current_workflow.state == WorkflowState.EXECUTING
        ):
            self.stop_workflow()
        return False
