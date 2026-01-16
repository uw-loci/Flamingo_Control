# ============================================================================
# src/py2flamingo/services/tiff_size_validator.py
"""
TIFF file size validation service.

Checks if workflow parameters will produce TIFF files that exceed
the standard TIFF 4GB file size limit.
"""

import logging
from typing import Tuple, Optional, NamedTuple
from dataclasses import dataclass
from pathlib import Path
import re


# Standard TIFF has 32-bit offsets, limiting file size to 4GB
TIFF_4GB_LIMIT = 4 * 1024 * 1024 * 1024  # 4,294,967,296 bytes

# Safe margin to account for TIFF headers, metadata, etc.
TIFF_SAFE_LIMIT = int(TIFF_4GB_LIMIT * 0.95)  # ~4.08 GB with 5% margin

logger = logging.getLogger(__name__)


@dataclass
class TiffSizeEstimate:
    """Result of TIFF file size estimation."""
    num_planes: int
    image_width: int
    image_height: int
    bytes_per_pixel: int
    estimated_bytes: int
    exceeds_limit: bool
    max_safe_planes: int
    message: str

    @property
    def estimated_gb(self) -> float:
        """Get estimated size in GB."""
        return self.estimated_bytes / (1024 * 1024 * 1024)

    @property
    def limit_gb(self) -> float:
        """Get limit in GB."""
        return TIFF_4GB_LIMIT / (1024 * 1024 * 1024)


def calculate_tiff_size(
    num_planes: int,
    image_width: int = 2048,
    image_height: int = 2048,
    bytes_per_pixel: int = 2
) -> TiffSizeEstimate:
    """
    Calculate expected TIFF file size and check against 4GB limit.

    Args:
        num_planes: Number of Z planes in the stack
        image_width: Image width in pixels (default: 2048)
        image_height: Image height in pixels (default: 2048)
        bytes_per_pixel: Bytes per pixel (default: 2 for 16-bit)

    Returns:
        TiffSizeEstimate with size calculation and warning info
    """
    # Calculate image size
    image_bytes = image_width * image_height * bytes_per_pixel

    # Estimate total size (add ~1% for TIFF headers/metadata)
    header_overhead = 1.01
    estimated_bytes = int(num_planes * image_bytes * header_overhead)

    # Check against limit
    exceeds_limit = estimated_bytes > TIFF_SAFE_LIMIT

    # Calculate maximum safe number of planes
    max_safe_planes = int(TIFF_SAFE_LIMIT / (image_bytes * header_overhead))

    # Build message
    if exceeds_limit:
        message = (
            f"WARNING: Estimated TIFF size ({estimated_bytes / 1e9:.2f} GB) "
            f"exceeds the 4GB limit!\n\n"
            f"Parameters:\n"
            f"  - {num_planes} planes\n"
            f"  - {image_width}x{image_height} pixels\n"
            f"  - {bytes_per_pixel * 8}-bit depth\n\n"
            f"Maximum safe planes for this image size: {max_safe_planes}\n\n"
            f"The acquisition will fail after ~{max_safe_planes} planes "
            f"due to the standard TIFF 4GB file size limit."
        )
    else:
        message = (
            f"TIFF size OK: {estimated_bytes / 1e9:.2f} GB "
            f"(limit: {TIFF_4GB_LIMIT / 1e9:.2f} GB)"
        )

    return TiffSizeEstimate(
        num_planes=num_planes,
        image_width=image_width,
        image_height=image_height,
        bytes_per_pixel=bytes_per_pixel,
        estimated_bytes=estimated_bytes,
        exceeds_limit=exceeds_limit,
        max_safe_planes=max_safe_planes,
        message=message
    )


def validate_workflow_params(
    z_range_mm: float,
    z_step_um: float,
    image_width: int = 2048,
    image_height: int = 2048,
    bytes_per_pixel: int = 2
) -> TiffSizeEstimate:
    """
    Validate workflow parameters before execution.

    Args:
        z_range_mm: Total Z range in millimeters
        z_step_um: Z step size in micrometers
        image_width: Image width in pixels
        image_height: Image height in pixels
        bytes_per_pixel: Bytes per pixel (2 for 16-bit)

    Returns:
        TiffSizeEstimate with validation result
    """
    # Calculate number of planes
    z_step_mm = z_step_um / 1000.0
    if z_step_mm <= 0:
        z_step_mm = 0.001  # Minimum 1um step

    num_planes = max(1, int(z_range_mm / z_step_mm) + 1)

    return calculate_tiff_size(
        num_planes=num_planes,
        image_width=image_width,
        image_height=image_height,
        bytes_per_pixel=bytes_per_pixel
    )


def parse_workflow_file(workflow_path: Path) -> Optional[TiffSizeEstimate]:
    """
    Parse a workflow file and validate its TIFF size.

    Args:
        workflow_path: Path to workflow file

    Returns:
        TiffSizeEstimate if parseable, None otherwise
    """
    try:
        content = workflow_path.read_text()

        # Parse number of planes
        num_planes_match = re.search(r'Number of planes\s*=\s*(\d+)', content)
        num_planes = int(num_planes_match.group(1)) if num_planes_match else 1

        # Parse AOI dimensions
        aoi_width_match = re.search(r'AOI width\s*=\s*(\d+)', content)
        aoi_height_match = re.search(r'AOI height\s*=\s*(\d+)', content)

        image_width = int(aoi_width_match.group(1)) if aoi_width_match else 2048
        image_height = int(aoi_height_match.group(1)) if aoi_height_match else 2048

        # Assume 16-bit (2 bytes per pixel)
        bytes_per_pixel = 2

        return calculate_tiff_size(
            num_planes=num_planes,
            image_width=image_width,
            image_height=image_height,
            bytes_per_pixel=bytes_per_pixel
        )

    except Exception as e:
        logger.error(f"Failed to parse workflow file {workflow_path}: {e}")
        return None


def get_recommended_planes(
    z_range_mm: float,
    image_width: int = 2048,
    image_height: int = 2048,
    bytes_per_pixel: int = 2
) -> Tuple[int, float]:
    """
    Get recommended number of planes and step size to stay under 4GB limit.

    Args:
        z_range_mm: Desired Z range in millimeters
        image_width: Image width in pixels
        image_height: Image height in pixels
        bytes_per_pixel: Bytes per pixel

    Returns:
        Tuple of (max_planes, min_step_um)
    """
    # Calculate image size
    image_bytes = image_width * image_height * bytes_per_pixel

    # Calculate max planes (with safety margin)
    max_planes = int(TIFF_SAFE_LIMIT / (image_bytes * 1.01))

    # Calculate minimum step size to cover the range
    if max_planes > 1:
        min_step_mm = z_range_mm / (max_planes - 1)
        min_step_um = min_step_mm * 1000
    else:
        min_step_um = z_range_mm * 1000  # Single plane

    return max_planes, min_step_um
