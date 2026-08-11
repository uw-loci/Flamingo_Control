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
from typing import List, Optional, Sequence, Tuple

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


@dataclass
class PlannedTile:
    """One acquisition tile, with the Z depth inherited from the overview."""

    x_mm: float
    y_mm: float
    z_min_mm: float
    z_max_mm: float
    #: How many overview tiles with a recorded Z depth this tile overlaps.
    #: 0 means its Z range is a fallback, not a measurement.
    source_tiles: int = 0
    #: How many overview tiles this tile overlaps at all, Z recorded or not.
    #: 0 means it covers none of the selected region.
    covers_tiles: int = 0

    @property
    def z_from_overview(self) -> bool:
        return self.source_tiles > 0


def _tile_xy(tile) -> Tuple[float, float]:
    """(x, y) from a (x, y) tuple or anything with .x/.y."""
    if isinstance(tile, (tuple, list)):
        return (float(tile[0]), float(tile[1]))
    return (float(tile.x), float(tile.y))


def _tile_z(tile) -> Optional[Tuple[float, float]]:
    """(z_min, z_max) from a tile that carries one, else None."""
    lo = getattr(tile, "z_stack_min", None)
    hi = getattr(tile, "z_stack_max", None)
    if lo is None or hi is None:
        return None
    try:
        lo, hi = float(lo), float(hi)
    except (TypeError, ValueError):
        return None
    if hi < lo:
        lo, hi = hi, lo
    return (lo, hi) if hi > lo else None


#: Overlap below this is float noise, not a shared field. Tile positions are
#: reached by repeated subtraction of the step, so two footprints that meet
#: exactly land a few ulp either side of touching — and a strict inequality
#: then calls half of them overlapping. 1 nm is far above that accumulation and
#: far below any overlap worth collecting a tile for.
_TOUCHING_TOLERANCE_MM = 1e-6


def _footprints_overlap(
    x_mm: float,
    y_mm: float,
    ox_mm: float,
    oy_mm: float,
    half_x: float,
    half_y: float,
) -> bool:
    """Do two tile footprints share area, rather than merely touch?"""
    return (
        half_x - abs(ox_mm - x_mm) > _TOUCHING_TOLERANCE_MM
        and half_y - abs(oy_mm - y_mm) > _TOUCHING_TOLERANCE_MM
    )


def overlapping_overview_tiles(
    x_mm: float,
    y_mm: float,
    acq_fov_x_mm: float,
    acq_fov_y_mm: float,
    overview_tiles: Sequence,
    overview_fov_x_mm: float,
    overview_fov_y_mm: float,
) -> int:
    """How many overview tile footprints this acquisition tile overlaps.

    Purely geometric, and deliberately separate from :func:`inherit_z_range`:
    an overview tile with no recorded Z still *covers* ground. Coverage decides
    whether a tile is worth collecting; the Z range decides how deep.
    """
    half_x = (float(acq_fov_x_mm) + float(overview_fov_x_mm)) / 2.0
    half_y = (float(acq_fov_y_mm) + float(overview_fov_y_mm)) / 2.0
    n = 0
    for tile in overview_tiles:
        ox, oy = _tile_xy(tile)
        if _footprints_overlap(x_mm, y_mm, ox, oy, half_x, half_y):
            n += 1
    return n


def inherit_z_range(
    x_mm: float,
    y_mm: float,
    acq_fov_x_mm: float,
    acq_fov_y_mm: float,
    overview_tiles: Sequence,
    overview_fov_x_mm: float,
    overview_fov_y_mm: float,
) -> Tuple[Optional[float], Optional[float], int]:
    """Z range for one acquisition tile, from the overview tiles it covers.

    Returns ``(z_min, z_max, n_sources)``; ``(None, None, 0)`` when no overview
    tile's footprint overlaps this one.

    The UNION of the overlapping tiles' ranges, deliberately. Collect Tiles
    gives every overview tile its own depth, so an acquisition tile straddling
    two of them has to span both — taking one, or an average, would cut the
    stack short exactly where two depths disagree, which is where the sample is
    changing. Erring deep costs acquisition time; erring shallow loses data that
    cannot be recovered without re-running the sample.
    """
    half_x = (float(acq_fov_x_mm) + float(overview_fov_x_mm)) / 2.0
    half_y = (float(acq_fov_y_mm) + float(overview_fov_y_mm)) / 2.0
    lo = hi = None
    n = 0
    for tile in overview_tiles:
        span = _tile_z(tile)
        if span is None:
            continue
        ox, oy = _tile_xy(tile)
        if not _footprints_overlap(x_mm, y_mm, ox, oy, half_x, half_y):
            continue
        n += 1
        lo = span[0] if lo is None else min(lo, span[0])
        hi = span[1] if hi is None else max(hi, span[1])
    return (lo, hi, n)


