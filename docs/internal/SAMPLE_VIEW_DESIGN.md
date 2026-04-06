# Sample View - Integrated Sample Interaction Window

## Overview

The **Sample View** is a unified window that combines all elements needed for interacting with and viewing a sample during microscopy sessions. It consolidates the 3D visualization, live camera feed, orthogonal plane views with MIP data, and illumination controls into a single integrated interface.

**Key Principle:** Everything needed for sample interaction is visible at once - no window switching required during active imaging.

---

## Design Goals

1. **Single Window** - All sample interaction elements in one place
2. **Always Visible** - Critical elements (live view, plane views, illumination) always on screen
3. **MIP-Based Plane Views** - Show Maximum Intensity Projections of acquired data (not just position indicators)
4. **Deprecates Stage Chamber Visualization** - The 3 plane views replace the separate stage chamber window
5. **Quick Access** - Buttons to launch dialogs for saved positions, image display settings, etc.
6. **Launched from Main Dialog** - Connection, Workflow, and Sample Info stay in main window

---

## Components to Integrate

| Component | Source | Purpose |
|-----------|--------|---------|
| 3D Volume View | `Sample3DVisualizationWindow` | Napari-based 3D visualization of acquired data |
| Live Camera Feed | `CameraLiveViewer` | Real-time camera display |
| XZ Plane View (Top-Down) | New (based on `StageChamberVisualization`) | MIP + objective/sample holder/focus plane |
| XY Plane View (Side) | New (based on `StageChamberVisualization`) | MIP + objective/sample holder/focus plane |
| YZ Plane View (End) | New | MIP + objective/sample holder/focus plane |
| Illumination Controls | `LaserLEDControlPanel` | Laser and LED intensity control |
| Dialog Launchers | New | Buttons for saved positions, image settings |

---

## Plane View Features

The 3 orthogonal plane views replace the Stage Chamber Visualization window. Each plane shows:

### Visual Elements (layered, back to front):
1. **MIP of Acquired Data** - Maximum Intensity Projection of voxel data in that plane
2. **Chamber Boundaries** - Stage movement limits (wire-frame)
3. **Sample Holder** - Current position indicator (pole/nub in side view, circle in top-down)
4. **Objective Position** - Shows where the objective/camera is located
5. **Focus Plane Indicator** - Shows the current focal plane (calibrated Z position)
6. **Grid Lines** - Reference grid for positioning

### Interactivity:
- **Click-to-Move** - Click in any plane to move stage to that position
- **Real-time Updates** - Position and MIP update as stage moves and data is acquired
- **Crosshair Linking** - Crosshairs in each view show current slice position of other views

### Data Source:
```python
# MIP extraction from voxel storage
volume = voxel_storage.get_display_volume(channel_id)  # Shape: (Z, Y, X)

# XY Plane (Top-Down): MIP along Z axis
xy_mip = np.max(volume, axis=0)  # Shape: (Y, X)

# XZ Plane (Side View): MIP along Y axis
xz_mip = np.max(volume, axis=1)  # Shape: (Z, X)

# YZ Plane (End View): MIP along X axis
yz_mip = np.max(volume, axis=2)  # Shape: (Z, Y)
```

---

## Proposed Layout

