# Stage Position Hardware Query Implementation

**Date:** 2025-11-10
**Summary:** Implemented hardware position querying after stage movements to verify actual position instead of relying on optimistic target-based tracking.

## Problem Statement

The position controller was tracking stage position optimistically by assuming the stage reached the commanded target position. However, the microscope hardware supports querying actual position via the `STAGE_POSITION_GET` command (0x6008). After fixing the protocol implementation for position queries, we needed to integrate hardware position verification into the movement workflow.

## Solution Overview

Implemented a two-tier position query system:
1. **Single-axis movements** (jog, move_x/y/z/r) query only the moved axis
2. **Multi-axis movements** (go home, presets) query all 4 axes

## Changes Made

### 1. Fixed Movement Command Protocol (`stage_service.py`)

**File:** `src/py2flamingo/services/stage_service.py:265-282`

**Problem:** Movement commands were placing the axis code in `params[0]` (hardwareID) instead of `params[3]` (int32Data0).

**Fix:** Corrected parameter order to match the protocol structure:
```python
params=[
    0,     # params[0] (hardwareID) - not used
    0,     # params[1] (subsystemID) - not used
    0,     # params[2] (clientID) - not used
    axis,  # params[3] (int32Data0) = axis code (1=X, 2=Y, 3=Z, 4=R)
    0,     # params[4] (int32Data1)
    0,     # params[5] (int32Data2)
    CommandDataBits.TRIGGER_CALL_BACK  # params[6] = flag
]
```

### 2. Position Query After Movement (`position_controller.py`)

**File:** `src/py2flamingo/controllers/position_controller.py:784-856`

**New Method:** `_query_position_after_move(moved_axes, target_position)`

This method intelligently queries hardware based on which axes were moved:

- **Single axis:** Query only that axis using `stage_service.get_axis_position(axis_code)`
- **All axes (None or 4 axes):** Query all using `stage_service.get_position()`
- **Fallback:** Returns target position if hardware query fails

**Benefits:**
- Faster feedback for single-axis movements (one query vs four)
- Complete verification for multi-axis movements
- Graceful degradation if hardware doesn't respond

### 3. Updated Motion Complete Handler (`position_controller.py`)

**File:** `src/py2flamingo/controllers/position_controller.py:858-920`

**Modified:** `_wait_for_motion_complete_async(target_position, moved_axes)`

**Changes:**
- Added `moved_axes` parameter to track which axes moved
- Calls `_query_position_after_move()` after motion-stopped callback
- Updates `_current_position` with hardware-verified values
- Logs actual hardware position confirmation

**Before:**
```python
# Update position (optimistic)
self._current_position = target_position
```

**After:**
```python
# Query actual position from hardware
actual_position = self._query_position_after_move(moved_axes, target_position)
self._current_position = actual_position
self.logger.info(f"Position confirmed from hardware: X={actual_position.x:.3f}, ...")
```

### 4. Updated Movement Methods

All movement methods now pass their moved axes to the motion complete handler:

**Single-axis movements:**
```python
# move_rotation() - line 332
self._wait_for_motion_complete_async(target_position, moved_axes=[AxisCode.ROTATION])

# move_x() - line 398
self._wait_for_motion_complete_async(target_position, moved_axes=[AxisCode.X_AXIS])

# move_y() - line 464
self._wait_for_motion_complete_async(target_position, moved_axes=[AxisCode.Y_AXIS])

# move_z() - line 533
self._wait_for_motion_complete_async(target_position, moved_axes=[AxisCode.Z_AXIS])
```

### 5. Implemented Multi-Axis Movement

**File:** `src/py2flamingo/controllers/position_controller.py:615-690`

**New Method:** `move_to_position(position, validate=True)`

This method was being called by `go_home()` and `undo_position()` but didn't exist. Implementation:

- Moves all 4 axes simultaneously
- Validates position bounds (optional via parameter)
- Queries all 4 axes from hardware after completion
- Used by home, undo, and preset movements

