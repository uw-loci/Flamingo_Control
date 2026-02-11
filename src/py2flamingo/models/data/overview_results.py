"""Overview result data types.

Shared data types for LED 2D Overview workflow results.
Extracted from workflows layer to models layer to break circular
dependencies between workflows and views.
"""

from dataclasses import dataclass, field
from typing import Optional, List

import numpy as np


# Visualization types available for LED 2D overview
VISUALIZATION_TYPES = [
    ("best_focus", "Best Focus"),
    ("focus_stack", "Extended Depth of Focus"),
    ("min_intensity", "Minimum Intensity"),
    ("max_intensity", "Maximum Intensity"),
    ("mean_intensity", "Mean Intensity"),
]


@dataclass
class TileResult:
    """Result for a single tile.

    Stores multiple visualization types for the same tile position.
    The 'images' dict maps visualization type to the corresponding image.

    For spatial overlap calculation in tile collection:
    - rotation_angle: The rotation angle at which this tile was captured
    - z_stack_min/max: The Z-stack bounds used for this tile (in workflow coordinates)
    """
    x: float
    y: float
    z: float
    tile_x_idx: int
    tile_y_idx: int
    images: dict = field(default_factory=dict)  # visualization_type -> np.ndarray
    rotation_angle: float = 0.0  # Rotation angle in degrees
    z_stack_min: float = 0.0  # Minimum Z position of Z-stack (mm)
    z_stack_max: float = 0.0  # Maximum Z position of Z-stack (mm)

    @property
    def image(self) -> np.ndarray:
        """Return best_focus image for backwards compatibility."""
        return self.images.get("best_focus", np.zeros((100, 100), dtype=np.uint16))

    def to_dict(self) -> dict:
        """Serialize tile result to dictionary (images saved separately)."""
        return {
            'x': self.x,
            'y': self.y,
            'z': self.z,
            'tile_x_idx': self.tile_x_idx,
            'tile_y_idx': self.tile_y_idx,
            'rotation_angle': self.rotation_angle,
            'z_stack_min': self.z_stack_min,
            'z_stack_max': self.z_stack_max,
            'image_types': list(self.images.keys())
        }

    @classmethod
    def from_dict(cls, data: dict, images: Optional[dict] = None) -> 'TileResult':
        """Deserialize tile result from dictionary and optional image dict."""
        return cls(
            x=data['x'],
            y=data['y'],
            z=data['z'],
            tile_x_idx=data['tile_x_idx'],
            tile_y_idx=data['tile_y_idx'],
            images=images or {},
            rotation_angle=data.get('rotation_angle', 0.0),
            z_stack_min=data.get('z_stack_min', 0.0),
            z_stack_max=data.get('z_stack_max', 0.0)
        )


@dataclass
class RotationResult:
    """Result for a single rotation angle.

    Stores multiple stitched images, one per visualization type.
    """
    rotation_angle: float
    tiles: List[TileResult] = field(default_factory=list)
    stitched_images: dict = field(default_factory=dict)  # visualization_type -> np.ndarray
    tiles_x: int = 0
    tiles_y: int = 0
    invert_x: bool = False  # Whether X-axis is inverted for display

    @property
    def stitched_image(self) -> Optional[np.ndarray]:
        """Return best_focus stitched image for backwards compatibility."""
        return self.stitched_images.get("best_focus")

    def to_dict(self) -> dict:
        """Serialize rotation result to dictionary (images saved separately)."""
        return {
            'rotation_angle': self.rotation_angle,
            'tiles_x': self.tiles_x,
            'tiles_y': self.tiles_y,
            'invert_x': self.invert_x,
            'tiles': [t.to_dict() for t in self.tiles],
            'stitched_image_types': list(self.stitched_images.keys())
        }

    @classmethod
    def from_dict(cls, data: dict, stitched_images: Optional[dict] = None,
                  tiles: Optional[List[TileResult]] = None) -> 'RotationResult':
        """Deserialize rotation result from dictionary and image data."""
        return cls(
            rotation_angle=data['rotation_angle'],
            tiles=tiles or [],
            stitched_images=stitched_images or {},
            tiles_x=data.get('tiles_x', 0),
            tiles_y=data.get('tiles_y', 0),
            invert_x=data.get('invert_x', False)
        )


@dataclass
class EffectiveBoundingBox:
    """Bounding box with swapped dimensions for rotation.

    For R=0: tile_x/y define the tiling grid, z_min/max define Z-stack
    For R=90: original Z becomes tile_x, original X becomes z depth
    """
    tile_x_min: float
    tile_x_max: float
    tile_y_min: float
    tile_y_max: float
    z_min: float
    z_max: float
