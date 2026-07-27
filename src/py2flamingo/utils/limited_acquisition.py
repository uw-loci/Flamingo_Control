"""Smart limited acquisition — pure geometry core.

Decides, per tile, *which illumination arm(s)* to fire and *which angular
sector* of a volume to collect, so the instrument only images the optically-best
region instead of the whole cuboid from every configuration. Reduces redundant
light-sheet exposure, acquisition time, and disk usage.

This module is intentionally free of Qt, hardware, and numpy dependencies — it is
plain geometry over stage coordinates (mm) and angles (degrees), so it is fully
unit-testable without a rig. The acquisition dialog wires these decisions into the
generated ``Workflow.txt`` (``<Illumination Path>`` and Start/End ``Angle``); the
stitcher reassembles the resulting partial / asymmetric data.

Coordinate frame (current TSPIM scope, see coordinate_system_reference.md):
    * stage X = illumination axis (two opposing light-sheet arms along +/-X)
    * stage Z = detection axis (toward the detection objective)
    * stage Y = vertical rotation axis (rotation swaps X<->Z)

Design goals from the spec:
    * Mode A/B: fire only the near arm when a tile is > 1 FOV from region center.
    * Mode C1/C2: collect only a rotational *sector* of the cuboid per angle;
      support 2 and 4 angles now, but never hard-code N — the sector width is
      ``360/N`` and the rotation step ``360/N`` for arbitrary N.

Sign / direction unknowns that must be confirmed on the rig are surfaced as
explicit parameters (``right_arm_at_positive_x``, ``good_direction_deg``,
``rotation_sign``) rather than baked in. Their defaults are the current best guess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

__all__ = [
    "ArmSelection",
    "SectorPlan",
    "MultiviewTile",
    "choose_illumination_arms",
    "sector_width_deg",
    "angle_schedule_deg",
    "plan_multiview_sectors",
    "assign_tiles_to_angles",
    "plan_halfrotate_split",
    "plan_multiview_acquisition",
]


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ArmSelection:
    """Which illumination arm(s) to enable for one tile.

    ``left_on`` / ``right_on`` map directly to the ``<Illumination Path>``
    ``Left path`` / ``Right path`` ON/OFF flags in Workflow.txt. ``reason`` is a
    short human-readable justification suitable for the UI description label.
    """

    left_on: bool
    right_on: bool
    reason: str


@dataclass(frozen=True)
class SectorPlan:
    """One angle's collection plan in a multi-view acquisition.

    Attributes:
        angle_deg: Rotation-stage angle to command for this view.
        sector_center_deg: Sample-frame azimuth that faces the good direction
            after rotating by ``angle_deg`` (i.e. the center of the collected wedge).
        sector_half_width_deg: Half-angular-width of the collected wedge, before
            overlap. Full nominal width is ``2 * sector_half_width_deg == 360/N``.
        overlap_deg: Extra half-width added on each side for inter-angle overlap.
    """

    angle_deg: float
    sector_center_deg: float
    sector_half_width_deg: float
    overlap_deg: float


# --------------------------------------------------------------------------- #
# Mode A / B — position-based single-arm selection
# --------------------------------------------------------------------------- #
def choose_illumination_arms(
    tile_x_mm: float,
    center_x_mm: float,
    fov_mm: float,
    margin_fovs: float = 1.0,
    right_arm_at_positive_x: bool = True,
) -> ArmSelection:
    """Pick the illumination arm(s) for a tile from its offset along X.

    A light sheet degrades with propagation distance, so a tile far to one side of
    the region center is best imaged by the arm on *that* side; the far arm only
    doubles exposure/time/disk for data the near arm already covers. Within
    ``margin_fovs`` field-of-views of center, keep both arms (today's behavior).

    Args:
        tile_x_mm: Tile center X (stage mm).
        center_x_mm: Acquisition-region center X (stage mm).
        fov_mm: Field of view (mm).
        margin_fovs: Keep both arms while ``|offset| <= margin_fovs * fov``.
            The spec's rule is "more than a full FOV from center" -> 1.0.
        right_arm_at_positive_x: True if the right arm (``I1``) enters from the
            +X side. RIG-VALIDATE: flips which arm is "near" for a given offset.

    Returns:
        ArmSelection with the near arm on, the far arm off, or both on near center.
    """
    if fov_mm <= 0:
        raise ValueError(f"fov_mm must be positive, got {fov_mm}")

    offset = tile_x_mm - center_x_mm
    threshold = margin_fovs * fov_mm

    if abs(offset) <= threshold:
        return ArmSelection(True, True, "within margin of center: both arms")

    # Which physical side is this tile on, and which arm is nearest it?
    tile_on_positive_x = offset > 0
    near_arm_is_right = tile_on_positive_x == right_arm_at_positive_x

    if near_arm_is_right:
        return ArmSelection(
            left_on=False,
            right_on=True,
            reason=f"{offset:+.2f} mm from center (> {threshold:.2f}): right arm only",
        )
    return ArmSelection(
        left_on=True,
        right_on=False,
        reason=f"{offset:+.2f} mm from center (> {threshold:.2f}): left arm only",
    )


# --------------------------------------------------------------------------- #
# Mode C1 / C2 — multi-view sector planning (arbitrary N)
# --------------------------------------------------------------------------- #
def sector_width_deg(n_angles: int) -> float:
    """Nominal angular width of each collected sector: ``360 / n_angles``."""
    if n_angles < 1:
        raise ValueError(f"n_angles must be >= 1, got {n_angles}")
    return 360.0 / n_angles


def angle_schedule_deg(n_angles: int, start_deg: float = 0.0) -> List[float]:
    """Rotation-stage angles for an N-view acquisition: ``start + k*360/N``.

    Never hard-codes 2 or 4 — evenly spaces N angles around the circle.
    """
    step = sector_width_deg(n_angles)
    return [_wrap_deg(start_deg + k * step) for k in range(n_angles)]


def plan_multiview_sectors(
    n_angles: int,
    good_direction_deg: float = 45.0,
    start_deg: float = 0.0,
    overlap_deg: float = 0.0,
    rotation_sign: float = 1.0,
) -> List[SectorPlan]:
    """Build the per-angle sector plan for an integrated multi-view acquisition.

    Each angle collects only the wedge of the cuboid that lands in the good
    optical zone (between the excitation and detection objectives) once the sample
    is rotated. For N angles the wedges tile the circle at ``360/N`` each; a small
    ``overlap_deg`` is added on both sides so adjacent angles share data for
    registration ("a little over halfway").

    Args:
        n_angles: Number of views (2, 4, ... arbitrary N).
        good_direction_deg: Lab-frame azimuth of the good zone (bisector between
            illumination arm and detection objective). RIG-VALIDATE.
        start_deg: Rotation angle of the first view.
        overlap_deg: Extra half-width per side for inter-angle overlap.
        rotation_sign: +1 or -1; sign relating a commanded rotation to sample-frame
            azimuth change. RIG-VALIDATE.

    Returns:
        One SectorPlan per angle, in schedule order.
    """
    half = sector_width_deg(n_angles) / 2.0
    plans: List[SectorPlan] = []
    for angle in angle_schedule_deg(n_angles, start_deg):
        # The sample-frame azimuth that rotates onto the good direction at this
        # angle is the wedge center we collect.
        sector_center = _wrap_deg(good_direction_deg - rotation_sign * angle)
        plans.append(
            SectorPlan(
                angle_deg=angle,
                sector_center_deg=sector_center,
                sector_half_width_deg=half,
                overlap_deg=overlap_deg,
            )
        )
    return plans


def assign_tiles_to_angles(
    tiles_xz_mm: Sequence[Tuple[float, float]],
    n_angles: int,
    rotation_center_xz_mm: Tuple[float, float],
    good_direction_deg: float = 45.0,
    start_deg: float = 0.0,
    overlap_deg: float = 0.0,
    rotation_sign: float = 1.0,
) -> Dict[int, List[int]]:
    """Assign each tile to the angle(s) whose good sector covers it.

    A tile at sample-frame azimuth ``phi`` (about the rotation center in the X-Z
    plane) is collected at angle ``k`` when, after rotating the sample by that
    angle, the tile lands within the good sector. With ``overlap_deg > 0`` a tile
    near a wedge boundary is assigned to both neighboring angles so the stitcher
    has overlap to register.

    Args:
        tiles_xz_mm: Tile centers as (x_mm, z_mm) in the un-rotated sample frame.
        n_angles: Number of views.
        rotation_center_xz_mm: (x, z) of the rotation axis in the X-Z plane.
        good_direction_deg, start_deg, overlap_deg, rotation_sign: see
            :func:`plan_multiview_sectors`.

    Returns:
        Mapping ``angle_index -> [tile_index, ...]``. Every angle index 0..N-1 is
        present (possibly empty). A tile with a degenerate position exactly on the
        rotation center is assigned to all angles.
    """
    plans = plan_multiview_sectors(
        n_angles, good_direction_deg, start_deg, overlap_deg, rotation_sign
    )
    cx, cz = rotation_center_xz_mm
    result: Dict[int, List[int]] = {k: [] for k in range(n_angles)}

    for t_idx, (x, z) in enumerate(tiles_xz_mm):
        dx, dz = x - cx, z - cz
        if abs(dx) < 1e-9 and abs(dz) < 1e-9:
            for k in range(n_angles):
                result[k].append(t_idx)
            continue
        phi = math.degrees(math.atan2(dz, dx))
        limit = plans[0].sector_half_width_deg + overlap_deg
        for k, plan in enumerate(plans):
            if _angular_distance_deg(phi, plan.sector_center_deg) <= limit + 1e-9:
                result[k].append(t_idx)
    return result


@dataclass(frozen=True)
class MultiviewTile:
    """One planned acquisition: a tile at a specific rotation-stage angle.

    ``x``/``z`` are the stage position rotated into the frame for ``angle_deg``;
    ``y`` is unchanged (rotation axis). ``z_min``/``z_max`` are the Z-stack range
    (kept as the source span, re-centered on the rotated Z — the precise per-angle
    range is a rig/cross-angle refinement). ``angle_index`` is 0..N-1 and
    ``source_index`` points back into the input tile list.
    """

    x: float
    y: float
    z: float
    z_min: float
    z_max: float
    angle_deg: float
    angle_index: int
    source_index: int


def plan_multiview_acquisition(
    tiles_xyz_zrange: Sequence[Tuple[float, float, float, float, float]],
    n_angles: int,
    rotation_center_xz_mm: Tuple[float, float],
    good_direction_deg: float = 45.0,
    start_deg: float = 0.0,
    overlap_deg: float = 0.0,
    rotation_sign: float = 1.0,
) -> List[MultiviewTile]:
    """Plan an integrated multi-view acquisition (Mode C).

    For each of ``n_angles`` views, collect only the tiles whose good sector faces
    the optics at that rotation (via :func:`assign_tiles_to_angles`), rotating each
    tile's stage position into that view's frame. This is the acquisition-side
    counterpart of the stitcher's rotation-affine fusion — together they collect
    and reassemble only the optically-best sector per angle.

    Args:
        tiles_xyz_zrange: Source tiles as ``(x, y, z, z_min, z_max)`` (stage mm),
            in the un-rotated frame. ``z`` is the tile's Z-stack center.
        n_angles: Number of views (2, 4, ... arbitrary N).
        rotation_center_xz_mm: (x, z) of the rotation axis (e.g. sample-mount tip).
        good_direction_deg, start_deg, overlap_deg, rotation_sign: see
            :func:`plan_multiview_sectors`. ``rotation_sign`` is shared with the
            physical stage rotation applied here. RIG-VALIDATE.

    Returns:
        Flat list of :class:`MultiviewTile`, grouped implicitly by angle.
    """
    tiles_xz = [(x, z) for (x, _y, z, _zmin, _zmax) in tiles_xyz_zrange]
    angles = angle_schedule_deg(n_angles, start_deg)
    assignment = assign_tiles_to_angles(
        tiles_xz,
        n_angles,
        rotation_center_xz_mm,
        good_direction_deg,
        start_deg,
        overlap_deg,
        rotation_sign,
    )
    tip_x, tip_z = rotation_center_xz_mm
    out: List[MultiviewTile] = []
    for k, angle in enumerate(angles):
        for idx in assignment[k]:
            x, y, z, z_min, z_max = tiles_xyz_zrange[idx]
            xr, zr = _rotate_xz(x, z, tip_x, tip_z, rotation_sign * angle)
            span = z_max - z_min
            out.append(
                MultiviewTile(
                    x=xr,
                    y=y,
                    z=zr,
                    z_min=zr - span / 2.0,
                    z_max=zr + span / 2.0,
                    angle_deg=angle,
                    angle_index=k,
                    source_index=idx,
                )
            )
    return out


def plan_halfrotate_split(
    good_direction_deg: float = 45.0,
    overlap_deg: float = 0.0,
    rotation_sign: float = 1.0,
) -> List[SectorPlan]:
    """Mode C1: half-and-rotate within a single volume (the N=2 case).

    Collect one half of the volume, command a 180 deg rotation, collect the other
    half — each half a little past the split plane (``overlap_deg``) so they stitch.
    This is exactly :func:`plan_multiview_sectors` with ``n_angles=2``, named for
    the single-volume workflow the UI exposes.
    """
    return plan_multiview_sectors(
        n_angles=2,
        good_direction_deg=good_direction_deg,
        start_deg=0.0,
        overlap_deg=overlap_deg,
        rotation_sign=rotation_sign,
    )


# --------------------------------------------------------------------------- #
# Angle helpers
# --------------------------------------------------------------------------- #
def _wrap_deg(a: float) -> float:
    """Wrap an angle to (-180, 180]."""
    a = math.fmod(a, 360.0)
    if a <= -180.0:
        a += 360.0
    elif a > 180.0:
        a -= 360.0
    return a


def _angular_distance_deg(a: float, b: float) -> float:
    """Smallest absolute difference between two angles, in [0, 180]."""
    return abs(_wrap_deg(a - b))


def _rotate_xz(
    x: float, z: float, tip_x: float, tip_z: float, angle_deg: float
) -> Tuple[float, float]:
    """Rotate a point in the X-Z plane about (tip_x, tip_z) by ``angle_deg``.

    Matches the convention of ``acquisition_profile_generator.rotate_point``
    (positive = physical clockwise): x' = x_tip + dx·cos + dz·sin,
    z' = z_tip − dx·sin + dz·cos.
    """
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    dx, dz = x - tip_x, z - tip_z
    return (tip_x + dx * ca + dz * sa, tip_z - dx * sa + dz * ca)
