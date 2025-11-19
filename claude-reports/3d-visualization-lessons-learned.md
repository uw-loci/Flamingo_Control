# 3D Visualization Implementation - Lessons Learned

**Date:** 2025-11-19
**Purpose:** Document lessons learned from implementing 3D sample chamber visualization for potential restart

---

## Executive Summary

Attempted to create a 3D visualization of the sample chamber using napari with rotation-aware data accumulation. While significant progress was made, several fundamental coordinate system challenges emerged that may warrant starting fresh with a clearer architecture.

---

## Key Challenges Encountered

### 1. Napari Coordinate System Complexity

**Issue:** Napari's 3D coordinate system interpretation differs from intuitive physical space.

**What We Learned:**
- Napari with `ndisplay=3` displays axes as 0, 1, 2 (cannot customize to X, Y, Z labels easily)
- Axis ordering matters critically: dimensions passed as (D0, D1, D2) map to (Axis 0, Axis 1, Axis 2)
- Camera orientation defaults don't align with our physical setup (chamber appears upside down)
- Setting `camera.angles` alone doesn't properly orient the initial view

**Attempted Solutions:**
- Tried multiple coordinate orderings: (X,Y,Z), (Z,Y,X), (X,Z,Y)
- Final working: (X, Y, Z) where dims = (width_x, height_y, depth_z)
  - Axis 0 = X (horizontal)
  - Axis 1 = Y (vertical) ✓
  - Axis 2 = Z (depth toward objective)

**Remaining Issues:**
- Camera still shows chamber upside down on startup
- Objective appears on left side instead of "back"
- Camera.angles setting didn't properly orient the view

---

### 2. Physical Space vs Voxel Space Mapping

**Critical Lesson:** The chamber doesn't start at (0,0,0) in physical coordinates.

**Physical Space Ranges:**
- X: 1.0mm to 12.31mm (11.31mm travel)
- Y: -5mm to +10mm (15mm chamber height, with 5mm anchor preventing collision)
- Z: 12.5mm to 26mm (13.5mm depth)

**Voxel Space:**
- Always starts at (0,0,0)
- Dimensions: (754, 1000, 900) voxels at 15µm resolution
- Represents only the chamber bounds (minimal memory footprint)

**Conversion Formula:**
```python
voxel_coordinate = (physical_position_µm - chamber_offset_µm) / voxel_size_µm

Chamber offsets:
- X offset: 1000µm (1.0mm - X minimum)
- Y offset: -5000µm (-5mm - chamber bottom = anchor - 10mm)
- Z offset: 12500µm (12.5mm - Z minimum)
```

**Key Insight:** Must maintain this offset mapping consistently:
- User-facing GUI: Always use physical coordinates (mm/µm)
- Internal display: Use voxel coordinates (0-indexed from chamber corner)
- Conversion layer: Critical for correct positioning

---

### 3. Sample Holder Positioning Issues

**Challenge:** Getting the holder to appear in the correct location within the chamber.

**What Worked:**
- Initializing at chamber center in voxel space: `dims[i] // 2`
- Drawing from y_bottom (current position) to y_top (chamber top)
- Only displaying visible portion (performance optimization)

**What Didn't Work:**
- Calculating initial position from physical coordinates with offsets (resulted in wrong position)
- Not accounting for offsets when updating position from GUI controls

**Current Behavior:**
- Holder appears for Y values 0-6000µm
- Disappears for Y > 6000µm (backwards from expected)
- Expected range: 5000-15000µm (Y_min anchor to beyond chamber top)
- Issue suggests Y axis drawing direction may be inverted in display

---

### 4. Napari API Compatibility Issues

**Lessons from Earlier Sessions:**

**Parameter Name Changes (napari 0.4 → 0.5+):**
- `edge_color` → `border_color` (Points layer)
- `edge_width` → `border_width` (Points layer)
- Must use `border_*` for compatibility

**Shape/Layer Quirks:**
- Shapes layer in 3D requires consistent dimensionality (all 3D coords)
- Vectors layer format: `[[[position], [direction]]]` as (N, 2, 3) array
- Ellipse shapes don't render reliably in 3D → use point circles instead
- Labels layer with sparse data (mostly zeros) doesn't show well

**Cannot Customize:**
- `axes.labels` only accepts boolean, not custom strings
- Stuck with 0, 1, 2 labels (cannot show X, Y, Z directly)

---

## Architectural Decisions

### What Worked Well:

**1. Configuration-Driven Design**
- All dimensions in `visualization_3d_config.yaml`
- Easy to adjust without code changes
- Clear separation of concerns

