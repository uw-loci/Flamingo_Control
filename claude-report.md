# Claude Report: Connection Error Status Indicator

**Status: RESOLVED** (2026-01-28)

## Issue Summary

When clicking "Connect" in the Flamingo Control application, if the TCP connection succeeds but subsequent communication with the microscope fails (e.g., settings retrieval times out), the UI was misleadingly showing:
- Connect button greyed out (as if successful)
- Status indicator showing blue "Ready" state
- No clear indication to the user that anything went wrong

The only indication of failure was in the log file and a text area showing "No settings available."

## Root Cause Analysis

The connection flow had these issues:

1. **Signal timing**: `connection_established` signal was emitted regardless of whether settings retrieval succeeded
2. **Error swallowing**: The controller caught exceptions from settings retrieval and returned `None` silently
3. **No error state**: The status indicator service had no way to represent a "connected but not working" state

### Call Flow (Before Fix)

```
Connect clicked
    ↓
TCP connection succeeds → status set to "Ready" (blue)
    ↓
Settings retrieval fails (timeout) → error only logged
    ↓
User sees "Ready" but microscope isn't responding
```

## Solution

Added an ERROR state to the status indicator system that displays when communication failures occur after TCP connection succeeds.

### Files Modified

| File | Changes |
|------|---------|
| `status_indicator_service.py` | Added `GlobalStatus.ERROR`, `on_connection_error()`, `clear_error()` methods, updated status calculation |
| `status_indicator_widget.py` | Added red color for ERROR state |
| `connection_view.py` | Added `connection_error` signal, emit on settings failure, reordered signal emissions |
| `application.py` | Connected `connection_error` signal to status indicator service |

### New Status Flow

```
Connect clicked
    ↓
TCP connection succeeds
    ↓
connection_established emitted → status set to "Ready" (blue)
    ↓
Settings retrieval fails (timeout)
    ↓
connection_error emitted → status set to "Settings retrieval failed" (red)
    ↓
User sees clear red error indicator
```

## Technical Details

### New GlobalStatus Enum Value

```python
class GlobalStatus(Enum):
    DISCONNECTED = "disconnected"  # Grey
    IDLE = "idle"                  # Blue
    MOVING = "moving"              # Amber
    WORKFLOW_RUNNING = "workflow_running"  # Purple
    ERROR = "error"                # Red (NEW)
```

### Status Priority Order

```python
def _calculate_status(self) -> GlobalStatus:
    if not self._is_connected:
        return GlobalStatus.DISCONNECTED
    if self._has_error:           # NEW - check error before idle
        return GlobalStatus.ERROR
    if self._is_workflow_running:
        return GlobalStatus.WORKFLOW_RUNNING
    if self._is_moving:
        return GlobalStatus.MOVING
    return GlobalStatus.IDLE
```

### Signal Connection

```python
# In application.py
if hasattr(self.connection_view, 'connection_error'):
    self.connection_view.connection_error.connect(
        lambda msg: self.status_indicator_service.on_connection_error(msg)
    )
```

## Verification

After these changes, when settings retrieval fails:
1. Status indicator turns red
2. Status text shows "Settings retrieval failed"
3. Settings text area shows error message in red
4. User has clear visual feedback that something is wrong

## Commits

```
6c16a29 Add ERROR status to show communication failures after connection
8e6da02 Update button states on communication error
```

### Follow-up Fix (8e6da02)

The initial fix showed the error in the status indicator but didn't update button states. Added `_update_status_error()` method to properly handle the error state:

- **Connect button**: Re-enabled (allows user to retry)
- **Disconnect button**: Stays enabled (TCP connection exists)
- **Sample View button**: Disabled (microscope not usable)
- **Debug/workflow buttons**: Disabled

This ensures users can:
1. See the error clearly (red status indicator + error message)
2. Retry by clicking Connect again
3. Cannot accidentally try to use features that require working communication

### Position Query Race Condition Fix (57b6677)

