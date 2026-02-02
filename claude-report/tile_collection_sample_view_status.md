# Claude Report: Add Sample View Workflow Status During Tile Collection

**Date:** 2026-02-02

## Issue Summary

During tile collection workflows, the Sample View's status bar showed "Workflow: Not Running" the entire time. The LED 2D Overview scan correctly updated this status, but the Tile Collection dialog did not.

## Root Cause

The `TileCollectionDialog._execute_with_queue_service()` had progress callbacks that updated a local `QProgressDialog`, but never called `sample_view.update_workflow_progress()`. The Sample View's `update_workflow_progress()` method was only called by `LED2DOverviewDialog`, not by the tile collection path.

## Solution

Added `update_sample_view()` calls to the existing progress callbacks in `TileCollectionDialog._execute_with_queue_service()`:

- **On start:** Status set to `"Tile Collection: 0/N tiles"`
- **On workflow progress:** Updates with `"Tile X/N"`
- **On image progress:** Updates with `"Tile X/N: M/T images"`
- **On completion/cancel/error:** Resets to `"Not Running"`

## Files Modified

| File | Changes |
|------|---------|
| `src/py2flamingo/views/dialogs/tile_collection_dialog.py` | Add `update_sample_view()` helper and calls in progress callbacks |

## Verification

1. Select tiles in 2D Overview Results, run Collect Tiles
2. Sample View status bar should show tile collection progress (e.g., "Workflow: Tile 1/3: 5/20 images")
3. After completion or cancellation, status should return to "Workflow: Not Running"