@dataclass
class AcquisitionPlan:
    """An acquisition grid derived from an overview, and how it was derived.

    Keeps the two grids' numbers side by side because they are different grids
    and confusing them is the failure this type exists to prevent.
    """

    region_x_mm: Tuple[float, float]  # stage span the overview selection covers
    region_y_mm: Tuple[float, float]
    overview_tiles: int
    overview_fov_x_mm: float
    overview_fov_y_mm: float
    geometry: "TileGeometry"  # the ACQUISITION grid
    tiles: List[PlannedTile] = field(default_factory=list)

    @property
    def acquisition_tiles(self) -> int:
        """Tiles that will actually be collected.

        Not ``geometry.total_tiles``: with ``drop_uncovered`` the grid's own
        rectangle is only a starting point, and the tiles falling outside the
        selection are removed from it.
        """
        return len(self.tiles)

    @property
    def grid_tiles(self) -> int:
        """Tiles in the full rectangle before any were dropped."""
        return self.geometry.total_tiles

    @property
    def dropped_tiles(self) -> int:
        return self.grid_tiles - len(self.tiles)

    @property
    def tiles_without_overview_z(self) -> List[PlannedTile]:
        """Tiles no overview tile covered, so their Z range is a fallback.

        Worth surfacing rather than counting: their depth was not measured, and
        the Z edges are what the laser acquisition sweeps.
        """
        return [t for t in self.tiles if not t.z_from_overview]

    def describe(self) -> str:
        g = self.geometry
        dropped = (
            f" less {self.dropped_tiles} outside the selection"
            if self.dropped_tiles
            else ""
        )
        return (
            f"{self.overview_tiles} overview tile(s) at "
            f"{self.overview_fov_x_mm:.4f}x{self.overview_fov_y_mm:.4f} mm cover "
            f"X {self.region_x_mm[0]:.3f}..{self.region_x_mm[1]:.3f}, "
            f"Y {self.region_y_mm[0]:.3f}..{self.region_y_mm[1]:.3f} mm -> "
            f"{g.tiles_x}x{g.tiles_y} = {self.acquisition_tiles} acquisition "
            f"tile(s){dropped} at "
            f"{g.fov_x_mm:.4f}x{g.fov_y_mm:.4f} mm, "
            f"{g.x_overlap_percent:.1f}/{g.y_overlap_percent:.1f}% overlap "
            f"(step {g.step_x_mm:.4f}/{g.step_y_mm:.4f} mm)"
        )


def selection_region_mm(
    centres_mm: Sequence[float], fov_mm: float
) -> Tuple[float, float]:
    """Stage span covered by tiles at `centres_mm`, each `fov_mm` wide.

    Tile positions are CENTRES (the server's own convention — Position A/B are
    corner-tile centres), so the imaged region runs half a field beyond the
    outermost centre at each end. Getting this wrong shrinks the acquisition
    region by one field, which quietly clips the edge of the sample.
    """
    if not centres_mm:
        return (0.0, 0.0)
    half = float(fov_mm) / 2.0
    return (min(centres_mm) - half, max(centres_mm) + half)


