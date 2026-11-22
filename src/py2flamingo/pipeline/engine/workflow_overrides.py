"""Apply a WORKFLOW node's overrides to its workflow text before it is sent.

The node runs a workflow template (a ``Workflow.txt``). Three switches change
it for the object the pipeline found (docs/pipeline_json_format.md, WORKFLOW):

* ``use_input_position``: the object's (or tuple's) position becomes the
  Start Position; the End Position moves by the same amount, so the template's
  Z range and tile span are kept.
* ``auto_z_range``: Start Z and End Z span the object's Z extent plus the
  buffer, at the template's plane spacing. The server takes the range from
  ``|End Z - Start Z|`` (``CheckStackBase``, ``CheckStackZStackMovie``), so
  End Z is written as well as ``Number of planes`` and ``Change in Z axis``.
* ``auto_tiling``: an object larger than one field of view gets a Tile grid
  around its center (Start/End are the corner tiles' CENTERS,
  ``utils/tile_geometry.corner_span_for_tiles``); a smaller one loses the
  template's tiling.

The template is edited in place (``utils.workflow_parser.edit_workflow_text``):
only the values above change and every other byte of the template is sent as
it was. Every Start and End position is then checked against the stage limits,
and the node refuses to run when one is outside or the limits are unknown.

Values are read through ``utils.workflow_keys`` (the reader alias layer), so a
template written by any server build reads the same.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from py2flamingo.utils.workflow_keys import number, whole_number, workflow_sections
from py2flamingo.utils.workflow_parser import edit_workflow_text, format_position

START = "Start Position"
END = "End Position"
STACK = "Stack Settings"
EXPERIMENT = "Experiment Settings"

#: Position keys in the workflow, by stage axis.
AXIS_KEYS = (
    ("x", "X (mm)"),
    ("y", "Y (mm)"),
    ("z", "Z (mm)"),
    ("r", "Angle (degrees)"),
)


class WorkflowOverrideError(RuntimeError):
    """The node's overrides cannot be applied safely; nothing was sent."""


