# src/py2flamingo/models/microscope.py
"""
Data models for microscope state and position.

This module defines the core data structures used throughout
the application for representing microscope state and coordinates.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum

class MicroscopeState(Enum):
    """
    Enumeration of possible microscope states.
    
    Attributes:
        IDLE: Microscope is idle and ready for commands
        ACQUIRING: Microscope is acquiring images
        MOVING: Stage is moving to a new position
        ERROR: Microscope is in an error state
        PROCESSING: Processing acquired data
        CONNECTING: Establishing connection
        DISCONNECTED: Not connected to microscope
    """
    IDLE = "idle"
    ACQUIRING = "acquiring"
    MOVING = "moving"
    ERROR = "error"
    PROCESSING = "processing"
    CONNECTING = "connecting"
    DISCONNECTED = "disconnected"

@dataclass
class Position:
    """
    Represents a position in the microscope coordinate system.
    
    Attributes:
        x: X-axis position in millimeters
        y: Y-axis position in millimeters
        z: Z-axis position in millimeters
        r: Rotation angle in degrees
    """
    x: float
    y: float
    z: float
    r: float
    
    def to_list(self) -> List[float]:
        """
        Convert position to list format for backward compatibility.
        
        Returns:
            List[float]: [x, y, z, r] coordinates
        """
        return [self.x, self.y, self.z, self.r]
    
    def to_dict(self) -> Dict[str, float]:
        """
        Convert position to dictionary format.
        
        Returns:
            Dict[str, float]: Position as dictionary
        """
        return {
            'x': self.x,
            'y': self.y,
            'z': self.z,
            'r': self.r
        }
    
    @classmethod
    def from_list(cls, coords: List[float]) -> 'Position':
        """
        Create Position from list of coordinates.
        
        Args:
            coords: List of [x, y, z, r] coordinates
            
        Returns:
            Position: New Position instance
        """
        if len(coords) != 4:
            raise ValueError(f"Expected 4 coordinates, got {len(coords)}")
        return cls(x=float(coords[0]), y=float(coords[1]), 
                  z=float(coords[2]), r=float(coords[3]))
    
    def __str__(self) -> str:
        """String representation of position."""
        return f"Position(x={self.x:.3f}, y={self.y:.3f}, z={self.z:.3f}, r={self.r:.1f}°)"

@dataclass
class StageLimits:
    """
    Stage movement limits for each axis.
    
    Attributes:
        x_min: Minimum X position in mm
        x_max: Maximum X position in mm
        y_min: Minimum Y position in mm
        y_max: Maximum Y position in mm
        z_min: Minimum Z position in mm
        z_max: Maximum Z position in mm
        r_min: Minimum rotation in degrees
        r_max: Maximum rotation in degrees
    """
    x_min: float = 0.0
    x_max: float = 26.0
    y_min: float = 0.0
    y_max: float = 26.0
    z_min: float = 0.0
    z_max: float = 26.0
    r_min: float = -720.0
    r_max: float = 720.0
    
    def is_position_valid(self, position: Position) -> bool:
        """
        Check if a position is within stage limits.
        
        Args:
            position: Position to validate
            
        Returns:
            bool: True if position is within limits
        """
        return (self.x_min <= position.x <= self.x_max and
                self.y_min <= position.y <= self.y_max and
                self.z_min <= position.z <= self.z_max and
                self.r_min <= position.r <= self.r_max)
    
    @classmethod
    def from_dict(cls, limits_dict: Dict[str, Dict[str, float]]) -> 'StageLimits':
        """
        Create StageLimits from dictionary.
        
        Args:
            limits_dict: Dictionary with axis limits
            
        Returns:
            StageLimits: New instance with specified limits
        """
        return cls(
            x_min=limits_dict['x']['min'],
            x_max=limits_dict['x']['max'],
            y_min=limits_dict['y']['min'],
            y_max=limits_dict['y']['max'],
            z_min=limits_dict['z']['min'],
            z_max=limits_dict['z']['max'],
            r_min=limits_dict['r']['min'],
            r_max=limits_dict['r']['max']
        )

@dataclass
class MicroscopeModel:
    """
    Complete model of microscope state and configuration.
    
    Attributes:
        name: Name of the microscope
        ip_address: IP address for connection
        port: Port number for connection
        current_position: Current stage position
        home_position: Home position for stage
        state: Current microscope state
        stage_limits: Movement limits for each axis
        lasers: List of available laser channels
        selected_laser: Currently selected laser
        laser_power: Current laser power (0-100)
        objective_magnification: Objective lens magnification
        pixel_size_mm: Image pixel size in millimeters
        metadata: Additional metadata dictionary
    """
    name: str
    ip_address: str
    port: int
    current_position: Position
    home_position: Optional[Position] = None
    state: MicroscopeState = MicroscopeState.DISCONNECTED
    stage_limits: Optional[StageLimits] = None
    lasers: List[str] = field(default_factory=list)
    selected_laser: Optional[str] = None
    laser_power: float = 0.0
    objective_magnification: float = 16.0
    pixel_size_mm: float = 0.000488
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def is_connected(self) -> bool:
        """
        Check if microscope is connected.
        
        Returns:
            bool: True if connected
        """
        return self.state != MicroscopeState.DISCONNECTED
    
    def can_accept_commands(self) -> bool:
        """
        Check if microscope can accept commands.
        
        Returns:
            bool: True if in a state that can accept commands
        """
        return self.state in [MicroscopeState.IDLE, MicroscopeState.PROCESSING]