**2. Dual-Resolution Storage**
- High-res storage (5µm voxels) separate from display (15µm)
- 3x resolution ratio prevents data loss during rotation
- Sparse arrays (sparse.DOK) for memory efficiency

**3. Minimal Display Bounds**
- Only show chamber region, not full stage travel range
- Significantly reduces voxel count
- Faster rendering and lower memory usage

### What Needs Improvement:

**1. Coordinate System Architecture**
- Need clearer documentation of physical vs voxel space
- Offset calculations should be centralized
- Consider a CoordinateMapper class for all conversions

**2. Initial View Setup**
- Camera orientation needs better defaults
- May need to programmatically rotate/flip the view
- Consider storing preferred camera state in config

**3. GUI Controls**
- Position spinboxes (not sliders) now working
- Need proper validation of ranges (don't allow Y < Y_min)
- Should prevent positions outside chamber bounds

---

## Current State

### What's Working: ✓
- Rotation indicator (red line) rotates correctly in XZ plane with Y rotation
- Objective circle appears (but on wrong side)
- Chamber wireframe displays with correct proportions
- X, Y, Z position spinboxes with real stage ranges
- Y-axis rotation control (only physical rotation)
- Coordinate offset system (mostly working)

### What's Not Working: ✗
- Sample holder disappears for Y > 6000µm (should show for 5000-15000µm)
- Y axis appears inverted (holder extends wrong direction)
- Camera orientation: chamber upside down, objective on left instead of back
- Napari axes still show 0,1,2 (cannot customize to X,Y,Z)

---

## Technical Specifications

### Chamber Configuration:
```yaml
y_min_anchor_mm: 5.0              # Anchor point (collision prevention)
chamber_below_anchor_mm: 10.0     # Chamber extends 10mm below anchor
chamber_above_anchor_mm: 5.0      # Chamber extends 5mm above anchor
chamber_width_x_mm: 11.31         # X dimension
chamber_x_offset_mm: 1.0          # X starts at 1mm
chamber_depth_z_mm: 13.5          # Z dimension
chamber_z_offset_mm: 12.5         # Z starts at 12.5mm
```

### Stage Ranges (Physical):
- X: 1.0mm → 12.31mm
- Y: 0mm → 15mm (with 5mm minimum for safety)
- Z: 12.5mm → 26mm
- Rotation: -180° → +180° (around Y axis only)

### Display Dimensions (Voxel):
- X: 754 voxels (11.31mm / 15µm)
- Y: 1000 voxels (15mm / 15µm)
- Z: 900 voxels (13.5mm / 15µm)

---

## Napari-Specific Findings

### Viewer Setup:
```python
viewer = napari.Viewer(ndisplay=3, show=False)
viewer.axes.visible = True
viewer.axes.labels = True  # Only boolean, not custom strings
viewer.axes.colored = True
```

### Coordinate Expectations:
- Points data: `np.array([[x, y, z], ...])`  → interpreted as (Axis 0, Axis 1, Axis 2)
- Shapes lines: `[[[x1,y1,z1], [x2,y2,z2]]]`
- Vectors: `[[[pos_x,pos_y,pos_z], [dir_x,dir_y,dir_z]]]`

### Layer Types Used:
- **Shapes (lines)**: Chamber wireframe (12 edges), rotation indicator
- **Points**: Sample holder (vertical line), objective (circle of points)
- **Image**: Multi-channel fluorescence data (not yet tested with real data)

---

## Performance Optimizations

### Implemented:
1. **Sparse Arrays:** DOK format for storage (only stores non-zero voxels)
2. **Partial Holder Display:** Only show from current position to chamber top
3. **Point Sampling:** Holder drawn every 2 voxels (step=2)
4. **Minimal Bounds:** Chamber-only display, not full stage range

### Memory Impact:
- Full stage range would be: ~6000 x 6000 x 6000 voxels (infeasible)
- Chamber-only display: 754 x 1000 x 900 ≈ 678K voxels (manageable)
- Sparse storage: Only occupied voxels consume memory

---

## Recommendations for Fresh Start

### 1. Coordinate System First
**Before writing any code:**
- Document physical coordinate system clearly (X, Y, Z definitions)
- Document napari's axis interpretation (test with simple example)
- Create CoordinateMapper class with thorough unit tests
- Test coordinate mapping in isolation before building UI

### 2. Camera Orientation Strategy
**Options to consider:**
- **Option A:** Accept napari's default orientation, adjust mental model
- **Option B:** Programmatically set camera matrix to desired view
- **Option C:** Use napari's `viewer.camera.set_view_direction()` if available
- **Recommendation:** Test camera control in minimal example first

### 3. Simplified Initial Implementation
**Progressive approach:**
1. Start with just the chamber wireframe (no holder, no objective)
2. Add static holder at center
3. Add position controls one axis at a time (verify each)
4. Add rotation indicator last
5. Only then add data accumulation

### 4. Validation at Each Step
**Test after each addition:**
- Print voxel coordinates to console
- Verify positions with napari's axis display
- Use simple geometric shapes before complex visualizations
- Log all coordinate transformations

---

## Code Structure Recommendations

### Separate Concerns:

**1. CoordinateMapper Class:**
```python
class CoordinateMapper:
    def __init__(self, chamber_config):
        self.offsets = calculate_offsets(chamber_config)
        self.voxel_size = chamber_config['voxel_size']

    def physical_to_voxel(self, x_mm, y_mm, z_mm) -> Tuple[int, int, int]
    def voxel_to_physical(self, vx, vy, vz) -> Tuple[float, float, float]
    def validate_physical_position(self, x, y, z) -> bool
```

**2. ChamberGeometry Class:**
```python
class ChamberGeometry:
    def get_wireframe_points(self) -> List
    def get_bounds(self) -> Dict
    def is_within_bounds(self, position) -> bool
```

**3. Separate Napari Wrapper:**
- Isolate napari-specific code
- Makes it easier to swap visualization backends
- Easier to test coordinate system independently

---

## Known Napari Quirks

### API Issues:
1. `axes.labels` only accepts `True/False`, not custom labels
2. Camera angles may not apply as expected at initialization
3. Shape ellipses unreliable in 3D → use point circles
4. Coordinate interpretation not well documented

### Version Compatibility:
- napari >= 0.5.0 uses `border_*` parameters
- napari < 0.5.0 uses `edge_*` parameters
- Must handle both for compatibility

### Performance:
- Large point sets slow down rendering
- Updating layer data triggers full refresh
- Consider using napari's async update mechanisms

---

## Unsolved Problems

### 1. Camera Orientation
- Chamber appears upside down on startup
- Objective on left instead of back
- `camera.angles = (45, 30, 0)` didn't fix this
- **Need to investigate:** camera matrix, view direction, or up vector

### 2. Y Axis Inversion
- Holder shows for Y=0-6000µm (wrong)
- Should show for Y=5000-15000µm (correct range)
- Holder extends in wrong direction
- **Possible cause:** Y axis might be inverted in napari's display

### 3. Axis Labeling
- Cannot show X, Y, Z labels (only 0, 1, 2)
- Makes it hard for users to understand orientation
- **Workaround needed:** Add text annotations or legend

---

## Data Integration (Not Yet Tested)

### Ready but Untested:
- `process_frame()` method accepts multi-channel image data
- Coordinate transformation system in place
- Dual-resolution storage system implemented
- Channel layer setup completed

### Unknown:
- Will coordinate transformations work correctly with real camera data?
- Will rotation-aware accumulation function as designed?
- Will sparse array performance be acceptable with real data?

---

## Configuration File Structure

### Current Config (`visualization_3d_config.yaml`):

**Well-Designed Sections:**
- Display settings (voxel size, rendering)
- Storage settings (high-res parameters)
- Channel configurations (laser lines: 405nm, 488nm, 561nm, 640nm)
- Performance settings (threading, LOD)

**Needs Improvement:**
- Chamber dimensions section became convoluted with offsets
- Should separate "physical chamber specs" from "stage travel ranges"
- Consider splitting into multiple configs (hardware vs visualization)

---

## What to Keep for Next Implementation

### Architecture ✓
1. **Configuration-driven approach** - avoid hardcoded values
2. **Dual-resolution storage** - essential for rotation without data loss
3. **Sparse arrays** - memory efficiency is critical
4. **Offset-based coordinate mapping** - correct principle, needs better implementation

### UI Design ✓
1. **Tabbed interface** - clean organization
2. **Separate position controls** (X, Y, Z spinboxes)
3. **Single rotation control** (Y-axis only)
4. **Per-channel controls** - visibility, opacity, colormap
5. **Real stage ranges** in spinboxes (not arbitrary values)

### Visualization Elements ✓
1. **Chamber wireframe** - helps with orientation
2. **Rotation indicator** - shows 0-degree position (working well!)
3. **Objective indicator** - shows imaging direction (good concept)
4. **Sample holder** - shows stage position (concept good, implementation needs work)

---

## What to Change for Next Implementation

### 1. Start with Coordinate System
**Before any napari code:**
- Create standalone coordinate mapper with unit tests
- Test with simple print statements
- Verify physical → voxel → physical round-trip works
- Document coordinate system in detail with diagrams

### 2. Test Napari Separately
**Create minimal napari test:**
- Simple 3D box with known dimensions
- Add single point at known position
- Verify camera orientation can be controlled
- Test axis display and labeling options
- Find way to set proper "up" direction

### 3. Build Incrementally
**One component at a time:**
1. Chamber wireframe only (verify orientation)
2. Add objective (verify it's on back wall)
3. Add holder at fixed position (verify it's inside and vertical)
4. Add position controls (verify each axis independently)
5. Add rotation indicator (verify plane of rotation)
6. Finally add data accumulation

### 4. Better Separation of Concerns
```
CoordinateMapper (pure Python, fully tested)
    ↓
ChamberGeometry (generates shapes in voxel space)
    ↓
NapariRenderer (handles napari-specific quirks)
    ↓
Sample3DVisualizationWindow (orchestrates UI)
```

---

## Specific Technical Recommendations

### Coordinate Mapper Implementation:
```python
class CoordinateMapper:
    """Maps between physical stage coordinates and voxel display coordinates."""

    def __init__(self, config):
        # Physical space bounds (mm)
        self.x_min, self.x_max = 1.0, 12.31
        self.y_min, self.y_max = -5.0, 10.0  # Chamber bounds (anchor at 5mm)
        self.z_min, self.z_max = 12.5, 26.0

        # Voxel size
        self.voxel_size_um = 15.0

        # Calculated offsets (chamber bottom/left/back in physical space)
        self.offset_um = {
            'x': self.x_min * 1000,
            'y': self.y_min * 1000,
            'z': self.z_min * 1000
        }

        # Dimensions in voxels
        self.dims = (
            int((self.x_max - self.x_min) * 1000 / self.voxel_size_um),
            int((self.y_max - self.y_min) * 1000 / self.voxel_size_um),
            int((self.z_max - self.z_min) * 1000 / self.voxel_size_um)
        )

    def physical_to_voxel(self, x_mm, y_mm, z_mm):
        """Convert physical mm to voxel indices."""
        x_um, y_um, z_um = x_mm * 1000, y_mm * 1000, z_mm * 1000
        return (
            int((x_um - self.offset_um['x']) / self.voxel_size_um),
            int((y_um - self.offset_um['y']) / self.voxel_size_um),
            int((z_um - self.offset_um['z']) / self.voxel_size_um)
        )

    def voxel_to_physical(self, vx, vy, vz):
        """Convert voxel indices to physical mm."""
        x_um = vx * self.voxel_size_um + self.offset_um['x']
        y_um = vy * self.voxel_size_um + self.offset_um['y']
        z_um = vz * self.voxel_size_um + self.offset_um['z']
        return (x_um / 1000, y_um / 1000, z_um / 1000)

    def validate_position(self, x_mm, y_mm, z_mm):
        """Check if position is within chamber bounds."""
        return (self.x_min <= x_mm <= self.x_max and
                self.y_min <= y_mm <= self.y_max and
                self.z_min <= z_mm <= z_max)
```

### Camera Orientation Setup:
**Need to investigate:**
- napari's camera model (orthographic vs perspective)
- How to set "up" vector properly
- Whether to flip Y axis in display
- How to set initial view direction toward objective (back wall)

**Possible approaches:**
```python
# Option 1: Set camera center and view direction
viewer.camera.center = (dims[0]//2, dims[1]//2, dims[2]//2)
viewer.camera.angles = (azimuth, elevation, roll)

# Option 2: Set view direction explicitly
viewer.camera.view_direction = (0, 0, -1)  # Look toward -Z
viewer.camera.up_direction = (0, 1, 0)     # Y is up

# Option 3: Set full camera matrix
# (Need to research napari's camera API)
```

---

## Questions to Answer Before Restart

### Napari Fundamentals:
1. What is the definitive axis ordering for 3D displays?
2. How to set camera orientation reliably?
3. Can Y axis be flipped to match physical "up" direction?
4. Is there a way to add custom axis labels or legends?

### Design Decisions:
1. Should we transform coordinates to match napari's expected orientation?
2. Or should we transform napari's display to match our coordinates?
3. Should holder extend from top down, or bottom up?
4. Where should (0,0,0) be in voxel space? (corner vs center?)

### Testing Strategy:
1. Create minimal reproducible example for coordinate system?
2. Test each axis independently before combining?
3. Add visual indicators (colored spheres at known positions)?
4. Create unit tests for coordinate mapper before UI work?

---

## Files Modified This Session

### Core Implementation:
- `src/py2flamingo/views/sample_3d_visualization_window.py` - Main window
- `src/py2flamingo/configs/visualization_3d_config.yaml` - Configuration
- `test_3d_visualization.py` - Test script with simulated data

### Previous Session Files (Still Relevant):
- `src/py2flamingo/visualization/dual_resolution_storage.py` - Storage engine
- `src/py2flamingo/visualization/coordinate_transforms.py` - Rotation transforms

---

## Success Criteria for Next Attempt

### Minimum Viable Visualization:
1. ✓ Chamber wireframe visible and correctly oriented
2. ✓ Objective on back wall (visible when looking from front)
3. ✓ Sample holder vertical, inside chamber, at center
4. ✓ Holder extends/retracts with Y position control (5-10mm range)
5. ✓ Rotation indicator rotates in horizontal plane with Y rotation
6. ✓ Coordinate system matches physical stage (X, Y, Z)
7. ✓ Camera orientation: Y-up, looking toward objective from front

### Stretch Goals:
- Data accumulation with rotation working
- Multi-channel visualization
- Performance acceptable for real-time updates
- Memory usage reasonable for extended sessions

---

## Key Takeaways

### Biggest Lessons:
1. **Coordinate systems are hard** - napari's interpretation isn't intuitive
2. **Test in isolation first** - don't build UI before understanding napari
3. **Physical space ≠ voxel space** - offsets are critical, must be consistent
4. **Napari quirks are significant** - API limitations, camera control, axis labeling
5. **Start simple, add complexity** - chamber wireframe should come before data

### What Would Help:
- Napari documentation deep dive (camera, coordinates, 3D rendering)
- Simple test cases for each coordinate transformation
- Visual debugging tools (colored markers at known positions)
- Step-by-step validation of each component

### Is Napari the Right Tool?
**Pros:**
- Good 3D rendering
- Multi-channel support
- Python integration
- Active development

**Cons:**
- Coordinate system complexity
- Limited camera control
- API quirks and version incompatibilities
- Axis labeling limitations

**Alternatives to Consider:**
- PyVista (more control over camera/axes)
- VisPy (lower level, more customizable)
- Plotly (interactive, web-based)
- VTK (powerful but complex)

---

## Next Steps (If Restarting)

### Day 1: Coordinate System
1. Create CoordinateMapper class with full unit tests
2. Document all coordinate transformations with examples
3. Test in Python REPL before any UI code

### Day 2: Napari Fundamentals
1. Minimal napari example: box + point at known coordinates
2. Test camera orientation control
3. Understand axis display and labeling
4. Document napari quirks and workarounds

### Day 3: Build Chamber Visualization
1. Chamber wireframe only
2. Verify correct orientation (objective placement)
3. Add position markers at known physical coordinates
4. Validate coordinate mapping end-to-end

### Day 4: Add Interactive Elements
1. Sample holder with position control
2. Verify each axis independently
3. Add rotation indicator
4. Test all controls thoroughly

### Day 5: Data Integration
1. Test with simulated data
2. Verify coordinate transformations
3. Test rotation-aware accumulation
4. Performance testing

---

## Current Code Status

**Git Repository:** https://github.com/uw-loci/Flamingo_Control
**Branch:** main
**Last Commit:** 2c227a9 - "Fix Y offset calculation for correct chamber bottom position"

**Key Files:**
- Main window: `src/py2flamingo/views/sample_3d_visualization_window.py` (~1000 lines)
- Config: `src/py2flamingo/configs/visualization_3d_config.yaml`
- Test: `test_3d_visualization.py`

**State:** Functional but with significant orientation and coordinate issues. Ready for potential fresh start with lessons learned applied.

---

## Conclusion

Significant progress was made in understanding napari's 3D visualization capabilities and the challenges of coordinate system mapping. The core architecture (dual-resolution storage, configuration-driven design, offset-based mapping) is sound. However, the napari-specific implementation revealed fundamental challenges with camera orientation and axis interpretation.

**Recommendation:** A fresh start with a coordinate-system-first approach and incremental validation would likely reach a working solution faster than continuing to debug the current implementation.

The lessons learned here provide a solid foundation for a more systematic second attempt.
