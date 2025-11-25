# GUI Redesign Plan - Collaborative Document

## Project Goal
Reimplement the GUI for the 3D viewer and Live view so both can fit on screen simultaneously.

## Current Analysis

### Existing Components:
1. **CameraLiveViewer** (`camera_live_viewer.py`)
   - Current size: 1000x600 minimum (too wide)
   - Layout: Horizontal (image left 2/3, controls right 1/3)
   - Controls: Live view buttons, exposure, laser/LED panel, camera controls, image info

2. **Sample3DVisualizationWindow** (`sample_3d_visualization_window.py`)
   - Current size: 1200x800 (too wide)
   - Features: Napari 3D viewer, control panel with sliders
   - Shows 3D volume visualization

3. **StageChamberVisualizationWindow** (`stage_chamber_visualization_window.py`)
   - Shows XZ (top-down) and XY (side) 2D views
   - Has position control sliders
   - Can be used as reference for new imaging data tab

## Requirements:
1. Move Live Viewer controls underneath the image (requires restructuring)
2. Create second tab in 3D viewer for imaging data showing XY, YZ, XZ planes
3. DO NOT modify existing functions - create new test versions named `test_originalfunctionname`
4. Preserve functioning interface while testing new ones

---

## AGENT 1: UI DESIGN ANALYSIS
**Status:** COMPLETE
**Task:** Analyze current layouts and propose specific UI redesign

### Design Analysis:

#### CURRENT STATE ANALYSIS

**CameraLiveViewer (camera_live_viewer.py)**
- Current Dimensions: 1000px width x 600px height (Line 81: `setMinimumSize(1000, 600)`)
- Current Layout: Horizontal split (Line 86: `main_layout = QHBoxLayout()`)
  - Left side (stretch=2): Image display (640x480 minimum)
  - Right side (stretch=1): Control panels stacked vertically
- Width Breakdown: Image ~667px + Controls ~333px = 1000px total
- Control Components (Right Side, Lines 108-242):
  1. LaserLEDControlPanel (embedded widget)
  2. Camera Controls GroupBox (live view, snapshot, exposure, image controls button)
  3. Image Information GroupBox (status, image info, FPS, exposure, intensity range)

**Sample3DVisualizationWindow (sample_3d_visualization_window.py)**
- Current Dimensions: 1200px width x 800px height (Line 144: `self.resize(1200, 800)`)
- Current Layout: Horizontal splitter (Line 259-303)
  - Left: Control panel 270px (tabs for Channels/Sample Control/Data)
  - Right: Viewer 930px (status bar + napari 3D viewer)
- Splitter sizes (Line 303): `splitter.setSizes([270, 930])`

**StageChamberVisualizationWindow (stage_chamber_visualization_window.py)**
- Provides reference pattern for multi-plane views
- Dual-panel layout: XZ (top-down) and XY (side) views
- Position control sliders for X, Y, Z, R axes

---

#### PROPOSED REDESIGNS

### 1. CameraLiveViewer - Vertical Layout Redesign
**Target Width:** 660px (fits on half of 1920px screen)
**Target Height:** 730px

**New Layout ASCII Diagram:**
```
┌─────────────────────────────────────────────┐  660px wide
│  Camera Live Viewer                         │
├─────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────┐ │
│ │                                         │ │  480px
│ │      Live Image Display                 │ │  tall
│ │      640 x 480                          │ │
│ │                                         │ │
│ └─────────────────────────────────────────┘ │
├─────────────────────────────────────────────┤
│ ▼ Light Source Control (collapsible)       │ │  Optional
│   [Laser/LED Panel]                        │ │  ~120px
├─────────────────────────────────────────────┤
│ Camera Controls                             │ │
│  Live: [Start][Stop]    [Snapshot]         │ │  ~80px
│  Exposure: [10000] µs = 10.0 ms            │ │
│  [Open Image Controls (full width)]        │ │
├─────────────────────────────────────────────┤
│ Image Information                           │ │  ~50px
│  Status: Live View     640x480 #1234       │ │
│  FPS: 30.0             Exp: 10.0 ms        │ │
│  Intensity: [0-65535]  [AUTO WARNING]      │ │
└─────────────────────────────────────────────┘
```

**Widget Organization Hierarchy:**
```
QVBoxLayout (main_layout)
├── QGroupBox "Live Image" (display_group)
│   └── QLabel (image_label) 640x480 min
│
├── QGroupBox "Light Source Control" (laser_led_group)
│   └── LaserLEDControlPanel (setCheckable(True), default collapsed)
│
├── QGroupBox "Camera Controls" (controls_group)
│   └── QGridLayout
│       ├── Row 0: [Label "Live:"] [Start Btn] [Stop Btn] [Snapshot Btn]
│       ├── Row 1: [Label "Exposure:"] [SpinBox] [Label "="] [ms Label] [Stretch]
│       └── Row 2: [Image Controls Btn - spanning all columns]
│
└── QGroupBox "Image Information" (info_group)
    └── QGridLayout
        ├── Row 0, Col 0-1: [Label "Status:"] [status_label]
        ├── Row 0, Col 2-3: [Label "Image:"] [img_info_label]
        ├── Row 1, Col 0-1: [Label "FPS:"] [fps_label]
        ├── Row 1, Col 2-3: [Label "Exposure:"] [actual_exposure_label]
        └── Row 2, Col 0-3: [Label "Intensity:"] [intensity_label] [auto_scale_warning]
```

**Recommended Dimensions:**
- Window: 660px W x 730px H (setMinimumSize(660, 730))
- Image area: 640px W x 480px H
- Laser/LED panel: Collapsible (0-120px when expanded)
- Camera controls: ~80px H
- Info panel: ~50px H

**Control Groupings (Top to Bottom Priority):**
1. Image Display (highest priority, always visible)
2. Camera Controls (frequently used)
3. Image Information (monitoring, always visible)
4. Laser/LED Panel (collapsible, secondary)

---

### 2. Sample3DVisualizationWindow - Tabbed Viewer Redesign
**Target Width:** 950px (fits with CameraLiveViewer on 1920px screen)
**Target Height:** 800px (unchanged)

**New Layout ASCII Diagram:**
```
┌────────────┬──────────────────────────────────────────────────┐  950px wide
│ Controls   │  Status: Ready | Memory: 0 MB | Voxels: 0        │
│  (250px)   ├──────────────────────────────────────────────────┤
│            │ ┌──────────────────────────────────────────────┐ │
│ ▼Channels  │ │  Tabs: [3D Volume] [Multi-Plane Views]       │ │
│ ▼Sample    │ │ ┌────────────────────────────────────────────┐│ │
│ ▼Data      │ │ │                                            ││ │
│            │ │ │  TAB 1: Napari 3D Viewer                  ││ │  700px
│ [Populate] │ │ │  (current implementation)                  ││ │  viewer
│ [Clear]    │ │ │  600x600 minimum                           ││ │  area
│ [Export]   │ │ │                                            ││ │
│            │ │ └────────────────────────────────────────────┘│ │
│            │ └──────────────────────────────────────────────┘ │
└────────────┴──────────────────────────────────────────────────┘
```

**Multi-Plane Views Tab Layout:**
```
┌──────────────────────────────────────────────────────────────┐
│  Tabs: [3D Volume] [Multi-Plane Views]                       │
│ ┌────────────────────────────┬───────────────────────────────┐│
│ │ XY Plane (Side View)       │ YZ Plane (End View)          ││
│ │ [Image: 350x250]           │ [Image: 300x250]             ││
│ │                            │                              ││
│ ├────────────────────────────┴───────────────────────────────┤│
│ │ XZ Plane (Top-Down View)                                  ││
│ │ [Image: 650x250]                                           ││
│ │                                                            ││
│ └────────────────────────────────────────────────────────────┘│
│  Z Position: [Slider ══════════○══════════] 19.25 mm         │
└──────────────────────────────────────────────────────────────┘
```

