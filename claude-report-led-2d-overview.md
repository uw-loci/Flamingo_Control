# Claude Report: LED 2D Overview Feature

**Date:** 2025-12-16 to 2026-01-06
**Commits:** 640d84a, 0c80f00, 1177270, e8c2137, 68d4781 (and others)

---

## Feature Overview

The "LED 2D Overview" extension creates rough 2D overview maps of a sample region at two rotation angles (R and R+90 degrees). It provides a quick visual survey of a specimen area without high-resolution focus stacking.

---

## Architecture

```
LED2DOverviewDialog (non-modal)
    │
    ├── Point entry (A, B, optional C)
    │   └── Load from saved position presets
    │
    ├── Scan settings (Starting R, Z range/step)
    │
    └── Start Scan (validated)
            │
            ▼
    LED2DOverviewWorkflow
            │
            ├── Enable LED for preview
            │
            ├── Generate serpentine tile path
            │
            ├── For each rotation (R, R+90):
            │   └── For each tile position:
            │       ├── Move to (X, Y)
            │       ├── Quick Z-stack (max 10 positions)
            │       └── Select best-focus frame
            │
            ├── Disable LED
            │
            └── Assemble tiles into grid
                    │
                    ▼
            LED2DOverviewResultWindow
                    │
                    └── Zoomable/pannable display with tile overlay
```

---

## Files

| File | Purpose |
|------|---------|
| `src/py2flamingo/views/dialogs/led_2d_overview_dialog.py` | Configuration dialog |
| `src/py2flamingo/views/dialogs/led_2d_overview_result.py` | Results display window |
| `src/py2flamingo/workflows/led_2d_overview_workflow.py` | Scan execution logic |
| `src/py2flamingo/services/stage_service.py` | Stage movement (fixed for async) |
| `src/py2flamingo/controllers/motion_tracker.py` | Motion callback tracking |

---

## Key Features

### 1. Configuration Dialog

**Bounding Points:**
- Points A and B (required) define minimum bounding box
- Point C (optional) expands the box
- "Get Pos" buttons capture current stage position
- Preset loading from Stage Control's saved positions

**Scan Settings:**
- Starting R rotation angle (second is +90)
- Z range and step size (default: 2.5mm range, 0.25mm step)
- MAX_Z_POSITIONS = 10 cap for speed

**Validation:**
- Blocks Start if no LED selected
- Blocks Start if live viewer not active
- Blocks Start if less than 2 bounding box points defined

### 2. Workflow Execution

**LED Control:**
```python
def _enable_led(self) -> bool:
    led_name = self._config.led_name
    led_map = {
        'led_red': 0, 'led_green': 1,
        'led_blue': 2, 'led_white': 3,
    }
    led_color = led_map.get(led_name.lower().replace(' ', '_'))
    return laser_led_controller.enable_led_for_preview(led_color)
```

**Optimized Movement Delays:**
```python
# X-axis move: 0.05s delay (was 0.5s)
# Y-axis move: 0.1s delay (was 0.5s)
# Z-axis move: 0.02s delay (was 0.25s)
```

**Z-Stack Speed Optimization:**
- Default Z step: 0.25mm (was 0.05mm)
- Maximum 10 Z positions per tile
- Hardware limit: ~0.4s per stage move

### 3. Result Display

**ZoomableImageLabel:**
- Mousewheel zoom (1% to 2000%)
- Click-and-drag panning
- Fit and 1:1 buttons
- Zoom percentage display

**Tile Overlay:**
- Grid lines showing tile boundaries
- XY coordinates displayed on each tile:
  ```
  X:12.34
  Y:56.78
  ```

**Scan Info:**
- Calculated from actual tile indices (not stored values)
- Shows tiles_x × tiles_y and total tile count

---

## Bug Fixes

### 1. StageService Timeout (2025-12-18)

**Issue:** Stage commands timed out because `_send_movement_command()` directly accessed the socket, conflicting with the async `socket_reader`.

**Fix:** Use base class `_send_command()` which properly routes through the async reader:

```python
def _send_movement_command(self, command_code, command_name, axis, position_mm):
    return self._send_command(
        command_code=command_code,
        command_name=command_name,
        params=[0, 0, 0, axis, 0, 0, 0],
        value=position_mm
    )
```

