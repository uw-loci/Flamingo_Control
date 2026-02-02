# Claude Report: Fix Server Crash Between Queued Tile Workflows

**Date:** 2026-02-02

## Issue Summary

When collecting two or more tiles via the Tile Collection dialog, the microscope server crashed (became unresponsive) between tile workflows, causing a full disconnection. Only the first tile completed successfully.

## Root Cause

Between queued tile workflows, the following command sequence was sent to the server:

| Step | Command | Source |
|------|---------|--------|
| 1 | `LIVE_VIEW_STOP` (0x3008) | `clear_tile_mode()` via `on_workflow_completed()` |
| 2 | `LIVE_VIEW_START` (0x3007) | `set_active_tile_position()` via next tile's start callback |
| 3 | `WORKFLOW_START` (0x3004) | `_execute_single_workflow()` starting next tile |

The `LIVE_VIEW_START` at step 2 was sent to a server that had just finished a workflow and was about to receive another `WORKFLOW_START`. The server could not handle `LIVE_VIEW_START` in this state — it timed out, stopped responding to all commands (`STATE_GET` also timed out), and the client disconnected.

**The chain:** `on_workflow_completed()` → `_clear_tile_position()` → `clear_tile_mode()` → `stop_live_view_streaming()` → then next tile's callback → `set_active_tile_position()` → `is_streaming()` returns False → `start_live_view_streaming()` → server crash.

The streaming start/stop cycle between tiles was unnecessary — the workflow handles its own camera acquisition.

## Solution

Removed the `start_live_view_streaming()` call from `CameraController.set_active_tile_position()`. This method now only sets tile mode metadata and ensures the display timer runs for frame routing. It no longer touches live view streaming, which is managed by the workflow itself.

Since `_workflow_started_streaming` is now always `False`, the corresponding `stop_live_view_streaming()` in `clear_tile_mode()` also never fires, eliminating the stop/start cycle entirely.

## Files Modified

| File | Changes |
|------|---------|
| `src/py2flamingo/controllers/camera_controller.py` | Remove `start_live_view_streaming()` from `set_active_tile_position()` |

## Verification

1. Open 2D Overview Results, select 2+ tiles, run Collect Tiles
2. All tiles should complete without server disconnection
3. Log should NOT show `LIVE_VIEW_START` or `LIVE_VIEW_STOP` between workflows
4. Log should show `set_active_tile_position` and `clear_tile_mode` between tiles (metadata only, no streaming commands)