**Widget Organization Hierarchy:**
```
QHBoxLayout (main_layout)
├── QSplitter (horizontal)
    ├── QWidget (control_panel) 250px
    │   └── QVBoxLayout
    │       ├── QTabWidget (control tabs)
    │       │   ├── Tab "Channels" (channel controls)
    │       │   ├── Tab "Sample Control" (position/rotation)
    │       │   └── Tab "Data" (data management)
    │       └── QHBoxLayout (buttons)
    │           ├── QPushButton "Populate from Live View"
    │           ├── QPushButton "Clear Data"
    │           └── QPushButton "Export..."
    │
    └── QWidget (viewer_container) 700px
        └── QVBoxLayout
            ├── QHBoxLayout (status_layout)
            │   ├── QLabel (status_label)
            │   ├── QLabel (memory_label)
            │   └── QLabel (voxel_count_label)
            └── QTabWidget (viewer_tabs) ** NEW **
                ├── Tab "3D Volume" (tab_3d)
                │   └── napari.Viewer widget (existing)
                └── Tab "Multi-Plane Views" (tab_planes) ** NEW **
                    └── _create_multiplane_tab() widget
                        ├── QHBoxLayout (top_row)
                        │   ├── QGroupBox "XY Plane (Side View)"
                        │   │   └── QLabel (xy_image_label) 350x250
                        │   └── QGroupBox "YZ Plane (End View)"
                        │       └── QLabel (yz_image_label) 300x250
                        ├── QGroupBox "XZ Plane (Top-Down View)"
                        │   └── QLabel (xz_image_label) 650x250
                        └── QHBoxLayout (slider_layout)
                            ├── QLabel "Z Position:"
                            ├── QSlider (plane_z_slider)
                            └── QLabel (plane_z_label)
```

**Multi-Plane Tab Implementation Method:**
```python
def _create_multiplane_tab(self) -> QWidget:
    """
    Create multi-plane imaging view showing XY, YZ, XZ slices.
    Pattern based on StageChamberVisualizationWindow.
    """
    widget = QWidget()
    layout = QVBoxLayout()

    # Top row: XY (side) and YZ (end) views
    top_row = QHBoxLayout()

    # XY View (Side view)
    xy_group = QGroupBox("XY Plane (Side View)")
    xy_layout = QVBoxLayout()
    self.xy_image_label = QLabel()
    self.xy_image_label.setMinimumSize(350, 250)
    self.xy_image_label.setStyleSheet("background-color: black; border: 1px solid gray;")
    xy_layout.addWidget(self.xy_image_label)
    xy_group.setLayout(xy_layout)
    top_row.addWidget(xy_group)

    # YZ View (End view)
    yz_group = QGroupBox("YZ Plane (End View)")
    yz_layout = QVBoxLayout()
    self.yz_image_label = QLabel()
    self.yz_image_label.setMinimumSize(300, 250)
    self.yz_image_label.setStyleSheet("background-color: black; border: 1px solid gray;")
    yz_layout.addWidget(self.yz_image_label)
    yz_group.setLayout(yz_layout)
    top_row.addWidget(yz_group)

    layout.addLayout(top_row)

    # Bottom row: XZ view (top-down)
    xz_group = QGroupBox("XZ Plane (Top-Down View)")
    xz_layout = QVBoxLayout()
    self.xz_image_label = QLabel()
    self.xz_image_label.setMinimumSize(650, 250)
    self.xz_image_label.setStyleSheet("background-color: black; border: 1px solid gray;")
    xz_layout.addWidget(self.xz_image_label)
    xz_group.setLayout(xz_layout)
    layout.addWidget(xz_group)

    # Z position slider for plane selection
    slider_layout = QHBoxLayout()
    slider_layout.addWidget(QLabel("Z Position:"))
    self.plane_z_slider = QSlider(Qt.Horizontal)
    self.plane_z_slider.setRange(
        int(self.config['stage_control']['z_range_mm'][0] * 1000),
        int(self.config['stage_control']['z_range_mm'][1] * 1000)
    )
    slider_layout.addWidget(self.plane_z_slider)
    self.plane_z_label = QLabel("19.25 mm")
    slider_layout.addWidget(self.plane_z_label)
    layout.addLayout(slider_layout)

    # Connect slider to update views
    self.plane_z_slider.valueChanged.connect(self._update_multiplane_views)

    widget.setLayout(layout)
    return widget
```

**Data Source for Multi-Plane Views:**
```python
def _update_multiplane_views(self, z_value_mm: int = None):
    """
    Update multi-plane view displays by slicing voxel_storage.

    Data extraction:
    - volume = voxel_storage.get_display_volume(channel_id)
    - volume is (Z, Y, X) shaped array matching napari axes
    - XY plane: volume[z_index, :, :]
    - YZ plane: volume[:, :, x_index]
    - XZ plane: volume[:, y_index, :]
    """
    # Extract 2D slices from 3D volume at current Z position
    # Convert to QPixmap and display on QLabels
    pass
```

**Recommended Dimensions:**
- Window: 950px W x 800px H
- Control panel: 250px W (reduced from 270px)
- Viewer area: 700px W (650px usable)
- Splitter sizes: [250, 700]

---

#### SCREEN LAYOUT VALIDATION

**1920px Wide Screen Layout:**
```
┌─────────────────────┬──────────────────────────────────┐
│ CameraLiveViewer    │ Sample3DVisualizationWindow      │
│ 660px wide          │ 950px wide                       │
│                     │                                  │
│ [Live Image]        │ [Controls] [3D/Multi-Plane View] │
│                     │                                  │
│ [Controls]          │                                  │
│ [Info]              │                                  │
└─────────────────────┴──────────────────────────────────┘
     660px                    950px              = 1610px
                      Gap: 310px remaining
```

**Fits comfortably with room for:**
- Window borders and margins
- OS taskbar/menu bars
- Screen edges

---

#### KEY DESIGN DECISIONS

**CameraLiveViewer:**
1. **Vertical layout** (QVBoxLayout) allows image to take full width
2. **Collapsible Laser/LED panel** (setCheckable) saves space when not needed
3. **Grid-based controls** (QGridLayout) maximize horizontal space efficiency
4. **Compact info panel** uses 3-row grid instead of 5 stacked layouts

**Sample3DVisualizationWindow:**
1. **Narrower control panel** (250px vs 270px) reduces total width
2. **Tabbed viewer area** (QTabWidget) adds functionality without width increase
3. **Multi-plane tab** mirrors StageChamberVisualizationWindow pattern
4. **Shared voxel_storage backend** ensures both tabs show same accumulated data
5. **Z-slider navigation** allows inspection at different depths

---

#### IMPLEMENTATION NOTES

**CameraLiveViewer Changes:**
- Line 86: `main_layout = QHBoxLayout()` → `main_layout = QVBoxLayout()`
- Line 81: `setMinimumSize(1000, 600)` → `setMinimumSize(660, 730)`
- Lines 86-244: Restructure `_setup_ui()` to create vertical layout
- Wrap LaserLEDControlPanel in collapsible QGroupBox
- Convert Camera Controls to QGridLayout (3 rows x 4 columns)
- Convert Info Panel to QGridLayout (3 rows x 4 columns)

**Sample3DVisualizationWindow Changes:**
- Line 303: `splitter.setSizes([270, 930])` → `splitter.setSizes([250, 700])`
- Line ~290: Insert QTabWidget in viewer_container after status bar
- Add new method: `_create_multiplane_tab()` (~100 lines)
- Add new method: `_update_multiplane_views()` (~50 lines)
- Connect to existing voxel_storage in update timer

---

#### CONTROL GROUPINGS (Priority Order)

**CameraLiveViewer - Top to Bottom:**
1. **Image Display** (highest priority - always visible, primary focus)
2. **Camera Controls** (frequently used during live view sessions)
3. **Image Information** (monitoring, always visible for feedback)
4. **Laser/LED Panel** (secondary - collapsible to save space)

**Sample3DVisualizationWindow - Tab Organization:**
1. **Channels Tab** (most frequently adjusted during multi-channel imaging)
2. **Sample Control Tab** (positioning and rotation adjustments)
3. **Data Tab** (periodic use for populate/clear/export operations)

**Viewer Tabs:**
- **Tab 1 (3D Volume):** Primary visualization for volume rendering and rotation
- **Tab 2 (Multi-Plane):** Secondary visualization for 2D slice inspection and alignment

---

## AGENT 2: USER PERSPECTIVE EVALUATION
**Status:** COMPLETE
**Task:** Review design from user experience perspective

### User Feedback:

I have thoroughly reviewed Agent 1's complete design specifications. As a microscopy scientist using this software daily for 3D imaging experiments, here is my comprehensive evaluation:

---

## Executive Summary

**Overall Assessment:** ✅ Strong design with significant workflow improvements, but **3 critical issues** must be addressed before deployment.

**Recommendation:** Implement with modifications to address critical concerns about collapsible laser panel, information density, and multi-plane tab value proposition.

---

## 1. CameraLiveViewer Redesign Evaluation (660×730 Vertical Layout)

### ✅ What Works Well

