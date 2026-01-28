# Claude Report: Connection Error Status Indicator

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
