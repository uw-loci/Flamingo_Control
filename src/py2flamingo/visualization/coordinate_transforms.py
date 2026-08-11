"""
Coordinate transformation utilities for 3D visualization.
Handles rotation transformations for sample positioning and
physical mm to napari pixel coordinate mapping.

Performance optimizations (SciPy 1.14+):
- Uses scipy.spatial.transform.Slerp for SLERP interpolation (2-3x faster)
- Cached matrix inversions via lru_cache (avoid recomputation)
- Optimized rotation matrix construction
"""

import logging
from enum import IntEnum
from functools import lru_cache
from typing import Dict, Optional, Tuple

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

logger = logging.getLogger(__name__)


@lru_cache(maxsize=128)
def _cached_inverse_matrix(matrix_tuple: tuple) -> np.ndarray:
    """
    Cached matrix inversion to avoid recomputation.

    Matrix inversions are expensive (~O(n³)) and often repeated
    for the same rotation matrices during transforms.

    Args:
        matrix_tuple: Flattened matrix as tuple (hashable for cache)

    Returns:
        Inverted matrix
    """
    matrix = np.array(matrix_tuple).reshape(4, 4)
    return np.linalg.inv(matrix)


class TransformQuality(IntEnum):
    """Quality modes for volume transforms."""

    FAST = 0  # Nearest-neighbor interpolation - ~3-5x faster
    QUALITY = 1  # Linear interpolation - smoother but slower


class CoordinateTransformer:
    """
    Handles transformation from camera/stage space to world space
    accounting for sample rotation.
    """

    def __init__(self, sample_center: Optional[Tuple[float, float, float]] = None):
        """
        Initialize coordinate transformer.

        Args:
            sample_center: Center of rotation in world coordinates (micrometers)
        """
        self.sample_center = np.array(sample_center or [0, 0, 0])
        self.rotation_matrix = np.eye(3)
        self.current_rotation = {"rx": 0, "ry": 0, "rz": 0}  # degrees

        logger.info(
            f"Initialized CoordinateTransformer with center at {self.sample_center}"
        )

    def set_rotation(self, rx: float = 0, ry: float = 0, rz: float = 0):
        """
        Set sample rotation in degrees.

        Args:
            rx: Rotation around X axis (degrees)
            ry: Rotation around Y axis (degrees)
            rz: Rotation around Z axis (degrees)
        """
        self.current_rotation = {"rx": rx, "ry": ry, "rz": rz}

        # Create rotation matrix (order matters - typically Z-Y-X for microscopy)
        r = Rotation.from_euler("zyx", [rz, ry, rx], degrees=True)
        self.rotation_matrix = r.as_matrix()

        logger.debug(f"Updated rotation to rx={rx}°, ry={ry}°, rz={rz}°")

    def transform_voxel_volume_affine(
        self,
        volume: np.ndarray,
        stage_offset_mm: Tuple[float, float, float],
        rotation_deg: float,
        center_voxels: np.ndarray,
        voxel_size_um: float = 50.0,
        quality: TransformQuality = TransformQuality.QUALITY,
    ) -> np.ndarray:
        """
        Transform entire voxel volume using affine transformation.
        Uses existing rotation utilities for consistency.

        Optimized with:
        - Cached matrix inversions (avoid recomputation for same params)
        - Efficient scipy.ndimage.affine_transform

        This method applies:
        1. Translation to origin (center point)
        2. Rotation around Y-axis
        3. Translation back from origin
        4. Stage position offset

        Args:
            volume: 3D numpy array to transform
            stage_offset_mm: (dx, dy, dz) stage offset in millimeters
            rotation_deg: Y-axis rotation in degrees
            center_voxels: (x, y, z) rotation center in voxel coordinates
            voxel_size_um: Voxel size in micrometers (default 50)
            quality: TransformQuality.FAST (nearest-neighbor) or QUALITY (linear)
                    FAST is ~3-5x faster, suitable for interactive preview

        Returns:
            Transformed 3D volume with same shape as input
        """
        from scipy.ndimage import affine_transform

        # Set rotation for Y-axis only
        self.set_rotation(ry=rotation_deg)

        # Build affine transformation matrix
        # Order: T3 @ T2 @ R @ T1 (translate to origin, rotate, translate back, apply offset)

        # T1: Translate center to origin
        T1 = np.eye(4)
        T1[:3, 3] = -center_voxels

        # R: Rotation matrix (use existing rotation_matrix)
        R = np.eye(4)
        R[:3, :3] = self.rotation_matrix

        # T2: Translate back from origin
        T2 = np.eye(4)
        T2[:3, 3] = center_voxels

        # T3: Apply stage offset (convert mm to voxels)
        T3 = np.eye(4)
        offset_voxels = np.array(stage_offset_mm) * 1000.0 / voxel_size_um
        T3[:3, 3] = offset_voxels

        # Combine transformations in correct order (forward transform)
        # This is the geometric transform that maps input coords to output coords
        combined = T3 @ T2 @ R @ T1

        # scipy's affine_transform: output[i] = input[M @ coords + offset]
        # This means scipy needs the INVERSE transformation (output to input)
        # to correctly map where each output pixel comes from in the input

        # Use cached inverse for repeated transforms with same matrix
        # Convert to tuple for hashable cache key
        combined_tuple = tuple(combined.flatten())
        combined_inv = _cached_inverse_matrix(combined_tuple)

        # Select interpolation order based on quality mode
        # order=0: nearest-neighbor (~3-5x faster, blocky appearance)
        # order=1: linear interpolation (smoother, slower)
        interp_order = int(quality)

        # Apply transformation using scipy with the inverse matrix
        transformed = affine_transform(
            volume,
            combined_inv[:3, :3],  # Inverse rotation/scale matrix
            offset=combined_inv[:3, 3],  # Inverse offset
            order=interp_order,
            mode="constant",
            cval=0,
        )

        return transformed


