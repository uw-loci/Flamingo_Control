# src/py2flamingo/models/workflow.py
"""
Data models for workflow representation.

This module contains data structures used to represent workflows
for image acquisition including snapshots, z-stacks, and tile scans.
"""
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from enum import Enum
from datetime import datetime

from .microscope import Position

class WorkflowType(Enum):
    """
    Types of workflows supported by the microscope.
    
    Attributes:
        SNAPSHOT: Single image acquisition
        ZSTACK: Z-stack acquisition
        TILE: Tile scan acquisition
        MULTI_ANGLE: Multi-angle acquisition
        TIME_LAPSE: Time-lapse acquisition
        CUSTOM: Custom workflow
    """
    SNAPSHOT = "snapshot"
    ZSTACK = "zstack"
    TILE = "tile"
    MULTI_ANGLE = "multi_angle"
    TIME_LAPSE = "time_lapse"
    CUSTOM = "custom"

@dataclass
class IlluminationSettings:
    """
    Illumination settings for workflow.
    
    Attributes:
        laser_channel: Selected laser channel name
        laser_power: Laser power percentage (0-100)
        laser_on: Whether laser is enabled
        led_on: Whether LED is enabled
        led_power: LED power percentage
        filter_position: Filter wheel position
    """
    laser_channel: str = "Laser 3 488 nm"
    laser_power: float = 5.0
    laser_on: bool = True
    led_on: bool = False
    led_power: float = 50.0
    filter_position: int = 1
    
    def to_dict(self) -> Dict[str, str]:
        """
        Convert to workflow dictionary format.
        
        Returns:
            Dict[str, str]: Illumination settings for workflow
        """
        settings = {}
        
        # Laser setting: "power on/off"
        laser_setting = f"{self.laser_power:.2f} {int(self.laser_on)}"
        settings[self.laser_channel] = laser_setting
        
        # LED setting
        led_setting = f"{self.led_power:.1f} {int(self.led_on)}"
        settings["LED_RGB_Board"] = led_setting
        
        return settings

@dataclass
class StackSettings:
    """
    Settings for z-stack acquisition.
    
    Attributes:
        num_planes: Number of z-planes to acquire
        z_step_mm: Step size between planes in millimeters
        z_velocity_mm_s: Stage velocity during acquisition
        bidirectional: Whether to acquire bidirectionally
    """
    num_planes: int = 1
    z_step_mm: float = 0.01
    z_velocity_mm_s: float = 0.4
    bidirectional: bool = False
    
    def calculate_z_range(self) -> float:
        """
        Calculate total Z range for stack.
        
        Returns:
            float: Total Z distance in millimeters
        """
        return (self.num_planes - 1) * self.z_step_mm

@dataclass
class TileSettings:
    """
    Settings for tile scan acquisition.
    
    Attributes:
        num_tiles_x: Number of tiles in X direction
        num_tiles_y: Number of tiles in Y direction
        overlap_percent: Overlap between tiles (0-100)
        snake_pattern: Whether to use snake pattern
    """
    num_tiles_x: int = 1
    num_tiles_y: int = 1
    overlap_percent: float = 10.0
    snake_pattern: bool = True

@dataclass
class ExperimentSettings:
    """
    General experiment settings.
    
    Attributes:
        save_data: Whether to save acquired data
        save_format: Data save format (Tiff, NotSaved)
        save_directory: Directory name for saving
        comment: Experiment comment/description
        display_max_projection: Whether to display MIP
        live_view_enabled: Whether live view is enabled
    """
    save_data: bool = False
    save_format: str = "NotSaved"
    save_directory: str = "Snapshots"
    comment: str = "GUI Acquisition"
    display_max_projection: bool = True
    live_view_enabled: bool = False
    
    def get_save_format(self) -> str:
        """Get save format string for workflow."""
        return "Tiff" if self.save_data else "NotSaved"

