# Glossary and naming rules

For developers. This page says which word and which unit suffix to use for each concept in
`src/py2flamingo`, which concepts look alike but must stay separate, and which names must
never change because something outside the code reads them.

New code follows these rules. Old code is renamed only when the rename is mechanical and
nothing persisted or on the wire changes (see [Names that must never change](#names-that-must-never-change)).

## Unit suffixes

A name that holds a physical quantity ends in its unit. A conversion is always written out
(`z_step_mm = z_step_um / 1000.0`), never hidden in a reassignment.

| Suffix | Unit | Example |
|---|---|---|
| `_mm` | millimeters | `x_mm`, `fov_mm`, `tile_step_mm` |
| `_um` | micrometers | `z_step_um`, `pixel_size_um` |
| `_ms` | milliseconds | `exposure_ms` (the wire value) |
| `_us` | microseconds | `exposure_us` (the app's workflow files) |
| `_s` | seconds | `timeout_s`, `settle_time_s` |
| `_deg` | degrees | `r_deg`, `angle_deg` |
| `_px` | pixels | `overlap_px`, `width_px` |
| `_percent` | 0 to 100 | `laser_power_percent`, `overlap_percent` |
| `_fraction` | 0 to 1 | `overlap_fraction` (MIP overview) |
| `_fps` | frames per second | `max_fps`; the existing `frame_rate_hz` means the same and stays |

- Use `_percent`, not `_pct`, and only for 0-100 values. A 0-1 value is a `_fraction`.
- Use `_s`, not `_secs`, `_seconds` or `_sec`, in new code. Existing `_seconds` names in
  public signatures stay until they are touched for another reason.
- A count has no unit and starts with `num_` (`num_planes`, `num_tiles`).
- A `QSpinBox` that shows one component of a duration (the seconds box of an h:m:s editor)
  names the component (`_interval_secs` beside `_interval_mins`); it does not hold a
  duration in seconds.
- Fields of `models.microscope.Position` are `x`, `y`, `z` (mm) and `r` (degrees), without
  suffixes; the class documents the units. A loose variable holding one of them takes the
  suffix (`x_mm`, `r_deg`).

## Preferred terms

Counts are identifier uses in `src/py2flamingo` on 2026-11-22, before the renames that
followed this page.

| Concept | Preferred name | Unit | Other names in use |
|---|---|---|---|
| Stage coordinate | `x_mm`, `y_mm`, `z_mm` (on `Position`: `x`, `y`, `z`) | mm | x 449, x_mm 74, x_pos 44, stage_x 10, x_um 6 |
| Rotation | `r_deg` (on `Position`: `r`) | degrees | r 396, angle 109, rotation_angle 83, angle_deg 67, rotation 34, rotation_deg 13, theta 6 |
| Exposure | `exposure_us` in the app; `exposure_ms` for the wire value | µs / ms | exposure_us 48, exposure_ms 12, exposure 10, exposure_s 8 |
| Pixel size at the sample | `pixel_size_um` (or `pixel_size_mm` where the math is in mm) | µm or mm per pixel | effective_pixel_size_um 28, pixel_size_um 22, pixel_um 15, pixel_size_mm 12 |
| Frame field of view | `fov_mm` (`fov_x_mm`, `fov_y_mm` when they differ) | mm | fov_mm 73, fov 58, fov_x/fov_y 40, fov_x_mm/fov_y_mm 38, fov_um 6 |
| Z step between planes | `z_step_um` (`z_step_mm` where the math is in mm) | µm or mm | z_step_um 57, z_step_mm 27, plane_spacing_um 24, z_step_size 18, step_mm 9 |
| Plane count | `num_planes` | count | num_planes 113, planes 59, n_planes 36 |
| Laser power | `laser_power_percent` | % of the line's maximum | power_percent 19, laser_power_percent 12 |
| Laser slot | `laser_index` | 1-based | laser_index 145, laser_number 14 |
| Display channel | `channel_id` | 0-based | ch 156, channel_id 127, channel 63 |
| Instrument | `microscope_name` | | microscope_name 80, rig 28, scope_name 16 |
| Tile step | `tile_step_mm` (`utils/tile_geometry.tile_step_mm`) | mm | |
| Tile overlap | `overlap_percent` | % | |

Notes on the table:

- **Exposure.** The camera's exposure command carries milliseconds on the wire. The app's
  workflow files and the Workflow tab carry microseconds (`Exposure time (us)`); the
  server's 2026-10-06 build writes `Exposure time (ms)`. `utils/workflow_keys.py` maps
  between the two on read and `core/workflow_exposure.py` on send. In ASLM mode the
  exposure is also the slit width (`models/aslm_timing.py`).
- **Pixel size.** The server's `CAMERA_PIXEL_FIELD_OF_VIEW_GET` returns mm per pixel. That
  is a pixel size, despite the server's name, and a variable holding it is
  `pixel_size_mm`, never `fov` or `pixel_fov`. `utils/fov.py` is the one reader.
- **Z step.** The Workflow tab's Z step is µm (`Plane spacing (um)` in the file). The LED
  2D overview works in mm (`z_step_size`, `z_step_mm`).

## Look-alike concepts that stay separate

| Pair | The difference |
|---|---|
| AOI / ROI / FOV | **AOI** is the camera sensor crop (rows and columns read out; `AOI width` in the app's workflow keys). The server's 2026-10-06 build calls the same thing **ROI** (`ROI width`, `CAMERA_ROI_*` commands), so ROI on the wire and in server files means the AOI. In analysis code, **ROI** is a region of an image chosen for processing. **FOV** is the frame's extent at the sample in mm: pixel size times the AOI in pixels. |
| `laser_index` / `channel_id` | `laser_index` is the server's laser slot, 1-based (Laser 1 to Laser 7). `channel_id` is a 3D-view display slot, 0-based: 0-3 left illumination, 4-7 right (`left + 4`), 8 LED. Neither is the other minus one; convert through the channel configuration. |
| Pixel size / frame FOV | Pixel size is per pixel (µm or mm per pixel). FOV is the whole frame (mm). Both were once called `fov`. |
| Workflow / acquisition | A **workflow** is the server feature: a `Workflow.txt` the server runs on its own (`services/workflow_service.py`, `controllers/workflow_controller.py`, `workflows/workflow_validator.py`). An **acquisition** is a scan the client drives itself, move by move (`acquisitions/led_2d_overview.py`, `acquisitions/volume_scan.py`). Client-side scans never get the word Workflow in their class name. |
| Microscope / scope | **Microscope** is the instrument (`microscope_name`, `{microscope}_settings.json`). **Scope** in `pipeline/` is the variable scope of a pipeline node (`pipeline/engine/scope_resolver.py`). The synthetic server in `testing/` is called "scope" in local variables; that is a nickname for the instrument, not a pipeline scope. |
| Illumination fusion / tile overlap fusion | Illumination fusion joins the left and right light paths of one tile. Tile overlap fusion joins adjacent tiles. Different controls, different code. |

## Class roles

| Suffix | Role |
|---|---|
| `Service` | Talks to the server, or owns one resource (a file, a device, a store) |
| `Controller` | Orchestrates one UI action: takes a request from a view, calls services, reports back |
| `Manager` | Owns a lifecycle or a registry (queues, events, windows, sessions) |
| `Worker` | Runs on a background thread (`QThread`, `QRunnable`, or a `QObject` moved to a thread) |
| `Runner` | Executes one pipeline node type (`pipeline/engine/node_runners/`) |
| `Handler` | A `logging.Handler` |

A controller does not encode protocol frames or read the command socket; it goes through a
service. Classes that do not follow this yet are being restructured; do not copy them.

## Spelling

American English in all new names, comments, docstrings, log messages and UI text: color,
gray, center, neighbor, behavior, analyze, canceled, labeled, modeled, traveled. A few
persisted values keep their British spelling (below); the code around them may still say
"canceled" in prose.

## Names that must never change

Renaming any of these breaks saved user data, a running server, a sibling package or a
tool. Keep the name and, if it is misleading, document it where it is defined.

**Saved settings**

- QSettings stores: `QSettings("py2flamingo", "viewer_controls")` and
  `QSettings("Flamingo", "Py2Flamingo")`, and every key in them.
- `window_geometry.json`: its window IDs default to the **class name** of each
  `PersistentDialog` / `PersistentWidget` subclass, and `save_dialog_state` uses the same
  IDs. Renaming such a class loses its saved geometry and state unless it passes the old
  name as `window_id`.
- JSON and YAML files and their keys: `saved_configurations.json`,
  `{microscope}_settings.json`, `position_presets.json` and `*_position_presets.json`,
  `pixel_calibration.json`, `webcam_calibration.json`, `optics_guard.json`,
  `drive_mappings.json`, `session_paths.json`, `acquisition_timing_history.json`,
  `progress_timing_cache.json`, `led_2d_overview_settings.json`, `benchmark_results.json`,
  `stitch_metadata.json`, pipeline files (`format_version`, node types, config keys), and
  every file under `src/py2flamingo/configs/`.
- Each dialog's saved `ui_state` dict keys (for example the Z-stack panel's `z_step_um`).
- 3D session (`session_manager`) and LED overview session (`zarr_2d_session`) metadata keys.

**Files the server reads or writes**

- Workflow and settings file keys and section names (`<Experiment Settings>`,
  `Plane spacing (um)`, `Exposure time (us)`, `AOI width`, `Microscope name`, ...), in
  both server builds' spellings. `utils/workflow_keys.py` is the one alias layer.
- `ScopeSettings.txt` keys, the `microscope_settings/` file names.

**Wire and server names**

- Command code names and values in `core/command_codes.py` (generated from the server
  header; they mirror the server's names, `PIXEL_FIELD_OF_VIEW` included) and the
  layouts in `core/server_layout.py`.
- Field names of the 128-byte frame (`int32Data0`, `doubleData`, ...).

**In-process string keys**

- Event keys in `core/events.py`: `view_snapshot`, `system_idle`, `processing`, `send`,
  `terminate`, `visualize`, `workflow_cancelled`.
- Queue keys in `core/queue_manager.py`: `image`, `command`, `command_data`, `z_plane`,
  `intensity`, `visualize`, `stage_location`, `other_data`.
- `WorkflowState` values (`"idle"`, ..., `"cancelled"`, `"error"`).
- The color alias `"grey"` in `views/widgets/colors.py`.
- Verification record and fact keys (`travelled_mm`, `mm_per_pixel`, ...): they are
  written into saved verification reports.
- The flamingo-stitcher queue status `"cancelled"`.

**Logger names**

- Loggers are `logging.getLogger(__name__)`, so a module's path is its logger name. Moving
  a module renames its logger; check `toolsAndTesting/parse_log.py` usage and the docs
  before moving one.
- `py2flamingo.core.tcp_protocol` (the frame delivery probe attaches to it by name) and
  `py2flamingo` (the crash handler logs to it).
- The command services log as `py2flamingo.services.camera_service`,
  `...stage_service`, `...laser_led_service` (named after the subclass's module).

**Used by sibling packages**

flamingo-mcp (`flamingo_mcp/gui_host.py`) imports:

- `py2flamingo.application.FlamingoApplication` (`setup_dependencies`,
  `create_main_window`, `qt_app`, `main_window`, `sample_view`, `connection_service`,
  `services`)
- `py2flamingo.testing.synthetic_microscope.SyntheticMicroscope` and its attributes
  (`start`, `stop`, `command_port`, `stage_position`, `stage.moving`, `stage.halted`,
  `stage.halt`, `frames_sent`, `received`, `clear_received`, `live_view`, `led_percent`,
  `led_enabled`, `exposure_ms`, `image_shape`, `workflows`, `workflow_running`)
- `py2flamingo.core.server_layout` (`REFERENCE`, `LAYOUT_2026_10_06`)
- `py2flamingo.core.command_codes.StageCommands`
- `py2flamingo.services.stage_service.StageService` (`get_position`, `move_to_position`)
- `py2flamingo.services.stage_motion_gate` (`is_motion_allowed`, `motion_block_reason`)

It also reaches into dialogs by attribute name:

- the main window's `_mip_overview_dialog`, and on that MIP overview dialog `_folder_edit`,
  `_update_date_combo`, `_load_btn`, `_on_load`, `_date_combo`, `_tiles`, `_config`
  (`tiles_x`, `tiles_y`), `_stitched_image`, `_flat_tile_infos`, `_detected_layout`;
- on the LED 2D overview dialog `point_a_x` ... `point_b_z`, `z_step_size`,
  `adaptive_z_checkbox`, `single_rotation_checkbox`, `_on_start_clicked`,
  `_validate_configuration`, `_load_current_settings`, and `_workflow`, the running
  `LED2DOverviewAcquisition`, with its `_cancelled`, `_running`, `cancel`,
  `scan_completed`, `scan_cancelled`, `scan_error`, `tile_completed`;
- the Sample View's `laser_led_panel` (`_led_combobox`, `_led_radio`, `_led_slider`).

Rename none of these without changing flamingo-mcp in the same change.

flamingo-stitcher imports nothing from `py2flamingo`; `stitching/` here re-exports it.