```
+==============================================================================+
|  Sample View                                                    [_][O][X]    |
+==============================================================================+
|                                                                              |
|  +---------------------------+  +----------------------------------------+   |
|  |                           |  |                                        |   |
|  |     Live Camera Feed      |  |           3D Volume View               |   |
|  |       (640 x 480)         |  |             (napari)                   |   |
|  |                           |  |                                        |   |
|  +---------------------------+  |                                        |   |
|  | Display: [Colormap▼]      |  |                                        |   |
|  | [Auto] Min[===] Max[===]  |  |                                        |   |
|  +---------------------------+  +----------------------------------------+   |
|                                                                              |
|  +---------------------------+  +----------------------------------------+   |
|  | ILLUMINATION              |  | POSITION SLIDERS                       |   |
|  | ☑ Laser1 (405) [======]   |  | X: [0.0]===●=========[12.3] mm        |   |
|  | ☐ Laser2 (488) [======]   |  | Y: [5.0]========●====[20.0] mm        |   |
|  | ☐ Laser3 (561) [======]   |  | Z: [12.5]=●==========[26.0] mm        |   |
|  | ☐ Laser4 (640) [======]   |  | R: [0.0]======●======[360°]           |   |
|  | ☐ LED [======] [Color▼]   |  +----------------------------------------+   |
|  | Path: [Left▼]             |                                              |
|  +---------------------------+                                              |
|                                                                              |
|  +---------------------------+---------------------------+-----------------+ |
|  |   XZ Plane (Top-Down)     |   XY Plane (Side View)    | YZ Plane (End)  | |
|  |   MIP + Position Overlay  |   MIP + Position Overlay  | MIP + Position  | |
|  |   [Click to move X,Z]     |   [Click to move X,Y]     | [Click Y,Z]     | |
|  +---------------------------+---------------------------+-----------------+ |
|                                                                              |
|  +------------------------------------------------------------------------+  |
|  | Workflow: [Not Running]  [=========>                    ] 0% | --:--   |  |
|  +------------------------------------------------------------------------+  |
|  | [Saved Positions]  [Image Settings]  [Stage Control]  [Export Data]   |  |
|  +------------------------------------------------------------------------+  |
|                                                                              |
+==============================================================================+
```

### Design Notes

**Dual Positioning Controls:**
- **Position Sliders** (right side) - Drag to move stage on any axis
- **Click-to-Move** (in plane views) - Click directly in MIP views to move to that position
- Both methods complement each other for different use cases

**Embedded Display Controls:**
- Colormap selector (Grayscale, Hot, Viridis, etc.)
- Auto-scale checkbox
- Manual min/max intensity sliders
- No separate window needed for basic display adjustments

**Workflow Progress (Placeholder):**
- Progress bar with percentage
- Time remaining estimate
- Status text (Not Running / Running Step X of Y / Complete)
- Not hooked up yet - placeholder for future workflow implementation

**Illumination Controls:**
- Always visible - no need to open separate window
- Checkboxes for quick on/off
- Power sliders for intensity
- LED color selection
- Light path selection (left/right objective)

---

## Detailed Layout Specifications

### Overall Window
- **Target Size:** ~1400 x 950 px (fits comfortably on 1920x1080)
- **Minimum Size:** 1200 x 850 px
- **Resizable:** Yes, with proportional scaling

### Left Column (Live + Controls)
| Element | Width | Height | Notes |
|---------|-------|--------|-------|
| Live Camera Feed | 640 px | 480 px | Fixed aspect ratio, scalable |
| Display Controls | 640 px | ~40 px | Colormap, auto-scale, intensity range |
| Illumination Controls | 640 px | ~180 px | Lasers, LED, light path |

### Right Column (3D + Position)
| Element | Width | Height | Notes |
|---------|-------|--------|-------|
| 3D Volume View | ~700 px | ~550 px | Napari viewer, fills available space |
| Position Sliders | ~700 px | ~120 px | X, Y, Z, R sliders with labels |

### Plane Views Section
| Element | Width | Height | Notes |
|---------|-------|--------|-------|
| XZ Plane (Top-Down) | ~450 px | ~200 px | Proportional to stage limits, click-to-move |
| XY Plane (Side) | ~450 px | ~200 px | Proportional to stage limits, click-to-move |
| YZ Plane (End) | ~300 px | ~200 px | Proportional to stage limits, click-to-move |

### Workflow Progress Bar (Placeholder)
- **Height:** ~30 px
- **Components:** Status label, progress bar, time remaining
- **State:** Not connected to workflow system yet - visual placeholder only

### Button Bar
- **Height:** ~40 px
- **Buttons:** Saved Positions, Image Settings, Stage Control, Export Data
- **Style:** Standard QPushButtons, evenly spaced

---

## Widget Hierarchy

