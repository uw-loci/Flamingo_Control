# ============================================================================
# src/py2flamingo/services/image_acquisition_service.py
"""
Service for acquiring images from the microscope.

This service provides high-level methods for various image acquisition modes
including snapshots, brightfield images, and z-stacks. It coordinates between
workflow creation, execution, and image retrieval following the MVC pattern.

Based on legacy code from:
- Flamingo_Control/oldcodereference/take_snapshot.py (lines 14-93)
- Flamingo_Control/oldcodereference/microscope_interactions.py acquire_brightfield_image() (lines 333-389)
"""

import logging
import shutil
import os
from pathlib import Path
from typing import Optional, Any, Dict, Tuple, Union, List

from py2flamingo.core.events import EventManager
from py2flamingo.core.queue_manager import QueueManager
from py2flamingo.models.microscope import Position
from py2flamingo.utils.file_handlers import (
    workflow_to_dict,
    dict_to_workflow,
    dict_comment,
    dict_save_directory
)


class ImageAcquisitionService:
    """
    Service for acquiring images from the microscope.

    This service handles creating workflow configurations for different
    acquisition modes (snapshot, brightfield, z-stack) and executing them
    to retrieve image data.

    Attributes:
        workflow_execution_service: Service for executing workflows
        connection_service: Service for microscope communication
        queue_manager: Manager for data queues
        event_manager: Manager for synchronization events
        position_controller: Controller for stage positioning (optional)
        logger: Logger instance
    """

    # Default constants
    DEFAULT_FRAMERATE = 40.0032  # frames per second
    DEFAULT_PLANE_SPACING = 10  # microns
    DEFAULT_LASER_CHANNEL = "Laser 3 488 nm"
    DEFAULT_LASER_SETTING = "5.00 1"

    def __init__(self,
                 workflow_execution_service: 'WorkflowExecutionService',
                 connection_service: 'ConnectionService',
                 queue_manager: QueueManager,
                 event_manager: EventManager,
                 position_controller: Optional['PositionController'] = None):
        """
        Initialize image acquisition service with dependency injection.

        Args:
            workflow_execution_service: WorkflowExecutionService instance
            connection_service: ConnectionService instance
            queue_manager: QueueManager instance for data flow
            event_manager: EventManager instance for synchronization
            position_controller: Optional PositionController for stage management
        """
        self.workflow_execution_service = workflow_execution_service
        self.connection_service = connection_service
        self.queue_manager = queue_manager
        self.event_manager = event_manager
        self.position_controller = position_controller
        self.logger = logging.getLogger(__name__)

        # Cache workflow base path
        self.workflow_dir = Path("workflows")
        self.workflow_dir.mkdir(parents=True, exist_ok=True)

    def acquire_snapshot(self,
                        position: Union[Position, List[float]],
                        laser_channel: str = DEFAULT_LASER_CHANNEL,
                        laser_power: str = DEFAULT_LASER_SETTING,
                        workflow_name: str = "ZStack.txt",
                        comment: str = "GUI Snapshot",
                        save_directory: str = "Snapshots") -> Optional[Any]:
        """
        Acquire a snapshot image at the specified position.

        This method creates a snapshot workflow with laser illumination enabled,
        sends it to the microscope, and retrieves the resulting image data.

        Based on take_snapshot() from oldcodereference/take_snapshot.py lines 14-93.

        Args:
            position: Target position as Position object or [x, y, z, r] list
            laser_channel: Name of laser channel to use (default: "Laser 3 488 nm")
            laser_power: Laser power setting as "power on/off" (default: "5.00 1")
            workflow_name: Base workflow file to use (default: "ZStack.txt")
            comment: Comment to add to workflow (default: "GUI Snapshot")
            save_directory: Directory name for saving images (default: "Snapshots")

        Returns:
            Image data from the snapshot, or None if terminated

        Raises:
            RuntimeError: If not connected to microscope
            FileNotFoundError: If base workflow file not found
            TimeoutError: If acquisition times out

        Example:
            >>> position = Position(x=10.0, y=20.0, z=5.0, r=0.0)
            >>> image = service.acquire_snapshot(position, laser_channel="Laser 3 488 nm")
        """
        self.logger.info(f"Acquiring snapshot at position {position}")

        # Convert position to list format if needed
        if isinstance(position, Position):
            xyzr_init = position.to_list()
        else:
            xyzr_init = position

        # Validate position format
        if len(xyzr_init) != 4:
            raise ValueError(f"Position must have 4 coordinates [x,y,z,r], got {len(xyzr_init)}")

        # Check connection
        if not self.connection_service.is_connected():
            raise RuntimeError("Not connected to microscope")

        # Clear all event queues for clean start
        self._clear_all_queues()

        # Create snapshot workflow
        workflow_dict = self._create_snapshot_workflow(
            workflow_name=workflow_name,
            xyzr_init=xyzr_init,
            laser_channel=laser_channel,
            laser_setting=laser_power,
            laser_on=True,
            comment=comment,
            save_directory=save_directory
        )

        # Save workflow to files
        self._save_workflow(workflow_dict, "currentSnapshot.txt")
        self._copy_workflow_to_active("currentSnapshot.txt")

        # Execute workflow and retrieve image
        try:
            image_data = self.workflow_execution_service.execute_workflow(
                workflow_dict=workflow_dict,
                xyzr_init=xyzr_init
            )

            self.logger.info("Snapshot acquired successfully")
            return image_data

        except Exception as e:
            self.logger.error(f"Failed to acquire snapshot: {e}")
            raise

    def acquire_brightfield(self,
                           position: Union[Position, List[float]],
                           laser_channel: str = DEFAULT_LASER_CHANNEL,
                           laser_setting: str = DEFAULT_LASER_SETTING,
                           workflow_name: str = "ZStack.txt") -> Optional[Any]:
        """
        Acquire a brightfield image at the specified position.

        This is similar to acquire_snapshot but with laser illumination disabled,
        using only LED illumination for brightfield imaging.

        Based on acquire_brightfield_image() from oldcodereference/microscope_interactions.py
        lines 333-389.

        Args:
            position: Target position as Position object or [x, y, z, r] list
            laser_channel: Name of laser channel (default: "Laser 3 488 nm")
            laser_setting: Laser power setting (default: "5.00 1")
            workflow_name: Base workflow file to use (default: "ZStack.txt")

        Returns:
            Image data from the brightfield acquisition, or None if terminated

        Raises:
            RuntimeError: If not connected to microscope
            FileNotFoundError: If base workflow file not found
            TimeoutError: If acquisition times out

        Example:
            >>> position = [10.0, 20.0, 5.0, 0.0]
            >>> image = service.acquire_brightfield(position)
        """
        self.logger.info(f"Acquiring brightfield image at position {position}")

        # Convert position to list format if needed
        if isinstance(position, Position):
            xyzr_init = position.to_list()
        else:
            xyzr_init = position

        # Validate position format
        if len(xyzr_init) != 4:
            raise ValueError(f"Position must have 4 coordinates [x,y,z,r], got {len(xyzr_init)}")

        # Check connection
        if not self.connection_service.is_connected():
            raise RuntimeError("Not connected to microscope")

        # Create brightfield workflow (laser_on=False)
        workflow_dict = self._create_snapshot_workflow(
            workflow_name=workflow_name,
            xyzr_init=xyzr_init,
            laser_channel=laser_channel,
            laser_setting=laser_setting,
            laser_on=False,  # Key difference: laser off for brightfield
            comment="Brightfield Image",
            save_directory="Brightfield"
        )

        # Save workflow to files
        self._save_workflow(workflow_dict, "currentSnapshot.txt")
        self._copy_workflow_to_active("currentSnapshot.txt")

        # Execute workflow and retrieve image
        try:
            image_data = self.workflow_execution_service.execute_workflow(
                workflow_dict=workflow_dict,
                xyzr_init=xyzr_init
            )

            self.logger.info("Brightfield image acquired successfully")
            return image_data

        except Exception as e:
            self.logger.error(f"Failed to acquire brightfield image: {e}")
            raise

    def acquire_zstack(self,
                      position: Union[Position, List[float]],
                      z_range: float,
                      num_planes: int,
                      laser_channel: str = DEFAULT_LASER_CHANNEL,
                      laser_power: str = DEFAULT_LASER_SETTING,
                      workflow_name: str = "ZStack.txt",
                      comment: str = "Z-Stack Acquisition",
                      save_directory: str = "ZStacks") -> Optional[Any]:
        """
        Acquire a z-stack of images at the specified position.

        This method creates a z-stack workflow that captures multiple planes
        along the z-axis at regular intervals.

        Args:
            position: Starting position as Position object or [x, y, z, r] list
            z_range: Total z-range to cover in millimeters
            num_planes: Number of planes to acquire in the stack
            laser_channel: Name of laser channel to use (default: "Laser 3 488 nm")
            laser_power: Laser power setting as "power on/off" (default: "5.00 1")
            workflow_name: Base workflow file to use (default: "ZStack.txt")
            comment: Comment to add to workflow (default: "Z-Stack Acquisition")
            save_directory: Directory name for saving images (default: "ZStacks")

        Returns:
            Image data from the z-stack, or None if terminated

        Raises:
            RuntimeError: If not connected to microscope
            FileNotFoundError: If base workflow file not found
            ValueError: If z_range or num_planes are invalid
            TimeoutError: If acquisition times out

        Example:
            >>> position = Position(x=10.0, y=20.0, z=5.0, r=0.0)
            >>> images = service.acquire_zstack(position, z_range=0.5, num_planes=50)
        """
        self.logger.info(f"Acquiring z-stack at position {position}: {num_planes} planes over {z_range}mm")

        # Convert position to list format if needed
        if isinstance(position, Position):
            xyzr_init = position.to_list()
        else:
            xyzr_init = list(position)

        # Validate inputs
        if len(xyzr_init) != 4:
            raise ValueError(f"Position must have 4 coordinates [x,y,z,r], got {len(xyzr_init)}")
        if num_planes < 2:
            raise ValueError(f"num_planes must be at least 2, got {num_planes}")
        if z_range <= 0:
            raise ValueError(f"z_range must be positive, got {z_range}")

        # Check connection
        if not self.connection_service.is_connected():
            raise RuntimeError("Not connected to microscope")

        # Calculate plane spacing in millimeters
        plane_spacing_mm = z_range / (num_planes - 1) if num_planes > 1 else 0

        # Create z-stack workflow
        workflow_dict = self._create_zstack_workflow(
            workflow_name=workflow_name,
            xyzr_init=xyzr_init,
            num_planes=num_planes,
            plane_spacing_mm=plane_spacing_mm,
            laser_channel=laser_channel,
            laser_setting=laser_power,
            comment=comment,
            save_directory=save_directory
        )

        # Save workflow to files
        self._save_workflow(workflow_dict, "currentZStack.txt")
        self._copy_workflow_to_active("currentZStack.txt")

        # Execute workflow and retrieve images
        try:
            image_data = self.workflow_execution_service.execute_workflow(
                workflow_dict=workflow_dict,
                xyzr_init=xyzr_init,
                wait_timeout=600.0  # Longer timeout for z-stacks (10 minutes)
            )

            self.logger.info("Z-stack acquired successfully")
            return image_data

        except Exception as e:
            self.logger.error(f"Failed to acquire z-stack: {e}")
            raise

    # ========================================================================
    # Private helper methods
    # ========================================================================

    def _create_snapshot_workflow(self,
                                  workflow_name: str,
                                  xyzr_init: List[float],
                                  laser_channel: str,
                                  laser_setting: str,
                                  laser_on: bool,
                                  comment: str,
                                  save_directory: str) -> Dict[str, Any]:
        """
        Create a snapshot workflow dictionary.

        This method loads a base workflow, modifies it for snapshot acquisition
        with the specified settings, and returns the configured workflow dict.

        Args:
            workflow_name: Base workflow file name
            xyzr_init: Initial position [x, y, z, r]
            laser_channel: Laser channel name
            laser_setting: Laser power setting
            laser_on: Whether to enable laser (True) or use LED only (False)
            comment: Workflow comment
            save_directory: Save directory name

        Returns:
            Configured workflow dictionary

        Raises:
            FileNotFoundError: If base workflow file not found
        """
        # Load base workflow
        workflow_path = self.workflow_dir / workflow_name
        if not workflow_path.exists():
            raise FileNotFoundError(f"Base workflow not found: {workflow_path}")

        snap_dict = workflow_to_dict(workflow_path)

        # Convert to snapshot (single plane at position)
        snap_dict = self._dict_to_snap(snap_dict, xyzr_init, self.DEFAULT_FRAMERATE, self.DEFAULT_PLANE_SPACING)

        # Set illumination (laser or LED)
        snap_dict = self._laser_or_LED(snap_dict, laser_channel, laser_setting, laser_on)

        # Add comment and save directory
        snap_dict = dict_comment(snap_dict, comment)
        snap_dict = dict_save_directory(snap_dict, save_directory)

        return snap_dict

    def _create_zstack_workflow(self,
                                workflow_name: str,
                                xyzr_init: List[float],
                                num_planes: int,
                                plane_spacing_mm: float,
                                laser_channel: str,
                                laser_setting: str,
                                comment: str,
                                save_directory: str) -> Dict[str, Any]:
        """
        Create a z-stack workflow dictionary.

        Args:
            workflow_name: Base workflow file name
            xyzr_init: Initial position [x, y, z, r]
            num_planes: Number of planes in stack
            plane_spacing_mm: Spacing between planes in millimeters
            laser_channel: Laser channel name
            laser_setting: Laser power setting
            comment: Workflow comment
            save_directory: Save directory name

        Returns:
            Configured workflow dictionary

        Raises:
            FileNotFoundError: If base workflow file not found
        """
        # Load base workflow
        workflow_path = self.workflow_dir / workflow_name
        if not workflow_path.exists():
            raise FileNotFoundError(f"Base workflow not found: {workflow_path}")

        zstack_dict = workflow_to_dict(workflow_path)

        # Set start position
        if "Start Position" not in zstack_dict:
            zstack_dict["Start Position"] = {}

        zstack_dict["Start Position"]["X (mm)"] = str(xyzr_init[0])
        zstack_dict["Start Position"]["Y (mm)"] = str(xyzr_init[1])
        zstack_dict["Start Position"]["Z (mm)"] = str(xyzr_init[2])
        zstack_dict["Start Position"]["Angle (degrees)"] = str(xyzr_init[3])

        # Set stack settings
        if "Stack Settings" not in zstack_dict:
            zstack_dict["Stack Settings"] = {}

        zstack_dict["Stack Settings"]["Number of planes"] = str(num_planes)
        zstack_dict["Stack Settings"]["Change in Z axis (mm)"] = str(plane_spacing_mm)

        # Set illumination
        zstack_dict = self._laser_or_LED(zstack_dict, laser_channel, laser_setting, laser_on=True)

        # Add comment and save directory
        zstack_dict = dict_comment(zstack_dict, comment)
        zstack_dict = dict_save_directory(zstack_dict, save_directory)

        # Set experiment settings
        if "Experiment Settings" not in zstack_dict:
            zstack_dict["Experiment Settings"] = {}

        zstack_dict["Experiment Settings"]["Frame rate (f/s)"] = str(self.DEFAULT_FRAMERATE)

        return zstack_dict

    def _dict_to_snap(self,
                     workflow_dict: Dict[str, Any],
                     xyzr_init: List[float],
                     framerate: float,
                     plane_spacing: float) -> Dict[str, Any]:
        """
        Convert a workflow dictionary to snapshot configuration.

        This sets the workflow to capture a single plane at the specified position.
        Replicates the functionality of the legacy dict_to_snap() function.

        Args:
            workflow_dict: Base workflow dictionary
            xyzr_init: Initial position [x, y, z, r]
            framerate: Frame rate in frames per second
            plane_spacing: Plane spacing in microns (not used for single snap)

        Returns:
            Modified workflow dictionary
        """
        # Set start position
        if "Start Position" not in workflow_dict:
            workflow_dict["Start Position"] = {}

        workflow_dict["Start Position"]["X (mm)"] = str(xyzr_init[0])
        workflow_dict["Start Position"]["Y (mm)"] = str(xyzr_init[1])
        workflow_dict["Start Position"]["Z (mm)"] = str(xyzr_init[2])
        workflow_dict["Start Position"]["Angle (degrees)"] = str(xyzr_init[3])

        # Set end position same as start (single plane)
        if "End Position" not in workflow_dict:
            workflow_dict["End Position"] = {}

        workflow_dict["End Position"]["X (mm)"] = str(xyzr_init[0])
        workflow_dict["End Position"]["Y (mm)"] = str(xyzr_init[1])
        workflow_dict["End Position"]["Z (mm)"] = str(xyzr_init[2])
        workflow_dict["End Position"]["Angle (degrees)"] = str(xyzr_init[3])

        # Set stack settings for single plane
        if "Stack Settings" not in workflow_dict:
            workflow_dict["Stack Settings"] = {}

        workflow_dict["Stack Settings"]["Number of planes"] = "1"
        workflow_dict["Stack Settings"]["Change in Z axis (mm)"] = str(plane_spacing / 1000.0)  # Convert um to mm

        # Set experiment settings
        if "Experiment Settings" not in workflow_dict:
            workflow_dict["Experiment Settings"] = {}

        workflow_dict["Experiment Settings"]["Frame rate (f/s)"] = str(framerate)

        return workflow_dict

    def _laser_or_LED(self,
                     workflow_dict: Dict[str, Any],
                     laser_channel: str,
                     laser_setting: str,
                     laser_on: bool) -> Dict[str, Any]:
        """
        Set laser or LED illumination settings in workflow.

        This configures the illumination source based on whether laser is enabled.
        Replicates the functionality of the legacy laser_or_LED() function.

        Args:
            workflow_dict: Workflow dictionary to modify
            laser_channel: Name of laser channel
            laser_setting: Laser power setting as "power on/off"
            laser_on: True to use laser, False to use LED only

        Returns:
            Modified workflow dictionary
        """
        if "Illumination Source" not in workflow_dict:
            workflow_dict["Illumination Source"] = {}

        if laser_on:
            # Enable laser with specified power
            workflow_dict["Illumination Source"][laser_channel] = laser_setting
            # Ensure LED is set (may need to be on or off depending on microscope config)
            if "LED" not in workflow_dict["Illumination Source"]:
                workflow_dict["Illumination Source"]["LED"] = "0.00 0"
        else:
            # Disable laser, use LED for brightfield
            workflow_dict["Illumination Source"][laser_channel] = "0.00 0"
            # Enable LED with default power
            if "LED" not in workflow_dict["Illumination Source"]:
                workflow_dict["Illumination Source"]["LED"] = "50.0 1"

        return workflow_dict

    def _save_workflow(self, workflow_dict: Dict[str, Any], filename: str) -> None:
        """
        Save workflow dictionary to file.

        Args:
            workflow_dict: Workflow configuration to save
            filename: Name of file to save to (in workflows directory)
        """
        output_path = self.workflow_dir / filename
        dict_to_workflow(output_path, workflow_dict)
        self.logger.debug(f"Saved workflow to {output_path}")

    def _copy_workflow_to_active(self, source_filename: str) -> None:
        """
        Copy a workflow file to the active workflow.txt file.

        The microscope reads from workflows/workflow.txt, so we need to copy
        our prepared workflow to this location.

        Args:
            source_filename: Source workflow file name (in workflows directory)
        """
        source = self.workflow_dir / source_filename
        dest = self.workflow_dir / "workflow.txt"
        shutil.copy(source, dest)
        self.logger.debug(f"Copied {source_filename} to workflow.txt")

    def _clear_all_queues(self) -> None:
        """
        Clear all event queues to ensure clean start.

        This clears the image, command, and other data queues to prevent
        stale data from previous operations.
        """
        try:
            self.queue_manager.clear_queue('image')
            self.queue_manager.clear_queue('command')
            self.queue_manager.clear_queue('other_data')
            self.queue_manager.clear_queue('stage_location')

            # Clear relevant events
            self.event_manager.clear_event('visualize')
            self.event_manager.clear_event('send')

            self.logger.debug("Cleared all queues and events")
        except Exception as e:
            self.logger.warning(f"Error clearing queues: {e}")