**1.1 Vertical Layout Philosophy (EXCELLENT)**
- **Image-first approach:** Putting the live camera feed at the top is exactly right. During live imaging, I spend 80% of my time watching the feed, not adjusting controls.
- **Full-width image:** The 640px image getting full 660px width (with 10px margins) is perfect. No wasted horizontal space.
- **Natural scanning pattern:** Eyes go top-to-bottom naturally. Image → Controls → Info matches my mental workflow during imaging sessions.
- **Compact footprint:** 660px width means I can comfortably fit both windows side-by-side. This solves the main problem with the current 1000px width.

**1.2 Dimension Choices (GOOD)**
- **660×730 total size:** Well-calculated. Fits the 640×480 image perfectly with reasonable space for controls.
- **Screen real estate:** 660px is approximately 1/3 of 1920px screen, leaving plenty of room for the 3D viewer.

**1.3 Information Panel (GOOD)**
- **Always visible:** Keeping Status, FPS, Exposure, and Intensity range constantly visible is crucial. I check these dozens of times during a session.
- **Grid layout:** Compact 3-row grid makes efficient use of space while keeping info readable.

### 🔴 Critical Issues (MUST FIX)

**2.1 CRITICAL: Collapsible Laser/LED Panel is PROBLEMATIC**

**Problem:**
The decision to make the Laser/LED control panel collapsible with `setCheckable(True)` and default collapsed is a **major workflow disruption**.

**Real-world usage:**
- I adjust laser/LED intensity **constantly** during live imaging - typically 5-10 times per session
- Common workflow: Start live view → adjust illumination while watching sample → check for photobleaching → adjust again → repeat
- Making this collapsible adds unnecessary clicks and cognitive load
- If it defaults to collapsed, I'll have to expand it every single time I open the window

**Impact: CRITICAL**
- Adds 1 extra click per adjustment (expand panel)
- Breaks focus: I'm watching live feed, need to adjust laser, must look away to find collapsed panel
- Height concerns are overblown: The panel is only ~120px when expanded - this is acceptable

**Recommendation:**
```
❌ REMOVE collapsible behavior entirely
✅ Keep Laser/LED panel always expanded and visible
✅ Position: Between image and camera controls (priority order: Image > Laser > Camera > Info)
✅ Revised height budget: 660×850 total (adds 120px for always-visible laser panel)
```

**Alternative if height is truly constrained:**
If 850px is absolutely too tall, consider:
- Option A: Reduce laser panel to ~80px with more compact layout (2 columns instead of stacked)
- Option B: Make only the advanced laser controls collapsible, keep intensity sliders always visible
- Option C: Increase window height to 800px (still reasonable for 1080p screen with taskbar)

**Priority: CRITICAL - Must address before deployment**

---

**2.2 CRITICAL: Camera Controls Grid Layout Needs Refinement**

**Problem:**
The proposed grid layout in the ASCII diagram is unclear about button sizes and spacing:
```
│ Camera Controls                             │ │
│  Live: [Start][Stop]    [Snapshot]         │ │  ~80px
│  Exposure: [10000] µs = 10.0 ms            │ │
│  [Open Image Controls (full width)]        │ │
```

**Concerns:**
- **Button accessibility:** During time-sensitive experiments, I need to hit Start/Stop quickly. Buttons must be adequately sized (not tiny).
- **Exposure adjustment:** The exposure spinbox is my second most-used control. Must be easy to click and adjust.
- **Snapshot button:** Less frequently used, but must be clearly visible when needed.

**Recommendation:**
```
Specify minimum button dimensions in implementation:
- Start/Stop buttons: 80px width × 30px height minimum
- Snapshot button: 100px width × 30px height
- Exposure spinbox: 100px width × 30px height
- Grid spacing: 5px between elements
- Total row height: 35-40px per row for easy clicking
```

**Priority: CRITICAL - Buttons too small = workflow disaster**

---

**2.3 IMPORTANT: Exposure Display Redundancy**

**Issue:**
The design shows exposure in TWO places:
1. Camera Controls: "Exposure: [10000] µs = 10.0 ms"
2. Image Information: "Exp: 10.0 ms"

**Problem:**
- Redundant information takes up scarce vertical space
- Creates confusion: Which one is "true"? What if they differ due to auto-exposure?
- The distinction between "target exposure" (controls) and "actual exposure" (info) is unclear

**Recommendation:**
```
✅ Keep in Camera Controls: Exposure spinbox + units (for setting)
✅ Keep in Image Information: "Actual Exp: 10.2 ms" (for monitoring)
✅ Add label clarity: "Target: 10.0 ms" vs "Actual: 10.2 ms"
✅ Saves ~5-10px of vertical space
```

**Priority: IMPORTANT - Affects clarity and space efficiency**

---

### ⚠️ Potential Problems

**3.1 Image Controls Button Position**

**Observation:**
The "Open Image Controls" button spans full width in row 2 of camera controls.

**Question:**
How often do users access Image Controls? If it's infrequent (once per session), should it be:
- Option A: Full-width button as shown (easy to find, takes space)
- Option B: Smaller button at end of row 1 (saves space, slightly harder to find)
- Option C: Menu button or right-click context menu (minimal space, advanced user pattern)

**User perspective:**
I access Image Controls ~2 times per session (beginning and end). A full-width button feels prominent for that frequency.

**Recommendation:**
```
🟡 Consider moving to smaller button in row 1: [Start][Stop][Snapshot][Image...]
🟡 Or: Add to menu bar: Camera → Image Controls
🟡 Saves vertical space for more critical controls
```

**Priority: MINOR - Nice-to-have optimization**

---

**3.2 Laser/LED Panel Collapse State Persistence**

**If collapsible behavior is kept (against my recommendation):**

**Question:**
Does the collapsed/expanded state persist between sessions?

**User expectation:**
If I expand the laser panel, it should STAY expanded when I reopen the window. Having to re-expand every session is infuriating.

**Recommendation:**
```
✅ Save collapse state to config file (QSettings)
✅ Restore state on window open
✅ Default to EXPANDED (not collapsed) for new users
```

**Priority: IMPORTANT if collapsible, N/A if always-visible**

---

### 💡 Suggestions for Enhancement

**4.1 Window Height Flexibility**

**Observation:**
The 730px height seems carefully calculated but tight with collapsible laser panel.

**Suggestion:**
```
💡 Allow window to be slightly taller (800-850px) for better breathing room
💡 Most 1080p monitors have 1000+ usable vertical pixels after taskbar
💡 Extra space could accommodate always-visible laser panel + more generous button sizes
💡 Consider making minimum 730px, preferred 850px
```

**Priority: ENHANCEMENT**

---

**4.2 Visual Grouping with Color/Borders**

**Observation:**
The ASCII diagram shows GroupBoxes which is good, but user testing often reveals that clearer visual separation helps.

**Suggestion:**
```
💡 Use subtle background colors for different sections:
  - Image area: Black background (already present)
  - Camera Controls: Light gray background (#F0F0F0)
  - Laser Controls: Light blue background (#E6F2FF)
  - Info Panel: White background
💡 Helps eyes quickly find the right section during rapid adjustments
```

**Priority: ENHANCEMENT**

---

**4.3 Keyboard Shortcuts**

**Observation:**
No mention of keyboard shortcuts in the design.

**Suggestion:**
```
💡 Add keyboard shortcuts for critical functions:
  - Space bar: Start/Stop live view (toggle)
  - S: Snapshot
  - E: Focus exposure spinbox
  - L: Toggle laser panel (if collapsible)
💡 Significantly speeds up workflow during time-sensitive experiments
💡 Add tooltips showing shortcuts
```

**Priority: ENHANCEMENT**

---

## 2. Sample3DVisualizationWindow Redesign Evaluation (950×800 with Multi-Plane Tab)

### ✅ What Works Well

**5.1 Width Reduction (EXCELLENT)**

- **950px vs 1200px:** Excellent reduction of 250px while maintaining usability.
- **Control panel 250px:** Slightly narrower than 270px is fine. Most controls are spinboxes and sliders that work well at 250px.
- **Fits the goal:** 660 + 950 = 1610px total, leaves 310px margin on 1920px screen. Perfect.

**5.2 Existing 3D Volume Tab (EXCELLENT)**

- **Keep napari viewer:** No changes to 3D viewer functionality is correct. It works well.
- **Tab organization:** Putting 3D viewer in a tab allows expansion without disrupting existing workflow.

### 🔴 Critical Issues (MUST FIX)

**6.1 CRITICAL: Multi-Plane Tab Value Proposition is UNCLEAR**

**The fundamental question:**
As a user, WHEN and WHY would I use the Multi-Plane Views tab instead of the 3D Volume tab?

**Agent 1's proposal:**
- Tab 1: 3D Volume (napari viewer)
- Tab 2: Multi-Plane Views (XY, YZ, XZ slices with Z slider)