### 2. LED Not Turning On (2025-12-18)

**Issue:** Config had `led_name='none'` - LED was never enabled.

**Fix:** Added `_enable_led()` method that maps LED names to indices and calls `enable_led_for_preview()` before scan starts.

### 3. Scan Too Slow (2025-12-18)

**Issue:** 21 seconds per tile due to excessive delays and too many Z positions.

**Analysis:** Hardware movement time is ~0.4s per move (unavoidable). Software delays were adding unnecessary wait time.

**Fixes:**
- Reduced `time.sleep()` delays from 0.5s to 0.05-0.1s
- Changed default Z step from 0.05mm to 0.25mm
- Added MAX_Z_POSITIONS = 10 cap
- Result: ~1 second per tile (hardware limited)

### 4. Cannot Zoom Out Enough (2025-12-18)

**Issue:** Minimum zoom was 10%, insufficient for large tile grids.

**Fix:** Changed `_min_zoom` from `0.1` to `0.01` (1%).

### 5. Incorrect Tile Count Display (2025-12-18)

**Issue:** Info showed "1x11" instead of actual "2x11" tiles.

**Fix:** Calculate from actual tile indices:
```python
actual_tiles_x = max(t.tile_x_idx for t in result.tiles) + 1
actual_tiles_y = max(t.tile_y_idx for t in result.tiles) + 1
```

### 6. Dialog Blocking Other Dialogs (2025-12-18)

**Issue:** `WindowStaysOnTopHint` prevented interaction with other dialogs (e.g., "Add Position" name input).

**Fix:** Removed `Qt.WindowStaysOnTopHint` from both dialog and result window.

### 7. Motion Callback Queue Overflow (2025-12-18)

**Issue:** Rapid Z-stack movements filled the callback queue (size 10), causing dropped messages.

**Fix:**
```python
self._callback_queue = queue.Queue(maxsize=100)  # Was 10

# Throttle warning spam
except queue.Full:
    self._queue_full_count += 1
    if self._queue_full_count % 10 == 1:
        self.logger.warning(f"Queue full - dropped {self._queue_full_count} messages")
```

### 8. GUI State Desync After Workflow (2025-12-18)

**Issue:** After LED 2D Overview finishes, the Sample View GUI showed incorrect states:
- Live button showed "Stop Live" (red) when live view was actually off
- LED checkbox stayed checked when LED was actually off
- Only the status message correctly showed "illumination off"

**Root Cause:** The workflow called `disable_all_light_sources()` which emitted signals, but:
1. `_on_camera_state_changed` only updated a label, not the live button
2. `_on_preview_disabled` intentionally kept checkboxes checked (to "remember user intent")

**Fixes:**
1. **sample_view.py**: `_on_camera_state_changed` now calls `_update_live_view_state()` to sync button:
```python
def _on_camera_state_changed(self, state) -> None:
    # Update status label
    self.live_status_label.setText(f"Status: {state_name}")
    # Also update the live view button to match actual camera state
    self._update_live_view_state()
```

2. **laser_led_control_panel.py**: `_on_preview_disabled` now unchecks all checkboxes:
```python
def _on_preview_disabled(self) -> None:
    # Uncheck all checkboxes to reflect actual hardware state
    for button in self._source_button_group.buttons():
        if button.isChecked():
            button.blockSignals(True)
            button.setChecked(False)
            button.blockSignals(False)
```

### 9. XY Coordinate Overlay Not Showing (2025-12-18)

**Issue:** XY coordinates were not appearing on tiles in the result window.

**Root Cause:**
1. Coordinates were passed as `(x, y, z)` but code calculated tile positions using `idx % tiles_x`
2. Tiles from serpentine scan were not in row-major order, so index-based calculation was wrong

**Fix:** Pass tile indices with coordinates and use them directly:
```python
# In _display_results():
coords = [(t.x, t.y, t.tile_x_idx, t.tile_y_idx) for t in result.tiles]

# In _draw_grid_overlay():
for coord in self._tile_coords:
    if len(coord) >= 4:
        x, y, tile_x_idx, tile_y_idx = coord[:4]
    # Use tile_x_idx and tile_y_idx directly for positioning
```

---

## Usage Instructions

