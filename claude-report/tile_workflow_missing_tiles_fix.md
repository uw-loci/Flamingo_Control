# Claude Report: Fix Missing Tiles in Tiled Acquisitions

## Overview

Fixed a bug where tiled acquisitions (e.g. 54-tile 6x9 grid) would silently lose tiles — typically 6 of 54 — with no errors logged. The loss worsened as acquisition progressed.

## Root Cause

The `_visualization_update_timer` (500ms single-shot) fired between tiles, triggering `_update_visualization()` on the GUI thread. This calls `get_display_volume_transformed()` for each channel — an operation that grows progressively more expensive as more data accumulates (1s early → 12s+ late in acquisition).

While the GUI thread was blocked on this transform:
1. Camera frames for the **next tile** continued arriving on the receiver thread
2. The frame buffer (`deque(maxlen=20)`) overflowed in ~1.1s at 18fps, silently dropping oldest frames
3. The display timer (50ms QTimer) couldn't fire to call `drain_all_frames()` since it runs on the blocked GUI thread
4. If the transform took longer than one tile's Z-paint duration (~3-5s), **all frames for that tile were lost**

### Evidence
- 48 "New tile at" messages for 54 workflows (6 tiles never had frames reach sample_view)
- Time gaps at missing tiles: 27-39s vs normal ~17s
- Transform timestamps showed progressive slowdown: 1s → 5s → 12s+ per channel
- 191 motion callback queue drops (secondary symptom of GUI blocking)

## Fix

### 1. Suppress visualization timer during tile workflows (PRIMARY)
**File: `sample_view.py`**

- `add_frame_to_volume()`: Gated `_visualization_update_timer.start()` with `not _tile_workflow_active`
- `_on_tile_zstack_frame()`: Removed `_visualization_update_timer.start()` entirely
- `finish_tile_workflows()` already calls `_visualization_update_timer.start()` — this now serves as the sole visualization trigger, showing all tiles at once after acquisition completes

### 2. Enlarged frame buffer for tile mode (SAFETY NET)
**File: `camera_service.py`**

Added `set_tile_mode_buffer(enabled)` method that resizes the deque from 20 to 500 frames (~28s at 18fps). Called from:
- `camera_controller.py`: `set_active_tile_position()` (enable) and `clear_tile_mode()` (disable)
- `tile_collection_dialog.py`: Queue-based tile mode initialization (enable), since `set_active_tile_position()` is bypassed in that code path

### 3. Frame drop logging (DIAGNOSTICS)
**File: `camera_service.py`**

Added `_dropped_frame_count` tracking in `_data_receiver_loop()`. Logs a warning every 50th drop with buffer size and total count, making future frame loss immediately visible.

## Changed Files

| File | Change |
|------|--------|
| `views/sample_view.py` | Gate `_visualization_update_timer.start()` during tile workflows |
| `services/camera_service.py` | Add `set_tile_mode_buffer()`, `_dropped_frame_count`, frame drop logging |
| `controllers/camera_controller.py` | Call `set_tile_mode_buffer()` in tile mode setup/teardown |
| `views/dialogs/tile_collection_dialog.py` | Call `set_tile_mode_buffer()` in queue-based tile mode setup |

## Data Flow (After Fix)

```
Tile Workflow Start
  → set_tile_mode_buffer(True)           # buffer: 20 → 500 frames
  → _tile_workflow_active = True          # suppresses viz timer

Per-tile frames:
  Camera thread → deque(maxlen=500) → drain_all_frames() → add_frame_to_volume()
                                                            ↳ viz timer: SUPPRESSED
                                                            ↳ channel timer: runs (lightweight)

Tile Workflow End
  → finish_tile_workflows()
    → _tile_workflow_active = False
    → _visualization_update_timer.start() # single final update with ALL tiles
  → set_tile_mode_buffer(False)           # buffer: 500 → 20 frames
```

## Commit

`c5cf948` — Fix missing tiles in tiled acquisitions by deferring visualization updates
