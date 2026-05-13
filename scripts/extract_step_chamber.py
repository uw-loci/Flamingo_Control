#!/usr/bin/env python3
"""Extract chamber geometry from a STEP file into a feature YAML.

Reads a SolidWorks AP203 STEP file (e.g. the LSControl 25x ASLM chamber CAD),
parses the entity graph, identifies the chamber's outer body, the detection
objective port, sample-entry / front port, illumination ports, and mounting
bolt holes, and writes them to a hand-editable YAML the runtime can load.

Stdlib only — no third-party deps. Idempotent: re-run when the STEP file is
updated.

Real-world axis mapping (per user, see the plan file):
    file +Z = real-world UP (vertical)
    file  Y = real-world FRONT-BACK (optical axis through sample;
              +Y = front sample-entry side, -Y = back detector side)
    file ±X = real-world LEFT-RIGHT (illumination axis)

Usage:
    python scripts/extract_step_chamber.py <input.STEP> <output.yaml>
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

_NUM_RE = re.compile(r"(?<!#)(-?\d+\.\d+(?:[eE][+-]?\d+)?)")
_ENT_RE = re.compile(r"^#(\d+)\s*=\s*(.*)$", re.S)
_POINT_RE = re.compile(
    r"\(\s*(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s*,\s*"
    r"(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s*,\s*"
    r"(-?\d+\.?\d*(?:[eE][+-]?\d+)?)\s*\)"
)


def parse_step(path: Path) -> dict[int, str]:
    """Parse a STEP file into {entity_id: body_text}."""
    text = path.read_text()
    ent: dict[int, str] = {}
    for stmt in (s.strip() for s in text.split(";") if s.strip().startswith("#")):
        m = _ENT_RE.match(stmt)
        if m:
            ent[int(m.group(1))] = m.group(2).strip()
    return ent


def parse_point(body: str | None) -> tuple[float, float, float] | None:
    if not body:
        return None
    m = _POINT_RE.search(body)
    if not m:
        return None
    return tuple(float(g) for g in m.groups())


def parse_refs(body: str) -> list[int]:
    return [int(x) for x in re.findall(r"#(\d+)", body)]


def parse_axis(ent: dict[int, str], aid: int):
    """Resolve an AXIS2_PLACEMENT_3D into (origin, axis_dir)."""
    body = ent.get(aid, "")
    if not body.startswith("AXIS2_PLACEMENT_3D"):
        return None
    refs = parse_refs(body)
    if len(refs) < 2:
        return None
    origin = parse_point(ent.get(refs[0], ""))
    axis = parse_point(ent.get(refs[1], ""))
    return (origin, axis)


def collect_cylinders(ent: dict[int, str]):
    """Yield (eid, origin, axis, radius) for each CYLINDRICAL_SURFACE."""
    for eid, body in ent.items():
        if not body.startswith("CYLINDRICAL_SURFACE"):
            continue
        refs = parse_refs(body)
        nums = _NUM_RE.findall(body)
        if not refs or not nums:
            continue
        radius = float(nums[-1])
        a = parse_axis(ent, refs[0])
        if not a or a[0] is None or a[1] is None:
            continue
        yield eid, a[0], a[1], radius


def collect_planes(ent: dict[int, str]):
    """Yield (eid, origin, normal) for each PLANE."""
    for eid, body in ent.items():
        if not body.startswith("PLANE"):
            continue
        refs = parse_refs(body)
        if not refs:
            continue
        a = parse_axis(ent, refs[0])
        if not a or a[0] is None or a[1] is None:
            continue
        yield eid, a[0], a[1]


def cartesian_bbox(ent: dict[int, str]):
    """Bounding box across all CARTESIAN_POINT entries."""
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for body in ent.values():
        if body.startswith("CARTESIAN_POINT"):
            p = parse_point(body)
            if p:
                xs.append(p[0])
                ys.append(p[1])
                zs.append(p[2])
    if not xs:
        return None
    return (
        (min(xs), max(xs)),
        (min(ys), max(ys)),
        (min(zs), max(zs)),
    )


def chamber_body_bbox(ent: dict[int, str], planes: list):
    """Compute the chamber outer body bbox from axis-aligned outer planes.

    The dense-cluster percentile approach picks up the cavity interior, not the
    outer walls. Instead, for each principal axis we take all axis-aligned
    planes (normal close to ±X/±Y/±Z) and use the extreme positions on each
    side. Planes at degenerate origins (X=0, Y=0, or Z=0 — typically thread
    placements) are excluded.
    """
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for eid, origin, normal in planes:
        ai, sign = snap_axis(normal)
        # Only axis-aligned planes contribute. Skip planes whose origin sits at
        # an exact zero on a non-aligned axis (these are SolidWorks part-origin
        # placements for screw threads, far outside the chamber body).
        x, y, z = origin
        if ai == 0:
            # X-aligned plane; take its X coordinate
            if abs(y) < 1e-3 and abs(z) < 1e-3:
                continue
            xs.append(x)
        elif ai == 1:
            if abs(x) < 1e-3 and abs(z) < 1e-3:
                continue
            ys.append(y)
        else:
            if abs(x) < 1e-3 and abs(y) < 1e-3:
                continue
            zs.append(z)
    if not xs or not ys or not zs:
        return None
    return (
        (min(xs), max(xs)),
        (min(ys), max(ys)),
        (min(zs), max(zs)),
    )


def snap_axis(axis: tuple[float, float, float]) -> tuple[int, int]:
    """Snap an axis vector to the nearest principal direction.

    Returns (axis_index, sign). axis_index is 0=X, 1=Y, 2=Z.
    """
    abs_components = [abs(c) for c in axis]
    idx = max(range(3), key=lambda i: abs_components[i])
    sign = 1 if axis[idx] >= 0 else -1
    return idx, sign


def find_named_feature(
    cyls: list,
    axis_index: int,
    near_y: float | None = None,
    radius_target: float | None = None,
    radius_tol: float = 0.5,
) -> dict | None:
    """Search the cylinder list for one matching constraints."""
    best = None
    best_score = float("inf")
    for eid, origin, axis, r in cyls:
        ai, sign = snap_axis(axis)
        if ai != axis_index:
            continue
        if radius_target is not None and abs(r - radius_target) > radius_tol:
            continue
        if near_y is not None:
            score = abs(origin[1] - near_y)
            if score < best_score:
                best_score = score
                best = (eid, origin, axis, r)
        else:
            return {"eid": eid, "origin": origin, "axis": axis, "radius": r}
    if best is None:
        return None
    eid, origin, axis, r = best
    return {"eid": eid, "origin": origin, "axis": axis, "radius": r}


def find_inner_pocket_corners(planes: list) -> dict:
    """Look for the 4-corner pattern of a rectangular inner pocket on ±X faces.

    The chamber has square pockets on the inner ±X faces (window seats around
    the illumination ports). Each pocket is bounded by 4 X-normal planes
    sharing the same X coordinate and bounded YZ-corners.
    """
    by_x: dict[float, list] = defaultdict(list)
    for eid, origin, normal in planes:
        ai, sign = snap_axis(normal)
        if ai != 0:  # X-normal only
            continue
        x = round(origin[0], 2)
        by_x[x].append(origin)

    pockets = {}
    for x, points in by_x.items():
        if len(points) < 4:
            continue
        ys = sorted({round(p[1], 2) for p in points})
        zs = sorted({round(p[2], 2) for p in points})
        if len(ys) < 2 or len(zs) < 2:
            continue
        # Look for the inner-pocket pattern: small Y-Z bounding rectangle
        # with the 4 corners present
        corners_present = sum(
            1
            for p in points
            if round(p[1], 2) in (ys[0], ys[-1]) and round(p[2], 2) in (zs[0], zs[-1])
        )
        if corners_present >= 4:
            pockets[x] = {
                "y": [ys[0], ys[-1]],
                "z": [zs[0], zs[-1]],
            }
    return pockets


def yaml_emit(value, indent: int = 0) -> Iterable[str]:
    """Minimal YAML serializer for our structured output (stdlib-only)."""
    pad = "  " * indent
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                yield f"{pad}{k}:"
                yield from yaml_emit(v, indent + 1)
            else:
                yield f"{pad}{k}: {format_scalar(v)}"
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                yield f"{pad}-"
                yield from yaml_emit(item, indent + 1)
            else:
                yield f"{pad}- {format_scalar(item)}"
    else:
        yield f"{pad}{format_scalar(value)}"


def format_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".") or "0"
    if isinstance(v, str):
        # Quote strings that contain YAML-significant chars, leading/trailing
        # whitespace, or path-like characters that PyYAML may mis-parse.
        unsafe = set(":#&*!|>'\"%@`,{}[]")
        if (
            v != v.strip()
            or any(c in unsafe for c in v)
            or " " in v
            or v == ""
            or v in ("null", "true", "false", "yes", "no", "on", "off")
        ):
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return v
    return str(v)


def build_features_yaml(step_path: Path, ent: dict[int, str]) -> dict:
    """Construct the YAML data structure for the chamber features."""
    cyls = list(collect_cylinders(ent))
    planes = list(collect_planes(ent))
    pockets = find_inner_pocket_corners(planes)
    body_bbox = chamber_body_bbox(ent, planes)

    # Detection objective: largest Y-axis cylinder (r ≥ 15) at the most negative Y
    detector = None
    candidates = [
        (eid, o, ax, r) for eid, o, ax, r in cyls if snap_axis(ax)[0] == 1 and r >= 15.0
    ]
    if candidates:
        # Pick the most negative Y location (BACK end)
        candidates.sort(key=lambda c: c[1][1])
        detector = candidates[0]

    # Sample-entry / front port: Y-axis cylinder (small radius) at the most positive Y
    front = None
    candidates_front = [
        (eid, o, ax, r)
        for eid, o, ax, r in cyls
        if snap_axis(ax)[0] == 1 and 5.0 < r < 12.0
    ]
    if candidates_front:
        # Pick the most positive Y location (FRONT end, near the cavity floor)
        candidates_front.sort(key=lambda c: -c[1][1])
        front = candidates_front[0]

    # Illumination ports: ±X axis, r=10
    illum_ports = []
    for eid, o, ax, r in cyls:
        ai, sign = snap_axis(ax)
        if ai == 0 and 9.5 < r < 10.5:
            illum_ports.append((sign, eid, o, ax, r))
    illum_ports.sort(key=lambda x: x[2][0])  # by X coordinate

    illum_left = illum_ports[0] if len(illum_ports) >= 1 else None
    illum_right = illum_ports[-1] if len(illum_ports) >= 2 else None

    # Mounting bolts: Z-axis cylinders at low Z, r=4
    bolts = [
        (eid, o, ax, r)
        for eid, o, ax, r in cyls
        if snap_axis(ax)[0] == 2 and 3.5 < r < 4.5 and o[2] < 700
    ]
    bolts.sort(key=lambda b: b[1][0])  # by X
    bolt_left = bolts[0] if bolts else None
    bolt_right = bolts[-1] if len(bolts) > 1 else None

    # Build feature list
    features = []

    # Outer chamber bulk (rendered as solid metal walls; toggle off by default)
    if body_bbox:
        features.append(
            {
                "role": "chamber_outer_box",
                "type": "aabb",
                "bounds_step": {
                    "x": [round(body_bbox[0][0], 2), round(body_bbox[0][1], 2)],
                    "y": [round(body_bbox[1][0], 2), round(body_bbox[1][1], 2)],
                    "z": [round(body_bbox[2][0], 2), round(body_bbox[2][1], 2)],
                },
                "layer_name": "STEP Chamber Bulk",
                "visible_default": False,
                "napari_kind": "surface",
                "color": "#888888",
                "opacity": 0.25,
            }
        )

    # Cavity AABB (used to subtract from bulk; not rendered itself)
    cavity_x_left = min(pockets) if pockets else None
    cavity_x_right = max(pockets) if pockets else None
    if cavity_x_left is not None and cavity_x_right is not None:
        # Use one of the pockets for Y/Z extents (they're symmetric)
        ref = pockets[cavity_x_left]
        features.append(
            {
                "role": "chamber_cavity",
                "type": "aabb",
                "bounds_step": {
                    "x": [cavity_x_left, cavity_x_right],
                    "y": ref["y"],
                    "z": ref["z"],
                },
                "_note": "subtractive volume; defines air gap. Not rendered.",
            }
        )

    if detector:
        eid, o, ax, r = detector
        features.append(
            {
                "role": "detection_objective_port",
                "type": "cylinder",
                "axis": [0, -1, 0],
                "center_step": [round(o[0], 2), round(o[1], 2), round(o[2], 2)],
                "radius_mm": round(r, 2),
                "y_extent_step": [round(o[1], 2), round(o[1] + 12, 2)],
                "layer_name": "STEP Detection Objective",
                "visible_default": True,
                "napari_kind": "shapes",
                "color": "#00FF88",
                "_note": (
                    f"33 mm dia detector port (r={round(r, 2)} mm) on the BACK"
                    " face. Real chamber may have a counterbore + lens-mount"
                    " sleeve protruding outward."
                ),
            }
        )

    if front:
        eid, o, ax, r = front
        features.append(
            {
                "role": "sample_entry_port",
                "type": "cylinder",
                "axis": [0, 1, 0],
                "center_step": [round(o[0], 2), round(o[1], 2), round(o[2], 2)],
                "radius_mm": round(r, 2),
                "y_extent_step": [round(o[1], 2), round(o[1] - 12, 2)],
                "layer_name": "STEP Sample-Entry / Front Port",
                "visible_default": True,
                "napari_kind": "shapes",
                "color": "#FF8800",
                "real_world_shape": "rounded_rectangle",
                "real_world_override": {
                    "enabled": False,
                    "slot_x_extent_mm": [None, None],
                    "slot_z_extent_mm": [None, None],
                },
                "_note": (
                    "FRONT face port for sample insertion / viewing."
                    " Production chamber has a rounded-rectangle slot here."
                    " Set real_world_override.enabled to true and fill in the"
                    " slot extents to override."
                ),
            }
        )

    if illum_left:
        sign, eid, o, ax, r = illum_left
        x = round(o[0], 2)
        # Find the matching pocket (innermost X face)
        pocket = (
            pockets.get(min(pockets.keys(), key=lambda k: abs(k - x)))
            if pockets
            else None
        )
        features.append(
            {
                "role": "illumination_port_left",
                "type": "cylinder",
                "axis": [-1, 0, 0],
                "center_step": [x, round(o[1], 2), round(o[2], 2)],
                "radius_mm": round(r, 2),
                "pocket_inner_face_x_step": min(pockets.keys()) if pockets else None,
                "pocket_yz_extent_step": pocket if pocket else None,
                "layer_name": "STEP Illumination Port (left)",
                "visible_default": True,
                "napari_kind": "shapes",
                "color": "#FFD700",
            }
        )

    if illum_right:
        sign, eid, o, ax, r = illum_right
        x = round(o[0], 2)
        pocket = (
            pockets.get(max(pockets.keys(), key=lambda k: abs(k - x)))
            if pockets
            else None
        )
        features.append(
            {
                "role": "illumination_port_right",
                "type": "cylinder",
                "axis": [1, 0, 0],
                "center_step": [x, round(o[1], 2), round(o[2], 2)],
                "radius_mm": round(r, 2),
                "pocket_inner_face_x_step": max(pockets.keys()) if pockets else None,
                "pocket_yz_extent_step": pocket if pocket else None,
                "layer_name": "STEP Illumination Port (right)",
                "visible_default": True,
                "napari_kind": "shapes",
                "color": "#FFD700",
            }
        )

    if bolt_left:
        eid, o, ax, r = bolt_left
        features.append(
            {
                "role": "rail_mount_bolt_left",
                "type": "cylinder",
                "axis": [0, 0, 1],
                "center_step": [round(o[0], 2), round(o[1], 2), round(o[2], 2)],
                "radius_mm": round(r, 2),
                "layer_name": "STEP Rail Bolt (left)",
                "visible_default": False,
                "napari_kind": "shapes",
                "color": "#666666",
            }
        )

    if bolt_right:
        eid, o, ax, r = bolt_right
        features.append(
            {
                "role": "rail_mount_bolt_right",
                "type": "cylinder",
                "axis": [0, 0, 1],
                "center_step": [round(o[0], 2), round(o[1], 2), round(o[2], 2)],
                "radius_mm": round(r, 2),
                "layer_name": "STEP Rail Bolt (right)",
                "visible_default": False,
                "napari_kind": "shapes",
                "color": "#666666",
            }
        )

    # USER-EDITABLE PLACEHOLDER: top sample-entry hole (vertical drop-in).
    # This is NOT in the 'basic windows' STEP file; emitted here so re-running
    # the extractor doesn't drop the user's customizations. Adjust radius_mm /
    # center_step to match the production chamber.
    if body_bbox:
        x_center = round((body_bbox[0][0] + body_bbox[0][1]) / 2 + 2, 2)
        y_center = round((body_bbox[1][0] + body_bbox[1][1]) / 2 + 8, 2)
        z_top = round(body_bbox[2][1], 2)
        features.append(
            {
                "role": "sample_entry_top_hole",
                "type": "cylinder",
                "axis": [0, 0, 1],
                "center_step": [x_center, y_center, z_top],
                "radius_mm": 10.0,
                "layer_name": "STEP Top Sample-Entry Hole",
                "visible_default": True,
                "napari_kind": "shapes",
                "color": "#FF4400",
                "todo": (
                    "Not present in 'basic windows' STEP file. Edit "
                    "radius_mm / center to match the production chamber's "
                    "top sample-entry hole, or set visible_default: false."
                ),
            }
        )

    return {
        "source_step_file": str(step_path),
        "units": "mm",
        "frame": "step",
        "axis_mapping_doc": (
            "file_x = real-world LEFT-RIGHT (illumination axis); "
            "file_y = real-world FRONT-BACK (optical axis: +Y front, -Y back); "
            "file_z = real-world UP (vertical)"
        ),
        "step_to_stage_transform": {
            # Detection objective lands at stage_z=12.5 (existing rectangular
            # Yellow Objective position). Sample-entry / front port lands at
            # stage_z>26, off the rectangular front. Adjust offsets if the
            # production CAD changes the detector's file_Y position.
            "axis_permutation": {
                "stage_x": "file_x",
                "stage_y": "file_z",
                "stage_z": "file_y",
            },
            "sign": {"stage_x": 1, "stage_y": 1, "stage_z": 1},
            "offset_mm": {
                "stage_x": -127.475,
                "stage_y": -674.02,
                "stage_z": 230.89,
            },
        },
        "features": features,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step_path", help="Input STEP file path")
    parser.add_argument("yaml_path", help="Output YAML file path")
    args = parser.parse_args()

    step_path = Path(args.step_path)
    yaml_path = Path(args.yaml_path)

    if not step_path.exists():
        print(f"ERROR: STEP file not found: {step_path}", file=sys.stderr)
        return 2

    print(f"Parsing STEP file: {step_path}")
    ent = parse_step(step_path)
    print(f"  {len(ent)} entities")

    data = build_features_yaml(step_path, ent)
    n_feat = len(data["features"])
    print(f"  {n_feat} chamber features extracted")
    for f in data["features"]:
        if "radius_mm" in f:
            print(
                f"    - {f['role']}: r={f['radius_mm']} mm @ "
                f"{f.get('center_step', f.get('bounds_step'))}"
            )
        else:
            print(f"    - {f['role']}: {f['type']} @ {f.get('bounds_step')}")

    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# STEP chamber features YAML — generated by extract_step_chamber.py\n"
        "# Hand-editable. Re-run the extractor to regenerate after a STEP update.\n"
        "# Fields prefixed with _ are notes; the runtime ignores them.\n"
        "\n"
    )
    with yaml_path.open("w") as f:
        f.write(header)
        for line in yaml_emit(data):
            f.write(line + "\n")

    print(f"\nWrote: {yaml_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
