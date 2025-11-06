# Stage Control Implementation - Phase 1

**Date:** 2025-11-06
**Status:** Phase 1 Complete - Ready for Testing

---

## Overview

Implemented a new Stage Control interface for controlling microscope stage position, starting with rotation control as the safest axis to test (no risk of hitting chamber walls).

## Phase 1: Rotation Control Implementation

### 1. Fixed Axis Codes ✓

**File:** `src/py2flamingo/services/stage_service.py`

Updated `AxisCode` class to match the old code's axis mapping:
```python
X_AXIS = 1  # (was 0)
Y_AXIS = 2  # (was 1)
Z_AXIS = 3  # (was 2)
ROTATION = 4  # (was 3)
```

**Source:** From `oldcodereference/microscope_connect.py` lines 108-111

### 2. Created StageControlView ✓

**File:** `src/py2flamingo/views/stage_control_view.py` (NEW)

Features:
- **Current Position Display**: Shows X, Y, Z (mm) and R (degrees)
- **Rotation Control Section**:
  - Input field for target rotation (0-360°)
  - "Move to Rotation" button
  - Status display (Ready/Moving/Connected/Disconnected)
  - Informational text explaining rotation is safest axis
- **Status and Error Messages**: Color-coded feedback
- **Connection-aware**: Button disabled when not connected

### 3. Added move_rotation() Method ✓

**File:** `src/py2flamingo/controllers/position_controller.py`

```python
def move_rotation(self, rotation_degrees: float) -> None:
    """Move only the rotation axis to the specified angle."""
```

Features:
- Validates rotation is in range [0, 360]
- Acquires movement lock to prevent concurrent movements
- Checks connection status
- Calls `_move_axis(axis.R, rotation_degrees, "Rotation")`
- Updates tracked position after movement
- Releases lock in finally block

### 4. Integrated into Application ✓

**Files Modified:**
- `src/py2flamingo/views/__init__.py` - Export StageControlView
- `src/py2flamingo/main_window.py` - Add Stage Control tab
- `src/py2flamingo/application.py` - Wire everything together

**Integration Points:**
- StageControlView created in `setup_dependencies()`
- Connected to `connection_established` and `connection_closed` signals
- Position display updates on connection
- Button enables/disables based on connection status
- Added as new tab: "Stage Control"

---

## Testing Instructions

### Prerequisites
1. Microscope must be connected
2. Initial position should be loaded from settings (home position)
3. Stage should be within chamber bounds before rotating

### Test Procedure

1. **Start Application**
   ```bash
   cd /home/msnelson/LSControl/Flamingo_Control
   PYTHONPATH=src .venv/bin/python -m py2flamingo.application
   ```

2. **Connect to Microscope**
   - Go to "Connection" tab
   - Enter IP/Port: `127.0.0.1:53717`
   - Click "Connect"
   - Wait for "Connected" status

3. **Navigate to Stage Control**
   - Click "Stage Control" tab
   - Verify current position is displayed
   - Verify "Move to Rotation" button is enabled
   - Status should show "Connected - Ready to move"

4. **Test Rotation Movement**
   - Enter a rotation angle: `45.0`
   - Click "Move to Rotation"
   - **Expected behavior:**
     - Status changes to "Moving Rotation..."
     - Success message: "Moving to rotation 45.00°..."
     - Position display updates: R = 45.00
     - Button disables for ~2 seconds
     - After 2 seconds, status returns to "Ready"

5. **Monitor Microscope Logs**
   ```bash
   tail -f /path/to/microscope/logs
   ```
   - Should see command `0x00006005, 24581, stage set position (slide control)`
   - Should see `int32Data0 = 4` (rotation axis)
   - Should see `doubleData = 45.0` (target rotation)
   - Should see motion thread starting
   - Should see "motion stopped" callback when complete

6. **Test Error Handling**
   - Try entering `-10` → Should show error: "Rotation must be between 0 and 360 degrees"
   - Try entering `500` → Should show error
   - Try entering `abc` → Should show error: "Invalid rotation value"
   - Disconnect microscope → Button should disable, status shows "Disconnected"

---

## Current Position Tracking

The system tracks position in software since the microscope hardware doesn't provide position feedback.

### Initialization
Position is initialized from `microscope_settings/ScopeSettings.txt`:
```
[Stage limits]
Home x-axis = 6.5
Home y-axis = 10.0
Home z-axis = 19.0
Home r-axis = 0.0
```

