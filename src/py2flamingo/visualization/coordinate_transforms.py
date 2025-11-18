"""
Coordinate transformation utilities for 3D visualization.
Handles rotation transformations for sample positioning.
"""

import numpy as np
from scipy.spatial.transform import Rotation
from typing import Tuple, Optional
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