"""Tile Z-range calculation utilities.

Pure math functions for calculating Z ranges from tile overlap,
extracted from tile_collection_dialog.py.
"""

import logging
import math
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)


def calculate_tile_z_ranges(
    primary_tiles: List,
    secondary_tiles: List,
    fallback_z_min: float,
    fallback_z_max: float
) -> Dict[Tuple[int, int], Tuple[float, float]]:
    """Calculate Z range for each primary tile based on secondary tile overlap.

    When tiles are selected from two orthogonal views (e.g., 0° and 90°),
    this function determines the Z range for each primary tile using the
    Z-stack bounds from secondary tiles that spatially overlap.

    Since tiles now store rotation_angle and z_stack_min/max, this function
    uses a simplified approach: for each primary tile, find all secondary tiles
    whose positions are reasonably close (within FOV distance) and aggregate
    their Z-stack bounds.

    Args:
        primary_tiles: Tiles from the primary view (where Z-stacks will be taken)
        secondary_tiles: Tiles from the secondary view (defines Z limits)
        fallback_z_min: Fallback minimum Z if no secondary tiles
        fallback_z_max: Fallback maximum Z if no secondary tiles

    Returns:
        Dictionary mapping (tile_x_idx, tile_y_idx) to (z_min, z_max)
    """
    # If no secondary tiles, use fallback range for all primary tiles
    if not secondary_tiles:
        logger.info(f"No secondary tiles - using fallback Z range [{fallback_z_min:.3f}, {fallback_z_max:.3f}] mm "
                   f"for all {len(primary_tiles)} primary tiles")
        return {
            (tile.tile_x_idx, tile.tile_y_idx): (fallback_z_min, fallback_z_max)
            for tile in primary_tiles
        }

    # Calculate FOV from tile spacing (tiles are adjacent with no overlap)
    fov_mm = estimate_fov_from_tiles(primary_tiles, secondary_tiles)
    logger.info(f"Estimated FOV: {fov_mm*1000:.1f} µm for spatial overlap calculation")

    # For each primary tile, find overlapping secondary tiles and collect Z-stack bounds
    tile_z_ranges = {}
    fallback_count = 0

    for p_tile in primary_tiles:
        # Collect Z-stack bounds from all secondary tiles that could overlap
        # Use a generous distance threshold (2x FOV) to account for rotation effects
        overlap_threshold = 2.0 * fov_mm
        overlapping_z_bounds = []

        for s_tile in secondary_tiles:
            # Calculate 3D distance between tile centers
            dx = p_tile.x - s_tile.x
            dy = p_tile.y - s_tile.y
            dz = p_tile.z - s_tile.z
            distance = math.sqrt(dx*dx + dy*dy + dz*dz)

            # If tiles are within overlap threshold, use secondary's Z-stack bounds
            if distance < overlap_threshold:
                # Use the Z-stack bounds from the secondary tile
                # These represent the actual Z range that was scanned
                if s_tile.z_stack_min != 0.0 or s_tile.z_stack_max != 0.0:
                    overlapping_z_bounds.append((s_tile.z_stack_min, s_tile.z_stack_max))
                    logger.debug(
                        f"Tile ({p_tile.tile_x_idx},{p_tile.tile_y_idx}) overlaps with secondary tile at "
                        f"({s_tile.x:.3f},{s_tile.y:.3f},{s_tile.z:.3f}), distance={distance*1000:.1f}µm, "
                        f"Z bounds=[{s_tile.z_stack_min:.3f}, {s_tile.z_stack_max:.3f}]"
                    )

        # Determine Z range for this primary tile
        if overlapping_z_bounds:
            # Use the envelope (min of mins, max of maxes) from all overlapping tiles
            all_z_mins = [bounds[0] for bounds in overlapping_z_bounds]
            all_z_maxs = [bounds[1] for bounds in overlapping_z_bounds]
            z_min = min(all_z_mins)
            z_max = max(all_z_maxs)
            logger.debug(
                f"Tile ({p_tile.tile_x_idx},{p_tile.tile_y_idx}) at ({p_tile.x:.3f},{p_tile.y:.3f},{p_tile.z:.3f}): "
                f"Found {len(overlapping_z_bounds)} overlapping tiles, Z range [{z_min:.3f}, {z_max:.3f}] mm"
            )
        else:
            # No overlap found - use fallback
            # This can happen with non-contiguous selections or gaps
            z_min = fallback_z_min
            z_max = fallback_z_max
            fallback_count += 1
            logger.warning(
                f"Tile ({p_tile.tile_x_idx},{p_tile.tile_y_idx}) at ({p_tile.x:.3f},{p_tile.y:.3f},{p_tile.z:.3f}): "
                f"No spatial overlap with secondary tiles. Using fallback Z range [{z_min:.3f}, {z_max:.3f}] mm"
            )

        # Validate Z range
        if z_max <= z_min:
            logger.error(f"Invalid Z range for tile ({p_tile.tile_x_idx},{p_tile.tile_y_idx}): "
                        f"[{z_min:.3f}, {z_max:.3f}] - z_max <= z_min!")
        elif abs(z_max - z_min) < 0.001:  # Less than 1 um
            logger.warning(f"Very small Z range for tile ({p_tile.tile_x_idx},{p_tile.tile_y_idx}): "
                          f"{(z_max-z_min)*1000:.1f} µm")

        tile_z_ranges[(p_tile.tile_x_idx, p_tile.tile_y_idx)] = (z_min, z_max)

    # Log summary
    overlap_count = len(tile_z_ranges) - fallback_count
    logger.info(f"Z range calculation complete: {overlap_count} tiles with overlap-based ranges, "
               f"{fallback_count} tiles using fallback")

    return tile_z_ranges


