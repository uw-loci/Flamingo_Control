# Claude Report: Sample View Implementation

**Date:** 2025-12-11 (Updated: 2025-12-12)
**Commits:** b967b66 through 67b18f9 (25+ commits)
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
- Zoom set to 1.57 for optimal chamber visibility
- Camera reset after embedding to ensure proper initialization

### 2. Live Camera Feed (Reused)
- Uses existing `CameraController.start_live_view()` and `stop_live_view()`
- Connects to `CameraController.new_image` signal for frame updates
- Displays FPS counter and frame status
- 4:3 aspect ratio (360×270 pixels)
- Auto-contrast with stabilization (1-second timer, percentage-based adjustments)

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
- Min-Max range sliders for X, Y, Z, R axes
- Color-coded to match napari axis colors (X=cyan, Y=magenta, Z=yellow, R=gray)
- Editable text fields for precise positioning
- X-axis respects inverted setting from config

### 6. Viewer Controls Dialog (New - 2025-12-12)
Full implementation with tabbed interface:

**Channels Tab** (per channel: 405nm, 488nm, 561nm, 640nm):
- Visibility toggle (checkbox)
- Colormap selector (blue, cyan, green, red, magenta, yellow, gray)
- Opacity slider (0-100%)
- Contrast range slider (QRangeSlider 0-65535)

**Display Tab**:
- Rendering mode selector (mip, minip, average, iso)
- Chamber wireframe visibility toggle
- Objective position indicator toggle
- XY Focus Frame visibility toggle
- Coordinate axes visibility toggle
- Reset View button (camera angles + zoom reset)

---

## Files Created/Modified

### New Files

#### `src/py2flamingo/views/sample_view.py` (~2000 lines)

Main classes:

```python
class SlicePlaneViewer(QFrame):
    """2D slice plane viewer with colored borders and overlays."""
    position_clicked = pyqtSignal(float, float)

class ViewerControlsDialog(QDialog):
    """Dialog for controlling napari viewer settings.

    Provides controls for:
    - Channel visibility, colormap, opacity, and contrast
    - Rendering mode (MIP, Volume, etc.)
    - Display settings (chamber wireframe, objective indicator)
    - Camera/view reset
    """
    channel_visibility_changed = pyqtSignal(int, bool)
    channel_colormap_changed = pyqtSignal(int, str)
    channel_opacity_changed = pyqtSignal(int, float)
    channel_contrast_changed = pyqtSignal(int, tuple)
    rendering_mode_changed = pyqtSignal(str)

class SampleView(QWidget):
    """Unified sample viewing and interaction interface."""
```

### Modified Files

#### `src/py2flamingo/application.py`
- Added `_open_sample_view()` method
- Wires Sample View with shared resources (voxel_storage, controllers)
- Connected to connection view button

#### `src/py2flamingo/views/connection_view.py`
- Added "Sample View" button to open new interface

#### `src/py2flamingo/views/sample_3d_visualization_window.py`
- Changed zoom from 2.0 to 1.57 for optimal chamber visibility
- Fixed LED channel detection (`led_R/G/B/W` instead of `led`)

#### `src/py2flamingo/views/laser_led_control_panel.py`
- Removed verbose "Select a light source..." text
- Reduced title font from 12pt to 11pt
- Fixed laser index mapping (button IDs are 1-based from `laser.index`)

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

### 5. Laser Index Mapping
**Decision:** Button IDs use `laser.index` directly (1-based)

| Button ID | Protocol Laser | Napari Channel |
|-----------|----------------|----------------|
| 1 | Laser 1 (405nm) | Channel 0 |
| 2 | Laser 2 (488nm) | Channel 1 |
| 3 | Laser 3 (561nm) | Channel 2 |
| 4 | Laser 4 (640nm) | Channel 3 |

**Rationale:** UI shows lasers 1-4, protocol expects 1-4, napari uses 0-3 internally

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

## Recent Commits (2025-12-12)