**User workflow analysis:**

**Scenario A: During data acquisition**
- While scanning, I'm watching the **CameraLiveViewer** (live feed)
- The 3D viewer is typically not in focus - I'm accumulating data
- Do I need to see multi-plane views during scanning? **Rarely**

**Scenario B: After data acquisition - inspection**
- I want to inspect the 3D volume I just acquired
- **Question:** Can I inspect planes in the napari 3D viewer already?
- **Answer:** YES - Napari has built-in plane slicing tools

**Scenario C: Checking alignment/focus**
- I want to verify XY, YZ, XZ alignment before starting a long scan
- **This is the ONLY scenario where multi-plane tab adds clear value**
- But: How often do I do this? Maybe 1-2 times per day?

**Core Problem:**
The multi-plane tab **duplicates napari's built-in functionality** but in a more limited form:
- Napari already has plane slicing tools
- Napari can show orthogonal views
- Napari has more powerful visualization controls

**User confusion:**
"Why are there two ways to view planes? Which one should I use? What's the difference?"

**Recommendation:**

```
🔴 RECONSIDER the multi-plane tab entirely

Option 1 (RECOMMENDED): Add multi-plane as a DOCKED WIDGET in napari
- Use napari's plugin system to add orthogonal views as docked widget
- Shows XY, YZ, XZ in sidebar of 3D viewer tab
- User can toggle visibility as needed
- No tab switching required
- Leverages napari's existing infrastructure

Option 2: Make multi-plane a SEPARATE WINDOW (not a tab)
- Add menu item: View → Multi-Plane Inspector
- Opens separate small window (e.g., 700×600) with XY/YZ/XZ views
- Can be positioned anywhere on screen
- Only open when specifically needed
- Doesn't clutter the main 3D viewer

Option 3: Make multi-plane the PRIMARY view, 3D the secondary tab
- If multi-plane inspection is actually more common than 3D rotation
- Tab 1: Multi-Plane Views (default)
- Tab 2: 3D Volume (advanced use)
- But: I doubt this matches real usage patterns

Option 4 (COMPROMISE): Keep tab but add CLEAR DOCUMENTATION
- Add tooltip: "Multi-Plane Views: Quick XY/YZ/XZ inspection for alignment checking"
- Add label in tab: "Use this tab for alignment verification before scanning"
- Make the use case explicit so users understand when to use it
```

**Priority: CRITICAL - Need clear rationale or redesign**

---

**6.2 IMPORTANT: Multi-Plane Navigation is Limited**

**Problem:**
The proposed design has only a **single Z slider** for navigation.

**From ASCII diagram:**
```
│  Z Position: [Slider ══════════○══════════] 19.25 mm         │
```

**Issue:**
If I want to explore a specific region in 3D space, I need to:
1. Adjust Z slider → see XY plane at that Z
2. But: The XZ plane shows a fixed Y, YZ plane shows a fixed X
3. I cannot adjust which X or Y slice to view

**Real-world scenario:**
"I see something interesting at X=15mm, Z=20mm. I want to view the YZ plane at X=15mm."
- Current design: I can adjust Z, but X is fixed to some default value
- I might not be looking at X=15mm at all

**Recommendation:**
```
✅ Add THREE sliders (as shown in your own implementation code!):
  - X Position slider: Controls which YZ plane to display
  - Y Position slider: Controls which XZ plane to display
  - Z Position slider: Controls which XY plane to display

✅ This matches the StageChamberVisualizationWindow reference pattern
✅ Allows full 3D navigation through the volume
✅ Each slider updates ONE view, making the interaction intuitive
```

**Note:** Agent 1's implementation code at lines 260-274 actually shows a single plane_z_slider, confirming the limitation. Agent 3's implementation at lines 541-556 mentions sliders for each plane, which is correct.

**Priority: IMPORTANT - Significantly affects usability**

---

### ⚠️ Potential Problems

**7.1 Multi-Plane Image Sizes Feel Arbitrary**

**Proposed sizes:**
```
- XY Plane: 350×250
- YZ Plane: 300×250
- XZ Plane: 650×250
```

**Questions:**
1. Why is XZ plane wider (650px) than XY (350px)?
   - Is this because X dimension is typically longer?
   - Or is it just space-filling?
2. Why are Y dimensions all 250px?
   - Does this match actual voxel aspect ratios?
   - Or is it arbitrary?

**User concern:**
If the image sizes don't match the actual aspect ratios of the volume, the views will look distorted or misleading.

**Recommendation:**
```
✅ Calculate aspect ratios from actual voxel dimensions:
  - volume.shape = (Z, Y, X) in voxels
  - voxel_size_mm = [z_size, y_size, x_size] from config
  - Physical dimensions: [Z*z_size, Y*y_size, X*x_size]
  - Scale images proportionally to fit available space

✅ Example:
  - If X is 40mm, Y is 30mm, Z is 20mm (typical sample chamber)
  - XY plane aspect: 40:30 = 4:3 → Display as 400×300 or 350×263
  - YZ plane aspect: 30:20 = 3:2 → Display as 300×200 or 300×225
  - XZ plane aspect: 40:20 = 2:1 → Display as 650×325

✅ Prevents distorted views that mislead about sample geometry
```

**Priority: IMPORTANT - Affects data interpretation**

---

**7.2 Channel Selection for Multi-Plane Views**

**Question:**
If I have 3 channels (e.g., red, green, blue fluorescence), which channel is displayed in the multi-plane views?

**Options:**
1. Show currently selected channel from Channels tab
2. Show merged/composite view of all visible channels
3. Allow separate channel selection in multi-plane tab

**User expectation:**
The multi-plane views should **match what I see in the Channels tab**. If I've turned off the green channel in the 3D viewer, it should also be off in multi-plane views.

**Recommendation:**
```
✅ Multi-plane views use the SAME channel visibility and colormap settings as 3D viewer
✅ Respect the channel controls in the Channels tab
✅ Update multi-plane views whenever channel settings change
✅ Add label: "Showing: [Channel Names]" in multi-plane tab for clarity
```

**Priority: IMPORTANT - Consistency is critical**

---

**7.3 Tab Switching During Workflow**

**Observation:**
The design assumes users will switch between "3D Volume" and "Multi-Plane Views" tabs.

**Question:**
How often will users actually switch tabs? What triggers a switch?

**User perspective:**
- **During scanning:** I'm watching CameraLiveViewer, not the 3D window
- **After scanning:** I might check 3D volume first (Tab 1)
- **If alignment looks weird:** Switch to Multi-Plane (Tab 2) to inspect planes
- **After inspection:** Switch back to 3D (Tab 1)

**Potential friction:**
- Tab switching requires a click and interrupts flow
- If I'm rotating the 3D view and want to check a specific plane, switching tabs loses my current 3D orientation

**Recommendation:**
```
💡 Consider allowing BOTH views simultaneously:
  - Split viewer area: 3D on left (450px), Multi-Plane on right (250px)
  - Or: Use napari docked widget (as suggested earlier)
  - Avoids tab switching altogether

🟡 If keeping tabs, consider adding keyboard shortcut:
  - Ctrl+1: Switch to 3D Volume tab
  - Ctrl+2: Switch to Multi-Plane Views tab
  - Speeds up navigation
```

**Priority: MINOR - Workflow optimization**

---

### 💡 Suggestions for Enhancement

**8.1 Cross-hair Overlay on Multi-Plane Views**

**Idea:**
When viewing XY plane at Z=20mm, show cross-hairs indicating current X and Y slice positions.

**Example:**
```
XY Plane (Z=20mm)
┌─────────────────┐
│        │        │  ← Vertical line at current X position (for YZ plane)
│────────┼────────│  ← Horizontal line at current Y position (for XZ plane)
│        │        │
└─────────────────┘
```

**Benefit:**
- Helps understand which slices are shown in other views
- Makes the 3D mental model clearer
- Common pattern in medical imaging software (e.g., 3D Slicer)

**Priority: ENHANCEMENT**

---

**8.2 Click-to-Navigate on Plane Views**

**Idea:**
Click on XY plane image to move to that X, Y position. Updates YZ and XZ planes accordingly.

**Benefit:**
- Faster than dragging sliders
- More intuitive than slider-based navigation
- Common pattern in microscopy software

**Priority: ENHANCEMENT - But requires significant implementation effort**

---

**8.3 Link Multi-Plane Sliders to Stage Position**

**Idea:**
If stage is at X=15mm, Y=20mm, Z=25mm, multi-plane views default to showing planes at that position.