```
SampleView (QMainWindow or QWidget)
├── QVBoxLayout (main_layout)
│   │
│   ├── QHBoxLayout (top_section)
│   │   ├── QVBoxLayout (left_column)
│   │   │   ├── QGroupBox "Live Camera Feed"
│   │   │   │   └── QLabel (live_image_label) 640x480
│   │   │   ├── QWidget (display_controls)  [EMBEDDED]
│   │   │   │   ├── QComboBox (colormap_combo)
│   │   │   │   ├── QCheckBox (auto_scale_checkbox)
│   │   │   │   ├── QSlider (min_intensity_slider)
│   │   │   │   └── QSlider (max_intensity_slider)
│   │   │   └── QGroupBox "Illumination"
│   │   │       └── LaserLEDControlPanel (modified/embedded)
│   │   │
│   │   └── QVBoxLayout (right_column)
│   │       ├── QGroupBox "3D Volume"
│   │       │   └── napari.Viewer widget
│   │       └── QGroupBox "Position Sliders"
│   │           ├── AxisSlider (x_slider) with min/max labels
│   │           ├── AxisSlider (y_slider) with min/max labels
│   │           ├── AxisSlider (z_slider) with min/max labels
│   │           └── AxisSlider (r_slider) with min/max labels
│   │
│   ├── QHBoxLayout (plane_views_section)
│   │   ├── MIPPlaneView "XZ (Top-Down)" [click-to-move enabled]
│   │   ├── MIPPlaneView "XY (Side)"     [click-to-move enabled]
│   │   └── MIPPlaneView "YZ (End)"      [click-to-move enabled]
│   │
│   ├── QWidget (workflow_progress)  [PLACEHOLDER - not connected]
│   │   ├── QLabel (workflow_status) "Workflow: Not Running"
│   │   ├── QProgressBar (workflow_progress_bar)
│   │   └── QLabel (time_remaining) "--:--"
│   │
│   └── QHBoxLayout (button_bar)
│       ├── QPushButton "Saved Positions"
│       ├── QPushButton "Image Settings"  (for advanced settings)
│       ├── QPushButton "Stage Control"   (opens main window tab)
│       └── QPushButton "Export Data"
```

---

## New Components to Create

### 1. Plane Views - Two Implementation Options

#### Option A: Napari-Based Plane Views (RECOMMENDED)
Use napari's built-in orthogonal slice viewing with custom overlays:

```python
class NapariPlaneViewer(QWidget):
    """
    Napari-based orthogonal plane viewer with position overlays.

    Uses napari's native 2D viewing mode with:
    - Orthogonal slicing through the volume
    - Shapes layer for position indicators (sample holder, objective, focus plane)
    - Points layer for click-to-move targets
    - Built-in colormap and contrast controls
    """

    def __init__(self, voxel_storage, stage_limits: dict, parent=None):
        super().__init__(parent)

        # Create napari viewer in 2D mode
        self.viewer = napari.Viewer(ndisplay=2, show=False)

        # Add image layer for volume data
        self.image_layer = self.viewer.add_image(
            np.zeros((10, 10, 10)),  # Placeholder
            name='Volume',
            colormap='gray',
            rendering='mip'  # Maximum Intensity Projection
        )

        # Add shapes layer for position indicators
        self.shapes_layer = self.viewer.add_shapes(
            name='Position Indicators',
            edge_color='cyan',
            face_color='transparent'
        )

        # Embed napari Qt widget
        layout = QVBoxLayout()
        layout.addWidget(self.viewer.window._qt_window)
        self.setLayout(layout)

    def set_plane(self, plane: str):
        """Set viewing plane: 'xy', 'xz', or 'yz'."""
        # Adjust napari camera/dims to show the specified plane
        ...

    def update_volume(self, volume: np.ndarray):
        """Update the displayed volume data."""
        self.image_layer.data = volume

    def update_position_indicators(self, x, y, z, objective_pos, focus_z):
        """Update shape overlays for position indicators."""
        ...
```

**Advantages of Napari:**
- Built-in MIP rendering
- Native colormap and contrast controls
- Zoom, pan, and other navigation
- Consistent look with 3D viewer
- Less custom code to maintain

**Considerations:**
- Three napari viewers may use more memory
- Need to add custom shapes for position indicators
- Click handling requires napari mouse callbacks

---

#### Option B: Custom PyQt MIPPlaneView Widget
A custom widget that extends the `ChamberViewPanel` concept to include MIP rendering:

```python
class MIPPlaneView(QWidget):
    """
    Orthogonal plane view with MIP overlay and position indicators.

    Combines:
    - MIP (Maximum Intensity Projection) of voxel data
    - Chamber boundaries and grid
    - Sample holder position indicator
    - Objective position indicator
    - Focus plane indicator
    - Click-to-move interaction
    """

    # Signals
    click_position = pyqtSignal(float, float)  # (axis1_mm, axis2_mm)

    def __init__(self, plane_type: str, stage_limits: dict, voxel_storage, parent=None):
        """
        Args:
            plane_type: "XZ", "XY", or "YZ"
            stage_limits: Dict with min/max for each axis
            voxel_storage: Reference to DualResolutionVoxelStorage
        """
        ...

    def update_mip(self, channel_id: int = None):
        """Recalculate MIP from voxel storage."""
        ...

    def update_position(self, x: float, y: float, z: float):
        """Update position indicators."""
        ...

    def set_focus_plane(self, z_mm: float):
        """Update focus plane indicator."""
        ...
```

**Advantages of Custom Widget:**
- Full control over rendering
- Lighter weight (no napari overhead)
- Existing `ChamberViewPanel` code can be extended
- Easier click-to-move implementation

**Considerations:**
- More custom rendering code
- Manual colormap/contrast implementation
- Need to implement zoom/pan manually

### 2. SampleView Window
The main integrated window:

```python
class SampleView(QWidget):
    """
    Integrated sample viewing and interaction window.

    Combines live camera, 3D visualization, MIP plane views,
    and illumination controls in a single interface.
    """

    def __init__(self,
                 camera_controller,
                 movement_controller,
                 laser_led_controller,
                 voxel_storage,
                 parent=None):
        ...

    # Dialog launchers
    def _on_saved_positions_clicked(self):
        """Open saved positions dialog."""
        ...

    def _on_image_settings_clicked(self):
        """Open image controls window."""
        ...

    def _on_stage_control_clicked(self):
        """Open stage control dialog (or focus main window Stage tab)."""
        ...

    def _on_export_data_clicked(self):
        """Open export dialog."""
        ...
```

---

## Integration with Main Dialog

### Main Window Changes
The main window (with Connection, Workflow, Sample Info, Stage Control tabs) gains a button to launch Sample View:

```python
# In MainWindow or ConnectionView
self.open_sample_view_btn = QPushButton("Open Sample View")
self.open_sample_view_btn.clicked.connect(self._open_sample_view)
self.open_sample_view_btn.setEnabled(False)  # Enable when connected

def _open_sample_view(self):
    """Open the integrated Sample View window."""
    if self.sample_view is None:
        self.sample_view = SampleView(
            camera_controller=self.camera_controller,
            movement_controller=self.movement_controller,
            laser_led_controller=self.laser_led_controller,
            voxel_storage=self.voxel_storage,
            parent=None  # Independent window
        )
    self.sample_view.show()
    self.sample_view.raise_()
```

### What Stays in Main Dialog
- **Connection Tab** - IP/Port, connect/disconnect, debug tools
- **Workflow Tab** - Workflow file selection and execution
- **Sample Info Tab** - Network paths, sample name, save location
- **Stage Control Tab** - Detailed stage positioning (may be redundant with Sample View)

---

## Deprecation Plan

### Stage Chamber Visualization Window
- **Current:** `stage_chamber_visualization_window.py` + `stage_chamber_visualization.py` widget
- **Replacement:** MIP plane views in Sample View
- **Action:** Mark as deprecated, keep for compatibility, remove in future version

### Separate Live Viewer and 3D Windows
- **Current:** `CameraLiveViewer` and `Sample3DVisualizationWindow` as separate windows
- **Replacement:** Integrated in Sample View
- **Action:** Keep original windows available for users who prefer separate windows

---

## Implementation Phases

### Phase 1: Core Structure
1. Create `SampleView` window with basic layout
2. Embed existing `CameraLiveViewer` display (not the full window, just the image display)
3. Embed existing napari viewer from `Sample3DVisualizationWindow`
4. Add `LaserLEDControlPanel`

