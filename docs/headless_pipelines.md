# Headless pipelines (no GUI, no microscope)

Pipelines can be **authored, fed data, and run from the command line or a
script** — useful for remote work, CI, and testing analysis against small data
files such as the synthetic collagen phantoms in
`QPSC_Project/tools/collagen-phantom-creation`.

Everything is exposed through the `py2flamingo-pipeline` CLI (entry point
`py2flamingo.pipeline.cli:main`) and three importable modules:

| Module | Purpose |
| --- | --- |
| `pipeline/headless_io.py` | `load_volumes(path)` → `{channel: (Z,Y,X) array}` from `.npy` / TIFF / OME-Zarr |
| `pipeline/builder.py` | `PipelineBuilder` + `make_template` to author pipelines without the editor |
| `pipeline/headless_services.py` | `build_headless_services` + `run_pipeline_headless` (synchronous, no Qt event loop) |

## CLI quick reference

```bash
# What node types exist and what ports they have
py2flamingo-pipeline nodes

# Scaffold a starter pipeline from a template
py2flamingo-pipeline create --list
py2flamingo-pipeline create --template threshold --out my.json

# Inspect / validate a pipeline JSON
py2flamingo-pipeline describe my.json
py2flamingo-pipeline validate my.json        # exit code 1 if invalid

# List saved pipelines (~/.flamingo/pipelines by default)
py2flamingo-pipeline list

# Run a pipeline on a data file
py2flamingo-pipeline run my.json --input volume.ome.tif --output-json out.json
```

## End-to-end with a collagen phantom

```bash
# 1. Generate a small phantom (use --no-qc to avoid the matplotlib QC step)
python QPSC_Project/tools/collagen-phantom-creation/generate_phantoms.py \
    --pattern wave --size 128 --no-qc --out-dir /tmp/ph

# 2. Scaffold a threshold pipeline
py2flamingo-pipeline create --template threshold --out /tmp/p.json

# 3. Run it on the phantom; objects are written to out.json
py2flamingo-pipeline run /tmp/p.json --input /tmp/ph/wave.ome.tif \
    --output-json /tmp/out.json
```

The `run` output lists each node's state and output port values, e.g.:

```
Loaded wave.ome.tif → ch0: shape=(1, 128, 128) dtype=uint8
Skipping (no-op): WORKFLOW
[Threshold] state=completed objects=<list len=5> mask=<ndarray ...> count=5
```

### Input formats (`--input`)

`load_volumes` dispatches on the path:

- `.npy` — a single numpy array.
- `.tif` / `.tiff` / `.ome.tif(f)` — read with `tifffile`; channel/spatial axes
  are inferred from the series `axes` string. Phantom ImageJ hyperstacks use
  `TZCYX` (channel 0 = collagen, 1 = tumor); the leading `T` is reduced to index
  0 and each channel becomes its own `(Z,Y,X)` volume.
- `.zarr` / `.ome.zarr` (a directory) — opened via the same helpers the 3-D
  viewer uses (`session_manager._find_zarr_array`), so ngff / sharded stores
  resolve identically to the GUI **Load Stitched** path.

Multi-channel files load **all** channels by default. Use `--volume-channel N`
to select one, and `--channel-axis K` if the axis order can't be inferred.

2-D inputs gain a singleton Z axis so every channel is 3-D `(Z, Y, X)`.

## Generating a test dataset (`collect`)

There is no microscope here, so `collect` synthesizes a phantom dataset on disk
(no socket, no hardware). Two modes mirror the real workflow:

```bash
# Fast path: a small already-stitched OME-TIFF + a ready pipeline JSON.
# Best for iterating on analysis pipelines without re-stitching every time.
py2flamingo-pipeline collect --mode stitched --out /tmp/ds
py2flamingo-pipeline run /tmp/ds/pipeline.json --input /tmp/ds/stitched.ome.tif

# Gold standard: a native raw acquisition folder (X{x}_Y{y}/Workflow.txt/.raw).
# Exercises the real chain: discover_tiles → stitching → analysis.
py2flamingo-pipeline collect --mode raw --out /tmp/acq --grid 2,2 --planes 4
python -m py2flamingo.stitching /tmp/acq --output-format ome-zarr-sharded
py2flamingo-pipeline run <pipeline.json> --input /tmp/acq_stitched/stitched.ome.zarr
```

