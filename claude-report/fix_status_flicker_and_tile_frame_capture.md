# Claude Report: Fix Status Flickering + Enable Frame Capture During Tile Workflows

**Date:** 2026-02-02

## Issue 1: Status Label Flickering

### Problem

Two signals (`progress_updated` and `workflow_progress`) fire back-to-back per image acquired during tile collection. In `tile_collection_dialog.py`, both are connected to callbacks that set different-length text on the same label ("Tile 1/2" vs "Tile 1/2: 5/20 images"), causing rapid resize wobble since the label had no minimum width.

### Solution

1. **`sample_view.py`**: Added `setMinimumWidth(350)` on `workflow_status_label` to prevent layout shifts when text length changes.
2. **`tile_collection_dialog.py`**: Removed the `update_sample_view()` call from `on_progress` — only `on_image_progress` updates the Sample View (it has the detailed "Tile X/N: M/T images" text).

## Issue 2: Frames Not Reaching Sample View

### Problem

The frame delivery chain requires `_data_receiver_loop` to be running (reads from live socket → fills `_frame_buffer` → `drain_all_frames()` → route to Sample View). This thread is only started by `start_live_view_streaming()`, which also sends `LIVE_VIEW_START` — but sending that command during/between workflows crashes the server (see `fix_tile_collection_server_crash.md`). With no thread reading the socket, frames are lost.

### Solution

Added a "listen-only" mode to `CameraService`:

- **`ensure_data_receiver_running()`**: Same socket access logic as `start_live_view_streaming()` but skips the `start_live_view()` command send and the `time.sleep(0.5)`. No-op if already streaming.
- **`stop_data_receiver()`**: Sets `_streaming = False`, joins thread, clears socket ref — same as `stop_live_view_streaming()` but skips the `stop_live_view()` command send.

Updated `CameraController`:

- **`set_active_tile_position()`**: Now calls `camera_service.ensure_data_receiver_running()` and sets `_workflow_started_streaming = True` on success.
- **`clear_tile_mode()`**: Changed from `stop_live_view_streaming()` to `stop_data_receiver()` so no `LIVE_VIEW_STOP` is sent.

## Files Modified

| File | Changes |
|------|---------|
| `src/py2flamingo/views/sample_view.py` | Added `setMinimumWidth(350)` on workflow status label |
| `src/py2flamingo/views/dialogs/tile_collection_dialog.py` | Removed duplicate `update_sample_view()` call from `on_progress` |
| `src/py2flamingo/services/camera_service.py` | Added `ensure_data_receiver_running()` and `stop_data_receiver()` methods |
| `src/py2flamingo/controllers/camera_controller.py` | Use new listen-only receiver methods in tile workflow lifecycle |

## Verification

1. Run tile collection on 2+ tiles — no server crash, both tiles complete
2. Sample View status bar shows steady "Tile X/N: M/T images" without wobble/flicker
3. With "Add Z-stacks to Sample View" checked, frames appear in the 3D viewer
4. Log shows "Data receiver started (no LIVE_VIEW_START sent)" and "Stopped data receiver (no LIVE_VIEW_STOP sent)"
5. Log does NOT show LIVE_VIEW_START or LIVE_VIEW_STOP commands between workflows
