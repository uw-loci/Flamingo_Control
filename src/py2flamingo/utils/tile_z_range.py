"""Tile Z-range calculation utilities.

Pure math functions for calculating Z ranges from tile overlap,
extracted from tile_collection_dialog.py.
"""

import logging
import math
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def calculate_tile_z_ranges(
    primary_tiles: List,
    secondary_tiles: List,
    fallback_z_min: float,
    fallback_z_max: float,
    tip_position: Optional[Tuple[float, float]] = None,
) -> Dict[Tuple[int, int], Tuple[float, float]]:
    """Calculate Z range for each primary tile based on secondary tile overlap.

    When tiles are selected from two orthogonal views (e.g., R=15° and R=105°),
    a 90° rotation swaps X and Z axes. The key relationship is:

        z_primary = x_secondary + (z_tip - x_tip)

    This function matches tiles by Y position (preserved across rotation),
    then maps matched secondary X positions to primary Z ranges.

    Args:
        primary_tiles: Tiles from the primary view (where Z-stacks will be taken)
        secondary_tiles: Tiles from the secondary view (defines Z limits)
        fallback_z_min: Fallback minimum Z if no secondary tiles match
        fallback_z_max: Fallback maximum Z if no secondary tiles match
        tip_position: (x_tip, z_tip) from "Tip of sample mount" preset.
            Used to compute the rotation offset. If None, offset is estimated
            from tile data midpoints.

    Returns:
        Dictionary mapping (tile_x_idx, tile_y_idx) to (z_min, z_max)
    """
    # If no secondary tiles, use fallback range for all primary tiles
    if not secondary_tiles:
        logger.info(
            f"No secondary tiles - using fallback Z range [{fallback_z_min:.3f}, {fallback_z_max:.3f}] mm "
            f"for all {len(primary_tiles)} primary tiles"
        )
        return {
            (tile.tile_x_idx, tile.tile_y_idx): (fallback_z_min, fallback_z_max)
            for tile in primary_tiles
        }

    # Calculate FOV from tile spacing (tiles are adjacent with no overlap)
    fov_mm = estimate_fov_from_tiles(primary_tiles, secondary_tiles)
    logger.info(f"Estimated FOV: {fov_mm*1000:.1f} µm for spatial overlap calculation")

    # Compute rotation offset: z_primary = x_secondary + offset
    if tip_position is not None:
        x_tip, z_tip = tip_position
        offset = z_tip - x_tip
        logger.info(
            f"Rotation offset from tip position: {offset:.4f} mm "
            f"(x_tip={x_tip:.4f}, z_tip={z_tip:.4f})"
        )
    else:
        offset = _estimate_rotation_offset(primary_tiles, secondary_tiles)
        logger.info(
            f"Rotation offset estimated from tile data: {offset:.4f} mm "
            f"(no tip position available)"
        )

    # For each primary tile, find secondary tiles at matching Y positions
    # and map their X range to primary Z range
    tile_z_ranges = {}
    fallback_count = 0

    for p_tile in primary_tiles:
        # Find secondary tiles with matching Y (within FOV tolerance)
        matched_secondary_x = []
        for s_tile in secondary_tiles:
            y_distance = abs(p_tile.y - s_tile.y)
            if y_distance < fov_mm:
                matched_secondary_x.append(s_tile.x)

        if matched_secondary_x:
            # Map secondary X range to primary Z range
            x_min = min(matched_secondary_x)
            x_max = max(matched_secondary_x)
            # z_primary = x_secondary + offset, with FOV/2 margin for tile extent
            z_min = x_min + offset - fov_mm / 2
            z_max = x_max + offset + fov_mm / 2
            logger.debug(
                f"Tile ({p_tile.tile_x_idx},{p_tile.tile_y_idx}) at Y={p_tile.y:.3f}: "
                f"{len(matched_secondary_x)} matched secondary tiles, "
                f"secondary X=[{x_min:.3f}, {x_max:.3f}], "
                f"mapped Z=[{z_min:.3f}, {z_max:.3f}] mm"
            )
        else:
            # No Y-matched secondary tiles - use fallback
            z_min = fallback_z_min
            z_max = fallback_z_max
            fallback_count += 1
            logger.warning(
                f"Tile ({p_tile.tile_x_idx},{p_tile.tile_y_idx}) at Y={p_tile.y:.3f}: "
                f"No secondary tiles at matching Y position. "
                f"Using fallback Z range [{z_min:.3f}, {z_max:.3f}] mm"
            )

        # Validate Z range
        if z_max <= z_min:
            logger.error(
                f"Invalid Z range for tile ({p_tile.tile_x_idx},{p_tile.tile_y_idx}): "
                f"[{z_min:.3f}, {z_max:.3f}] - z_max <= z_min!"
            )
        elif abs(z_max - z_min) < 0.001:  # Less than 1 um
            logger.warning(
                f"Very small Z range for tile ({p_tile.tile_x_idx},{p_tile.tile_y_idx}): "
                f"{(z_max-z_min)*1000:.1f} µm"
            )

        tile_z_ranges[(p_tile.tile_x_idx, p_tile.tile_y_idx)] = (z_min, z_max)

    # Log summary
    overlap_count = len(tile_z_ranges) - fallback_count
    logger.info(
        f"Z range calculation complete: {overlap_count} tiles with rotation-mapped ranges, "
        f"{fallback_count} tiles using fallback"
    )

    return tile_z_ranges


def _estimate_rotation_offset(primary_tiles: List, secondary_tiles: List) -> float:
    """Estimate the rotation offset from tile data when tip position is unavailable.

    The offset = z_tip - x_tip can be approximated as:
        offset = midpoint(primary Z values) - midpoint(secondary X values)

    This works because primary Z and secondary X span the same physical extent
    of the sample, just in different coordinate frames due to the 90° rotation.

    Args:
        primary_tiles: Tiles from primary view
        secondary_tiles: Tiles from secondary view

    Returns:
        Estimated offset in mm
    """
    primary_z_values = [t.z for t in primary_tiles]
    secondary_x_values = [t.x for t in secondary_tiles]

    primary_z_mid = (min(primary_z_values) + max(primary_z_values)) / 2
    secondary_x_mid = (min(secondary_x_values) + max(secondary_x_values)) / 2

    offset = primary_z_mid - secondary_x_mid
    logger.debug(
        f"Estimated rotation offset: {offset:.4f} mm "
        f"(primary Z midpoint={primary_z_mid:.4f}, secondary X midpoint={secondary_x_mid:.4f})"
    )
    return offset


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
        if fov < float("inf"):
            logger.debug(f"FOV estimated from primary tiles: {fov*1000:.1f} µm")
            return fov

    # Fallback to secondary tiles if primary insufficient
    if len(secondary_tiles) >= 2:
        fov = min_distance_in_tile_set(secondary_tiles)
        if fov < float("inf"):
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
        return float("inf")

    min_distance = float("inf")

    for i, tile1 in enumerate(tiles):
        for tile2 in tiles[i + 1 :]:
            # Calculate Euclidean distance in XY plane
            dx = tile1.x - tile2.x
            dy = tile1.y - tile2.y
            distance = math.sqrt(dx * dx + dy * dy)

            # Skip zero distances (same tile) and update minimum
            if distance > 1e-6 and distance < min_distance:
                min_distance = distance

    return min_distance
