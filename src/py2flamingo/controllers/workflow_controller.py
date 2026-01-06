# ============================================================================
# src/py2flamingo/controllers/workflow_controller.py
"""
Workflow Controller for Flamingo MVC Architecture.

Orchestrates workflow operations between UI and service layer.
Handles user actions related to workflow execution.

Uses MVCWorkflowService for sending workflows to the microscope.
"""

import logging
from typing import Tuple, Dict, Any, List, Optional, TYPE_CHECKING
from pathlib import Path
from datetime import datetime

from ..models import ConnectionModel, ConnectionState, WorkflowModel
from ..utils.workflow_parser import WorkflowTextFormatter

if TYPE_CHECKING:
    from ..services import MVCWorkflowService


class WorkflowController:
    """
    Controller for workflow operations.

    Orchestrates workflow UI interactions with MVCWorkflowService.
    Validates workflow files before sending and provides user-friendly feedback.

    Uses MVCWorkflowService for the actual workflow transmission to the microscope.

    Attributes:
        _workflow_service: MVCWorkflowService for sending workflows
        _connection_model: Connection model to check connection status
        _workflow_model: Optional workflow model for state tracking
        _text_formatter: WorkflowTextFormatter for dict-to-text conversion
        _logger: Logger instance
        _current_workflow_path: Path to currently loaded workflow
    """

    def __init__(self, workflow_service: 'MVCWorkflowService', connection_model: ConnectionModel,
                 workflow_model: Optional[WorkflowModel] = None,
                 workflows_dir: Optional[Path] = None):
        """
        Initialize controller with dependencies.

        Args:
            workflow_service: MVCWorkflowService for sending workflows
            connection_model: Connection model to check connection status
            workflow_model: Optional workflow model for state tracking
            workflows_dir: Directory for saving workflow files (default: "workflows")
        """
        self._workflow_service = workflow_service
        self._connection_model = connection_model
        self._workflow_model = workflow_model
        self._workflows_dir = workflows_dir or Path("workflows")
        self._text_formatter = WorkflowTextFormatter()
        self._logger = logging.getLogger(__name__)
        self._current_workflow_path: Optional[Path] = None
        self._current_workflow_data: Optional[bytes] = None  # Cache workflow data
        self._is_executing = False

    def load_workflow(self, file_path: str) -> Tuple[bool, str]:
        """
        Load workflow file for validation and preview.

        Does not send the workflow, just loads and validates it.

        Args:
            file_path: Path to workflow file

        Returns:
            Tuple of (success, message):
                - (True, "Workflow loaded successfully") on success
                - (False, "File not found: ...") if file doesn't exist
                - (False, error message) on validation errors
        """
        # Validate file path
        if not file_path:
            return (False, "Workflow file path cannot be empty")

        path = Path(file_path)

        # Validate file exists and readable
        valid, errors = self.validate_workflow_file(str(path))
        if not valid:
            error_msg = "; ".join(errors)
            return (False, error_msg)

        # Attempt to load workflow file
        try:
            if not path.exists():
                raise FileNotFoundError(f"Workflow file not found: {path}")

            # Read file contents
            workflow_data = path.read_bytes()

            # Cache workflow data and path
            self._current_workflow_data = workflow_data
            self._current_workflow_path = path

            self._logger.info(f"Loaded workflow: {path.name} ({len(workflow_data)} bytes)")
            return (True, f"Workflow loaded successfully: {path.name}")

        except FileNotFoundError:
            self._logger.error(f"Workflow file not found: {path}")
            return (False, f"File not found: {path}")

        except ValueError as e:
            self._logger.error(f"Invalid workflow file: {e}")
            return (False, f"Invalid workflow file: {str(e)}")

        except Exception as e:
            self._logger.exception("Error loading workflow")
            return (False, f"Error loading workflow: {str(e)}")

    def start_workflow(self) -> Tuple[bool, str]:
        """
        Start currently loaded workflow.

        Requires connection to be established before starting.

        Returns:
            Tuple of (success, message):
                - (True, "Workflow started successfully") on success
                - (False, "Not connected to server") if not connected
                - (False, "No workflow loaded") if no workflow loaded
                - (False, error message) on other errors
        """
        # Check connection
        if not self._is_connected():
            return (False, "Must connect to server before starting workflow")

        # Check if workflow loaded
        if not self._current_workflow_path or not self._current_workflow_data:
            return (False, "No workflow loaded. Load a workflow file first.")

        # Attempt to start workflow via workflow service
        try:
            success = self._workflow_service.start_workflow(self._current_workflow_data)

            if success:
                self._is_executing = True
                # Mark workflow as started in model
                if self._workflow_model:
                    self._workflow_model.mark_started()

                self._logger.info(f"Started workflow: {self._current_workflow_path.name}")
                return (True, f"Workflow started: {self._current_workflow_path.name}")
            else:
                return (False, "Failed to start workflow")

        except RuntimeError as e:
            self._logger.error(f"Not connected when starting workflow: {e}")
            return (False, "Connection lost. Reconnect before starting workflow.")

        except ValueError as e:
            self._logger.error(f"Invalid workflow: {e}")
            return (False, f"Invalid workflow: {str(e)}")

        except Exception as e:
            self._logger.exception("Error starting workflow")
            return (False, f"Error starting workflow: {str(e)}")

    def stop_workflow(self) -> Tuple[bool, str]:
        """
        Stop currently running workflow.

        Returns:
            Tuple of (success, message):
                - (True, "Workflow stopped") on success
                - (False, "Not connected to server") if not connected
                - (False, error message) on other errors
        """
        # Check connection
        if not self._is_connected():
            return (False, "Not connected to server")

        # Attempt to stop workflow via workflow service
        try:
            success = self._workflow_service.stop_workflow()

            if success:
                self._is_executing = False
                # Mark workflow as completed in model
                if self._workflow_model:
                    self._workflow_model.mark_completed()

                self._logger.info("Stopped workflow")
                return (True, "Workflow stopped successfully")
            else:
                return (False, "Failed to stop workflow")

        except RuntimeError as e:
            self._logger.error(f"Not connected when stopping workflow: {e}")
            return (False, "Connection lost. Cannot stop workflow.")

        except Exception as e:
            self._logger.exception("Error stopping workflow")
            return (False, f"Error stopping workflow: {str(e)}")

    def get_workflow_status(self) -> Dict[str, Any]:
        """
        Get current workflow execution status.

        Returns:
            Dictionary with workflow state:
                - 'loaded': bool - Whether workflow is loaded
                - 'running': bool - Whether workflow is running
                - 'workflow_name': str or None - Name of loaded workflow
                - 'workflow_path': str or None - Path to workflow file
                - 'execution_time': float or None - Execution time in seconds
        """
        # Check workflow model for running state
        is_running = self._workflow_model.is_running() if self._workflow_model else False
        execution_time = self._workflow_model.get_execution_time() if self._workflow_model else None

        return {
            'loaded': self._current_workflow_path is not None,
            'running': is_running,
            'workflow_name': self._current_workflow_path.name if self._current_workflow_path else None,
            'workflow_path': str(self._current_workflow_path) if self._current_workflow_path else None,
            'execution_time': execution_time,
        }

    def is_workflow_running(self) -> bool:
        """
        Check if workflow is currently executing.

        Returns:
            bool: True if workflow is running, False otherwise
        """
        if self._workflow_model:
            return self._workflow_model.is_running()
        return False

    def validate_workflow_file(self, path: str) -> Tuple[bool, List[str]]:
        """
        Validate workflow file before loading.

        Checks:
        - File exists
        - File is readable
        - File size is reasonable (<10MB)
        - File extension is .txt

        Args:
            path: Path to workflow file

        Returns:
            Tuple of (valid, errors):
                - (True, []) if valid
                - (False, [error1, error2, ...]) if invalid
        """
        errors = []
        file_path = Path(path)

        # Check file exists
        if not file_path.exists():
            errors.append(f"File not found: {path}")
            return (False, errors)

        # Check it's a file (not directory)
        if not file_path.is_file():
            errors.append(f"Path is not a file: {path}")
            return (False, errors)

        # Check file extension
        if file_path.suffix.lower() != '.txt':
            errors.append(f"Workflow file must be .txt format, got {file_path.suffix}")

        # Check file is readable
        if not file_path.exists() or not file_path.is_file():
            errors.append(f"File is not readable: {path}")
        else:
            # Check file size (should be < 10MB)
            try:
                file_size = file_path.stat().st_size
                max_size = 10 * 1024 * 1024  # 10MB
                if file_size > max_size:
                    errors.append(f"File too large: {file_size / (1024*1024):.2f}MB (max 10MB)")
                if file_size == 0:
                    errors.append("File is empty")
            except OSError as e:
                errors.append(f"Cannot read file: {str(e)}")

        # Return validation result
        if errors:
            return (False, errors)
        else:
            return (True, [])

    def start_workflow_from_ui(self, workflow, workflow_dict: Optional[Dict[str, Any]] = None,
                               save_to_disk: bool = True) -> Tuple[bool, str]:
        """
        Start workflow from UI-built Workflow object.

        Uses MVCWorkflowService for sending workflows to the microscope.

        Args:
            workflow: Workflow object from UI (or None if using workflow_dict)
            workflow_dict: Optional complete workflow dictionary from view
            save_to_disk: Whether to save workflow file to disk before sending

        Returns:
            Tuple of (success, message)
        """
        # Check connection
        if not self._is_connected():
            return (False, "Must connect to server before starting workflow")

        try:
            # Convert workflow dict to bytes
            if workflow_dict is not None:
                # Use the text formatter to convert dict to text
                workflow_bytes = self._text_formatter.format_to_bytes(workflow_dict)
            elif hasattr(workflow, 'to_dict'):
                # Convert Workflow model to dict first
                workflow_dict = workflow.to_dict()
                workflow_bytes = self._text_formatter.format_to_bytes(workflow_dict)
            else:
                return (False, "Invalid workflow: no dict or to_dict method")

            # Optionally save to disk
            if save_to_disk:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"workflow_{timestamp}.txt"
                file_path = self._workflows_dir / filename
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(workflow_bytes)
                self._logger.info(f"Saved workflow to: {file_path}")

            # Send workflow to microscope
            success = self._workflow_service.start_workflow(workflow_bytes)

            if success:
                self._is_executing = True
                # Mark workflow as started in model
                if self._workflow_model:
                    self._workflow_model.mark_started()

                workflow_name = workflow.name if hasattr(workflow, 'name') else "UI Workflow"
                self._logger.info(f"Started workflow from UI: {workflow_name}")
                return (True, f"Workflow started: {workflow_name}")
            else:
                return (False, "Failed to start workflow")

        except RuntimeError as e:
            self._logger.error(f"Not connected when starting workflow: {e}")
            return (False, "Connection lost. Reconnect before starting workflow.")

        except Exception as e:
            self._logger.exception("Error starting workflow from UI")
            return (False, f"Error starting workflow: {str(e)}")

    def _is_connected(self) -> bool:
        """
        Check if currently connected to microscope.

        Returns:
            True if connected, False otherwise
        """
        status = self._connection_model.status
        return status.state == ConnectionState.CONNECTED

    @property
    def is_executing(self) -> bool:
        """Check if a workflow is currently executing."""
        return self._is_executing
