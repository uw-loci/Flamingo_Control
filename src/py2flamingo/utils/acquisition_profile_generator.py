"""
Acquisition profile generator for variable Z-depth tile profiles.

Pure computation module - no Qt dependencies.
Generates tile acquisition profiles from 3D boolean masks,
with per-tile Z-depth based on mask extent at each XY position.
"""

import math
import logging
from dataclasses import dataclass
from typing import List, Tuple, Optional, Callable

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TileProfile:
    """A single tile in an acquisition profile with variable Z-depth.

    Attributes:
        x: Stage X position (mm)
        y: Stage Y position (mm)
        z_min: Z-stack start position (mm)
        z_max: Z-stack end position (mm)
        rotation_angle: Rotation angle in degrees
        tile_x_idx: Tile X index in the grid
        tile_y_idx: Tile Y index in the grid
    """
    x: float
    y: float
    z_min: float
    z_max: float
    rotation_angle: float
    tile_x_idx: int
    tile_y_idx: int

    @property
    def z_center(self) -> float:
        return (self.z_min + self.z_max) / 2.0


def mask_z_profile_at_xy(
    mask: np.ndarray,
    y_center_voxel: int,
    x_center_voxel: int,
    fov_half_y_voxels: int,
    fov_half_x_voxels: int,
) -> Optional[Tuple[int, int]]:
    """Extract Z extent of mask within a tile-sized window at given XY.

    Args:
        mask: 3D boolean array (Z, Y, X)
        y_center_voxel: Center Y voxel of the tile window
        x_center_voxel: Center X voxel of the tile window
        fov_half_y_voxels: Half-FOV in Y direction (voxels)
        fov_half_x_voxels: Half-FOV in X direction (voxels)

    Returns:
        (z_min_voxel, z_max_voxel) or None if no mask voxels in window
    """
    nz, ny, nx = mask.shape

    y_lo = max(0, y_center_voxel - fov_half_y_voxels)
    y_hi = min(ny, y_center_voxel + fov_half_y_voxels + 1)
    x_lo = max(0, x_center_voxel - fov_half_x_voxels)
    x_hi = min(nx, x_center_voxel + fov_half_x_voxels + 1)

    if y_lo >= y_hi or x_lo >= x_hi:
        return None

    window = mask[:, y_lo:y_hi, x_lo:x_hi]

    # Find Z extent: any nonzero in the YX window per Z slice
    z_any = window.any(axis=(1, 2))
    if not z_any.any():
        return None

    z_indices = np.where(z_any)[0]
    return (int(z_indices[0]), int(z_indices[-1]))


def rotate_point(
    x: float, z: float,
    tip_x: float, tip_z: float,
    angle_deg: float,
) -> Tuple[float, float]:
    """Rotate a point around tip position by given angle in X-Z plane.

    For 90 degrees this matches the established formula:
        x' = x_tip + (z - z_tip)
        z' = z_tip - (x - x_tip)

    Args:
        x: Original X coordinate
        z: Original Z coordinate
        tip_x: Rotation center X
        tip_z: Rotation center Z
        angle_deg: Rotation angle in degrees (positive = physical clockwise)

    Returns:
        (x_new, z_new) after rotation
    """
    angle_rad = math.radians(angle_deg)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    dx = x - tip_x
    dz = z - tip_z

    x_new = tip_x + dx * cos_a + dz * sin_a
    z_new = tip_z - dx * sin_a + dz * cos_a

    return (x_new, z_new)