| Commit | Description |
|--------|-------------|
| 3fb04f3 | Fix laser index mapping - button IDs are already 1-based |
| 5af3435 | Fix LED channel detection and napari zoom initialization |
| fbcd014 | Implement ViewerControlsDialog with full napari layer controls |
| 11dce4c | Fix LED button ID auto-assignment bug and reduce log spam |
| 3014c0c | Make objective indicator more visible and remove initial camera rotation |
| 67b18f9 | Color-code XYZ position sliders to match napari axis colors |

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

### 4. Contrast Settings Not Persisted
**Cause:** Contrast min/max values were hardcoded, not configurable
**Fix:** Added `default_contrast_min` and `default_contrast_max` to each channel in `visualization_3d_config.yaml`
**Commit:** 8e3d709

### 5. Light Source Not Enabling on Start Live View
**Cause:** When user clicked "Start Live", camera started but laser was not re-enabled
**Symptom:** UI showed laser selected but no illumination occurred
**Fix:** Added call to `laser_led_panel.restore_checked_illumination()` in `_on_live_view_toggle()`
**Commit:** 9db5e27

### 6. Contrast Sliders Only Had Single Handle
**Cause:** Used standard `QSlider` which only supports one value, not min/max range
**Fix:** Replaced with `QRangeSlider` from superqt library for dual-handle min/max control
**Commit:** 9db5e27

### 7. Channel Names Used Generic Labels (Ch1, Ch2)
**Cause:** Hardcoded channel names instead of reading from config
**Fix:** Load wavelength names from `visualization_3d_config.yaml` channels section
**Commit:** 9db5e27

### 8. Missing Data Collection Buttons
**Cause:** "Populate from Live View" and "Clear Data" buttons were not implemented
**Fix:** Added buttons that forward to Sample3DVisualizationWindow's existing functionality
**Commit:** da05700

### 9. Utility Buttons Not Connected
**Cause:** Saved Positions, Stage Control, Export Data buttons had no handlers
**Fix:** Connected each to appropriate functionality
**Commit:** da05700

### 10. Auto-Contrast Flickering
**Cause:** Per-frame min/max calculation caused constant flickering
**Fix:** Implemented stabilized algorithm with 1-second timer and percentage-based thresholds:
- If >20% pixels saturated: raise max to 95% of top 5% mean
- If <5% pixels above 70% of max: lower max by 10%
**Commit:** 37672d3

### 11. Laser Index Off-by-One Error
**Cause:** Button group IDs assumed to be 0-3, but actually set from `laser.index` (1-4)
**Symptom:** `Invalid laser index: 0` errors, wrong lasers activated
**Fix:** Removed erroneous `+1` conversion in 4 places (`_on_source_clicked`, `_on_path_selection_changed`, `get_selected_source`, `restore_checked_illumination`)
**Commit:** 3fb04f3

### 12. LED Channel Detection Failure
**Cause:** Detection checked for `active_source == "led"` but LED stores as `"led_R"`, `"led_G"`, etc.
**Fix:** Changed to `active_source.startswith("led_")` for proper pattern matching
**Commit:** 5af3435

### 13. Napari Zoom Not Initializing
**Cause:** Zoom set in Sample3DVisualizationWindow was reset when widget re-parented to SampleView
**Fix:** Added `_reset_viewer_camera()` method called via `QTimer.singleShot(100ms)` after embedding
**Commit:** 5af3435

### 14. Log Spam "Tick XXX: Live View not active"
**Cause:** When populate timer runs but live view is stopped, warning logged every 10 ticks
**Symptom:** Console flooded with WARNING-level messages during normal operation
**Fix:** Changed `logger.warning()` to `logger.debug()` at line 2990 in sample_3d_visualization_window.py
**Commit:** 11dce4c

