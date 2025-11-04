# Old Code Integration Analysis

This document provides a comprehensive analysis of functions from `oldcodereference` and their status in the current codebase.

## Summary

- **Total Functions Analyzed**: 18
- **Already Implemented**: 8
- **Need Implementation**: 10
- **New Services Required**: 4

---

## Already Implemented Functions ✓

These functions are already implemented in the current MVC architecture and **DO NOT need duplication**:

### 1. Position Management
| Old Function | Current Location | Status |
|-------------|------------------|--------|
| `go_to_position()` | `PositionController.go_to_position()` | ✓ Complete |
| `go_to_XYZR()` | `PositionController.go_to_xyzr()` | ✓ Complete |
| `move_axis()` | `PositionController._move_axis()` | ✓ Complete |

**File**: `src/py2flamingo/controllers/position_controller.py`

### 2. Settings Management
| Old Function | Current Location | Status |
|-------------|------------------|--------|
| `set_home()` | `SettingsController.set_home_position()` | ✓ Complete |

**File**: `src/py2flamingo/controllers/settings_controller.py`

### 3. Connection Management
| Old Function | Current Location | Status |
|-------------|------------------|--------|
| `start_connection()` | `ConnectionService.connect()` | ✓ Complete |
| `close_connection()` | `ConnectionService.disconnect()` | ✓ Complete |
| `get_microscope_settings()` | `ConnectionService.get_microscope_settings()` | ✓ Complete |

**File**: `src/py2flamingo/services/connection_service.py`

### 4. Image Acquisition (Partial)
| Old Function | Current Location | Status |
|-------------|------------------|--------|
| `take_snapshot()` | `SnapshotController.take_snapshot()` | ⚠️ Needs Enhancement |

**File**: `src/py2flamingo/controllers/snapshot_controller.py`
**Note**: Exists but needs full workflow integration features from old code.

---

## Functions That Need Implementation

These functions are **missing** from the current codebase and need to be added:

### Category A: Workflow Execution (High Priority)

#### 1. `check_workflow()`
**Source**: `microscope_interactions.py:80-92`
**Purpose**: Validates workflow before sending to microscope
**Destination**: New `WorkflowExecutionService`
**Priority**: High

#### 2. `send_workflow()`
**Source**: `microscope_interactions.py:94-112`
**Purpose**: Sends workflow and waits for system idle
**Destination**: New `WorkflowExecutionService`
**Priority**: High

#### 3. `resolve_workflow()`
**Source**: `microscope_interactions.py:114-133`
**Purpose**: Waits for workflow completion and retrieves image
**Destination**: New `WorkflowExecutionService`
**Priority**: High

---

### Category B: System Initialization (High Priority)

#### 4. `initial_setup()`
**Source**: `microscope_interactions.py:14-77`
**Purpose**: Generates command codes, retrieves settings, calculates FOV and movement parameters
**Destination**: New `MicroscopeInitializationService`
**Priority**: High
**Returns**:
- command_labels
- ymax (Y boundary)
- y_move (FOV-based step size)
- image_pixel_size_mm
- frame_size

---

### Category C: Sample Search/Scanning (Medium Priority)

#### 5. `y_axis_sample_boundary_search()`
**Source**: `microscope_interactions.py:159-251`
**Purpose**: Scans Y-axis to locate sample boundaries using intensity analysis
**Destination**: New `SampleSearchService`
**Priority**: Medium
**Features**:
- Rolling Y intensity calculation
- Peak detection
- Boundary finding
- MIP (Maximum Intensity Projection) handling

#### 6. `z_axis_sample_boundary_search()`
**Source**: `microscope_interactions.py:255-331`
**Purpose**: Scans Z-axis to find optimal focus plane
**Destination**: New `SampleSearchService`
**Priority**: Medium
**Features**:
- Z-stack acquisition
- Brightness tracking
- Peak detection for optimal focus

---

### Category D: Image Acquisition (Medium Priority)

#### 7. `acquire_brightfield_image()`
**Source**: `microscope_interactions.py:333-389`
**Purpose**: Acquires brightfield image with LED (laser off)
**Destination**: New `ImageAcquisitionService`
**Priority**: Medium