def generate_tile_profile(
    mask: np.ndarray,
    voxel_to_stage_fn: Callable[[int, int, int], Tuple[float, float, float]],
    fov_mm: float,
    voxel_size_mm: float,
    buffer_fraction: float = 0.25,
    rotation_angles: Optional[List[float]] = None,
    tip_position: Optional[Tuple[float, float]] = None,
) -> List[TileProfile]:
    """Generate tile acquisition profiles from a 3D mask.

    Tiles a grid over the mask bounding box, computing per-tile Z ranges
    based on where the mask is nonzero within each tile's FOV footprint.

    Args:
        mask: 3D boolean array (Z, Y, X) - True where sample is above threshold
        voxel_to_stage_fn: Converts (z_voxel, y_voxel, x_voxel) → (x_mm, y_mm, z_mm)
        fov_mm: Field of view in mm (square)
        voxel_size_mm: Voxel size in mm
        buffer_fraction: Fraction of FOV to add as buffer around mask (default 0.25)
        rotation_angles: List of rotation angles in degrees (default [0])
        tip_position: (tip_x_mm, tip_z_mm) for rotation center. Required if
                      rotation_angles contains non-zero values.

    Returns:
        List of TileProfile objects for all angles
    """
    if rotation_angles is None:
        rotation_angles = [0.0]

    if mask.ndim != 3:
        raise ValueError(f"Mask must be 3D, got {mask.ndim}D")

    nonzero = np.argwhere(mask)
    if len(nonzero) == 0:
        logger.warning("Empty mask - no tiles to generate")
        return []

    nz, ny, nx = mask.shape

    # Bounding box in voxels
    z_min_v, y_min_v, x_min_v = nonzero.min(axis=0)
    z_max_v, y_max_v, x_max_v = nonzero.max(axis=0)

    # Convert bbox corners to stage coordinates
    x_min_mm, y_min_mm, z_min_mm = voxel_to_stage_fn(z_min_v, y_min_v, x_min_v)
    x_max_mm, y_max_mm, z_max_mm = voxel_to_stage_fn(z_max_v, y_max_v, x_max_v)

    # Ensure min <= max (voxel_to_stage_fn may invert axes)
    if x_min_mm > x_max_mm:
        x_min_mm, x_max_mm = x_max_mm, x_min_mm
    if y_min_mm > y_max_mm:
        y_min_mm, y_max_mm = y_max_mm, y_min_mm
    if z_min_mm > z_max_mm:
        z_min_mm, z_max_mm = z_max_mm, z_min_mm

    # Buffer in mm
    buffer_mm = buffer_fraction * fov_mm
    z_buffer_mm = buffer_fraction * fov_mm

    # FOV in voxels (for Z profile extraction)
    fov_voxels = max(1, int(round(fov_mm / voxel_size_mm)))
    fov_half_voxels = fov_voxels // 2

    all_profiles = []

    for angle in rotation_angles:
        if abs(angle) > 0.01 and tip_position is None:
            logger.warning(
                f"Skipping angle {angle}° - tip position required for rotation"
            )
            continue

        # For the base angle (first in list), tile directly over mask bbox
        # For rotated angles, rotate tile positions
        if abs(angle) < 0.01:
            # No rotation - tile directly
            profiles = _generate_tiles_for_angle(
                mask=mask,
                x_min_mm=x_min_mm - buffer_mm,
                x_max_mm=x_max_mm + buffer_mm,
                y_min_mm=y_min_mm - buffer_mm,
                y_max_mm=y_max_mm + buffer_mm,
                z_buffer_mm=z_buffer_mm,
                fov_mm=fov_mm,
                fov_half_voxels=fov_half_voxels,
                voxel_to_stage_fn=voxel_to_stage_fn,
                voxel_size_mm=voxel_size_mm,
                mask_shape=(nz, ny, nx),
                rotation_angle=0.0,
            )
            all_profiles.extend(profiles)
        else:
            # Rotate the mask bounding box corners around tip to find
            # the effective acquisition region
            tip_x, tip_z = tip_position
            corners_x = [x_min_mm - buffer_mm, x_max_mm + buffer_mm]
            corners_z = [z_min_mm - z_buffer_mm, z_max_mm + z_buffer_mm]

            rotated_xs = []
            rotated_zs = []
            for cx in corners_x:
                for cz in corners_z:
                    rx, rz = rotate_point(cx, cz, tip_x, tip_z, angle)
                    rotated_xs.append(rx)
                    rotated_zs.append(rz)

            # Rotated bounding box (axis-aligned envelope)
            rot_x_min = min(rotated_xs)
            rot_x_max = max(rotated_xs)
            rot_z_min = min(rotated_zs)
            rot_z_max = max(rotated_zs)

            # For rotated view: tile_x uses rotated X range, Z uses rotated Z range
            # Y is unchanged by X-Z rotation
            profiles = _generate_tiles_for_rotated_angle(
                mask=mask,
                tile_x_min=rot_x_min,
                tile_x_max=rot_x_max,
                y_min_mm=y_min_mm - buffer_mm,
                y_max_mm=y_max_mm + buffer_mm,
                z_min_mm=rot_z_min,
                z_max_mm=rot_z_max,
                z_buffer_mm=z_buffer_mm,
                fov_mm=fov_mm,
                fov_half_voxels=fov_half_voxels,
                voxel_to_stage_fn=voxel_to_stage_fn,
                voxel_size_mm=voxel_size_mm,
                mask_shape=(nz, ny, nx),
                rotation_angle=angle,
                tip_position=tip_position,
            )
            all_profiles.extend(profiles)

    logger.info(
        f"Generated {len(all_profiles)} tile profiles across "
        f"{len(rotation_angles)} angle(s)"
    )
    return all_profiles


