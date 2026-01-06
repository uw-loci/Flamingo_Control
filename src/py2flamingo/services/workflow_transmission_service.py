"""
Workflow Transmission Service - Single funnel point for sending workflows to microscope.

This module provides a centralized service for converting and transmitting
workflows to the microscope. ALL workflow send operations should go through
this service to ensure consistency and maintainability.

Usage:
    service = WorkflowTransmissionService(connection_service)

    # From UI dict
    success, msg = service.execute_workflow_from_dict(workflow_dict)

    # From Workflow model
    success, msg = service.execute_workflow(workflow)

    # From file
    success, msg = service.execute_workflow_from_file(path)
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List, Union
from datetime import datetime

from py2flamingo.models.data.workflow import (
    Workflow, WorkflowType, WorkflowState,
    IlluminationSettings, StackSettings, TileSettings,
    TimeLapseSettings, ExperimentSettings
)
from py2flamingo.models.microscope import Position
from py2flamingo.models.command import WorkflowCommand
from py2flamingo.core.tcp_protocol import CommandCode


class WorkflowTransmissionService:
    """
    Centralized workflow orchestration service.

    This is the SINGLE FUNNEL POINT for all workflow operations.
    All UI components, controllers, and services should use this class
    to create and execute workflows.

    Responsibilities:
    - Convert various input formats to workflow text
    - Validate workflow configurations
    - Send workflows to the microscope
    - Optionally save workflow files to disk
    - Track workflow execution state

    Attributes:
        connection_service: MVCConnectionService for sending commands
        workflows_dir: Directory for saving workflow files
        logger: Logger instance
    """

    def __init__(self, connection_service: 'MVCConnectionService',
                 workflows_dir: Optional[Path] = None):
        """
        Initialize workflow orchestrator.

        Args:
            connection_service: MVCConnectionService for microscope communication
            workflows_dir: Optional directory for saving workflow files
        """
        self.connection_service = connection_service
        self.workflows_dir = workflows_dir or Path("workflows")
        self.logger = logging.getLogger(__name__)

        # Current workflow state
        self._current_workflow: Optional[Workflow] = None
        self._is_executing = False

    # ==========================================================================
    # PUBLIC API - Use these methods to execute workflows
    # ==========================================================================

    def execute_workflow(self, workflow: Workflow,
                        save_to_disk: bool = True) -> Tuple[bool, str]:
        """
        Execute a Workflow model object.

        This is the primary entry point for Workflow dataclass objects.

        Args:
            workflow: Workflow model to execute
            save_to_disk: Whether to save workflow file to disk

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            # Validate workflow
            errors = self.validate_workflow(workflow)
            if errors:
                return False, f"Validation errors: {'; '.join(errors)}"

            # Convert to dict format
            workflow_dict = self._workflow_to_dict(workflow)

            # Execute via dict path
            return self._execute_workflow_dict(workflow_dict, save_to_disk)

        except Exception as e:
            self.logger.error(f"Failed to execute workflow: {e}", exc_info=True)
            return False, f"Error: {str(e)}"

    def execute_workflow_from_dict(self, workflow_dict: Dict[str, Any],
                                   save_to_disk: bool = True) -> Tuple[bool, str]:
        """
        Execute a workflow from dictionary format.

        This is the primary entry point for UI-generated workflow dicts.

        Args:
            workflow_dict: Complete workflow configuration dict
            save_to_disk: Whether to save workflow file to disk

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            # Validate dict structure
            errors = self._validate_workflow_dict(workflow_dict)
            if errors:
                return False, f"Validation errors: {'; '.join(errors)}"

            return self._execute_workflow_dict(workflow_dict, save_to_disk)

        except Exception as e:
            self.logger.error(f"Failed to execute workflow from dict: {e}", exc_info=True)
            return False, f"Error: {str(e)}"

    def execute_workflow_from_file(self, file_path: Path) -> Tuple[bool, str]:
        """
        Execute a workflow from an existing file.

        Args:
            file_path: Path to workflow file

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            if not file_path.exists():
                return False, f"Workflow file not found: {file_path}"

            # Read file
            workflow_bytes = file_path.read_bytes()

            # Send to microscope
            return self._send_workflow_bytes(workflow_bytes)

        except Exception as e:
            self.logger.error(f"Failed to execute workflow from file: {e}", exc_info=True)
            return False, f"Error: {str(e)}"

    def execute_workflow_from_text(self, workflow_text: str,
                                   save_to_disk: bool = False,
                                   filename: str = "workflow.txt") -> Tuple[bool, str]:
        """
        Execute a workflow from text content.

        Args:
            workflow_text: Workflow file content as text
            save_to_disk: Whether to save to disk before sending
            filename: Filename to use if saving

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            workflow_bytes = workflow_text.encode('utf-8')

            if save_to_disk:
                file_path = self.workflows_dir / filename
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(workflow_bytes)
                self.logger.info(f"Saved workflow to: {file_path}")

            return self._send_workflow_bytes(workflow_bytes)

        except Exception as e:
            self.logger.error(f"Failed to execute workflow from text: {e}", exc_info=True)
            return False, f"Error: {str(e)}"

    def stop_workflow(self) -> Tuple[bool, str]:
        """
        Stop the currently executing workflow.

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            if not self.connection_service.is_connected():
                return False, "Not connected to microscope"

            # Send stop command
            from py2flamingo.models.command import Command
            cmd = Command(code=CommandCode.CMD_WORKFLOW_STOP)
            self.connection_service.send_command(cmd)

            self._is_executing = False
            self.logger.info("Workflow stop command sent")
            return True, "Workflow stopped"

        except Exception as e:
            self.logger.error(f"Failed to stop workflow: {e}", exc_info=True)
            return False, f"Error: {str(e)}"

    # ==========================================================================
    # VALIDATION
    # ==========================================================================

    def validate_workflow(self, workflow: Workflow) -> List[str]:
        """
        Validate a Workflow model.

        Args:
            workflow: Workflow to validate

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        # Check basic requirements
        if workflow.workflow_type is None:
            errors.append("Workflow type is required")

        # Check illumination
        if workflow.illumination is None:
            errors.append("Illumination settings required")
        elif not workflow.illumination.laser_enabled and not workflow.illumination.led_enabled:
            errors.append("At least one illumination source must be enabled")

        # Type-specific validation
        if workflow.workflow_type == WorkflowType.ZSTACK:
            if workflow.stack_settings is None:
                errors.append("Z-stack settings required for Z-stack workflow")
            elif workflow.stack_settings.num_planes < 1:
                errors.append("Number of planes must be at least 1")
            elif workflow.stack_settings.z_step_um <= 0:
                errors.append("Z step must be positive")

        elif workflow.workflow_type == WorkflowType.TILE:
            if workflow.tile_settings is None:
                errors.append("Tile settings required for tile workflow")
            elif workflow.tile_settings.tiles_x < 1 or workflow.tile_settings.tiles_y < 1:
                errors.append("Tile count must be at least 1")

        elif workflow.workflow_type == WorkflowType.TIME_LAPSE:
            if workflow.time_lapse_settings is None:
                errors.append("Time-lapse settings required for time-lapse workflow")

        return errors

    def _validate_workflow_dict(self, workflow_dict: Dict[str, Any]) -> List[str]:
        """
        Validate a workflow dictionary.

        Args:
            workflow_dict: Workflow dict to validate

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        # Check required sections
        required_sections = ['Experiment Settings', 'Start Position', 'Illumination Source']
        for section in required_sections:
            if section not in workflow_dict:
                errors.append(f"Missing required section: {section}")

        # Check for at least one illumination source
        if 'Illumination Source' in workflow_dict:
            illum = workflow_dict['Illumination Source']
            has_illumination = False
            for key, value in illum.items():
                if isinstance(value, str) and ' 1' in value:
                    has_illumination = True
                    break
            if not has_illumination:
                errors.append("At least one illumination source must be enabled")

        return errors

    # ==========================================================================
    # INTERNAL IMPLEMENTATION
    # ==========================================================================

    def _execute_workflow_dict(self, workflow_dict: Dict[str, Any],
                               save_to_disk: bool) -> Tuple[bool, str]:
        """
        Internal method to execute a workflow dict.

        Args:
            workflow_dict: Complete workflow configuration
            save_to_disk: Whether to save file to disk

        Returns:
            Tuple of (success: bool, message: str)
        """
        # Convert to text format
        workflow_text = self._dict_to_text(workflow_dict)
        workflow_bytes = workflow_text.encode('utf-8')

        # Save to disk if requested
        if save_to_disk:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"workflow_{timestamp}.txt"
            file_path = self.workflows_dir / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(workflow_bytes)
            self.logger.info(f"Saved workflow to: {file_path}")

        # Send to microscope
        return self._send_workflow_bytes(workflow_bytes)

    def _send_workflow_bytes(self, workflow_bytes: bytes) -> Tuple[bool, str]:
        """
        Send workflow bytes to the microscope.

        This is the FINAL COMMON PATH for all workflow execution.

        Args:
            workflow_bytes: Encoded workflow content

        Returns:
            Tuple of (success: bool, message: str)
        """
        if not self.connection_service.is_connected():
            return False, "Not connected to microscope"

        try:
            # Create workflow command
            cmd = WorkflowCommand(
                code=CommandCode.CMD_WORKFLOW_START,
                workflow_data=workflow_bytes
            )

            # Send command
            response = self.connection_service.send_command(cmd)

            self._is_executing = True
            self.logger.info(f"Workflow started: {len(workflow_bytes)} bytes sent")
            return True, f"Workflow started ({len(workflow_bytes)} bytes)"

        except Exception as e:
            self.logger.error(f"Failed to send workflow: {e}", exc_info=True)
            return False, f"Send error: {str(e)}"

    # ==========================================================================
    # FORMAT CONVERSION
    # ==========================================================================

    def _workflow_to_dict(self, workflow: Workflow) -> Dict[str, Any]:
        """
        Convert Workflow model to dictionary format.

        Args:
            workflow: Workflow model

        Returns:
            Dictionary suitable for workflow file generation
        """
        workflow_dict = {}

        # Experiment Settings
        exp_settings = {
            'Sample': workflow.experiment_settings.sample_name if workflow.experiment_settings else '',
            'Save image drive': workflow.experiment_settings.save_drive if workflow.experiment_settings else '',
            'Save image directory': workflow.experiment_settings.save_directory if workflow.experiment_settings else '',
            'Save image data': 'Tiff',
            'Display max projection': 'true',
            'Save max projection': 'false',
        }

        # Add camera settings
        if workflow.experiment_settings:
            exp_settings['Exposure time (us)'] = workflow.experiment_settings.exposure_us
            exp_settings['Frame rate (f/s)'] = workflow.experiment_settings.frame_rate

        workflow_dict['Experiment Settings'] = exp_settings

        # Start Position
        workflow_dict['Start Position'] = {
            'X (mm)': workflow.start_position.x,
            'Y (mm)': workflow.start_position.y,
            'Z (mm)': workflow.start_position.z,
            'Angle (degrees)': workflow.start_position.r,
        }

        # End Position
        if workflow.end_position:
            workflow_dict['End Position'] = {
                'X (mm)': workflow.end_position.x,
                'Y (mm)': workflow.end_position.y,
                'Z (mm)': workflow.end_position.z,
                'Angle (degrees)': workflow.end_position.r,
            }
        else:
            workflow_dict['End Position'] = workflow_dict['Start Position'].copy()

        # Illumination Source
        illum_dict = {}
        if workflow.illumination:
            if workflow.illumination.laser_enabled and workflow.illumination.laser_channel:
                illum_dict[workflow.illumination.laser_channel] = f"{workflow.illumination.laser_power_mw:.2f} 1"
            if workflow.illumination.led_enabled:
                illum_dict['LED_RGB_Board'] = f"{workflow.illumination.led_intensity_percent:.1f} 1"
        workflow_dict['Illumination Source'] = illum_dict

        # Stack Settings
        stack_dict = {
            'Stack option': 'None',
            'Number of planes': 1,
            'Change in Z axis (mm)': 0.0,
            'Z stage velocity (mm/s)': 0.4,
        }

        if workflow.stack_settings:
            z_range_mm = (workflow.stack_settings.num_planes - 1) * workflow.stack_settings.z_step_um / 1000.0
            stack_dict.update({
                'Stack option': 'ZStack',
                'Number of planes': workflow.stack_settings.num_planes,
                'Change in Z axis (mm)': z_range_mm,
                'Z stage velocity (mm/s)': workflow.stack_settings.z_velocity_mm_s,
            })

        if workflow.tile_settings:
            stack_dict.update({
                'Stack option': 'Tile',
                'Stack option settings 1': workflow.tile_settings.tiles_x,
                'Stack option settings 2': workflow.tile_settings.tiles_y,
            })

        workflow_dict['Stack Settings'] = stack_dict

        return workflow_dict

    def _dict_to_text(self, workflow_dict: Dict[str, Any]) -> str:
        """
        Convert workflow dictionary to workflow file text format.

        Uses the C++ expected format with <Workflow Settings> wrapper,
        4-space indentation, and ` = ` separator.

        Args:
            workflow_dict: Workflow configuration dictionary

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

        # Write all illumination settings (except path settings)
        for key, value in illum.items():
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

    # ==========================================================================
    # STATE ACCESSORS
    # ==========================================================================

    @property
    def is_executing(self) -> bool:
        """Check if a workflow is currently executing."""
        return self._is_executing

    @property
    def current_workflow(self) -> Optional[Workflow]:
        """Get the currently executing workflow, if any."""
        return self._current_workflow
