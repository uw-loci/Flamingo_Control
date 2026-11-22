"""Tile workflow file parsing utilities.

Pure file-parsing functions for extracting metadata from workflow files,
extracted from tile_collection_dialog.py.
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from py2flamingo.utils.workflow_keys import (
    PATH_SECTION,
    SIDES,
    find_value,
    path_recording,
    read_workflow_sections,
)

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
    xy_match = re.search(r"X([-\d.]+)_Y([-\d.]+)", workflow_file.stem)
    if xy_match:
        result = {
            "x": float(xy_match.group(1)),
            "y": float(xy_match.group(2)),
            "filename": workflow_file.name,
        }
        # Also extract rotation if present (R90, R-45, etc.)
        r_match = re.search(r"_R([-\d.]+)_", workflow_file.stem)
        if r_match:
            result["r"] = float(r_match.group(1))
        else:
            result["r"] = 0.0  # Default to 0 if not in filename
        return result
    logger.warning(f"Could not parse position from filename: {workflow_file.name}")
    return None


def _float(value: Optional[str]) -> Optional[float]:
    """A workflow number, or None when it is blank or not a number."""
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def read_xy_position_from_workflow(
    workflow_file: Path,
) -> Optional[Tuple[float, float]]:
    """Read the Start Position X, Y (mm) from a Workflow.txt's content.

    Unlike ``parse_workflow_position`` (which reads X/Y from the *filename*),
    this reads the ``<Start Position>`` block inside the file. That is the only
    place a Single Workflow acquisition records its stage position — its folder
    and workflow file are not named ``X..._Y...``.

    Returns:
        (x_mm, y_mm), or None if the block/fields are absent.
    """
    try:
        start = read_workflow_sections(workflow_file).get("Start Position", {})
        x, y = _float(start.get("X (mm)")), _float(start.get("Y (mm)"))
        if x is not None and y is not None:
            return x, y
    except Exception as e:
        logger.error(f"Failed to read XY position from {workflow_file.name}: {e}")
    return None


def read_z_range_from_workflow(workflow_file: Path) -> Tuple[float, float]:
    """Read Z-stack range from workflow file.

    Args:
        workflow_file: Path to workflow file

    Returns:
        Tuple of (z_min, z_max) in mm: the Start and End Position Z, each 0.0
        when absent; (0.0, 10.0) when the file cannot be read.
    """
    try:
        sections = read_workflow_sections(workflow_file)
    except Exception as e:
        logger.error(f"Failed to read Z range from {workflow_file.name}: {e}")
        return (0.0, 10.0)
    z_min = _float(sections.get("Start Position", {}).get("Z (mm)"))
    z_max = _float(sections.get("End Position", {}).get("Z (mm)"))
    return (z_min or 0.0, z_max or 0.0)


# The key the server writes for an attached laser: "Laser 2 2: 488 nm MLE".
_LASER_KEY = re.compile(r"^Laser\s+(\d+)\s+\d+:\s+\d+\s+nm\s+MLE$")


def read_laser_channels_from_workflow(workflow_file: Path) -> List[int]:
    """Read enabled laser channels from workflow file.

    Parses the <Illumination Source> block to determine which lasers are enabled.
    Format: "Laser N N: XXX nm MLE = power enabled", where enabled is 1 or 0
    (3.0.0) or true or false (servers from 2026-10-06 on).

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
        sections = read_workflow_sections(workflow_file)
        if "Illumination Source" not in sections:
            logger.warning(f"No Illumination Source block in {workflow_file.name}")
            return [0]

        channels = []
        for key, value in sections["Illumination Source"].items():
            m = _LASER_KEY.match(key)
            words = str(value).split()
            if not m or len(words) < 2 or not re.fullmatch(r"[\d.]+", words[0]):
                continue
            if words[1].isdigit() and int(words[1]) == 1:
                # Laser number to channel: Laser 1 -> ch 0, Laser 2 -> ch 1, etc.
                channels.append(int(m.group(1)) - 1)

        if not channels:
            logger.warning(
                f"No enabled lasers found in {workflow_file.name}, defaulting to channel 0"
            )
            return [0]

        logger.info(f"Parsed laser channels from {workflow_file.name}: {channels}")
        return channels

    except Exception as e:
        logger.error(f"Failed to read laser channels from {workflow_file.name}: {e}")
        return [0]