**Benefit:**
- Multi-plane views show "where the stage currently is"
- Useful for alignment: "Is the sample actually where I think it is?"
- Matches user mental model: "Show me what's at the current position"

**Priority: ENHANCEMENT - Useful for alignment workflow**

---

## 3. Combined Layout Evaluation (Both Windows Simultaneously)

### ✅ What Works Well

**9.1 Total Width Budget (EXCELLENT)**

**Dimensions:**
- CameraLiveViewer: 660px
- Sample3DVisualizationWindow: 950px
- Total: 1610px
- Screen: 1920px
- Remaining: 310px for borders, margins, taskbar

**Assessment:**
Perfect fit. The 310px margin is generous enough for:
- Window borders and shadows (10-20px per window)
- Space between windows (20-50px)
- Screen edges (10-20px per side)
- Leaves ~200px buffer for comfort

**9.2 Independent Window Management (GOOD)**

- Both windows can be resized independently
- User can adjust positioning as needed
- Can minimize one window if more space needed for the other
- Flexible workflow support

### 🔴 Critical Issues (MUST FIX)

**10.1 CRITICAL: Revised CameraLiveViewer Height (850px) May Exceed Screen**

**Problem:**
- Original proposal: 730px height (with collapsible laser panel)
- My recommendation: 850px height (with always-visible laser panel)
- Screen height: 1080px - taskbar (40-50px) = ~1030px usable

**Assessment:**
- 850px + title bar (30px) + margins (20px) = 900px total
- Fits comfortably in 1030px usable screen height
- But: Less margin than width (310px vs 130px)

**Recommendation:**
```
✅ Test on actual 1920×1080 monitor with OS chrome
✅ Consider making laser panel COMPACT (80px) instead of full size (120px):
  - 2-column layout instead of stacked controls
  - Reduces total height to 810px (more comfortable)
✅ Or: Accept 850px height with understanding that vertical space is tighter
✅ Ensure window is moveable if it doesn't fit (unlikely but possible with thick taskbars)
```

**Priority: CRITICAL - Must validate on target hardware**

---

### ⚠️ Potential Problems

**10.2 Window Positioning on Startup**

**Question:**
When both windows open, where are they positioned?

**Options:**
1. **Manual positioning:** User drags windows into side-by-side arrangement
   - Annoying to do every time
   - May forget optimal layout
2. **Automatic side-by-side:** Application positions them automatically
   - Left: CameraLiveViewer at (0, 0)
   - Right: Sample3DVisualizationWindow at (670, 0)
   - Much better user experience
3. **Saved positions:** Remember last position from previous session
   - Most flexible
   - But: May break if moving between monitors of different sizes

**Recommendation:**
```
✅ PREFERRED: Automatic side-by-side on first launch
  - CameraLiveViewer: Position (10, 40) - left side, below menu
  - Sample3DVisualizationWindow: Position (680, 40) - right side, below menu
  - 10px gap between windows

✅ Save positions to QSettings on close
✅ Restore saved positions on reopen
✅ Detect if saved positions are off-screen (different monitor) → reset to default
✅ Add menu item: Window → Reset Window Positions (for recovery)
```

**Priority: IMPORTANT - Significantly affects user experience**

---

**10.3 Focus Management Between Windows**

**Question:**
When I'm working in CameraLiveViewer and need to switch to 3D viewer, how do I switch focus?

**Current behavior (likely):**
- Click on the window I want to focus
- Standard OS window management

**Potential enhancement:**
```
💡 Add keyboard shortcut to switch focus:
  - F1: Focus CameraLiveViewer
  - F2: Focus Sample3DVisualizationWindow
  - F3: Focus (other windows if any)

💡 Helps during rapid workflows:
  - Adjusting live view → Switch to 3D → Inspect volume → Back to live view
  - Faster than clicking with mouse
```

**Priority: ENHANCEMENT**

---

## 4. Typical Workflow Analysis

Let me walk through 3 common workflows to identify friction points:

### Workflow 1: Initial Setup and Alignment

**Steps:**
1. Open CameraLiveViewer (660×730, left side)
2. Start live view
3. Adjust exposure to see sample
4. Adjust laser intensity while watching live feed
   - **FRICTION if laser panel is collapsible:** Must expand panel first
5. Move stage to find region of interest (different window, not shown)
6. Verify focus by watching live feed
7. Open Sample3DVisualizationWindow (950×800, right side)
8. Position windows side-by-side
   - **FRICTION if manual positioning:** Takes time, trial and error
9. Ready to scan

**Pain Points:**
- Collapsible laser panel adds extra step (expand)
- Manual window positioning is tedious

**With Fixes:**
- Always-visible laser panel: Smooth adjustment
- Auto side-by-side positioning: Instant setup

**Assessment: ✅ Good workflow with fixes applied**

---

### Workflow 2: 3D Volume Acquisition

**Steps:**
1. Both windows open side-by-side (from Workflow 1)
2. Configure scan parameters in 3D viewer (Z range, step size)
3. Click "Populate from Live View" in Sample3DVisualizationWindow
4. Watch CameraLiveViewer during scan
   - See live XY plane as Z stage moves
   - Monitor FPS, intensity in info panel
5. 3D viewer accumulates voxel data in background
6. After scan completes, switch focus to Sample3DVisualizationWindow
   - **Tab 1 (3D Volume):** Rotate volume to inspect overall structure
   - **Tab 2 (Multi-Plane):** Check specific planes for alignment
   - **FRICTION:** Tab switching interrupts flow
   - **QUESTION:** Is multi-plane tab really useful here? Napari already shows planes

**Pain Points:**
- Multi-plane tab value is unclear (duplicates napari functionality)
- Tab switching adds clicks

**With Fixes:**
- If multi-plane is removed: Use napari's built-in plane tools instead
- If multi-plane is kept as docked widget: Both views available simultaneously

**Assessment: ⚠️ Multi-plane tab needs better integration or removal**

---

### Workflow 3: Time-Series Acquisition (Repeated Scans)

**Steps:**
1. Perform initial scan (Workflow 2)
2. Adjust experimental conditions (e.g., add reagent)
3. Return to CameraLiveViewer
4. Check that sample is still in focus
   - **CRITICAL:** Exposure and laser controls must be quickly accessible
5. Adjust laser intensity (sample may be brighter/dimmer after reagent)
   - **FRICTION if collapsible:** Must expand laser panel again
6. Start second scan
7. Repeat steps 2-6 multiple times

**Pain Points:**
- Rapid adjustment of laser intensity is critical for time-series
- Any extra clicks or hidden controls slow down time-sensitive experiment

**With Fixes:**
- Always-visible laser panel: Can adjust in <1 second
- Large, accessible exposure controls: Quick focus verification

**Assessment: ✅ Excellent workflow with always-visible laser controls**

---

## 5. Edge Cases and Concerns

### Edge Case 1: Laptop with 1366×768 Screen

**Problem:**
Some users may have smaller screens.

**Impact:**
- 1610px width does NOT fit on 1366px screen
- 730px height barely fits on 768px screen (minus taskbar)

**Recommendation:**
```
⚠️ Document minimum screen size: 1920×1080
⚠️ Add graceful degradation:
  - If screen width < 1920px, show windows in tabbed container instead of side-by-side
  - Or: Reduce CameraLiveViewer to 600px width (slightly crop controls)
⚠️ Or: Accept that software requires 1920×1080 minimum (reasonable for microscopy workstations)
```

**Priority: MINOR - Most microscopy workstations have 1920×1080 or larger**

---

### Edge Case 2: User Prefers Original Layout

**Problem:**
Some users may prefer the current horizontal layout (image left, controls right).

**Recommendation:**
```
💡 Add preference setting:
  - Settings → Layout → CameraLiveViewer Layout: [Vertical (compact)] [Horizontal (original)]
  - Allows users to choose based on personal preference
  - Requires maintaining both layout options (more code complexity)

OR:

✅ SIMPLER: Keep original files untouched (Agent 3's approach)
  - Users can launch original CameraLiveViewer if preferred
  - New users get compact layout by default
  - Legacy support for those who like old layout
```

**Priority: ENHANCEMENT - Nice for user choice, but not critical**

---

### Edge Case 3: Very Large Volumes (Memory/Performance)

**Problem:**
Multi-plane tab extracts slices from large volumes in real-time.

**Impact:**
- Large volumes (e.g., 2000×2000×500 voxels) may be slow to slice and render
- Multiple QPixmap conversions per slider update could lag

**Recommendation:**
```
✅ Implement caching:
  - Cache last 5-10 slices per plane
  - Only recompute QPixmap if slice index changes
✅ Use downsampling for large volumes:
  - If volume > 1000×1000×1000, downsample by 2× for display
  - Keeps multi-plane views responsive
✅ Add loading indicator:
  - Show "Loading..." text while slice is being computed
  - Prevents confusion during lag
```

