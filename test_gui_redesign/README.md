# GUI Redesign Test Implementation

This directory contains test implementations of redesigned GUI components for the Flamingo Control microscope software.

## Overview

The goal is to enable the Live Viewer and 3D Visualization windows to fit side-by-side on a 1920px wide screen. This is achieved by:

1. **CameraLiveViewer**: Converting from horizontal (1000x600) to vertical layout (660x730)
2. **Sample3DVisualizationWindow**: Narrowing from 1200px to 950px and adding multi-plane imaging tab

## Design Specifications (from Agent 1)

### CameraLiveViewer
- **Target Dimensions**: 660px W x 730px H
- **Layout**: Vertical (image on top, controls below)
- **Key Changes**:
  - Image display: 640x480 minimum, full width
  - Laser/LED panel: Collapsible to save space
  - Camera controls: Compact horizontal arrangement
  - Image info: Condensed grid layout

### Sample3DVisualizationWindow
- **Target Dimensions**: 950px W x 800px H
- **Layout**: Horizontal splitter with tabbed viewer
- **Key Changes**:
  - Control panel: 250px (narrowed from 270px)
  - Viewer area: 700px with QTabWidget
  - New "Multi-Plane Views" tab showing XY, YZ, XZ planes
  - Z-slider for navigating through depth

### Screen Layout
```
┌─────────────────────┬──────────────────────────────────┐
│ CameraLiveViewer    │ Sample3DVisualizationWindow      │
│ 660px wide          │ 950px wide                       │
│                     │                                  │
│ [Live Image]        │ [Controls] [3D/Multi-Plane View] │
│ [Controls]          │                                  │
│ [Info]              │                                  │
└─────────────────────┴──────────────────────────────────┘
     660px                    950px              = 1610px
                      Gap: 310px remaining on 1920px screen
```

## Files in This Directory

### 1. test_camera_live_viewer.py
Test implementation of CameraLiveViewer with vertical layout.

**Usage:**
```python
from test_gui_redesign.test_camera_live_viewer import TestCameraLiveViewer

viewer = TestCameraLiveViewer(camera_controller, laser_led_controller, image_controls_window)
viewer.show()
```

**Implementation Status:**
- ✅ Basic structure created
- ⏳ Awaiting Agent 2 feedback
- ⏳ Final layout implementation per Agent 1 specs

### 2. test_sample_3d_visualization_window.py
Extended version of Sample3DVisualizationWindow with multi-plane imaging tab.

**Usage:**
```python
from test_gui_redesign.test_sample_3d_visualization_window import TestSample3DVisualizationWindow

window = TestSample3DVisualizationWindow(
    movement_controller,
    camera_controller,
    laser_led_controller
)
window.show()
```

**Implementation Status:**
- ✅ Basic structure created
- ✅ Multi-plane tab UI layout
- ⏳ Data extraction from voxel_storage
- ⏳ Awaiting Agent 2 feedback

### 3. test_integration_demo.py
Demonstration script showing both windows side-by-side.

**Usage:**
```bash
cd /home/msnelson/LSControl/Flamingo_Control
python test_gui_redesign/test_integration_demo.py
```

**Features:**
- Launches both test windows
- Positions them side-by-side
- Shows total screen usage
- Validates dimensions

### 4. README.md (this file)
Documentation for the test implementation.

## Implementation Approach

### Design Pattern: Inheritance-Based Testing

All test implementations use inheritance to avoid modifying original code:

```python
class TestCameraLiveViewer(CameraLiveViewer):
    """Test version with new layout."""

    def _setup_ui(self) -> None:
        """Override to use new layout."""
        self.test_setup_ui()

    def test_setup_ui(self) -> None:
        """New layout implementation."""
        # Create vertical layout per Agent 1 specs
        pass
```

**Benefits:**
- ✅ Original code remains unchanged and functional
- ✅ Easy to compare old vs new side-by-side
- ✅ Can easily revert if issues found
- ✅ Maintains all existing functionality
- ✅ Signal connections and methods inherited unchanged

### Key Principles

1. **DO NOT modify original files** - Only create new test files
2. **Inherit, don't rewrite** - Reuse as much original code as possible
3. **Override selectively** - Only change what's necessary (mainly `_setup_ui`)
4. **Preserve functionality** - All buttons, sliders, signals work identically
5. **Document changes** - Clear comments showing what changed and why

## Testing Strategy

