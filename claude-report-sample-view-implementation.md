# Claude Report: Sample View Implementation

**Date:** 2025-12-11
**Commits:** b967b66 through 2cb6a40 (11 commits)
**Purpose:** Create a unified Sample View interface combining all sample interaction controls in one window

---

## Overview

The Sample View is a new unified interface that consolidates microscope sample interaction controls that were previously scattered across multiple dialog windows. This allows users to view and interact with the sample without switching between windows.

### Goals
- Combine 3D visualization, live camera feed, and 2D slice views in one interface
- Reuse existing implementations (no duplication of backend code)
- Provide quick access to illumination and position controls
- Enable click-to-move on 2D slice plane views
- Maintain compact layout (600px width target)

---

## Components Integrated

### 1. 3D Napari Viewer (Reused)
- Embeds the existing `Sample3DVisualizationWindow` napari viewer
- Shares the same `voxel_storage` for volume data
- Displays zoom level in status bar
- Zoom set to 1.6 for better chamber visibility

### 2. Live Camera Feed (Reused)
- Uses existing `CameraController.start_live_view()` and `stop_live_view()`
- Connects to `CameraController.new_image` signal for frame updates
- Displays FPS counter and frame status
- 4:3 aspect ratio (360×270 pixels)

### 3. 2D Slice Plane Viewers (New)
Three plane viewers showing MIP projections:

| Plane | Horizontal Axis | Vertical Axis | Border Colors |
|-------|-----------------|---------------|---------------|
| XY    | X (Cyan)        | Y (Magenta)   | Cyan/Magenta  |
| XZ    | X (Cyan)        | Z (Yellow)    | Cyan/Yellow   |
| YZ    | Y (Magenta)     | Z (Yellow)    | Magenta/Yellow|

**Features:**
- Colored borders matching napari axis colors
- Click-to-move: Click location sends position command
- Overlay elements: Holder outline, objective position, viewing frame
- Sized proportionally to stage dimensions (X:11mm, Y:20mm, Z:13.5mm)

### 4. Light Source Control (Existing)
- Embeds `LaserLEDControlPanel` for quick illumination switching
- Moved up in layout per user feedback
- Reduced title font size for compactness

### 5. Position Controls (New)
- Min-Max range sliders for X, Y, Z axes
- Editable text fields for precise positioning
- X-axis respects inverted setting from config

---

## Files Created/Modified

### New Files

#### `src/py2flamingo/views/sample_view.py` (~1400 lines)

Main classes:

```python
class SlicePlaneViewer(QFrame):
    """2D slice plane viewer with colored borders and overlays."""
    position_clicked = pyqtSignal(float, float)

    def __init__(self, plane: str, h_axis: str, v_axis: str,
                 width: int, height: int, parent=None):
        # Colored borders matching napari axis colors
        self.setStyleSheet(f"""
            SlicePlaneViewer {{
                border-left: 3px solid {h_color};
                border-right: 3px solid {h_color};
                border-top: 3px solid {v_color};
                border-bottom: 3px solid {v_color};
            }}
        """)
```

```python
class ViewerControlsDialog(QDialog):
    """Placeholder dialog for viewer-specific settings."""
    # Future: napari layer controls, colormap settings, etc.
```

```python
class SampleView(QWidget):
    """Unified sample viewing and interaction interface."""

    def __init__(self, camera_controller, movement_controller,
                 laser_led_controller, voxel_storage=None,
                 image_controls_window=None, sample_3d_window=None, parent=None):
        # Reuses existing window implementations

    def _embed_3d_viewer(self) -> None:
        """Embed napari viewer from existing Sample3DVisualizationWindow."""
        viewer = self.sample_3d_window.viewer
        viewer_widget = viewer.window._qt_viewer
        # Replace placeholder with actual widget
```

### Modified Files

#### `src/py2flamingo/application.py`
- Added `_open_sample_view()` method
- Wires Sample View with shared resources (voxel_storage, controllers)
- Connected to connection view button

#### `src/py2flamingo/views/connection_view.py`
- Added "Sample View" button to open new interface

#### `src/py2flamingo/views/sample_3d_visualization_window.py`
- Changed zoom from 2.0 to 1.6 for better chamber visibility

#### `src/py2flamingo/views/laser_led_control_panel.py`
- Removed verbose "Select a light source..." text
- Reduced title font from 12pt to 11pt

#### `src/py2flamingo/views/image_controls_window.py`
- Renamed window title to "Live Display"

---

## Design Decisions

### 1. Reuse vs. Recreate
**Decision:** Embed existing window widgets rather than recreating functionality

**Rationale:** User feedback explicitly requested that the 3D viewer and live viewer be "direct ports" from their respective dialogs. This ensures:
- Consistent behavior with standalone windows
- No code duplication
- Shared state (voxel_storage, camera state)

### 2. Axis Colors
**Decision:** Match napari's default axis colors

| Axis | Color | Hex |
|------|-------|-----|
| X | Cyan | #008B8B |
| Y | Magenta | #8B008B |
| Z | Yellow | #8B8B00 |

**Rationale:** Provides visual consistency between 3D viewer and 2D slice planes

### 3. Layout Proportions
**Decision:** Size 2D viewers proportionally to actual stage dimensions