### 15. LED Button ID Auto-Assignment Bug (Invalid laser index: 0)
**Cause:** Qt's `QButtonGroup.addButton(button, -1)` auto-assigns IDs starting from -1, not preserving the -1 value. LED button got ID 0, which caused `restore_checked_illumination()` to fall through to laser handling with index 0.
**Symptom:** Selecting LED caused "Invalid laser index: 0" error when live view restarted
**Fix:**
- Added `LED_BUTTON_ID = -100` class constant (large negative to avoid Qt auto-assignment)
- Updated `addButton()` call to use `self.LED_BUTTON_ID`
- Fixed `get_selected_source()` and `restore_checked_illumination()` to check for `LED_BUTTON_ID`
- Fixed `restore_checked_illumination()` to call correct method `enable_led_for_preview()` instead of non-existent `enable_led_preview()`
- Added explicit `elif source_id >= 1` checks for laser handling to prevent invalid indices
**Commit:** 11dce4c

### 16. Objective Indicator Too Dim
**Cause:** Objective circle had thin lines (`edge_width=1`), dim color (`#666600`), and low opacity (`0.3`)
**Symptom:** Objective position indicator barely visible in 3D viewer
**Fix:** Increased `edge_width` to 3, changed color to bright gold (`#FFCC00`), increased opacity to 0.7
**Commit:** 3014c0c

### 17. Sample Chamber Rotated on Startup
**Cause:** Camera angles initialized to `(45, 30, 0)` providing 3D perspective, but appeared oddly rotated
**Fix:** Changed camera angles to `(0, 0, 0)` for straight-on view with no initial rotation
**Commit:** 3014c0c

### 18. Position Sliders Not Color-Coded
**Cause:** XYZ position sliders had default gray styling, inconsistent with napari axis colors
**Fix:** Added color styling to match napari axis colors:
- X: Cyan (#008B8B) - label and slider groove/handle
- Y: Magenta (#8B008B) - label and slider groove/handle
- Z: Yellow (#8B8B00) - label and slider groove/handle
- R: Gray (no napari equivalent)
**Commit:** 67b18f9

---

## Testing Notes

### Verified Functionality
- [x] Sample View opens from connection view button
- [x] 3D napari viewer displays and shares voxel_storage
- [x] Live camera feed starts/stops correctly
- [x] Light source switching works from embedded panel
- [x] Light source re-enables when Start Live clicked
- [x] Position sliders send movement commands
- [x] Zoom level displays in status bar (1.57)
- [x] Position sliders load current stage position on init
- [x] Laser power levels load from controller
- [x] Contrast settings load from visualization config
- [x] Range sliders with dual handles for min/max contrast
- [x] Channel labels show wavelength names (405nm, 488nm, etc.)
- [x] Populate from Live button syncs with 3D window
- [x] Clear Data button clears voxel storage
- [x] Saved Positions opens position history dialog
- [x] Stage Control opens stage visualization window
- [x] Export Data saves voxel data to file
- [x] Auto-contrast stabilization (no flickering)
- [x] Editable position fields with validation
- [x] LED maps to Channel 0 for brightfield testing
- [x] Viewer Controls dialog with full napari layer controls
- [x] Napari zoom initializes correctly to 1.57
- [x] LED selection and restore works correctly (button ID fix)
- [x] Rotation uses sample holder position as rotation center
- [x] Objective indicator visible (thicker, brighter gold)
- [x] Camera starts with straight-on view (no rotation)
- [x] Position sliders color-coded to match napari axes (X=cyan, Y=magenta, Z=yellow)

### Known Limitations
- 2D slice viewers show placeholder images (MIP projection not yet connected)
- Click-to-move on slice viewers sends commands but overlay positions not updated in real-time

---

## Future Enhancements

1. **MIP Projection Updates**: Connect 2D slice viewers to voxel_storage for real-time MIP rendering
2. **Overlay Synchronization**: Update holder/objective overlays when positions change
3. **Channel Selection**: Add per-viewer channel controls
4. **Live Display Settings Dialog**: Implement contrast controls in the settings popup
