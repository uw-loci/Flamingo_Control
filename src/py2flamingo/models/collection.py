# ============================================================================
# src/py2flamingo/models/collection.py
"""
Data models for multi-angle collection workflows.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime


@dataclass
class CollectionParameters:
    """
    Parameters for multi-angle collection.
    
    Attributes:
        angle_increment: Angle step size in degrees
        overlap_percent: Tile overlap percentage
        base_workflow_file: Base workflow filename
        comment: Collection comment/description
        save_directory: Where to save collected data
        z_padding: Extra Z range to include (mm)
    """
    angle_increment: float
    overlap_percent: float = 10.0
    base_workflow_file: str = "MultiAngle.txt"
    comment: str = ""
    save_directory: Optional[str] = None
    z_padding: float = 0.1
    
    def validate(self) -> bool:
        """
        Validate collection parameters.
        
        Returns:
            True if valid, raises ValueError if not
        """
        if not 0 < self.angle_increment <= 180:
            raise ValueError("Angle increment must be between 0 and 180 degrees")
        
        if not 0 <= self.overlap_percent <= 50:
            raise ValueError("Overlap must be between 0 and 50 percent")
        
        if self.z_padding < 0:
            raise ValueError("Z padding must be non-negative")
        
        return True


@dataclass
class AngleData:
    """
    Data collected at a specific angle.
    
    Attributes:
        angle: Rotation angle in degrees
        num_tiles: Number of tiles collected
        num_z_planes: Number of Z planes
        start_time: When collection started
        end_time: When collection ended
        file_paths: List of saved file paths
        metadata: Additional metadata
    """
    angle: float
    num_tiles: int = 0
    num_z_planes: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    file_paths: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def duration_seconds(self) -> Optional[float]:
        """Calculate collection duration in seconds."""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None


@dataclass
class MultiAngleCollection:
    """
    Complete multi-angle collection data.
    
    Attributes:
        sample_name: Name of the sample
        parameters: Collection parameters
        angles: List of angles to collect
        completed_angles: List of completed angles
        angle_data: Detailed data for each angle
        start_time: Collection start time
        end_time: Collection end time
        total_images: Total number of images collected
        status: Current status
    """
    sample_name: str
    parameters: CollectionParameters
    angles: List[float] = field(default_factory=list)
    completed_angles: List[float] = field(default_factory=list)
    angle_data: Dict[float, AngleData] = field(default_factory=dict)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    total_images: int = 0
    status: str = "pending"
    
    def __post_init__(self):
        """Initialize angle data entries."""
        for angle in self.angles:
            if angle not in self.angle_data:
                self.angle_data[angle] = AngleData(angle=angle)
    
    def mark_angle_complete(self, angle: float, data: AngleData):
        """
        Mark an angle as complete with its data.
        
        Args:
            angle: Completed angle
            data: Data collected at this angle
        """
        if angle not in self.completed_angles:
            self.completed_angles.append(angle)
        
        self.angle_data[angle] = data
        self.total_images += data.num_tiles * data.num_z_planes
    
    def progress_percent(self) -> float:
        """Calculate completion percentage."""
        if not self.angles:
            return 0.0
        return len(self.completed_angles) / len(self.angles) * 100
    
    def is_complete(self) -> bool:
        """Check if collection is complete."""
        return len(self.completed_angles) == len(self.angles)
    
    def duration_seconds(self) -> Optional[float]:
        """Calculate total collection duration."""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None
    
    def average_angle_time(self) -> Optional[float]:
        """Calculate average time per angle."""
        completed = len(self.completed_angles)
        if completed > 0 and self.duration_seconds():
            return self.duration_seconds() / completed
        return None
    
    def estimated_time_remaining(self) -> Optional[float]:
        """Estimate remaining time in seconds."""
        avg_time = self.average_angle_time()
        if avg_time:
            remaining_angles = len(self.angles) - len(self.completed_angles)
            return avg_time * remaining_angles
        return None
    
    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            'sample_name': self.sample_name,
            'parameters': {
                'angle_increment': self.parameters.angle_increment,
                'overlap_percent': self.parameters.overlap_percent,
                'workflow_file': self.parameters.base_workflow_file,
                'comment': self.parameters.comment
            },
            'angles': self.angles,
            'completed_angles': self.completed_angles,
            'progress_percent': self.progress_percent(),
            'total_images': self.total_images,
            'status': self.status,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None
        }
