"""
Coordinate transformation utilities for 3D visualization.
Handles rotation transformations for sample positioning and
physical mm to napari pixel coordinate mapping.
"""

import numpy as np
from scipy.spatial.transform import Rotation
from typing import Tuple, Optional, Dict
import logging

logger = logging.getLogger(__name__)


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
        self.current_rotation = {'rx': 0, 'ry': 0, 'rz': 0}  # degrees

        logger.info(f"Initialized CoordinateTransformer with center at {self.sample_center}")

    def set_rotation(self, rx: float = 0, ry: float = 0, rz: float = 0):
        """
        Set sample rotation in degrees.

        Args:
            rx: Rotation around X axis (degrees)
            ry: Rotation around Y axis (degrees)
            rz: Rotation around Z axis (degrees)
        """
        self.current_rotation = {'rx': rx, 'ry': ry, 'rz': rz}

        # Create rotation matrix (order matters - typically Z-Y-X for microscopy)
        r = Rotation.from_euler('zyx', [rz, ry, rx], degrees=True)
        self.rotation_matrix = r.as_matrix()

        logger.debug(f"Updated rotation to rx={rx}°, ry={ry}°, rz={rz}°")

    def camera_to_world(self, camera_coords: np.ndarray, z_position: float) -> np.ndarray:
        """
        Transform 2D camera coordinates + Z position to 3D world coordinates
        accounting for sample rotation.

        Args:
            camera_coords: (N, 2) array of X,Y positions in camera space (micrometers)
            z_position: Current Z position of the focal plane (micrometers)

        Returns:
            world_coords: (N, 3) array of X,Y,Z positions in world space
        """
        # Ensure camera_coords is 2D
        if camera_coords.ndim == 1:
            camera_coords = camera_coords.reshape(1, -1)

        # Convert 2D camera coords to 3D by adding Z
        coords_3d = np.column_stack([
            camera_coords[:, 0],
            camera_coords[:, 1],
            np.full(len(camera_coords), z_position)
        ])

        # Center coordinates around rotation center
        centered = coords_3d - self.sample_center

        # Apply rotation
        rotated = centered @ self.rotation_matrix.T

        # Translate back
        world_coords = rotated + self.sample_center

        return world_coords

    def world_to_camera(self, world_coords: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Transform 3D world coordinates to camera space.
        Inverse of camera_to_world.

        Args:
            world_coords: (N, 3) array of world coordinates

        Returns:
            camera_coords: (N, 2) array of X,Y camera coordinates
            z_positions: (N,) array of Z positions
        """
        # Center coordinates
        centered = world_coords - self.sample_center

        # Apply inverse rotation (transpose of rotation matrix)
        unrotated = centered @ self.rotation_matrix

        # Translate back
        camera_3d = unrotated + self.sample_center

        # Split into 2D camera coords and Z
        camera_coords = camera_3d[:, :2]
        z_positions = camera_3d[:, 2]

        return camera_coords, z_positions

    def world_to_voxel(self, world_coords: np.ndarray,
                      voxel_size: Tuple[float, float, float]) -> np.ndarray:
        """
        Convert world coordinates (micrometers) to voxel indices.

        Args:
            world_coords: (N, 3) array of world coordinates
            voxel_size: Size of each voxel in micrometers (x, y, z)

        Returns:
            voxel_indices: (N, 3) array of voxel indices
        """
        voxel_size_array = np.array(voxel_size)
        voxel_coords = world_coords / voxel_size_array
        voxel_indices = np.round(voxel_coords).astype(int)
        return voxel_indices

    def voxel_to_world(self, voxel_indices: np.ndarray,
                      voxel_size: Tuple[float, float, float]) -> np.ndarray:
        """
        Convert voxel indices to world coordinates.

        Args:
            voxel_indices: (N, 3) array of voxel indices
            voxel_size: Size of each voxel in micrometers

        Returns:
            world_coords: (N, 3) array of world coordinates
        """
        voxel_size_array = np.array(voxel_size)
        world_coords = voxel_indices * voxel_size_array
        return world_coords

    def get_rotation_matrix(self) -> np.ndarray:
        """Get current 3x3 rotation matrix."""
        return self.rotation_matrix.copy()

    def get_transformation_matrix(self) -> np.ndarray:
        """
        Get full 4x4 homogeneous transformation matrix.

        Returns:
            4x4 transformation matrix
        """
        T = np.eye(4)
        T[:3, :3] = self.rotation_matrix
        T[:3, 3] = self.sample_center
        return T

    def apply_to_plane(self, plane_corners: np.ndarray, z_position: float) -> np.ndarray:
        """
        Apply transformation to the corners of an imaging plane.
        Useful for visualizing the scan region in 3D.

        Args:
            plane_corners: (4, 2) array of corner points in camera space
            z_position: Z position of the plane

        Returns:
            transformed_corners: (4, 3) array in world space
        """
        return self.camera_to_world(plane_corners, z_position)

    def calculate_scan_volume(self, scan_bounds: dict) -> dict:
        """
        Calculate the 3D bounding box of a scan volume after rotation.

        Args:
            scan_bounds: Dictionary with 'x_range', 'y_range', 'z_range' in camera space

        Returns:
            Dictionary with 'min' and 'max' points in world space
        """
        # Create corners of scan volume
        x_min, x_max = scan_bounds['x_range']
        y_min, y_max = scan_bounds['y_range']
        z_min, z_max = scan_bounds['z_range']

        corners = np.array([
            [x_min, y_min, z_min],
            [x_min, y_min, z_max],
            [x_min, y_max, z_min],
            [x_min, y_max, z_max],
            [x_max, y_min, z_min],
            [x_max, y_min, z_max],
            [x_max, y_max, z_min],
            [x_max, y_max, z_max]
        ])

        # Transform corners
        transformed = []
        for corner in corners:
            cam_coord = corner[:2].reshape(1, 2)
            world = self.camera_to_world(cam_coord, corner[2])
            transformed.append(world[0])

        transformed = np.array(transformed)

        return {
            'min': np.min(transformed, axis=0),
            'max': np.max(transformed, axis=0)
        }

    def create_rotation_interpolation(self, start_rotation: dict,
                                     end_rotation: dict,
                                     num_steps: int) -> list:
        """
        Create interpolated rotation steps for smooth transitions.

        Args:
            start_rotation: Starting rotation {'rx': , 'ry': , 'rz': } in degrees
            end_rotation: Ending rotation {'rx': , 'ry': , 'rz': } in degrees
            num_steps: Number of interpolation steps

        Returns:
            List of rotation dictionaries
        """
        # Convert to Rotation objects
        r_start = Rotation.from_euler('zyx',
                                      [start_rotation['rz'],
                                       start_rotation['ry'],
                                       start_rotation['rx']],
                                      degrees=True)
        r_end = Rotation.from_euler('zyx',
                                    [end_rotation['rz'],
                                     end_rotation['ry'],
                                     end_rotation['rx']],
                                    degrees=True)

        # Interpolate using SLERP (Spherical Linear Interpolation)
        times = np.linspace(0, 1, num_steps)
        interpolated = []

        for t in times:
            # SLERP interpolation
            r_interp = Rotation.from_matrix(
                Rotation.from_quat(
                    self._slerp(r_start.as_quat(), r_end.as_quat(), t)
                ).as_matrix()
            )

            # Convert back to Euler angles
            angles = r_interp.as_euler('zyx', degrees=True)
            interpolated.append({
                'rx': angles[2],
                'ry': angles[1],
                'rz': angles[0]
            })

        return interpolated

    def _slerp(self, q1: np.ndarray, q2: np.ndarray, t: float) -> np.ndarray:
        """
        Spherical linear interpolation between quaternions.

        Args:
            q1: Start quaternion
            q2: End quaternion
            t: Interpolation parameter (0 to 1)

        Returns:
            Interpolated quaternion
        """
        # Normalize quaternions
        q1 = q1 / np.linalg.norm(q1)
        q2 = q2 / np.linalg.norm(q2)

        # Compute angle between quaternions
        dot = np.dot(q1, q2)

        # If quaternions are very close, use linear interpolation
        if dot > 0.9995:
            result = q1 + t * (q2 - q1)
            return result / np.linalg.norm(result)

        # Ensure shortest path
        if dot < 0:
            q2 = -q2
            dot = -dot

        # Clamp dot product
        dot = np.clip(dot, -1, 1)

        # Calculate interpolation coefficients
        theta = np.arccos(dot)
        sin_theta = np.sin(theta)

        if sin_theta > 0.001:  # Avoid division by small numbers
            w1 = np.sin((1 - t) * theta) / sin_theta
            w2 = np.sin(t * theta) / sin_theta
        else:
            w1 = 1 - t
            w2 = t

        return w1 * q1 + w2 * q2

    def transform_voxel_volume_affine(self, volume: np.ndarray,
                                     stage_offset_mm: Tuple[float, float, float],
                                     rotation_deg: float,
                                     center_voxels: np.ndarray,
                                     voxel_size_um: float = 50.0) -> np.ndarray:
        """
        Transform entire voxel volume using affine transformation.
        Uses existing rotation utilities for consistency.

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

        # Combine transformations in correct order
        combined = T3 @ T2 @ R @ T1

        # Apply transformation using scipy
        # Note: scipy expects the inverse transformation matrix
        # and we transpose the rotation part
        transformed = affine_transform(
            volume,
            combined[:3, :3].T,  # Transpose for scipy convention
            offset=combined[:3, 3],
            order=1,  # Linear interpolation
            mode='constant',
            cval=0
        )

        return transformed

    def rotate_volume_with_padding(self, volume: np.ndarray,
                                  angle_degrees: float,
                                  center_voxels: np.ndarray,
                                  pad_size: int = 20) -> np.ndarray:
        """
        Rotate volume with padding to prevent edge clipping.

        Args:
            volume: 3D numpy array to rotate
            angle_degrees: Y-axis rotation angle
            center_voxels: Rotation center in voxel coordinates
            pad_size: Padding size in voxels

        Returns:
            Rotated volume with same shape as input
        """
        # Pad volume to prevent clipping
        padded = np.pad(volume, pad_size, mode='constant', constant_values=0)

        # Adjust center for padding
        center_padded = center_voxels + pad_size

        # Apply rotation with no stage offset
        rotated_padded = self.transform_voxel_volume_affine(
            padded,
            stage_offset_mm=(0, 0, 0),
            rotation_deg=angle_degrees,
            center_voxels=center_padded
        )

        # Crop back to original size
        if pad_size > 0:
            return rotated_padded[
                pad_size:-pad_size,
                pad_size:-pad_size,
                pad_size:-pad_size
            ]
        return rotated_padded


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
        self.x_range_mm = tuple(config['x_range_mm'])
        self.y_range_mm = tuple(config['y_range_mm'])
        self.z_range_mm = tuple(config['z_range_mm'])

        # Voxel size (convert µm to mm)
        self.voxel_size_mm = config['voxel_size_um'] / 1000.0

        # Inversion flags (can be set by user preferences)
        self.invert_x = config.get('invert_x', False)
        self.invert_z = config.get('invert_z', False)

        # Calculate napari dimensions in pixels
        self.napari_dims = self._calculate_napari_dimensions()

        logger.info(f"Initialized PhysicalToNapariMapper:")
        logger.info(f"  Physical ranges: X={self.x_range_mm}, Y={self.y_range_mm}, Z={self.z_range_mm}")
        logger.info(f"  Voxel size: {self.voxel_size_mm*1000:.1f} µm")
        logger.info(f"  Napari dims: {self.napari_dims} pixels")
        logger.info(f"  Inversions: X={self.invert_x}, Z={self.invert_z}")

    def _calculate_napari_dimensions(self) -> Tuple[int, int, int]:
        """Calculate napari volume dimensions in pixels."""
        width_x = int((self.x_range_mm[1] - self.x_range_mm[0]) / self.voxel_size_mm)
        height_y = int((self.y_range_mm[1] - self.y_range_mm[0]) / self.voxel_size_mm)
        depth_z = int((self.z_range_mm[1] - self.z_range_mm[0]) / self.voxel_size_mm)

        return (width_x, height_y, depth_z)

    def set_inversions(self, invert_x: bool = None, invert_z: bool = None):
        """
        Set axis inversion flags.

        Args:
            invert_x: If True, invert X axis direction
            invert_z: If True, invert Z axis direction
        """
        if invert_x is not None:
            self.invert_x = invert_x
            logger.info(f"X axis inversion set to: {self.invert_x}")

        if invert_z is not None:
            self.invert_z = invert_z
            logger.info(f"Z axis inversion set to: {self.invert_z}")

    def physical_to_napari(self, x_mm: float, y_mm: float, z_mm: float) -> Tuple[int, int, int]:
        """
        Convert physical stage coordinates (mm) to napari pixel coordinates.

        Args:
            x_mm: Physical X position in mm
            y_mm: Physical Y position in mm
            z_mm: Physical Z position in mm

        Returns:
            (napari_x, napari_y, napari_z) in pixel coordinates
        """
        # Apply inversions to physical coordinates if enabled
        x_eff = self._apply_x_inversion(x_mm)
        z_eff = self._apply_z_inversion(z_mm)

        # X: left to right (straightforward mapping)
        napari_x = (x_eff - self.x_range_mm[0]) / self.voxel_size_mm

        # Y: INVERTED (y_max maps to napari Y=0, y_min maps to napari Y=max)
        # This makes increasing Y move "up" visually in the display
        napari_y = (self.y_range_mm[1] - y_mm) / self.voxel_size_mm

        # Z: back to front (objective at Z=0)
        napari_z = (z_eff - self.z_range_mm[0]) / self.voxel_size_mm

        # Round to nearest pixel
        napari_x = int(round(napari_x))
        napari_y = int(round(napari_y))
        napari_z = int(round(napari_z))

        # Clamp to valid range
        napari_x = np.clip(napari_x, 0, self.napari_dims[0] - 1)
        napari_y = np.clip(napari_y, 0, self.napari_dims[1] - 1)
        napari_z = np.clip(napari_z, 0, self.napari_dims[2] - 1)

        return (napari_x, napari_y, napari_z)

    def napari_to_physical(self, napari_x: int, napari_y: int, napari_z: int) -> Tuple[float, float, float]:
        """
        Convert napari pixel coordinates to physical stage coordinates (mm).

        Args:
            napari_x: Napari X pixel coordinate
            napari_y: Napari Y pixel coordinate
            napari_z: Napari Z pixel coordinate

        Returns:
            (x_mm, y_mm, z_mm) in physical mm coordinates
        """
        # Convert pixels to mm
        x_mm = napari_x * self.voxel_size_mm + self.x_range_mm[0]
        y_mm = self.y_range_mm[1] - (napari_y * self.voxel_size_mm)  # Y inverted
        z_mm = napari_z * self.voxel_size_mm + self.z_range_mm[0]

        # Unapply inversions
        x_mm = self._unapply_x_inversion(x_mm)
        z_mm = self._unapply_z_inversion(z_mm)

        return (x_mm, y_mm, z_mm)

    def _apply_x_inversion(self, x_mm: float) -> float:
        """Apply X axis inversion if enabled."""
        if self.invert_x:
            # Reflect around center of X range
            center_x = (self.x_range_mm[0] + self.x_range_mm[1]) / 2
            return 2 * center_x - x_mm
        return x_mm

    def _unapply_x_inversion(self, x_mm: float) -> float:
        """Unapply X axis inversion (inverse operation)."""
        # Inversion is symmetric, so same operation
        return self._apply_x_inversion(x_mm)

    def _apply_z_inversion(self, z_mm: float) -> float:
        """Apply Z axis inversion if enabled."""
        if self.invert_z:
            # Reflect around center of Z range
            center_z = (self.z_range_mm[0] + self.z_range_mm[1]) / 2
            return 2 * center_z - z_mm
        return z_mm

    def _unapply_z_inversion(self, z_mm: float) -> float:
        """Unapply Z axis inversion (inverse operation)."""
        # Inversion is symmetric, so same operation
        return self._apply_z_inversion(z_mm)

    def validate_physical_position(self, x_mm: float, y_mm: float, z_mm: float) -> bool:
        """
        Check if physical position is within chamber bounds.

        Args:
            x_mm: Physical X position
            y_mm: Physical Y position
            z_mm: Physical Z position

        Returns:
            True if position is within bounds, False otherwise
        """
        x_valid = self.x_range_mm[0] <= x_mm <= self.x_range_mm[1]
        y_valid = self.y_range_mm[0] <= y_mm <= self.y_range_mm[1]
        z_valid = self.z_range_mm[0] <= z_mm <= self.z_range_mm[1]

        return x_valid and y_valid and z_valid

    def get_physical_bounds(self) -> Dict[str, Tuple[float, float]]:
        """Get physical coordinate bounds."""
        return {
            'x': self.x_range_mm,
            'y': self.y_range_mm,
            'z': self.z_range_mm
        }

    def get_napari_dimensions(self) -> Tuple[int, int, int]:
        """Get napari volume dimensions in pixels."""
        return self.napari_dims

    def test_round_trip(self, x_mm: float, y_mm: float, z_mm: float,
                       tolerance: float = None) -> bool:
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

        logger.debug(f"Round-trip test: ({x_mm:.2f}, {y_mm:.2f}, {z_mm:.2f}) → "
                    f"{napari_coords} → ({x_back:.2f}, {y_back:.2f}, {z_back:.2f})")
        logger.debug(f"  Max error: {max_error:.4f} mm (tolerance: {tolerance:.4f} mm)")

        return max_error <= tolerance