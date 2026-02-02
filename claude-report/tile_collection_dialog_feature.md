# TileCollectionDialog Feature Report

## Overview
The TileCollectionDialog ("Collect Tiles - Workflow Configuration") allows users to select tiles from LED 2D Overview scans and generate Z-stack workflows for automated acquisition.

## Recent Fixes (2026-01-12)

### 1. Camera Exposure Auto-Detection - FIXED

**Problem:** Camera exposure was always returning 0.0 us.

**Root Cause:** Two issues identified by examining C++ server code:
1. Wrong command code: Python used `0x3002` (12290), but C++ uses `0x300A` (12298)
2. Wrong response field: Python read from `value` (doubleData), but C++ returns exposure in `int32Data0`

**C++ Reference:** `PCOBase.cpp:949`
```cpp
pscmd->int32Data0 = (int32_t) exposure;  // in microseconds
```

**Files Modified:**
- `src/py2flamingo/services/camera_service.py` - Fixed command code and parsing
- `src/py2flamingo/core/command_codes.py` - Fixed constant
- `src/py2flamingo/core/socket_reader.py` - Fixed constant

**Result:** Camera now correctly reports exposure (e.g., 9002 us) and calculates frame rate (40 fps).

### 2. Settings Persistence - WORKING

All dialog settings now persist between opens:
- Workflow type (defaults to Z-Stack)
- Illumination settings (lasers, LED, paths)
- Camera settings (exposure, AOI, dual camera)
- Z-Stack settings (step size, velocity, options)
- Save settings (drive, directory, format)

Uses `WindowGeometryManager.save_dialog_state()` / `restore_dialog_state()`.

---

## CRITICAL: Workflow Command Structure (2026-01-14)

### The Problem
Workflows sent from Python were not executing. Server log showed:
```
cmdDataBits0 = 0x00000000
additionalDataBytes = 0
Work flow empty
Command data is invalid, unable to proceed
```

### Root Cause Analysis
Compared server logs between Python (failed) and C++ GUI (succeeded):

| Field | Python (Failed) | C++ GUI (Success) | Fix |
|-------|-----------------|-------------------|-----|
| `cmdDataBits0` (params[6]) | 0x00000000 | **0x80000000** | TRIGGER_CALL_BACK flag required! |
| `additionalDataBytes` | 0 | 2204 | Must contain file size |
| 72-byte data buffer | file_size packed | empty | Must be empty |
| File read mode | text (CRLF→LF) | binary | Preserve original line endings |

### The Fix
**File:** `src/py2flamingo/services/microscope_command_service.py`

```python
# Build params to match C++ GUI (from server log):
# - params[0-5] = 0 (hardwareID, subsystemID, clientID, int32Data0-2)
# - params[6] = 0x80000000 (cmdDataBits0 = TRIGGER_CALL_BACK flag!)
params = [0, 0, 0, 0, 0, 0, 0x80000000]  # TRIGGER_CALL_BACK

# Empty buffer (NOT file size!)
empty_buffer = b'\x00' * 72

# File size goes in addDataBytes field, NOT the buffer
cmd_bytes = self.connection.encoder.encode_command(
    code=command_code,
    status=0,
    params=params,
    value=0.0,
    data=empty_buffer,  # Empty buffer!
    additional_data_size=file_size  # File size here!
)
```

### Reference Files
- **Server log comparison:** `oldcodereference/LogFileExamples/ServerWorkflowResponse.txt`
- **Old working Python code:** `oldcodereference/tcpip_nuc.py` (lines 55-92)
- **Working workflow file:** `workflows/WorkflowZstack.txt`

### Key Learnings
1. **TRIGGER_CALL_BACK (0x80000000)** is REQUIRED for workflow commands
2. **File size goes in `additionalDataBytes`** field, not the 72-byte buffer
3. **Read workflow files as binary** to preserve CRLF line endings
4. **Old tcpip_nuc.py used cmdDataBits0=1**, but C++ GUI uses 0x80000000

---

## Position Polling During Workflows (2026-01-14)

### The Problem
During server-controlled workflows, the stage moves but the Sample View doesn't update position because:
1. Server moves stage directly
2. Python's cached position doesn't know about server movements
3. Need to actively query hardware position

### Solution
Added workflow position polling to `movement_controller.py`:

```python
def start_workflow_polling(self, interval: float = 2.0) -> None:
    """Start polling hardware position during workflow execution."""
    # Queries actual hardware position every 2 seconds
    # Emits position_changed signal for Sample View updates

def stop_workflow_polling(self) -> None:
    """Stop polling when workflow completes."""
```