**Problem:** Stage position showed 0,0,0,0 because position queries were colliding with settings retrieval:
1. `connection_established` signal triggered position queries immediately
2. Settings retrieval pauses the SocketReader for synchronous operation
3. Position responses arriving during the pause were lost (timeout)

**Solution:**
- Added `settings_loaded` signal emitted after settings retrieval completes
- Split `_on_stage_connection_established()` into two handlers:
  - `_on_stage_connection_established()`: enables controls (on TCP connection)
  - `_on_settings_loaded()`: queries position (after settings complete)
- Position queries now wait for settings retrieval to finish

**Files Modified:**
- `connection_view.py`: Added `settings_loaded` signal, emit after successful settings load
- `application.py`: New `_on_settings_loaded()` handler, connect to signal

Pushed to: https://github.com/uw-loci/Flamingo_Control.git (main branch)

---

# Claude Report: Server Directory Creation Fix

## Date: 2026-01-27

## Issue Summary

When executing tile collection workflows, the server failed to create save directories because nested paths couldn't be created recursively.

**Server error:**
```
Failed to create directory: /media/deploy/ctlsm1/20260127_123617_Test/2026-01-27/X11.09_Y14.46
Error message: No such file or directory
```

**Root cause:**
1. Server's `makeDirectory` function can only create single-level directories
2. The server adds an unpredictable timestamp prefix (e.g., `20260127_123617_`) to all directories
3. Python cannot pre-create directories because we don't know the timestamp in advance

## Solution: Flattened Names During Collection + Post-Collection Reorganization

### Approach

1. **During collection**: Use flattened directory names (underscores instead of slashes)
   - Server can successfully create single-level directories
   - Example: `Test_2026-01-27_X11.09_Y14.46` (flat)

2. **After collection**: Reorganize files locally into nested structure
   - Only if local drive mapping is configured
   - Moves contents from flat to nested for MIP Overview compatibility
   - Example: `Test/2026-01-27/X11.09_Y14.46/` (nested)

3. **No local access**: Leave files in flattened structure
   - Collection still succeeds
   - MIP Overview won't load (but at least data is saved)

## Files Modified

| File | Changes |
|------|---------|
| `services/configuration_service.py` | Added drive mapping storage/retrieval methods |
| `views/dialogs/tile_collection_dialog.py` | Flattened directory names + post-collection reorganization |
| `views/workflow_panels/save_panel.py` | Added "Local Path..." button for drive mapping UI |
| `views/workflow_view.py` | Added save directory sanitization (replaces `/` with `_`) |

## Implementation Details

### 1. Configuration Service: Drive Mapping Storage

New methods in `src/py2flamingo/services/configuration_service.py`:

```python
DRIVE_MAPPINGS_KEY = 'drive_path_mappings'

def get_drive_mappings(self) -> Dict[str, str]:
    """Get server-to-local drive mappings.
    Example: {"/media/deploy/ctlsm1": "G:/CTLSM1"}
    """

def set_drive_mapping(self, server_path: str, local_path: str) -> None:
    """Set local path mapping for a server drive."""

def get_local_path_for_drive(self, server_path: str) -> Optional[str]:
    """Get local path for a server drive, or None if not mapped."""
```

### 2. Tile Collection Dialog: Flattened Names + Reorganization

**Key changes in `src/py2flamingo/views/dialogs/tile_collection_dialog.py`:**

1. **Flattened directory format during collection:**
   ```python
   # OLD (failed): tile_save_directory = f"{base}/{date}/{tile}"
   # NEW (works): tile_save_directory = f"{base}_{date}_{tile}"
   ```

2. **Tracking for post-collection reorganization:**
   ```python
   self._tile_folder_mapping: Dict[str, Tuple[str, str]] = {}
   # Maps: flattened_name -> (date_folder, tile_folder)
   ```

3. **Reorganization after `queue_completed` signal** via `_reorganize_tile_folders()` method

### 3. Save Panel: Drive Mapping UI

Added "Local Path..." button in `src/py2flamingo/views/workflow_panels/save_panel.py`:
- Opens directory picker dialog
- Saves mapping to configuration service
- Shows confirmation with explanation