@dataclass
class WorkflowEdit:
    """A workflow template and the values a node changes in it."""

    text: str
    sections: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    edits: Dict[str, Dict[str, str]] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    @classmethod
    def from_bytes(cls, data: Any) -> "WorkflowEdit":
        if isinstance(data, (bytes, bytearray)):
            text = bytes(data).decode("utf-8")
        elif isinstance(data, str):
            text = data
        else:
            raise WorkflowOverrideError(
                "The workflow service returned "
                f"{type(data).__name__}, not workflow text; nothing was sent."
            )
        sections = workflow_sections(text)
        for name in (START, END):
            if name not in sections:
                raise WorkflowOverrideError(
                    f"The workflow template has no <{name}>, so there is no "
                    "position to start from; nothing was sent."
                )
        return cls(text=text, sections=sections)

    # -------------------------------------------------------------- reading

    def position(self, name: str) -> Dict[str, float]:
        """``{x, y, z, r}`` of Start or End as it will be sent."""
        out = {}
        for axis, key in AXIS_KEYS:
            raw = self.edits.get(name, {}).get(key, self.sections[name].get(key))
            value = number(raw)
            if value is None or not math.isfinite(value):
                raise WorkflowOverrideError(
                    f"<{name}> {key} is {raw!r}, not a number; nothing was sent."
                )
            out[axis] = value
        return out

    def value(self, section: str, key: str) -> Any:
        return self.edits.get(section, {}).get(
            key, self.sections.get(section, {}).get(key)
        )

    @property
    def changed(self) -> bool:
        return any(self.edits.values())

    # -------------------------------------------------------------- editing

    def _set(self, section: str, key: str, text: str) -> None:
        self.edits.setdefault(section, {})[key] = text

    def _set_position(self, name: str, pos: Dict[str, float]) -> None:
        keys = {key: pos[axis] for axis, key in AXIS_KEYS}
        for key, text in format_position(keys, name).items():
            self._set(name, key, text)

    def move_start_to(self, x: float, y: float, z: float, r: float) -> None:
        """Start Position to (x, y, z, r); End Position by the same amount."""
        start, end = self.position(START), self.position(END)
        new = {"x": x, "y": y, "z": z, "r": r}
        for axis, v in new.items():
            if not math.isfinite(float(v)):
                raise WorkflowOverrideError(
                    f"The input position {axis} = {v!r} is not a number; "
                    "nothing was sent."
                )
        delta = {a: float(new[a]) - start[a] for a in new}
        self._set_position(START, {a: float(new[a]) for a in new})
        self._set_position(END, {a: end[a] + delta[a] for a in new})
        self.notes.append(
            "position: start "
            + ", ".join(f"{a}={float(new[a]):.3f}" for a in ("x", "y", "z", "r"))
        )

    def set_z_range(self, z_bottom_mm: float, z_top_mm: float) -> int:
        """Stack from ``z_bottom_mm`` covering up to ``z_top_mm`` at the
        template's plane spacing. Returns the number of planes."""
        step_um = number(self.value(EXPERIMENT, "Plane spacing (um)"))
        if step_um is None or not step_um > 0:
            raise WorkflowOverrideError(
                "Auto Z-range needs the template's 'Plane spacing (um)' and it "
                f"is {self.value(EXPERIMENT, 'Plane spacing (um)')!r}; nothing "
                "was sent."
            )
        if not z_top_mm > z_bottom_mm:
            raise WorkflowOverrideError(
                f"Auto Z-range: the object's Z range {z_bottom_mm:.3f}-"
                f"{z_top_mm:.3f} mm is empty; nothing was sent."
            )
        step_mm = step_um / 1000.0
        # Range = planes x step, the Workflow tab's convention
        # (views/workflow_panels/zstack_panel.py); rounded up so the buffered
        # extent is covered.
        planes = max(1, math.ceil((z_top_mm - z_bottom_mm) / step_mm - 1e-9))
        change_mm = planes * step_mm
        start, end = self.position(START), self.position(END)
        start["z"] = z_bottom_mm
        end["z"] = z_bottom_mm + change_mm
        self._set_position(START, start)
        self._set_position(END, end)
        self._set(STACK, "Number of planes", str(planes))
        self._set(STACK, "Change in Z axis (mm)", f"{change_mm:.6f}")
        self.notes.append(
            f"Z range: {z_bottom_mm:.3f}-{z_bottom_mm + change_mm:.3f} mm, "
            f"{planes} planes at {step_um:g} um"
        )
        return planes

    def template_overlap_percent(self) -> Optional[float]:
        """The template's tile overlap, when it is a Tile workflow. The server
        reads ``Stack option settings 1`` as the X overlap percent."""
        if str(self.value(STACK, "Stack option") or "").strip() != "Tile":
            return None
        return number(self.value(STACK, "Stack option settings 1"))

    def set_tiling(
        self,
        center_x_mm: float,
        center_y_mm: float,
        num_tiles_x: int,
        num_tiles_y: int,
        fov_x_mm: float,
        fov_y_mm: float,
        overlap_percent: float,
    ) -> None:
        """A Tile grid of ``num_tiles_x`` x ``num_tiles_y`` centered on the
        point; Z and angle stay as they are."""
        from py2flamingo.utils.tile_geometry import corner_span_for_tiles
        from py2flamingo.utils.workflow_serialization import tile_stack_fields

        span_x = corner_span_for_tiles(num_tiles_x, fov_x_mm, overlap_percent)
        span_y = corner_span_for_tiles(num_tiles_y, fov_y_mm, overlap_percent)
        start, end = self.position(START), self.position(END)
        start["x"], end["x"] = center_x_mm - span_x / 2, center_x_mm + span_x / 2
        start["y"], end["y"] = center_y_mm - span_y / 2, center_y_mm + span_y / 2
        self._set_position(START, start)
        self._set_position(END, end)
        for key, value in tile_stack_fields(
            {"overlap_percent": overlap_percent}
        ).items():
            self._set(
                STACK, key, f"{value:g}" if isinstance(value, float) else str(value)
            )
        self.notes.append(
            f"tiling: {num_tiles_x}x{num_tiles_y} tiles at {overlap_percent:g}% "
            f"overlap around x={center_x_mm:.3f}, y={center_y_mm:.3f}"
        )

    def clear_tiling(self) -> None:
        """One field: End X/Y = Start X/Y, and a Tile template becomes a stack."""
        start, end = self.position(START), self.position(END)
        end["x"], end["y"] = start["x"], start["y"]
        self._set_position(END, end)
        if str(self.value(STACK, "Stack option") or "").strip() == "Tile":
            planes = whole_number(self.value(STACK, "Number of planes"), 1)
            self._set(STACK, "Stack option", "ZStack" if planes > 1 else "None")
            self._set(STACK, "Stack option settings 1", "0")
            self._set(STACK, "Stack option settings 2", "0")
        self.notes.append("tiling: the object fits one field, no tiles")

    # -------------------------------------------------------------- checking

    def check_within_limits(
        self, limits: Optional[Dict[str, Dict[str, float]]]
    ) -> None:
        """Refuse a Start or End position outside ``limits`` ({axis: {min, max}})
        or when the limits are unknown."""
        if not limits:
            raise WorkflowOverrideError(
                "The stage limits are unknown, so the workflow's positions "
                "cannot be checked; nothing was sent."
            )
        problems = []
        for name in (START, END):
            pos = self.position(name)
            for axis, key in AXIS_KEYS:
                bounds = limits.get(axis)
                if not bounds:
                    problems.append(f"no {axis} limits")
                    continue
                lo, hi = float(bounds["min"]), float(bounds["max"])
                if not lo <= pos[axis] <= hi:
                    problems.append(
                        f"{name} {axis} = {pos[axis]:.3f} is outside {lo:g}..{hi:g}"
                    )
        if problems:
            raise WorkflowOverrideError(
                "The workflow would move the stage outside its limits: "
                + "; ".join(sorted(set(problems)))
                + ". Nothing was sent."
            )

    def to_bytes(self) -> bytes:
        """The text to send: the template as it was when nothing changed."""
        if not self.changed:
            return self.text.encode("utf-8")
        edited = edit_workflow_text(self.text, self.edits)
        # Read back what will be sent: every edited value must read as written.
        back = workflow_sections(edited)
        for section, keys in self.edits.items():
            for key, text in keys.items():
                got = back.get(section, {}).get(key)
                if str(got).strip() != str(text).strip():
                    raise WorkflowOverrideError(
                        f"<{section}> {key} reads back as {got!r} after it was "
                        f"set to {text!r}; nothing was sent."
                    )
        return edited.encode("utf-8")