Notes:
- **Raw frames are full sensor size** (`microscope_hardware.yaml`,
  2048×2048) because the stitching reader assumes that — a 2×2×4-plane set is
  ~130 MB. Keep `--planes`/`--grid` small. The stitched mode is tiny (~MB).
- The raw set tiles a single phantom field with `--overlap` (default 0.15) so
  overlapping tiles share structure for registration.
- Stitching requires the `multiview-stitcher` backend; without it, use
  `--mode stitched` for pipeline testing, or stitch on a machine that has it.
- Programmatic API: `py2flamingo.testing.phantom_dataset`
  (`make_phantom_volume`, `write_raw_acquisition`, `write_stitched_dataset`).

A standalone **mock microscope** (spoofs the TCP protocol, no data files) lives
at `tests/mock_server.py` — useful for exercising the connection/workflow-send
path: `python tests/mock_server.py --port 53717`, then connect the app to it.

## Authoring from Python

```python
from py2flamingo.pipeline.builder import PipelineBuilder
from py2flamingo.pipeline.headless_io import load_volumes
from py2flamingo.pipeline.headless_services import (
    build_headless_services, run_pipeline_headless,
)
from py2flamingo.pipeline.models.pipeline import NodeType

b = PipelineBuilder("detect_collagen")
b.add(NodeType.THRESHOLD, channel_thresholds={0: 100}, min_object_size=8)
pipeline = b.build()                       # validates the graph

volumes = load_volumes("phantom.ome.tif")  # {0: (Z,Y,X)}
services = build_headless_services(volumes=volumes)
run = run_pipeline_headless(pipeline, services=services)

print(run.succeeded, run.node_states)
```

`PipelineBuilder.connect(src, "port_name", dst, "port_name")` wires nodes by
**port name** (it resolves the editor's UUID port ids for you).

## Node-type coverage when headless

| Runs from data/config | Needs hardware or live viewer (stubbed) |
| --- | --- |
| THRESHOLD, OVERVIEW_ANALYSIS, EXTERNAL_COMMAND, CONDITIONAL, FOR_EACH, TIMED_LOOP, POST_PROCESSING | WORKFLOW (microscope), SAMPLE_VIEW_DATA (live 3-D viewer) |

- `run` **auto-skips WORKFLOW** (replaces it with a no-op) unless you pass
  `--enable-workflow`, which injects a stub facade so WORKFLOW nodes complete
  immediately without touching the microscope.
- `SAMPLE_VIEW_DATA` reads `voxel_storage`, so it works headless when you supply
  `--input` (the volume is served from the in-memory store).
- Use `--skip-tag a,b` to no-op additional node types (e.g.
  `--skip-tag post_processing` for a hardware-free CI run).

## Stitching headless

There are two ways to stitch a raw acquisition folder without the GUI:

1. **Standalone stitching CLI** (recommended for pure stitching):

   ```bash
   python -m py2flamingo.stitching /path/to/acq --output-format ome-zarr-sharded
   ```

2. **As a pipeline node** (`POST_PROCESSING`), e.g. inside an acquire→stitch
   flow. `acquisition_dir` is a required *input port* by default, so a
   config-only node must mark it optional — the `stitch` template does this:

   ```bash
   py2flamingo-pipeline create --template stitch --acq-dir /path/to/acq --out s.json
   py2flamingo-pipeline run s.json
   ```

## Testing

`tests/test_headless_phantom_e2e.py` covers the loaders, the templates, and the
full file → build → run → assert loop (synthesizing a phantom-like volume, and
shelling out to the real generator when present). The broader
`tests/test_pipeline_smoke.py` runs every shipped pipeline headlessly.

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
    tests/test_headless_phantom_e2e.py tests/test_pipeline_smoke.py -q
```