### Phase 1: Layout Testing (Current Phase)
- ✅ Create basic structure and widget hierarchy
- ⏳ Verify dimensions match Agent 1 specifications
- ⏳ Test with mock data/controllers
- ⏳ Ensure all widgets visible and accessible

### Phase 2: Functional Testing
- ⏳ Test with real hardware controllers
- ⏳ Verify all buttons and controls work
- ⏳ Test image display and updates
- ⏳ Test multi-plane data extraction
- ⏳ Verify signal connections

### Phase 3: Integration Testing
- ⏳ Test both windows open simultaneously
- ⏳ Verify total width ≤ 1920px
- ⏳ Test on target monitor resolution
- ⏳ Verify window resizing behavior
- ⏳ Test window positioning and management

### Phase 4: User Acceptance Testing
- ⏳ Collect Agent 2 feedback
- ⏳ Test with actual imaging workflows
- ⏳ Verify control accessibility during operations
- ⏳ Assess cognitive load and intuitiveness
- ⏳ Gather user feedback

## Implementation Status

### Completed
- ✅ Code analysis of existing implementations
- ✅ Agent 1 design specifications received
- ✅ Basic test file structure created
- ✅ Inheritance-based approach implemented
- ✅ Documentation framework

### In Progress
- ⏳ Waiting for Agent 2 user evaluation
- ⏳ Final layout implementation per Agent 1 specs
- ⏳ Multi-plane data extraction implementation
- ⏳ Collapsible laser/LED panel implementation

### Pending
- ⏳ Functional testing with hardware
- ⏳ Integration testing both windows
- ⏳ User acceptance testing
- ⏳ Performance optimization
- ⏳ Final refinements based on feedback

## Technical Details

### CameraLiveViewer Changes

**Current (_setup_ui line 83-244):**
```python
def _setup_ui(self) -> None:
    main_layout = QHBoxLayout()  # HORIZONTAL
    # Left: Image (stretch=2)
    # Right: Controls (stretch=1)
    self.setMinimumSize(1000, 600)
```

**Test Version (test_setup_ui):**
```python
def test_setup_ui(self) -> None:
    main_layout = QVBoxLayout()  # VERTICAL
    # Top: Image (full width, 640x480 min)
    # Bottom: Controls (horizontal arrangement)
    #   - Camera controls (grid layout)
    #   - Laser/LED panel (collapsible)
    #   - Image info (grid layout)
    self.setMinimumSize(660, 730)
```

### Sample3DVisualizationWindow Changes

**Current:**
```python
# Line 303: splitter.setSizes([270, 930])
# Line 289-301: Napari viewer embedded directly
```

**Test Version:**
```python
# Narrower splitter: splitter.setSizes([250, 700])
# Wrap napari in QTabWidget:
viewer_tabs = QTabWidget()
viewer_tabs.addTab(napari_widget, "3D Volume")
viewer_tabs.addTab(multiplane_widget, "Multi-Plane Views")
```

### Multi-Plane Tab Data Flow

```
voxel_storage (DualResolutionVoxelStorage)
    ↓
get_display_volume(channel_id) → volume (Z, Y, X) shape
    ↓
Slice extraction:
- XY plane: volume[z_idx, :, :]
- YZ plane: volume[:, :, x_idx]
- XZ plane: volume[:, y_idx, :]
    ↓
Convert to QPixmap (with colormap, contrast)
    ↓
Display on QLabel widgets
    ↓
Update on slider change or data update
```

## Next Steps

1. **Await Agent 2 Feedback** - User evaluation of Agent 1's design
2. **Finalize Layout Implementation** - Complete test_setup_ui() methods
3. **Implement Multi-Plane Data** - Connect to voxel_storage
4. **Test with Hardware** - Verify functionality with real microscope
5. **Refine and Optimize** - Based on testing results
6. **Documentation** - Update with final specifications

## Questions or Issues?

- See `/home/msnelson/LSControl/Flamingo_Control/GUI_REDESIGN_PLAN.md` for full design discussion
- Agent 1 design specifications: Lines 32-383
- Agent 2 user evaluation: Lines 385-428 (in progress)
- Agent 3 implementation plan: Lines 430+ (this implementation)

## References

- Original CameraLiveViewer: `src/py2flamingo/views/camera_live_viewer.py`
- Original Sample3DVisualizationWindow: `src/py2flamingo/views/sample_3d_visualization_window.py`
- Reference pattern: `src/py2flamingo/views/stage_chamber_visualization_window.py`
- Design document: `GUI_REDESIGN_PLAN.md`