def position_from_input(position_data: Any) -> Tuple[float, float, float, float]:
    """(x, y, z, r) from a DetectedObject or an (x, y, z[, r]) tuple.

    A DetectedObject has no angle; it is taken at r = 0, as the runner always
    did. Anything else on the input is refused rather than ignored."""
    if hasattr(position_data, "centroid_stage"):
        # A DetectedObject whose stage coordinates were never measured
        # carries (0, 0, 0). Driving there is a real move to the corner of
        # the chamber, so refuse instead of taking it literally.
        if not getattr(position_data, "stage_coords_available", True):
            raise WorkflowOverrideError(
                "The object on the position input has no "
                "stage coordinates (no coordinate config was available when it "
                "was detected), so there is no position to move to. Untick "
                "'Override Position from Input' to use the template's own position."
            )
        sx, sy, sz = position_data.centroid_stage
        return (float(sx), float(sy), float(sz), 0.0)
    if isinstance(position_data, (tuple, list)) and len(position_data) >= 3:
        r = position_data[3] if len(position_data) > 3 else 0.0
        return tuple(float(v) for v in (*position_data[:3], r))
    raise WorkflowOverrideError(
        "The position input is a "
        f"{type(position_data).__name__}, not an object or (x, y, z[, r]); "
        "nothing was sent."
    )