### Updates
Position is updated after each movement command:
- `move_rotation()` updates `_current_position.r`
- `go_to_position()` updates all axes
- `get_current_position()` returns tracked position

---

## Safety Features

### Phase 1 Safety
1. **Rotation Only**: First implementation uses rotation (safest axis)
2. **Bounds Checking**: Validates rotation 0-360°
3. **Movement Lock**: Prevents concurrent movement commands
4. **Connection Check**: Verifies connection before sending commands

### Future Safety (Phase 2+)
- [ ] Chamber boundary checking for X, Y, Z axes
- [ ] Motion-stopped callback handling
- [ ] Emergency stop button
- [ ] Safe limits configuration UI

---

## Known Limitations

### 1. Motion Completion Detection
Currently uses a 2-second timer to reset "Moving" status. This is a temporary solution.

**Reason:** Microscope sends unsolicited "motion stopped" callback (command 0x6010) when movement completes, but callback listener is not yet implemented.

**Next Step:** Implement motion-stopped callback listener (Phase 3)

### 2. No Visual Feedback During Motion
User doesn't see real-time position updates during movement.

**Why:** Position is tracked in software, not queried from hardware. We update position immediately to the target value.

**Future:** Could implement position interpolation for visual feedback

### 3. Position Accuracy
Position display shows the **commanded** position, not actual hardware position.

**Why:** `STAGE_POSITION_GET` command doesn't work (hardware doesn't support position readback per oldcodereference testing)

**Mitigation:** Position tracking works as long as:
- Initial home position is correct
- No manual movements outside software
- No power loss/reset

---

## File Changes Summary

### New Files
- `src/py2flamingo/views/stage_control_view.py` - Stage Control UI

### Modified Files
- `src/py2flamingo/services/stage_service.py` - Fixed axis codes, added imports
- `src/py2flamingo/controllers/position_controller.py` - Added `move_rotation()` method
- `src/py2flamingo/views/__init__.py` - Export StageControlView
- `src/py2flamingo/main_window.py` - Add Stage Control tab
- `src/py2flamingo/application.py` - Wire StageControlView, add connection handlers

---

## Next Steps

### Phase 2: Bounds Checking and Safety
1. Load stage limits from configuration
2. Implement bounds validation in `PositionController._validate_position()`
3. Add visual indicators for safe zones
4. Add X, Y, Z axis controls with bounds checking

### Phase 3: Motion Completion
1. Implement motion-stopped callback listener
2. Update position only after motion complete
3. Remove 2-second timer hack
4. Add progress feedback during movement

### Phase 4: Advanced Features
1. 3D visualization of sample chamber and stage position
2. Preset positions (home, favorite positions)
3. Movement history/undo
4. Joystick/keyboard control for fine adjustments

---

## Testing Checklist

- [ ] Application launches without errors
- [ ] Stage Control tab appears in UI
- [ ] Position display shows initial home position on connect
- [ ] Rotation input field accepts numeric values
- [ ] Button disabled when disconnected
- [ ] Button enabled when connected
- [ ] Rotation movement command sent successfully
- [ ] Position display updates after movement
- [ ] Error messages display for invalid input
- [ ] Movement lock prevents concurrent movements
- [ ] Microscope logs show correct command/parameters
- [ ] Stage actually rotates to commanded angle
- [ ] Motion-stopped callback received (check logs)

---

## Architecture

```
User Input (StageControlView)
    ↓
Validate & Show Feedback
    ↓
PositionController.move_rotation()
    ↓
Acquire Lock, Check Connection
    ↓
_move_axis(R, angle, "Rotation")
    ↓
MVCConnectionService.send_command()
    ↓
TCP Socket → Microscope
    ↓
Acknowledgment ← Microscope
    ↓
Update Position Tracking
    ↓
Release Lock
    ↓
Update View (success/error)
```

**Asynchronous Motion:**
```
Command Sent → Microscope moves in background
                    ↓
              Motion complete
                    ↓
         Unsolicited callback 0x6010
              (not yet handled)
```

---

## Conclusion

Phase 1 provides a safe, working implementation for rotation control. The foundation is in place for adding X, Y, Z controls with proper bounds checking and motion completion detection in future phases.

**Status:** ✅ Ready for testing on remote PC with microscope connection
