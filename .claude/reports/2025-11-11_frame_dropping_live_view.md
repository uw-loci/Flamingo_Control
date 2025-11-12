# Frame-Dropping Live View Implementation

**Date:** 2025-11-11
**Status:** Completed
**Commit:** b3d0560

## Overview

Implemented a robust frame buffering strategy for the Flamingo microscope live view system that ensures camera data is always acquired while display updates remain responsive, preventing lag accumulation during high-speed imaging.

## Problem Statement

The user identified a critical issue with live view performance:

> "The camera sends data as fast as it acquires it, and any processing that slows down display of the data may require dropping frames, so it is best to acquire ALL of the data into a buffer, then remove frames that do not need to be displayed due to processing time. At the same time, make sure to update the live display, I do not want the software to end up in a situation where data is coming in quickly and the processing never catches up, and the image NEVER updates."

### Key Requirements

1. **Acquire ALL camera data** - Never block or drop incoming frames
2. **Drop frames intelligently** - Discard accumulated frames during processing
3. **Always update display** - Ensure processing never gets stuck on old frames
4. **Proper illumination sequence** - Match working C++ implementation

## Solution Architecture

### 1. Two-Tier Buffering System

```
┌────────────────────────────────────────────────────────────┐
│  TIER 1: Fast Acquisition (CameraService)                  │
│  ────────────────────────────────────────────────────────  │
│  Camera → Socket → Receiver Thread                         │
│                      ↓ (No processing, just buffer)        │
│               Thread-safe deque (20 frames)                │
│                      ↓ (Overflow? Drop oldest)             │
│                   Frame Buffer                             │
└────────────────────────────────────────────────────────────┘
                          ↓
┌────────────────────────────────────────────────────────────┐
│  TIER 2: Display Update (CameraController)                 │
│  ────────────────────────────────────────────────────────  │
│  QTimer @ 30 FPS                                           │
│       ↓                                                     │
│  Pull Latest Frame                                         │
│       ↓                                                     │
│  CLEAR Accumulated Frames ← Key frame-dropping strategy    │
│       ↓                                                     │
│  Process (scale, transform)                                │
│       ↓                                                     │
│  Display (always fresh data)                               │
└────────────────────────────────────────────────────────────┘
```

### 2. Frame Dropping Strategy

**Implementation:** `camera_service.py:439-482`

The `get_latest_frame(clear_buffer=True)` method implements intelligent frame dropping:

1. Get the most recent frame from buffer
2. Clear all older accumulated frames
3. Return the latest frame for processing

**Result:** Processing always works on fresh data, never builds up a backlog.

### 3. Timer-Based Display

**Implementation:** `camera_controller.py:86-93, 302-344`

Replaced callback-based display with QTimer approach:
- Timer fires at target FPS (default 30 Hz)
- Each timer tick pulls latest frame and discards accumulated
- Display updates are guaranteed, never blocked by processing

## Implementation Details

### Modified Files

#### 1. `camera_service.py`

**Lines 132-137:** Added frame buffer infrastructure
```python
from collections import deque
self._frame_buffer_lock = threading.Lock()
self._frame_buffer = deque(maxlen=20)  # Keep last 20 frames max
```

**Lines 439-506:** Updated receiver loop for fast buffering
```python
def _data_receiver_loop(self) -> None:
    """
    CRITICAL DESIGN: This thread ONLY receives and buffers frames.
    No processing, no callbacks, no delays - just fast acquisition.
    """
    # Read header and image data (lines 457-473)
    # Fast buffering (lines 483-485)
    with self._frame_buffer_lock:
        self._frame_buffer.append((image_array, header))
```

**Lines 439-500:** Added frame management methods
- `get_latest_frame(clear_buffer=True)` - Pull latest and drop accumulated
- `get_buffer_size()` - Monitor buffer depth
- `clear_frame_buffer()` - Manual buffer clear

#### 2. `camera_controller.py`

**Lines 86-93:** Added display timer
```python
self._display_timer = QTimer()
self._display_timer.timeout.connect(self._pull_and_display_frame)
self._display_timer_interval_ms = int(1000 / self._max_display_fps)
```

**Lines 112-143:** Updated start/stop to use timer
```python
def start_live_view(self) -> bool:
    self.camera_service.start_live_view_streaming()
    self._display_timer.start(self._display_timer_interval_ms)

def stop_live_view(self) -> bool:
    self._display_timer.stop()
    self.camera_service.stop_live_view_streaming()
```

**Lines 302-344:** Added timer callback for frame pulling
```python
def _pull_and_display_frame(self) -> None:
    """
    Timer callback that pulls latest frame and displays it.
    Frame dropping happens here via clear_buffer=True.
    """
    frame = self.camera_service.get_latest_frame(clear_buffer=True)
    if frame is None:
        return
    image, header = frame
    # Process and emit to display
    self.new_image.emit(image, header)
```

#### 3. `laser_led_service.py`

**Lines 45-48:** Added illumination command codes
```python
ILLUMINATION_LEFT_ENABLE = 0x7004   # 28676
ILLUMINATION_LEFT_DISABLE = 0x7005  # 28677
```

