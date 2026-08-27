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
| `{name}_settings.json` | Stage soft limits, position-history sizing, **`reference_position`** (the recovery anchor). `n7_settings.json`, `liara_settings.json`. **This is the ONLY store that gates a stage move** — `position_controller.move_x/y/z/r` raise and `movement_controller._clamp_to_limits` clamps against it. `microscope_hardware.yaml`'s `stage_limits` block is a tile-count *estimate* only, and ScopeSettings.txt contributes just `Soft limit max y-axis`. Selected by name, so a case mismatch used to mean placeholders; the service now case-folds as a fallback and warns | **N** | yes | Placeholder limits 0-26 mm are used — a guess, possibly WIDER than the instrument. Run Edit ▸ Microscope Setup |
| `{name}_start_position.txt` | **Vestigial.** Nothing reads its contents any more — `get_start_position()` was removed as dead in `2026-08-11`. `FlamingoConnect.check_start_position()` only checks that *some* `*_start_position.txt` exists and creates an empty `default_start_position.txt` if not | 1 | yes | An empty placeholder is created |
| `{name}_pixel_calibration.json` | **Per scope since 2026-08-26.** XY Pixel Calibrator result (µm/px + the optics the config *believed* when it was measured). **Absent by default** — the 2026-06-26 one was removed 2026-08-17 as stale: it was stamped `5.000` (the YAML fallback of the day) while measuring 1.0276 µm/px, i.e. ~6.33×, against a scope now reporting 6.205. Two points, quality 0.37, 3.3° shear — and `residual_px: 0.0` is meaningless with two points, which exactly determine the affine | **N** | the shared seed is | Falls back to ScopeSettings/YAML optics |
| `{name}_optics_guard.json` | Remembers optics config to detect a changed objective. Signature is `name|mag|sensor_px`. **Per scope since 2026-08-26** — one shared acknowledgement list mixed two instruments' entries with no way to tell them apart | **N** | the shared seed is | No mismatch warning |
| `{name}_position_presets.json` | Named stage positions. **Per microscope since 2026-08-26** — these are raw stage coordinates, and `move_to_position(validate=True)` CLAMPS rather than refusing, so a shared file sent the stage somewhere else while the UI reported the preset's name. The pre-split `position_presets.json` is adopted for a scope only when EVERY preset is reachable within that scope's limits (all-or-nothing, 1 µm tolerance for a position saved at a limit), and is copied, never moved | **N** | **no** (the legacy `position_presets.json` stays tracked and acts as the seed, like the `.example.json` pattern in §4) | No presets |
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
| `microscope_hardware.yaml` | Sensor size, **acquisition frame rate (40)**, optics defaults, plus a per-microscope `microscopes:` overlay | 1 file, N entries |
| `stitching_config.yaml` | ~74 stitching defaults | 1 |
| `visualization_3d_config.yaml` | Chamber ranges, voxel size for the 3D view | 1 |
| `step_chamber_features.yaml`, `..._aslm.yaml` | Chamber geometry from STEP files | 1 per chamber type |

These are code-adjacent defaults. Two of them (`microscope_hardware.yaml`,
`visualization_3d_config.yaml`) now carry a `microscopes:` map keyed on the scope's own
**Microscope name**, deep-merged over the base — so they are one file holding N entries,
not one setting. Overlay order for optics is **measured calibration > ScopeSettings >
the `microscopes:` block > the base YAML** (`get_hardware_config()`).

The per-scope block supplies fallbacks and one *assertion*,
`expected_objective_magnification`: it is never used as a value, but when the scope
reports something >2% away, `get_hardware_config()` records an `optics_disagreement`
that the app shows on connect. That is how a stale server objective becomes visible —
Liara shipped a stale 17× against a measured 25.48×.

**`microscopes:` must stay LAST in `microscope_hardware.yaml`.**
`PixelCalibrationService.apply_config_patch` rewrites the first matching `key:` line
(`re.subn`, `count=1`, `MULTILINE`) and `^\s*` matches indented keys, so a per-scope
entry placed above the base would be patched instead of it — silently, with the dialog
reporting success. Pinned by
`tests/test_hardware_config_optics.py::TestYamlOrderingConstraint`.

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
- `AcquisitionManifest.txt` — written by us into the acquisition's date folder when a tile collection finishes. A RECORD of what was collected and every setting used, not an input: nothing reads it back. See
  `claude-reports/design/acquisition_folder_format.md`.

