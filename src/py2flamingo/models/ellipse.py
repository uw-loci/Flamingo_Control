# ============================================================================
# src/py2flamingo/models/ellipse.py
"""
Data models for ellipse fitting and tracking.
"""

from dataclasses import dataclass
from typing import Optional, Tuple, List
import numpy as np


@dataclass
class EllipseParameters:
    """
    Parameters defining an ellipse.
    
    Attributes:
        center_x: X coordinate of ellipse center
        center_y: Y coordinate of ellipse center  
        semi_major: Semi-major axis length
        semi_minor: Semi-minor axis length
        rotation: Rotation angle in degrees
    """
    center_x: float
    center_y: float
    semi_major: float
    semi_minor: float
    rotation: float = 0.0
    
    def point_at_angle(self, angle_deg: float) -> Tuple[float, float]:
        """
        Calculate point on ellipse at given angle.
        
        Args:
            angle_deg: Angle in degrees from ellipse center
            
        Returns:
            Tuple of (x, y) coordinates
        """
        # Convert to radians
        angle_rad = np.radians(angle_deg)
        rotation_rad = np.radians(self.rotation)
        
        # Calculate point on unrotated ellipse
        x_ellipse = self.semi_major * np.cos(angle_rad)
        y_ellipse = self.semi_minor * np.sin(angle_rad)
        
        # Apply rotation
        x_rotated = (x_ellipse * np.cos(rotation_rad) - 
                    y_ellipse * np.sin(rotation_rad))
        y_rotated = (x_ellipse * np.sin(rotation_rad) + 
                    y_ellipse * np.cos(rotation_rad))
        
        # Translate to center
        x_final = x_rotated + self.center_x
        y_final = y_rotated + self.center_y
        
        return (x_final, y_final)
    
    def area(self) -> float:
        """Calculate ellipse area."""
        return np.pi * self.semi_major * self.semi_minor
    
    def eccentricity(self) -> float:
        """Calculate ellipse eccentricity."""
        if self.semi_major == 0:
            return 0
        return np.sqrt(1 - (self.semi_minor / self.semi_major) ** 2)
    
    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            'center_x': self.center_x,
            'center_y': self.center_y,
            'semi_major': self.semi_major,
            'semi_minor': self.semi_minor,
            'rotation': self.rotation
        }


@dataclass
class EllipseModel:
    """
    Model for sample tracking using ellipse fitting.
    
    Attributes:
        sample_name: Name of the sample
        top_ellipse: Ellipse parameters for top boundary
        bottom_ellipse: Ellipse parameters for bottom boundary
        fit_quality: Quality metric for ellipse fit (0-1)
        num_points: Number of points used for fitting
    """
    sample_name: str
    top_ellipse: Optional[EllipseParameters] = None
    bottom_ellipse: Optional[EllipseParameters] = None
    fit_quality: float = 0.0
    num_points: int = 0
    
    def predict_bounds_at_angle(self, angle_deg: float) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
        """
        Predict top and bottom bounds at given angle.
        
        Args:
            angle_deg: Angle in degrees
            
        Returns:
            Tuple of (top_point, bottom_point) or None
        """
        if not self.top_ellipse or not self.bottom_ellipse:
            return None
        
        top_point = self.top_ellipse.point_at_angle(angle_deg)
        bottom_point = self.bottom_ellipse.point_at_angle(angle_deg)
        
        return (top_point, bottom_point)
    
    def get_center_trajectory(self, angles: List[float]) -> List[Tuple[float, float, float]]:
        """
        Calculate center trajectory for given angles.
        
        Args:
            angles: List of angles in degrees
            
        Returns:
            List of (x, y, z) center positions
        """
        trajectory = []
        
        for angle in angles:
            bounds = self.predict_bounds_at_angle(angle)
            if bounds:
                top, bottom = bounds
                center_x = (top[0] + bottom[0]) / 2
                center_y = 0  # Assuming Y doesn't change with rotation
                center_z = (top[1] + bottom[1]) / 2
                trajectory.append((center_x, center_y, center_z))
        
        return trajectory
    
    def validate_fit(self) -> bool:
        """
        Validate ellipse fit quality.
        
        Returns:
            True if fit is acceptable
        """
        if not self.top_ellipse or not self.bottom_ellipse:
            return False
        
        # Check eccentricity is reasonable
        if self.top_ellipse.eccentricity() > 0.99:
            return False
        if self.bottom_ellipse.eccentricity() > 0.99:
            return False
        
        # Check fit quality
        if self.fit_quality < 0.5:
            return False
        
        # Check sufficient points
        if self.num_points < 5:
            return False
        
        return True
    
    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            'sample_name': self.sample_name,
            'top_ellipse': self.top_ellipse.to_dict() if self.top_ellipse else None,
            'bottom_ellipse': self.bottom_ellipse.to_dict() if self.bottom_ellipse else None,
            'fit_quality': self.fit_quality,
            'num_points': self.num_points
        }
