# Claude Report: Connection Error Status Indicator

**Status: RESOLVED** (2026-01-28)

## Issue Summary

When clicking "Connect" in the Flamingo Control application, if the TCP connection succeeds but subsequent communication with the microscope fails (e.g., settings retrieval times out), the UI was misleadingly showing:
- Connect button greyed out (as if successful)
- Status indicator showing blue "Ready" state
- No clear indication to the user that anything went wrong

The only indication of failure was in the log file and a text area showing "No settings available."

## Root Cause Analysis

The connection flow had these issues:

1. **Signal timing**: `connection_established` signal was emitted regardless of whether settings retrieval succeeded
2. **Error swallowing**: The controller caught exceptions from settings retrieval and returned `None` silently
3. **No error state**: The status indicator service had no way to represent a "connected but not working" state

### Call Flow (Before Fix)

```
Connect clicked
    ↓
TCP connection succeeds → status set to "Ready" (blue)
    ↓
Settings retrieval fails (timeout) → error only logged
    ↓
User sees "Ready" but microscope isn't responding
```

## Solution

Added an ERROR state to the status indicator system that displays when communication failures occur after TCP connection succeeds.

### Files Modified

| File | Changes |
|------|---------|
| `status_indicator_service.py` | Added `GlobalStatus.ERROR`, `on_connection_error()`, `clear_error()` methods, updated status calculation |
| `status_indicator_widget.py` | Added red color for ERROR state |
| `connection_view.py` | Added `connection_error` signal, emit on settings failure, reordered signal emissions |
| `application.py` | Connected `connection_error` signal to status indicator service |

### New Status Flow

```
Connect clicked
    ↓
TCP connection succeeds
    ↓
connection_established emitted → status set to "Ready" (blue)
    ↓
Settings retrieval fails (timeout)
    ↓
connection_error emitted → status set to "Settings retrieval failed" (red)
    ↓
User sees clear red error indicator
```

## Technical Details

### New GlobalStatus Enum Value

```python
class GlobalStatus(Enum):
    DISCONNECTED = "disconnected"  # Grey
    IDLE = "idle"                  # Blue
    MOVING = "moving"              # Amber
    WORKFLOW_RUNNING = "workflow_running"  # Purple
    ERROR = "error"                # Red (NEW)
```

### Status Priority Order

```python
def _calculate_status(self) -> GlobalStatus:
    if not self._is_connected:
        return GlobalStatus.DISCONNECTED
    if self._has_error:           # NEW - check error before idle
        return GlobalStatus.ERROR
    if self._is_workflow_running:
        return GlobalStatus.WORKFLOW_RUNNING
    if self._is_moving:
        return GlobalStatus.MOVING
    return GlobalStatus.IDLE
```

### Signal Connection

```python
# In application.py
if hasattr(self.connection_view, 'connection_error'):
    self.connection_view.connection_error.connect(
        lambda msg: self.status_indicator_service.on_connection_error(msg)
    )
```

## Verification

After these changes, when settings retrieval fails:
1. Status indicator turns red
2. Status text shows "Settings retrieval failed"
3. Settings text area shows error message in red
4. User has clear visual feedback that something is wrong

## Commits

```
6c16a29 Add ERROR status to show communication failures after connection
8e6da02 Update button states on communication error
```

### Follow-up Fix (8e6da02)

The initial fix showed the error in the status indicator but didn't update button states. Added `_update_status_error()` method to properly handle the error state:

- **Connect button**: Re-enabled (allows user to retry)
- **Disconnect button**: Stays enabled (TCP connection exists)
- **Sample View button**: Disabled (microscope not usable)
- **Debug/workflow buttons**: Disabled

This ensures users can:
1. See the error clearly (red status indicator + error message)
2. Retry by clicking Connect again
3. Cannot accidentally try to use features that require working communication

### Position Query Race Condition Fix (57b6677)

**Problem:** Stage position showed 0,0,0,0 because position queries were colliding with settings retrieval:
1. `connection_established` signal triggered position queries immediately
2. Settings retrieval pauses the SocketReader for synchronous operation
3. Position responses arriving during the pause were lost (timeout)

**Solution:**
- Added `settings_loaded` signal emitted after settings retrieval completes
- Split `_on_stage_connection_established()` into two handlers:
  - `_on_stage_connection_established()`: enables controls (on TCP connection)
  - `_on_settings_loaded()`: queries position (after settings complete)
- Position queries now wait for settings retrieval to finish

**Files Modified:**
- `connection_view.py`: Added `settings_loaded` signal, emit after successful settings load
- `application.py`: New `_on_settings_loaded()` handler, connect to signal

Pushed to: https://github.com/uw-loci/Flamingo_Control.git (main branch)