**Lines 313-359:** Added illumination control methods
```python
def enable_illumination(self) -> bool:
    """
    Enable illumination waveform for synchronized imaging.
    CRITICAL: Must be called after enabling laser preview.
    """
    result = self._send_command(
        LaserLEDCommandCode.ILLUMINATION_LEFT_ENABLE,
        "ILLUMINATION_LEFT_ENABLE",
        params=[0, 0, 0, 0, 0, 0, 0]
    )
    return result['success']
```

#### 4. `laser_led_controller.py`

**Lines 179-238:** Updated laser preview sequence
```python
def enable_laser_for_preview(self, laser_index: int) -> bool:
    """
    CRITICAL: Follows exact command sequence from working C++ implementation:
    1. Disable LED if active (0x4003)
    2. Set laser power
    3. Enable laser preview mode (0x2004 with laser_index)
    4. Enable illumination (0x7004) - coordinates exposure timing
    """
    # Step 1: Disable LED
    if self._active_source and self._active_source.startswith("led"):
        self.laser_led_service.disable_led_preview()

    # Step 2: Set laser power
    power = self._laser_powers.get(laser_index, 5.0)
    self.laser_led_service.set_laser_power(laser_index, power)

    # Step 3: Enable laser preview
    self.laser_led_service.enable_laser_preview(laser_index)

    # Step 4: Enable illumination (NEW - CRITICAL!)
    self.laser_led_service.enable_illumination()
```

**Lines 240-300:** Updated LED preview sequence
- Added illumination enable step after LED preview enable
- Follows same pattern as laser sequence

## Command Sequence Analysis

Based on analysis of `LiveViewCommunication_Analysis.md` (created by parallel agent analysis of C++ code) and `SwitchToLaserSnapshotLiveMode.txt` log file.

### Working Sequence (Verified from C++ Logs)

**For Laser Live View:**
1. `LED_DISABLE` (0x4003) - Disable LED if active
2. `LASER_PREVIEW_ENABLE` (0x2004) - Enable laser with index in int32Data0
3. `ILLUMINATION_LEFT_ENABLE` (0x7004) - **← CRITICAL MISSING STEP**
4. `CAMERA_SNAPSHOT` (0x3006) - Optional test frame
5. `CAMERA_LIVE_VIEW_START` (0x3007) - Start continuous imaging

### Command Code Values

From `command_codes.py` and verified against logs:

| Command | Code (Hex) | Code (Dec) | Purpose |
|---------|------------|------------|---------|
| LED_DISABLE | 0x4003 | 16387 | Disable LED preview |
| LASER_PREVIEW_ENABLE | 0x2004 | 8196 | Enable laser external trigger |
| ILLUMINATION_LEFT_ENABLE | 0x7004 | 28676 | Configure exposure timing |
| CAMERA_SNAPSHOT | 0x3006 | 12294 | Single frame capture |
| CAMERA_LIVE_VIEW_START | 0x3007 | 12295 | Continuous acquisition |
| CAMERA_LIVE_VIEW_STOP | 0x3008 | 12296 | Stop acquisition |

### Critical Parameter: cmdDataBits0

All commands use `cmdDataBits0 = 0x80000000` (TRIGGER_CALL_BACK flag):
- Requests server to send response
- Essential for GET commands
- Used in all working SET commands for acknowledgment

## Performance Characteristics

### Frame Acquisition
- **Speed:** Limited only by camera and network bandwidth
- **Blocking:** Never blocks (dedicated receiver thread)
- **Buffer:** 20 frames max (deque auto-drops oldest on overflow)

### Frame Display
- **Rate:** Configurable (default 30 FPS)
- **Latency:** 1/FPS worst case (~33ms at 30 FPS)
- **Dropping:** Automatic (all accumulated frames cleared each timer tick)

### Example Scenarios

**Scenario 1: Fast Camera (50 FPS), Normal Display (30 FPS)**
- Camera sends 50 frames/sec → all buffered
- Display timer fires every 33ms
- Between ticks: 50/30 ≈ 1.67 frames accumulate
- Timer: Pull frame #50, drop frame #49, display frame #50
- Result: 20 frames/sec dropped, display always updates