1. **Connect** to microscope
2. **Open Sample View** (View menu or connection panel)
3. **Start live view** and configure LED illumination
4. Go to **Extensions > LED 2D Overview...**
5. Define bounding region:
   - Navigate to corner, click "Get Pos" for Point A
   - Navigate to opposite corner, click "Get Pos" for Point B
   - (Optional) Add Point C to expand region
6. Adjust Z range/step if needed (default: 2.5mm range, 0.25mm step)
7. Set starting rotation angle
8. Click **Start Scan**
9. View results in the result window:
   - Mousewheel to zoom
   - Click and drag to pan
   - Hover over tiles to see XY coordinates

---

## Performance Characteristics

| Metric | Value |
|--------|-------|
| Time per tile | ~1 second (hardware limited) |
| Z positions per tile | Up to 10 |
| Minimum zoom | 1% |
| Maximum zoom | 2000% |
| Callback queue size | 100 messages |

---

## Design Decisions

1. **Non-modal dialog** - User can interact with Sample View while dialog is open
2. **Quick Z-stack** - Uses variance of Laplacian for best-focus selection
3. **No tile overlap** - Adjacent tiles for simpler assembly (rough overview)
4. **Preset integration** - Load saved positions from Stage Control
5. **Stage limit validation** - Spinboxes constrained to legal coordinate ranges
6. **XY coordinates on tiles** - More useful than Z for navigation planning

---

## Text Overlay Sizing: Lessons Learned (2025-12-28)

### The Problem

The XY coordinate text on tiles appeared extremely small (less than 1/10th of tile height) despite code setting it to 15-40% of tile height.

### Failed Attempts

| Attempt | Code | Result | Why It Failed |
|---------|------|--------|---------------|
| 1 | `max(12, int(tile_h * 0.15))` | Tiny text | 15% was too small |
| 2 | `max(12, int(tile_h * 0.40))` | Still tiny | Unknown - possibly caching |
| 3 | `max(50, int(tile_h * 0.25))` | Still tiny | The `max(50, ...)` was suspicious |
| 4 | `max(50, int(tile_h * 0.40))` | Still tiny | Same issue |

### The Fix That Worked

```python
font_pixel_size = int(tile_h * 0.35)  # Direct percentage, no max()
font_pixel_size = max(font_pixel_size, 20)  # Only enforce absolute minimum
```

This produced text that was **too large** (35% of tile height), confirming the calculation was finally working. Final value settled at **17%** of tile height.

### Root Cause Analysis

The exact root cause is unclear, but contributing factors were:

1. **Arbitrary minimum capping**: `max(50, int(tile_h * 0.15))` - if tile_h was 500px, this gave 75px, which *should* have been visible. The max() wasn't the problem.

2. **Possible code caching or git sync issues**: Changes may not have been applied immediately on the test system.

3. **Font choice**: Changed from `QFont("Courier")` to `QFont("Arial")` which may render more predictably.

4. **Logging visibility**: Added `logger.info()` to print actual dimensions, which helped confirm calculations were correct.

### Key Insights

1. **The overlay is drawn on the original pixmap at full resolution**, then the entire pixmap is scaled down for display. A 175px font in a 5000px image displayed at 500px becomes a 17.5px font visually.

2. **tile_h is derived from pixmap dimensions**: `tile_h = pixmap.height() / tiles_y`. If the stitched image is large and there are few tiles, tile_h is large.

3. **setPixelSize() works correctly**: When we set 35% of tile_h, we got huge text. The Qt font rendering was not the issue.

### Future Overlay/Text Manipulation Guidelines

When modifying text or overlay elements in `led_2d_overview_result.py`:

1. **Always add logging** to verify actual dimensions:
   ```python
   logger.info(f"pixmap={w}x{h}, tiles={tiles_x}x{tiles_y}, "
               f"tile_size={tile_w:.0f}x{tile_h:.0f}px, font={font_pixel_size}px")
   ```

2. **Use direct percentages** without complex min/max logic:
   ```python
   font_pixel_size = int(tile_h * 0.17)  # 17% of tile height
   font_pixel_size = max(font_pixel_size, 12)  # Only absolute minimum
   ```

3. **Test with extreme values first**: If text appears too small, try 50% or higher to confirm the code path is working at all.

4. **Consider display scaling**: Text drawn on the original image will be scaled down with the image. A font that looks reasonable at 100% zoom may be unreadable at 10% zoom.