def _stage_to_voxel_approx(
    x_mm: float, y_mm: float,
    voxel_to_stage_fn: Callable,
    voxel_size_mm: float,
    mask_shape: Tuple[int, int, int],
) -> Tuple[int, int]:
    """Approximate stage XY to voxel YX using inverse of voxel_to_stage_fn.

    We sample the voxel_to_stage_fn at the mask center to determine
    the axis directions, then use linear interpolation.

    Returns:
        (y_voxel, x_voxel)
    """
    nz, ny, nx = mask_shape
    cy, cx = ny // 2, nx // 2

    # Sample at center to get reference point
    ref_x_mm, ref_y_mm, _ = voxel_to_stage_fn(0, cy, cx)

    # Sample offset to determine axis direction
    if cx + 1 < nx:
        dx_x_mm, _, _ = voxel_to_stage_fn(0, cy, cx + 1)
        x_sign = 1.0 if dx_x_mm > ref_x_mm else -1.0
    else:
        x_sign = 1.0

    if cy + 1 < ny:
        _, dy_y_mm, _ = voxel_to_stage_fn(0, cy + 1, cx)
        y_sign = 1.0 if dy_y_mm > ref_y_mm else -1.0
    else:
        y_sign = -1.0  # Y typically inverted

    # Linear interpolation
    x_voxel = cx + int(round((x_mm - ref_x_mm) / (x_sign * voxel_size_mm)))
    y_voxel = cy + int(round((y_mm - ref_y_mm) / (y_sign * voxel_size_mm)))

    x_voxel = max(0, min(nx - 1, x_voxel))
    y_voxel = max(0, min(ny - 1, y_voxel))

    return (y_voxel, x_voxel)


def _generate_tiles_for_angle(
    mask: np.ndarray,
    x_min_mm: float, x_max_mm: float,
    y_min_mm: float, y_max_mm: float,
    z_buffer_mm: float,
    fov_mm: float,
    fov_half_voxels: int,
    voxel_to_stage_fn: Callable,
    voxel_size_mm: float,
    mask_shape: Tuple[int, int, int],
    rotation_angle: float,
) -> List[TileProfile]:
    """Generate tiles for a single (non-rotated) angle."""
    step = fov_mm
    profiles = []

    # Generate X positions
    x_positions = []
    x = x_min_mm
    while x <= x_max_mm + step / 2:
        x_positions.append(x)
        x += step

    # Generate Y positions
    y_positions = []
    y = y_min_mm
    while y <= y_max_mm + step / 2:
        y_positions.append(y)
        y += step

    # Serpentine pattern: X outer loop, Y inner loop
    for x_idx, x_pos in enumerate(x_positions):
        if x_idx % 2 == 0:
            y_range = list(enumerate(y_positions))
        else:
            y_range = list(reversed(list(enumerate(y_positions))))

        for y_idx, y_pos in y_range:
            # Convert tile center to voxel coordinates for Z profile
            y_voxel, x_voxel = _stage_to_voxel_approx(
                x_pos, y_pos, voxel_to_stage_fn, voxel_size_mm, mask_shape
            )

            z_extent = mask_z_profile_at_xy(
                mask, y_voxel, x_voxel, fov_half_voxels, fov_half_voxels
            )

            if z_extent is None:
                continue  # No mask voxels in this tile

            z_min_vox, z_max_vox = z_extent

            # Convert Z voxel range to stage mm
            _, _, z_min_stage = voxel_to_stage_fn(z_min_vox, 0, 0)
            _, _, z_max_stage = voxel_to_stage_fn(z_max_vox, 0, 0)
            if z_min_stage > z_max_stage:
                z_min_stage, z_max_stage = z_max_stage, z_min_stage

            # Add Z buffer
            z_min_stage -= z_buffer_mm
            z_max_stage += z_buffer_mm

            profiles.append(TileProfile(
                x=x_pos,
                y=y_pos,
                z_min=z_min_stage,
                z_max=z_max_stage,
                rotation_angle=rotation_angle,
                tile_x_idx=x_idx,
                tile_y_idx=y_idx,
            ))

    logger.info(
        f"Angle {rotation_angle}°: {len(profiles)} tiles from "
        f"{len(x_positions)}x{len(y_positions)} grid"
    )
    return profiles


