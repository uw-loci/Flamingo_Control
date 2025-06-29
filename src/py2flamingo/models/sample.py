
# models/sample.py
"""
Data models for sample representation and bounding boxes.

This module contains data structures used to represent samples
and their spatial boundaries within the microscope field of view.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from models.microscope import Position

@dataclass
class SampleBounds:
    """
    Represents the 3D bounding box of a sample.
    
    Attributes:
        top: Position of the top boundary of the sample
        bottom: Position of the bottom boundary of the sample
        angle: Rotation angle at which bounds were measured (degrees)
    """
    top: Position
    bottom: Position
    angle: float = 0.0
    
    def get_center(self) -> Position:
        """
        Calculate the center position of the bounding box.
        
        Returns:
            Position: Center point between top and bottom bounds
        """
        return Position(
            x=(self.top.x + self.bottom.x) / 2,
            y=(self.top.y + self.bottom.y) / 2,
            z=(self.top.z + self.bottom.z) / 2,
            r=self.angle
        )
    
    def get_dimensions(self) -> Tuple[float, float, float]:
        """
        Calculate the dimensions of the bounding box.
        
        Returns:
            Tuple[float, float, float]: Width (x), height (y), depth (z) in mm
        """
        return (
            abs(self.top.x - self.bottom.x),
            abs(self.top.y - self.bottom.y),
            abs(self.top.z - self.bottom.z)
        )

@dataclass
class Sample:
    """
    Complete representation of a sample including its properties and bounds.
    
    Attributes:
        name: Unique identifier for the sample
        bounds_list: List of bounds at different rotation angles
        located: Whether the sample has been successfully located
        fluorescence_channel: Imaging channel used for detection
        notes: Additional notes or metadata about the sample
    """
    name: str
    bounds_list: List[SampleBounds] = field(default_factory=list)
    located: bool = False
    fluorescence_channel: str = "Laser 3 488 nm"
    notes: str = ""
    
    def add_bounds(self, bounds: SampleBounds) -> None:
        """
        Add a new bounding box measurement to the sample.
        
        Args:
            bounds: SampleBounds object to add
        """
        self.bounds_list.append(bounds)
        self.located = True
    
    def get_bounds_at_angle(self, angle: float, tolerance: float = 1.0) -> Optional[SampleBounds]:
        """
        Retrieve bounds measured at or near a specific angle.
        
        Args:
            angle: Target angle in degrees
            tolerance: Acceptable deviation from target angle
            
        Returns:
            Optional[SampleBounds]: Bounds at the specified angle, or None if not found
        """
        for bounds in self.bounds_list:
            if abs(bounds.angle - angle) <= tolerance:
                return bounds
        return None