# Sample Chamber Profiles (3D Viewer)

The 3D viewer draws the sample chamber from a **CAD-derived description**, not
from the STEP file directly. This document explains how to **switch** between
chambers and how to **create a new chamber profile** — for example when a
different objective requires a different chamber.

> You don't need CAD software or any special tools to *switch* chambers — only
> to *create* a new profile.

---

## 1. How it works

```
chamber.STEP  ──▶  extract_step_chamber.py  ──▶  <chamber>.yaml  ──▶  3D viewer
   (CAD)            (run once, offline)         (features file)      (loads YAML)
```

The viewer never reads the STEP file. It reads a **features YAML** — a
hand-editable description of the chamber's walls, ports, and bolt holes, plus a
transform that places the CAD geometry into the microscope's stage coordinates.

Each features YAML is one **chamber profile**. Profiles live in:

- `src/py2flamingo/configs/step_chamber_features.yaml` — the shipped default.
- `src/py2flamingo/configs/chambers/*.yaml` — additional profiles you add.

---

## 2. Switching chambers (no restart)

1. Open **Sample View**.
2. Open **Viewer Controls** → **STEP Chamber Geometry**.
3. Use the **Chamber profile** dropdown to pick a chamber.

The 3D chamber rebuilds immediately. Your choice is remembered between
sessions. The dropdown lists the shipped default plus every YAML found in
`configs/chambers/`.

If a profile fails to load you'll get a warning dialog; the viewer may need an
app restart to fully recover.

---

## 3. Creating a new chamber profile

You need the chamber's **STEP file** and its **focal-plane stage coordinates**
(the "Tip of sample mount" calibration position for that objective).

### Step 3.1 — Extract geometry from the STEP file

```bash
python3 scripts/extract_step_chamber.py <chamber.STEP> configs/chambers/<name>.yaml
```

This parses the CAD and writes a starter features YAML. It prints every feature
it found — check that against the physical part, because detection is
heuristic and tuned to the 25× chamber.

### Step 3.2 — Calibrate the transform (the important step)

