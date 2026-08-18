"""Narrow each LED overview tile's Z sweep to where the sample actually is.

Z travel is **61% of a tile** — 10.0 s of 16.28 s, measured over 140 tiles on
2026-08-17 — because every tile traverses the whole bounding-box depth at the
stage's 1 mm/s. Reducing the plane COUNT does not touch that: the subsample
still spans the full range. Reducing the RANGE is the only thing that does.

Most of that range is empty. A bounding box is drawn around the whole sample,
but any one tile sees a slice of it, and outside that slice the stage is
travelling through nothing. Where a tile's neighbours found their sample is a
good prediction of where this tile's sample will be, so the sweep can start
narrow and widen only when it has to.

**This module never decides to lose data.** When a narrowed sweep finds content
running to the edge of its band, the true extent is unknown — and the Z edges
are exactly what the laser acquisition sweeps afterwards, so an underestimate
there is not a slower overview, it is a truncated acquisition. Those tiles are
re-swept over the full range instead. :func:`should_resweep` is the whole safety
argument, and it is why this can be turned on without knowing the sample shape.

The failure mode it cannot rule out is a sample this heuristic simply does not
fit — a tube, a sparse or discontinuous specimen, anything where neighbouring
tiles say nothing useful about each other. :func:`adaptation_is_not_paying`
detects that from the re-sweep rate and gives up, so the worst case is bounded
near the non-adaptive cost rather than well above it.

**Measured on synthetic samples**, 10x14 tiles over the real 10 mm box
(``tests/test_led_overview_adaptive_z_scan.py`` drives the scan loop for these):

===========================  ===============  ===================
sample shape                 Z travel saved   outcome
===========================  ===============  ===================
flat slab, 1 mm thick               83%       no re-sweeps
flat slab, 4 mm thick               50%       no re-sweeps
sample jumps 4 mm mid-scan          77%       2 re-sweeps
dome / curved surface               60%       5 re-sweeps
gentle tilt, 0.15 mm/tile           49%       40 re-sweeps
sample fills 2/3 of the box         27%       17 re-sweeps
steep tilt, 0.5 mm/tile             ~0%       gives up
sample much smaller than box        ~0%       gives up
scattered / discontinuous           ~0%       gives up
===========================  ===============  ===================

The pattern: it pays when the sample roughly fills its bounding box and its
depth varies smoothly, and it declines to try otherwise. A box drawn far larger
than the sample cannot be helped here -- every empty tile has to sweep the full
range to prove it is empty -- and is better fixed by drawing a tighter box.

Pure and dependency-free, so the policy can be tested without a stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

#: Fraction of a tile's own peak focus score above which a plane counts as
#: containing sample. Relative, not absolute: an absolute floor would be a
#: magic number tied to one camera, one exposure and one specimen. Variance of
#: Laplacian on textureless background is near zero and on sample is orders of
#: magnitude higher, so the exact fraction matters much less than the ratio.
DEFAULT_CONTENT_THRESHOLD = 0.25

#: A tile with no real structure has a peak that is noise. Judging its content
#: against its OWN peak would mark every plane as sample and report a full-depth
#: extent, which then widens every neighbour's band. Compared against the
#: brightest tile the scan has seen instead, so the scan calibrates itself.
DEFAULT_BACKGROUND_FRACTION = 0.05

#: Extra depth added to each side of the predicted band. The sample moves
#: between tiles; this is the room it is allowed to move without triggering a
#: re-sweep.
DEFAULT_MARGIN_MM = 0.5

#: A band narrower than this is not worth the risk: the saving is small and the
#: chance of clipping is high.
DEFAULT_MIN_BAND_MM = 1.0

#: Once this many tiles have been swept, the travel actually spent is compared
#: against what the full range would have cost. Anything above
#: :data:`NO_SAVING_RATIO` of the baseline is not worth the risk of predicting.
MIN_TILES_BEFORE_JUDGING = 8
NO_SAVING_RATIO = 0.9


@dataclass(frozen=True)
class ZBand:
    """The Z span one tile will sweep."""

    z_min: float
    z_max: float
    #: True when this is the entire bounding box, i.e. nothing was narrowed.
    #: A band that merely happens to equal the full range still counts as full,
    #: because "content reached the edge" only means something inside a
    #: narrowed band -- at the box edge it means the box is too small, which a
    #: re-sweep cannot fix.
    full: bool = False

    @property
    def depth_mm(self) -> float:
        return self.z_max - self.z_min


@dataclass(frozen=True)
class ContentExtent:
    """Where a tile's sample sat inside the band that was swept."""

    z_low: Optional[float] = None
    z_high: Optional[float] = None
    touches_low_edge: bool = False
    touches_high_edge: bool = False

    @property
    def has_content(self) -> bool:
        return self.z_low is not None and self.z_high is not None

    @property
    def touches_an_edge(self) -> bool:
        return self.touches_low_edge or self.touches_high_edge


