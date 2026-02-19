# Visual Pipeline Workflow System — Reference Guide

**Commit:** `1ba0b33` + Node Enhancements + Workflow Config Dialog (2026-02-19)
**Location:** `src/py2flamingo/pipeline/`

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Package Layout](#package-layout)
3. [Data Models](#data-models)
4. [Execution Engine](#execution-engine)
5. [Node Types & Runners](#node-types--runners)
6. [Threshold Analysis Service](#threshold-analysis-service)
7. [Node Graph Editor UI](#node-graph-editor-ui)
8. [Application Integration](#application-integration)
9. [JSON Pipeline Format](#json-pipeline-format)
10. [Key Design Decisions](#key-design-decisions)
11. [Extension Guide](#extension-guide)
12. [File Reference](#file-reference)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│  Pipeline Editor Dialog (UI)                         │
│  ┌──────────┐  ┌───────────────┐  ┌──────────────┐ │
│  │ Node     │  │ Graph View    │  │ Property     │ │
│  │ Palette  │  │ (Canvas)      │  │ Panel        │ │
│  └──────────┘  └───────────────┘  └──────────────┘ │
└──────────────────────┬──────────────────────────────┘
                       │ run_requested / stop_requested
              ┌────────▼────────┐
              │ Pipeline        │
              │ Controller      │ ← mediates UI ↔ Engine
              └────────┬────────┘
                       │
              ┌────────▼────────┐
              │ Pipeline        │
              │ Executor        │ ← QThread, DAG walker
              │ (+ Runners)     │
              └────────┬────────┘
                       │ delegates to
         ┌─────────────┼──────────────┐
         ▼             ▼              ▼
   WorkflowFacade  ThresholdSvc  subprocess
   (existing)      (new)         (external)
```

The pipeline layer sits **on top of** the existing workflow system. It never
replaces or modifies existing acquisition logic — runners call into
`WorkflowFacade` and `WorkflowQueueService` as-is.

---

## Package Layout

```
pipeline/
├── __init__.py
├── models/
│   ├── __init__.py                  # Public re-exports
│   ├── port_types.py                # PortType enum, compatibility matrix
│   ├── pipeline.py                  # Pipeline, PipelineNode, Port, Connection
│   └── detected_object.py           # DetectedObject dataclass
├── engine/
│   ├── __init__.py
│   ├── executor.py                  # PipelineExecutor (QThread)
│   ├── context.py                   # ExecutionContext
│   ├── scope_resolver.py            # ForEach/Conditional scope identification
│   └── node_runners/
│       ├── __init__.py
│       ├── base_runner.py           # AbstractNodeRunner ABC
│       ├── workflow_runner.py       # → WorkflowFacade
│       ├── threshold_runner.py      # → ThresholdAnalysisService
│       ├── foreach_runner.py        # Iterates collection
│       ├── conditional_runner.py    # Evaluates condition, picks branch
│       ├── external_command_runner.py  # subprocess with temp I/O
│       └── sample_view_data_runner.py  # Reads current 3D viewer state
├── services/
│   ├── __init__.py
│   ├── threshold_analysis_service.py  # Extracted from UnionThresholderDialog
│   ├── pipeline_repository.py       # JSON save/load
│   └── pipeline_service.py          # Facade
├── ui/
│   ├── __init__.py
│   ├── graph_scene.py               # QGraphicsScene
│   ├── graph_view.py                # QGraphicsView (pan/zoom)
│   ├── node_item.py                 # Node rectangles
│   ├── port_item.py                 # Port circles
│   ├── connection_item.py           # Bezier wires + drag wire
│   ├── node_palette.py              # Draggable node type list
│   ├── property_panel.py            # Dynamic config form
│   ├── workflow_config_dialog.py    # Full workflow configuration dialog
│   └── pipeline_editor_dialog.py    # Top-level dialog
└── controllers/
    ├── __init__.py
    └── pipeline_controller.py       # UI ↔ Engine mediator
```

---

## Data Models

### PortType (`models/port_types.py`)

```python
class PortType(Enum):
    VOLUME        # 3D numpy array
    OBJECT_LIST   # List[DetectedObject]
    OBJECT        # Single DetectedObject
    POSITION      # Stage coordinates (x, y, z, r)
    SCALAR        # Numeric value
    BOOLEAN       # True/False
    STRING        # Text
    FILE_PATH     # File path string
    TRIGGER       # Execution-order-only, no data
    ANY           # Accepts/produces any type
```

**Compatibility Matrix** — enforced at connection time:
- Same type → same type: always allowed
- ANY ↔ anything: allowed
- TRIGGER → anything: allowed (execution ordering)
- OBJECT → POSITION: allowed (contains centroid_stage)
- SCALAR → BOOLEAN: allowed (truthy test)
- STRING ↔ FILE_PATH: allowed

Use `can_connect(source_type, target_type) -> bool` to check.

Port colors are in `PORT_COLORS: dict[PortType, str]` for UI rendering.

### Pipeline Graph (`models/pipeline.py`)

**Core classes:**

| Class | Key Fields | Purpose |
|-------|-----------|---------|
| `Port` | id, name, port_type, direction, required | Typed I/O endpoint |
| `PipelineNode` | id, node_type, name, inputs, outputs, config, x, y | Processing step |
| `Connection` | id, source_node_id, source_port_id, target_node_id, target_port_id | Typed edge |
| `Pipeline` | name, nodes (dict), connections (dict) | The full DAG |

**Pipeline graph operations:**

| Method | Description |
|--------|-------------|
| `add_node(node)` | Add a node |
| `remove_node(node_id)` | Remove node + all its connections |
| `add_connection(src_node, src_port, tgt_node, tgt_port)` | Create typed connection (validates types, cycles, duplicates) |
| `topological_sort()` | Kahn's algorithm → list of node IDs |
| `validate()` | Returns `List[str]` of errors (cycles, type mismatches, unconnected required ports) |
| `get_downstream_nodes(node_id)` | All reachable nodes (BFS) |
| `get_downstream_from_port(node_id, port_id)` | Reachable from specific output port |
| `to_dict()` / `from_dict()` | JSON serialization |

**Factory function:**
```python
create_node(node_type, name=None, config=None, x=0, y=0) -> PipelineNode
```
Creates a node with all default ports pre-configured for its type.

### DetectedObject (`models/detected_object.py`)

```python
@dataclass
class DetectedObject:
    label_id: int                              # ndimage.label() ID
    centroid_voxel: Tuple[float, float, float]  # (z, y, x) in voxels
    centroid_stage: Tuple[float, float, float]  # (x, y, z) in mm
    bounding_box: Tuple[slice, slice, slice]    # (z, y, x) slices
    volume_voxels: int
    volume_mm3: float
    source_channel: Optional[int] = None
```

Supports `to_dict()` / `from_dict()` for JSON serialization. Bounding boxes are serialized as `[[start, stop], ...]`.

**Helper methods:**
- `bounding_box_mm(voxel_size_um, z_range_mm, y_range_mm, x_range_mm, invert_x)` — converts voxel bounding box to stage coordinate ranges (mm). Returns dict with `x_min, x_max, y_min, y_max, z_min, z_max`. Uses project coord conventions (Y inverted, X optionally inverted).
- `extent_mm(voxel_size_um)` — returns physical size as `(x, y, z)` in mm.

---

## Execution Engine

### PipelineExecutor (`engine/executor.py`)

QThread subclass that walks the DAG:

1. **Validate** — calls `pipeline.validate()`
2. **Scope resolve** — ScopeResolver identifies ForEach/Conditional body nodes
3. **Top-level walk** — executes non-scoped nodes in topological order
4. **Per-node**: find runner → `runner.run(node, pipeline, context)`
5. **Cancellation** — checked at each node boundary via `isInterruptionRequested()`

**Signals:**

| Signal | Args | When |
|--------|------|------|
| `node_started` | node_id | Node begins |
| `node_completed` | node_id | Node succeeds |
| `node_error` | node_id, msg | Node fails |
| `pipeline_progress` | current, total | After each node |
| `pipeline_completed` | — | All done |
| `pipeline_error` | msg | Fatal error |
| `foreach_iteration` | node_id, current, total | Each loop iteration |
| `log_message` | str | General log |

**Subgraph execution:**
```python
executor.execute_subgraph(node_ids: list, context: ExecutionContext)
```
Used by ForEach/Conditional runners to execute their body nodes. Runs on the same thread (not a new QThread).

### ExecutionContext (`engine/context.py`)

Per-run state shared across all runners:

| Feature | Method |
|---------|--------|
| Store output | `set_port_value(port_id, PortValue)` |
| Read output | `get_port_value(port_id)` |
| Resolve input | `get_input_value(pipeline, node_id, port_name)` — follows connections |
| Services | `get_service(name)` — injected at creation |
| Cancel | `cancel()` / `check_cancelled()` |
| Scope copy | `create_scoped_copy()` — child inherits values but has own port_values dict |

**Service injection keys** (set by PipelineController):
- `'workflow_facade'` — WorkflowFacade
- `'workflow_queue_service'` — WorkflowQueueService
- `'voxel_storage'` — DualResolutionVoxelStorage
- `'position_controller'` — PositionController (for reading current stage position)
- `'coordinate_config'` — dict with `display` and `stage_control` sections from visualization YAML (for voxel↔stage coordinate transforms)

### ScopeResolver (`engine/scope_resolver.py`)

Identifies which nodes belong to ForEach/Conditional scopes:

- **ForEach**: follows `current_item` and `index` output ports downstream → body nodes
- **Conditional**: follows `true_branch` and `false_branch` output ports → branch nodes

Key methods:
- `resolve()` → `Dict[owner_node_id, ScopeInfo]`
- `get_top_level_node_ids()` → nodes NOT in any scope (in topo order)
- `get_body_sorted(scope_owner_id)` → body nodes in topo order
- `get_branch_sorted(scope_owner_id, 'true'|'false')` → branch nodes

---

## Node Types & Runners

### 1. Workflow Node (`node_runners/workflow_runner.py`)

| | |
|---|---|
| **Config** | `template_file` (path to .txt workflow file), `use_input_position` (bool), `auto_z_range` (bool), `buffer_percent` (float) |
| **Inputs** | `trigger` (TRIGGER), `position` (POSITION, optional), `z_range` (OBJECT, optional — DetectedObject for bounding box) |
| **Outputs** | `volume` (VOLUME — dict of all channels), `file_path` (FILE_PATH), `completed` (TRIGGER) |

**Configuration UI:**
- **"Configure Workflow..." button** in PropertyPanel opens `PipelineWorkflowConfigDialog` — a full dialog embedding the same reusable panels (IlluminationPanel, CameraPanel, ZStackPanel, SavePanel) used by TileCollectionDialog
- The dialog saves a standard `.txt` workflow file to `~/.flamingo/pipelines/workflow_templates/` and sets the `template_file` path on the node
- Alternatively, users can manually browse to any existing `.txt` workflow file
- Position fields in the template are placeholders (0,0,0) — overridden at runtime

**Behavior:**
- Loads workflow from `.txt` template file via `WorkflowFacade.load_workflow()`
- If `position` input connected and `use_input_position=True`, overrides workflow position
- Accepts `DetectedObject` on position input (extracts `centroid_stage`)
- **Z-range auto-override**: if `auto_z_range=True` and a DetectedObject with bounding box is available, extracts Z extent from bounding box + configurable buffer (`buffer_percent`, default 25%) and applies to workflow Z range
- Starts workflow via `WorkflowFacade.start_workflow()`
- Polls `get_workflow_status()` until COMPLETED/IDLE/STOPPED (1s interval, 30min timeout)
- Outputs multi-channel volume dict (all channels with data) from voxel_storage
- **Legacy**: old pipelines with `config_mode='inline'` are detected and skipped with a warning

**Example workflow templates** are provided in `workflows/`:
- `PipelineZStack.txt` — default pipeline Z-stack (3 lasers, 500µm range, placeholder positions)

### 2. Threshold Node (`node_runners/threshold_runner.py`)

| | |
|---|---|
| **Config** | `channel_thresholds` (dict), `enabled_channels` (list), `gauss_sigma`, `opening_enabled`, `opening_radius`, `min_object_size`, `default_threshold` |
| **Inputs** | `volume` (VOLUME, optional — falls back to voxel_storage) |
| **Outputs** | `objects` (OBJECT_LIST), `mask` (VOLUME), `count` (SCALAR) |

**Behavior:**
- Filters `channel_thresholds` by `enabled_channels` list before processing (UI has per-channel enable checkbox)
- If volume input connected: uses it for all configured channel thresholds
- If unconnected: reads from `voxel_storage` service per channel
- **Coordinate transforms**: builds `voxel_to_stage_fn` from `coordinate_config` service, using project conventions (Y inverted, X optionally inverted per `invert_x_default`). Passes to `ThresholdAnalysisService.analyze()` so detected objects have real stage coordinates in `centroid_stage` (no longer `(0,0,0)`)
- Gets `voxel_size_um` from coordinate config display section

### 3. ForEach Node (`node_runners/foreach_runner.py`)

| | |
|---|---|
| **Config** | (none) |
| **Inputs** | `collection` (OBJECT_LIST, **required**) |
| **Outputs** | `current_item` (OBJECT), `index` (SCALAR), `completed` (TRIGGER) |

**Behavior:**
- Gets body nodes from ScopeResolver
- For each item in collection:
  - Creates scoped context copy
  - Injects `current_item` and `index` port values
  - Calls `executor.execute_subgraph(body_sorted, scoped_context)`
- Emits `foreach_iteration` signal each iteration

### 4. Conditional Node (`node_runners/conditional_runner.py`)

| | |
|---|---|
| **Config** | `comparison_op` (>, <, ==, !=, >=, <=), `threshold_value` |
| **Inputs** | `value` (ANY, **required**), `threshold` (SCALAR, optional) |
| **Outputs** | `true_branch` (TRIGGER), `false_branch` (TRIGGER), `pass_through` (ANY) |

**Behavior:**
- Gets threshold from port (if connected) or from config `threshold_value`
- Evaluates `value <op> threshold`
- Fires matching branch trigger
- Passes input value through on `pass_through`
- Executes matching branch subgraph via ScopeResolver

### 5. External Command Node (`node_runners/external_command_runner.py`)

| | |
|---|---|
| **Config** | `command_template`, `input_format` (numpy/tiff/json), `output_format` (json/csv/numpy), `timeout_seconds` |
| **Inputs** | `input_data` (ANY), `trigger` (TRIGGER) |
| **Outputs** | `output_data` (ANY), `file_path` (FILE_PATH), `completed` (TRIGGER) |

**Behavior:**
- Creates temp directory
- Serializes input to temp file (numpy `.npy`, TIFF, or JSON)
- Runs command via `subprocess.run()` with `{input_file}` and `{output_dir}` placeholders
- Parses first output file in output_dir
- Command template example: `python3 my_script.py --input {input_file} --outdir {output_dir}`

### 6. Sample View Data Node (`node_runners/sample_view_data_runner.py`)

| | |
|---|---|
| **Config** | `channel_0`..`channel_3` (bool — which channels to include) |
| **Inputs** | (none — source node) |
| **Outputs** | `volume` (VOLUME — Dict[int, np.ndarray]), `position` (POSITION — (x,y,z,r) tuple), `config` (ANY — coordinate config dict) |

**Behavior:**
- Source node that reads current 3D viewer state (no inputs required)
- Reads volumes from `voxel_storage` for each enabled channel (checks `has_data()` first)
- Reads current stage position from `position_controller.get_current_position()`
- Passes coordinate config (voxel_size_um, stage ranges, invert_x) as config output
- Designed to feed into Threshold → ForEach → Workflow pipelines

---

## Threshold Analysis Service

**Location:** `pipeline/services/threshold_analysis_service.py`

Extracted from `UnionThresholderDialog._recompute_mask()`. Same pipeline:

```
Per-channel:  smooth → threshold → boolean mask → label (ch_id+1)
Post-union:   morphological opening → small object removal → per-component extraction
```

**NEW addition over dialog code:** After filtering, uses `ndimage.label()` + `ndimage.find_objects()` + `ndimage.center_of_mass()` to produce `List[DetectedObject]`.

**API:**
```python
service = ThresholdAnalysisService()
result = service.analyze(
    volumes={0: vol_ch0, 1: vol_ch1},   # channel_id -> 3D array
    settings=ThresholdSettings(
        channel_thresholds={0: 200, 1: 300},
        gauss_sigma=1.0,
        opening_enabled=True,
        opening_radius=2,
        min_object_size=100,
    ),
    voxel_size_um=(50.0, 50.0, 50.0),   # for volume_mm3 calculation
    voxel_to_stage_fn=my_converter,       # optional (z,y,x) -> (sx,sy,sz)
)
# result.combined_mask: np.ndarray (bool)
# result.labels: np.ndarray (int32)
# result.objects: List[DetectedObject]
# result.object_count: int
```

**Dialog refactoring:** `UnionThresholderDialog._recompute_mask()` now:
1. Gathers volumes and thresholds from UI controls
2. Creates `ThresholdSettings`
3. Calls `ThresholdAnalysisService().analyze()`
4. Updates `_current_mask`, `_current_labels`, napari visualization

No behavioral change for existing users.

---

## Node Graph Editor UI

### PipelineEditorDialog (`ui/pipeline_editor_dialog.py`)

Three-panel layout via QSplitter, with toolbar at top and compact log at bottom:

```
┌──────────────────────────────────────────────────────┐
│ [New] [Open] [Save] [Validate] [Run] [Stop]   Ready │  ← toolbar bar
├──────────┬──────────────────────────┬────────────────┤
│ Node     │  · · · · · · · · · · ·  │ Properties     │
│ Types    │  · · · · · · · · · · ·  │                │
│          │  · · ┌──────┐──►┌─────┐ │ Name: [____]   │
│ Workflow │  · · │Acq   │   │Thr  │ │ Type: [combo]  │
│ Threshold│  · · └──────┘   └─────┘ │ Sigma: [spin]  │
│ ForEach  │  · · · · · · · · · · ·  │ ...            │
│ Condtnl  │  · · · · · · · · · · ·  │                │
│ ExtCmd   │  (dot grid canvas)       │                │
│          │                          │ Select a node  │
│ Drag     │  "Drag node types from   │ to edit its    │
│ items... │   the palette..."        │ properties     │
├──────────┴──────────────────────────┴────────────────┤
│ Pipeline execution log...                            │  ← compact 80px
└──────────────────────────────────────────────────────┘
```

**Key UI features:**
- **Toolbar** at top with styled background and separator border
- **Dot grid canvas** — dark background (#252525) with subtle dot grid at 30px spacing; visually distinct from other panels
- **Empty canvas hint** — centered text appears when no nodes exist, auto-hides once first node is dropped
- **Palette hint** — "Drag items into the canvas to add pipeline nodes" below the node type list; open-hand cursor on list items
- **Property panel hint** — "Select a node on the canvas to edit its properties" when nothing selected
- **Run/Stop buttons** — white text on green/red background; disabled state is uniform gray (not colored)
- **Status label** — bold green "Ready" / bold blue "Running..."
- **Compact log** — fixed 80px height with placeholder text and top border separator

**Keyboard:** Delete/Backspace removes selected node or connection, Escape cancels drag

Extends `PersistentDialog` for geometry save/restore.

### Graph Interactions

| Action | Behavior |
|--------|----------|
| Drag from NodePalette | Creates node at drop position; canvas hint disappears |
| Left-click node | Select → shows config in PropertyPanel |
| Drag node | Move (updates model x,y, redraws wires) |
| Left-drag from output port | Draws temporary wire; green highlight on compatible targets |
| Release on compatible input | Creates Connection in model + visual wire |
| Middle-drag or Right-drag | Pan canvas |
| Scroll wheel | Zoom (0.2x – 3.0x, anchored under mouse, no Ctrl required) |
| Right-click (no drag) on node | Context menu: Delete Node |
| Right-click on connection | Context menu: Delete Connection |
| Right-click on empty canvas | Context menu: Add Node (all 6 types), Fit to Content, Reset Zoom |
| Delete/Backspace | Remove selected connection or node |

### Visual Design

| Element | Appearance |
|---------|-----------|
| **Canvas** | Dark background (#252525) with subtle dot grid (#333, 30px spacing) |
| **Empty canvas** | Centered hint text in gray (#555): "Drag node types from the palette onto this canvas to build a pipeline" |
| **Node body** | Dark rounded rect (#2d2d2d), 180px wide |
| **Node header** | Colored by type: blue (Workflow), orange (Threshold), purple (ForEach), yellow (Conditional), green (External), teal (Sample View Data) |
| **Ports** | Small colored circles (color = port type), left=inputs, right=outputs |
| **Wires** | Cubic bezier curves, color matches source port type |
| **Status dot** | Top-right of header: gray=idle, blue=running, green=completed, red=error |
| **Selection** | Blue border highlight (#4fc3f7) |
| **Toolbar** | Dark background (#2a2a2a) with bottom border; styled Run (green) and Stop (red) buttons |

### PropertyPanel Config Schemas

| Node Type | Config Fields |
|-----------|--------------|
| Workflow | **"Configure Workflow..." button** (opens full dialog with IlluminationPanel, CameraPanel, ZStackPanel, SavePanel), template_file (file browser), use_input_position (bool), auto_z_range (bool), buffer_percent (float) |
| Threshold | gauss_sigma (float), opening_enabled (bool), opening_radius (int), min_object_size (int), default_threshold (int), + per-channel: enable checkbox + threshold spinbox (ch 0-3) |
| ForEach | (none — auto-configured) |
| Conditional | comparison_op (combo: >,<,==,!=,>=,<=), threshold_value (float) |
| External Command | command_template (text), input_format (combo), output_format (combo), timeout_seconds (int) |
| Sample View Data | channel_0..channel_3 (bool checkboxes — which channels to read from viewer) |

**Widget types** available in property panel schemas: `str`, `int`, `float`, `bool`, `combo`, `file` (QLineEdit + browse button with file filter), `folder` (QLineEdit + browse button for directories), `header` (bold section label).

---

## Application Integration

### Modified Files

| File | Change |
|------|--------|
| `services/component_factory.py` | Added `create_pipeline_layer(app)` → returns `pipeline_service`, `pipeline_controller` |
| `application.py` | Imports `create_pipeline_layer`, adds `pipeline_service`/`pipeline_controller` attrs, calls factory after signal wiring |
| `main_window.py` | Added "Pipeline Editor..." action under Extensions menu + `_on_pipeline_editor()` handler |
| `services/signal_wiring.py` | Added `wire_pipeline_signals(app)` (currently a stub — controller manages its own internal wiring) |
| `views/dialogs/union_thresholder_dialog.py` | Refactored `_recompute_mask()` to delegate to ThresholdAnalysisService |

### Startup Flow

```
FlamingoApplication.setup_dependencies()
  → create_core_layer()
  → create_models_layer()
  → create_services_layer()
  → create_controllers_layer()
  → create_views_layer()
  → wire_all_signals()           # includes wire_pipeline_signals()
  → create_pipeline_layer(app)   # creates PipelineService + PipelineController
```

### Menu Access

Extensions → Pipeline Editor...

The Pipeline Editor is **always available** (no connection or Sample View required) since pipelines can be designed offline and saved as JSON.

The handler uses lazy creation: if `pipeline_controller` exists on `app`, calls `open_editor()`. Otherwise creates the components on the fly.

---

## JSON Pipeline Format

```json
{
  "name": "Example: Acquire-Analyze-Reacquire",
  "nodes": [
    {
      "id": "uuid-string",
      "node_type": "WORKFLOW",
      "name": "Initial Acquisition",
      "inputs": [
        {"id": "uuid", "name": "trigger", "port_type": "TRIGGER", "direction": "INPUT", "required": false},
        {"id": "uuid", "name": "position", "port_type": "POSITION", "direction": "INPUT", "required": false}
      ],
      "outputs": [
        {"id": "uuid", "name": "volume", "port_type": "VOLUME", "direction": "OUTPUT", "required": false},
        {"id": "uuid", "name": "file_path", "port_type": "FILE_PATH", "direction": "OUTPUT", "required": false},
        {"id": "uuid", "name": "completed", "port_type": "TRIGGER", "direction": "OUTPUT", "required": false}
      ],
      "config": {"workflow_type": "zstack"},
      "x": 50.0,
      "y": 100.0
    }
  ],
  "connections": [
    {
      "id": "uuid-string",
      "source_node_id": "uuid-of-source-node",
      "source_port_id": "uuid-of-source-port",
      "target_node_id": "uuid-of-target-node",
      "target_port_id": "uuid-of-target-port"
    }
  ]
}
```

Pipelines are saved to `~/.flamingo/pipelines/` by default (configurable via `PipelineRepository`).

---

## Key Design Decisions

### 1. Non-breaking overlay
The pipeline system calls INTO existing services (WorkflowFacade, WorkflowQueueService) rather than replacing them. No existing code paths were changed except the thresholder refactoring (which preserves identical behavior).

### 2. Scope-based ForEach/Conditional
Rather than a flat DAG walk, ForEach/Conditional nodes OWN subgraphs identified by ScopeResolver. This allows:
- ForEach to re-execute body nodes N times with different context
- Conditional to execute only the matching branch
- Nested ForEach/Conditional (scopes compose naturally)

### 3. Typed ports with compatibility matrix
Connections are validated at creation time (not just execution). The compatibility matrix allows useful implicit conversions (OBJECT→POSITION, TRIGGER→anything) while catching actual errors.

### 4. Scoped context copies
ForEach iterations get independent copies of port_values (via `create_scoped_copy()`) so one iteration's intermediate results don't clobber another's. Services are shared by reference.

### 5. Service extraction pattern
ThresholdAnalysisService was extracted so the same analysis pipeline can be used from:
- UnionThresholderDialog (interactive, with napari visualization)
- ThresholdRunner (programmatic, in pipeline execution)
- Any future code that needs threshold + component analysis

### 6. Always-available editor
Pipeline Editor doesn't require connection or Sample View, unlike other Extensions. Users can design and save pipelines offline.

---

## Extension Guide

### Adding a New Node Type

1. **Add enum value** in `models/pipeline.py`:
   ```python
   class NodeType(Enum):
       ...
       MY_NEW_TYPE = auto()
   ```

2. **Add default ports** in `create_default_ports()` in `models/pipeline.py`:
   ```python
   elif node_type == NodeType.MY_NEW_TYPE:
       inputs = [_make_port('input1', PortType.VOLUME, inp)]
       outputs = [_make_port('result', PortType.SCALAR, out)]
   ```

3. **Add node color** in `NODE_COLORS` in `models/pipeline.py`

4. **Create runner** in `engine/node_runners/my_runner.py`:
   ```python
   class MyRunner(AbstractNodeRunner):
       def run(self, node, pipeline, context):
           data = self._get_input(node, pipeline, context, 'input1')
           result = do_something(data)
           self._set_output(node, context, 'result', PortType.SCALAR, result)
   ```

5. **Register runner** in `controllers/pipeline_controller.py`:
   ```python
   runners = {
       ...
       NodeType.MY_NEW_TYPE: MyRunner(),
   }
   ```

6. **Add config schema** in `ui/property_panel.py`:
   ```python
   _CONFIG_SCHEMAS[NodeType.MY_NEW_TYPE] = [
       ('param1', 'Parameter 1', 'float', 0.0),
   ]
   ```

7. **Add description** in `ui/node_palette.py`:
   ```python
   _NODE_DESCRIPTIONS[NodeType.MY_NEW_TYPE] = "Description here"
   ```

### Adding a New Port Type

1. Add to `PortType` enum in `models/port_types.py`
2. Add compatibility rules via `_allow()` calls
3. Add color to `PORT_COLORS`
4. Identity connection is auto-registered for all types

### Injecting New Services

In `controllers/pipeline_controller.py`, add to the services dict:
```python
services['my_service'] = self._app.my_service
```
Then access in any runner via:
```python
svc = context.get_service('my_service')
```

---

## File Reference

| File | Lines | Purpose |
|------|-------|---------|
| `models/port_types.py` | ~100 | PortType enum, compatibility, colors |
| `models/pipeline.py` | ~440 | Pipeline graph model + algorithms (6 node types) |
| `models/detected_object.py` | ~115 | DetectedObject dataclass + coordinate helpers |
| `engine/executor.py` | ~140 | PipelineExecutor QThread |
| `engine/context.py` | ~100 | ExecutionContext per-run state |
| `engine/scope_resolver.py` | ~155 | ForEach/Conditional scope identification |
| `engine/node_runners/base_runner.py` | ~60 | AbstractNodeRunner ABC |
| `engine/node_runners/workflow_runner.py` | ~215 | WorkflowFacade delegation, template loading, position + z-range override |
| `engine/node_runners/threshold_runner.py` | ~120 | ThresholdAnalysisService + coordinate transforms |
| `engine/node_runners/foreach_runner.py` | ~95 | Collection iteration |
| `engine/node_runners/conditional_runner.py` | ~100 | Branch evaluation |
| `engine/node_runners/external_command_runner.py` | ~125 | Subprocess I/O |
| `engine/node_runners/sample_view_data_runner.py` | ~80 | Reads current 3D viewer state |
| `services/threshold_analysis_service.py` | ~170 | Extracted threshold pipeline |
| `services/pipeline_repository.py` | ~80 | JSON file persistence |
| `services/pipeline_service.py` | ~110 | Facade + example pipeline |
| `ui/pipeline_editor_dialog.py` | ~360 | Top-level editor dialog (toolbar, splitter, log) |
| `ui/graph_scene.py` | ~290 | QGraphicsScene with dot grid + empty hint |
| `ui/graph_view.py` | ~220 | Pan/zoom/drag view + right-click context menu |
| `ui/node_item.py` | ~190 | Node rectangle rendering |
| `ui/port_item.py` | ~95 | Port circle rendering |
| `ui/connection_item.py` | ~115 | Bezier wire rendering |
| `ui/node_palette.py` | ~115 | Drag sidebar with hint text |
| `ui/property_panel.py` | ~370 | Dynamic config form with file/folder browsers, "Configure Workflow..." button |
| `ui/workflow_config_dialog.py` | ~265 | Full workflow config dialog (embeds IlluminationPanel, CameraPanel, ZStackPanel, SavePanel) |
| `controllers/pipeline_controller.py` | ~145 | UI ↔ Engine mediator + coordinate config loader |

**Total new code:** ~4,500 lines across 32 files
**Modified existing code:** ~125 lines changed across 5 files
