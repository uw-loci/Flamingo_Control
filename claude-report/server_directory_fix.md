# Server Directory Creation Fix

## Date: 2026-01-27

## Problem Summary

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

---

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

---

## Files Modified

| File | Changes |
|------|---------|
| `services/configuration_service.py` | Added drive mapping storage/retrieval methods |
| `views/dialogs/tile_collection_dialog.py` | Flattened directory names + post-collection reorganization |
| `views/workflow_panels/save_panel.py` | Added "Local Path..." button for drive mapping UI |
| `views/workflow_view.py` | Added save directory sanitization (replaces `/` with `_`) |

---

## Implementation Details

### 1. Configuration Service: Drive Mapping Storage

**File:** `src/py2flamingo/services/configuration_service.py`

New methods added:

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

def remove_drive_mapping(self, server_path: str) -> bool:
    """Remove a drive mapping."""
```

### 2. Tile Collection Dialog: Flattened Names + Reorganization

**File:** `src/py2flamingo/views/dialogs/tile_collection_dialog.py`

**Key changes:**

1. **Flattened directory format during collection:**
   ```python
   # OLD (failed):
   tile_save_directory = f"{base}/{date}/{tile}"  # Nested - server can't create

   # NEW (works):
   tile_save_directory = f"{base}_{date}_{tile}"  # Flattened - single level
   ```

2. **Tracking for post-collection reorganization:**
   ```python
   self._tile_folder_mapping: Dict[str, Tuple[str, str]] = {}
   # Maps: flattened_name -> (date_folder, tile_folder)
   ```

3. **Reorganization after `queue_completed` signal:**
   ```python
   def _reorganize_tile_folders(self) -> bool:
       """Move flattened folders to nested structure.

       Finds: 20260127_123617_Test_2026-01-27_X11.09_Y14.46/
       Moves to: Test/2026-01-27/X11.09_Y14.46/

       Only runs if:
       - Local drive mapping is configured
       - Local path exists and is accessible
       """
   ```

**Completion Signal Safety:**
The reorganization runs ONLY after `queue_completed` signal, which fires after:
1. Each workflow receives `SYSTEM_STATE_IDLE` callback
2. All workflows in queue have completed
3. All files are fully written to disk

### 3. Save Panel: Drive Mapping UI

**File:** `src/py2flamingo/views/workflow_panels/save_panel.py`

Added "Local Path..." button next to "Refresh" button:
- Opens directory picker dialog
- Saves mapping to configuration service
- Shows confirmation with explanation of what it does

```python
def _configure_local_path(self) -> None:
    """Open dialog to configure local path for current drive."""
    # Gets current drive from combo box
    # Opens QFileDialog for directory selection
    # Saves mapping via config_service.set_drive_mapping()
```

### 4. Workflow View: Save Directory Sanitization

**File:** `src/py2flamingo/views/workflow_view.py`

Added validation in `_validate_workflow()`:
- Checks for `/` or `\` in save directory
- Replaces with `_` and shows error asking user to review
- Prevents users from accidentally creating nested paths

```python
if '/' in save_dir or '\\' in save_dir:
    sanitized = save_dir.replace('/', '_').replace('\\', '_')
    self._save_panel.set_save_directory(sanitized)
    errors.append(f"Save directory '{save_dir}' contains path separators...")
```

---

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
  - Finds date folder: 2026-01-27/
  - Finds tile folders: X11.09_Y14.46/, X11.09_Y13.95/, etc.
```

### Without Local Access (No Reorganization)
```
Server creates: /media/deploy/USB_drive/20260127_123617_Test_2026-01-27_X11.09_Y14.46/
Files stay in flattened structure.
Note: MIP Overview cannot load this format (but at least collection succeeds)
```

---

## Configuration Persistence

Drive mappings are stored in the configuration service's config dictionary under `drive_path_mappings` key:

```python
{
    'drive_path_mappings': {
        '/media/deploy/ctlsm1': 'G:/CTLSM1',
        '/media/deploy/ctlsm2': 'H:/CTLSM2'
    }
}
```

The mappings persist across sessions (stored with other config settings).

---

## User Workflow

