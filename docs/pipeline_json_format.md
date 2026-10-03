# Pipeline JSON Format Reference

## Overview

The Flamingo Control pipeline system lets you build visual processing graphs — directed acyclic graphs (DAGs) where nodes represent acquisition or analysis steps and typed connections carry data between them.

**Most people never edit this file.** Build pipelines in **Extensions > Pipeline Editor...** and click Save — see [the Pipeline Editor guide](pipeline_editor_guide.md). Read on if you are generating pipelines from a script, reviewing one in a diff, or writing a tool that emits them.

Where they are saved: the editor's Save dialog puts a pipeline **wherever you choose**. The `py2flamingo-pipeline` CLI instead defaults to `~/.flamingo/pipelines/`, which is also the only directory `py2flamingo-pipeline list` reads — so pipelines saved from the editor elsewhere do not appear in that listing. See [Headless pipelines](headless_pipelines.md).

### Contents

- [Format Version](#format-version) · [Top-Level Structure](#top-level-structure) · [Node Object](#node-object) · [Port Object](#port-object)
- **Node types:** [WORKFLOW](#workflow) · [THRESHOLD](#threshold) · [FOR_EACH](#for_each) · [CONDITIONAL](#conditional) · [EXTERNAL_COMMAND](#external_command) · [SAMPLE_VIEW_DATA](#sample_view_data) · [OVERVIEW_ANALYSIS](#overview_analysis) · [POST_PROCESSING](#post_processing) · [TIMED_LOOP](#timed_loop) · [PYTHON_FUNCTION](#python_function)
- [What a detected object means](#what-a-detected-object-means) — the measured fields and their units
- [Limitations](#limitations) — what the measurements do and do not mean
- [Port Types](#port-types) · [Port Type Compatibility](#port-type-compatibility) · [Connection Object](#connection-object) · [UUIDs](#uuids) · [Examples](#examples) · [Validation](#validation)

### A note on defaults

Each node's table gives two things that are distinct:

- **Default** is what the *runner* uses when a hand-written JSON omits the key.
- **Panel seeds** is what the *editor* writes into a brand-new node.

They are not always the same, and where they differ the table says so. A pipeline built in the GUI carries the panel's value explicitly; one written by hand and missing the key gets the runner's.

## Format Version

Every pipeline JSON includes a `format_version` field at the top level. The current version is `"1.0"`. Older files without this field are treated as version 1.0 automatically.

## Top-Level Structure

```json
{
  "format_version": "1.0",
  "name": "My Pipeline",
  "nodes": [ ... ],
  "connections": [ ... ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `format_version` | string | Schema version (currently `"1.0"`) |
| `name` | string | Display name for the pipeline |
| `nodes` | array | List of node objects |
| `connections` | array | List of connection objects |

## Node Object

Each node represents a processing step in the graph.

```json
{
  "id": "uuid4-string",
  "node_type": "THRESHOLD",
  "name": "Detect Objects",
  "inputs": [ ... ],
  "outputs": [ ... ],
  "config": { ... },
  "x": 300.0,
  "y": 100.0
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | UUID4 unique identifier |
| `node_type` | string | One of the node type enum values (see below) |
| `name` | string | User-visible display name |
| `inputs` | array | List of input port objects |
| `outputs` | array | List of output port objects |
| `config` | object | Type-specific configuration (see per-type tables) |
| `x` | float | X position on editor canvas |
| `y` | float | Y position on editor canvas |

## Port Object

Ports are the typed connection points on nodes.

```json
{
  "id": "uuid4-string",
  "name": "volume",
  "port_type": "VOLUME",
  "direction": "INPUT",
  "required": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | UUID4 unique identifier |
| `name` | string | Port name (used for lookups) |
| `port_type` | string | Data type enum value (see Port Types) |
| `direction` | string | `"INPUT"` or `"OUTPUT"` |
| `required` | bool | If true, this input must be connected for execution |

## Node Types

### WORKFLOW

Executes a microscope acquisition workflow (e.g., Z-stack).

#### Default ports

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| INPUT | trigger | TRIGGER | no |
| INPUT | position | POSITION | no |
| INPUT | z_range | OBJECT | no |
| OUTPUT | volume | VOLUME | — |
| OUTPUT | file_path | FILE_PATH | — |
| OUTPUT | completed | TRIGGER | — |

#### Config properties

| Key | Panel label | Type | Default | Units | What it does |
|-----|-------------|------|---------|-------|--------------|
| `template_file` | Workflow Template | string | `""` | path | The `.txt` workflow template this node acquires with. |
| `use_input_position` | Override Position from Input | bool | `true` | — | When an object or `(x,y,z[,r])` arrives on the `position` port, it **replaces** the template's start position. **Has no effect while that port is unconnected**, which is the state of a freshly dropped node. |
| `auto_z_range` | Auto Z-Range from Object | bool | `false` | — | Replaces the template's `z_range_um` with the input object's Z bounding box plus `buffer_percent`. Needs the `z_range` port wired. |
| `auto_tiling` | Auto Tiling from Object | bool | `false` | — | If the buffered XY extent fits one field of view, tiling is **removed**; otherwise an N×M grid is computed and **both** start and end positions are rewritten around the object's centroid. Overlap comes from the template, or 10% if it has none. Needs the `z_range` port wired. |
| `buffer_percent` | BBox Buffer (%) | float | `25.0` | percent | Padding added **at each end of every axis**, so `25` makes the extent **50% larger**. Read only when `auto_z_range` or `auto_tiling` is on. |
| `config_mode` | *(not shown)* | string | — | — | Legacy. Only honored to warn and skip; present in older saved pipelines. |

Three cautions on the `auto_*` switches, none of them visible in the editor:

- **The buffer is silently clamped to the chamber.** An object near a stage
  limit gets less padding than asked for, and the log line prints the clamped
  result as though it were the request.
- **The derived Z range is quantized to the display voxel size**, 50 µm by
  default (see [Limitations](#limitations)), however fine a Z step you asked for.
- **`auto_tiling` ignores the object's own Z.** Z comes from whatever
  `auto_z_range` left behind.

### THRESHOLD

Applies threshold analysis to a volume, producing detected objects.

#### Default ports

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| INPUT | volume | VOLUME | no |
| OUTPUT | objects | OBJECT_LIST | — |
| OUTPUT | mask | VOLUME | — |
| OUTPUT | count | SCALAR | — |

#### Config properties

| Key | Panel label | Type | Default | Units | Range | What raising it does |
|-----|-------------|------|---------|-------|-------|----------------------|
| `gauss_sigma` | Gaussian Sigma | float | `0.0` | **voxels** (σ) | 0.00–99999.00, 2 dp | Fewer, larger, smoother objects. Also lowers peak intensities, so it shifts the effective threshold down. **Affects the mask only** — intensity features are read from the unsmoothed data. |
| `opening_enabled` | Opening Enabled | bool | `false` | — | — | Turns on the morphological opening below. |
| `opening_radius` | Opening Radius | int | `1` | **voxels** | 0–999999 | Removes thicker necks and shrinks objects. The element is a 6-connected cross dilated N times (an L1 ball), **not a sphere**. Inert unless `opening_enabled`. |
| `min_object_size` | Min Object Size (voxels) | int | `0` | **voxels** | 0–999999 | Discards more small objects. The bound is **inclusive** — an object of exactly this size is kept. `0` disables. Counts across the merged mask, not per channel. |
| `default_threshold` | Default Threshold | int | `100` | **gray levels** (absolute) | 0–999999 | Fewer voxels pass. **Used only when `channel_thresholds` is empty**; otherwise dead. Not scaled for bit depth, so `100` is ~0.4% of full scale on 8-bit and ~0.15% on 16-bit. |
| `channel_thresholds` | Channel Thresholds | object | `{}` | **gray levels** (absolute) | 0–65535 per channel | Per channel; keys are **strings**, `"0"`–`"7"`. Comparison is `>=`, inclusive. **A value of `0` or less skips that channel entirely** — it does not mean "accept everything". |
| `enabled_channels` | *(not shown)* | array | **absent = no filtering** | channel ids 0–7 | — | Restricts which `channel_thresholds` entries are used. **Omitting it uses every entry, including channels 4–7** — it does not default to `[0,1,2,3]`. |
| `voxel_size_um` | *(not shown)* | array[3] | `[50.0, 50.0, 50.0]` | **µm**, (z, y, x) | — | Physical size of one voxel. **Ignored whenever the application supplies a coordinate config**, when the value comes from `display.voxel_size_um` instead. Every physical output scales with it — see [Limitations](#limitations). |

**Objects are 6-connected components — faces only.** Two voxels touching at a
corner count as two objects, not one. This sets `count`, which is what
CONDITIONAL and FOR_EACH branch on, so it decides what gets re-imaged.

### FOR_EACH

Iterates over a collection, emitting one item at a time to downstream nodes.

#### Default ports

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| INPUT | collection | OBJECT_LIST | **yes** |
| OUTPUT | current_item | OBJECT | — |
| OUTPUT | index | SCALAR | — |
| OUTPUT | completed | TRIGGER | — |

#### Config properties

None.

### CONDITIONAL

Branches execution based on comparing a value against a threshold.

#### Default ports

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| INPUT | value | ANY | **yes** |
| INPUT | threshold | SCALAR | no |
| OUTPUT | true_branch | TRIGGER | — |
| OUTPUT | false_branch | TRIGGER | — |
| OUTPUT | pass_through | ANY | — |

#### Config properties

| Key | Type | Default | Options | Description |
|-----|------|---------|---------|-------------|
| `comparison_op` | string | `">"` | `>`, `<`, `==`, `!=`, `>=`, `<=` | Comparison operator |
| `threshold_value` | float | `0.0` | — | Comparison threshold |

### EXTERNAL_COMMAND

Runs an external shell command, passing data in/out via files.

#### Default ports

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| INPUT | input_data | ANY | no |
| INPUT | trigger | TRIGGER | no |
| OUTPUT | output_data | ANY | — |
| OUTPUT | file_path | FILE_PATH | — |
| OUTPUT | completed | TRIGGER | — |

#### Config properties

| Key | Type | Default | Options | Description |
|-----|------|---------|---------|-------------|
| `command_template` | string | `""` | — | Shell command to execute |
| `input_format` | string | `"numpy"` | `numpy`, `tiff`, `json` | Format for input data serialization |
| `output_format` | string | `"json"` | `json`, `csv`, `numpy` | Format for output data deserialization |
| `timeout_seconds` | int | `300` | — | Max execution time in seconds |

### SAMPLE_VIEW_DATA

Reads current volume data and position from the 3D sample view. Has no inputs — this is a data source node.

#### Default ports

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| OUTPUT | volume | VOLUME | — |
| OUTPUT | position | POSITION | — |
| OUTPUT | config | ANY | — |

#### Config properties

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `channel_0` | bool | `true` | Channel 1 — 405nm (DAPI) Left |
| `channel_1` | bool | `true` | Channel 2 — 488nm (GFP) Left |
| `channel_2` | bool | `true` | Channel 3 — 561nm (RFP) Left |
| `channel_3` | bool | `true` | Channel 4 — 640nm (Far-Red) Left |
| `channel_4` | bool | `true` | Channel 5 — 405nm Right (dual-side acquisitions) |
| `channel_5` | bool | `true` | Channel 6 — 488nm Right |
| `channel_6` | bool | `true` | Channel 7 — 561nm Right |
| `channel_7` | bool | `true` | Channel 8 — 640nm Right |

**All eight default to `true`.** The runner treats an absent `channel_N` as
enabled, so a hand-written node that omits the right-side keys selects **all
eight channels**. The editor seeds channels 5–8 unticked, so a GUI-built
pipeline carries `false` explicitly. Write the key if you mean it off.

### OVERVIEW_ANALYSIS

Analyzes a 2D overview image (or one slice of a 3D volume) and selects tiles
of interest based on a chosen detection method (entropy, variance, intensity,
etc.).

#### Default ports

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| INPUT | image | VOLUME | — |
| INPUT | image_path | FILE_PATH | — |
| INPUT | trigger | TRIGGER | — |
| OUTPUT | selected_tiles | OBJECT_LIST | — |
| OUTPUT | count | SCALAR | — |
| OUTPUT | mask | VOLUME | — |

#### Config properties

| Key | Type | Default | Description |
|-----|------|---------|-------------|
Only `method`, the grid and the post-processing settings apply to every run.
**The rest are read only by the method named in their row** — and the editor
shows all of them, enabled, whichever method is selected, so most of the fields
on screen at any time are being ignored.

| Key | Panel label | Default | Units | Range | Read by |
|-----|-------------|---------|-------|-------|---------|
| `method` | Detection Method | `"entropy"` | — | see below | always |
| `tiles_x` / `tiles_y` | Tiles X / Tiles Y | `8` / `8` | tiles | 0–999999 | always |
| `image_path` | Image Path | `""` | path | — | always (used when the `image`/`image_path` ports are unconnected) |
| `entropy_threshold` | Entropy Threshold | `3.0` | **bits** | 0–6 | `entropy` |
| `smoothing` | Entropy Smoothing | `true` | — | — | `entropy` — smooths scores over the **tile grid** at a fixed σ of 1.5 tiles |
| `gradient_threshold` | Gradient Max Anisotropy | `0.5` | dimensionless | 0–1 | `gradient` — **the only setting where a LOWER value selects**; above 1 selects everything |
| `dog_threshold` | DoG Min Variance | `0.0` | gray levels² | ≥ 0 | `dog` — **`0.0` selects every tile** |
| `dog_sigma1` / `dog_sigma2` | DoG Sigma 1 / 2 | `1.0` / `4.0` | **image pixels** | > 0 | `dog` — `sigma2 > sigma1` is required and unenforced |
| `variance_threshold` | Variance Threshold | `100.0` | **gray levels²** | ≥ 0 | `variance` |
| `edge_threshold` | Edge Threshold | `500.0` | **gray levels²** | ≥ 0 | `edge` — variance of a Laplacian, so this is a **focus** measure and responds to defocus as much as to sample |
| `intensity_min` / `intensity_max` | Intensity Min / Max | `20.0` / `255.0` | gray levels | — | `intensity` — **the `255` default assumes 8-bit** and rejects nearly every tile of a 16-bit overview |
| `bp_var_min` / `bp_var_max` | Band-pass Var Min / Max | `0.0` / `1000.0` | gray levels² | ≥ 0 | `bandpass` |
| `bp_entropy_min` | Band-pass Entropy Min | `2.0` | bits | 0–6 | `bandpass` |
| `tube_interior_method` | Tube Interior Method | `"entropy"` | — | `entropy`, `variance` | `tube_detect` |
| `tube_interior_threshold` | Tube Interior Threshold | `3.0` | bits or gray levels² | — | `tube_detect`, per the row above |
| `tube_edge_sensitivity` | Tube Edge Sensitivity | `0.5` | dimensionless | 0–1 | `tube_detect` — above ~1.25 the internal term goes negative; if fewer than two edges are found **every tile is selected** |
| `morphological_cleanup` | Morphological Cleanup | `false` | — | — | always |
| `morphological_radius` | Cleanup Radius | `1` | **tiles** | 0–999999 | always, when cleanup is on |
| `invert` | Invert Selection | `false` | — | — | always, applied **after** cleanup |

**The thresholds are in three different units and do not transfer between
them.** Variance, edge, DoG and intensity thresholds are in the raw intensity
units of the input image, so they must be re-tuned for a different bit depth or
exposure — one tuned on 8-bit data is off by about 65536× on 16-bit. Entropy
thresholds are in bits (0–6) and are min-max normalized per tile, so they are
bit-depth independent — but that same normalization means **an empty tile
containing only read noise still scores high entropy**, which is the default
method's main failure mode.

#### Which method suits which sample

| Sample | Method | Why |
|---|---|---|
| General, unknown | `entropy` (default) | Texture-based and bit-depth independent. Watch for empty tiles scoring high. |
| In a capillary or tube | `tube_detect` | Finds the tube walls from a column-wise intensity profile, then tests the interior. **Assumes a vertical tube** and has no rotation setting. |
| Tube, want the walls ignored | `dog` | Suppresses thin high-frequency features, i.e. tube edges, keeping broader texture. |
| Tube, want the walls found | `gradient` | Scores directionality: 0 is isotropic sample texture, 1 is a strong edge. |
| Bright, well-separated sample | `intensity` | Simple mean gray level. Check the `intensity_max` default against your bit depth. |
| Broad, low-contrast texture | `variance`, `bandpass` | Raw spread of gray values, optionally band-limited. |
| Focus-sensitive | `edge` | Variance of Laplacian — as much a focus measure as a sample detector. |
| Belt and braces | `combined` | The **union** of the variance and edge selections, not a consensus. |

The Property Panel offers an Import button to populate `tiles_x` / `tiles_y` /
`image_path` from a stitched dataset's `stitch_metadata.json`.

### POST_PROCESSING

Runs the stitching / deconvolution / format-conversion pipeline on a raw
acquisition directory. The runner lazy-imports `py2flamingo.stitching.pipeline`
inside `run()`, so non-stitching pipelines won't pull in `pyimagej` or
`pycudadecon`.

#### Default ports

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| INPUT | acquisition_dir | FILE_PATH | true |
| INPUT | trigger | TRIGGER | — |
| OUTPUT | output_path | FILE_PATH | — |
| OUTPUT | completed | TRIGGER | — |

#### Config properties

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `acquisition_dir` | string | `""` | Path to raw acquisition (overridden by input port if connected) |
| `output_dir` | string | `""` | Stitched output dir (defaults to `<acquisition_dir>/stitched`) |
| `pixel_size_um` | float | **the resolved `effective_pixel_size_um` for this microscope**; `0.406` only if that lookup fails | XY voxel size in µm. `0.406` was the old 16× value and survives only as a last-resort fallback, so a reader on a different objective cannot predict their number from this table. **Set it explicitly for a reproducible run.** Auto-importable from a stitched dataset's `stitch_metadata.json`. |
| `z_step_um` | float | `0.0` | Z step (`0` = derive from metadata) |
| `destripe` | bool | `false` | Apply PyStripe artifact correction |
| `illumination_fusion` | string | `"max"` | `max`, `mean`, or `leonardo` |
| `deconvolution_enabled` | bool | `false` | Run deconvolution |
| `deconvolution_engine` | string | `"pycudadecon"` | `pycudadecon` or `redlionfish` |
| `output_format` | string | `"ome-zarr-sharded"` | `ome-zarr-sharded`, `ome-tiff`, `both`, `tiff` |
| `package_ozx` | bool | `false` | Package output as `.ozx` |
| `channels` | string | `""` | Comma-separated channel IDs (`""` = all) |

### TIMED_LOOP

Repeats a body subgraph N times with a configurable delay between iterations,
or indefinitely if `iterations <= 0`. Body nodes are identified the same way
ForEach uses ScopeResolver (downstream of `iteration` / `elapsed_seconds`).

#### Default ports

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| INPUT | trigger | TRIGGER | — |
| OUTPUT | iteration | SCALAR | — |
| OUTPUT | elapsed_seconds | SCALAR | — |
| OUTPUT | completed | TRIGGER | — |

#### Config properties

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `iterations` | int | **`1`** (panel seeds `10`) | Number of iterations. `0` or less = indefinite, cancel with Stop. A hand-written JSON omitting this key runs the body **once**, not ten times, and nothing warns. |
| `interval_seconds` | float | `60.0` | Seconds. Means two different things: under `timing_mode: sequential` it is the pause **after** each pass finishes; under `clock_aligned` it is the wall-clock spacing of pass **starts**, so a slow pass eats into the wait. |
| `timing_mode` | string | `"sequential"` | `sequential` (delay after each body) or `clock_aligned` (start at fixed wall-clock intervals) |

Cancellation is responsive — the runner sleeps in 0.5s slices and checks `context.check_cancelled()` between them.

### PYTHON_FUNCTION

Runs a short piece of Python you write yourself, for the analyses the built-in
nodes do not cover — a custom thresholding rule, or a true/false test. Full
guide: [the Python Function node](pipeline_python_function_node.md).

#### Default ports

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| INPUT | volume | VOLUME | no |
| INPUT | value | ANY | no |
| INPUT | objects | OBJECT_LIST | no |
| INPUT | trigger | TRIGGER | no |
| OUTPUT | value | SCALAR | — |
| OUTPUT | boolean | BOOLEAN | — |
| OUTPUT | mask | VOLUME | — |
| OUTPUT | objects | OBJECT_LIST | — |
| OUTPUT | result | ANY | — |
| OUTPUT | true_branch | TRIGGER | — |
| OUTPUT | false_branch | TRIGGER | — |
| OUTPUT | completed | TRIGGER | — |

The outputs are the union of what THRESHOLD and CONDITIONAL offer, because
which ones a given body uses is not knowable in advance. Unused outputs stay
unconnected and cost nothing.

#### Config properties

| Key | Panel label | Type | Default | What it does |
|-----|-------------|------|---------|--------------|
| `code` | Code | string | a worked Otsu example | The function body. `return` a value, or a dict with any of `value`, `boolean`, `mask`, `objects`, `result`. A misspelled key is an error, not a silent drop. |
| `params` | *(not shown)* | object | `{}` | Passed to the body as `params`. No form for it yet; until there is, put the values in the code. |

`boolean` also picks a branch: `true_branch` or `false_branch` fires. A body
that returns no boolean is an analysis step, not a decision, and fires neither.

**This node runs code from the pipeline file.** Opening someone else's pipeline
and running it means running their Python. The guard on the body rejects
`import` and a list of escape hatches, but it is explicitly **not a security
boundary**. Read a pipeline before you run it.

**It does not fall back to the current view.** Unlike THRESHOLD, an unconnected
`volume` input is `None`, and the shipped example body then fails. Wire a
SAMPLE_VIEW_DATA or WORKFLOW node into it.

## What a detected object means

THRESHOLD and OVERVIEW_ANALYSIS emit objects on their `objects` /
`selected_tiles` ports. These are the measured fields, with the units the code
actually produces:

| Field | Units | Definition |
|-------|-------|------------|
| `label_id` | — | Index within this run. Not stable across runs. |
| `centroid_voxel` | voxels, (z, y, x) | Center of mass on the analysis grid. |
| `centroid_stage` | **mm**, (x, y, z) | Center of mass in stage coordinates. `(0, 0, 0)` when unmeasured, so check `stage_coords_available` before using it. |
| `stage_coords_available` | bool | **`false` means `centroid_stage` is unknown**, not that the object sits at the stage origin. A WORKFLOW node refuses to move to an unmeasured position rather than driving to the chamber corner. |
| `bounding_box` | voxels | `((z0, z1), (y0, y1), (x0, x1))`, end-exclusive. |
| `volume_voxels` | voxels | Count of voxels in the object. |
| `volume_mm3` | **mm³** | `volume_voxels` × the nominal voxel volume. No partial-volume or surface correction — see [Limitations](#limitations). |
| `source_channel` | channel id | The channel contributing the most voxels, 0-based. |
| `mean/max/min/std_intensity` | gray levels | Read from the **unsmoothed** data, so `gauss_sigma` does not change them. |
| `surface_area_voxels` | **a count, not an area** | Number of 6-connected boundary voxels. Dimensionless; does not scale with voxel size; about 20% low for a sphere. |
| `sphericity` | dimensionless, ≤ 1 | `π^(1/3)·(6V)^(2/3) / surface_area_voxels`. **Computed from the mask alone and so blind to voxel anisotropy**, and the clamp saturates — see [Limitations](#limitations). |
| `principal_axis_lengths` | **µm**, (major, mid, minor) | Each is 2·√variance of the voxel positions along that axis — about 2.24× smaller than a uniform solid's extent, so **not a diameter**. |
| `elongation` | dimensionless, ≥ 1 | major / minor of the above. `null` for an object one voxel thick, whose minor axis is zero. |

The last five are `null` for objects smaller than 8 voxels, and are omitted from
the serialized form entirely.

**OVERVIEW_ANALYSIS objects are different.** Its `selected_tiles` carry
**image pixel coordinates** in `centroid_stage` and `volume_mm3 = 0.0`. Do not
wire them into a WORKFLOW `position` input — `use_input_position` defaults to
`true`, so pixel indices would be sent to the stage as millimeters. Use them to
drive tile selection only.

## Limitations

Worth knowing before a number from a pipeline goes into a figure.

**Measurements are on the 3-D display voxel grid, not on camera pixels.** The
shipped grid is 50 µm isotropic, so the smallest reportable volume is
1.25 × 10⁻⁴ mm³ and `volume_mm3` **is not comparable with a measurement made on
raw or stitched tiles** (where the pixel size is well under a micrometer). When
no coordinate config is available — the default for `py2flamingo-pipeline run` —
the fallback is 50 µm isotropic and nothing warns. Everything physical scales
with it: `volume_mm3`, `centroid_stage`, `principal_axis_lengths`, `elongation`,
and the bounding box that `auto_z_range` and `auto_tiling` consume.

**Anisotropic sampling is handled inconsistently.** `principal_axis_lengths`
and `elongation` are fully anisotropy-aware, so on a grid with a coarse Z a
physically isotropic object correctly reports a large elongation. `sphericity`
is not aware of it at all: an object on a 10:1 grid reports the same value as on
an isotropic one. `gauss_sigma` is isotropic **in voxels**, so on an anisotropic
grid it blurs Z and XY by different physical distances.

**`sphericity` is a relative filter, not a measurement.** It substitutes a
boundary-voxel count for a surface area and then clamps the result at 1.0, so
`1.0` means "at or above this estimator's sphere limit" — including for flat
plates. Use it to rank objects within one dataset at one voxel size; do not
publish it as a shape metric.

**Objects are 6-connected.** Diagonally touching structures count separately.

**Morphology features need 8 voxels.** Below that, `sphericity`,
`surface_area_voxels`, `elongation` and `principal_axis_lengths` are absent.

**4-D and 5-D input is reduced to one 3-D volume**, because volume ports are
3-D. The reduction is always logged, and `py2flamingo-pipeline run --timepoint N`
chooses which point.

**OVERVIEW_ANALYSIS analyzes one plane and clips the edges.** A 3-D input is
reduced to its **first Z plane** (logged as a warning), and the tile grid uses
floor division, so the remainder strip at the bottom and right of the image is
never analyzed, up to `tiles-1` pixels on each axis.

**A saved pipeline does not record enough to reproduce a result.** The file
carries `format_version`, `name`, `nodes` and `connections` — no software
version, timestamp, input path, microscope identity, or voxel size. The same
pipeline on two microscopes yields different `volume_mm3`, and nothing in the
file or the output says which grid was used.

## Port Types

| Type | Description |
|------|-------------|
| `VOLUME` | 3D numpy array |
| `OBJECT_LIST` | List of detected objects |
| `OBJECT` | Single detected object (from ForEach iteration) |
| `POSITION` | Stage coordinates (x, y, z, r) |
| `SCALAR` | Numeric value |
| `BOOLEAN` | True/False |
| `STRING` | Text value |
| `FILE_PATH` | Path to a file |
| `TRIGGER` | Execution-order-only, carries no data |
| `ANY` | Accepts any type (used for pass-through) |

## Port Type Compatibility

Connections are allowed between these source→target pairs:

- **Same type → same type** (all types can connect to themselves)
- **Any type → ANY** (ANY accepts everything)
- **ANY → any type** (ANY can feed anything)
- **OBJECT → POSITION** (object contains centroid coordinates)
- **TRIGGER → any type** (trigger provides execution ordering)
- **SCALAR → BOOLEAN** (truthy test)
- **STRING ↔ FILE_PATH** (bidirectional)

All other combinations are rejected.

## Connection Object

A connection is a directed edge from an output port to an input port.

```json
{
  "id": "uuid4-string",
  "source_node_id": "uuid-of-source-node",
  "source_port_id": "uuid-of-output-port",
  "target_node_id": "uuid-of-target-node",
  "target_port_id": "uuid-of-input-port"
}
```

**Rules:**
- Each input port can have at most **one** incoming connection
- Output ports can have multiple outgoing connections
- The graph must be acyclic (no cycles)
- Source and target must be on different nodes
- Port types must be compatible per the matrix above

## UUIDs

**Do not write these by hand.** A five-node pipeline needs roughly thirty UUIDs
— one per node, per port and per connection — and two tools generate them for
you:

```bash
py2flamingo-pipeline create --template threshold --out my.json
```

or, from Python, `PipelineBuilder` (see
[Headless pipelines](headless_pipelines.md)). Both emit a valid pipeline with
correct ids that the editor will open.


All `id` fields use UUID4 format (e.g., `"a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"`). Generate with Python's `uuid.uuid4()` or any UUID4 generator. Every ID in a pipeline must be unique.

## Examples

### Minimal: SampleViewData → Threshold

```json
{
  "format_version": "1.0",
  "name": "Simple Threshold",
  "nodes": [
    {
      "id": "11111111-1111-4111-8111-111111111111",
      "node_type": "SAMPLE_VIEW_DATA",
      "name": "Current View",
      "inputs": [],
      "outputs": [
        {"id": "11111111-1111-4111-8111-aaaaaaaaaaaa", "name": "volume", "port_type": "VOLUME", "direction": "OUTPUT", "required": false},
        {"id": "11111111-1111-4111-8111-bbbbbbbbbbbb", "name": "position", "port_type": "POSITION", "direction": "OUTPUT", "required": false},
        {"id": "11111111-1111-4111-8111-cccccccccccc", "name": "config", "port_type": "ANY", "direction": "OUTPUT", "required": false}
      ],
      "config": {"channel_0": true, "channel_1": true, "channel_2": false, "channel_3": false},
      "x": 50.0,
      "y": 100.0
    },
    {
      "id": "22222222-2222-4222-8222-222222222222",
      "node_type": "THRESHOLD",
      "name": "Detect Objects",
      "inputs": [
        {"id": "22222222-2222-4222-8222-aaaaaaaaaaaa", "name": "volume", "port_type": "VOLUME", "direction": "INPUT", "required": false}
      ],
      "outputs": [
        {"id": "22222222-2222-4222-8222-bbbbbbbbbbbb", "name": "objects", "port_type": "OBJECT_LIST", "direction": "OUTPUT", "required": false},
        {"id": "22222222-2222-4222-8222-cccccccccccc", "name": "mask", "port_type": "VOLUME", "direction": "OUTPUT", "required": false},
        {"id": "22222222-2222-4222-8222-dddddddddddd", "name": "count", "port_type": "SCALAR", "direction": "OUTPUT", "required": false}
      ],
      "config": {"channel_thresholds": {"0": 200}, "gauss_sigma": 1.0, "min_object_size": 50},
      "x": 300.0,
      "y": 100.0
    }
  ],
  "connections": [
    {
      "id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      "source_node_id": "11111111-1111-4111-8111-111111111111",
      "source_port_id": "11111111-1111-4111-8111-aaaaaaaaaaaa",
      "target_node_id": "22222222-2222-4222-8222-222222222222",
      "target_port_id": "22222222-2222-4222-8222-aaaaaaaaaaaa"
    }
  ]
}
```

### Full: Acquire → Threshold → ForEach → Reacquire

```json
{
  "format_version": "1.0",
  "name": "Acquire-Analyze-Reacquire",
  "nodes": [
    {
      "id": "aaaa1111-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "node_type": "WORKFLOW",
      "name": "Initial Acquisition",
      "inputs": [
        {"id": "a1000001-0000-4000-8000-000000000001", "name": "trigger", "port_type": "TRIGGER", "direction": "INPUT", "required": false},
        {"id": "a1000001-0000-4000-8000-000000000002", "name": "position", "port_type": "POSITION", "direction": "INPUT", "required": false},
        {"id": "a1000001-0000-4000-8000-000000000003", "name": "z_range", "port_type": "OBJECT", "direction": "INPUT", "required": false}
      ],
      "outputs": [
        {"id": "a1000001-0000-4000-8000-000000000004", "name": "volume", "port_type": "VOLUME", "direction": "OUTPUT", "required": false},
        {"id": "a1000001-0000-4000-8000-000000000005", "name": "file_path", "port_type": "FILE_PATH", "direction": "OUTPUT", "required": false},
        {"id": "a1000001-0000-4000-8000-000000000006", "name": "completed", "port_type": "TRIGGER", "direction": "OUTPUT", "required": false}
      ],
      "config": {"template_file": "", "use_input_position": false, "auto_z_range": false, "auto_tiling": false, "buffer_percent": 25.0},
      "x": 50.0,
      "y": 100.0
    },
    {
      "id": "bbbb2222-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      "node_type": "THRESHOLD",
      "name": "Detect Objects",
      "inputs": [
        {"id": "b2000001-0000-4000-8000-000000000001", "name": "volume", "port_type": "VOLUME", "direction": "INPUT", "required": false}
      ],
      "outputs": [
        {"id": "b2000001-0000-4000-8000-000000000002", "name": "objects", "port_type": "OBJECT_LIST", "direction": "OUTPUT", "required": false},
        {"id": "b2000001-0000-4000-8000-000000000003", "name": "mask", "port_type": "VOLUME", "direction": "OUTPUT", "required": false},
        {"id": "b2000001-0000-4000-8000-000000000004", "name": "count", "port_type": "SCALAR", "direction": "OUTPUT", "required": false}
      ],
      "config": {"channel_thresholds": {"0": 200}, "gauss_sigma": 1.0, "min_object_size": 100},
      "x": 300.0,
      "y": 100.0
    },
    {
      "id": "cccc3333-cccc-4ccc-8ccc-cccccccccccc",
      "node_type": "FOR_EACH",
      "name": "For Each Object",
      "inputs": [
        {"id": "c3000001-0000-4000-8000-000000000001", "name": "collection", "port_type": "OBJECT_LIST", "direction": "INPUT", "required": true}
      ],
      "outputs": [
        {"id": "c3000001-0000-4000-8000-000000000002", "name": "current_item", "port_type": "OBJECT", "direction": "OUTPUT", "required": false},
        {"id": "c3000001-0000-4000-8000-000000000003", "name": "index", "port_type": "SCALAR", "direction": "OUTPUT", "required": false},
        {"id": "c3000001-0000-4000-8000-000000000004", "name": "completed", "port_type": "TRIGGER", "direction": "OUTPUT", "required": false}
      ],
      "config": {},
      "x": 550.0,
      "y": 100.0
    },
    {
      "id": "dddd4444-dddd-4ddd-8ddd-dddddddddddd",
      "node_type": "WORKFLOW",
      "name": "Re-acquire at Object",
      "inputs": [
        {"id": "d4000001-0000-4000-8000-000000000001", "name": "trigger", "port_type": "TRIGGER", "direction": "INPUT", "required": false},
        {"id": "d4000001-0000-4000-8000-000000000002", "name": "position", "port_type": "POSITION", "direction": "INPUT", "required": false},
        {"id": "d4000001-0000-4000-8000-000000000003", "name": "z_range", "port_type": "OBJECT", "direction": "INPUT", "required": false}
      ],
      "outputs": [
        {"id": "d4000001-0000-4000-8000-000000000004", "name": "volume", "port_type": "VOLUME", "direction": "OUTPUT", "required": false},
        {"id": "d4000001-0000-4000-8000-000000000005", "name": "file_path", "port_type": "FILE_PATH", "direction": "OUTPUT", "required": false},
        {"id": "d4000001-0000-4000-8000-000000000006", "name": "completed", "port_type": "TRIGGER", "direction": "OUTPUT", "required": false}
      ],
      "config": {"template_file": "", "use_input_position": true, "auto_z_range": false, "auto_tiling": false, "buffer_percent": 25.0},
      "x": 800.0,
      "y": 100.0
    }
  ],
  "connections": [
    {
      "id": "e0000001-0000-4000-8000-000000000001",
      "source_node_id": "aaaa1111-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "source_port_id": "a1000001-0000-4000-8000-000000000004",
      "target_node_id": "bbbb2222-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      "target_port_id": "b2000001-0000-4000-8000-000000000001"
    },
    {
      "id": "e0000002-0000-4000-8000-000000000002",
      "source_node_id": "bbbb2222-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      "source_port_id": "b2000001-0000-4000-8000-000000000002",
      "target_node_id": "cccc3333-cccc-4ccc-8ccc-cccccccccccc",
      "target_port_id": "c3000001-0000-4000-8000-000000000001"
    },
    {
      "id": "e0000003-0000-4000-8000-000000000003",
      "source_node_id": "cccc3333-cccc-4ccc-8ccc-cccccccccccc",
      "source_port_id": "c3000001-0000-4000-8000-000000000002",
      "target_node_id": "dddd4444-dddd-4ddd-8ddd-dddddddddddd",
      "target_port_id": "d4000001-0000-4000-8000-000000000002"
    }
  ]
}
```

## Validation

After loading a pipeline from JSON, call `Pipeline.validate()` to check for errors:

```python
import json
from py2flamingo.pipeline.models.pipeline import Pipeline

with open('my_pipeline.json') as f:
    data = json.load(f)

pipeline = Pipeline.from_dict(data)
errors = pipeline.validate()
if errors:
    for e in errors:
        print(f"Error: {e}")
else:
    print("Pipeline is valid")
```

Validation checks:
- Graph has at least one node
- No cycles in the graph
- All connections reference existing nodes and ports
- All connection port types are compatible
- All required input ports are connected

## Warning

**Always review pipelines before running them.** Pipelines can trigger microscope acquisitions and stage movements. An incorrectly constructed pipeline could move the stage to unexpected positions or run unintended workflows. Verify node configurations, connections, and workflow templates before execution.
