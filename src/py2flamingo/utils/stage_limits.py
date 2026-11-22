"""The stage-limit arithmetic, one copy for every mover.

Two policies use it, and each caller says which:

* **refuse** (``within_limits``): ``PositionController.move_x/y/z`` and ``move_rotation`` raise
  ValueError for a target outside the soft limits. That check is the guard;
  every GUI move ends there.
* **clamp** (``clamp_to_limits``): ``PositionController.move_to_position``
  (presets, click-to-move, overview tile navigation), the movement
  controller's absolute and relative moves, and the jog panel pull an
  out-of-range target onto the nearest limit and say so in the log.

A non-finite target is refused by both: NaN passes ``min(max(v, lo), hi)``
unchanged, and ``lo <= nan <= hi`` is False.
"""

from __future__ import annotations

import math
from typing import Optional


def within_limits(value: float, lo: float, hi: float) -> bool:
    """True when ``lo <= value <= hi`` (never for NaN)."""
    return lo <= value <= hi


def clamp_to_limits(
    value: float, lo: Optional[float] = None, hi: Optional[float] = None
) -> float:
    """``value`` pulled onto ``[lo, hi]``; a missing bound is not applied.

    Raises ValueError for a value that is not a finite number."""
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"stage target {value!r} is not a finite number")
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v