### First-Time Setup (One-Time)
1. Open tile collection dialog or workflow builder
2. Select save drive from dropdown (e.g., `/media/deploy/ctlsm1`)
3. Click "Local Path..." button
4. Browse to local mount point (e.g., `G:\CTLSM1`)
5. Confirmation dialog shows the mapping is saved

### Each Tile Collection
1. Select tiles from LED 2D Overview
2. Click "Collect Tiles"
3. Configure workflow settings
4. Click "Create Workflows" then "Yes" to execute
5. After all workflows complete:
   - If local mapping configured: Folders automatically reorganized
   - If no mapping: Files remain in flattened structure

---

## Troubleshooting

### Issue: Folders Not Being Reorganized

**Check:**
1. Is local drive mapping configured?
   - Look for "Local Path..." button in save panel
   - Click it to verify/configure mapping

2. Does local path exist and is it accessible?
   - Check network drive is mounted
   - Verify path in mapping matches actual mount point

3. Did all workflows complete?
   - Reorganization only runs after `queue_completed`
   - Check for workflow errors in log

**Logs to check:**
```
logger.info("Starting folder reorganization: {local_base}")
logger.info("Reorganized: {src} -> {dest}")
logger.warning("Could not find folder matching pattern: {pattern}")
```

### Issue: Server Still Failing to Create Directory

**Check:**
1. Is save directory still nested?
   - Look at workflow file content
   - `Save image directory` should NOT contain `/`

2. Is validation being bypassed?
   - Check if using TileCollectionDialog or WorkflowView
   - Both should sanitize directories now

### Issue: MIP Overview Can't Find Files

**Check:**
1. Were folders reorganized?
   - Look for nested structure: `base/date/tile/`
   - If still flat, check local mapping

2. Is MIP Overview looking in right place?
   - Verify MIP Overview is pointed to local path
   - Check date folder matches expected format

---

## Future Improvements

1. **Server-side fix**: Update firmware to support `mkdir -p` equivalent
   - Would eliminate need for flattened names
   - Would eliminate need for post-collection reorganization

2. **Progress feedback during reorganization**
   - Currently happens silently after completion dialog
   - Could add progress indicator for many tiles

3. **Automatic local path detection**
   - Could try to auto-detect local mounts for known server paths
   - Would simplify first-time setup

4. **Undo/rollback capability**
   - If reorganization partially fails, could offer to undo
   - Currently logs errors but continues with other tiles

---

## Related Documentation

| Document | Relevance |
|----------|-----------|
| `docs/workflow_system.md` | Save panel settings documentation |
| `claude-report/tile_collection_dialog_feature.md` | Tile collection workflow documentation |
| `src/py2flamingo/services/configuration_service.py` | API documentation for drive mappings |

---

## Test Scenarios

### 1. Test Collection Success
- [ ] Select tiles from LED 2D Overview
- [ ] Execute collection
- [ ] Verify server creates directories without errors
- [ ] Verify files are written successfully

### 2. Test Post-Collection Reorganization
- [ ] Configure local path mapping for CTLSM1
- [ ] Run tile collection
- [ ] Verify folders are moved from flat to nested structure
- [ ] Verify MIP Overview can load the reorganized folders

### 3. Test Without Local Mapping
- [ ] Use drive without local mapping configured
- [ ] Run tile collection
- [ ] Verify collection succeeds (no errors)
- [ ] Verify files remain in flattened structure

### 4. Test Workflow Tab Sanitization
- [ ] Enter save directory with `/` characters in Workflow tab
- [ ] Verify sanitization warning appears
- [ ] Verify workflow runs successfully with sanitized name

---

## Commit

```
Fix server directory creation by using flattened names during collection

Server's makeDirectory can only create single-level directories. This caused
tile collection to fail when using nested paths like base/date/tile/.

Solution:
- Use flattened directory names during collection (base_date_tile)
- After collection completes, reorganize to nested structure locally
- Add drive mapping UI to configure server-to-local path mapping
- Sanitize save directory in workflow view to prevent nested paths

Files modified:
- services/configuration_service.py: Drive mapping storage
- views/dialogs/tile_collection_dialog.py: Flattened names + reorganization
- views/workflow_panels/save_panel.py: Local path configuration UI
- views/workflow_view.py: Save directory sanitization
```
