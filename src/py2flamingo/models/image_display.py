"""
Data model for image display settings.

This module contains the model for tracking image display transformation settings
used by the live feed viewer.
"""
from dataclasses import dataclass
from ..utils.image_transforms import Rotation, Colormap


@dataclass
class ImageDisplayModel:
    """
    Model for image display transformation settings.

    Tracks all settings for how images should be transformed before display.

    Attributes:
        rotation: Rotation angle (0, 90, 180, 270 degrees)
        flip_horizontal: Whether to flip image left-right
        flip_vertical: Whether to flip image up-down
        downsample_factor: Downsampling factor (1, 2, 4, 8)
        colormap: Colormap to apply for visualization
        auto_contrast: Whether to apply automatic contrast adjustment
        percentile_low: Lower percentile for contrast (0-100)
        percentile_high: Upper percentile for contrast (0-100)
    """
    rotation: Rotation = Rotation.NONE
    flip_horizontal: bool = False
    flip_vertical: bool = False
    downsample_factor: int = 1
    colormap: Colormap = Colormap.GRAY
    auto_contrast: bool = True
    percentile_low: float = 1.0
    percentile_high: float = 99.0

    def to_dict(self) -> dict:
        """
        Convert settings to dictionary.

        Returns:
            dict: Display settings as dictionary

        Example:
            >>> model = ImageDisplayModel(rotation=Rotation.CW_90)
            >>> settings = model.to_dict()
            >>> settings['rotation']
            90
        """
        return {
            'rotation': self.rotation.value,
            'flip_horizontal': self.flip_horizontal,
            'flip_vertical': self.flip_vertical,
            'downsample_factor': self.downsample_factor,
            'colormap': self.colormap.value,
            'auto_contrast': self.auto_contrast,
            'percentile_low': self.percentile_low,
            'percentile_high': self.percentile_high,
        }

    def set_rotation(self, degrees: int) -> None:
        """
        Set rotation angle.

        Args:
            degrees: Rotation in degrees (0, 90, 180, 270)

        Raises:
            ValueError: If degrees is not a valid rotation
        """
        try:
            self.rotation = Rotation(degrees)
        except ValueError:
            raise ValueError(f"Invalid rotation: {degrees}. Must be 0, 90, 180, or 270")

    def set_colormap(self, colormap_name: str) -> None:
        """
        Set colormap by name.

        Args:
            colormap_name: Name of colormap (e.g., "gray", "viridis")

        Raises:
            ValueError: If colormap name is not valid
        """
        try:
            self.colormap = Colormap(colormap_name.lower())
        except ValueError:
            raise ValueError(f"Invalid colormap: {colormap_name}")

    def reset(self) -> None:
        """Reset all settings to defaults."""
        self.rotation = Rotation.NONE
        self.flip_horizontal = False
        self.flip_vertical = False
        self.downsample_factor = 1
        self.colormap = Colormap.GRAY
        self.auto_contrast = True
        self.percentile_low = 1.0
        self.percentile_high = 99.0
