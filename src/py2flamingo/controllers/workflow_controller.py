# ============================================================================
# src/py2flamingo/controllers/workflow_controller.py
"""
Workflow Controller for Flamingo MVC Architecture.

Orchestrates workflow operations between UI and service layer.
Handles user actions related to workflow execution.
"""

import logging
from typing import Tuple, Dict, Any, List, Optional
from pathlib import Path

from ..services import MVCWorkflowService
from ..models import ConnectionModel, ConnectionState, WorkflowModel


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

    def __init__(self, service: MVCWorkflowService, connection_model: ConnectionModel,
                 workflow_model: Optional[WorkflowModel] = None):
        """
        Initialize controller with dependencies.

        Args:
            service: Workflow service for workflow operations
            connection_model: Connection model to check connection status
            workflow_model: Optional workflow model for state tracking
        """
        self._service = service
        self._connection_model = connection_model
        self._workflow_model = workflow_model
        self._logger = logging.getLogger(__name__)
        self._current_workflow_path: Optional[Path] = None
        self._current_workflow_data: Optional[bytes] = None  # Cache workflow data

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
            workflow_data = self._service.load_workflow(path)

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

        # Attempt to start workflow
        try:
            self._service.start_workflow(self._current_workflow_data)

            # Mark workflow as started in model
            if self._workflow_model:
                self._workflow_model.mark_started()

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

            # Mark workflow as completed in model
            if self._workflow_model:
                self._workflow_model.mark_completed()

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

    def start_workflow_from_ui(self, workflow, workflow_dict: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        Start workflow from UI-built Workflow object.

        Converts the Workflow object to workflow file format and sends it.

        Args:
            workflow: Workflow object from UI (or None if using workflow_dict)
            workflow_dict: Optional complete workflow dictionary from view

        Returns:
            Tuple of (success, message)
        """
        # Check connection
        if not self._is_connected():
            return (False, "Must connect to server before starting workflow")

        try:
            # Convert workflow to file content
            if workflow_dict is not None:
                workflow_text = self._workflow_dict_to_text(workflow_dict)
            else:
                workflow_text = self._workflow_to_text(workflow)
            workflow_data = workflow_text.encode('utf-8')

            self._logger.info(f"Generated workflow ({len(workflow_data)} bytes):\n{workflow_text[:500]}...")

            # Store as current workflow
            self._current_workflow_data = workflow_data
            self._current_workflow_path = None  # No file path for UI-built workflows

            # Start the workflow
            self._service.start_workflow(workflow_data)

            # Mark workflow as started in model
            if self._workflow_model:
                self._workflow_model.mark_started()

            workflow_name = workflow.name if hasattr(workflow, 'name') else "UI Workflow"
            self._logger.info(f"Started workflow from UI: {workflow_name}")
            return (True, f"Workflow started: {workflow_name}")

        except ConnectionError:
            self._logger.error("Not connected when starting workflow")
            return (False, "Connection lost. Reconnect before starting workflow.")

        except Exception as e:
            self._logger.exception("Error starting workflow from UI")
            return (False, f"Error starting workflow: {str(e)}")

    def _workflow_to_text(self, workflow) -> str:
        """
        Convert Workflow object to workflow file text format.

        Args:
            workflow: Workflow object with all settings

        Returns:
            Workflow file content as string
        """
        lines = ["<Workflow Settings>"]

        # Experiment Settings section
        lines.append("    <Experiment Settings>")

        # Get experiment settings from workflow
        exp = workflow.experiment_settings if hasattr(workflow, 'experiment_settings') else None

        # Stack settings for plane spacing
        stack = workflow.stack_settings if hasattr(workflow, 'stack_settings') else None
        plane_spacing = stack.z_step_um if stack else 1.0

        lines.append(f"    Plane spacing (um) = {plane_spacing}")
        lines.append("    Frame rate (f/s) = 100.0")  # Default
        lines.append("    Exposure time (us) = 10000")  # Default
        lines.append("    Duration (dd:hh:mm:ss) = 00:00:00:01")
        lines.append("    Interval (dd:hh:mm:ss) = 00:00:00:01")
        lines.append(f"    Sample = {exp.file_prefix if exp else ''}")
        lines.append("    Number of angles = 1")
        lines.append("    Angle step size = 0")
        lines.append("    Region = ")
        lines.append(f"    Save image drive = {str(exp.save_directory.parent) if exp else '/media/deploy/ctlsm1'}")
        lines.append(f"    Save image directory = {exp.save_directory.name if exp else 'data'}")
        lines.append(f"    Comments = {exp.comment if exp else ''}")
        lines.append(f"    Save max projection = {'true' if exp and exp.max_projection_display else 'false'}")
        lines.append(f"    Display max projection = {'true' if exp and exp.max_projection_display else 'true'}")
        lines.append(f"    Save image data = {'Tiff' if exp and exp.save_data else 'NotSaved'}")
        lines.append("    Save to subfolders = false")
        lines.append(f"    Work flow live view enabled = {'true' if exp and exp.display_during_acquisition else 'true'}")
        lines.append("    </Experiment Settings>")

        # Camera Settings section
        lines.append("")
        lines.append("    <Camera Settings>")
        lines.append("    Exposure time (us) = 10000")
        lines.append("    Frame rate (f/s) = 100.0")
        lines.append("    AOI width = 2048")
        lines.append("    AOI height = 2048")
        lines.append("    </Camera Settings>")

        # Stack Settings section
        lines.append("")
        lines.append("    <Stack Settings>")
        lines.append("    Stack index = ")

        if stack:
            z_range_mm = (stack.num_planes - 1) * stack.z_step_um / 1000.0
            lines.append(f"    Change in Z axis (mm) = {z_range_mm:.6f}")
            lines.append(f"    Number of planes = {stack.num_planes}")
            lines.append(f"    Z stage velocity (mm/s) = {stack.z_velocity_mm_s}")
        else:
            lines.append("    Change in Z axis (mm) = 0.001")
            lines.append("    Number of planes = 1")
            lines.append("    Z stage velocity (mm/s) = 0.4")

        lines.append("    Rotational stage velocity (°/s) = 0")
        lines.append("    Auto update stack calculations = true")
        lines.append("    Camera 1 capture percentage = 100")
        lines.append("    Camera 1 capture mode = 0")
        lines.append("    Stack option = None")
        lines.append("    Stack option settings 1 = 0")
        lines.append("    Stack option settings 2 = 0")
        lines.append("    </Stack Settings>")

        # Start Position section
        lines.append("")
        lines.append("    <Start Position>")
        pos = workflow.start_position if hasattr(workflow, 'start_position') else None
        if pos:
            lines.append(f"    X (mm) = {pos.x:.6f}")
            lines.append(f"    Y (mm) = {pos.y:.6f}")
            lines.append(f"    Z (mm) = {pos.z:.6f}")
            lines.append(f"    Angle (degrees) = {pos.r:.2f}")
        else:
            lines.append("    X (mm) = 0.0")
            lines.append("    Y (mm) = 0.0")
            lines.append("    Z (mm) = 10.0")
            lines.append("    Angle (degrees) = 0.0")
        lines.append("    </Start Position>")

        # End Position section
        lines.append("")
        lines.append("    <End Position>")
        end_pos = workflow.end_position if hasattr(workflow, 'end_position') and workflow.end_position else pos
        if end_pos:
            lines.append(f"    X (mm) = {end_pos.x:.6f}")
            lines.append(f"    Y (mm) = {end_pos.y:.6f}")
            lines.append(f"    Z (mm) = {end_pos.z:.6f}")
            lines.append(f"    Angle (degrees) = {end_pos.r:.2f}")
        else:
            lines.append("    X (mm) = 0.0")
            lines.append("    Y (mm) = 0.0")
            lines.append("    Z (mm) = 10.0")
            lines.append("    Angle (degrees) = 0.0")
        lines.append("    </End Position>")

        # Illumination Source section
        lines.append("")
        lines.append("    <Illumination Source>")
        illum = workflow.illumination if hasattr(workflow, 'illumination') else None
        if illum and illum.laser_enabled and illum.laser_channel:
            # Format: "power on/off" where on/off is 1 or 0
            lines.append(f"    {illum.laser_channel} = {illum.laser_power_mw:.2f} 1")
        if illum and illum.led_enabled and illum.led_channel:
            lines.append(f"    {illum.led_channel} = {illum.led_intensity_percent:.1f} 1")
            lines.append("    LED selection = 0 0")  # Default to red
        # Add defaults for other lasers (disabled)
        lines.append("    LED DAC = 42000 0")
        lines.append("    </Illumination Source>")

        # Illumination Path section
        lines.append("")
        lines.append("    <Illumination Path>")
        lines.append("    Left path = ON")
        lines.append("    Right path = OFF")
        lines.append("    </Illumination Path>")

        # Illumination Options section
        lines.append("")
        lines.append("    <Illumination Options>")
        lines.append("    Run stack with multiple lasers on = false")
        lines.append("    </Illumination Options>")

        lines.append("</Workflow Settings>")

        return "\n".join(lines)

    def _workflow_dict_to_text(self, workflow_dict: Dict[str, Any]) -> str:
        """
        Convert workflow dictionary to workflow file text format.

        This method handles the complete workflow dictionary format
        from WorkflowView.get_workflow_dict().

        Args:
            workflow_dict: Complete workflow dictionary from UI

        Returns:
            Workflow file content as string
        """
        lines = ["<Workflow Settings>"]

        # Experiment Settings section
        lines.append("    <Experiment Settings>")
        exp = workflow_dict.get('Experiment Settings', {})

        # Plane spacing (from stack settings or default)
        plane_spacing = exp.get('Plane spacing (um)', 1.0)
        lines.append(f"    Plane spacing (um) = {plane_spacing}")

        # Frame rate and exposure
        frame_rate = exp.get('Frame rate (f/s)', 100.0)
        exposure_time = exp.get('Exposure time (us)', 10000)
        lines.append(f"    Frame rate (f/s) = {frame_rate:.1f}")
        lines.append(f"    Exposure time (us) = {int(exposure_time)}")

        # Time-lapse settings
        duration = exp.get('Duration (dd:hh:mm:ss)', '00:00:00:01')
        interval = exp.get('Interval (dd:hh:mm:ss)', '00:00:00:01')
        lines.append(f"    Duration (dd:hh:mm:ss) = {duration}")
        lines.append(f"    Interval (dd:hh:mm:ss) = {interval}")

        # Sample name
        sample = exp.get('Sample', '')
        lines.append(f"    Sample = {sample}")

        # Multi-angle settings
        num_angles = exp.get('Number of angles', 1)
        angle_step = exp.get('Angle step size', 0)
        lines.append(f"    Number of angles = {num_angles}")
        lines.append(f"    Angle step size = {angle_step}")

        # Region
        region = exp.get('Region', '')
        lines.append(f"    Region = {region}")

        # Save settings
        save_drive = exp.get('Save image drive', '/media/deploy/ctlsm1')
        save_dir = exp.get('Save image directory', 'data')
        lines.append(f"    Save image drive = {save_drive}")
        lines.append(f"    Save image directory = {save_dir}")

        # Comments
        comments = exp.get('Comments', '')
        lines.append(f"    Comments = {comments}")

        # Display/Save options
        save_mip = exp.get('Save max projection', 'false')
        display_mip = exp.get('Display max projection', 'true')
        save_format = exp.get('Save image data', 'Tiff')
        save_subfolders = exp.get('Save to subfolders', 'false')
        live_view = exp.get('Work flow live view enabled', 'true')

        lines.append(f"    Save max projection = {save_mip}")
        lines.append(f"    Display max projection = {display_mip}")
        lines.append(f"    Save image data = {save_format}")
        lines.append(f"    Save to subfolders = {save_subfolders}")
        lines.append(f"    Work flow live view enabled = {live_view}")

        lines.append("    </Experiment Settings>")

        # Camera Settings section
        lines.append("")
        lines.append("    <Camera Settings>")
        cam = workflow_dict.get('Camera Settings', {})

        cam_exposure = cam.get('Exposure time (us)', 10000)
        cam_framerate = cam.get('Frame rate (f/s)', 100.0)
        aoi_width = cam.get('AOI width', 2048)
        aoi_height = cam.get('AOI height', 2048)

        lines.append(f"    Exposure time (us) = {int(cam_exposure)}")
        lines.append(f"    Frame rate (f/s) = {cam_framerate:.1f}")
        lines.append(f"    AOI width = {aoi_width}")
        lines.append(f"    AOI height = {aoi_height}")
        lines.append("    </Camera Settings>")

        # Stack Settings section
        lines.append("")
        lines.append("    <Stack Settings>")
        stack = workflow_dict.get('Stack Settings', {})

        lines.append("    Stack index = ")
        lines.append(f"    Change in Z axis (mm) = {stack.get('Change in Z axis (mm)', 0.001):.6f}")
        lines.append(f"    Number of planes = {stack.get('Number of planes', 1)}")
        lines.append(f"    Z stage velocity (mm/s) = {stack.get('Z stage velocity (mm/s)', 0.4)}")
        lines.append(f"    Rotational stage velocity (°/s) = {stack.get('Rotational stage velocity (°/s)', 0)}")
        lines.append(f"    Auto update stack calculations = {stack.get('Auto update stack calculations', 'true')}")
        lines.append(f"    Camera 1 capture percentage = {stack.get('Camera 1 capture percentage', 100)}")
        lines.append(f"    Camera 1 capture mode = {stack.get('Camera 1 capture mode', 0)}")
        lines.append(f"    Camera 2 capture percentage = {stack.get('Camera 2 capture percentage', 100)}")
        lines.append(f"    Camera 2 capture mode = {stack.get('Camera 2 capture mode', 0)}")
        lines.append(f"    Stack option = {stack.get('Stack option', 'None')}")
        lines.append(f"    Stack option settings 1 = {stack.get('Stack option settings 1', 0)}")
        lines.append(f"    Stack option settings 2 = {stack.get('Stack option settings 2', 0)}")
        lines.append("    </Stack Settings>")

        # Start Position section
        lines.append("")
        lines.append("    <Start Position>")
        start_pos = workflow_dict.get('Start Position', {})
        lines.append(f"    X (mm) = {start_pos.get('X (mm)', 0.0):.6f}")
        lines.append(f"    Y (mm) = {start_pos.get('Y (mm)', 0.0):.6f}")
        lines.append(f"    Z (mm) = {start_pos.get('Z (mm)', 10.0):.6f}")
        lines.append(f"    Angle (degrees) = {start_pos.get('Angle (degrees)', 0.0):.2f}")
        lines.append("    </Start Position>")

        # End Position section
        lines.append("")
        lines.append("    <End Position>")
        end_pos = workflow_dict.get('End Position', start_pos)
        lines.append(f"    X (mm) = {end_pos.get('X (mm)', 0.0):.6f}")
        lines.append(f"    Y (mm) = {end_pos.get('Y (mm)', 0.0):.6f}")
        lines.append(f"    Z (mm) = {end_pos.get('Z (mm)', 10.0):.6f}")
        lines.append(f"    Angle (degrees) = {end_pos.get('Angle (degrees)', 0.0):.2f}")
        lines.append("    </End Position>")

        # Illumination Source section
        lines.append("")
        lines.append("    <Illumination Source>")
        illum = workflow_dict.get('Illumination Source', {})

        # Write all illumination settings
        for key, value in illum.items():
            # Skip path settings - they go in Illumination Path
            if key in ('Left path', 'Right path'):
                continue
            lines.append(f"    {key} = {value}")

        lines.append("    </Illumination Source>")

        # Illumination Path section
        lines.append("")
        lines.append("    <Illumination Path>")
        left_path = illum.get('Left path', 'ON 1')
        right_path = illum.get('Right path', 'OFF 0')
        lines.append(f"    Left path = {left_path}")
        lines.append(f"    Right path = {right_path}")
        lines.append("    </Illumination Path>")

        # Illumination Options section
        lines.append("")
        lines.append("    <Illumination Options>")
        illum_opts = workflow_dict.get('Illumination Options', {})
        multi_laser = illum_opts.get('Run stack with multiple lasers on', 'false')
        lines.append(f"    Run stack with multiple lasers on = {multi_laser}")
        lines.append("    </Illumination Options>")

        lines.append("</Workflow Settings>")

        return "\n".join(lines)

    def _is_connected(self) -> bool:
        """
        Check if currently connected to microscope.

        Returns:
            True if connected, False otherwise
        """
        status = self._connection_model.status
        return status.state == ConnectionState.CONNECTED
