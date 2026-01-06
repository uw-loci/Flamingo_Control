"""Unified Workflow Management System for Flamingo Control.

This package provides a clean, consolidated architecture for workflow management,
replacing the previously fragmented system with a single, coherent pipeline.

Architecture:
    WorkflowFacade: Single API entry point for all workflow operations
    WorkflowOrchestrator: Core business logic and lifecycle management
    WorkflowRepository: File I/O operations (load, save, templates)
    WorkflowValidator: Centralized validation logic
    WorkflowExecutor: Hardware execution engine

Usage Example:
    ```python
    from py2flamingo.workflows import WorkflowFacade
    from py2flamingo.models.hardware.stage import Position

    # Create facade (single entry point)
    facade = WorkflowFacade()

    # Create a simple snapshot workflow
    position = Position(x=10, y=20, z=5, r=0)
    workflow = facade.create_snapshot(position, laser_power=10.0)

    # Validate workflow
    facade.validate_workflow(workflow)

    # Execute workflow
    facade.start_workflow(workflow)

    # Monitor progress
    while facade.get_workflow_status() == WorkflowState.EXECUTING:
        progress = facade.get_workflow_progress()
        print(f"Progress: {progress:.1f}%")
        time.sleep(1)
    ```

Migration from Legacy Code:
    # Old way (multiple entry points - DEPRECATED):
    tcp_client.send_workflow(workflow_dict)  # Still available for MinimalGUI
    connection_service.send_workflow(data)    # REMOVED
    connection_manager.send_workflow(data)    # REMOVED
    workflow_service.run_workflow(wf, conn)   # DEPRECATED - raises error

    # New way - use MVCWorkflowService with WorkflowTextFormatter:
    from py2flamingo.services import MVCWorkflowService
    from py2flamingo.utils.workflow_parser import WorkflowTextFormatter

    # Convert dict to text and send
    formatter = WorkflowTextFormatter()
    workflow_bytes = formatter.format_to_bytes(workflow_dict)
    workflow_service.start_workflow(workflow_bytes)

    # Or through the WorkflowController (recommended for UI):
    workflow_controller.start_workflow_from_ui(workflow, workflow_dict)

This consolidation addresses the original issues of:
- 4 different workflow entry points
- 2 incompatible WorkflowService classes
- Command codes hardcoded in multiple places
- Duplicate validation logic
- No single source of truth for workflow state
"""

# Import all components for easy access
from .workflow_facade import (
    WorkflowFacade,
    WorkflowError,
    WorkflowValidationError,
    WorkflowExecutionError
)

from .workflow_orchestrator import (
    WorkflowOrchestrator,
    WorkflowConfiguration,
    WorkflowOrchestrationError
)

from .workflow_repository import (
    WorkflowRepository,
    RepositoryError,
    WorkflowNotFoundError,
    WorkflowFormatError
)

from .workflow_validator import (
    WorkflowValidator,
    ValidationResult,
    HardwareConstraints,
    WorkflowValidationError as ValidatorError  # Avoid name conflict
)

from .workflow_executor import (
    WorkflowExecutor,
    ExecutionState,
    ExecutionContext,
    WorkflowExecutionError as ExecutorError  # Avoid name conflict
)

from .volume_scan_workflow import (
    VolumeScanConfig,
    VolumeScanWorkflow,
    run_volume_scan,
)

# LED2DOverviewWorkflow is imported directly from its module to avoid
# circular import issues:
#   from py2flamingo.workflows.led_2d_overview_workflow import LED2DOverviewWorkflow

# Define public API
__all__ = [
    # Main facade
    'WorkflowFacade',

    # Core components (for advanced usage)
    'WorkflowOrchestrator',
    'WorkflowRepository',
    'WorkflowValidator',
    'WorkflowExecutor',

    # Configuration
    'WorkflowConfiguration',
    'HardwareConstraints',
    'ValidationResult',
    'ExecutionContext',

    # Exceptions
    'WorkflowError',
    'WorkflowValidationError',
    'WorkflowExecutionError',
    'WorkflowOrchestrationError',
    'RepositoryError',
    'WorkflowNotFoundError',
    'WorkflowFormatError',

    # States
    'ExecutionState',

    # Volume scan workflow
    'VolumeScanConfig',
    'VolumeScanWorkflow',
    'run_volume_scan',
]

# Module version
__version__ = '2.0.0'

# Convenience function for getting singleton facade
_facade_instance = None

def get_facade() -> WorkflowFacade:
    """Get singleton WorkflowFacade instance.

    This is the recommended way to access workflow functionality
    throughout the application.

    Returns:
        Singleton WorkflowFacade instance

    Example:
        ```python
        from py2flamingo.workflows import get_facade

        facade = get_facade()
        workflow = facade.create_snapshot(position)
        ```
    """
    global _facade_instance
    if _facade_instance is None:
        _facade_instance = WorkflowFacade()
    return _facade_instance

def reset_facade():
    """Reset the singleton facade instance.

    This is mainly useful for testing or when you need to
    completely reinitialize the workflow system.
    """
    global _facade_instance
    if _facade_instance is not None:
        _facade_instance.reset()
        _facade_instance = None