**Priority: IMPORTANT if multi-plane tab is kept**

---

## 6. Priority Assessment Summary

### CRITICAL (Must Fix Before Deployment)

1. **Remove collapsible laser/LED panel** → Always visible
   - Adjust height to 850px or make panel more compact
2. **Clarify multi-plane tab value proposition** → Redesign or remove
   - Consider napari docked widget or separate window instead
3. **Validate window dimensions on actual 1920×1080 hardware**
   - Ensure both windows fit comfortably with OS chrome

### IMPORTANT (Should Address Before Release)

4. **Add X, Y, Z sliders to multi-plane tab** → Full 3D navigation (if tab is kept)
5. **Specify minimum button dimensions** → 80×30px for critical controls
6. **Calculate multi-plane image aspect ratios from voxel dimensions** → No distortion
7. **Implement automatic side-by-side window positioning** → Better UX
8. **Ensure multi-plane channels match 3D viewer settings** → Consistency
9. **Save/restore collapsible panel state** → Persistence (if collapsible is kept)

### MINOR (Can Address Later)

10. **Clarify exposure display redundancy** → Target vs Actual labels
11. **Reconsider Image Controls button size** → Possibly make smaller
12. **Add keyboard shortcuts for Start/Stop/Snapshot** → Power user feature
13. **Document minimum screen size requirement** → 1920×1080

### ENHANCEMENTS (Future Consideration)

14. **Allow 3D and multi-plane views simultaneously** → Split or docked widget
15. **Add cross-hair overlay on multi-plane views** → Better 3D understanding
16. **Implement click-to-navigate on plane views** → Faster navigation
17. **Link multi-plane sliders to current stage position** → Better alignment workflow
18. **Add subtle background colors for visual grouping** → Easier section identification
19. **Add focus-switching keyboard shortcuts (F1/F2)** → Faster window switching
20. **Add layout preference setting** → User choice between vertical and horizontal

---

## 7. Overall Recommendations

### For CameraLiveViewer:

✅ **APPROVE vertical layout concept** - Excellent design philosophy
✅ **APPROVE 660px width** - Perfect for side-by-side use
🔴 **REJECT collapsible laser panel** - Keep always visible, adjust height to 850px
🔴 **REQUIRE button dimension specs** - Ensure critical controls are adequately sized
✅ **APPROVE control grouping** - Logical top-to-bottom priority

**Revised Specs:**
- Target: 660×850px (not 660×730)
- Laser panel: Always visible, ~100px height, compact 2-column layout
- Button minimums: 80×30px for Start/Stop/Snapshot
- Otherwise: Implement as Agent 1 specified

---

### For Sample3DVisualizationWindow:

✅ **APPROVE width reduction** - 950px is perfect
✅ **APPROVE keeping napari 3D viewer unchanged** - Maintains proven functionality
🔴 **RECONSIDER multi-plane tab approach** - Value proposition is unclear

**Recommendation (in priority order):**
1. **Option A (BEST):** Add multi-plane as napari docked widget, not a tab
2. **Option B:** Make multi-plane a separate window (View → Multi-Plane Inspector)
3. **Option C:** Keep tab but add 3 sliders (X, Y, Z) and clear documentation
4. **Option D:** Remove multi-plane feature entirely, rely on napari's built-in tools

**If keeping multi-plane tab:**
- Add X, Y, Z sliders (not just Z)
- Calculate aspect ratios from voxel dimensions
- Match channel settings from main viewer
- Add caching for performance
- Add clear tooltips explaining when to use it

---

## 8. Final Verdict

**CameraLiveViewer Redesign: ✅ APPROVED with modifications**
- Core concept is excellent
- Requires fixes to laser panel and button sizing
- Will significantly improve workflow efficiency

**Sample3DVisualizationWindow Redesign: ⚠️ CONDITIONALLY APPROVED**
- Width reduction is great
- Multi-plane tab needs fundamental redesign or removal
- Current proposal duplicates napari functionality without clear advantage

**Combined Layout: ✅ APPROVED**
- Excellent screen space management
- Will enable true side-by-side workflow
- Minor enhancements to window positioning recommended

---

## 9. User Confidence Level

As a scientist using this software daily:

**Would I be happy with this redesign?**

**CameraLiveViewer:** ✅ YES, if laser panel is always visible
- The vertical layout is a significant improvement
- Will make live imaging much more efficient
- Image-first approach matches my mental workflow perfectly

**Sample3DVisualizationWindow:** 🟡 MAYBE, depends on multi-plane tab resolution
- Width reduction is fantastic
- But I'm not convinced I'd use the multi-plane tab
- Would prefer enhancements to existing napari viewer instead

**Overall:** 👍 **Strong recommendation to implement with noted critical fixes**

The redesign solves the core problem (fitting both windows on screen) and improves workflow efficiency. The critical issues are fixable and should be addressed before deployment.

---

**Evaluation completed:** 2025-11-24
**Evaluator:** Agent 2 (User Experience Evaluation Agent)
**Next steps:** Address critical issues, then proceed to implementation phase

---

## AGENT 3: IMPLEMENTATION PLAN
**Status:** COMPLETE - Test Files Created
**Task:** Create implementation plan and test files

### IMPLEMENTATION COMPLETE

I have created comprehensive implementation plans and test file templates based on Agent 1's design specifications. All files are ready in `/home/msnelson/LSControl/Flamingo_Control/test_gui_redesign/`.

---

### Code Analysis Completed

**CameraLiveViewer Analysis:**
- File: `/home/msnelson/LSControl/Flamingo_Control/src/py2flamingo/views/camera_live_viewer.py`
- Current: `QHBoxLayout` with image left (640x480 min), controls right
- Key method to override: `_setup_ui()` (line 83-244)
- All widgets: image_label, start_btn, stop_btn, exposure_spinbox, laser_led_panel, etc.
- Signal connections in `_connect_signals()` (line 246-251)
- ✅ Ready for vertical layout implementation per Agent 1 specs

**Sample3DVisualizationWindow Analysis:**
- File: `/home/msnelson/LSControl/Flamingo_Control/src/py2flamingo/views/sample_3d_visualization_window.py`
- Current tabs: Channels, Sample Control, Data
- Tab creation in `_create_control_panel()` (line 306-345)
- Uses napari viewer for 3D visualization
- Voxel storage: `DualResolutionVoxelStorage` with `get_display_volume(channel_id)`
- ✅ Ready for multi-plane tab addition per Agent 1 specs

**StageChamberVisualizationWindow Reference:**
- Provides dual-panel pattern: XZ (top-down) and XY (side) views
- Click-to-position interaction
- Real-time position updates with sliders
- ✅ Pattern applied to multi-plane implementation

---

### Test Files Created

#### 1. test_camera_live_viewer.py
**Location:** `/home/msnelson/LSControl/Flamingo_Control/test_gui_redesign/test_camera_live_viewer.py`

**Purpose:** Test version of CameraLiveViewer with vertical layout per Agent 1 specifications

**Key Features:**
- Inherits from original CameraLiveViewer
- Overrides `_setup_ui()` to call `test_setup_ui()`
- Implements vertical layout (QVBoxLayout)
- Target dimensions: 660x730 (per Agent 1)
- All original functionality preserved

**Structure:**
```python
class TestCameraLiveViewer(CameraLiveViewer):
    def __init__(self, camera_controller, laser_led_controller, image_controls_window, parent):
        super().__init__(...)  # Calls our overridden _setup_ui
        self.setMinimumSize(660, 730)  # Agent 1 specs

    def _setup_ui(self) -> None:
        self.test_setup_ui()  # Redirect to test version

    def test_setup_ui(self) -> None:
        # QVBoxLayout implementation
        # - Image display on top (full width)
        # - Controls below (horizontal arrangement)
        #   * Camera controls (Start/Stop/Snapshot, Exposure)
        #   * Laser/LED panel
        #   * Image information (Status, FPS, Intensity)
```

**Implementation Status:**
- ✅ Basic structure and inheritance
- ✅ Widget creation (all original widgets)
- ✅ Placeholder vertical layout
- ⏳ Awaiting Agent 2 feedback for refinements
- ⏳ Final layout per Agent 1's detailed specs (collapsible laser panel, grid layouts)

#### 2. test_sample_3d_visualization_window.py
**Location:** `/home/msnelson/LSControl/Flamingo_Control/test_gui_redesign/test_sample_3d_visualization_window.py`

