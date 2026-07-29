"""Single source of truth for assembling a workflow section dict.

Historically the workflow.txt section dict was hand-assembled in several places
that drifted apart (the Workflow tab, the tile-collection dialog, and two model
serializers). This module holds the ONE assembler they all route through, so the
transmitted format can never diverge again — the ``#4`` consolidation.

``build_workflow_section_dict`` is a pure function: it takes the per-section
inputs each caller already has (panel dicts, positions) and returns the nested
``{"Experiment Settings": ..., "Camera Settings": ..., "Stack Settings": ...}``
dict that ``TextFormatter.format_to_bytes`` serializes and sends. No Qt, no I/O —
so it is unit-testable and golden-file-testable in isolation.

Tiling note: the server reads ``Stack option settings 1/2`` as the X/Y OVERLAP
PERCENT (see ``CheckStackTile.cpp`` + ``WorkflowSettings.cpp`` getTileX/Y
OverlapPercent), NOT tile counts. This assembler owns that mapping so a caller
cannot get it wrong — tile counts stay client-side (estimate / tile_geometry).
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from ..models.data.workflow import WorkflowType

# Camera laser slots in the exact order/format the server file lists them.
_LASER_SLOTS = [
    (1, "405 nm"),
    (2, "488 nm"),
    (3, "561 nm"),
    (4, "640 nm"),
    (5, None),  # empty slot
    (6, None),
    (7, None),
]

# Workflow types whose Z scan defines the plane spacing (others report 1.0 um,
# matching the historical Workflow-tab behaviour).
_Z_SCANNING_TYPES = (
    WorkflowType.ZSTACK,
    WorkflowType.TILE,
    WorkflowType.MULTI_ANGLE,
)


def build_workflow_section_dict(
    *,
    workflow_type: WorkflowType,
    position_a: Any,
    position_b: Any,
    camera: Dict[str, Any],
    save: Dict[str, Any],
    illumination: Dict[str, Any],
    illumination_options: Dict[str, Any],
    stack: Dict[str, Any],
    plane_spacing_um: float,
    tiling: Optional[Dict[str, Any]] = None,
    timelapse: Optional[Dict[str, Any]] = None,
    multiangle: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the nested workflow.txt section dict.

    Args:
        workflow_type: which flow (drives End Position + per-type sections).
        position_a: start position (has ``.x/.y/.z/.r``).
        position_b: end/second position (has ``.x/.y/.z/.r``).
        camera: camera panel settings — needs ``exposure_us``, ``frame_rate``,
            ``aoi_width``, ``aoi_height``, ``cam{1,2}_capture_percentage``,
            ``cam{1,2}_capture_mode``.
        save: Experiment-Settings save fields (spread verbatim).
        illumination: Illumination Source dict.
        illumination_options: Illumination Options dict.
        stack: Stack Settings from the Z-stack panel (planes, Z change, velocity…).
        plane_spacing_um: Z plane spacing; emitted for Z-scanning types, else 1.0.
        tiling: for TILE — ``overlap_percent`` (and any extra Stack-option fields).
            Ignored for non-tile types.
        timelapse: for TIME_LAPSE — extra Experiment-Settings fields.
        multiangle: for MULTI_ANGLE — extra Experiment-Settings fields.

    Returns:
        Nested section dict for ``TextFormatter.format_to_bytes``.
    """
    experiment_settings: Dict[str, Any] = {
        **save,
        "Plane spacing (um)": (
            plane_spacing_um if workflow_type in _Z_SCANNING_TYPES else 1.0
        ),
        "Frame rate (f/s)": camera["frame_rate"],
        "Exposure time (us)": camera["exposure_us"],
    }
    if workflow_type == WorkflowType.TIME_LAPSE and timelapse:
        experiment_settings.update(timelapse)
    if workflow_type == WorkflowType.MULTI_ANGLE and multiangle:
        experiment_settings.update(multiangle)

    workflow_dict: Dict[str, Any] = {
        "Experiment Settings": experiment_settings,
        "Camera Settings": {
            "Exposure time (us)": camera["exposure_us"],
            "Frame rate (f/s)": camera["frame_rate"],
            "AOI width": camera["aoi_width"],
            "AOI height": camera["aoi_height"],
        },
        "Start Position": {
            "X (mm)": position_a.x,
            "Y (mm)": position_a.y,
            "Z (mm)": position_a.z,
            "Angle (degrees)": position_a.r,
        },
        "Illumination Source": illumination,
        "Illumination Options": illumination_options,
    }

    # Stack settings (copy so the caller's dict is never mutated).
    stack_dict: Dict[str, Any] = dict(stack)
    if workflow_type == WorkflowType.TILE:
        stack_dict.update(_tile_stack_fields(tiling))
    stack_dict["Camera 1 capture percentage"] = camera["cam1_capture_percentage"]
    stack_dict["Camera 1 capture mode"] = camera["cam1_capture_mode"]
    stack_dict["Camera 2 capture percentage"] = camera["cam2_capture_percentage"]
    stack_dict["Camera 2 capture mode"] = camera["cam2_capture_mode"]
    workflow_dict["Stack Settings"] = stack_dict

    workflow_dict["End Position"] = _end_position(workflow_type, position_a, position_b)
    return workflow_dict