| Axis | Range | Proportion |
|------|-------|------------|
| X | 11mm | ~0.55 |
| Y | 20mm | 1.0 |
| Z | 13.5mm | ~0.675 |

**Rationale:** Prevents visual distortion when mapping stage positions

### 4. Click-to-Move
**Decision:** Clicking on 2D slice views sends position commands

**Rationale:** Provides intuitive navigation without needing separate joystick controls

---

## Layout Evolution

The layout went through several iterations based on user feedback:

### Initial Issues (LS-GUI1.PNG)
- Camera feed stretched (wrong aspect ratio)
- 3D viewer too horizontal (should be tall)
- Plane views wrong proportions
- Illumination controls squished

### Fixes Applied
- Set camera feed to 4:3 aspect ratio (360×270)
- Made 3D viewer tall/vertical (min 250×450)
- Sized plane views based on stage dimensions
- Added minimum width (320px) for illumination panel

### Final Layout
```
┌─────────────────────────────────────────┐
│ Sample View                          [X]│
├──────────────┬──────────────────────────┤
│              │                          │
│  3D Napari   │    Live Camera Feed      │
│   Viewer     │       (4:3)              │
│   (tall)     │                          │
│              ├──────────────────────────┤
│              │  [Live View Settings]    │
│              │  Status: 30 FPS          │
├──────────────┴──────────────────────────┤
│  Light Source Control                   │
│  ○ LED  ○ Laser 1  ○ Laser 2 ...       │
├─────────────────────────────────────────┤
│  Position Sliders (X, Y, Z with min-max)│
├──────────────┬──────────────────────────┤
│   XY Plane   │   XZ        YZ           │
│   (large)    │  (smaller planes)        │
└──────────────┴──────────────────────────┘
```

---

## Commit History

| Commit | Description |
|--------|-------------|
| b967b66 | Compact GUI layout to two-thirds width (900px → 600px) |
| 1094e17 | Replace GUI_REDESIGN_PLAN with SAMPLE_VIEW_DESIGN |
| 10dc178 | Add SampleView - integrated sample interaction window |
| b75c19a | Fix: Use correct signal name (new_image) in SampleView |
| cc2b743 | Fix Sample View layout proportions for vertical chamber |
| e2ca9b1 | Enhance Sample View layout and controls |
| 8fc116b | Integrate napari viewer and improve Sample View controls |
| 576b7f5 | Connect Sample View to actual backend components |
| 2655535 | Refactor Sample View to reuse existing implementations |
| 9c1bde3 | Add 2D slice plane viewers with colored borders and overlays |
| 2cb6a40 | Add zoom display and improve Sample View compactness |
| 8e3d709 | Fix Sample View to load real values on initialization (safety fix) |

---

## Bugs Fixed

### 1. AttributeError: 'CameraController' has no attribute 'frame_received'
**Cause:** Used wrong signal name
**Fix:** Changed `frame_received` to `new_image` (correct signal in CameraController)
**Commit:** b75c19a

### 2. 3D Viewer Not Matching Original
**Cause:** Created duplicate napari viewer instead of reusing existing
**Fix:** Refactored to embed `Sample3DVisualizationWindow.viewer.window._qt_viewer`
**Commit:** 2655535

### 3. Position Sliders Showing Max Values (Safety Issue)
**Cause:** Sliders initialized with hardcoded values (50000) instead of querying actual stage position
**Risk:** User opening Sample View could accidentally move stage to dangerous position
**Fix:** Added `_load_current_positions()` method that queries `movement_controller.get_position()` on init
**Commit:** 8e3d709

**Code added:**
```python
def _load_current_positions(self) -> None:
    """Load current stage positions from movement controller and update sliders.

    This is critical for safety - sliders must reflect actual stage position,
    not default values that could cause dangerous movements.
    """
    current_pos = self.movement_controller.get_position()
    for axis_id, value in positions.items():
        slider.blockSignals(True)  # Prevent triggering movement
        slider.setValue(int(value * self._slider_scale))
        slider.blockSignals(False)
```

### 4. Contrast Settings Not Persisted
**Cause:** Contrast min/max values were hardcoded, not configurable
**Fix:** Added `default_contrast_min` and `default_contrast_max` to each channel in `visualization_3d_config.yaml`
**Commit:** 8e3d709

---

## Testing Notes

### Verified Functionality
- [x] Sample View opens from connection view button
- [x] 3D napari viewer displays and shares voxel_storage
- [x] Live camera feed starts/stops correctly
- [x] Light source switching works from embedded panel
- [x] Position sliders send movement commands
- [x] Zoom level displays in status bar
- [x] Position sliders load current stage position on init
- [x] Laser power levels load from controller
- [x] Contrast settings load from visualization config

### Known Limitations
- 2D slice viewers show placeholder images (MIP projection not yet connected)
- Click-to-move on slice viewers sends commands but overlay positions not updated in real-time
- Viewer Controls dialog is a placeholder

---

## Future Enhancements

1. **MIP Projection Updates**: Connect 2D slice viewers to voxel_storage for real-time MIP rendering
2. **Overlay Synchronization**: Update holder/objective overlays when positions change
3. **Viewer Controls**: Implement napari layer controls, colormap settings
4. **Channel Selection**: Add per-viewer channel controls
5. **Zoom Optimization**: Determine optimal zoom level for different chamber sizes