---

### Category E: Utilities (Low Priority)

#### 8. `replace_none()`
**Source**: `microscope_interactions.py:135-157`
**Purpose**: Utility to replace None values in boundary lists
**Destination**: `src/py2flamingo/utils/calculations.py`
**Priority**: Low

#### 9. `send_command()` (Simple wrapper)
**Source**: `microscope_connect.py:161-165`
**Purpose**: Simple wrapper to send command and wait
**Destination**: `ConnectionService` (as a method)
**Priority**: Low

---

## New Services/Files Required

Based on the analysis, the following new services should be created:

### 1. WorkflowExecutionService
**File**: `src/py2flamingo/services/workflow_execution_service.py`
**Methods**:
- `check_workflow(workflow_dict) -> bool`
- `send_workflow(workflow_dict) -> None`
- `resolve_workflow(timeout) -> image_data`
- `wait_for_system_idle() -> None`

**Dependencies**:
- ConnectionService
- QueueManager
- EventManager
- WorkflowService

---

### 2. MicroscopeInitializationService
**File**: `src/py2flamingo/services/initialization_service.py`
**Methods**:
- `initial_setup() -> InitializationData`
- `load_command_codes() -> dict`
- `calculate_fov_parameters() -> tuple`
- `get_stage_limits() -> dict`

**Dependencies**:
- ConnectionService
- ConfigurationService

**Returns**: `InitializationData` dataclass with:
- command_codes
- stage_limits
- fov_parameters
- pixel_size
- frame_size

---

### 3. SampleSearchService
**File**: `src/py2flamingo/services/sample_search_service.py`
**Methods**:
- `scan_y_axis(params) -> boundaries`
- `scan_z_axis(params) -> optimal_z`
- `find_sample_boundaries(num_samples) -> list`
- `optimize_focus(position) -> Position`

**Dependencies**:
- WorkflowExecutionService
- ImageAcquisitionService
- Calculations utilities
- PositionController

---

### 4. ImageAcquisitionService
**File**: `src/py2flamingo/services/image_acquisition_service.py`
**Methods**:
- `acquire_snapshot(position, laser_settings) -> image_data`
- `acquire_brightfield(position) -> image_data`
- `acquire_zstack(position, params) -> image_stack`

**Dependencies**:
- WorkflowExecutionService
- ConnectionService
- SnapshotController (enhance existing)

---

## Dependencies Between New Services

```
MicroscopeInitializationService
    ↓
SampleSearchService → ImageAcquisitionService → WorkflowExecutionService
                                                      ↓
                                              ConnectionService
```

---

## Implementation Priority

### Phase 1: Core Workflow (Required for GUI)
1. **WorkflowExecutionService** - Critical for all operations
2. **MicroscopeInitializationService** - Required at startup
3. Enhance **SnapshotController** with workflow integration

### Phase 2: Image Acquisition (Required for GUI)
4. **ImageAcquisitionService** - Needed for live view and snapshots

### Phase 3: Advanced Features (Post-GUI)
5. **SampleSearchService** - Advanced scanning features

### Phase 4: Utilities
6. Add utility functions to existing modules

---

## GUI Requirements Met by These Functions

Once implemented, the GUI will be able to:

1. ✓ Move stage during live view (PositionController)
2. ✓ Change laser selection and power (Settings + Workflow)
3. ✓ Take snapshots (ImageAcquisitionService)
4. ✓ Acquire brightfield images (ImageAcquisitionService)
5. ✓ Pull current settings on connect (MicroscopeInitializationService)
6. ✓ Display current position and state (MicroscopeModel)
7. ✓ Set and recall home position (SettingsController)

---

## Notes

- **No Duplicates Found**: All "already implemented" functions should be used as-is
- **MVC Architecture**: All new services follow the Model-View-Controller pattern
- **Testing**: Each new service will need corresponding unit tests
- **Thread Safety**: Services using queues/events need proper synchronization

---

## Next Steps

1. Create new service files (4 services)
2. Assign agents to implement each service
3. Write unit tests for each service
4. Integration testing with existing controllers
5. GUI implementation (separate phase)