@dataclass
class WorkflowModel:
    """
    Complete workflow model for acquisition.

    This model represents all settings needed for an acquisition workflow.

    Attributes:
        type: Type of workflow
        name: Workflow name
        start_position: Starting position for acquisition
        end_position: Ending position (for stacks/tiles)
        illumination: Illumination settings
        stack_settings: Settings for z-stack
        tile_settings: Settings for tile scan
        experiment_settings: General experiment settings
        metadata: Additional workflow metadata
        _is_running: Internal flag tracking if workflow is executing
        _start_time: Timestamp when workflow started
        _end_time: Timestamp when workflow completed
    """
    type: WorkflowType
    name: str
    start_position: Position
    end_position: Optional[Position] = None
    illumination: IlluminationSettings = field(default_factory=IlluminationSettings)
    stack_settings: Optional[StackSettings] = None
    tile_settings: Optional[TileSettings] = None
    experiment_settings: ExperimentSettings = field(default_factory=ExperimentSettings)
    metadata: Dict[str, Any] = field(default_factory=dict)
    _is_running: bool = field(default=False, init=False, repr=False)
    _start_time: Optional[datetime] = field(default=None, init=False, repr=False)
    _end_time: Optional[datetime] = field(default=None, init=False, repr=False)
    
    def to_workflow_dict(self) -> Dict[str, Any]:
        """
        Convert to workflow dictionary format for microscope.
        
        Returns:
            Dict[str, Any]: Workflow dictionary
        """
        workflow = {}
        
        # Start position
        workflow["Start Position"] = {
            "X (mm)": self.start_position.x,
            "Y (mm)": self.start_position.y,
            "Z (mm)": self.start_position.z,
            "Angle (degrees)": self.start_position.r
        }
        
        # End position
        if self.end_position:
            end_pos = self.end_position
        else:
            # For snapshot, end position is same as start with minimal Z change
            end_pos = Position(
                x=self.start_position.x,
                y=self.start_position.y,
                z=self.start_position.z + 0.01,
                r=self.start_position.r
            )
        
        workflow["End Position"] = {
            "X (mm)": end_pos.x,
            "Y (mm)": end_pos.y,
            "Z (mm)": end_pos.z,
            "Angle (degrees)": end_pos.r
        }
        
        # Illumination
        workflow["Illumination Source"] = self.illumination.to_dict()
        
        # Stack settings
        if self.stack_settings:
            workflow["Stack Settings"] = {
                "Number of planes": self.stack_settings.num_planes,
                "Change in Z axis (mm)": self.stack_settings.z_step_mm,
                "Z stage velocity (mm/s)": str(self.stack_settings.z_velocity_mm_s),
                "Bidirectional": str(self.stack_settings.bidirectional).lower()
            }
        else:
            # Default stack settings for snapshot
            workflow["Stack Settings"] = {
                "Number of planes": 1,
                "Change in Z axis (mm)": 0.01,
                "Z stage velocity (mm/s)": "0.4",
                "Bidirectional": "false"
            }
        
        # Experiment settings
        workflow["Experiment Settings"] = {
            "Comments": self.experiment_settings.comment,
            "Save image directory": self.experiment_settings.save_directory,
            "Save image data": self.experiment_settings.get_save_format(),
            "Display max projection": str(self.experiment_settings.display_max_projection).lower(),
            "Work flow live view enabled": str(self.experiment_settings.live_view_enabled).lower()
        }
        
        # Add any additional metadata
        workflow.update(self.metadata)

        return workflow

    def mark_started(self) -> None:
        """Mark workflow as started (called when CMD_WORKFLOW_START sent)."""
        self._is_running = True
        self._start_time = datetime.now()
        self._end_time = None

    def mark_completed(self) -> None:
        """Mark workflow as completed (called when CMD_WORKFLOW_STOP sent or workflow finishes)."""
        self._is_running = False
        self._end_time = datetime.now()

    def is_running(self) -> bool:
        """
        Check if workflow is currently executing.

        Returns:
            bool: True if workflow is running, False otherwise
        """
        return self._is_running

    def get_execution_time(self) -> Optional[float]:
        """
        Get workflow execution time in seconds.

        Returns:
            Optional[float]: Execution time if started, None otherwise
        """
        if self._start_time is None:
            return None

        end_time = self._end_time or datetime.now()
        return (end_time - self._start_time).total_seconds()

    @classmethod
    def create_snapshot(cls, position: Position, 
                       laser_channel: str = "Laser 3 488 nm",
                       laser_power: float = 5.0) -> 'WorkflowModel':
        """
        Factory method to create a snapshot workflow.
        
        Args:
            position: Position for snapshot
            laser_channel: Laser channel to use
            laser_power: Laser power percentage
            
        Returns:
            WorkflowModel: Configured snapshot workflow
        """
        return cls(
            type=WorkflowType.SNAPSHOT,
            name="GUI Snapshot",
            start_position=position,
            illumination=IlluminationSettings(
                laser_channel=laser_channel,
                laser_power=laser_power,
                laser_on=True,
                led_on=False
            ),
            stack_settings=StackSettings(num_planes=1),
            experiment_settings=ExperimentSettings(
                save_data=False,
                display_max_projection=True,
                comment="GUI Snapshot"
            )
        )