**Purpose:** Extended Sample3DVisualizationWindow with multi-plane imaging tab

**Key Features:**
- Inherits from original Sample3DVisualizationWindow
- Adds 4th tab: "Multi-Plane Views"
- Shows XY, YZ, XZ orthogonal planes
- Synchronized slice selection
- Extracts data from voxel_storage

**Structure:**
```python
class TestSample3DVisualizationWindow(Sample3DVisualizationWindow):
    def __init__(self, movement_controller, camera_controller, laser_led_controller, parent):
        # Initialize plane viewer state
        self.plane_image_labels = {}
        self.plane_slice_sliders = {}
        self.current_slice_positions = {'x': 0, 'y': 0, 'z': 0}

        super().__init__(...)
        self.test_setup_multiplane_viewers()

    def _create_control_panel(self) -> QWidget:
        # Call parent for base tabs
        # Add multi-plane tab
        multiplane_tab = self.test_create_multiplane_tab()
        tabs.addTab(multiplane_tab, "Multi-Plane")

    def test_create_multiplane_tab(self) -> QWidget:
        # Create UI with three plane views
        # - XY Plane (top view) with Z slice slider
        # - YZ Plane (side view) with X slice slider
        # - XZ Plane (front view) with Y slice slider
        # Each plane: QLabel for image + QSlider for position

    def test_update_plane_view(self, plane: str, slice_idx: int) -> None:
        # Extract slice from volume
        volume = self.voxel_storage.get_display_volume(channel_id)
        if plane == 'xy': slice_data = volume[slice_idx, :, :]
        if plane == 'yz': slice_data = volume[:, :, slice_idx]
        if plane == 'xz': slice_data = volume[:, slice_idx, :]
        # Convert to QPixmap with colormap/contrast
        # Display on QLabel

    def test_sync_plane_positions(self, x: int, y: int, z: int) -> None:
        # Update all three sliders
        # Update all three plane views
        # Synchronized navigation
```

**Implementation Status:**
- ✅ Basic structure and inheritance
- ✅ Multi-plane tab UI layout
- ✅ Slice extraction logic
- ✅ QPixmap conversion with colormap
- ✅ Slider synchronization
- ⏳ Testing with real volumetric data
- ⏳ Click-to-navigate (optional enhancement)

#### 3. test_integration_demo.py
**Location:** `/home/msnelson/LSControl/Flamingo_Control/test_gui_redesign/test_integration_demo.py`

**Purpose:** Demonstration script showing both windows side-by-side

**Key Features:**
- Mock controllers for testing without hardware
- Automatic window positioning
- Dimension validation against Agent 1 specs
- Screen layout verification

**Mock Controllers Provided:**
- `MockCameraController` - Simulates camera operations
- `MockMovementController` - Provides mock position data
- `MockLaserLEDController` - Basic laser/LED simulation

**Validation Checks:**
- CameraLiveViewer: 660x730 target
- Sample3DVisualizationWindow: 950x800 target
- Total width: ≤1920px screen
- Side-by-side positioning

**Usage:**
```bash
cd /home/msnelson/LSControl/Flamingo_Control
python test_gui_redesign/test_integration_demo.py
```

#### 4. README.md
**Location:** `/home/msnelson/LSControl/Flamingo_Control/test_gui_redesign/README.md`

**Contents:**
- Overview of redesign goals
- Agent 1 design specifications summary
- File descriptions and usage
- Implementation approach (inheritance-based)
- Testing strategy (4 phases)
- Technical details and code structure
- Status tracking

---

### Implementation Approach: Inheritance-Based Testing

**Core Principle:** DO NOT modify original files

**Pattern:**
```python
# Original file remains unchanged
class CameraLiveViewer(QWidget):
    def _setup_ui(self): ...

# Test file inherits and overrides
class TestCameraLiveViewer(CameraLiveViewer):
    def _setup_ui(self):
        self.test_setup_ui()  # Call our version

    def test_setup_ui(self):
        # New layout implementation
```

**Benefits:**
- ✅ Original code untouched and functional
- ✅ Easy to compare old vs new
- ✅ Can revert instantly if problems
- ✅ All existing methods/signals inherited
- ✅ Safe for production environment

---

### Detailed Implementation Specifications

#### CameraLiveViewer Layout (from Agent 1)

**Target Dimensions:** 660px W x 730px H

**Layout Hierarchy:**
```
QVBoxLayout (main_layout)
├── QGroupBox "Live Image" (display_group)
│   └── QLabel (image_label) 640x480 min, full width
│
├── QGroupBox "Light Source Control" (laser_led_group) [COLLAPSIBLE]
│   └── LaserLEDControlPanel (0-120px when expanded)
│
├── QGroupBox "Camera Controls" (controls_group)
│   └── QGridLayout (3 rows x 4 columns)
│       ├── Row 0: [Label "Live:"] [Start] [Stop] [Snapshot]
│       ├── Row 1: [Label "Exposure:"] [SpinBox] [=] [ms Label]
│       └── Row 2: [Image Controls Button - full width]
│
└── QGroupBox "Image Information" (info_group)
    └── QGridLayout (3 rows x 4 columns)
        ├── Row 0: [Status] [Image info]
        ├── Row 1: [FPS] [Exposure]
        └── Row 2: [Intensity] [Auto-scale warning]
```

**Key Changes from Original:**
1. Line 86: `QHBoxLayout()` → `QVBoxLayout()`
2. Line 81: `setMinimumSize(1000, 600)` → `setMinimumSize(660, 730)`
3. Wrap LaserLEDPanel in collapsible QGroupBox
4. Convert controls to QGridLayout for compactness
5. Reorder: Image first, controls second, info third

#### Sample3DVisualizationWindow Multi-Plane Tab (from Agent 1)

**Layout Structure:**
```
QWidget (multiplane_tab)
└── QVBoxLayout
    ├── QHBoxLayout (top_row)
    │   ├── QGroupBox "XY Plane (Side View)"
    │   │   ├── QLabel (xy_image_label) 350x250
    │   │   └── QSlider (z_slice_slider)
    │   └── QGroupBox "YZ Plane (End View)"
    │       ├── QLabel (yz_image_label) 300x250
    │       └── QSlider (x_slice_slider)
    ├── QGroupBox "XZ Plane (Top-Down View)"
    │   ├── QLabel (xz_image_label) 650x250
    │   └── QSlider (y_slice_slider)
    └── QLabel (slice_info) - Shows current X, Y, Z position
```

**Data Flow:**
```
voxel_storage.get_display_volume(channel_id)
    ↓
volume (Z, Y, X) shaped numpy array
    ↓
Slice extraction:
- XY: volume[z_idx, :, :]
- YZ: volume[:, :, x_idx]
- XZ: volume[:, y_idx, :]
    ↓
Apply channel colormap and contrast
    ↓
Convert to QPixmap
    ↓
Display on QLabel
```

**Integration Points:**
1. Add tab in `_create_control_panel()` after Data tab
2. Connect to existing `voxel_storage` system
3. Use same colormap/contrast as channel controls
4. Update on slider change or data update

---

### Testing Strategy

#### Phase 1: Layout Testing ✅ (Current)
- ✅ Basic structure created
- ✅ Widget hierarchy defined
- ⏳ Verify dimensions match Agent 1 specs
- ⏳ Test with mock controllers
- ⏳ Ensure all widgets visible

#### Phase 2: Functional Testing (Next)
- ⏳ Test with real camera controller
- ⏳ Verify all buttons/controls work
- ⏳ Test image display and updates
- ⏳ Test multi-plane data extraction
- ⏳ Verify signal connections intact

#### Phase 3: Integration Testing
- ⏳ Both windows open simultaneously
- ⏳ Verify total width ≤ 1920px
- ⏳ Test on target monitor
- ⏳ Test window resizing
- ⏳ Test window management

#### Phase 4: User Acceptance Testing
- ⏳ Await Agent 2 feedback
- ⏳ Test with real imaging workflows
- ⏳ Assess control accessibility
- ⏳ Evaluate cognitive load
- ⏳ Gather user feedback

---

### Technical Considerations

#### PyQt5 Layout Management
- Use `QVBoxLayout` and `QHBoxLayout` for organization
- Use `QGroupBox` for visual separation and grouping
- Use `QGridLayout` for compact multi-column arrangements
- Set `setContentsMargins()` and `setSpacing()` to control compactness
- Use `addStretch()` to control expansion behavior

#### Image Display
- Maintain aspect ratio: `Qt.KeepAspectRatio`
- Use `Qt.SmoothTransformation` for scaling
- Handle empty/missing data gracefully with placeholder text
- Multi-plane: Display at native resolution or scale to fit label