### 4. Workflow View: Save Directory Sanitization

Added validation in `src/py2flamingo/views/workflow_view.py`:
- Checks for `/` or `\` in save directory
- Replaces with `_` and shows error asking user to review

## Directory Structure Flow

### During Collection (Server Perspective)
```
Save image directory = Test_2026-01-27_X11.09_Y14.46
Server creates: /media/deploy/ctlsm1/20260127_123617_Test_2026-01-27_X11.09_Y14.46/
Result: SUCCESS (single directory level)
```

### After Collection (Local Reorganization)
```
Before: G:\CTLSM1\20260127_123617_Test_2026-01-27_X11.09_Y14.46\*_MP.tif
After:  G:\CTLSM1\Test\2026-01-27\X11.09_Y14.46\*_MP.tif

MIP Overview can now load from: G:\CTLSM1\Test\
```

## User Setup

**One-time setup:**
1. Select save drive from dropdown (e.g., `/media/deploy/ctlsm1`)
2. Click "Local Path..." button
3. Browse to local mount point (e.g., `G:\CTLSM1`)
4. Mapping is saved for future sessions

## Troubleshooting

### Folders Not Being Reorganized
- Check local drive mapping is configured ("Local Path..." button)
- Verify local path exists and is accessible
- Check all workflows completed (reorganization runs after `queue_completed`)

### Server Still Failing
- Check save directory doesn't contain `/` or `\`
- Verify using updated TileCollectionDialog or WorkflowView

### MIP Overview Can't Find Files
- Verify folders were reorganized (look for nested structure)
- Check MIP Overview is pointed to local path

## Related Files to Check for Future Issues

| File | Purpose |
|------|---------|
| `services/configuration_service.py` | Drive mapping storage |
| `views/dialogs/tile_collection_dialog.py` | Tile workflow creation & reorganization |
| `views/workflow_panels/save_panel.py` | Local path UI |
| `views/workflow_view.py` | Directory sanitization |
| `services/workflow_queue_service.py` | Queue completion signals |
| `docs/workflow_system.md` | Save panel documentation |

## Documentation Updated

- `docs/workflow_system.md` - Added save panel local path mapping docs
- `claude-report/tile_collection_dialog_feature.md` - Added server directory fix section (in gitignored dir)

---

# Claude Report: LED 2D Overview Documentation

**Date:** 2026-01-28

## Summary

Created comprehensive documentation for the LED 2D Overview feature and integrated it into the project's documentation structure.

## Files Created

| File | Size | Description |
|------|------|-------------|
| `docs/led_2d_overview.md` | 14KB | Complete user & developer guide |

## Files Updated

| File | Changes |
|------|---------|
| `README.md` | Added LED 2D Overview feature section; added link in Documentation section |
| `docs/CLAUDE.md` | Added LED 2D Overview feature context for AI assistance |

## Documentation Contents

The new `docs/led_2d_overview.md` includes:

1. **Overview** - Purpose and capabilities
2. **Features** - Dual-rotation scanning, visualization types, tile selection, etc.
3. **Quick Start** - 6-step getting started guide
4. **User Workflow** - Detailed 4-stage workflow:
   - Stage 1: Configuration (bounding points, LED settings, rotation)
   - Stage 2: Scanning (progress tracking, cancellation)
   - Stage 3: Results & Selection (visualization types, manual/auto selection)
   - Stage 4: Tile Collection (workflow generation)
5. **Configuration Parameters** - Tables of scan settings and calculated values
6. **Architecture (Developer Reference)**:
   - Component overview with file sizes
   - Data flow diagram
   - Key classes (BoundingBox, ScanConfiguration, TileResult, RotationResult)
   - Signal flow diagram
   - Integration points (menu entry, service dependencies)
7. **Troubleshooting** - Common issues and solutions
8. **Related Features** - Links to MIP Overview, Tile Collection, etc.

## Architecture Assessment

Verified the LED 2D Overview feature is well-separated (9/10 architecture quality):
- 7 main files (~228KB total)
- Minimal integration points (menu entry + signals)
- No direct database modifications, global state pollution, or monkey-patching
- Could be extracted to separate package with ~90% import path changes only

---

# Claude Report: Tile Collection Dialog Resize

**Date:** 2026-01-28

## Summary

Increased the minimum size of the Tile Collection Dialog to accommodate recent UI additions.

## Changes

| Property | Before | After | Change |
|----------|--------|-------|--------|
| Minimum Width | 500px | 550px | +10% |
| Minimum Height | 600px | 720px | +20% |

## File Modified

`src/py2flamingo/views/dialogs/tile_collection_dialog.py` (lines 243-244)

```python
# Before
self.setMinimumWidth(500)
self.setMinimumHeight(600)

