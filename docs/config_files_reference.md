# Configuration and settings files — what exists, what each does, what multiplies

Written 2026-08-10 after `saved_configurations.json` was deleted as "runtime state"
on the strength of its filename. It held the only saved route to the microscope and
the rig could not connect. This document exists so that classification never has to
be guessed again.

Two questions it answers:

1. **What is each file for, and what breaks without it?**
2. **If you connect a second microscope, which files do you gain another of, and
   which stay singular?**

---

## 1. The root: `ScopeSettings.txt` selects everything else

Nothing here is chosen by a config setting. The chain is:

```
connect to microscope
      └─> the scope sends its settings; the client writes them to
          microscope_settings/ScopeSettings.txt
                └─> <Type> ... Microscope name = n7
                          └─> selects microscope_settings/n7_settings.json
                          └─> selects microscope_settings/n7_start_position.txt
```

`ConfigurationService.get_microscope_name()` reads `scope_settings["Type"]["Microscope
name"]`, defaulting to `"default"`. Every per-microscope filename is an f-string built
from that value.

**Consequence for multi-scope:** `ScopeSettings.txt` is a *single* file that is
overwritten by whichever microscope you last connected to. You do not maintain one per
scope — the scope supplies it. What you maintain per scope is the `{name}_*` files it
points at.

`ScopeSettings.txt` also carries `Microscope address = 10.129.37.22 53717`, which is
**not** the address the client dials. Connections come from `saved_configurations.json`.
The two can and do disagree; the saved profile wins.

---

## 2. Every file, and whether it multiplies

Legend — **Mult.**: `1` = one for the whole install · `N` = one per microscope ·
`1(N)` = one file containing an entry per microscope.

### Repository root — connection and UI

| File | Purpose | Mult. | In git | Missing ⇒ |
|---|---|---|---|---|
| `saved_configurations.json` | Connection profiles: name, IP, port | **1(N)** | no — seeded from `.example` | **Cannot connect.** Warns and starts with no profiles |
| `saved_configurations.example.json` | Seed for the above | 1 | **yes** | Fresh clone starts with no profiles |
| `drive_mappings.json` | Server path → local drive (`/media/deploy/ctlsm1` → `D:/CTLSM1`) | 1 | no — seeded | Acquired data can't be resolved to a local path |
| `drive_mappings.example.json` | Seed for the above | 1 | **yes** | — |
| `session_paths.json` | Last-used folders per dialog | 1 | no | Dialogs open at defaults. Harmless |
| `window_geometry.json` | Window sizes/positions | 1 | no | Default layout. Harmless |

A profile lives in one shared file, so **two microscopes = two entries, still one
file.** `drive_mappings.json` likewise holds a dict of mappings.

### `microscope_settings/` — instrument configuration

| File | Purpose | Mult. | In git | Missing ⇒ |
|---|---|---|---|---|
| `ScopeSettings.txt` | Downloaded from the scope. Type, **Microscope name**, stage limits, home | 1 (overwritten per connection) | yes | Name falls back to `"default"`, so `default_settings.json` is sought and stage limits are unknown |
| `{name}_settings.json` | Stage soft limits, position-history sizing, **`reference_position`** (the recovery anchor). `n7_settings.json` | **N** | yes | Placeholder limits 0-26 mm are used — a guess, possibly WIDER than the instrument. Run Edit ▸ Microscope Setup |
| `{name}_start_position.txt` | Per-scope start position | **N** | yes | `check_start_position()` creates `default_start_position.txt` |
| `pixel_calibration.json` | XY Pixel Calibrator result (µm/px + the optics it was measured at) | 1 ⚠ | yes | Falls back to ScopeSettings/YAML optics |
| `optics_guard.json` | Remembers optics config to detect a changed objective | 1 ⚠ | yes | No mismatch warning |
| `position_presets.json` | Named stage positions | 1 ⚠ | yes | No presets |
| `led_2d_overview_settings.json` | LED overview dialog state (bbox, overlap, Z step) | 1 | yes | Dialog defaults |
| `progress_timing_cache.json` | Learned per-phase timings for ETA | 1 | yes | ETA re-learns. Harmless |
| `stitching_timing_cache.json` | Same, for stitching | 1 | no | Harmless |
| `webcam_calibration.json` | Webcam↔stage affine per rotation angle | 1 | no | Webcam overview uncalibrated |
| `ControlSettings.txt` | Read by the config-migration service | 1 | yes | Migration skips it |
| `FlamingoMetaData_test.txt` | Fixture for `tests/test_utils_parsers.py` | 1 | yes | That test fails |
| `LowResTSPIM.txt` | No code reference; likely a stored instrument profile | 1 | yes | — |
| `localhost_settings.json` | A `{name}_settings.json` for a scope named `localhost` | N | yes | Only matters if you connect as `localhost` |