def read_num_planes_from_workflow(workflow_file: Path) -> Optional[int]:
    """Read number of Z-planes per channel from workflow file.

    This is the authoritative plane count set by the ZStack panel:
    num_planes = int(z_range_mm / (z_step_um / 1000)) + 1

    Args:
        workflow_file: Path to workflow file

    Returns:
        Number of planes per channel, or None if not found
    """
    try:
        value = find_value(read_workflow_sections(workflow_file), "Number of planes")
        if value is not None and str(value).strip().isdigit():
            num_planes = int(str(value).strip())
            logger.debug(f"Parsed num_planes from {workflow_file.name}: {num_planes}")
            return num_planes
    except Exception as e:
        logger.error(f"Failed to read num_planes from {workflow_file.name}: {e}")
    return None


def read_save_directory_from_workflow(workflow_file: Path) -> Tuple[str, str]:
    """Read save drive and directory from workflow file.

    Parses "Save image drive = ..." and "Save image directory = ..."
    from the Experiment Settings block.

    Args:
        workflow_file: Path to workflow file

    Returns:
        Tuple of (save_drive, save_directory)
        e.g. ("G:\\", "CTLSM1\\Test_2026-03-06_X4.88_Y17.63")
    """
    try:
        sections = read_workflow_sections(workflow_file)
    except Exception as e:
        logger.error(f"Failed to read save directory from {workflow_file.name}: {e}")
        return ("", "")
    drive = find_value(sections, "Save image drive")
    directory = find_value(sections, "Save image directory")
    return (drive or "", directory or "")


def read_illumination_path_from_workflow(workflow_file: Path) -> Tuple[bool, bool]:
    """Read left/right illumination path from workflow file.

    Reads the <Illumination Path> block in either form the server writes:
    "Left path = ON 1" (3.0.0) or "Left path recording = true" (2026-10-06 on).

    Args:
        workflow_file: Path to workflow file

    Returns:
        Tuple of (left_enabled, right_enabled). (True, False) when the file
        cannot be read or has no Illumination Path block; a side missing from
        the block is False.
    """
    try:
        sections = read_workflow_sections(workflow_file)
    except Exception as e:
        logger.error(f"Failed to read illumination path from {workflow_file.name}: {e}")
        return (True, False)

    if PATH_SECTION not in sections:
        logger.info(
            f"No Illumination Path block in {workflow_file.name}, defaulting to left"
        )
        return (True, False)

    block = sections[PATH_SECTION]
    left_enabled, right_enabled = (
        path_recording(block.get(f"{side} path", "")) for side in SIDES
    )
    logger.info(
        f"Illumination path from {workflow_file.name}: "
        f"left={left_enabled}, right={right_enabled}"
    )
    return (left_enabled, right_enabled)


def read_z_velocity_from_workflow(workflow_file: Path) -> float:
    """Read Z stage velocity from workflow file.

    Args:
        workflow_file: Path to workflow file

    Returns:
        Z velocity in mm/s (default 1.0)
    """
    try:
        velocity = _float(
            find_value(read_workflow_sections(workflow_file), "Z stage velocity (mm/s)")
        )
        if velocity is not None and velocity >= 0:
            logger.debug(
                f"Parsed Z velocity from {workflow_file.name}: {velocity} mm/s"
            )
            return velocity
    except Exception as e:
        logger.error(f"Failed to read Z velocity from {workflow_file.name}: {e}")
    return 1.0