def content_extent(
    planes: Sequence[Tuple[float, float]],
    band: ZBand,
    *,
    threshold_fraction: float = DEFAULT_CONTENT_THRESHOLD,
    scan_peak_score: Optional[float] = None,
    background_fraction: float = DEFAULT_BACKGROUND_FRACTION,
) -> ContentExtent:
    """Which part of ``band`` held sample, from per-plane ``(z, focus_score)``.

    ``scan_peak_score`` is the highest focus score seen anywhere in the scan so
    far. A tile whose own peak is a small fraction of it is background, and
    reports no content rather than a full-depth one -- see
    :data:`DEFAULT_BACKGROUND_FRACTION`.

    Edge contact is judged against the planes actually swept, not against the
    band's nominal bounds: a sweep capped to a handful of planes does not reach
    the arithmetic ends of its range, and comparing against those would report
    every tile as clear of the edges.
    """
    scored = [(float(z), float(s)) for z, s in planes if s is not None]
    if not scored:
        return ContentExtent()

    peak = max(s for _z, s in scored)
    if peak <= 0:
        return ContentExtent()
    if scan_peak_score and peak < float(scan_peak_score) * background_fraction:
        # Nothing here. Contributing a full-depth extent would widen every
        # neighbour's band and quietly undo the whole optimisation.
        return ContentExtent()

    cut = peak * threshold_fraction
    content_z = [z for z, s in scored if s >= cut]
    if not content_z:
        return ContentExtent()

    swept_lo = min(z for z, _s in scored)
    swept_hi = max(z for z, _s in scored)
    lo, hi = min(content_z), max(content_z)

    # Content is "at the edge" only when it is present AT the outermost plane
    # swept. Content sits on plane positions, so half a spacing means exactly
    # that and nothing looser.
    #
    # Anything wider double-counts the safety. The band is already built as
    # content plus a margin, and the margin IS the room the sample is allowed
    # to move; a tolerance of one or two planes on top is comparable to the
    # whole margin, and then the band trips on its own thickness. Measured on a
    # 10x14 grid, a 1.5-plane reach re-swept a plain 4 mm-thick flat sample on
    # every tile and gave up with 2.5% MORE travel than not adapting -- because
    # a capped 10-plane sweep of a 5 mm band has 0.5 mm planes, exactly the
    # default margin.
    #
    # Stopping one plane short of the edge is not a clipped measurement: no
    # content at that plane and content at the next brackets the sample's edge
    # between them, which is as well as any sweep can locate it.
    spacing = (swept_hi - swept_lo) / max(1, len(scored) - 1)
    reach = spacing * 0.5

    return ContentExtent(
        z_low=lo,
        z_high=hi,
        touches_low_edge=not band.full and (lo - swept_lo) <= reach,
        touches_high_edge=not band.full and (swept_hi - hi) <= reach,
    )


def should_resweep(extent: ContentExtent, band: ZBand) -> bool:
    """Does this tile have to be swept again over the full range?

    Two cases, and only inside a narrowed band -- at the full range there is
    nothing further to discover:

    * **Content ran to an edge.** The sample continues past where the sweep
      stopped, so its extent was not measured.
    * **Nothing was found at all.** This looks exactly like an empty tile and
      is not distinguishable from one: a sample that stepped clean out of the
      predicted band leaves the same empty planes behind. Treating "found
      nothing" as "there is nothing" is the assumption that silently loses a
      whole tile, so an empty narrow band is re-swept rather than believed.

    Both matter more than the time they cost, because the overview's Z edges
    become the laser acquisition's Z range. An underestimate is not a slower
    overview, it is a truncated acquisition, discovered later and unrecoverable
    without re-running the sample. When genuinely empty tiles make this
    expensive, :func:`adaptation_is_not_paying` is what notices.
    """
    if band.full:
        return False
    return (not extent.has_content) or extent.touches_an_edge