def estimate_fov_from_tiles(primary_tiles: List, secondary_tiles: List) -> float:
    """Estimate field of view from tile spacing.

    Since tiles are adjacent with no overlap, the distance between
    adjacent tiles equals the FOV. We estimate from tiles in a single
    rotation to avoid mixing coordinate frames.

    Args:
        primary_tiles: Tiles from primary view
        secondary_tiles: Tiles from secondary view

    Returns:
        Estimated FOV in mm
    """
    # Try to estimate from primary tiles first (same rotation)
    if len(primary_tiles) >= 2:
        fov = min_distance_in_tile_set(primary_tiles)
        if fov < float('inf'):
            logger.debug(f"FOV estimated from primary tiles: {fov*1000:.1f} µm")
            return fov

    # Fallback to secondary tiles if primary insufficient
    if len(secondary_tiles) >= 2:
        fov = min_distance_in_tile_set(secondary_tiles)
        if fov < float('inf'):
            logger.debug(f"FOV estimated from secondary tiles: {fov*1000:.1f} µm")
            return fov

    # Ultimate fallback - use reasonable default for lightsheet microscopy
    logger.warning("Insufficient tiles for FOV estimation, using default 0.4mm (400µm)")
    return 0.4  # 400 um default


def min_distance_in_tile_set(tiles: List) -> float:
    """Find minimum non-zero distance between tiles in a set.

    Args:
        tiles: List of TileResult objects

    Returns:
        Minimum distance in mm, or inf if no valid distance found
    """
    if len(tiles) < 2:
        return float('inf')

    min_distance = float('inf')

    for i, tile1 in enumerate(tiles):
        for tile2 in tiles[i+1:]:
            # Calculate Euclidean distance in XY plane
            dx = tile1.x - tile2.x
            dy = tile1.y - tile2.y
            distance = math.sqrt(dx*dx + dy*dy)

            # Skip zero distances (same tile) and update minimum
            if distance > 1e-6 and distance < min_distance:
                min_distance = distance

    return min_distance