def _tile_stack_fields(tiling: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Stack-option fields for a Tile workflow.

    Emits ``Stack option = Tile`` and X/Y ``Stack option settings 1/2`` as the
    OVERLAP PERCENT the server expects. Accepts either an explicit
    ``overlap_percent`` (preferred) or pre-built ``Stack option settings 1/2``
    values (back-compat), defaulting to 0 when neither is present.
    """
    tiling = tiling or {}
    overlap = tiling.get("overlap_percent")
    if overlap is None:
        overlap = tiling.get("Stack option settings 1", 0)
    fields = {
        "Stack option": "Tile",
        "Stack option settings 1": overlap,
        "Stack option settings 2": overlap,
    }
    # Preserve any additional caller-provided Stack-option keys (not the count
    # aliases we deliberately replace with overlap %).
    for k, v in tiling.items():
        if k not in (
            "overlap_percent",
            "Stack option settings 1",
            "Stack option settings 2",
            "Stack option",
        ):
            fields[k] = v
    return fields


def build_tile_illumination_source(
    illumination_list: List[Any], *, left_on: bool, right_on: bool
) -> Dict[str, str]:
    """Illumination Source dict for a tile-collection workflow.

    Lists all 7 laser slots (even disabled ones) + LED in the exact
    ``key = "power enabled"`` format the server expects, and folds the
    per-tile Left/Right path flags into the same dict (the formatter reads
    ``Left path``/``Right path`` from here and emits the Illumination Path
    section). ``left_on``/``right_on`` are the effective flags after any
    smart-limited-acquisition per-tile override.
    """
    enabled: Dict[str, float] = {}
    led = None
    for illum in illumination_list:
        if getattr(illum, "laser_enabled", False) and getattr(
            illum, "laser_channel", None
        ):
            enabled[illum.laser_channel] = illum.laser_power_mw
        if getattr(illum, "led_enabled", False):
            led = illum

    out: Dict[str, str] = {}
    for num, wavelength in _LASER_SLOTS:
        if wavelength:
            channel_key = f"Laser {num} {wavelength}"
            power = enabled.get(channel_key, 0.0)
            on = 1 if channel_key in enabled else 0
            out[f"Laser {num} {num}: {wavelength} MLE"] = f"{power:.2f} {on}"
        else:
            out[f"Laser {num}"] = "0.00 0"

    if led is not None:
        out["LED_RGB_Board"] = f"{led.led_intensity_percent:.2f} 1"
    else:
        out["LED_RGB_Board"] = "0.00 0"
    out["LED selection"] = "0 0"
    out["LED DAC"] = "42000 0"

    out["Left path"] = f"{'ON' if left_on else 'OFF'} {1 if left_on else 0}"
    out["Right path"] = f"{'ON' if right_on else 'OFF'} {1 if right_on else 0}"
    return out


def build_tile_collection_section_dict(
    *,
    name: str,
    position: Any,
    camera: Dict[str, Any],
    illumination_source: Dict[str, str],
    multi_laser: bool,
    save_settings: Dict[str, Any],
    z_min: float,
    z_max: float,
    is_zstack: bool,
    z_step_um: float,
    z_velocity_mm_s: float,
) -> Dict[str, Any]:
    """Section dict for one tile-collection workflow (Z-stack or snapshot/tile).

    Routes through :func:`build_workflow_section_dict` — the same serializer the
    Workflow tab uses — so the per-tile collection and the main tab can never
    diverge. Positions: for a Z-stack, Start Z = ``z_min`` and End Z = ``z_max``
    (X/Y/R shared); for a snapshot both Zs are the tile's Z.
    """
    if is_zstack:
        z_range_mm = z_max - z_min
        num_planes = max(1, int(z_range_mm / (z_step_um / 1000.0)) + 1)
        stack: Dict[str, Any] = {
            "Stack option": "ZStack",
            "Change in Z axis (mm)": z_range_mm,
            "Number of planes": num_planes,
            "Z stage velocity (mm/s)": z_velocity_mm_s,
        }
        plane_spacing = z_step_um
        start_z, end_z = z_min, z_max
        workflow_type = WorkflowType.ZSTACK
    else:
        stack = {
            "Stack option": "Snapshot",
            "Change in Z axis (mm)": 0.01,
            "Number of planes": 1,
            "Z stage velocity (mm/s)": 0.1,
        }
        plane_spacing = 1.0
        start_z, end_z = position.z, position.z
        workflow_type = WorkflowType.SNAPSHOT

    save = {
        "Sample": name,
        "Duration (dd:hh:mm:ss)": "00:00:00:00",
        "Interval (dd:hh:mm:ss)": "00:00:00:00",
        "Number of angles": "",
        "Angle step size": "",
        "Region": "",
        "Save image drive": save_settings["save_drive"],
        "Save image directory": save_settings["save_directory"],
        "Comments": "Tile collection workflow",
        "Save max projection": "true" if save_settings["save_mip"] else "false",
        "Display max projection": ("true" if save_settings["display_mip"] else "false"),
        "Save image data": (
            save_settings["save_format"]
            if save_settings["save_enabled"]
            else "NotSaved"
        ),
        "Save to subfolders": "false",
        "Work flow live view enabled": (
            "true" if save_settings["live_view"] else "false"
        ),
    }

    pos_a = SimpleNamespace(x=position.x, y=position.y, z=start_z, r=position.r)
    pos_b = SimpleNamespace(x=position.x, y=position.y, z=end_z, r=position.r)

    return build_workflow_section_dict(
        workflow_type=workflow_type,
        position_a=pos_a,
        position_b=pos_b,
        camera=camera,
        save=save,
        illumination=illumination_source,
        illumination_options={
            "Run stack with multiple lasers on": "true" if multi_laser else "false"
        },
        stack=stack,
        plane_spacing_um=plane_spacing,
    )


def _end_position(
    workflow_type: WorkflowType, position_a: Any, position_b: Any
) -> Dict[str, Any]:
    """Compute the End Position for a workflow type (per-type axis mixing)."""
    if workflow_type == WorkflowType.ZSTACK:
        # Z-Stack: X/Y/R from A, Z from B.
        return {
            "X (mm)": position_a.x,
            "Y (mm)": position_a.y,
            "Z (mm)": position_b.z,
            "Angle (degrees)": position_a.r,
        }
    if workflow_type == WorkflowType.TILE:
        # Tiling: X/Y/Z from B, R from A.
        return {
            "X (mm)": position_b.x,
            "Y (mm)": position_b.y,
            "Z (mm)": position_b.z,
            "Angle (degrees)": position_a.r,
        }
    if workflow_type == WorkflowType.MULTI_ANGLE:
        # Multi-Angle: X/Y/R from A, Z from B.
        return {
            "X (mm)": position_a.x,
            "Y (mm)": position_a.y,
            "Z (mm)": position_b.z,
            "Angle (degrees)": position_a.r,
        }
    # Snapshot, Time-Lapse: End == Start.
    return {
        "X (mm)": position_a.x,
        "Y (mm)": position_a.y,
        "Z (mm)": position_a.z,
        "Angle (degrees)": position_a.r,
    }
