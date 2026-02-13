# Claude Report: Phase 6 — SampleView Decomposition

## Overview

Phase 6 decomposed `sample_view.py` (~4,000 lines) by extracting three large, self-contained components into their own files. This reduced `sample_view.py` by ~1,891 lines while creating clearly-bounded modules for chamber visualization, viewer controls, and 2D slice plane views.

## Summary

| New File | Lines | Extracted From |
|---|---|---|
| `views/chamber_visualization_manager.py` | 854 | Chamber/3D napari layer management |
| `views/dialogs/viewer_controls_dialog.py` | 423 | Channel visibility/colormap/contrast dialog |
| `views/widgets/slice_plane_viewer.py` | 731 | 2D MIP plane viewer widget |

**sample_view.py: ~4,000 → ~2,109 lines (-47%)**

## Extractions

### 1. ChamberVisualizationManager

**File:** `src/py2flamingo/views/chamber_visualization_manager.py` (854 lines)

Manages all napari 3D visualization layers for the sample chamber:

- Chamber boundary box (wireframe)
- Objective lens marker (sphere + cone)
- Stage position indicator
- Holder tip visualization
- Grid/axis overlays
- Light sheet plane indicators

Previously embedded as methods throughout `SampleView`. Now a standalone manager class that receives a napari viewer reference and operates independently.

### 2. ViewerControlsDialog

**File:** `src/py2flamingo/views/dialogs/viewer_controls_dialog.py` (423 lines)

Dialog for controlling per-channel visualization settings:

- Channel visibility toggles (on/off)
- Colormap selection (blue, cyan, green, red, magenta, yellow, gray)
- Contrast limit sliders (min, max per channel)
- Emits `plane_views_update_requested` signal when settings change

Uses `PersistentDialog` base class for automatic window geometry persistence.

### 3. SlicePlaneViewer

**File:** `src/py2flamingo/views/widgets/slice_plane_viewer.py` (731 lines)

Self-contained QWidget for 2D Maximum Intensity Projection views of the 3D volume:

- Three instances in SampleView: XZ, XY, YZ planes
- Multi-channel display with additive RGB blending
- Pan/zoom interaction (drag + mousewheel)
- Stage position overlay (white cross), objective marker (green circle)
- Double-click navigation (emits `position_clicked` signal for stage movement)
- Target marker (orange/purple crosshair for active/stale navigation targets)
- Axis labels and coordinate readout overlay
- Viewing frame boundary (cyan dashed rectangle)

Communicates with SampleView exclusively via Qt signals. See the [2D Plane Views report](../docs/claude-report-2d-plane-views.md) for detailed feature documentation.

## Integration Points

`SampleView` retains ownership of the three `SlicePlaneViewer` instances and coordinates their data updates:

1. **Automatic updates**: Visualization timer (500ms single-shot) → `_update_visualization()` → `_update_plane_views()` → fetches per-channel MIP from `voxel_storage` → calls `set_multi_channel_mip()` on each viewer. **Note:** During tile workflows, the timer is suppressed to avoid GUI-thread blocking that causes frame loss (see `tile_workflow_missing_tiles_fix` report). The final update fires in `finish_tile_workflows()`.
2. **Manual updates**: `ViewerControlsDialog.plane_views_update_requested` signal → `_update_plane_views()`
3. **Navigation**: `SlicePlaneViewer.position_clicked` signal → `SampleView._on_plane_click()` → stage movement

## Verification

All files pass `python3 -m py_compile`. Import chains verified.
