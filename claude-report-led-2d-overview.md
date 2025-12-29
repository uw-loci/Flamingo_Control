# Claude Report: LED 2D Overview Feature

**Date:** 2025-12-16 to 2025-12-18
**Commits:** 640d84a, 0c80f00, 1177270 (and others)

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

## Future Enhancements

1. **Tile overlap with blending** - For smoother stitched images
2. **Save/load scan configurations** - Remember frequently used regions
3. **Export results** - Save assembled overview image
4. **Progress bar** - Visual feedback during scan
5. **Custom rotation angles** - Not just R and R+90