### Integration
- `status_indicator_service.on_workflow_started()` → starts polling
- `status_indicator_service.on_workflow_stopped()` → stops polling
- Polling interval: 2 seconds (to avoid overwhelming server during acquisition)

### LED 2D Overview Compatibility
LED 2D Overview does NOT conflict because:
- Uses its own signals (`scan_started/scan_completed`)
- Moves stage via `stage_service.move_to_position()` directly
- Updates position after each tile capture
- Never triggers `on_workflow_started()` from status_indicator_service

---

## Workflow File Location

Generated workflows are saved to:
```
workflows/{save_directory}_{timestamp}/Workflow.txt
```

Note: Save drive is server-side storage, not accessible from Python. Workflows are saved locally in the project directory.

## Reference Workflow Format

See `/workflows/WorkflowZstack.txt` for the expected format that the server parses.

**Critical formatting requirements:**
- 2 spaces for section tags (`<Experiment Settings>`)
- 4 spaces for field names (`    Plane spacing (um) = 2.5`)
- LF or CRLF line endings (preserved when read as binary)
- Exposure with comma formatting (`9,002` not `9002`)
- All 7 lasers listed with format: `Laser N N: XXX nm MLE = power enabled`

---

## Sequential Workflow Execution (2026-01-14)

### The Problem
When selecting multiple tiles from the LED 2D Overview, workflows were being sent simultaneously.
The server can only process one workflow at a time, so all but the first would fail:
```
Workflow 1: SUCCESS
Workflow 2: FAILED (server busy)
Workflow 3: FAILED (server busy)
```

### Solution: WorkflowQueueService
Created `src/py2flamingo/services/workflow_queue_service.py` that:
1. Queues multiple workflow files for sequential execution
2. Waits for each workflow to complete before starting the next
3. Detects completion by polling `SYSTEM_STATE_GET` (0xa007)
4. Emits Qt signals for progress tracking

### Key Components

**WorkflowQueueService:**
```python
# Enqueue workflows
queue_service.enqueue(workflow_files, metadata_list)

# Start execution (non-blocking)
queue_service.start()

# Cancel if needed
queue_service.cancel()

# Signals for progress
queue_service.progress_updated.connect(on_progress)  # (current, total, message)
queue_service.workflow_completed.connect(on_complete)  # (index, total, path)
queue_service.queue_completed.connect(on_done)
queue_service.error_occurred.connect(on_error)
```

**MVCConnectionService.query_system_state():**
```python
# Query system state for completion detection
result = connection_service.query_system_state()
# Returns: {'state': 0, 'is_idle': True}  # 0=IDLE, 1=BUSY, 2=ERROR
```

### Files Modified
- `src/py2flamingo/services/workflow_queue_service.py` - NEW
- `src/py2flamingo/services/connection_service.py` - Added `query_system_state()`
- `src/py2flamingo/views/dialogs/tile_collection_dialog.py` - Updated `_execute_workflows()`
- `src/py2flamingo/application.py` - Wire up WorkflowQueueService

### Integration with TileCollectionDialog
The dialog now uses `WorkflowQueueService` automatically when available:
- Shows progress dialog with cancel button
- Waits for each workflow to complete (polls system state)
- Falls back to estimated timing if queue service unavailable
- Supports Sample View integration for live Z-stack visualization

---

## Callback-Based Workflow Completion Detection (2026-01-15)

### The Problem
Polling `SYSTEM_STATE_GET` every 10 seconds was unreliable and potentially caused server issues.
The C++ GUI uses callback-based completion detection instead of polling.

### Solution: Listen for CAMERA_STACK_COMPLETE Callback

**Callback Code:** `0x3011` (12305) - CAMERA_STACK_COMPLETE

When the server finishes a workflow, it sends this callback containing:
- `int32Data0`: images acquired
- `int32Data1`: images expected
- `int32Data2`: error count
- `doubleData`: acquisition time (microseconds)

### Implementation

**Files Modified:**
- `src/py2flamingo/core/command_codes.py` - Added `CameraCommands.STACK_COMPLETE` and `UICommands`
- `src/py2flamingo/core/socket_reader.py` - Added progress callbacks to `UNSOLICITED_COMMANDS`
- `src/py2flamingo/services/workflow_queue_service.py` - Rewritten for callback-based detection
- `src/py2flamingo/services/connection_service.py` - Added `register_callback()` / `unregister_callback()`

