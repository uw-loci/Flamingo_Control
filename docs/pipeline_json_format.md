# Pipeline JSON Format Reference

## Overview

The Flamingo Control pipeline system lets you build visual processing graphs — directed acyclic graphs (DAGs) where nodes represent acquisition or analysis steps and typed connections carry data between them. Pipelines are saved as JSON files to `~/.flamingo/pipelines/`.

This document describes the JSON format so pipelines can be created or edited outside the GUI.

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

**Default Ports:**

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| INPUT | trigger | TRIGGER | no |
| INPUT | position | POSITION | no |
| INPUT | z_range | OBJECT | no |
| OUTPUT | volume | VOLUME | — |
| OUTPUT | file_path | FILE_PATH | — |
| OUTPUT | completed | TRIGGER | — |

**Config Properties:**

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `template_file` | string | `""` | Path to workflow template (.txt) |
| `use_input_position` | bool | `true` | Override stage position from input port |
| `auto_z_range` | bool | `false` | Derive Z-range from input object bounding box |
| `auto_tiling` | bool | `false` | Auto-compute tiling grid from object XY extent vs. FOV. If object fits in one FOV, stays single-tile; otherwise applies buffer and computes NxM grid |
| `buffer_percent` | float | `25.0` | Extra bounding box buffer (%) for Z-range and XY tiling |

### THRESHOLD

Applies threshold analysis to a volume, producing detected objects.

**Default Ports:**

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| INPUT | volume | VOLUME | no |
| OUTPUT | objects | OBJECT_LIST | — |
| OUTPUT | mask | VOLUME | — |
| OUTPUT | count | SCALAR | — |

**Config Properties:**

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `gauss_sigma` | float | `0.0` | Gaussian blur sigma |
| `opening_enabled` | bool | `false` | Enable morphological opening |
| `opening_radius` | int | `1` | Opening structuring element radius |
| `min_object_size` | int | `0` | Minimum object size in voxels |
| `default_threshold` | int | `100` | Fallback threshold value |
| `channel_thresholds` | object | `{}` | Per-channel thresholds (keys are **string** channel IDs: `"0"`, `"1"`, etc.) |
| `enabled_channels` | array | `[0,1,2,3]` | List of enabled channel indices |

### FOR_EACH

Iterates over a collection, emitting one item at a time to downstream nodes.

**Default Ports:**

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| INPUT | collection | OBJECT_LIST | **yes** |
| OUTPUT | current_item | OBJECT | — |
| OUTPUT | index | SCALAR | — |
| OUTPUT | completed | TRIGGER | — |

**Config Properties:** None.

### CONDITIONAL

Branches execution based on comparing a value against a threshold.

**Default Ports:**

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| INPUT | value | ANY | **yes** |
| INPUT | threshold | SCALAR | no |
| OUTPUT | true_branch | TRIGGER | — |
| OUTPUT | false_branch | TRIGGER | — |
| OUTPUT | pass_through | ANY | — |

**Config Properties:**

| Key | Type | Default | Options | Description |
|-----|------|---------|---------|-------------|
| `comparison_op` | string | `">"` | `>`, `<`, `==`, `!=`, `>=`, `<=` | Comparison operator |
| `threshold_value` | float | `0.0` | — | Comparison threshold |

### EXTERNAL_COMMAND

Runs an external shell command, passing data in/out via files.

**Default Ports:**

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| INPUT | input_data | ANY | no |
| INPUT | trigger | TRIGGER | no |
| OUTPUT | output_data | ANY | — |
| OUTPUT | file_path | FILE_PATH | — |
| OUTPUT | completed | TRIGGER | — |

**Config Properties:**

| Key | Type | Default | Options | Description |
|-----|------|---------|---------|-------------|
| `command_template` | string | `""` | — | Shell command to execute |
| `input_format` | string | `"numpy"` | `numpy`, `tiff`, `json` | Format for input data serialization |
| `output_format` | string | `"json"` | `json`, `csv`, `numpy` | Format for output data deserialization |
| `timeout_seconds` | int | `300` | — | Max execution time in seconds |

### SAMPLE_VIEW_DATA

Reads current volume data and position from the 3D sample view. Has no inputs — this is a data source node.

**Default Ports:**

| Direction | Name | Port Type | Required |
|-----------|------|-----------|----------|
| OUTPUT | volume | VOLUME | — |
| OUTPUT | position | POSITION | — |
| OUTPUT | config | ANY | — |

**Config Properties:**

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `channel_0` | bool | `true` | Include 405nm (DAPI) channel |
| `channel_1` | bool | `true` | Include 488nm (GFP) channel |
| `channel_2` | bool | `true` | Include 561nm (RFP) channel |
| `channel_3` | bool | `true` | Include 640nm (Far-Red) channel |

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
