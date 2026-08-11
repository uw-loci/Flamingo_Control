# ============================================================================
# src/py2flamingo/models/ellipse.py
"""
Data models for ellipse fitting and tracking.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

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
            "center_x": self.center_x,
            "center_y": self.center_y,
            "semi_major": self.semi_major,
            "semi_minor": self.semi_minor,
            "rotation": self.rotation,
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

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "sample_name": self.sample_name,
            "top_ellipse": self.top_ellipse.to_dict() if self.top_ellipse else None,
            "bottom_ellipse": (
                self.bottom_ellipse.to_dict() if self.bottom_ellipse else None
            ),
            "fit_quality": self.fit_quality,
            "num_points": self.num_points,
        }
