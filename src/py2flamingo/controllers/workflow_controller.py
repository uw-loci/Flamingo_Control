# ============================================================================
# src/py2flamingo/controllers/workflow_controller.py
"""
Workflow Controller for Flamingo MVC Architecture.

Orchestrates workflow operations between UI and service layer.
Handles user actions related to workflow execution.

Uses MVCWorkflowService for sending workflows to the microscope.
"""

import logging
import time
from typing import Tuple, Dict, Any, List, Optional, TYPE_CHECKING, Callable
from pathlib import Path
from datetime import datetime

from ..models import ConnectionModel, ConnectionState, WorkflowModel
from ..utils.workflow_parser import WorkflowTextFormatter

if TYPE_CHECKING:
    from ..services import MVCWorkflowService, WorkflowTemplateService, AcquisitionTimingService, MVCConnectionService


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
        _connection_service: Optional MVCConnectionService for querying drives
        _template_service: Optional WorkflowTemplateService for template management
        _timing_service: Optional AcquisitionTimingService for time estimation
        _text_formatter: WorkflowTextFormatter for dict-to-text conversion
        _logger: Logger instance
        _current_workflow_path: Path to currently loaded workflow
    """

    def __init__(self, workflow_service: 'MVCWorkflowService', connection_model: ConnectionModel,
                 workflow_model: Optional[WorkflowModel] = None,
                 workflows_dir: Optional[Path] = None,
                 connection_service: Optional['MVCConnectionService'] = None,
                 template_service: Optional['WorkflowTemplateService'] = None,
                 timing_service: Optional['AcquisitionTimingService'] = None):
        """
        Initialize controller with dependencies.

        Args:
            workflow_service: MVCWorkflowService for sending workflows
            connection_model: Connection model to check connection status
            workflow_model: Optional workflow model for state tracking
            workflows_dir: Directory for saving workflow files (default: "workflows")
            connection_service: Optional MVCConnectionService for querying drives
            template_service: Optional WorkflowTemplateService for template management
            timing_service: Optional AcquisitionTimingService for time estimation
        """
        self._workflow_service = workflow_service
        self._connection_model = connection_model
        self._workflow_model = workflow_model
        self._workflows_dir = workflows_dir or Path("workflows")
        self._connection_service = connection_service
        self._template_service = template_service
        self._timing_service = timing_service
        self._text_formatter = WorkflowTextFormatter()
        self._logger = logging.getLogger(__name__)
        self._current_workflow_path: Optional[Path] = None
        self._current_workflow_data: Optional[bytes] = None  # Cache workflow data
        self._is_executing = False
        self._check_stack_callback: Optional[Callable[[bytes], Dict]] = None
        self._current_workflow_start_time: Optional[float] = None
        self._current_workflow_params: Optional[Dict[str, Any]] = None

        # Tile workflow position tracking for Sample View integration
        self._active_tile_position: Optional[Dict] = None
        self._camera_controller = None  # Will be set via setter

    def set_camera_controller(self, camera_controller):
        """Set camera controller reference for tile workflow integration.

        Args:
            camera_controller: CameraController instance
        """
        self._camera_controller = camera_controller

    def set_active_tile_position(self, position: dict):
        """Set position metadata for active tile workflow.

        This enables Sample View integration by passing position data to the
        CameraController, which will intercept and route frames.

        Args:
            position: Dict with x, y, z_min, z_max, filename
        """
        self._active_tile_position = position

        # Pass to CameraController for frame interception
        if self._camera_controller and hasattr(self._camera_controller, 'set_active_tile_position'):
            self._camera_controller.set_active_tile_position(position)
            self._logger.info(f"Set tile position for Sample View integration: {position.get('filename', 'unknown')}")
        else:
            self._logger.warning("Camera controller not available for tile position tracking")

    def _clear_tile_position(self):
        """Clear tile position metadata after workflow completes."""
        if self._active_tile_position:
            self._logger.info(f"Clearing tile position: {self._active_tile_position.get('filename', 'unknown')}")
            self._active_tile_position = None

            # Clear camera controller tile mode
            if self._camera_controller and hasattr(self._camera_controller, 'clear_tile_mode'):
                self._camera_controller.clear_tile_mode()

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

                # Clear tile position for Sample View integration
                self._clear_tile_position()

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
                # Record start time and params for timing tracking
                self._current_workflow_start_time = time.time()
                self._current_workflow_params = self._extract_timing_params(workflow_dict)

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

    # Template Management Methods

    def set_template_service(self, template_service: 'WorkflowTemplateService') -> None:
        """Set the template service for template management."""
        self._template_service = template_service

    def get_template_names(self) -> List[str]:
        """
        Get list of available template names.

        Returns:
            List of template names, empty if no template service
        """
        if self._template_service is None:
            return []
        return self._template_service.get_template_names()

    def save_template(self, name: str, workflow_type: str, workflow_dict: Dict[str, Any],
                     description: str = "") -> Tuple[bool, str]:
        """
        Save current workflow settings as a template.

        Args:
            name: Name for the template
            workflow_type: Type of workflow (SNAPSHOT, ZSTACK, etc.)
            workflow_dict: Complete workflow settings dictionary
            description: Optional description

        Returns:
            Tuple of (success, message)
        """
        if self._template_service is None:
            return (False, "Template service not available")

        try:
            self._template_service.save_template(name, workflow_type, workflow_dict, description)
            self._logger.info(f"Saved template: {name}")
            return (True, f"Template '{name}' saved successfully")
        except ValueError as e:
            return (False, str(e))
        except Exception as e:
            self._logger.error(f"Error saving template: {e}", exc_info=True)
            return (False, f"Error saving template: {str(e)}")

    def load_template(self, name: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Load a template by name.

        Args:
            name: Template name to load

        Returns:
            Tuple of (success, template_data, message)
            template_data is dict with 'workflow_type' and 'settings' keys
        """
        if self._template_service is None:
            return (False, None, "Template service not available")

        try:
            template = self._template_service.get_template(name)
            if template is None:
                return (False, None, f"Template '{name}' not found")

            template_data = {
                'workflow_type': template.workflow_type,
                'settings': template.settings,
                'description': template.description,
            }
            self._logger.info(f"Loaded template: {name}")
            return (True, template_data, f"Template '{name}' loaded")
        except Exception as e:
            self._logger.error(f"Error loading template: {e}", exc_info=True)
            return (False, None, f"Error loading template: {str(e)}")

    def delete_template(self, name: str) -> Tuple[bool, str]:
        """
        Delete a template by name.

        Args:
            name: Template name to delete

        Returns:
            Tuple of (success, message)
        """
        if self._template_service is None:
            return (False, "Template service not available")

        try:
            deleted = self._template_service.delete_template(name)
            if deleted:
                self._logger.info(f"Deleted template: {name}")
                return (True, f"Template '{name}' deleted")
            else:
                return (False, f"Template '{name}' not found")
        except Exception as e:
            self._logger.error(f"Error deleting template: {e}", exc_info=True)
            return (False, f"Error deleting template: {str(e)}")

    # Workflow Validation Methods

    def set_check_stack_callback(self, callback: Callable[[bytes], Dict]) -> None:
        """
        Set callback for hardware CHECK_STACK validation.

        The callback should send CHECK_STACK command (12331) and return result dict.
        """
        self._check_stack_callback = callback

    def check_workflow(self, workflow_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate workflow and calculate estimates.

        Performs:
        1. Local validation (structure, parameters)
        2. Estimate calculations (time, data size, images)
        3. Hardware validation via CHECK_STACK command (if connected)

        Args:
            workflow_dict: Complete workflow settings dictionary

        Returns:
            Dictionary with validation results:
                - valid: bool
                - errors: List[str]
                - warnings: List[str]
                - estimates: Dict with acquisition_time, data_size_gb, total_images, etc.
                - hardware_validation: Dict with valid, message (if connected)
        """
        result = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'estimates': {},
            'hardware_validation': None,
        }

        # 1. Local validation
        errors, warnings = self._validate_workflow_dict(workflow_dict)
        result['errors'] = errors
        result['warnings'] = warnings
        if errors:
            result['valid'] = False

        # 2. Calculate estimates
        result['estimates'] = self._calculate_estimates(workflow_dict)

        # 3. Hardware validation (if connected)
        if self._is_connected() and self._check_stack_callback:
            try:
                # Convert to bytes for CHECK_STACK
                workflow_bytes = self._text_formatter.format_to_bytes(workflow_dict)
                hw_result = self._check_stack_callback(workflow_bytes)
                result['hardware_validation'] = hw_result
                if not hw_result.get('valid', True):
                    result['valid'] = False
                    if hw_result.get('message'):
                        result['errors'].append(f"Hardware: {hw_result['message']}")
            except Exception as e:
                self._logger.error(f"Hardware validation error: {e}", exc_info=True)
                result['hardware_validation'] = {
                    'valid': False,
                    'message': f"Check failed: {str(e)}"
                }
        elif not self._is_connected():
            result['hardware_validation'] = {
                'valid': True,
                'message': "Not connected - hardware validation skipped"
            }

        return result

    def _validate_workflow_dict(self, workflow_dict: Dict[str, Any]) -> Tuple[List[str], List[str]]:
        """
        Validate workflow dictionary structure and parameters.

        Returns:
            Tuple of (errors, warnings)
        """
        errors = []
        warnings = []

        # Check required sections
        required_sections = ['Experiment Settings', 'Camera Settings', 'Start Position']
        for section in required_sections:
            if section not in workflow_dict:
                errors.append(f"Missing required section: {section}")

        # Validate stack settings
        if 'Stack Settings' in workflow_dict:
            stack = workflow_dict['Stack Settings']

            # Number of planes
            num_planes = int(stack.get('Number of planes', 1))
            if num_planes < 1:
                errors.append("Number of planes must be at least 1")
            elif num_planes > 1000:
                warnings.append(f"Large number of planes ({num_planes}) may take extended time")

            # Z step
            z_step_mm = float(stack.get('Change in Z axis (mm)', 0))
            if z_step_mm <= 0 and num_planes > 1:
                errors.append("Z step must be positive for Z-stacks")

            # Z velocity
            z_velocity = float(stack.get('Z stage velocity (mm/s)', 0.1))
            if z_velocity <= 0:
                errors.append("Z velocity must be positive")
            elif z_velocity < 0.01:
                errors.append(f"Z velocity ({z_velocity} mm/s) below minimum (0.01 mm/s)")
            elif z_velocity > 2.0:
                errors.append(f"Z velocity ({z_velocity} mm/s) exceeds maximum (2.0 mm/s)")
            elif z_velocity > 1.5:
                warnings.append(f"Z velocity ({z_velocity} mm/s) near maximum limit")

        # Validate camera settings
        if 'Camera Settings' in workflow_dict:
            camera = workflow_dict['Camera Settings']
            exposure = float(camera.get('Exposure time (us)', 0))
            if exposure <= 0:
                errors.append("Exposure time must be positive")

        # Validate illumination
        if 'Illumination Source' in workflow_dict:
            illum = workflow_dict['Illumination Source']
            has_illumination = False
            for key, value in illum.items():
                if isinstance(value, str) and ' ' in value:
                    parts = value.split()
                    if len(parts) >= 2 and parts[1] == '1':
                        has_illumination = True
                        break
            if not has_illumination:
                warnings.append("No illumination source enabled - images will be dark")

        return errors, warnings

    def _calculate_estimates(self, workflow_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate acquisition estimates from workflow dictionary.

        Returns:
            Dictionary with estimates
        """
        estimates = {}

        # Get parameters
        stack = workflow_dict.get('Stack Settings', {})
        camera = workflow_dict.get('Camera Settings', {})

        num_planes = int(stack.get('Number of planes', 1))
        z_step_mm = float(stack.get('Change in Z axis (mm)', 0))
        z_velocity = float(stack.get('Z stage velocity (mm/s)', 0.1))
        exposure_us = float(camera.get('Exposure time (us)', 1000))
        aoi_width = int(camera.get('AOI width', 2048))
        aoi_height = int(camera.get('AOI height', 2048))

        # Z range
        z_range_um = (num_planes - 1) * (z_step_mm * 1000) if num_planes > 1 else 0
        estimates['z_range_um'] = z_range_um
        estimates['num_planes'] = num_planes
        estimates['z_step_um'] = z_step_mm * 1000

        # Total images
        estimates['total_images'] = num_planes

        # Data size (16-bit grayscale)
        bytes_per_image = aoi_width * aoi_height * 2  # 16-bit = 2 bytes
        total_bytes = bytes_per_image * num_planes
        estimates['data_size_gb'] = total_bytes / (1024 ** 3)

        # Acquisition time estimate
        z_range_mm = z_range_um / 1000
        z_move_time = z_range_mm / z_velocity if z_velocity > 0 else 0
        exposure_time_total = num_planes * exposure_us / 1_000_000  # Convert to seconds
        settle_time = num_planes * 0.001  # 1ms settle per plane
        return_time = z_range_mm / z_velocity if z_velocity > 0 else 0  # Return to start

        theoretical_time = z_move_time + exposure_time_total + settle_time + return_time

        # Apply learned correction if timing service is available
        if self._timing_service:
            # Count lasers from illumination settings
            num_lasers = self._count_enabled_lasers(workflow_dict)
            corrected_time, sample_count = self._timing_service.get_corrected_estimate(
                theoretical_time=theoretical_time,
                num_planes=num_planes,
                num_lasers=num_lasers,
                total_z_travel_mm=z_range_mm * 2  # Round trip
            )
            estimates['acquisition_time'] = corrected_time
            estimates['sample_count'] = sample_count
        else:
            estimates['acquisition_time'] = theoretical_time
            estimates['sample_count'] = 0

        return estimates

    def _count_enabled_lasers(self, workflow_dict: Dict[str, Any]) -> int:
        """Count number of enabled lasers in workflow."""
        count = 0
        illum = workflow_dict.get('Illumination Source', {})
        for key, value in illum.items():
            if isinstance(value, str) and ' ' in value:
                parts = value.split()
                if len(parts) >= 2 and parts[1] == '1':
                    # Check if it's a laser (not LED)
                    if 'Laser' in key or 'nm' in key:
                        count += 1
        return max(1, count)  # At least 1

    def _extract_timing_params(self, workflow_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Extract parameters needed for timing tracking from workflow dict."""
        stack = workflow_dict.get('Stack Settings', {})
        camera = workflow_dict.get('Camera Settings', {})

        num_planes = int(stack.get('Number of planes', 1))
        z_step_mm = float(stack.get('Change in Z axis (mm)', 0))
        z_velocity = float(stack.get('Z stage velocity (mm/s)', 0.1))
        exposure_us = float(camera.get('Exposure time (us)', 1000))

        z_range_mm = (num_planes - 1) * z_step_mm if num_planes > 1 else 0

        return {
            'workflow_type': 'ZSTACK' if num_planes > 1 else 'SNAPSHOT',
            'num_planes': num_planes,
            'num_lasers': self._count_enabled_lasers(workflow_dict),
            'z_velocity_mm_s': z_velocity,
            'z_range_mm': z_range_mm,
            'total_z_travel_mm': z_range_mm * 2,  # Round trip
            'exposure_us': exposure_us,
            'theoretical_duration_s': self._calculate_estimates(workflow_dict).get('acquisition_time', 0),
        }

    # Timing Service Methods

    def set_timing_service(self, timing_service: 'AcquisitionTimingService') -> None:
        """Set the timing service for adaptive time estimation."""
        self._timing_service = timing_service

    def record_workflow_completion(self) -> None:
        """
        Record workflow completion timing for learning.

        Should be called when workflow completes successfully.
        """
        if not self._timing_service:
            return

        if not self._current_workflow_start_time or not self._current_workflow_params:
            self._logger.warning("No workflow timing data to record")
            return

        actual_duration = time.time() - self._current_workflow_start_time
        params = self._current_workflow_params

        try:
            self._timing_service.record_acquisition(
                workflow_type=params['workflow_type'],
                num_planes=params['num_planes'],
                num_lasers=params['num_lasers'],
                z_velocity_mm_s=params['z_velocity_mm_s'],
                z_range_mm=params['z_range_mm'],
                total_z_travel_mm=params['total_z_travel_mm'],
                exposure_us=params['exposure_us'],
                theoretical_duration_s=params['theoretical_duration_s'],
                actual_duration_s=actual_duration
            )
            self._logger.info(
                f"Recorded workflow timing: theoretical={params['theoretical_duration_s']:.2f}s, "
                f"actual={actual_duration:.2f}s"
            )
        except Exception as e:
            self._logger.error(f"Error recording workflow timing: {e}", exc_info=True)
        finally:
            # Clear tracking data
            self._current_workflow_start_time = None
            self._current_workflow_params = None

    def on_workflow_completed(self) -> None:
        """
        Handle workflow completion event.

        Records timing data and updates model state.
        """
        self._is_executing = False

        # Record timing for learning
        self.record_workflow_completion()

        # Update model
        if self._workflow_model:
            self._workflow_model.mark_completed()

        # Clear tile position for Sample View integration
        self._clear_tile_position()

    def on_workflow_cancelled(self) -> None:
        """
        Handle workflow cancellation.

        Does not record timing (incomplete data).
        """
        self._is_executing = False
        self._current_workflow_start_time = None
        self._current_workflow_params = None

        if self._workflow_model:
            self._workflow_model.mark_completed()

        # Clear tile position for Sample View integration
        self._clear_tile_position()