5. **Use Arial over Courier**: Arial renders more predictably across systems.

6. **Check git sync**: Ensure changes are actually deployed to the test system.

### Current Settings (2025-12-28)

| Parameter | Value | Notes |
|-----------|-------|-------|
| Font | Arial, Bold | More predictable rendering |
| Font size | 17% of tile_h | Two lines fit comfortably |
| Minimum | 12px | Only for extremely small tiles |
| Line spacing | Based on QFontMetrics.height() | Proper vertical centering |

---

## Fast Scan Mode (2025-12-28)

### Overview

Added `fast_mode` option to `ScanConfiguration` (default: `True`) that uses continuous Z sweeps instead of step-by-step Z-stack capture.

### How It Works

**Standard Mode (fast_mode=False):**
- Move to XY position, wait
- For each Z position: move, wait 20ms, grab frame
- Compute projections
- ~50+ seconds for 100 tiles

**Fast Mode (fast_mode=True):**
- Move to XY position
- Sweep Z continuously with 15ms delays between grabs
- Compute projections from captured frames
- Serpentine XY pattern for efficient stage motion
- Still captures full Z sweep for min/max/mean projections

### Configuration

```python
@dataclass
class ScanConfiguration:
    ...
    fast_mode: bool = True  # Use continuous Z sweeps
```

---

## Recent Updates (2026-01-05 to 2026-01-06)

### 1. Tile Scan Order Optimization (e8c2137)

Changed tile scanning to use X as the outer (slowest) loop instead of Y.

**Rationale:** Long thin specimens oriented along Y axis experience less wobble when the stage moves continuously in Y (fast axis) and steps in X (slow axis). This reduces motion artifacts during acquisition.

**Before:** Y outer loop (scan columns)
**After:** X outer loop (scan rows)

### 2. Live View Warning in Dialog (e8c2137)

Added a visible warning label in the Scan Info section:

```
⚠ Note: Live View must be active with LED enabled to detect FOV.
   Click Refresh after enabling.
```

This helps users understand why FOV detection fails when Live View is not running.

### 3. Tile Click Performance Optimization (68d4781)

**Problem:** Clicking tiles to select them for workflow collection was extremely slow (multi-second delays). Each click regenerated the entire pixmap from the numpy array, redrew all grid lines, and re-rendered all coordinate text.

**Solution:** Implemented a cached base pixmap strategy:
- Base pixmap (image + grid + coordinates) is cached after initial render
- Tile selection clicks only copy the cached base and draw selection rectangles
- Base is invalidated only when image/grid/coords actually change

**Result:** Tile selection is now nearly instantaneous.

**Key code pattern:**
```python
def _get_base_pixmap(self) -> QPixmap:
    """Get or create the cached base pixmap (image + grid + coords)."""
    if self._cached_base_pixmap is None:
        # Create base pixmap with image, grid overlay, and coordinates
        self._cached_base_pixmap = self._create_base_pixmap()
    return self._cached_base_pixmap

def _invalidate_base_cache(self):
    """Invalidate cached base pixmap when image/grid/coords change."""
    self._cached_base_pixmap = None

def _update_display_with_selections(self):
    """Update display by drawing selections on cached base."""
    base = self._get_base_pixmap().copy()  # Copy, don't modify cache
    # Draw only selection rectangles on the copy
    self._draw_selection_overlay(base)
    self.setPixmap(base)
```

### 4. Start Scan Button with Progress Percentage (2026-01-06)

**Problem:** After clicking "Start Scan", there was no visual feedback that the scan was running. Users had no indication that things were working until results appeared.

**Solution:** Button now shows real-time progress percentage during scan:

| State | Button Text | Color | Enabled |
|-------|-------------|-------|---------|
| Ready | "Start Scan" | Green (#4CAF50) | Yes |
| Running | "In Progress... 0%" → "In Progress... 100%" | Amber (#f1c21b) | No |
| Complete | "Start Scan" | Green (#4CAF50) | Re-validated |

**Implementation:**

The dialog connects to the workflow's `tile_completed` signal which provides progress info:

```python
# Connect to workflow progress signal
self._workflow.tile_completed.connect(self._on_tile_completed)

def _on_tile_completed(self, rotation_idx: int, tile_idx: int, total_tiles: int) -> None:
    """Handle tile completion - update progress display."""
    # Get actual number of rotations (1 if tip not calibrated, 2 otherwise)
    num_rotations = 2
    if self._workflow and hasattr(self._workflow, '_rotation_angles'):
        num_rotations = len(self._workflow._rotation_angles)

    # Calculate overall progress
    tiles_done = rotation_idx * total_tiles + tile_idx + 1
    total_all_rotations = total_tiles * num_rotations
    percent = int((tiles_done / total_all_rotations) * 100)
    self._set_scan_in_progress(True, percent)

def _set_scan_in_progress(self, in_progress: bool, percent: int = 0) -> None:
    """Update button appearance to reflect scan state."""
    if in_progress:
        self.start_btn.setText(f"In Progress... {percent}%")
        self.start_btn.setStyleSheet(
            f"QPushButton {{ background-color: {WARNING_COLOR}; ... }}"
        )
    else:
        self.start_btn.setText("Start Scan")
        self._update_start_button_state()
```

**Key details:**
- Uses existing `tile_completed` signal from workflow (no workflow changes needed)
- Correctly handles 1 or 2 rotations based on tip calibration status
- Percentage updates after each tile completes
- Pattern mirrors Sample View's "Start Live" / "Stop Live" toggle button
- **Debugging:** Added extensive logging to track signal emission and reception

### 5. Cancel Scan Button (2026-01-07)

**Problem:** Once a scan starts, there's no way to stop it. The Close button is ignored during scanning, forcing users to wait for completion even if they realize they made a configuration error.

**Solution:** Added a "Cancel Scan" button that appears during scanning:

| Scan State | Start Button | Cancel Button | Close Button |
|------------|--------------|---------------|--------------|
| Idle | Visible, enabled | Hidden | Enabled |
| Running | "In Progress... X%", disabled | Visible, enabled (red) | Disabled |
| Complete | Visible, enabled | Hidden | Enabled |

**Implementation:**
```python
# In UI setup:
self.cancel_btn = QPushButton("Cancel Scan")
self.cancel_btn.setStyleSheet(
    f"QPushButton {{ background-color: {ERROR_COLOR}; ... }}"  # Red
)
self.cancel_btn.clicked.connect(self._on_cancel_clicked)
self.cancel_btn.setVisible(False)  # Hidden until scan starts

# In _set_scan_in_progress():
if in_progress:
    self.cancel_btn.setVisible(True)
    self.close_btn.setEnabled(False)
else:
    self.cancel_btn.setVisible(False)
    self.close_btn.setEnabled(True)

# Handler:
def _on_cancel_clicked(self) -> None:
    """Handle Cancel Scan button click."""
    if self._workflow:
        self._workflow.cancel()
        # scan_cancelled signal triggers cleanup
```

**Behavior:**
- Cancel button appears when scan starts
- Calls workflow's `cancel()` method
- Workflow emits `scan_cancelled` signal
- Dialog cleans up via `_on_workflow_completed()` handler
- Button state returns to idle

### 6. Visual Highlighting for Incomplete Sections (2026-01-07)

**Problem:** Users couldn't easily see which sections were preventing the "Start Scan" button from being enabled. They had to hover over the disabled button to read the tooltip explaining what was missing.

**Solution:** Added subtle visual highlighting to sections that need attention:

**Validation Requirements:**
1. **Bounding Points** - At least 2 points (A and B) with non-degenerate bounding box
2. **Imaging** - LED selected in Sample View AND Live View active

**Visual Feedback:**
- Incomplete sections get a thin amber border (2px, `WARNING_COLOR`)
- Section title changes to amber color
- Border disappears when requirements are met
- Non-intrusive: no backgrounds, icons, or flashing

**Implementation:**
```python
def _update_section_highlighting(self):
    """Update visual highlighting on sections that need attention."""
    # Check bounding points
    bbox = self._get_bounding_box()
    points_incomplete = bbox is None or (bbox.width < 0.001 and bbox.height < 0.001)

    if points_incomplete:
        self.points_group.setStyleSheet(
            f"QGroupBox {{ border: 2px solid {WARNING_COLOR}; "
            "border-radius: 4px; padding-top: 10px; margin-top: 6px; }}"
            f"QGroupBox::title {{ color: {WARNING_COLOR}; }}"
        )
    else:
        self.points_group.setStyleSheet("")

    # Check imaging (LED + Live View)
    imaging_incomplete = led_not_selected or live_view_inactive
    # Apply same highlighting pattern to imaging_group
```

**Called automatically when:**
- Dialog opens (via `_update_start_button_state()`)
- User enters/changes bounding points
- User clicks "Refresh from Sample View"
- Any validation state changes

**User Experience:**
1. Open dialog → See amber border on both "Bounding Points" and "Imaging" sections
2. Enter Point A and B → "Bounding Points" border disappears
3. Start Live View in Sample View → "Imaging" border remains (no LED yet)
4. Enable LED and click Refresh → "Imaging" border disappears
5. "Start Scan" button becomes enabled

### 7. Coordinate Bounds Validation (2026-01-08)

**Problem:** Users could fill in Point A with valid coordinates, leaving Point B at its default values (0.00, 0.00, 0.00). If these default values were outside the microscope's valid stage range, the validation would incorrectly allow the scan to proceed, or the highlighting would disappear prematurely, giving false feedback that the configuration was complete.

**Solution:** Added explicit coordinate bounds validation to check that all Point A and Point B coordinates are within the microscope's valid X/Y/Z stage limits.

**Validation Logic:**
```python
def _are_coordinates_valid(self) -> bool:
    """Check if Point A and Point B coordinates are within valid stage bounds."""
    x_limits = self._stage_limits['x']
    y_limits = self._stage_limits['y']
    z_limits = self._stage_limits['z']

    # Check each coordinate against its respective limits
    point_a_valid = (
        x_limits['min'] <= self.point_a_x.value() <= x_limits['max'] and
        y_limits['min'] <= self.point_a_y.value() <= y_limits['max'] and
        z_limits['min'] <= self.point_a_z.value() <= z_limits['max']
    )

    point_b_valid = (
        x_limits['min'] <= self.point_b_x.value() <= x_limits['max'] and
        y_limits['min'] <= self.point_b_y.value() <= y_limits['max'] and
        z_limits['min'] <= self.point_b_z.value() <= z_limits['max']
    )

    # Log warnings for debugging
    if not point_a_valid or not point_b_valid:
        self._logger.warning(f"Invalid coordinates detected with ranges: "
                           f"X[{x_limits['min']}-{x_limits['max']}], ...")

    return point_a_valid and point_b_valid
```

**Integration:**
1. **Validation Check** - `_validate_configuration()` checks coordinate validity FIRST
   - Returns clear error message showing valid ranges if coordinates are invalid
   - Example: "One or more coordinates are outside valid stage bounds. Valid ranges: X[2.0-24.0], Y[3.5-22.5], Z[0.0-26.0]"

2. **Visual Highlighting** - `_update_section_highlighting()` checks coordinate validity
   - Amber border persists on "Bounding Points" section if any coordinate is out of bounds
   - Works alongside existing checks for bounding box dimensions

3. **Logging** - Invalid coordinates are logged with specific values and valid ranges
   - Helps users identify typos or configuration errors
   - Example: "Point B has invalid coordinates: X=0.000 (valid: 2.0-24.0), Y=0.000 (valid: 3.5-22.5), Z=0.000 (valid: 0.0-26.0)"

**Behavior:**
- If Point B is left at default (0.00, 0.00, 0.00) and these values are outside the microscope's actual stage bounds:
  - "Bounding Points" section remains highlighted in amber
  - "Start Scan" button stays disabled
  - Hover tooltip shows helpful error message with valid ranges
- User must explicitly set Point B to valid coordinates within the microscope's range
- Catches typos like entering 25.5 when max is 24.0

**Stage Limits Loading:**
Stage limits are loaded from microscope settings in `_load_stage_limits()`:
- Called before UI setup to ensure spinboxes use correct ranges
- Falls back to default (0-26 mm) if microscope settings unavailable
- Validation uses current `_stage_limits` values, catching discrepancies

**Commit:** 55b0339

---

## Future Enhancements

1. **Tile overlap with blending** - For smoother stitched images
2. **Save/load scan configurations** - Remember frequently used regions
3. **Export results** - Save assembled overview image
4. ~~**Progress bar** - Visual feedback during scan~~ (Partially addressed with button state)
5. **Custom rotation angles** - Not just R and R+90
