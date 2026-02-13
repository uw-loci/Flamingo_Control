# Claude Report: Background Visualization Updates During Tile Workflows

## Overview

Restored live 3D visualization updates during tiled acquisitions by running the expensive transform computation in a background thread. The previous fix (`c5cf948`) suppressed **all** visualization updates during tile workflows to prevent GUI-thread blocking from causing frame loss. This made the user fly blind until acquisition completed.

## Approach

**Core insight**: The expensive part is `get_display_volume_transformed()` (numpy computation, 1-22s). The cheap part is `layer.data = volume` + MIP projections (~fast). Only the cheap part needs the GUI thread.

Solution: Run transform computation in a `threading.Thread`, deliver results to the GUI thread via `pyqtSignal(dict)`. The GUI thread only does the fast layer assignment, so frame draining is never blocked.

## Changes

### 1. Class-level signal
Added `_viz_results_ready = pyqtSignal(dict)` on `SampleView` to deliver background-computed volumes (channel_id → numpy array) to the GUI thread.

### 2. `_update_visualization_async()` — new method
- Guards against concurrent updates with `_viz_update_in_progress` flag
- Captures immutable snapshots of `last_stage_position` and `holder_position` before spawning the thread (thread safety)
- Spawns a daemon `threading.Thread` that calls `get_display_volume_transformed()` for each channel with data
- Emits `_viz_results_ready` signal when done, delivering results to the GUI thread

### 3. `_apply_visualization_results()` — new method (GUI thread)
- Connected to `_viz_results_ready` signal
- Performs the fast `layer.data = volume` assignment
- Applies auto-contrast on first data
- Updates 2D MIP plane views
- Resets `_viz_update_in_progress` in `finally` block

### 4. Async dispatch in `_update_visualization()`
Added early return at top: when `_tile_workflow_active` is True, delegates to `_update_visualization_async()` instead of running transforms on the GUI thread. Normal (non-tile) workflows are unaffected.

### 5. Re-enabled timer triggers
- `_on_tile_zstack_frame()`: Kicks `_visualization_update_timer` on first frame of each new tile (was fully suppressed in `c5cf948`)
- `add_frame_to_volume()`: Removed the `_tile_workflow_active` gate — timer now fires unconditionally since `_update_visualization` handles the async dispatch internally

## Changed Files

| File | Change |
|------|--------|
| `views/sample_view.py` | Add `_viz_results_ready` signal, `_update_visualization_async()`, `_apply_visualization_results()`, async dispatch in `_update_visualization()`, re-enable timer triggers |

## Data Flow (After This Change)

```
Per-tile frames (during tile workflow):
  Camera thread → deque(maxlen=500) → drain_all_frames() → add_frame_to_volume()
                                                            ↳ viz timer: fires (500ms debounce)

Timer fires → _update_visualization()
  → _tile_workflow_active? YES → _update_visualization_async()
    → _viz_update_in_progress? skip if true
    → Snapshot state (stage_pos, holder_pos, channel list)
    → threading.Thread("VizUpdate"):
        → get_display_volume_transformed() per channel  [BACKGROUND — GUI free]
        → emit _viz_results_ready(results)
    → GUI thread receives signal:
        → layer.data = volume                           [FAST — ~ms]
        → _update_plane_views()
        → _viz_update_in_progress = False

Normal (non-tile) path unchanged:
  Timer fires → _update_visualization() → runs transforms inline on GUI thread
```

## Safety Nets (Unchanged from c5cf948)

- Frame buffer: 500 frames during tile mode (28s at 18fps)
- Frame drop logging: warns every 50th drop
- Concurrency guard: skips background update if previous one is still running

## Verification

1. Run a tiled acquisition (54 tiles, 6x9 grid, 2 channels)
2. Confirm tiles appear progressively in the 3D view during acquisition
3. Confirm all 54 tiles captured ("Processed 54 tiles" in log)
4. Confirm no "Frame buffer full" warnings
5. Check logs for "Skipping viz update — previous still in progress" (expected — confirms gating)
6. Verify 2D plane views also update during acquisition

## Commit

`76421b2` — Add background visualization updates during tile workflows