# After
self.setMinimumWidth(550)
self.setMinimumHeight(720)
```

---

# Claude Report: Napari Icon Removal from Dialogs

**Date:** 2026-01-28

## Issue

The napari logo appeared in the title bar of most dialog windows, confusing users who thought the application was a napari plugin. The icon was being inherited from napari when it was imported/initialized for the 3D visualization.

## Solution

Added `self.setWindowIcon(QIcon())` to dialog constructors to clear the inherited napari icon. The Sample View window (which actually uses napari) retains the napari icon since it's appropriate there.

## Files Modified

| File | Dialog Name |
|------|-------------|
| `views/dialogs/led_2d_overview_dialog.py` | LED 2D Overview |
| `views/dialogs/led_2d_overview_result.py` | LED 2D Overview - Results |
| `views/dialogs/tile_collection_dialog.py` | Collect Tiles - Workflow Configuration |
| `views/dialogs/overview_thresholder_dialog.py` | 2D Overview Tile Thresholder |
| `views/dialogs/mip_overview_dialog.py` | MIP Overview |
| `views/dialogs/advanced_illumination_dialog.py` | Advanced Illumination Settings |
| `views/dialogs/advanced_save_dialog.py` | Advanced Save Settings |
| `views/dialogs/advanced_camera_dialog.py` | Advanced Camera Settings |

## Implementation

Each dialog received two changes:

1. **Import addition:**
   ```python
   from PyQt5.QtGui import QIcon  # Added to existing QtGui import
   ```

2. **Icon clearing in constructor:**
   ```python
   self.setWindowTitle("Dialog Name")
   self.setWindowIcon(QIcon())  # Clear inherited napari icon
   ```

## Result

- Dialog windows now show the default system icon (or no icon) instead of the napari logo
- Sample View retains the napari icon since it genuinely uses napari for 3D visualization
- Users are no longer confused about whether the application is a napari plugin

---

# Claude Report: Contrast Slider for ImagePanel

**Date:** 2026-01-28

## Summary

Added min/max contrast sliders to the ImagePanel component used by MIP Overview and LED 2D Overview dialogs.

## Features

- **Dual sliders**: Min (black point) and Max (white point) sliders
- **Auto-range**: Slider endpoints are image minimum and 95th percentile values
- **Real-time update**: Moving sliders instantly updates display
- **Slider protection**: Min cannot exceed max (and vice versa)

## Files Modified

| File | Changes |
|------|---------|
| `views/dialogs/led_2d_overview_result.py` | Added contrast slider UI and logic to ImagePanel class |

## Implementation Details

### UI Layout

Added between zoom label and Fit/1:1 buttons:
```
[100%] ... [Contrast:] [---min---] [0-100%] [---max---] ... [Fit] [1:1]
```

### Contrast Calculation

```python
# When image is loaded:
self._image_min = float(np.min(flat))
self._image_max_pct = float(np.percentile(flat, 99.5))  # Updated from 95%