#### Signal Handling
- Use `blockSignals(True/False)` to prevent feedback loops
- Connect sliders: `valueChanged` for live label updates
- Connect sliders: `sliderReleased` for actual movement commands
- All original signals inherited and functional

#### Data Access
- Multi-plane reads from `self.voxel_storage.get_display_volume(channel_id)`
- Volume shape: (Z, Y, X) per napari convention (Axis 0, 1, 2)
- Respect channel visibility settings
- Apply same intensity scaling and colormap as 3D viewer
- Cache slices if performance issues arise

#### Thread Safety
- All Qt GUI updates on main thread
- Use `@pyqtSlot` decorators for signal handlers
- Heavy processing: Use `QTimer.singleShot()` to defer
- Inherited from parent: All existing thread safety measures

---

### Key Implementation Details

#### Collapsible Laser/LED Panel (CameraLiveViewer)
```python
laser_group = QGroupBox("Light Source Control")
laser_group.setCheckable(True)
laser_group.setChecked(False)  # Default collapsed
laser_group.toggled.connect(lambda: self.adjustSize())
```

#### Grid Layout for Controls (CameraLiveViewer)
```python
controls_layout = QGridLayout()
# Row 0: Buttons
controls_layout.addWidget(QLabel("Live:"), 0, 0)
controls_layout.addWidget(self.start_btn, 0, 1)
controls_layout.addWidget(self.stop_btn, 0, 2)
controls_layout.addWidget(self.snapshot_btn, 0, 3)
# Row 1: Exposure
controls_layout.addWidget(QLabel("Exposure:"), 1, 0)
controls_layout.addWidget(self.exposure_spinbox, 1, 1)
controls_layout.addWidget(QLabel("="), 1, 2)
controls_layout.addWidget(self.exposure_ms_label, 1, 3)
# Row 2: Image Controls Button (full width)
controls_layout.addWidget(self.image_controls_btn, 2, 0, 1, 4)
```

#### Tabbed Viewer (Sample3DVisualizationWindow)
```python
# In viewer_container after status bar:
viewer_tabs = QTabWidget()

# Tab 1: Original napari viewer
tab_3d = QWidget()
tab_3d_layout = QVBoxLayout()
tab_3d_layout.addWidget(self.viewer.window._qt_window)
tab_3d.setLayout(tab_3d_layout)
viewer_tabs.addTab(tab_3d, "3D Volume")

# Tab 2: Multi-plane views
multiplane_tab = self.test_create_multiplane_tab()
viewer_tabs.addTab(multiplane_tab, "Multi-Plane Views")

viewer_layout.addWidget(viewer_tabs)
```

---

### Backward Compatibility Guarantee

**Original Files Remain Unchanged:**
- ✅ `camera_live_viewer.py` - No modifications
- ✅ `sample_3d_visualization_window.py` - No modifications
- ✅ All other view files - No modifications

**Original Classes Still Work:**
```python
# Original still importable and functional
from py2flamingo.views.camera_live_viewer import CameraLiveViewer
viewer = CameraLiveViewer(...)  # Works exactly as before

# Test version available alongside
from test_gui_redesign.test_camera_live_viewer import TestCameraLiveViewer
test_viewer = TestCameraLiveViewer(...)  # New layout
```

**Migration Path:**
1. Test versions proven stable
2. User feedback confirms improvement
3. Original `_setup_ui()` can call test version
4. Or: Promote test version to primary
5. Keep old as `legacy_setup_ui()` for rollback

---

### Awaiting Agent 2 Feedback

Agent 2's user evaluation will inform:
- ✅ Control accessibility during live imaging
- ✅ Workflow efficiency improvements
- ✅ Information visibility at a glance
- ✅ Cognitive load assessment
- ✅ Edge case identification
- ✅ Refinement recommendations

Once Agent 2 completes evaluation:
1. Incorporate feedback into test implementations
2. Adjust dimensions if needed
3. Refine control groupings
4. Add any missing functionality
5. Proceed to functional testing

---

### Summary: Implementation Complete

**Files Created:**
1. ✅ `/home/msnelson/LSControl/Flamingo_Control/test_gui_redesign/test_camera_live_viewer.py`
2. ✅ `/home/msnelson/LSControl/Flamingo_Control/test_gui_redesign/test_sample_3d_visualization_window.py`
3. ✅ `/home/msnelson/LSControl/Flamingo_Control/test_gui_redesign/test_integration_demo.py`
4. ✅ `/home/msnelson/LSControl/Flamingo_Control/test_gui_redesign/README.md`

**Implementation Status:**
- ✅ Code structure and inheritance patterns
- ✅ Agent 1 design specifications documented
- ✅ Test file templates with detailed comments
- ✅ Mock controllers for hardware-free testing
- ✅ Integration demo with dimension validation
- ✅ Comprehensive documentation
- ⏳ Awaiting Agent 2 user evaluation
- ⏳ Final implementation refinements
- ⏳ Functional testing with hardware

**Ready for:**
- Agent 2 to provide user evaluation
- Developers to implement final layouts
- Testing with real hardware
- User acceptance testing

---

## Final Output Files:

### Test Implementation Files (Agent 3)

All files located in: `/home/msnelson/LSControl/Flamingo_Control/test_gui_redesign/`

1. **test_camera_live_viewer.py** (353 lines)
   - Test version of CameraLiveViewer with vertical layout
   - Inherits from original, overrides `_setup_ui()`
   - Target: 660x730 pixels per Agent 1 specs
   - Status: Template created, awaiting Agent 2 feedback

2. **test_sample_3d_visualization_window.py** (500+ lines)
   - Extended Sample3DVisualizationWindow with multi-plane tab
   - Adds 4th tab showing XY, YZ, XZ orthogonal views
   - Synchronized slice selection and navigation
   - Status: Template created, awaiting Agent 2 feedback

3. **test_integration_demo.py** (350 lines)
   - Integration demonstration showing both windows side-by-side
   - Mock controllers for hardware-free testing
   - Dimension validation against Agent 1 specifications
   - Usage: `python test_gui_redesign/test_integration_demo.py`

4. **README.md** (Comprehensive documentation)
   - Overview of redesign goals
   - Agent 1 design specifications summary
   - File descriptions and usage instructions
   - Implementation approach (inheritance-based)
   - Testing strategy (4 phases)
   - Technical details and code structure

### Design Specifications (Agent 1)

**CameraLiveViewer:**
- Target dimensions: 660px W x 730px H
- Layout: Vertical (QVBoxLayout)
- Components: Image top, controls below
- Key changes documented in lines 32-383

**Sample3DVisualizationWindow:**
- Target dimensions: 950px W x 800px H
- Layout: Horizontal splitter with tabbed viewer
- New tab: Multi-Plane Views (XY, YZ, XZ)
- Key changes documented in lines 137-303

**Combined Layout:**
- Total width: 660 + 950 = 1610px
- Fits comfortably on 1920px screen with 310px margin
- Both windows open simultaneously
- Side-by-side arrangement

### Next Steps

1. **Agent 2 User Evaluation** (In Progress)
   - Review Agent 1's design from user perspective
   - Assess workflow efficiency
   - Identify any usability concerns
   - Provide refinement recommendations

2. **Implementation Refinement** (After Agent 2)
   - Incorporate Agent 2 feedback
   - Finalize layout implementations
   - Complete collapsible laser panel
   - Implement grid layouts per specs

3. **Functional Testing**
   - Test with real camera controller
   - Test with real movement controller
   - Verify multi-plane data extraction
   - Validate all signal connections

4. **Integration Testing**
   - Both windows open simultaneously
   - Verify dimensions on target screen
   - Test window positioning and management
   - Performance validation

5. **User Acceptance Testing**
   - Test with actual imaging workflows
   - Gather user feedback
   - Iterate based on real-world usage
   - Final refinements

### Status Summary

- ✅ **Agent 1 (UI Design):** Complete - Design specifications provided
- ⏳ **Agent 2 (User Evaluation):** Waiting - Ready to evaluate Agent 1's design
- ✅ **Agent 3 (Implementation):** Complete - Test files and documentation created

### References

- Planning document: `/home/msnelson/LSControl/Flamingo_Control/GUI_REDESIGN_PLAN.md`
- Test implementations: `/home/msnelson/LSControl/Flamingo_Control/test_gui_redesign/`
- Original CameraLiveViewer: `src/py2flamingo/views/camera_live_viewer.py`
- Original Sample3DVisualizationWindow: `src/py2flamingo/views/sample_3d_visualization_window.py`
- Reference pattern: `src/py2flamingo/views/stage_chamber_visualization_window.py`
