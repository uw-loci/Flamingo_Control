"""The rig's motion envelope and its verification targets.

Both come from ``microscope_settings/{name}_settings.json``, the file the app
already treats as the only store that bounds a stage move
(``services/stage_motion_gate.py``). Without it there is no envelope and the
motion checks are BLOCKED; the fallback limits the settings service invents
(0-26 mm) are wider than any real stage and are never used here.

Targets (optional) let every run image the same place:

```json
"verification_targets": [
  {"name": "bead slide", "x": 6.0, "y": 12.0, "z": 19.0, "r": 0.0,
   "reference_dir": "verification/references/bead slide"}
]
```

A reference folder, once it holds images from a known-good run, turns the
image metrics of later runs into comparisons (shift, intensity, focus).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from py2flamingo.verification.context import Target

LINEAR_AXES = ("x", "y", "z")
# Moves stay this far inside each settings limit. Not a hardware figure: a
# margin so that a small verification move never ends on a limit.
DEFAULT_MARGIN_MM = 0.2
DEFAULT_MARGIN_DEG = 2.0
# Largest distance from the target any verification move may go.
DEFAULT_MAX_EXCURSION_MM = 2.0
DEFAULT_MAX_EXCURSION_DEG = 10.0


@dataclass
class KeepOut:
    name: str
    bounds: Dict[str, Tuple[float, float]]

    def contains(self, position: Dict[str, float]) -> bool:
        return all(
            lo <= position.get(axis, float("nan")) <= hi
            for axis, (lo, hi) in self.bounds.items()
        )


@dataclass
class Envelope:
    limits: Dict[str, Tuple[float, float]]
    source: str
    keep_outs: List[KeepOut] = field(default_factory=list)
    margin_mm: float = DEFAULT_MARGIN_MM
    margin_deg: float = DEFAULT_MARGIN_DEG
    max_excursion_mm: float = DEFAULT_MAX_EXCURSION_MM
    max_excursion_deg: float = DEFAULT_MAX_EXCURSION_DEG

    def problems(self, axis: str, target: float, origin: Dict[str, float]) -> List[str]:
        """Why a move of ``axis`` to ``target`` (from ``origin``) is refused."""
        out = []
        lo, hi = self.limits[axis]
        margin = self.margin_deg if axis == "r" else self.margin_mm
        if not (lo + margin <= target <= hi - margin):
            out.append(
                f"{axis} {target:.3f} is outside {lo + margin:.3f}..{hi - margin:.3f} "
                f"(limits {lo:g}..{hi:g} less a {margin:g} margin, {self.source})"
            )
        reach = self.max_excursion_deg if axis == "r" else self.max_excursion_mm
        if axis in origin and abs(target - origin[axis]) > reach:
            out.append(
                f"{axis} move of {abs(target - origin[axis]):.3f} exceeds the "
                f"verification limit of {reach:g}"
            )
        after = dict(origin)
        after[axis] = target
        for box in self.keep_outs:
            if box.contains(after):
                out.append(f"{axis} {target:.3f} enters keep-out '{box.name}'")
        return out

    def describe(self) -> str:
        parts = [f"{a} {lo:g}..{hi:g}" for a, (lo, hi) in self.limits.items()]
        boxes = f", {len(self.keep_outs)} keep-out box(es)" if self.keep_outs else ""
        return f"{', '.join(parts)} (from {self.source}{boxes})"


@dataclass
class RigProfile:
    name: str
    settings_file: Optional[Path]
    envelope: Optional[Envelope]
    targets: List[Target]
    why_no_envelope: str = ""


def settings_path(microscope_name: str, base: Optional[Path] = None) -> Optional[Path]:
    folder = (base or Path.cwd()) / "microscope_settings"
    exact = folder / f"{microscope_name}_settings.json"
    if exact.exists():
        return exact
    wanted = exact.name.lower()
    if folder.is_dir():
        for candidate in sorted(folder.glob("*_settings.json")):
            if candidate.name.lower() == wanted:
                return candidate
    return None


def load_rig(microscope_name: str, base: Optional[Path] = None) -> RigProfile:
    path = settings_path(microscope_name, base) if microscope_name else None
    if path is None:
        return RigProfile(
            microscope_name,
            None,
            None,
            [],
            f"no microscope_settings/{microscope_name or '<name>'}_settings.json: "
            "stage limits unknown (Edit > Microscope Setup writes it)",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return RigProfile(microscope_name, path, None, [], f"{path.name}: {e}")

    envelope = None
    why = ""
    raw = data.get("stage_limits") or {}
    try:
        limits = {
            axis: (float(raw[axis]["min"]), float(raw[axis]["max"]))
            for axis in ("x", "y", "z", "r")
        }
        if any(lo >= hi for lo, hi in limits.values()):
            raise ValueError("a min is not below its max")
        boxes = [
            KeepOut(
                str(b.get("name", f"box {i + 1}")),
                {a: (float(b[a][0]), float(b[a][1])) for a in LINEAR_AXES if a in b},
            )
            for i, b in enumerate(data.get("keep_out_boxes") or [])
        ]
        envelope = Envelope(limits, path.name, boxes)
    except (KeyError, TypeError, ValueError) as e:
        why = f"{path.name} has no usable stage_limits ({e})"

    targets = []
    for t in data.get("verification_targets") or []:
        try:
            ref = t.get("reference_dir")
            targets.append(
                Target(
                    name=str(t["name"]),
                    position={a: float(t[a]) for a in ("x", "y", "z", "r")},
                    reference_dir=Path(ref) if ref else None,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return RigProfile(microscope_name, path, envelope, targets, why)
