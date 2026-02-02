# Claude Report: MIP Collection Folder Auto-Reorganization

**Date:** 2026-01-30

## Issue Summary

When tiles are collected from the MIP Overview dialog, the output folders remain in flattened server format (e.g., `20260129_164546_Test_2026-01-29_X10.58_Y14.46`) instead of being reorganized into the nested MIP-compatible structure (`Test/2026-01-29/X10.58_Y14.46/`).

## Root Cause

The `TileCollectionDialog` already has folder reorganization code (`_reorganize_tile_folders()`), but it only runs when:
1. The "Enable post-processing" checkbox is checked in the SavePanel
2. A local path mapping is configured for the selected server drive

When launched from MIP Overview, these conditions were not met because the MIP Overview dialog — which already knows the local base folder (the user browsed to it to load MIPs) — did not pass this information to TileCollectionDialog.

## Solution

Pass the MIP local folder info from MIP Overview to TileCollectionDialog so local access can be auto-configured. The local drive root is `config.base_folder.parent` (e.g., if user browsed to `G:\CTLSM1\Test`, the drive root is `G:\CTLSM1`).

## Files Modified

| File | Changes |
|------|---------|
| `views/dialogs/mip_overview_dialog.py` | Pass `local_base_folder` parameter to TileCollectionDialog constructor |
| `views/dialogs/tile_collection_dialog.py` | Accept `local_base_folder` param, add `_auto_configure_local_access()` method |
| `views/workflow_panels/save_panel.py` | Add `enable_local_access()` public method |

## Implementation Details

### 1. MIP Overview Dialog (`mip_overview_dialog.py`)

Added `local_base_folder` argument when constructing `TileCollectionDialog`:

```python
dialog = TileCollectionDialog(
    ...,
    local_base_folder=str(self._config.base_folder.parent) if self._config else None,
)
```

### 2. Tile Collection Dialog (`tile_collection_dialog.py`)

- Constructor accepts `local_base_folder: str = None`, stored as `self._local_base_folder_hint`
- After `_restore_dialog_state()`, calls `_auto_configure_local_access()` if hint is set
- New `_auto_configure_local_access()` method:
  - Checks a save drive is selected
  - Skips if a mapping already exists for that drive (doesn't override user config)
  - Calls `self._save_panel.enable_local_access(local_base_folder)` to set up the mapping and UI

### 3. Save Panel (`save_panel.py`)

New `enable_local_access(local_path: str)` method:
- Saves drive-to-local-path mapping via `config_service.set_drive_mapping()`
- Sets the local path display text
- Checks the "Enable post-processing" checkbox
- Updates status display

## Data Flow

```
MIP Overview (user browsed to G:\CTLSM1\Test)
    ↓ local_base_folder = "G:\CTLSM1" (base_folder.parent)
TileCollectionDialog.__init__()
    ↓ _auto_configure_local_access("G:\CTLSM1")
SavePanel.enable_local_access("G:\CTLSM1")
    ↓ sets drive mapping, checks post-processing checkbox
_reorganize_tile_folders() runs after queue completion
    ↓ folders reorganized using the configured local path
Result: Test/2026-01-29/X10.58_Y14.46/ (nested structure)
```

## Verification

1. Launch MIP Overview, load MIP tiles from a local folder (e.g., `G:\CTLSM1\Test`)
2. Select tiles and click "Collect Tiles"
3. Verify the SavePanel shows "Enable post-processing" checked and local path populated
4. Execute collection
5. After queue completion, verify folders are reorganized into nested structure