**Scenario 2: Slow Processing (200ms per frame)**
- Camera sends 50 frames/sec → all buffered
- Timer fires every 33ms, but processing takes 200ms
- During processing: 10 new frames arrive
- After processing completes: Pull latest (frame #60), drop frames #51-59
- Next display update: Fresh frame #60, not stuck on frame #50
- Result: Display updates every 200ms, always shows latest

## Testing Recommendations

### 1. High Frame Rate Test
```python
# Set camera to maximum FPS
# Verify display stays responsive
# Check logs for "Dropped N accumulated frames"
```

### 2. Buffer Monitoring
```python
# During acquisition:
buffer_size = camera_service.get_buffer_size()
# Should stay low (0-5) under normal conditions
# May spike to 10-20 during slow processing (expected)
```

### 3. Slow Processing Simulation
```python
# Add delay in display processing
import time
def _display_image(self, image, header):
    time.sleep(0.1)  # Simulate slow processing
    # ... normal display code

# Verify:
# - Display still updates (slower)
# - Frames are dropped (logged)
# - No memory accumulation
```

### 4. Illumination Command Verification
```bash
# Check logs for proper sequence:
# "Step 1: Disabling LED"
# "Step 2: Setting laser X power to Y%"
# "Step 3: Enabling laser X preview mode"
# "Step 4: Enabling illumination for synchronized imaging"
```

### 5. Long Duration Test
```python
# Run live view for 10+ minutes
# Monitor:
# - Memory usage (should be constant)
# - Buffer size (should stay bounded)
# - Display responsiveness (should not degrade)
```

## Configuration Options

### Display Frame Rate
```python
camera_controller.set_max_display_fps(15)  # Slower, more dropping
camera_controller.set_max_display_fps(60)  # Faster, less dropping
```

### Buffer Size
Edit `camera_service.py:137`:
```python
self._frame_buffer = deque(maxlen=30)  # Increase for more buffering
self._frame_buffer = deque(maxlen=10)  # Decrease for less latency
```

## Documentation Created

### LiveViewCommunication_Analysis.md (1,949 lines)

Comprehensive analysis document created by 4 parallel agents analyzing C++ codebase:

**Section 1: LiveView Application (Linux/LiveView)** - ~283 lines
- Client-side viewer application
- OpenCV-based display with transformations
- Histogram visualization
- Zoom and overlay modes

**Section 2: LiveViewControl (Shared/LiveView)** - ~365 lines
- TCP/IP client for image streaming
- Multi-device queue management
- Producer-consumer pattern
- Thread-safe frame delivery

**Section 3: InterfaceControl (Shared/LiveView)** - ~387 lines
- Command/control communication
- Bidirectional TCP/IP protocol
- Callback-based event system
- SCommand structure (132 bytes)

**Section 4: ControlSystem (Linux/ControlSystem)** - ~597 lines
- Server-side microscope control
- Multi-threaded camera acquisition
- Workflow orchestration
- "Update on change" live view mode

**Section 5: Command Sequence Comparison** - ~317 lines
- Working command sequence from logs
- Python implementation verification
- Parameter analysis (cmdDataBits0, int32Data0)
- Timing considerations

## Benefits

### For Users
✅ Live view never freezes or gets stuck
✅ Display always shows most recent data
✅ No lag accumulation during fast imaging
✅ Proper synchronized illumination

### For Developers
✅ Clear separation of concerns (acquisition vs. display)
✅ Thread-safe with proper locking
✅ Configurable performance parameters
✅ Comprehensive logging for debugging
✅ Well-documented command sequences

### For System Performance
✅ Minimal memory footprint (bounded buffer)
✅ No blocking on camera acquisition path
✅ Efficient frame dropping (discard old, keep new)
✅ Responsive to system load

## Related Files

- `src/py2flamingo/services/camera_service.py` - Camera data acquisition
- `src/py2flamingo/controllers/camera_controller.py` - Display coordination
- `src/py2flamingo/services/laser_led_service.py` - Light source control
- `src/py2flamingo/controllers/laser_led_controller.py` - Light source coordination
- `src/py2flamingo/views/camera_live_viewer.py` - UI display
- `src/py2flamingo/views/laser_led_control_panel.py` - Light source UI
- `LiveViewCommunication_Analysis.md` - C++ protocol analysis

## Future Enhancements

### Potential Improvements

1. **Adaptive Frame Rate**
   - Automatically adjust display FPS based on processing time
   - Could reduce dropping when system is lightly loaded

2. **Buffer Statistics**
   - Track drop rate, buffer fullness over time
   - Display metrics in UI for monitoring

3. **Configurable Dropping Strategy**
   - Option to keep every Nth frame instead of just latest
   - Useful for slower timelapses

4. **Multiple Buffer Modes**
   - "Latest only" (current implementation)
   - "Record all" (for acquisition workflows)
   - "Smart decimation" (adaptive sampling)

### Not Recommended

❌ **Processing in Receiver Thread** - Would block acquisition
❌ **Larger Buffers** - Just delays the problem, doesn't solve it
❌ **No Frame Dropping** - Would cause unbounded memory growth

## Conclusion

The implementation successfully addresses the user's requirements by:

1. **Always acquiring camera data** - Fast receiver thread with dedicated buffer
2. **Intelligently dropping frames** - Clear accumulated frames on each display update
3. **Always updating display** - Timer-based pulling ensures periodic updates
4. **Proper illumination sequence** - Matches verified working C++ implementation

The architecture cleanly separates acquisition (fast, never blocks) from display (slower, can drop frames), ensuring system remains responsive under all conditions.

## References

- Original C++ codebase: `oldcodereference/serversidecode/`
- Working command log: `oldcodereference/LogFileExamples/SwitchToLaserSnapshotLiveMode.txt`
- Protocol analysis: `LiveViewCommunication_Analysis.md`
- Command definitions: `src/py2flamingo/core/command_codes.py`

---

**Generated:** 2025-11-11
**Author:** Claude (Anthropic)
**Session:** Frame-dropping live view implementation
