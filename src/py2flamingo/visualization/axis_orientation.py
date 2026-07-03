"""Per-microscope stage->napari axis orientation.

Different microscopes place the detection objective and illumination in
different physical orientations relative to the user. The 3D viewer therefore
needs to know, **per microscope**, which stage axis (x/y/z) maps to which napari
display axis and with what sign. Historically this was hardcoded as the single
convention ``display_offset = [+dz, -dy, +/-dx]`` (napari Z,Y,X) with an
``invert_x`` flag; this module generalizes it to an arbitrary signed axis
permutation so a second scope (detection objective on the right, illumination
one-sided, stage X along the beam into/out of the screen) can be described in
config instead of code.

Model
-----
napari display axes are ordered ``(0, 1, 2) = (depth-into-screen, vertical,
horizontal)`` — i.e. napari ``(Z, Y, X)``. For **each** display axis we record:

* ``stage`` — which stage axis ('x' | 'y' | 'z') drives it, and
* ``flip`` — ``False`` if the display coordinate increases with the stage
  coordinate, ``True`` if it decreases (the napari "Y is drawn downward"
  inversion is exactly ``flip=True`` on the vertical axis).

From that single description both mappings the code needs fall out:

* **delta -> offset** (rigid-body shift of cached data as the stage moves)::

      offset[axis] = (-1 if flip else +1) * stage_delta[stage]

* **absolute -> napari voxel** (placing the holder / markers)::

      napari[axis] = (stage_max - stage)/vs   if flip
                     (stage - stage_min)/vs   otherwise

The legacy convention is the default:

* axis 0 (depth)      = stage ``z``, ``flip = invert_z``  (default False -> +dz)
* axis 1 (vertical)   = stage ``y``, ``flip = True``      (always -> -dy, napari y-down)
* axis 2 (horizontal) = stage ``x``, ``flip = invert_x``  (default True  -> -dx)

so ``AxisOrientation.legacy(invert_x, invert_z)`` reproduces the old numbers
bit-for-bit (the no-regression contract).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np

# napari display axis indices
DEPTH, VERTICAL, HORIZONTAL = 0, 1, 2
_STAGE_AXES = ("x", "y", "z")


@dataclass(frozen=True)
class AxisEntry:
    stage: str  # 'x' | 'y' | 'z'
    flip: bool  # True => display coordinate decreases as the stage coordinate increases


@dataclass(frozen=True)
class AxisOrientation:
    """Signed stage->napari axis permutation for one microscope.

    ``per_display_axis[i]`` describes napari display axis ``i`` (0 depth,
    1 vertical, 2 horizontal).
    """

    per_display_axis: Tuple[AxisEntry, AxisEntry, AxisEntry]

    # ---- constructors -------------------------------------------------

    @classmethod
    def legacy(
        cls, invert_x: bool = False, invert_z: bool = False
    ) -> "AxisOrientation":
        """The historical single-scope convention (default)."""
        return cls(
            (
                AxisEntry("z", bool(invert_z)),  # depth
                AxisEntry("y", True),  # vertical (napari y-down)
                AxisEntry("x", bool(invert_x)),  # horizontal
            )
        )

    @classmethod
    def from_config(
        cls, config: Dict, invert_x: bool = False, invert_z: bool = False
    ) -> "AxisOrientation":
        """Build from a viz-config dict.

        Uses an explicit ``orientation`` block when present, else falls back to
        the legacy convention parameterized by ``invert_x`` / ``invert_z`` (so
        existing configs behave identically).

        Expected block shape::

            orientation:
              depth:      { stage: x, flip: false }
              vertical:   { stage: y, flip: true }
              horizontal: { stage: z, flip: false }
        """
        block = (config or {}).get("orientation")
        if not block:
            return cls.legacy(invert_x=invert_x, invert_z=invert_z)
        try:
            entries = [
                _entry(block, "depth"),
                _entry(block, "vertical"),
                _entry(block, "horizontal"),
            ]
        except (KeyError, TypeError, ValueError) as exc:  # malformed -> legacy
            raise ValueError(f"Invalid 'orientation' config block: {exc}") from exc
        ori = cls(tuple(entries))
        ori.validate()
        return ori

    def validate(self) -> None:
        """Ensure the three display axes use the three distinct stage axes."""
        used = [e.stage for e in self.per_display_axis]
        if sorted(used) != sorted(_STAGE_AXES):
            raise ValueError(
                f"orientation must assign x, y, z exactly once; got {used}"
            )

    # ---- mappings -----------------------------------------------------

    def delta_offset(self, dx: float, dy: float, dz: float) -> np.ndarray:
        """Napari (Z, Y, X) offset for a stage delta (rigid-body data shift)."""
        d = {"x": dx, "y": dy, "z": dz}
        return np.array(
            [(-1.0 if e.flip else 1.0) * d[e.stage] for e in self.per_display_axis],
            dtype=float,
        )

    def delta_offset_matrix(self) -> np.ndarray:
        """3x3 signed permutation ``M`` with ``delta_offset(v) == M @ v``.

        Rows are napari display axes (depth, vertical, horizontal); columns are
        stage axes (x, y, z). Lets callers vectorize the mapping over arrays of
        stage offsets (e.g. per-pixel camera grids) instead of scalar calls.
        """
        col = {"x": 0, "y": 1, "z": 2}
        m = np.zeros((3, 3), dtype=float)
        for row, e in enumerate(self.per_display_axis):
            m[row, col[e.stage]] = -1.0 if e.flip else 1.0
        return m

    def absolute(
        self,
        x: float,
        y: float,
        z: float,
        ranges: Dict[str, Tuple[float, float]],
        voxel_size_mm: float,
    ) -> Tuple[float, float, float]:
        """Absolute napari (axis0, axis1, axis2) voxel coords for a stage pos.

        ``ranges`` maps stage axis -> (min_mm, max_mm). Not rounded/clamped —
        callers do that as before.
        """
        pos = {"x": x, "y": y, "z": z}
        out = []
        for e in self.per_display_axis:
            lo, hi = ranges[e.stage]
            v = (hi - pos[e.stage]) if e.flip else (pos[e.stage] - lo)
            out.append(v / voxel_size_mm)
        return tuple(out)

    def inverse_absolute(
        self,
        a0: float,
        a1: float,
        a2: float,
        ranges: Dict[str, Tuple[float, float]],
        voxel_size_mm: float,
    ) -> Tuple[float, float, float]:
        """Inverse of :meth:`absolute`: napari (a0,a1,a2) voxels -> stage (x,y,z) mm."""
        a = (a0, a1, a2)
        pos: Dict[str, float] = {}
        for i, e in enumerate(self.per_display_axis):
            lo, hi = ranges[e.stage]
            pos[e.stage] = (
                (hi - a[i] * voxel_size_mm) if e.flip else (lo + a[i] * voxel_size_mm)
            )
        return pos["x"], pos["y"], pos["z"]

    def display_extent(
        self,
        display_axis: int,
        ranges: Dict[str, Tuple[float, float]],
        voxel_size_mm: float,
    ) -> int:
        """Napari voxel extent of a display axis = extent of its stage axis."""
        lo, hi = ranges[self.per_display_axis[display_axis].stage]
        return int((hi - lo) / voxel_size_mm)

    def order_by_display(self, vals: Dict[str, float]) -> Tuple[float, float, float]:
        """Reorder a per-stage-axis mapping into napari (depth, vertical, horizontal).

        ``vals`` maps stage axis -> value; returns the values ordered by which
        stage axis drives each display axis. Used to build the world/storage
        frame (chamber origin, sample-region center, half-widths) so it lines up
        with the per-orientation display dimensions. Legacy => (z, y, x) value.
        """
        return tuple(vals[e.stage] for e in self.per_display_axis)

    def stage_axis_for(self, display_axis: int) -> str:
        return self.per_display_axis[display_axis].stage

    def display_axis_for(self, stage_axis: str) -> int:
        for i, e in enumerate(self.per_display_axis):
            if e.stage == stage_axis:
                return i
        raise KeyError(stage_axis)


def _entry(block: Dict, key: str) -> AxisEntry:
    sub = block[key]
    stage = str(sub["stage"]).lower()
    if stage not in _STAGE_AXES:
        raise ValueError(f"{key}.stage must be x/y/z, got {stage!r}")
    return AxisEntry(stage=stage, flip=bool(sub.get("flip", False)))