class PhysicalToNapariMapper:
    """
    Maps between physical stage coordinates (mm) and napari pixel coordinates.

    Napari coordinate system:
        - Origin (0,0,0) at back upper left
        - Z=0: Back wall (where objective is located)
        - Y=0: Top of chamber
        - X=0: Left side of chamber

    Physical coordinate system:
        - X: Stage left-right position (mm)
        - Y: Stage vertical position (mm) - inverted for intuitive "up" direction
        - Z: Stage depth position (mm)

    Features:
        - Bidirectional transformation (physical ↔ napari)
        - Y-axis inversion for user-friendly visualization
        - Optional X/Z axis inversion for different stage configurations
        - Validation of physical positions against chamber bounds
    """

    def __init__(self, config: Dict):
        """
        Initialize the physical to napari coordinate mapper.

        Args:
            config: Configuration dictionary with:
                - x_range_mm: [x_min, x_max]
                - y_range_mm: [y_min, y_max]
                - z_range_mm: [z_min, z_max]
                - voxel_size_um: Voxel size in micrometers
        """
        # Physical ranges (mm)
        self.x_range_mm = tuple(config["x_range_mm"])
        self.y_range_mm = tuple(config["y_range_mm"])
        self.z_range_mm = tuple(config["z_range_mm"])

        # Voxel size (convert µm to mm)
        self.voxel_size_mm = config["voxel_size_um"] / 1000.0

        # Inversion flags (can be set by user preferences)
        self.invert_x = config.get("invert_x", False)
        self.invert_z = config.get("invert_z", False)

        # Per-microscope stage->napari orientation. When the config carries an
        # explicit 'orientation' block it is used; otherwise the legacy
        # convention (parameterized by invert_x/invert_z) — bit-identical.
        from py2flamingo.visualization.axis_orientation import AxisOrientation

        self.orientation = config.get("orientation_obj") or AxisOrientation.from_config(
            config, invert_x=self.invert_x, invert_z=self.invert_z
        )

        # Calculate napari dimensions in pixels
        self.napari_dims = self._calculate_napari_dimensions()

        logger.info(f"Initialized PhysicalToNapariMapper:")
        logger.info(
            f"  Physical ranges: X={self.x_range_mm}, Y={self.y_range_mm}, Z={self.z_range_mm}"
        )
        logger.info(f"  Voxel size: {self.voxel_size_mm*1000:.1f} µm")
        logger.info(f"  Napari dims: {self.napari_dims} pixels")
        logger.info(f"  Inversions: X={self.invert_x}, Z={self.invert_z}")

    def _ranges(self) -> Dict[str, Tuple[float, float]]:
        return {"x": self.x_range_mm, "y": self.y_range_mm, "z": self.z_range_mm}

    def _calculate_napari_dimensions(self) -> Tuple[int, int, int]:
        """Napari volume dimensions in pixels, ordered (napari_x, napari_y, napari_z).

        Each entry is the extent of whichever stage axis drives that display axis
        under the current orientation (legacy: horizontal=X, vertical=Y, depth=Z).
        """
        from py2flamingo.visualization.axis_orientation import (
            DEPTH,
            HORIZONTAL,
            VERTICAL,
        )

        r = self._ranges()
        return (
            self.orientation.display_extent(HORIZONTAL, r, self.voxel_size_mm),
            self.orientation.display_extent(VERTICAL, r, self.voxel_size_mm),
            self.orientation.display_extent(DEPTH, r, self.voxel_size_mm),
        )

    def physical_to_napari(
        self, x_mm: float, y_mm: float, z_mm: float
    ) -> Tuple[int, int, int]:
        """
        Convert physical stage coordinates (mm) to napari pixel coordinates.

        Args:
            x_mm: Physical X position in mm
            y_mm: Physical Y position in mm
            z_mm: Physical Z position in mm

        Returns:
            (napari_x, napari_y, napari_z) in pixel coordinates
        """
        # Per-microscope orientation: absolute() returns (depth, vertical,
        # horizontal) = napari (Z, Y, X). Legacy reproduces the old formulas.
        a0, a1, a2 = self.orientation.absolute(
            x_mm, y_mm, z_mm, self._ranges(), self.voxel_size_mm
        )
        napari_z, napari_y, napari_x = a0, a1, a2

        # Round to nearest pixel
        napari_x = int(round(napari_x))
        napari_y = int(round(napari_y))
        napari_z = int(round(napari_z))

        # Clamp to valid range
        napari_x = np.clip(napari_x, 0, self.napari_dims[0] - 1)
        napari_y = np.clip(napari_y, 0, self.napari_dims[1] - 1)
        napari_z = np.clip(napari_z, 0, self.napari_dims[2] - 1)

        return (napari_x, napari_y, napari_z)

    def napari_to_physical(
        self, napari_x: int, napari_y: int, napari_z: int
    ) -> Tuple[float, float, float]:
        """
        Convert napari pixel coordinates to physical stage coordinates (mm).

        Args:
            napari_x: Napari X pixel coordinate
            napari_y: Napari Y pixel coordinate
            napari_z: Napari Z pixel coordinate

        Returns:
            (x_mm, y_mm, z_mm) in physical mm coordinates
        """
        # Inverse of the orientation mapping. absolute() consumed
        # (a0,a1,a2)=(napari_z, napari_y, napari_x).
        x_mm, y_mm, z_mm = self.orientation.inverse_absolute(
            napari_z, napari_y, napari_x, self._ranges(), self.voxel_size_mm
        )
        return (x_mm, y_mm, z_mm)

    def get_napari_dimensions(self) -> Tuple[int, int, int]:
        """Get napari volume dimensions in pixels."""
        return self.napari_dims

    def test_round_trip(
        self, x_mm: float, y_mm: float, z_mm: float, tolerance: float = None
    ) -> bool:
        """
        Test round-trip transformation (physical → napari → physical).

        Args:
            x_mm, y_mm, z_mm: Physical coordinates to test
            tolerance: Maximum allowed error in mm (default: voxel_size_mm)

        Returns:
            True if round-trip error is within tolerance
        """
        # Default tolerance is one voxel size (quantization error)
        if tolerance is None:
            tolerance = self.voxel_size_mm

        # Forward transform
        napari_coords = self.physical_to_napari(x_mm, y_mm, z_mm)

        # Backward transform
        x_back, y_back, z_back = self.napari_to_physical(*napari_coords)

        # Calculate errors
        error_x = abs(x_back - x_mm)
        error_y = abs(y_back - y_mm)
        error_z = abs(z_back - z_mm)

        max_error = max(error_x, error_y, error_z)

        logger.debug(
            f"Round-trip test: ({x_mm:.2f}, {y_mm:.2f}, {z_mm:.2f}) → "
            f"{napari_coords} → ({x_back:.2f}, {y_back:.2f}, {z_back:.2f})"
        )
        logger.debug(f"  Max error: {max_error:.4f} mm (tolerance: {tolerance:.4f} mm)")

        return max_error <= tolerance