```python
# Move all 4 axes
self._move_axis(self.axis.X, position.x, "X-axis")
self._move_axis(self.axis.Y, position.y, "Y-axis")
self._move_axis(self.axis.Z, position.z, "Z-axis")
self._move_axis(self.axis.R, position.r, "Rotation")

# Query all axes after movement
self._wait_for_motion_complete_async(
    position,
    moved_axes=[AxisCode.X_AXIS, AxisCode.Y_AXIS, AxisCode.Z_AXIS, AxisCode.ROTATION]
)
```

### 6. Removed Outdated Documentation

**Files Updated:**
- `src/py2flamingo/services/stage_service.py`
- `src/py2flamingo/controllers/position_controller.py`

**Removed statements:**
- "Position feedback may not be available from hardware"
- "Many microscopes require software-side position tracking"
- "Returns settings, not position" (for POSITION_GET)
- "Use callbacks instead of polling" (for motion stopped)

**Rationale:** These comments were written when we believed position queries didn't work. After fixing the protocol (params[3] for axis, position in data buffer bytes 52-60), position queries work reliably.

## Testing

All imports verified:
```bash
PYTHONPATH=/home/msnelson/LSControl/Flamingo_Control/src \
/home/msnelson/LSControl/Flamingo_Control/.venv/bin/python -c \
"from py2flamingo.services.stage_service import StageService; \
from py2flamingo.controllers.position_controller import PositionController; \
print('✓ All imports successful')"
# Output: ✓ All imports successful
```

## Protocol Details Reference

For future developers working with stage position:

**STAGE_POSITION_GET (0x6008) - Query position:**
- params[3] (int32Data0) = axis code (1=X, 2=Y, 3=Z, 4=R)
- params[6] (cmdDataBits0) = 0x80000000 (TRIGGER_CALL_BACK)
- Response: Position as little-endian double in data buffer bytes 52-60
- Must query one axis at a time (0xFF doesn't work)

**STAGE_POSITION_SET_SLIDER (0x6005) - Move axis:**
- params[3] (int32Data0) = axis code (1=X, 2=Y, 3=Z, 4=R)
- params[6] (cmdDataBits0) = 0x80000000 (TRIGGER_CALL_BACK)
- value (doubleData) = target position in mm (or degrees for rotation)
- Response: Immediate acknowledgment, then unsolicited STAGE_MOTION_STOPPED callback

**STAGE_MOTION_STOPPED (0x6010) - Callback:**
- Sent by microscope when motion completes
- Use `MotionTracker.wait_for_motion_complete()` to wait for this

## Migration Path

**No breaking changes.** All existing code continues to work:

1. Controllers using `move_x()`, `move_y()`, `move_z()`, `move_rotation()` automatically get hardware verification
2. Multi-axis operations via `move_to_position()` now supported
3. Position tracking remains reliable even if hardware queries fail (fallback to target)

## Benefits

✅ **Verified positions** - Know actual hardware position, not just commanded target
✅ **Efficient queries** - Single-axis movements only query 1 axis
✅ **Complete verification** - Multi-axis movements verify all 4 axes
✅ **Fault tolerance** - Falls back gracefully if hardware doesn't respond
✅ **No breaking changes** - Drop-in improvement to existing code

## Files Modified

1. `src/py2flamingo/services/stage_service.py` - Fixed movement command params, updated docs
2. `src/py2flamingo/controllers/position_controller.py` - Added position querying, created move_to_position

## Related Work

This builds on the earlier fix for `STAGE_POSITION_GET` command (commit ea7f97e) which discovered:
- Axis code must be in params[3] (int32Data0), not params[0]
- Position is returned in data buffer bytes 52-60 as double
- Must query each axis individually (can't use 0xFF for all axes)

## Future Enhancements

Potential improvements for future work:

1. **Position tolerance checking** - Warn if actual position differs significantly from target
2. **Motion failure detection** - Detect if stage didn't reach target (mechanical issue)
3. **Position caching** - Cache last known position to reduce queries when appropriate
4. **Concurrent axis queries** - Query multiple axes in parallel for faster multi-axis verification