---

## 3. Adding a second microscope

**Add one of each:**

- `microscope_settings/{name}_settings.json` — stage soft limits are the important
  part and they are genuinely instrument-specific.

The filename must **byte-match** the `Microscope name` that scope reports in its
  `ScopeSettings.txt`. Include all four axes with `min` and `max` — a partial axis makes
  `_load_settings` raise, which lands in the not-configured branch and substitutes the
  *wider* placeholders. `r` especially: Edit ▸ Microscope Setup only collects x/y/z.

**Add an entry, not a file:**

- `saved_configurations.json` — a second `{name, ip_address, port}` object.
- `drive_mappings.json` — a second server-path → drive entry if it writes elsewhere.
- `microscope_hardware.yaml`'s `microscopes:` map — optics and camera only. **Never
  stage limits** (a third representation of the envelope is how they drift apart) and
  **never sensor dimensions** (`disk_tile_loader` freezes `FRAME_WIDTH`/`FRAME_HEIGHT`
  from them at import, so a per-scope value goes stale after a mid-session switch).
- `visualization_3d_config.yaml`'s `microscopes:` map — chamber ranges and orientation.
  Needed, not optional: the base ranges are n7's, and a scope whose travel falls outside
  them has every tile silently dropped from 3D storage.

**Change nothing:**

- The `workflows/` templates.

**How the preset seed behaves on each rig:** both machines pull the same tracked
`position_presets.json` (n7's 8 named positions). On n7 every one is reachable, so
they migrate to `n7_position_presets.json` and nothing is lost. On Liara all 8 are
outside its 0-5 / 0-15 envelope, so it adopts none and starts clean — no n7
coordinates on a 5 mm axis. Neither machine writes to the tracked file, so a pull
never conflicts.

**Known gaps with two scopes configured:** the Pixel Calibrator patches the *base*
`optics:` block regardless of which scope is connected (bounded — those values are
offline fallbacks), and `stitching_config.yaml`'s `pixel_size_um` is still global.

**Watch out — single files that arguably should be per-scope** (marked ⚠ above). These
are shared today, so connecting a second instrument silently reuses the first one's
values:

| File | Why it is a problem |
|---|---|
| `pixel_calibration.json` | µm/px is an optics property. A calibration measured on one scope would be applied to another. Partly mitigated: it records the optics it was measured at and is *ignored* when they no longer match |
| `optics_guard.json` | Tracks one optics configuration |

**This table is now empty** — every file that was shared-but-instrument-specific
became per-scope on 2026-08-26. The pattern for all three: **writes always go to
`{scope}_{name}`; reads fall back to the shared pre-split file** until that scope
writes its own (`config_loader.scoped_settings_read_path` /
`scoped_settings_write_path`). No migration and no guessing at an owner — for the
calibration and guard files the stored `optics_signature` already records which optics
they describe, and `position_presets.json` is adopted only when every preset is
reachable on the connecting scope.

**`stitching_config.yaml` does NOT need the same treatment**, despite holding
`pixel_size_um` and a deconvolution `psf:` block. `py2flamingo.stitching` is a
re-export shim over the standalone `flamingo_stitcher` package, so the copy under
`src/py2flamingo/configs/` never drives a stitch — its only reader is an autofill
preview in `pipeline/ui/property_panel.py`. In the stitcher itself the pixel size is
already resolved **per acquisition** (`suggested_pixel_size_um` reads that
acquisition's own ScopeSettings objective, falling back to the per-microscope entry in
the stitcher's `microscope_hardware.yaml`). What genuinely is still global there is the
deconvolution PSF: `deconvolution.py` takes NA and `n_immersion` from the stitcher's
hardware config, whose `microscopes:` map is read only for `objective_magnification`.
So Liara would generate a PSF at NA 0.4 / n 1.33 with `nz: 31` — the file's own comment
says 31 suffices only below NA 0.5, and Liara's objective is **0.7**. That is work for
the flamingo-stitcher repo, not this one.

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