def full_band(z_min: float, z_max: float) -> ZBand:
    return ZBand(z_min=float(z_min), z_max=float(z_max), full=True)


def predict_band(
    neighbours: Iterable[ContentExtent],
    *,
    z_min: float,
    z_max: float,
    margin_mm: float = DEFAULT_MARGIN_MM,
    min_band_mm: float = DEFAULT_MIN_BAND_MM,
) -> ZBand:
    """The band to sweep next, from where neighbouring tiles found their sample.

    Falls back to the full range whenever there is nothing to go on -- no
    neighbours, or none of them with content. Narrowing on no evidence is
    exactly the guess this must not make.
    """
    z_min, z_max = float(z_min), float(z_max)
    withs = [e for e in neighbours if e.has_content]
    if not withs:
        return full_band(z_min, z_max)

    lo = min(e.z_low for e in withs) - float(margin_mm)
    hi = max(e.z_high for e in withs) + float(margin_mm)

    # Widen symmetrically to the floor. A band of a plane or two saves little
    # and clips easily.
    if (hi - lo) < float(min_band_mm):
        centre = (lo + hi) / 2.0
        lo = centre - float(min_band_mm) / 2.0
        hi = centre + float(min_band_mm) / 2.0

    lo = max(lo, z_min)
    hi = min(hi, z_max)
    if hi <= lo or (hi - lo) >= (z_max - z_min):
        return full_band(z_min, z_max)
    return ZBand(z_min=lo, z_max=hi, full=False)


def neighbour_extents(
    extents: Dict[Tuple[int, int], ContentExtent], x_idx: int, y_idx: int
) -> List[ContentExtent]:
    """Extents of the already-scanned tiles adjacent to this one in the grid.

    Grid adjacency rather than scan order: the serpentine's previous tile is a
    neighbour everywhere except at a column turn, where it is a whole column
    away and predicts nothing.
    """
    out = []
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        found = extents.get((x_idx + dx, y_idx + dy))
        if found is not None:
            out.append(found)
    return out


def adaptation_is_not_paying(
    adaptive_tiles: int, swept_mm: float, full_depth_mm: float
) -> bool:
    """Is the travel actually spent close enough to the baseline to stop?

    Measured against the distance the full range would have cost, not inferred
    from the re-sweep count. A re-sweep rate is only a proxy, and a misleading
    one: a sample with half its tiles empty re-sweeps half the time and STILL
    saves a quarter of the travel, because the re-swept tiles are cheap
    narrow-band misses and the rest are real savings. Judging it on the rate
    threw that saving away.

    Samples this heuristic does not fit -- discontinuous, sparse, oddly shaped,
    in a tube -- show up here rather than as a scan that mysteriously ran long,
    and the caller stops adapting.
    """
    if adaptive_tiles < MIN_TILES_BEFORE_JUDGING:
        return False
    baseline = float(full_depth_mm) * adaptive_tiles
    if baseline <= 0:
        return False
    return float(swept_mm) >= baseline * NO_SAVING_RATIO


def describe_saving(
    full_depth_mm: float,
    swept_depths_mm: Sequence[float],
    tiles: int,
    resweeps: int,
) -> str:
    """One line stating what adaptation actually bought, re-sweeps included.

    ``tiles`` is the number of TILES, which is not the number of sweeps: a
    re-swept tile appears twice in ``swept_depths_mm``. Deriving the baseline
    from the sweep count instead would inflate it by exactly the re-sweeps and
    report the largest saving on the runs that saved least.
    """
    if not swept_depths_mm or tiles <= 0:
        return "Adaptive Z: no tiles swept."
    total = sum(swept_depths_mm)
    baseline = full_depth_mm * tiles
    if baseline <= 0:
        return "Adaptive Z: no baseline to compare against."
    saved = (1.0 - total / baseline) * 100.0
    return (
        f"Adaptive Z: swept {total:.1f} mm against {baseline:.1f} mm for the "
        f"full range ({saved:.0f}% less Z travel) over {tiles} tile(s), "
        f"including {resweeps} re-sweep(s) where a narrowed band did not "
        f"measure the sample's Z extent."
    )
