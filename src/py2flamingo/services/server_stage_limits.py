"""The connected server's own stage soft limits, applied to every stage move.

``{name}_settings.json`` is what the app keeps per microscope, and until
2026-11-21 it was the only thing that bounded a move. The server has its own
soft limits ('Stage limits', 'Soft limit min/max x-axis' in its settings) and
applies none of them to a position command (PIStageBase: no software limit on
POSITION_SET), so a json wider than the instrument let the app drive the stage
where the server's setup says it must not go. On CTLSM1 the json allowed
y from 1 and z from 8; the server's soft limits start at y 5 and z 11.

The connection service stores the server's soft limits here when it reads the
settings at connect, and clears them on disconnect.
``MicroscopeSettingsService.get_stage_limits`` narrows the json to them, so
every caller that bounds a move (the position controller, the jog panel, the
LED overview, presets, workflows) gets the narrower of the two on each bound.

A module-level store, like ``stage_motion_gate``, because the settings service
and the connection service do not hold each other.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

AXES = ("x", "y", "z", "r")

_lock = threading.Lock()
_limits: Optional[Dict[str, Tuple[float, float]]] = None


def parse(settings: Dict) -> Dict[str, Tuple[float, float]]:
    """Soft limits per axis from the server's settings; axes it lacks are left out."""
    section = (settings or {}).get("Stage limits") or {}
    out = {}
    for axis in AXES:
        try:
            lo = float(section[f"Soft limit min {axis}-axis"])
            hi = float(section[f"Soft limit max {axis}-axis"])
        except (KeyError, TypeError, ValueError):
            continue
        if lo < hi:
            out[axis] = (lo, hi)
    return out


def set_from_settings(settings: Dict) -> Dict[str, Tuple[float, float]]:
    """Store the soft limits found in ``settings`` (the dict read at connect)."""
    global _limits
    found = parse(settings)
    with _lock:
        _limits = found or None
    if found:
        logger.info(
            "Server soft limits (applied to every stage move with the settings json): %s",
            ", ".join(f"{a} {lo:g}..{hi:g}" for a, (lo, hi) in found.items()),
        )
    else:
        logger.warning(
            "The server's settings carry no stage soft limits; moves are bounded by "
            "the settings json alone"
        )
    return found


def clear() -> None:
    global _limits
    with _lock:
        _limits = None


def get() -> Optional[Dict[str, Tuple[float, float]]]:
    with _lock:
        return dict(_limits) if _limits else None


def narrow(
    limits: Dict[str, Dict[str, float]], source: str = "the settings json"
) -> Tuple[Dict[str, Dict[str, float]], List[str]]:
    """``limits`` ({axis: {'min', 'max'}}) narrowed to the server's soft limits.

    Returns the narrowed copy and one line per bound ``source`` set wider than
    the server. Unchanged when no server limits are known."""
    server = get()
    out = {axis: dict(bounds) for axis, bounds in limits.items()}
    notes: List[str] = []
    if not server:
        return out, notes
    for axis, (slo, shi) in server.items():
        bounds = out.get(axis)
        if not bounds:
            continue
        lo, hi = float(bounds["min"]), float(bounds["max"])
        if slo > lo:
            notes.append(
                f"{axis} min {lo:g} in {source} raised to the server's {slo:g}"
            )
        if shi < hi:
            notes.append(
                f"{axis} max {hi:g} in {source} lowered to the server's {shi:g}"
            )
        new_lo, new_hi = max(lo, slo), min(hi, shi)
        if new_lo < new_hi:
            bounds["min"], bounds["max"] = new_lo, new_hi
        else:
            notes.append(
                f"{axis}: {source} {lo:g}..{hi:g} and the server's {slo:g}..{shi:g} "
                "do not overlap; the settings json is kept"
            )
    return out, notes
