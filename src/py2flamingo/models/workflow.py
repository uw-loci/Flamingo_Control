# ============================================================================
# src/py2flamingo/models/workflow.py
"""
Data models for microscope workflows.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum

from .microscope import Position


class WorkflowType(Enum):
    """Types of microscope workflows."""
    SNAPSHOT = "Snap"
    STACK = "Stack"
    TILE = "Tile"
    TIME_SERIES = "TimeSeries"
    MULTI_CHANNEL = "MultiChannel"


class SaveFormat(Enum):
    """Image save formats."""
    TIFF = "Tiff"
    PNG = "Png"
    NOT_SAVED = "NotSaved"


@dataclass
class IlluminationSettings:
    """
    Illumination configuration for workflows.
    
    Attributes:
        laser_channel: Selected laser channel
        laser_power: Laser power percentage (0-100)
        laser_on: Whether laser is enabled
        filter_position: Filter wheel position
        illumination_path: Light path (left/right/both)
    """
    laser_channel: str
    laser_power: float
    laser_on: bool = True
    filter_position: Optional[int] = None
    illumination_path: str = "both"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for workflow."""
        return {
            'Laser': self.laser_channel,
            'Power': self.laser_power,
            'LaserOn': self.laser_on,
            'FilterPosition': self.filter_position,
            'IlluminationPath': self.illumination_path
        }


@dataclass
class StackSettings:
    """
    Settings for Z-stack acquisition.
    
    Attributes:
        num_planes: Number of Z planes
        plane_spacing_um: Spacing between planes in micrometers
        z_velocity_mm_s: Z stage velocity in mm/s
        bidirectional: Whether to use bidirectional scanning
    """
    num_planes: int = 1
    plane_spacing_um: float = 10.0
    z_velocity_mm_s: float = 0.4
    bidirectional: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for workflow."""
        return {
            'Number of planes': self.num_planes,
            'Plane spacing (um)': self.plane_spacing_um,
            'Z stage velocity (mm/s)': str(self.z_velocity_mm_s),
            'Bidirectional': 'true' if self.bidirectional else 'false'
        }


@dataclass
class TileSettings:
    """
    Settings for tile/mosaic acquisition.
    
    Attributes:
        overlap_percent: Percentage overlap between tiles
        num_tiles_x: Number of tiles in X
        num_tiles_y: Number of tiles in Y
        tile_order: Order of tile acquisition
    """
    overlap_percent: float = 10.0
    num_tiles_x: int = 1
    num_tiles_y: int = 1
    tile_order: str = "RowByRow"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for workflow."""
        return {
            'Overlap (%)': self.overlap_percent,
            'Tiles X': self.num_tiles_x,
            'Tiles Y': self.num_tiles_y,
            'Tile order': self.tile_order
        }


@dataclass
class ExperimentSettings:
    """
    General experiment settings.
    
    Attributes:
        save_format: Image save format
        save_directory: Directory to save images
        comments: Experiment comments
        display_max_projection: Show max projection
        live_view_enabled: Enable live view during acquisition
        framerate: Acquisition framerate
    """
    save_format: SaveFormat = SaveFormat.TIFF
    save_directory: str = "C:/Data"
    comments: str = ""
    display_max_projection: bool = True
    live_view_enabled: bool = False
    framerate: float = 40.0032
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for workflow."""
        return {
            'Save image data': self.save_format.value,
            'Save image directory': self.save_directory,
            'Comments': self.comments,
            'Display max projection': 'true' if self.display_max_projection else 'false',
            'Work flow live view enabled': 'true' if self.live_view_enabled else 'false',
            'Framerate': self.framerate
        }


@dataclass
class WorkflowModel:
    """
    Complete workflow data model.
    
    Attributes:
        type: Type of workflow
        start_position: Starting position
        end_position: Ending position (for scans)
        illumination: Illumination settings
        stack_settings: Z-stack settings
        tile_settings: Tile/mosaic settings
        experiment: General experiment settings
        name: Workflow name
    """
    type: WorkflowType
    start_position: Position
    end_position: Optional[Position] = None
    illumination: Optional[IlluminationSettings] = None
    stack_settings: Optional[StackSettings] = None
    tile_settings: Optional[TileSettings] = None
    experiment: ExperimentSettings = field(default_factory=ExperimentSettings)
    name: str = "Workflow"
    
    def __post_init__(self):
        """Initialize default values based on workflow type."""
        if self.end_position is None:
            # For snapshots, end position is same as start
            if self.type == WorkflowType.SNAPSHOT:
                self.end_position = Position(
                    x=self.start_position.x,
                    y=self.start_position.y,
                    z=self.start_position.z + 0.01,
                    r=self.start_position.r
                )
        
        # Initialize default settings based on type
        if self.type == WorkflowType.STACK and self.stack_settings is None:
            self.stack_settings = StackSettings()
        elif self.type == WorkflowType.TILE and self.tile_settings is None:
            self.tile_settings = TileSettings()
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary format for microscope.
        
        Returns:
            Dictionary in format expected by microscope software
        """
        workflow_dict = {
            'Work Flow Type': self.type.value,
            'Start Position': {
                'X (mm)': self.start_position.x,
                'Y (mm)': self.start_position.y,
                'Z (mm)': self.start_position.z,
                'Angle (degrees)': self.start_position.r
            },
            'End Position': {
                'X (mm)': self.end_position.x if self.end_position else self.start_position.x,
                'Y (mm)': self.end_position.y if self.end_position else self.start_position.y,
                'Z (mm)': self.end_position.z if self.end_position else self.start_position.z,
                'Angle (degrees)': self.end_position.r if self.end_position else self.start_position.r
            },
            'Experiment Settings': self.experiment.to_dict()
        }
        
        # Add illumination settings
        if self.illumination:
            workflow_dict['Illumination Settings'] = self.illumination.to_dict()
        
        # Add stack settings
        if self.stack_settings:
            workflow_dict['Stack Settings'] = self.stack_settings.to_dict()
        
        # Add tile settings
        if self.tile_settings:
            workflow_dict['Tile Settings'] = self.tile_settings.to_dict()
        
        return workflow_dict
    
    def validate(self) -> bool:
        """
        Validate workflow configuration.
        
        Returns:
            True if valid, raises ValueError if not
        """
        # Check positions
        if self.start_position is None:
            raise ValueError("Start position is required")
        
        # Check illumination
        if self.illumination and not 0 <= self.illumination.laser_power <= 100:
            raise ValueError("Laser power must be between 0 and 100")
        
        # Check stack settings
        if self.stack_settings:
            if self.stack_settings.num_planes < 1:
                raise ValueError("Number of planes must be at least 1")
            if self.stack_settings.plane_spacing_um <= 0:
                raise ValueError("Plane spacing must be positive")
        
        # Check tile settings
        if self.tile_settings:
            if self.tile_settings.num_tiles_x < 1 or self.tile_settings.num_tiles_y < 1:
                raise ValueError("Number of tiles must be at least 1")
            if not 0 <= self.tile_settings.overlap_percent <= 50:
                raise ValueError("Overlap must be between 0 and 50 percent")
        
        return True


@dataclass
class WorkflowResult:
    """
    Result from workflow execution.
    
    Attributes:
        workflow_id: Unique workflow identifier
        status: Execution status
        start_time: When workflow started
        end_time: When workflow completed
        num_images: Number of images acquired
        error_message: Error message if failed
        metadata: Additional metadata
    """
    workflow_id: str
    status: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    num_images: int = 0
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)