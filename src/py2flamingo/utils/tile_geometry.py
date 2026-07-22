"""Server-parity tile-grid geometry for Tile workflows.

This mirrors the microscope server's own tile expansion so the client can show
the *true* grid the hardware will image (tile count, positions, volume) and warn
about tiles that fall outside the stage hard limits — before a workflow is sent.

The reference implementation is the server C++
``ControlSystem/Workflow/CheckStackTile.cpp`` (``setStackAddToList``). Key facts
reproduced here verbatim so the numbers agree with the scope:

* Start/End positions are the **centers of the corner tiles**, so the imaged
  region of interest spans half a FOV beyond each: ``roiDelta = |start-end| + FOV``.
* Overlap percent is clamped to ``[0, 50]``.
* Effective step ``FOVOverlap = FOV * (100 - overlap) / 100``.
* Tile count ``tiles = ceil(roiDelta / FOVOverlap)`` (with the axis collapsing to
  a single tile when start == end).
* The grid is re-centered over the ROI (excess coverage split evenly), and tile
  positions step **downward** by ``FOVOverlap`` from the centered start.
* Every tile position is checked against the stage **hard** limits; any outside
  makes the server reject the workflow.

NOTE (from the C++): the camera FOV is computed with an X/Y swap
(``FOVCameraX`` uses image *height*). For a square sensor this is a no-op; pass
``fov_x_mm``/``fov_y_mm`` already resolved from the hardware config.

Pure and dependency-free (stdlib ``math`` only) so it is unit-testable against
the C++ formula without a running Qt app or hardware.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# Server tiling overlap clamp (PlatformIO/.../SystemLimits.h: TilingLimits).
OVERLAP_PERCENT_MIN = 0.0
OVERLAP_PERCENT_MAX = 50.0


@dataclass
class TileLimitViolation:
    """One tile position that falls outside a stage hard limit."""

    axis: str  # "x" or "y"
    index_x: int  # tile column (row index for a pure-Y violation)
    index_y: int  # tile row
    position_mm: float  # the offending stage position
    limit_mm: float  # the limit it crossed
    kind: str  # "min" or "max"

    def describe(self) -> str:
        cmp = "<" if self.kind == "min" else ">"
        return (
            f"tile ({self.index_x}, {self.index_y}): {self.axis.upper()}="
            f"{self.position_mm:.3f} mm {cmp} hard limit "
            f"{self.kind} {self.limit_mm:.3f} mm"
        )


@dataclass
class TileGeometry:
    """The grid the server will produce for a Tile workflow."""

    tiles_x: int
    tiles_y: int
    fov_x_mm: float
    fov_y_mm: float
    x_overlap_percent: float  # clamped value actually used
    y_overlap_percent: float
    step_x_mm: float  # FOVOverlap X (effective center-to-center pitch)
    step_y_mm: float  # FOVOverlap Y
    roi_delta_x_mm: float  # |start-end| + FOV
    roi_delta_y_mm: float
    tile_region_x_mm: float  # tiles_x * step_x
    tile_region_y_mm: float
    delta_z_mm: float
    positions: List[Tuple[float, float]] = field(default_factory=list)  # (x, y)
    violations: List[TileLimitViolation] = field(default_factory=list)

    @property
    def total_tiles(self) -> int:
        return self.tiles_x * self.tiles_y

    @property
    def volume_mm3(self) -> float:
        return self.tile_region_x_mm * self.tile_region_y_mm * self.delta_z_mm

    @property
    def has_limit_errors(self) -> bool:
        return bool(self.violations)


def client_tile_count_1d(range_mm: float, fov_mm: float, overlap_percent: float) -> int:
    """The client's historical 1-D tile count (``tiling_panel.set_from_positions``).

    ``floor(range / (FOV * (1 - overlap))) + 1`` — differs from the server, which
    uses ``ceil((range + FOV) / (FOV * (1 - overlap)))`` (see
    :func:`compute_tile_geometry`). Exposed so a "Check Tiling" diagnostic can
    show both counts and surface the inputs where they disagree.
    """
    step = fov_mm * (1.0 - overlap_percent / 100.0)
    if step <= 0:
        return 1
    return max(1, int(range_mm / step) + 1)


def _clamp_overlap(value: float) -> float:
    if value < OVERLAP_PERCENT_MIN:
        return OVERLAP_PERCENT_MIN
    if value > OVERLAP_PERCENT_MAX:
        return OVERLAP_PERCENT_MAX
    return value


def compute_tile_geometry(
    start_x: float,
    end_x: float,
    start_y: float,
    end_y: float,
    start_z: float,
    end_z: float,
    fov_x_mm: float,
    fov_y_mm: float,
    x_overlap_percent: float,
    y_overlap_percent: float,
    *,
    hard_limit_min_x: Optional[float] = None,
    hard_limit_max_x: Optional[float] = None,
    hard_limit_min_y: Optional[float] = None,
    hard_limit_max_y: Optional[float] = None,
) -> TileGeometry:
    """Compute the server's tile grid for a Tile workflow.

    Args mirror ``CheckStackTile::setStackAddToList``. Start/End are tile
    *centers* (mm). ``fov_*_mm`` is the sample-plane field of view. Overlap is a
    percentage (clamped to [0, 50]). Hard limits are optional; when given, every
    tile position is checked and out-of-range tiles are recorded in
    ``violations`` (mirroring the server's per-tile hard-limit rejection).
    """
    fov_x_half = fov_x_mm / 2.0
    fov_y_half = fov_y_mm / 2.0

    # ROI runs half a FOV beyond each corner-tile center (C++ :129-156).
    if end_x < start_x:
        roi_start_x = start_x + fov_x_half
        roi_end_x = end_x - fov_x_half
    else:
        roi_start_x = end_x + fov_x_half
        roi_end_x = start_x - fov_x_half

    if end_y < start_y:
        roi_start_y = start_y + fov_y_half
        roi_end_y = end_y - fov_y_half
    else:
        roi_start_y = end_y + fov_y_half
        roi_end_y = start_y - fov_y_half

    roi_delta_x = roi_start_x - roi_end_x  # = |start-end| + FOV
    roi_delta_y = roi_start_y - roi_end_y

    x_overlap = _clamp_overlap(x_overlap_percent)
    y_overlap = _clamp_overlap(y_overlap_percent)

    fov_x_overlap = fov_x_mm * (100.0 - x_overlap) / 100.0
    fov_y_overlap = fov_y_mm * (100.0 - y_overlap) / 100.0

    tiles_x_f = roi_delta_x / fov_x_overlap if fov_x_overlap else 1.0
    tiles_y_f = roi_delta_y / fov_y_overlap if fov_y_overlap else 1.0

    # No change along an axis -> exactly one tile, full FOV pitch (C++ :180-191).
    if start_x == end_x:
        tiles_x_f = 1.0
        fov_x_overlap = fov_x_mm
    if start_y == end_y:
        tiles_y_f = 1.0
        fov_y_overlap = fov_y_mm

    tiles_x = int(math.ceil(tiles_x_f))
    tiles_y = int(math.ceil(tiles_y_f))
    tiles_x = max(1, tiles_x)
    tiles_y = max(1, tiles_y)

    tile_x_distance = tiles_x * fov_x_overlap
    tile_y_distance = tiles_y * fov_y_overlap
    delta_z = abs(start_z - end_z)

    # Re-center the (ceil-rounded) grid over the ROI (C++ :221-231).
    x_offset = (tile_x_distance - roi_delta_x) / 2.0
    y_offset = (tile_y_distance - roi_delta_y) / 2.0
    stack_start_x = (roi_start_x + x_offset) - fov_x_half
    stack_start_y = (roi_start_y + y_offset) - fov_y_half

    positions: List[Tuple[float, float]] = []
    violations: List[TileLimitViolation] = []

    stack_pos_y = stack_start_y
    for index_y in range(tiles_y):
        if hard_limit_min_y is not None and stack_pos_y < hard_limit_min_y:
            violations.append(
                TileLimitViolation(
                    "y", -1, index_y, stack_pos_y, hard_limit_min_y, "min"
                )
            )
        elif hard_limit_max_y is not None and hard_limit_max_y < stack_pos_y:
            violations.append(
                TileLimitViolation(
                    "y", -1, index_y, stack_pos_y, hard_limit_max_y, "max"
                )
            )

        stack_pos_x = stack_start_x
        for index_x in range(tiles_x):
            if hard_limit_min_x is not None and stack_pos_x < hard_limit_min_x:
                violations.append(
                    TileLimitViolation(
                        "x", index_x, index_y, stack_pos_x, hard_limit_min_x, "min"
                    )
                )
            elif hard_limit_max_x is not None and hard_limit_max_x < stack_pos_x:
                violations.append(
                    TileLimitViolation(
                        "x", index_x, index_y, stack_pos_x, hard_limit_max_x, "max"
                    )
                )

            positions.append((stack_pos_x, stack_pos_y))
            stack_pos_x -= fov_x_overlap
        stack_pos_y -= fov_y_overlap

    return TileGeometry(
        tiles_x=tiles_x,
        tiles_y=tiles_y,
        fov_x_mm=fov_x_mm,
        fov_y_mm=fov_y_mm,
        x_overlap_percent=x_overlap,
        y_overlap_percent=y_overlap,
        step_x_mm=fov_x_overlap,
        step_y_mm=fov_y_overlap,
        roi_delta_x_mm=roi_delta_x,
        roi_delta_y_mm=roi_delta_y,
        tile_region_x_mm=tile_x_distance,
        tile_region_y_mm=tile_y_distance,
        delta_z_mm=delta_z,
        positions=positions,
        violations=violations,
    )
