"""Record what was actually collected, next to the data.

An acquisition folder currently arrives with nothing in it that says how it was
made. The per-tile ``Workflow.txt`` files the server writes describe one tile's
Z sweep and illumination, and nothing anywhere records the settings that
produced the *set*: the requested tile overlap, which objective and pixel size
the grid was computed from, which overview the tiles were picked off, or the
fact that the run was one of several in a batch.

That gap has a cost. A 97-tile brain acquisition was stitched with tiles
stepping a full field apart, giving 0.25% overlap where 20% had been asked for
— and there was no way, from the data on disk, to tell whether 20% had been
entered, whether it reached the tile-step calculation, or which field of view
it was applied to. This file is what makes that answerable afterwards.

Written as ``AcquisitionManifest.txt`` in the acquisition root (the parent of
the ``X..._Y...`` tile folders), in the same ``<Section>`` / ``key = value``
shape as ``Workflow.txt``, so it stays readable in Notepad **and** parses with
the existing ``utils.file_handlers.text_to_dict``.

Deliberately free of Qt and of service imports: it takes plain values and
returns text, so it is unit-testable and cannot fail an acquisition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

MANIFEST_FILENAME = "AcquisitionManifest.txt"

# Bump when the section layout changes in a way a reader would care about.
MANIFEST_VERSION = "1"

_UNKNOWN = "unknown"


@dataclass
class TargetingSource:
    """What picked these tiles.

    ``kind`` is the feature the user came from ("LED 2D Overview", "MIP
    Overview", "Webcam Overview", "Union Thresholder", "Manual"). ``path`` is
    the file or session that was on screen when the tiles were chosen — the
    thing you would have to re-open to understand why the grid looks the way it
    does. Recorded because without it a re-acquisition cannot be aimed at the
    same place.
    """

    kind: str = "Manual"
    path: Optional[str] = None
    detail: str = ""

    def as_section(self) -> Dict[str, Any]:
        section: Dict[str, Any] = {"Targeted from": self.kind}
        section["Source file"] = self.path or "(none recorded)"
        if self.detail:
            section["Detail"] = self.detail
        return section


@dataclass
class TileRecord:
    """One tile as it was requested. Server-side outcome is not known here."""

    index: int
    folder: str
    x_mm: float
    y_mm: float
    z_min_mm: float
    z_max_mm: float
    n_planes: Optional[int] = None
    angle_deg: Optional[float] = None
    illumination_sides: str = ""
    note: str = ""


@dataclass
class AcquisitionManifest:
    """Everything worth knowing about one acquisition, ready to render."""

    microscope: str = _UNKNOWN
    software_version: str = _UNKNOWN
    started: str = ""
    finished: str = ""
    acquisition_dir: str = ""
    save_drive: str = ""
    save_directory: str = ""
    targeting: TargetingSource = field(default_factory=TargetingSource)
    optics: Dict[str, Any] = field(default_factory=dict)
    camera: Dict[str, Any] = field(default_factory=dict)
    zstack: Dict[str, Any] = field(default_factory=dict)
    tiling: Dict[str, Any] = field(default_factory=dict)
    illumination: Dict[str, Any] = field(default_factory=dict)
    stage: Dict[str, Any] = field(default_factory=dict)
    batch: Dict[str, Any] = field(default_factory=dict)
    tiles: List[TileRecord] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def overlap_check(
    fov_mm: Optional[float],
    step_mm: Optional[float],
    requested_percent: Optional[float],
) -> Dict[str, Any]:
    """Requested vs. achieved overlap, and whether they agree.

    The single most valuable line in the manifest. The requested percentage and
    the step that actually got used are computed in different places from
    different field-of-view numbers, and when they disagree the data is
    unusable for registration while looking perfectly normal in every log.
    Stating both, plus the achieved percentage implied by the step, turns a
    silent mismatch into one readable line.
    """
    out: Dict[str, Any] = {
        "Requested overlap (%)": (
            _UNKNOWN
            if requested_percent is None
            else round(float(requested_percent), 2)
        ),
        "Field of view (mm)": _UNKNOWN if not fov_mm else round(float(fov_mm), 4),
        "Tile step (mm)": _UNKNOWN if not step_mm else round(float(step_mm), 4),
    }
    if not fov_mm or not step_mm or fov_mm <= 0:
        out["Achieved overlap (%)"] = _UNKNOWN
        return out

    achieved = (fov_mm - float(step_mm)) / float(fov_mm) * 100.0
    out["Achieved overlap (%)"] = round(achieved, 2)
    if requested_percent is None:
        return out

    # A percentage point of slack absorbs the 0.01 mm quantisation the tile
    # folder names impose on stage positions.
    if abs(achieved - float(requested_percent)) > 1.0:
        out["MISMATCH"] = (
            f"requested {float(requested_percent):.1f}% but the tile step gives "
            f"{achieved:.2f}%. The overlap did not reach the step calculation, "
            f"or a different field of view was used to compute it. Tiles with "
            f"under ~5% overlap cannot be registered."
        )
    return out


def _fmt(value: Any) -> str:
    if value is None:
        return _UNKNOWN
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _section_text(name: str, values: Dict[str, Any]) -> List[str]:
    lines = [f"<{name}>"]
    for key, value in values.items():
        lines.append(f"  {key} = {_fmt(value)}")
    lines.append(f"</{name}>")
    return lines


def format_manifest_text(manifest: AcquisitionManifest) -> str:
    """Render the manifest. Sections mirror Workflow.txt so both parse alike."""
    lines: List[str] = []
    lines.append("# Flamingo acquisition manifest")
    lines.append("# What was collected, and the settings that produced it.")
    lines.append("# Written by Py2Flamingo when the acquisition finished.")
    lines.append("")

    lines += _section_text(
        "Acquisition",
        {
            "Manifest version": MANIFEST_VERSION,
            "Microscope": manifest.microscope,
            "Software version": manifest.software_version,
            "Started": manifest.started or _UNKNOWN,
            "Finished": manifest.finished or _UNKNOWN,
            "Acquisition folder": manifest.acquisition_dir or _UNKNOWN,
            "Save drive": manifest.save_drive or _UNKNOWN,
            "Save directory": manifest.save_directory or _UNKNOWN,
            "Tiles requested": len(manifest.tiles),
        },
    )
    lines.append("")

    lines += _section_text("Targeting", manifest.targeting.as_section())
    lines.append("")

    for name, values in (
        ("Optics", manifest.optics),
        ("Camera", manifest.camera),
        ("Z Stack", manifest.zstack),
        ("Tiling", manifest.tiling),
        ("Illumination", manifest.illumination),
        ("Stage", manifest.stage),
        ("Batch", manifest.batch),
    ):
        if values:
            lines += _section_text(name, values)
            lines.append("")

    if manifest.tiles:
        lines.append("<Tiles>")
        lines.append(
            "  # index  folder                 X (mm)   Y (mm)   "
            "Z start   Z end     planes  angle  arms"
        )
        for tile in manifest.tiles:
            lines.append(
                "  {idx:<5d}  {folder:<20s}  {x:>7.3f}  {y:>7.3f}  "
                "{z0:>7.3f}  {z1:>7.3f}  {n:>6s}  {a:>5s}  {arms}{note}".format(
                    idx=tile.index,
                    folder=tile.folder[:20],
                    x=tile.x_mm,
                    y=tile.y_mm,
                    z0=tile.z_min_mm,
                    z1=tile.z_max_mm,
                    n="?" if tile.n_planes is None else str(tile.n_planes),
                    a="?" if tile.angle_deg is None else f"{tile.angle_deg:g}",
                    arms=tile.illumination_sides or "-",
                    note=f"  # {tile.note}" if tile.note else "",
                )
            )
        lines.append("</Tiles>")
        lines.append("")

    if manifest.warnings:
        lines.append("<Warnings>")
        for warning in manifest.warnings:
            lines.append(f"  ! {warning}")
        lines.append("</Warnings>")
        lines.append("")

    if manifest.notes:
        lines.append("<Notes>")
        for note in manifest.notes:
            lines.append(f"  {note}")
        lines.append("</Notes>")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_manifest(
    folder, manifest: AcquisitionManifest, *, logger=None
) -> Optional[Path]:
    """Write ``AcquisitionManifest.txt`` into `folder`. Never raises.

    Returns the path written, or None. A manifest is a record of a run that has
    already succeeded — it must never be the thing that reports a failure.
    """
    try:
        from py2flamingo.utils.file_handlers import safe_write

        path = Path(folder) / MANIFEST_FILENAME
        safe_write(path, format_manifest_text(manifest), newline="\n")
        if logger is not None:
            logger.info(f"Wrote acquisition manifest: {path}")
        return path
    except Exception as exc:  # pragma: no cover - defensive
        if logger is not None:
            logger.warning(f"Could not write acquisition manifest: {exc}")
        return None