**WorkflowQueueService Changes:**
```python
# Command codes for workflow progress
CAMERA_STACK_COMPLETE = 0x3011  # Stack acquisition complete
UI_SET_GAUGE_VALUE = 0x9004    # Progress bar update

def _on_stack_complete(self, message) -> None:
    """Handle CAMERA_STACK_COMPLETE callback from server."""
    self._completion_data = {
        'images_acquired': message.int32_data0,
        'images_expected': message.int32_data1,
        'error_count': message.int32_data2,
        'acquisition_time_us': message.double_data
    }
    self._completion_event.set()

def _register_callbacks(self) -> None:
    """Register callback handlers with connection service."""
    self._connection_service.register_callback(CAMERA_STACK_COMPLETE, self._on_stack_complete)
    self._connection_service.register_callback(UI_SET_GAUGE_VALUE, self._on_progress_update)
```

**Fallback:** If no callback received within 10 seconds, falls back to polling `SYSTEM_STATE_GET`.

### Progress Tracking (TODO)
The `UI_SET_GAUGE_VALUE` callback contains stage position data during acquisition.
Future enhancement: Update Sample View with current acquisition position from progress callbacks.

---

## Two-Point Position UI (2026-01-15)

### Overview
Added intuitive "pick two points" UI for defining workflow regions. Instead of entering abstract
parameters (number of planes, step size), users define **Point A** (start) and **Point B** (end) -
the system calculates everything else.

### New Component: DualPositionPanel

**File:** `src/py2flamingo/views/workflow_panels/dual_position_panel.py` (NEW)

Features:
- Position A (Start): X, Y, Z, R - always fully visible
- Position B (End): visibility controlled by workflow type
- "Use Current" button for each position
- "Load Saved" dropdown for each position (loads from saved presets)

### Mode Switching by Workflow Type

| Workflow Type | Position A | Position B | Auto-Calculated |
|---------------|------------|------------|-----------------|
| **Snapshot** | X, Y, Z, R | Hidden | Nothing |
| **Z-Stack** | X, Y, Z, R | Z only (X, Y, R greyed) | num_planes from Z range |
| **Tiling** | X, Y, Z, R | X, Y, Z (R greyed) | tiles_x, tiles_y from XY range |
| **Multi-Angle** | X, Y, Z, R | Z only | num_planes from Z range |

### Files Modified

- `src/py2flamingo/views/workflow_panels/dual_position_panel.py` - NEW panel
- `src/py2flamingo/views/workflow_panels/zstack_panel.py` - Added `set_two_point_mode()`, `set_z_range_from_positions()`
- `src/py2flamingo/views/workflow_panels/tiling_panel.py` - Added `set_two_point_mode()`, `set_from_positions()`
- `src/py2flamingo/views/workflow_view.py` - Replaced PositionPanel with DualPositionPanel
- `src/py2flamingo/application.py` - Connect preset service to workflow view

### User Experience

**Z-Stack workflow:**
1. Select "Z-Stack" → Position B shows only Z spinbox
2. "Use Current" for A → captures full XYZR
3. Move stage to end Z → "Use Current" for B → captures only Z
4. OR select saved position from dropdown
5. ZStackPanel auto-calculates: num_planes from Z range

**Tiling workflow:**
1. Select "Tile Scan" → Position B shows X, Y, Z
2. "Use Current" for A at top-left corner (or load saved)
3. "Use Current" for B at bottom-right corner (or load saved)
4. TilingPanel auto-calculates: tiles_x, tiles_y from XY range
5. ZStackPanel auto-calculates: num_planes from Z range per tile

---

## Bug Fixes (2026-01-15)

### ConnectionView._app AttributeError - FIXED

**Problem:** `Test Workflow Gen` button crashed with:
```
AttributeError: 'ConnectionView' object has no attribute '_app'
```

**Root Cause:** Line 924 referenced `self._app` which was never defined. The `_workflow_service`
attribute is already passed in the constructor.

**Fix:** Changed check from `self._app` to `self._workflow_service`:
```python
# Before (wrong):
if not self._app or not hasattr(self._app, 'workflow_service'):

# After (correct):
if not self._workflow_service:
```

**File:** `src/py2flamingo/views/connection_view.py`

---

## LED 2D Overview Result Window