# When converting to display:
display_min = self._image_min + (slider_min / 1000.0) * intensity_range
display_max = self._image_min + (slider_max / 1000.0) * intensity_range
img_clipped = np.clip(image, display_min, display_max)
img_8bit = rescale_to_255(img_clipped)
```

### Instance Variables Added

```python
self._contrast_min_slider = 0      # 0-1000 range for precision
self._contrast_max_slider = 1000
self._image_min = 0.0              # Actual image intensity min
self._image_max_pct = 255.0        # Actual image 99.5th percentile
```

---

# Claude Report: Session Save Path Defaults

**Date:** 2026-01-28

## Summary

Added default save locations for MIP Overview and LED 2D Overview session saves, with persistence of user preferences.

## Default Locations

| Feature | Default Folder |
|---------|----------------|
| LED 2D Overview | `Flamingo_Control/2DOverviewSession/` |
| MIP Overview | `Flamingo_Control/MIPOverviewSession/` |

## Behavior

1. **First use**: Defaults to session folder in project root
2. **User selects different folder**: Choice is remembered via configuration service
3. **Next session**: Opens to user's previously selected folder
4. **Folder creation**: Default folders are auto-created if they don't exist

## Files Modified

| File | Changes |
|------|---------|
| `services/configuration_service.py` | Added `get_led_2d_session_path()`, `set_led_2d_session_path()`, `get_mip_session_path()`, `set_mip_session_path()` |
| `views/dialogs/led_2d_overview_result.py` | Updated `_save_session()` with default path and persistence |
| `views/dialogs/mip_overview_dialog.py` | Updated `_on_save_session()` with default path and persistence |
| `.gitignore` | Added `2DOverviewSession/` and `MIPOverviewSession/` |

## Configuration Service Methods

```python
# Session save path keys
LED_2D_SESSION_PATH_KEY = 'led_2d_overview_session_path'
MIP_SESSION_PATH_KEY = 'mip_overview_session_path'

def get_led_2d_session_path(self) -> Optional[str]: ...
def set_led_2d_session_path(self, path: str) -> None: ...
def get_mip_session_path(self) -> Optional[str]: ...
def set_mip_session_path(self, path: str) -> None: ...
```

## Path Priority

1. User's previously saved preference (from configuration service)
2. Default session folder in project root
3. Falls back to home directory if folder creation fails

---

# Claude Report: MIP Overview Axis Inversion Fix

**Date:** 2026-01-28

## Issue

MIP Overview was not correctly handling inverted stage axes when displaying tiles. The `invert_x` setting from `visualization_3d_config.yaml` was being ignored, causing tiles to be displayed with incorrect orientation.

## Solution

Added proper axis inversion support to MIP Overview, matching the behavior of LED 2D Overview.

## Files Modified

| File | Changes |
|------|---------|
| `models/mip_overview.py` | Added `invert_x` field to `MIPOverviewConfig`; added `load_invert_x_setting()` helper function |
| `views/dialogs/mip_overview_dialog.py` | Load `invert_x` from config; invert tile placement during stitching; pass to `set_tile_coordinates` |

## Implementation Details

### MIPOverviewConfig Changes

```python
@dataclass
class MIPOverviewConfig:
    # ... existing fields ...
    invert_x: bool = False  # NEW: Whether X-axis is inverted for display
```

### Config Loading

```python
def load_invert_x_setting() -> bool:
    """Load invert_x from visualization_3d_config.yaml."""
    # Reads stage_control.invert_x_default from config file
    # Default: False if config not found
```

### Tile Placement During Stitching

```python
# Calculate position (invert X if needed to match stage orientation)
if self._config.invert_x:
    inverted_x_idx = (tiles_x - 1) - tile.tile_x_idx
    x_pos = inverted_x_idx * tile_w
else:
    x_pos = tile.tile_x_idx * tile_w
```

## Config Setting

In `configs/visualization_3d_config.yaml`:
```yaml
stage_control:
  invert_x_default: true  # Low X stage values appear on RIGHT side
```

## Behavior

- When `invert_x=True`: Low X stage values (e.g., X=4mm) appear on the RIGHT side of the overview
- When `invert_x=False`: Low X stage values appear on the LEFT side (standard orientation)
- Saved sessions preserve their `invert_x` setting for consistent display
