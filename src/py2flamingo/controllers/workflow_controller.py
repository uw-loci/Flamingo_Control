# ============================================================================
# src/py2flamingo/controllers/workflow_controller.py
"""
Workflow Controller for Flamingo MVC Architecture.

Orchestrates workflow operations between UI and service layer.
Handles user actions related to workflow execution.
"""

import logging
from typing import Tuple, Dict, Any, List
from pathlib import Path

from ..services import MVCWorkflowService
from ..models import ConnectionModel, ConnectionState


class WorkflowController:
    """
    Controller for workflow operations.

    Orchestrates workflow UI interactions with the workflow service layer.
    Validates workflow files before sending and provides user-friendly feedback.

    Attributes:
        _service: Workflow service for workflow operations
        _connection_model: Connection model to check connection status
        _logger: Logger instance
        _current_workflow_path: Path to currently loaded workflow
    """

    def __init__(self, service: MVCWorkflowService, connection_model: ConnectionModel):
        """
        Initialize controller with dependencies.

        Args:
            service: Workflow service for workflow operations
            connection_model: Connection model to check connection status
        """
        self._service = service
        self._connection_model = connection_model
        self._logger = logging.getLogger(__name__)
        self._current_workflow_path: Optional[Path] = None

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

        # Attempt to load via service
        try:
            self._service.load_workflow(str(path))
            self._current_workflow_path = path
            self._logger.info(f"Loaded workflow: {path.name}")
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
        if not self._current_workflow_path:
            return (False, "No workflow loaded. Load a workflow file first.")

        # Attempt to start workflow
        try:
            self._service.start_workflow()
            self._logger.info(f"Started workflow: {self._current_workflow_path.name}")
            return (True, f"Workflow started: {self._current_workflow_path.name}")

        except ConnectionError:
            self._logger.error("Not connected when starting workflow")
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

        # Attempt to stop workflow
        try:
            self._service.stop_workflow()
            self._logger.info("Stopped workflow")
            return (True, "Workflow stopped successfully")

        except ConnectionError:
            self._logger.error("Not connected when stopping workflow")
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
        """
        status = self._service.get_workflow_status()

        return {
            'loaded': self._current_workflow_path is not None,
            'running': status.get('running', False),
            'workflow_name': self._current_workflow_path.name if self._current_workflow_path else None,
            'workflow_path': str(self._current_workflow_path) if self._current_workflow_path else None,
        }

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

    def _is_connected(self) -> bool:
        """
        Check if currently connected to microscope.

        Returns:
            True if connected, False otherwise
        """
        status = self._connection_model.status
        return status.state == ConnectionState.CONNECTED
