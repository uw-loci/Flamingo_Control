"""Tile workflow file parsing utilities.

Pure file-parsing functions for extracting metadata from workflow files,
extracted from tile_collection_dialog.py.
"""

import logging
import re
from pathlib import Path
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)


def parse_workflow_position(workflow_file: Path) -> Optional[Dict]:
    """Extract R, X, Y position from workflow filename.

    Filename format: tile_collection_R90_X10.50_Y20.75.txt

    Args:
        workflow_file: Path to workflow file

    Returns:
        Dictionary with 'x', 'y', 'r', 'filename' or None if parse fails
    """
    # Match X and Y (required)
    xy_match = re.search(r'X([-\d.]+)_Y([-\d.]+)', workflow_file.stem)
    if xy_match:
        result = {
            'x': float(xy_match.group(1)),
            'y': float(xy_match.group(2)),
            'filename': workflow_file.name
        }
        # Also extract rotation if present (R90, R-45, etc.)
        r_match = re.search(r'_R([-\d.]+)_', workflow_file.stem)
        if r_match:
            result['r'] = float(r_match.group(1))
        else:
            result['r'] = 0.0  # Default to 0 if not in filename
        return result
    logger.warning(f"Could not parse position from filename: {workflow_file.name}")
    return None


def read_z_range_from_workflow(workflow_file: Path) -> Tuple[float, float]:
    """Read Z-stack range from workflow file.

    Args:
        workflow_file: Path to workflow file

    Returns:
        Tuple of (z_min, z_max) in mm
    """
    try:
        with open(workflow_file, 'r') as f:
            content = f.read()

        # Parse Start Position Z
        z_min = 0.0
        z_max = 0.0

        start_match = re.search(r'<Start Position>.*?Z \(mm\) = ([-\d.]+)', content, re.DOTALL)
        if start_match:
            z_min = float(start_match.group(1))

        end_match = re.search(r'<End Position>.*?Z \(mm\) = ([-\d.]+)', content, re.DOTALL)
        if end_match:
            z_max = float(end_match.group(1))

        return (z_min, z_max)

    except Exception as e:
        logger.error(f"Failed to read Z range from {workflow_file.name}: {e}")
        return (0.0, 10.0)


def read_laser_channels_from_workflow(workflow_file: Path) -> List[int]:
    """Read enabled laser channels from workflow file.

    Parses the <Illumination Source> block to determine which lasers are enabled.
    Format: "Laser N N: XXX nm MLE = power enabled"
    where enabled is 1 (on) or 0 (off).

    Laser mapping:
        Laser 1 (405nm) -> channel 0
        Laser 2 (488nm) -> channel 1
        Laser 3 (561nm) -> channel 2
        Laser 4 (640nm) -> channel 3

    Args:
        workflow_file: Path to workflow file

    Returns:
        Ordered list of enabled channel IDs, e.g. [1, 3] for 488nm + 640nm.
        Falls back to [0] if parsing fails.
    """
    try:
        with open(workflow_file, 'r') as f:
            content = f.read()

        # Extract Illumination Source block
        illum_match = re.search(
            r'<Illumination Source>(.*?)</Illumination Source>',
            content, re.DOTALL
        )
        if not illum_match:
            logger.warning(f"No Illumination Source block in {workflow_file.name}")
            return [0]

        illum_block = illum_match.group(1)
        channels = []

        # Match lines like: "Laser 2 2: 488 nm MLE = 10.00 1"
        # The last number (1 or 0) indicates enabled/disabled
        for match in re.finditer(
            r'Laser\s+(\d+)\s+\d+:\s+\d+\s+nm\s+MLE\s*=\s*[\d.]+\s+(\d+)',
            illum_block
        ):
            laser_num = int(match.group(1))
            enabled = int(match.group(2))
            if enabled == 1:
                # Laser number to channel: Laser 1 -> ch 0, Laser 2 -> ch 1, etc.
                channels.append(laser_num - 1)

        if not channels:
            logger.warning(f"No enabled lasers found in {workflow_file.name}, defaulting to channel 0")
            return [0]

        logger.info(f"Parsed laser channels from {workflow_file.name}: {channels}")
        return channels

    except Exception as e:
        logger.error(f"Failed to read laser channels from {workflow_file.name}: {e}")
        return [0]


def read_z_velocity_from_workflow(workflow_file: Path) -> float:
    """Read Z stage velocity from workflow file.

    Args:
        workflow_file: Path to workflow file

    Returns:
        Z velocity in mm/s (default 1.0)
    """
    try:
        with open(workflow_file, 'r') as f:
            content = f.read()
        match = re.search(r'Z stage velocity \(mm/s\)\s*=\s*([\d.]+)', content)
        if match:
            velocity = float(match.group(1))
            logger.debug(f"Parsed Z velocity from {workflow_file.name}: {velocity} mm/s")
            return velocity
    except Exception as e:
        logger.error(f"Failed to read Z velocity from {workflow_file.name}: {e}")
    return 1.0