### Save Button Dropdown - NEW
Replaced 4 separate save buttons with single "Save" dropdown:
- **Whole Session** (default) - saves all images + metadata for later loading
- **Initial image** - saves first rotation as TIFF
- **Rotated image** - saves second rotation as TIFF

---

## Test Commands (Connection View)

Two test buttons added to Debug Commands section:

1. **Test Workflow File** - Sends `workflows/WorkflowZstack.txt` directly to test transmission
2. **Test Workflow Gen** - Generates a test workflow using Python functions and sends it

These help diagnose whether issues are with transmission or generation.

---

## Server Directory Creation Fix (2026-01-27)

### The Problem

Server failed to create nested save directories for tile collections:
```
Failed to create directory: /media/deploy/ctlsm1/20260127_123617_Test/2026-01-27/X11.09_Y14.46
Error message: No such file or directory
```

Server's `makeDirectory` can only create single-level directories, and it adds an unpredictable
timestamp prefix that we cannot pre-create.

### Solution: Flattened Names + Post-Collection Reorganization

1. **During collection**: Use flattened directory names (underscores instead of slashes)
   - `Test_2026-01-27_X11.09_Y14.46` instead of `Test/2026-01-27/X11.09_Y14.46`
   - Server successfully creates single-level directory

2. **After collection**: Reorganize files locally into nested structure
   - Only if local drive mapping is configured via "Local Path..." button
   - Moves contents for MIP Overview compatibility

### Files Modified

- `services/configuration_service.py` - Drive mapping storage/retrieval
- `views/dialogs/tile_collection_dialog.py` - Flattened names + reorganization
- `views/workflow_panels/save_panel.py` - "Local Path..." button for drive mapping
- `views/workflow_view.py` - Save directory sanitization

### User Setup

One-time setup: Click "Local Path..." next to save drive, select local mount point (e.g., `G:\CTLSM1`).

After tile collection completes, folders are automatically reorganized from:
```
G:\CTLSM1\20260127_123617_Test_2026-01-27_X11.09_Y14.46\
```
To:
```
G:\CTLSM1\Test\2026-01-27\X11.09_Y14.46\
```

See `claude-report/server_directory_fix.md` for full details.

---

## TIFF 4GB File Size Limit Validation (2026-01-15)

### The Problem

Large Z-stack workflows were failing silently due to the standard TIFF 4GB file size limit.
Server log showed:
```
[error] Bytes written not equal to buffer size (-1, 8388608)
[info] system fault detected, disk full, stopping experiment
```

Despite terabytes of disk space available, the "disk full" error occurs because the server
writes to a single TIFF file which hits the 4GB limit.

### Root Cause

Standard TIFF format uses 32-bit file offsets, limiting files to 4,294,967,296 bytes (4GB).
For 2048x2048 16-bit images (8MB each), maximum safe planes is approximately **481**.

### Solution: Pre-flight Validation

Added TIFF size validation before workflow execution to warn users:

**New File:** `src/py2flamingo/services/tiff_size_validator.py`

```python
from py2flamingo.services.tiff_size_validator import (
    calculate_tiff_size, validate_workflow_params, TiffSizeEstimate
)

# Calculate expected file size
estimate = calculate_tiff_size(
    num_planes=1600,
    image_width=2048,
    image_height=2048,
    bytes_per_pixel=2  # 16-bit
)

if estimate.exceeds_limit:
    print(f"WARNING: {estimate.estimated_gb:.2f} GB exceeds 4GB limit")
    print(f"Max safe planes: {estimate.max_safe_planes}")
```

### Integration Points

1. **TileCollectionDialog**: Validates before executing tile workflows
   - Shows warning dialog with options: Yes/No/Help
   - Help button explains the 4GB limit
   - User can cancel and adjust parameters

2. **WorkflowView**: Validates Z-stack workflows in `_validate_workflow()`
   - Shows error message if limit exceeded
   - Blocks workflow start until user addresses issue

### Key Calculations

| Image Size | Max Safe Planes | Single Image Size |
|------------|-----------------|-------------------|
| 2048x2048 | 481 | 8 MB |
| 1024x1024 | 1,925 | 2 MB |
| 4096x4096 | 120 | 32 MB |

### Files Modified

- `src/py2flamingo/services/tiff_size_validator.py` - NEW validation utility
- `src/py2flamingo/services/__init__.py` - Export validator
- `src/py2flamingo/views/dialogs/tile_collection_dialog.py` - Pre-execution check
- `src/py2flamingo/views/workflow_view.py` - Workflow validation check