### Phase 2: MIP Plane Views
1. Create `MIPPlaneView` widget based on `ChamberViewPanel`
2. Add MIP calculation from voxel storage
3. Implement position indicators (sample holder, objective, focus plane)
4. Add click-to-move functionality

### Phase 3: Integration
1. Connect all signals (position updates, data updates, illumination)
2. Add dialog launcher buttons
3. Wire up "Open Sample View" button in main dialog
4. Test full workflow

### Phase 4: Polish
1. Add keyboard shortcuts
2. Implement window position saving/restoring
3. Add tooltips and help
4. Performance optimization for MIP updates

---

## Technical Considerations

### Performance
- MIP calculation can be expensive for large volumes
- Use caching: recalculate MIP only when data changes
- Consider downsampling for display if volume is very large
- Use `QTimer.singleShot()` to defer heavy updates

### Threading
- All Qt GUI updates on main thread
- MIP calculation could be done in background thread with signal to update display
- Inherit thread safety patterns from existing components

### Memory
- MIP images are 2D projections (much smaller than full volume)
- Cache 3 MIP images (one per plane)
- Clear cache when voxel storage is cleared

### Signal Connections
```python
# Position updates
movement_controller.position_changed.connect(self._on_position_changed)

# Data updates (when new voxels are added)
voxel_storage.data_changed.connect(self._on_data_changed)

# Camera updates
camera_controller.frame_received.connect(self._on_frame_received)

# Illumination (already handled by LaserLEDControlPanel)
```

---

## Existing Code to Reuse

| Component | File | Reuse Strategy |
|-----------|------|----------------|
| Live image display | `camera_live_viewer.py` | Extract display logic, not full window |
| Napari 3D viewer | `sample_3d_visualization_window.py` | Embed napari widget directly |
| Illumination panel | `laser_led_control_panel.py` | Use directly as embedded widget |
| Chamber drawing | `stage_chamber_visualization.py` | Extend `ChamberViewPanel` for MIP |
| Position control | `stage_chamber_visualization_window.py` | Reference for click-to-move |
| Voxel storage | `dual_resolution_voxel_storage.py` | Direct reference for MIP extraction |

---

## Design Decisions (Resolved)

1. **Dual positioning controls** - CONFIRMED
   - Position sliders provide direct axis control
   - Click-to-move in plane views provides visual positioning
   - Both methods available simultaneously

2. **Display controls** - EMBEDDED
   - Basic display controls (colormap, auto-scale, intensity) embedded below live view
   - No separate window needed for common adjustments
   - "Image Settings" button available for advanced options

3. **Illumination controls** - ALWAYS VISIBLE
   - Embedded in main interface, not hidden in separate window
   - Quick access to laser on/off and power adjustment

4. **Workflow progress** - PLACEHOLDER
   - Progress bar and time estimate UI present but not connected
   - Will be hooked up when workflow system is implemented

---

## Open Questions

1. **Should the 3D napari viewer stay as is or be simplified?**
   - Current 3D viewer has many controls (channels, sample control, data tabs)
   - Sample View could have a simplified version with controls in a sidebar
   - Consider: Keep full napari controls available via right-click context menu?

2. **Napari for plane views vs custom PyQt widgets?**
   - Napari: Built-in MIP, colormap, zoom/pan - but heavier weight
   - Custom: Lighter, full control, easier click-to-move - but more code
   - Need to test napari's memory usage with 3-4 viewers

3. **Should plane views share colormap with live view?**
   - Option A: Same colormap/intensity settings for all views (simpler)
   - Option B: Independent settings per view (more flexible)

4. **What happens to the main window when Sample View is open?**
   - Keep both visible (current plan)
   - Main window provides Connection, Workflow, Sample Info
   - Sample View provides active imaging interface

---

## Summary

The Sample View consolidates:
- Live camera feed
- 3D volume visualization (napari)
- 3 orthogonal MIP plane views (replacing Stage Chamber Visualization)
- Illumination controls
- Quick-access dialog buttons

All in a single integrated window, launched from the main dialog, providing everything needed for active sample interaction without window switching.