The extractor's `step_to_stage_transform` offsets are **placeholders**. They
must be recalibrated so the chamber sits correctly in stage space. See
[§5](#5-the-step_to_stage_transform) for the procedure.

### Step 3.3 — Hand-edit and label

Review the feature list (see the [field reference](#6-feature-fields)), fix any
mis-detected ports, and add a `display_name:` at the top of the file so the
dropdown shows a friendly name.

### Step 3.4 — Use it

Save the file in `configs/chambers/`. It appears in the **Chamber profile**
dropdown automatically — no restart, no config edit.

> **Re-running the extractor overwrites the file.** Keep a note of your
> hand-edits (recalibrated offsets, corrected ports) and re-apply them, or
> extract to a temporary file and merge.

---

## 4. Top-level YAML fields

```yaml
display_name: "40x ASLM chamber"          # dropdown label (optional)
source_step_file: "/path/to/chamber.STEP" # provenance only (optional)
units: mm                                 # informational
frame: step                               # informational
axis_mapping_doc: "file_x = ... "          # human note (optional)
step_to_stage_transform: { ... }           # REQUIRED — see §5
features: [ ... ]                          # REQUIRED — see §6
```

| Field | Required | Purpose |
|---|---|---|
| `display_name` | no | Label shown in the Chamber profile dropdown. Falls back to the filename. |
| `source_step_file` | no | Records which STEP file this was extracted from. Not used by the viewer. |
| `units` / `frame` | no | Informational. All coordinates are millimetres in CAD ("file") frame. |
| `axis_mapping_doc` | no | Free-text reminder of what each file axis means physically. |
| `step_to_stage_transform` | **yes** | Maps CAD coordinates to microscope stage coordinates. |
| `features` | **yes** | List of chamber parts to draw (walls, ports, holes). |

Any field whose name starts with `_` (e.g. `_note`) is a comment — the viewer
ignores it. `todo:` is also treated as a note.

---

## 5. The `step_to_stage_transform`

The STEP file uses arbitrary CAD axes (`file_x`, `file_y`, `file_z`). The viewer
works in **stage frame** (`stage_x` = left/right, `stage_y` = vertical,
`stage_z` = depth). The transform converts one to the other:

```
stage_axis = sign × (chosen file-axis value) + offset_mm
```

```yaml
step_to_stage_transform:
  axis_permutation:        # which file axis feeds each stage axis
    stage_x: file_x
    stage_y: file_z
    stage_z: file_y
  sign:                    # +1 or -1 per stage axis
    stage_x: 1
    stage_y: 1
    stage_z: 1
  offset_mm:               # millimetre shift per stage axis
    stage_x: -127.475
    stage_y: -671.50
    stage_z: 207.20
```

| Field | Meaning |
|---|---|
| `axis_permutation` | Maps each `stage_*` axis to a `file_x` / `file_y` / `file_z`. Needed because CAD and stage axes rarely line up. |
| `sign` | `+1` or `-1` per stage axis, to flip a direction. |
| `offset_mm` | Translation (mm) applied after permutation + sign. |

### Calibrating the offsets

The offsets are chosen so the **cavity centre** lands on the **focal plane**:

1. Find the cavity centroid in CAD frame = the midpoints of the
   `chamber_cavity` feature's `bounds_step` x/y/z.
2. Get the focal-plane stage coordinates for this chamber/objective — the
   "Tip of sample mount" calibration position (mm).
3. For each stage axis, solve:

   ```
   offset = focal_plane_stage_value − sign × (file value feeding that axis)
   ```

**Worked example (25× chamber)** — cavity centroid `(134.125, −187.95, 678.50)`,
focal plane `(6.655, 7.0, 19.25)`, permutation as above:

| Stage axis | Calculation | offset_mm |
|---|---|---|
| `stage_x` | `6.655 − 134.125` | `−127.475` |
| `stage_y` | `7.0 − 678.50` | `−671.50` |
| `stage_z` | `19.25 − (−187.95)` | `207.20` |

This makes data acquired against the tip-test reference land at the cavity
centre with no migration. If the chamber renders far off-screen after a swap,
the offsets are wrong — recheck this step.

---

## 6. Feature fields

`features:` is a list. Each entry describes one chamber part. The viewer draws
it as one or more napari layers.

### Common fields

| Field | Required | Meaning |
|---|---|---|
| `role` | **yes** | Identifier for the part. Some roles get special handling — see [§7](#7-recognised-roles). |
| `type` | **yes** | `aabb` (axis-aligned box) or `cylinder`. |
| `layer_name` | to render | The napari layer name. **Omit it and the feature is treated as an internal helper and not drawn** (e.g. the cavity volume used only for hole-punching). |
| `visible_default` | no | `true` / `false` — whether the layer starts visible. |
| `color` | no | Hex colour, e.g. `"#00FF88"`. |
| `opacity` | no | `0.0`–`1.0`. |
| `napari_kind` | no | `surface` or `shapes`. Informational; the renderer is chosen by `type`. |

### Box features (`type: aabb`)

```yaml
- role: chamber_outer_box
  type: aabb
  bounds_step:
    x: [118.28, 149.98]   # [min, max] in CAD frame, mm
    y: [-210.04, -167.02]
    z: [656.50, 700.50]
  layer_name: "STEP Chamber Bulk"
  visible_default: false
```

| Field | Meaning |
|---|---|
| `bounds_step` | `x` / `y` / `z`, each `[min, max]` in CAD-frame millimetres. |

### Cylinder features (`type: cylinder`)

```yaml
- role: detection_objective_port
  type: cylinder
  axis: [0, -1, 0]                 # direction in CAD frame
  center_step: [134.13, -218.39, 680.65]
  radius_mm: 16.5
  y_extent_step: [-218.39, -206.39] # length along the axis
  layer_name: "STEP Detection Objective"
```

| Field | Meaning |
|---|---|
| `axis` | `[x, y, z]` unit vector for the cylinder's axis, in CAD frame. |
| `center_step` | `[x, y, z]` centre point, CAD-frame mm. |
| `radius_mm` | Bore radius. |
| `x_extent_step` / `y_extent_step` / `z_extent_step` | Optional `[start, end]` giving the cylinder's length along its axis. If absent, a 12 mm default is used. |

### Optional rendering overrides

| Field | Meaning |
|---|---|
| `display_as: rectangle` | Draw a cylinder feature as a flat rectangle outline instead of rings — for ports that are rectangular in the real chamber. Requires `rect_extents_step`. |
| `rect_extents_step` | Extents of the rectangle, keyed by axis (used with `display_as: rectangle`). |
| `real_world_shape` | Free-text note of the true shape (e.g. `rounded_rectangle`). |
| `real_world_override` | `{enabled: true, ...}` — draw a rounded-rectangle (stadium) slot instead of a circle. Leave `enabled: false` to keep the circular CAD shape. |

---

## 7. Recognised roles

Most `role` values are free identifiers, but these are recognised specially —
the viewer's per-feature visibility checkboxes key on them, and `chamber_cavity`
gets a dedicated multi-layer render (interior walls + back + bottom):

| Role | What it is |
|---|---|
| `chamber_cavity` | Interior air cavity. Rendered as wireframe + back/bottom walls. Used for holder-clearance checks. |
| `chamber_outer_box` | Solid chamber bulk. Hidden by default (it occludes the sample). |
| `detection_objective_port` | Detection objective bore on the back face. |
| `sample_entry_port` | Front sample-entry / viewing port. |
| `illumination_port_left`, `illumination_port_right` | Light-sheet illumination bores. |
| `sample_entry_top_hole` | Top drop-in hole. Often a placeholder — edit or hide it to match the real chamber. |
| `rail_mount_bolt_left`, `rail_mount_bolt_right` | Mounting bolt holes. Hidden by default. |

A profile may add other features with custom roles; they render normally as
long as they have a `layer_name`.

---

## 8. What a profile does *not* carry

A chamber profile covers **geometry and the stage transform only**. These
remain global in `src/py2flamingo/configs/visualization_3d_config.yaml` and must
be changed there if a new chamber needs different values:

- `stage_control.x/y/z_range_mm` — the napari display volume.
- `sample_chamber.holder_diameter_mm`, `fep_tube_diameter_mm`,
  `fep_tube_length_mm` — sample-holder hardware dimensions. **These have no
  defaults** — a missing value raises a clear error. Set them to the installed
  holder, never guess.
- `step_chamber.collision` — the holder-vs-chamber collision safety gate.

---

## 9. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Chamber renders far away / off-screen after a swap | `step_to_stage_transform.offset_mm` not recalibrated — see §5. |
| A key/port moves to the wrong side | Wrong `axis_permutation` or `sign`. |
| Profile missing from the dropdown | File not in `configs/chambers/`, or not a `.yaml`, or failed to parse. |
| "Could not load chamber profile" warning | YAML is malformed or missing `step_to_stage_transform` / `features`. |
| A feature doesn't appear | No `layer_name`, or `visible_default: false`, or its toggle is off in Viewer Controls. |
| Hand-edits disappeared | The extractor was re-run and overwrote the file — re-apply edits. |

For the full extraction internals and axis conventions, see the docstring of
`scripts/extract_step_chamber.py`.