def _generate_tiles_for_rotated_angle(
    mask: np.ndarray,
    tile_x_min: float, tile_x_max: float,
    y_min_mm: float, y_max_mm: float,
    z_min_mm: float, z_max_mm: float,
    z_buffer_mm: float,
    fov_mm: float,
    fov_half_voxels: int,
    voxel_to_stage_fn: Callable,
    voxel_size_mm: float,
    mask_shape: Tuple[int, int, int],
    rotation_angle: float,
    tip_position: Tuple[float, float],
) -> List[TileProfile]:
    """Generate tiles for a rotated angle.

    For rotated views, the tile grid is laid out in the rotated coordinate frame.
    Each tile's Z range is determined by inverse-rotating the tile position back
    to the mask coordinate frame and checking the mask Z extent there.
    """
    step = fov_mm
    tip_x, tip_z = tip_position
    profiles = []

    # Generate tile positions in rotated frame
    x_positions = []
    x = tile_x_min
    while x <= tile_x_max + step / 2:
        x_positions.append(x)
        x += step

    y_positions = []
    y = y_min_mm
    while y <= y_max_mm + step / 2:
        y_positions.append(y)
        y += step

    # For Z range check, inverse-rotate tile center back to mask frame
    inverse_angle = -rotation_angle

    for x_idx, x_pos in enumerate(x_positions):
        if x_idx % 2 == 0:
            y_range = list(enumerate(y_positions))
        else:
            y_range = list(reversed(list(enumerate(y_positions))))

        for y_idx, y_pos in y_range:
            # Inverse-rotate to find where this tile sees the mask
            orig_x, orig_z_center = rotate_point(
                x_pos, (z_min_mm + z_max_mm) / 2,
                tip_x, tip_z, inverse_angle
            )

            # Convert to voxel coords and check mask
            y_voxel, x_voxel = _stage_to_voxel_approx(
                orig_x, y_pos, voxel_to_stage_fn, voxel_size_mm, mask_shape
            )

            z_extent = mask_z_profile_at_xy(
                mask, y_voxel, x_voxel, fov_half_voxels, fov_half_voxels
            )

            if z_extent is None:
                continue

            z_min_vox, z_max_vox = z_extent

            # Convert Z voxel range to stage mm
            _, _, z_min_stage = voxel_to_stage_fn(z_min_vox, 0, 0)
            _, _, z_max_stage = voxel_to_stage_fn(z_max_vox, 0, 0)
            if z_min_stage > z_max_stage:
                z_min_stage, z_max_stage = z_max_stage, z_min_stage

            # Rotate the Z range to the rotated frame
            _, rot_z_min = rotate_point(orig_x, z_min_stage, tip_x, tip_z, rotation_angle)
            _, rot_z_max = rotate_point(orig_x, z_max_stage, tip_x, tip_z, rotation_angle)
            if rot_z_min > rot_z_max:
                rot_z_min, rot_z_max = rot_z_max, rot_z_min

            rot_z_min -= z_buffer_mm
            rot_z_max += z_buffer_mm

            profiles.append(TileProfile(
                x=x_pos,
                y=y_pos,
                z_min=rot_z_min,
                z_max=rot_z_max,
                rotation_angle=rotation_angle,
                tile_x_idx=x_idx,
                tile_y_idx=y_idx,
            ))

    logger.info(
        f"Angle {rotation_angle}°: {len(profiles)} tiles from "
        f"{len(x_positions)}x{len(y_positions)} grid"
    )
    return profiles
