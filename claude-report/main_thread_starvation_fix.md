# Claude Report: Fix Main-Thread Starvation in Tile→Sample View Pipeline

**Date:** 2026-01-30

## Issue Summary

After the signal/slot wiring fix (commit 3b8599f), tile mode activated and frames were routed for workflow 1, but:
1. All subsequent cross-thread signals were starved — `clear_tile_mode` never called after WF1, `set_active_tile_position` never delivered for WF2-4
2. Application froze during workflow 3 (log ends abruptly)
3. No data appeared in 3D view

## Root Cause

`_on_tile_zstack_frame()` → `_add_frame_to_3d_volume()` performed three expensive operations **per frame on the main thread**:

| Operation | Cost per frame |
|-----------|---------------|
| Synchronous TCP call (`get_pixel_field_of_view()`) | Blocked socket reader, could consume server callbacks → freeze |
| `np.meshgrid` for 2048×2048 pixels | ~96MB allocation (identical for every frame in same tile) |
| `image.copy()` into `_accumulated_zstacks` | 8MB leak (stored but never read back) |

Each frame callback blocked the main thread for hundreds of ms at 30fps, starving the Qt event loop so queued cross-thread signals were never delivered.

## Solution

Three changes in `src/py2flamingo/views/sample_view.py`:

### 1. Cache pixel FOV in `prepare_for_tile_workflows()`

Query `get_pixel_field_of_view()` once when tile workflows start, store as `self._cached_pixel_size_mm`. Eliminates the synchronous TCP call from every frame.

### 2. Cache XY world coordinates per tile in `_add_frame_to_3d_volume()`

XY world coordinates are identical for every frame in the same tile (same center, same pixel grid, same pixel size). Computed once per tile into `self._tile_xy_cache`, reused for all frames. Only Z varies per frame.

Additionally, the threshold mask is applied **before** building the coordinate array — if 10% of pixels pass threshold, a (~400K, 3) array is built instead of (~4M, 3).

### 3. Replace image accumulation with frame counter

`_accumulated_zstacks` stored `image.copy()` (8MB/frame) but was only used for counting. Replaced with an integer counter per tile key.

## Performance Impact

| Operation | Before (per frame) | After (per frame) |
|-----------|-------------------|-------------------|
| TCP call (`get_pixel_field_of_view`) | 1 synchronous call | **0** (cached) |
| Meshgrid 2048×2048 | 2 arrays (~32MB) | **0** (cached per tile) |
| World coord arrays | ~96MB allocated | **Only masked pixels** (~5-10MB) |
| Image copy to accumulator | 8MB | **0** (counter only) |
| **Total per-frame allocation** | **~136MB** | **~5-10MB** |

## Files Modified

| File | Changes |
|------|---------|
| `src/py2flamingo/views/sample_view.py` | Cache pixel FOV, precompute XY coords per tile, apply mask before building arrays, remove image accumulation leak |

## Verification

1. Run app, connect, open Sample View, open 2D Overview
2. Select 4 tiles → Collect Tiles → check "Add Z-stacks to Sample View"
3. Log should show:
   - `"Sample View: Cached pixel FOV: X.XXX µm/pixel"` (once, during prepare)
   - `"Cached XY coordinates for tile ..."` (once per tile, not per frame)
   - `"Set tile position for Sample View: ..."` for **each** workflow (not just WF1)
   - `"CameraController: Cleared tile mode after N frames"` between workflows
   - No pixel FOV queries during frame routing
4. Application should NOT freeze during workflows
5. 3D volume should populate with voxel data at correct tile positions
