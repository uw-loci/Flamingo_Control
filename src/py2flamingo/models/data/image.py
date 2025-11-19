"""Image data models for acquired microscopy images.

This module provides models for representing images, image metadata,
and image collections from microscope acquisitions.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple, Union
from datetime import datetime
from enum import Enum
import numpy as np
from pathlib import Path
from ..base import BaseModel, ValidatedModel, ValidationError


class ImageType(Enum):
    """Types of microscopy images."""
    BRIGHTFIELD = "brightfield"
    FLUORESCENCE = "fluorescence"
    PHASE = "phase_contrast"
    DIC = "dic"  # Differential interference contrast
    CONFOCAL = "confocal"
    LIGHT_SHEET = "light_sheet"
    TWO_PHOTON = "two_photon"


class ImageDimension(Enum):
    """Image dimensionality."""
    GRAYSCALE_2D = "2D"           # Single channel, single plane
    RGB_2D = "RGB_2D"              # RGB, single plane
    GRAYSCALE_3D = "3D"           # Single channel, z-stack
    RGB_3D = "RGB_3D"              # RGB z-stack
    GRAYSCALE_4D = "4D"           # Single channel, time series of z-stacks
    MULTICHANNEL_2D = "MC_2D"     # Multi-channel, single plane
    MULTICHANNEL_3D = "MC_3D"     # Multi-channel z-stack
    MULTICHANNEL_4D = "MC_4D"     # Multi-channel time series


@dataclass
class PixelCalibration:
    """Pixel size calibration for images."""
    x_um: float              # Pixel width in micrometers
    y_um: float              # Pixel height in micrometers
    z_um: Optional[float] = None  # Z-step for stacks
    unit: str = "um"         # Calibration unit

    def get_voxel_volume(self) -> Optional[float]:
        """Calculate voxel volume for 3D data.

        Returns:
            Voxel volume in cubic micrometers, or None for 2D
        """
        if self.z_um is not None:
            return self.x_um * self.y_um * self.z_um
        return None

    def get_pixel_area(self) -> float:
        """Calculate pixel area.

        Returns:
            Pixel area in square micrometers
        """
        return self.x_um * self.y_um


@dataclass
class ImageMetadata(BaseModel):
    """Comprehensive metadata for microscopy images."""
    # Acquisition parameters
    acquisition_time: datetime
    exposure_time_ms: float
    gain_db: float = 0.0
    binning: int = 1
    bit_depth: int = 16

    # Optical path
    objective_magnification: float = 20.0
    numerical_aperture: float = 0.75
    immersion_medium: str = "air"
    wavelength_nm: Optional[float] = None
    filter_name: Optional[str] = None

    # Illumination
    illumination_type: ImageType = ImageType.FLUORESCENCE
    laser_power_mw: Optional[float] = None
    led_intensity_percent: Optional[float] = None

    # Stage position
    stage_x_mm: Optional[float] = None
    stage_y_mm: Optional[float] = None
    stage_z_mm: Optional[float] = None
    stage_r_deg: Optional[float] = None

    # Calibration
    pixel_calibration: Optional[PixelCalibration] = None

    # Camera settings
    camera_model: Optional[str] = None
    camera_temperature_c: Optional[float] = None
    roi_offset_x: int = 0
    roi_offset_y: int = 0

    # Additional info
    sample_id: Optional[str] = None
    experiment_id: Optional[str] = None
    user_comment: Optional[str] = None
    software_version: Optional[str] = None

    def get_physical_size(self, width_px: int, height_px: int) -> Tuple[float, float]:
        """Calculate physical size of image.

        Args:
            width_px: Image width in pixels
            height_px: Image height in pixels

        Returns:
            Tuple of (width_um, height_um)
        """
        if not self.pixel_calibration:
            raise ValueError("Pixel calibration required for physical size")

        width_um = width_px * self.pixel_calibration.x_um
        height_um = height_px * self.pixel_calibration.y_um
        return (width_um, height_um)


@dataclass
class ImageData(ValidatedModel):
    """Container for actual image data with metadata."""
    data: np.ndarray
    metadata: ImageMetadata
    name: Optional[str] = None
    file_path: Optional[Path] = None
    dimension_order: str = "YX"  # e.g., "TCZYX" for time, channel, z, y, x

    def __post_init__(self):
        """Initialize and validate after creation."""
        super().__post_init__()

        # Ensure data is numpy array
        if not isinstance(self.data, np.ndarray):
            self.data = np.array(self.data)

        # Set default name if not provided
        if not self.name:
            timestamp = self.metadata.acquisition_time.strftime("%Y%m%d_%H%M%S")
            self.name = f"Image_{timestamp}"

    def validate(self) -> None:
        """Validate image data."""
        # Check data is not empty
        if self.data.size == 0:
            raise ValidationError("Image data cannot be empty")

        # Check dimension order matches data shape
        if len(self.dimension_order) != self.data.ndim:
            raise ValidationError(
                f"Dimension order '{self.dimension_order}' doesn't match "
                f"data shape {self.data.shape} ({self.data.ndim}D)"
            )

        # Check bit depth matches data type
        expected_dtype = self._get_dtype_for_bit_depth(self.metadata.bit_depth)
        if expected_dtype and self.data.dtype != expected_dtype:
            # Just a warning, not an error
            pass

    @staticmethod
    def _get_dtype_for_bit_depth(bit_depth: int) -> Optional[np.dtype]:
        """Get numpy dtype for given bit depth."""
        dtype_map = {
            8: np.uint8,
            12: np.uint16,  # Stored in uint16
            14: np.uint16,  # Stored in uint16
            16: np.uint16,
            32: np.float32
        }
        return dtype_map.get(bit_depth)

    @property
    def shape(self) -> Tuple[int, ...]:
        """Get image shape."""
        return self.data.shape

    @property
    def dtype(self) -> np.dtype:
        """Get image data type."""
        return self.data.dtype

    @property
    def width(self) -> int:
        """Get image width in pixels."""
        x_idx = self.dimension_order.find('X')
        if x_idx >= 0:
            return self.data.shape[x_idx]
        # Assume last dimension if not specified
        return self.data.shape[-1]

    @property
    def height(self) -> int:
        """Get image height in pixels."""
        y_idx = self.dimension_order.find('Y')
        if y_idx >= 0:
            return self.data.shape[y_idx]
        # Assume second-to-last dimension if not specified
        return self.data.shape[-2] if self.data.ndim >= 2 else 1

    @property
    def num_channels(self) -> int:
        """Get number of channels."""
        c_idx = self.dimension_order.find('C')
        if c_idx >= 0:
            return self.data.shape[c_idx]
        return 1

    @property
    def num_z_planes(self) -> int:
        """Get number of Z planes."""
        z_idx = self.dimension_order.find('Z')
        if z_idx >= 0:
            return self.data.shape[z_idx]
        return 1

    @property
    def num_timepoints(self) -> int:
        """Get number of timepoints."""
        t_idx = self.dimension_order.find('T')
        if t_idx >= 0:
            return self.data.shape[t_idx]
        return 1

    def get_channel(self, channel: int) -> np.ndarray:
        """Extract single channel from multi-channel image.

        Args:
            channel: Channel index

        Returns:
            Array with channel dimension removed
        """
        c_idx = self.dimension_order.find('C')
        if c_idx < 0:
            if channel == 0:
                return self.data
            else:
                raise ValueError(f"No channel dimension, cannot get channel {channel}")

        if channel >= self.data.shape[c_idx]:
            raise ValueError(f"Channel {channel} out of range (0-{self.data.shape[c_idx]-1})")

        return np.take(self.data, channel, axis=c_idx)

    def get_z_plane(self, z: int) -> np.ndarray:
        """Extract single Z plane from stack.

        Args:
            z: Z plane index

        Returns:
            Array with Z dimension removed
        """
        z_idx = self.dimension_order.find('Z')
        if z_idx < 0:
            if z == 0:
                return self.data
            else:
                raise ValueError(f"No Z dimension, cannot get plane {z}")

        if z >= self.data.shape[z_idx]:
            raise ValueError(f"Z plane {z} out of range (0-{self.data.shape[z_idx]-1})")

        return np.take(self.data, z, axis=z_idx)

    def get_max_projection(self, axis: str = 'Z') -> np.ndarray:
        """Create maximum intensity projection.

        Args:
            axis: Axis to project along ('Z', 'T', etc.)

        Returns:
            Maximum projection array
        """
        axis_idx = self.dimension_order.find(axis.upper())
        if axis_idx < 0:
            return self.data  # No axis to project

        return np.max(self.data, axis=axis_idx)

    def get_statistics(self) -> Dict[str, float]:
        """Calculate basic image statistics.

        Returns:
            Dictionary with min, max, mean, std, etc.
        """
        return {
            'min': float(np.min(self.data)),
            'max': float(np.max(self.data)),
            'mean': float(np.mean(self.data)),
            'std': float(np.std(self.data)),
            'median': float(np.median(self.data)),
            'sum': float(np.sum(self.data)),
            'non_zero_pixels': int(np.count_nonzero(self.data))
        }

    def save(self, path: Path, format: str = 'tiff') -> None:
        """Save image to file.

        Args:
            path: Output file path
            format: File format ('tiff', 'png', 'npy')
        """
        path = Path(path)
        self.file_path = path

        if format.lower() == 'npy':
            np.save(path, self.data)
        else:
            # Would use appropriate image IO library here
            # e.g., tifffile, imageio, etc.
            pass

        self.update()

    @classmethod
    def create_from_array(cls, data: np.ndarray,
                         metadata: Optional[ImageMetadata] = None,
                         dimension_order: Optional[str] = None) -> 'ImageData':
        """Create ImageData from numpy array.

        Args:
            data: Image data array
            metadata: Optional metadata
            dimension_order: Dimension order string

        Returns:
            New ImageData instance
        """
        if metadata is None:
            metadata = ImageMetadata(
                acquisition_time=datetime.now(),
                exposure_time_ms=10.0
            )

        if dimension_order is None:
            # Guess dimension order based on shape
            if data.ndim == 2:
                dimension_order = "YX"
            elif data.ndim == 3:
                # Could be ZYX, CYX, or TYX
                dimension_order = "ZYX"
            elif data.ndim == 4:
                dimension_order = "CZYX"
            elif data.ndim == 5:
                dimension_order = "TCZYX"
            else:
                dimension_order = "".join(["D" for _ in range(data.ndim)])

        return cls(data=data, metadata=metadata, dimension_order=dimension_order)


@dataclass
class ImageStack(BaseModel):
    """Collection of related images (z-stack, time series, etc.)."""
    images: List[ImageData]
    stack_type: str  # 'z-stack', 'time-series', 'multi-channel', etc.
    name: Optional[str] = None

    def __post_init__(self):
        """Initialize stack properties."""
        super().__post_init__()

        if not self.name and self.images:
            self.name = f"Stack_{self.images[0].name}"

    @property
    def num_images(self) -> int:
        """Get number of images in stack."""
        return len(self.images)

    def get_image(self, index: int) -> ImageData:
        """Get image at index.

        Args:
            index: Image index

        Returns:
            ImageData at index
        """
        if not (0 <= index < len(self.images)):
            raise IndexError(f"Index {index} out of range (0-{len(self.images)-1})")
        return self.images[index]

    def to_array(self) -> np.ndarray:
        """Convert stack to single numpy array.

        Returns:
            Stacked array with additional dimension
        """
        if not self.images:
            raise ValueError("Cannot convert empty stack to array")

        arrays = [img.data for img in self.images]
        return np.stack(arrays)

    def get_max_projection(self) -> np.ndarray:
        """Create maximum intensity projection of stack.

        Returns:
            Maximum projection array
        """
        if not self.images:
            raise ValueError("Cannot project empty stack")

        arrays = [img.data for img in self.images]
        stacked = np.stack(arrays)
        return np.max(stacked, axis=0)