### `src/py2flamingo/configs/` — shared defaults, always tracked

| File | Purpose | Mult. |
|---|---|---|
| `microscope_hardware.yaml` | Sensor size, **acquisition frame rate (40)**, optics defaults | 1 |
| `stitching_config.yaml` | ~74 stitching defaults | 1 |
| `visualization_3d_config.yaml` | Chamber ranges, voxel size for the 3D view | 1 |
| `step_chamber_features.yaml`, `..._aslm.yaml` | Chamber geometry from STEP files | 1 per chamber type |

These are code-adjacent defaults, not per-site settings. Overlay order for optics is
**measured calibration > ScopeSettings > YAML** (`get_hardware_config()`).

> `microscope_hardware.yaml.bak` and `stitching_config.yaml.bak` are tracked and unused —
> deletion candidates.

### `workflows/` — templates

`ZStack.txt`, `Snapshot.txt`, `WorkflowZstack.txt`, `PipelineZStack.txt`. Shared, not
per-microscope. Note `workflow_repository` **globs** this directory, so a template with
no reference anywhere in the code is still user-selectable — grep is not evidence of
disuse here.

### Elsewhere

- `~/.flamingo/pipelines/*.json` — saved pipelines. Shared.
- `Workflow.txt` — generated per acquisition and sent to the scope. Not config.

---

## 3. Adding a second microscope

**Add one of each:**

- `microscope_settings/{name}_settings.json` — stage soft limits are the important
  part and they are genuinely instrument-specific.
- `microscope_settings/{name}_start_position.txt` — optional; auto-placeholdered.

**Add an entry, not a file:**

- `saved_configurations.json` — a second `{name, ip_address, port}` object.
- `drive_mappings.json` — a second server-path → drive entry if it writes elsewhere.

**Change nothing:**

- Everything in `src/py2flamingo/configs/`, and the `workflows/` templates.

**Watch out — single files that arguably should be per-scope** (marked ⚠ above). These
are shared today, so connecting a second instrument silently reuses the first one's
values:

| File | Why it is a problem |
|---|---|
| `pixel_calibration.json` | µm/px is an optics property. A calibration measured on one scope would be applied to another. Partly mitigated: it records the optics it was measured at and is *ignored* when they no longer match |
| `position_presets.json` | Stage coordinates are instrument-specific; presets from one scope may be unreachable or wrong on another |
| `optics_guard.json` | Tracks one optics configuration |

Only `pixel_calibration.json` currently defends itself. The others would need a
`{name}_` prefix to be multi-scope-safe.

---

## 4. Configuration vs per-run state

The distinction that matters when deciding whether a file may be deleted, untracked,
or reset. **Decide by when it is written, not by its name or extension.**

| | Written | Examples | Losing it |
|---|---|---|---|
| **Configuration** | Only when someone changes something | `saved_configurations.json`, `drive_mappings.json`, `{name}_settings.json`, `position_presets.json`, `pixel_calibration.json` | Loses a deliberate decision. May stop the system working |
| **Per-run state** | Continuously, by the app | `window_geometry.json`, `session_paths.json`, `*_timing_cache.json` | Costs nothing; regenerates |

Configuration must not be tracked in git directly — it holds machine-specific values
(IPs, drive letters), and a tracked copy that the app rewrites leaves the working tree
permanently dirty, which is what made `git describe --dirty` useless. The pattern
instead is a tracked `<name>.example.json` that `utils/seed_config.py` copies when the
real file is absent. Seeding never overwrites, so a customised machine is untouched by
updates.

**Before deleting or untracking any file here, read it** — `git show HEAD:<path>`.
Filename and gitignore status tell you how a file is *versioned*, not what it *is*.

---

## 5. Minimum set to go from clean clone to imaging

1. `saved_configurations.json` — seeded automatically. Verify the IP/port match the scope.
2. Connect. This writes `microscope_settings/ScopeSettings.txt` and fixes the name.
3. `microscope_settings/{name}_settings.json` — must exist for that name, or stage
   limits are unknown.
4. `drive_mappings.json` — seeded automatically; needed before loading acquired data.

Everything else has a working default or degrades harmlessly.