def plan_acquisition_from_overview(
    overview_centres: Sequence[Tuple[float, float]],
    *,
    overview_fov_x_mm: float,
    overview_fov_y_mm: float,
    acquisition_fov_x_mm: float,
    acquisition_fov_y_mm: float,
    overlap_percent: float,
    z_min_mm: float = 0.0,
    z_max_mm: float = 0.0,
    stage_limits: Optional[dict] = None,
    drop_uncovered: bool = False,
) -> AcquisitionPlan:
    """Tile the region an overview selection covers, using the ACQUISITION field.

    The overview and the acquisition do not see the same field of view. LED
    transmission fills the whole sensor; the light sheet does not fill it
    vertically, so the acquisition field is smaller and generally NOT square.
    Re-imaging at the overview's tile centres therefore leaves gaps wherever the
    acquisition field is smaller than the overview's — the tiles were spaced for
    a field the laser cannot illuminate.

    So the overview's tile centres are used only to bound a REGION, and the
    acquisition grid is generated fresh over that region from its own field and
    its own overlap. This is also the coupling that let a requested 20% overlap
    reach the stage as 0.25%: the overview grid was a one-way door into
    acquisition, and nothing re-derived the spacing.

    ``stage_limits`` is the ``{"x": {"min":…, "max":…}, "y": {…}}`` shape used
    by ``{name}_settings.json``; out-of-range tiles come back in
    ``geometry.violations`` rather than being silently dropped.

    ``drop_uncovered`` removes tiles that overlap no overview tile at all. A
    selection is rarely a rectangle — a sample in a tube, or an L-shaped one,
    gets picked out tile by tile — but the grid generated over its bounding
    region always is. Without this, re-tiling would silently collect the holes
    the user deliberately left out, at full acquisition cost and with no
    measured depth to collect them at. Tiles that overlap the selection even
    partially are kept: covering the selected region needs every tile that
    intersects it.
    """
    xy = [_tile_xy(t) for t in overview_centres]
    xs = [x for x, _y in xy]
    ys = [y for _x, y in xy]
    region_x = selection_region_mm(xs, overview_fov_x_mm)
    region_y = selection_region_mm(ys, overview_fov_y_mm)

    # compute_tile_geometry takes corner-tile CENTRES, and it extends the ROI by
    # half a field beyond each. The region above is already an edge-to-edge
    # span, so hand it back the centres that reproduce it.
    half_x = float(acquisition_fov_x_mm) / 2.0
    half_y = float(acquisition_fov_y_mm) / 2.0
    start_x, end_x = region_x[0] + half_x, region_x[1] - half_x
    start_y, end_y = region_y[0] + half_y, region_y[1] - half_y
    # A region narrower than one acquisition field collapses to a single tile at
    # its centre rather than an inverted span.
    if end_x < start_x:
        start_x = end_x = (region_x[0] + region_x[1]) / 2.0
    if end_y < start_y:
        start_y = end_y = (region_y[0] + region_y[1]) / 2.0

    limits = stage_limits or {}

    def _limit(axis: str, key: str):
        try:
            return float(limits[axis][key])
        except (KeyError, TypeError, ValueError):
            return None

    geometry = compute_tile_geometry(
        start_x,
        end_x,
        start_y,
        end_y,
        z_min_mm,
        z_max_mm,
        acquisition_fov_x_mm,
        acquisition_fov_y_mm,
        overlap_percent,
        overlap_percent,
        hard_limit_min_x=_limit("x", "min"),
        hard_limit_max_x=_limit("x", "max"),
        hard_limit_min_y=_limit("y", "min"),
        hard_limit_max_y=_limit("y", "max"),
    )
    # Carry each overview tile's measured Z depth onto the acquisition tiles
    # that cover it. Without this, changing the AOI silently discards the
    # per-tile depths — which is the entire product of Collect Tiles, and what
    # the laser acquisition sweeps.
    fallback = (float(z_min_mm), float(z_max_mm))
    planned = []
    for x, y in geometry.positions:
        covers = overlapping_overview_tiles(
            x,
            y,
            geometry.fov_x_mm,
            geometry.fov_y_mm,
            overview_centres,
            overview_fov_x_mm,
            overview_fov_y_mm,
        )
        if drop_uncovered and covers == 0:
            continue
        lo, hi, n = inherit_z_range(
            x,
            y,
            geometry.fov_x_mm,
            geometry.fov_y_mm,
            overview_centres,
            overview_fov_x_mm,
            overview_fov_y_mm,
        )
        if lo is None:
            lo, hi = fallback
        planned.append(
            PlannedTile(
                x_mm=x,
                y_mm=y,
                z_min_mm=lo,
                z_max_mm=hi,
                source_tiles=n,
                covers_tiles=covers,
            )
        )

    return AcquisitionPlan(
        region_x_mm=region_x,
        region_y_mm=region_y,
        overview_tiles=len(overview_centres),
        overview_fov_x_mm=float(overview_fov_x_mm),
        overview_fov_y_mm=float(overview_fov_y_mm),
        geometry=geometry,
        tiles=planned,
    )


def client_tile_count_1d(range_mm: float, fov_mm: float, overlap_percent: float) -> int:
    """The client's historical 1-D tile count. **Superseded — do not use.**

    ``floor(range / (FOV * (1 - overlap))) + 1``. This is what
    ``tiling_panel.set_from_positions`` used until it was switched to
    :func:`compute_tile_geometry`, and it under-counts: it treats the corners as
    the edges of the imaged region rather than as corner-tile *centers*, and it
    rounds down. For a 6 x 12 mm region at 2.1454 mm FOV and 10% overlap it says
    4x7=28 while the scope images 5x8=40 — a third of the run unaccounted for in
    the size and time estimates.

    Kept only so the divergence stays documented and testable; nothing in the
    application calls it.
    """
    step = fov_mm * (1.0 - overlap_percent / 100.0)
    if step <= 0:
        return 1
    return max(1, int(range_mm / step) + 1)


def tile_positions_1d(lo: float, hi: float, step: float) -> List[float]:
    """Every position a scan will visit along one axis. THE definition.

    Counting tiles is part of the geometry, not a separate estimate. This one
    function is walked by the LED overview's position generator, by its fast
    (continuous) scan path, and by the dialog's tile-count preview, because
    each of those once had its own arithmetic and they disagreed in production:
    for one 2026-08-09 bounding box the preview said 13 rows, the generator
    laid down 14, and the scan that moved the stage did 12.

    ``int(range / step) + 1`` is NOT equivalent — it drops the final row
    whenever the range does not divide evenly, which is most of the time.
    """
    if step <= 0:
        return [lo]
    positions: List[float] = []
    x = lo
    while x <= hi + step / 2:
        positions.append(x)
        x += step
    return positions or [lo]


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
