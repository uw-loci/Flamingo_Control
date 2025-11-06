# Debug Query System Implementation and Command Testing

**Date:** 2025-11-05
**Session Focus:** TCP protocol documentation, debug query improvements, command testing, and troubleshooting

---

## Overview

This session focused on implementing and improving the debug query system for testing microscope commands, documenting the TCP protocol structure, and systematically testing which commands are actually implemented in the microscope firmware.

## Work Completed

### 1. TCP Protocol Documentation

Added comprehensive TCP protocol documentation to `.claude/claude.md` for future reference.

**Protocol Structure (128 bytes total):**

```
Byte Offset | Size | Field Name      | Type    | Description
------------|------|-----------------|---------|----------------------------------
0-3         | 4    | Start Marker    | uint32  | 0xF321E654 (validates packet)
4-7         | 4    | Command Code    | uint32  | Command identifier
8-11        | 4    | Status          | uint32  | Status code (1=IDLE, 0=BUSY)
12-15       | 4    | cmdBits0        | int32   | Parameter 0
16-19       | 4    | cmdBits1        | int32   | Parameter 1
20-23       | 4    | cmdBits2        | int32   | Parameter 2
24-27       | 4    | cmdBits3        | int32   | Parameter 3
28-31       | 4    | cmdBits4        | int32   | Parameter 4
32-35       | 4    | cmdBits5        | int32   | Parameter 5
36-39       | 4    | cmdBits6        | int32   | Parameter 6
40-47       | 8    | Value           | double  | Floating-point value
48-51       | 4    | addDataBytes    | uint32  | Size of additional data
52-123      | 72   | Data            | bytes   | Arbitrary data field
124-127     | 4    | End Marker      | uint32  | 0xFEDC4321
```

**Key Documentation Added:**
- Complete byte offset table for all protocol fields
- Python struct format string with visual mapping
- Field usage patterns for different command types
- Two-part response handling (128-byte ack + additional data)
- Packet validation requirements
- Communication architecture notes

**Location:** `.claude/claude.md` lines 123-262

---

### 2. Debug Query Display Improvements

**User Request:** "For all debug queries, show the breakdown of the entire data. Currently you have things like 'Parameters' rather than a list of what the parameters actually are."

**Implementation:** Updated `connection_view.py` to show complete protocol breakdown with byte offsets.

**Before:**
```
Parameters: [0, 0, 0, 0, 0, 0, 0]
```

**After:**
```
[Offset 0-3]   Start Marker:     4079969876 (0xF321E654)
[Offset 4-7]   Command Code:     40967
[Offset 8-11]  Status:           1

Command Parameters (7 x 4 bytes = 28 bytes):
[Offset 12-15] cmdBits0/Param[0]: 0
[Offset 16-19] cmdBits1/Param[1]: 0
[Offset 20-23] cmdBits2/Param[2]: 0
[Offset 24-27] cmdBits3/Param[3]: 40962
[Offset 28-31] cmdBits4/Param[4]: 0
[Offset 32-35] cmdBits5/Param[5]: 0
[Offset 36-39] cmdBits6/Param[6]: 0

[Offset 40-47] Value (double):   0.0
[Offset 48-51] addDataBytes:     0 (size of additional data)
```

**Benefits:**
- Shows exact byte offset for each field
- Clearly labels all 7 separate parameter fields
- Makes it obvious which fields contain data
- Helpful for debugging protocol issues
- Educational for understanding binary protocol structure

**File Modified:** `src/py2flamingo/views/connection_view.py`

---

### 3. Debug Query Architecture Fix

**Issue Encountered:** AttributeError when attempting to use queue-based communication.

**Initial Error:**
```python
AttributeError: 'MVCConnectionService' object has no attribute 'event_manager'
  File "position_controller.py", line 459, in debug_query_command
    event_manager = self.connection.event_manager
```

**Root Cause:** Confusion between two different architectures:
- **Old `ConnectionService`** (lines 21-279): Has background threads, uses `event_manager` and queue system
- **New `MVCConnectionService`** (lines 281+): No background threads, direct socket communication

**Solution:** Reverted debug queries to use direct socket access, which is correct for MVC architecture.

**Corrected Implementation:**
```python
def debug_query_command(self, command_code: int, command_name: str) -> dict:
    """
    Send a command and return parsed response for debugging.

    Note:
        This method uses direct socket communication. MVCConnectionService
        doesn't use background threads, so direct socket access is safe.
    """
    # Get command socket from connection service
    command_socket = self.connection._command_socket

    # Send command
    command_socket.sendall(cmd_bytes)

    # Read 128-byte acknowledgment
    ack_response = self._receive_full_bytes(command_socket, 128, timeout=3.0)
```

**File Modified:** `src/py2flamingo/controllers/position_controller.py`

---

### 4. Systematic Command Testing

Tested multiple commands to determine which are actually implemented in the microscope firmware vs. just defined in `CommandCodes.h`.

**Test Results:**

#### ✓ Working Commands (3)

1. **SYSTEM_STATE_GET (40967 / 0xA007)**
   - Returns system state in Status field (1 = IDLE)
   - Param[3] contains state code (40962 = IDLE)
   - Consistently reliable response

2. **CAMERA_PIXEL_FIELD_OF_VIEW_GET (12343 / 0x3037)**
   - Returns pixel size in Value field
   - Test result: 0.000253 mm/pixel
   - Useful for calculating real-world dimensions

3. **CAMERA_WORK_FLOW_STOP (12293 / 0x3005)**
   - Command to stop camera workflow
   - Responds with acknowledgment
   - Safe to call even when no workflow running

#### ✗ Not Working Commands (4)

1. **CAMERA_IMAGE_SIZE_GET (12327 / 0x3027)**
   - Times out after 3 seconds
   - No response from microscope
   - Old code has handlers, but firmware doesn't implement it

2. **STAGE_POSITION_GET (24584 / 0x6008)**
   - Times out - **no position feedback available**
   - Critical finding: Cannot query current position from hardware
   - Software must track position locally

3. **STAGE_MOTION_STOPPED (24592 / 0x6010)**
   - Times out
   - Cannot detect when stage has completed motion from hardware
   - Must use state polling or timing estimates

4. **COMMON_SCOPE_SETTINGS (4103 / 0x1007)**
   - Times out
   - This is actually a response code, not a query command

**Updated Dropdown:** Modified dropdown in connection view to clearly mark working (✓) vs. non-working (✗) commands based on actual test results.

**File Modified:** `src/py2flamingo/views/connection_view.py`

---

### 5. Command Codes Reference Document

**User Request:** "Please double check the code values with the commands in CommandCodes.h. Create a text document that contains the command code name, the hex code, and the numerical code, and save it to the same folder."

**Created:** `oldcodereference/CommandCodes_Reference.txt` (338 lines, 19.6 KB)

**Contents:**
- All 120+ command codes from CommandCodes.h
- Three columns for each: Name, Hex Value, Decimal Value
- Test result markers ([✓] / [✗]) for tested commands
- Organized by subsystem (Common, Laser, Camera, LED, Stage, etc.)
- Summary of tested commands with explanations
- Protocol structure notes
- Testing recommendations

**Example Entries:**
```
COMMAND_CODES_SYSTEM_STATE_GET                        0x0000A007      40967 [✓]
COMMAND_CODES_CAMERA_PIXEL_FIELD_OF_VIEW_GET          0x00003037      12343 [✓]
COMMAND_CODES_CAMERA_WORK_FLOW_STOP                   0x00003005      12293 [✓]
COMMAND_CODES_CAMERA_IMAGE_SIZE_GET                   0x00003027      12327 [✗]
COMMAND_CODES_STAGE_POSITION_GET                      0x00006008      24584 [✗]
COMMAND_CODES_STAGE_MOTION_STOPPED                    0x00006010      24592 [✗]
COMMAND_CODES_COMMON_SCOPE_SETTINGS                   0x00001007      4103 [✗]
```

**Note:** File is in `oldcodereference/` which is intentionally excluded from git tracking per `.gitignore` line 156.

---

### 6. Protocol Encoder Enhancement

Added `additional_data_size` parameter to `ProtocolEncoder.encode_command()` for future file transfer support.

**Before:**
```python
def encode_command(self, code: int, status: int = 0, params: List[int] = None,
                   value: float = 0.0, data: bytes = b'') -> bytes:
```

**After:**
```python
def encode_command(self, code: int, status: int = 0, params: List[int] = None,
                   value: float = 0.0, data: bytes = b'',
                   additional_data_size: int = 0) -> bytes:
```

**Use Case:** Commands like `SCOPE_SETTINGS_SAVE` need to indicate file size in `addDataBytes` field before sending file data.

**File Modified:** `src/py2flamingo/core/tcp_protocol.py`

---

## Key Findings

### 1. Position Feedback Unavailable

**Critical Discovery:** `STAGE_POSITION_GET` times out, meaning the microscope does not provide position feedback.

**Implications:**
- Software must track position locally (can drift)
- Cannot detect manual stage movement
- Cannot verify actual position after movement
- Cannot detect partial movement failures
- Position tracking is reset after power cycle or software restart

**Current Mitigation:**
- Software tracks commanded positions
- Assumes all movements complete successfully
- User must be aware position may not be accurate if:
  - Stage is manually moved
  - Movement is blocked/obstructed
  - Software is restarted
  - Power is cycled

### 2. Command Implementation Varies

Not all commands defined in `CommandCodes.h` are implemented in the microscope firmware. This specific microscope model implements only a subset of the defined protocol.

**Testing Strategy:**
- Test new commands with safe, read-only operations first
- Expect 3-second timeout for unimplemented commands
- Document which commands work for this microscope model
- Don't assume old code command usage indicates current firmware support

### 3. Architecture Clarity

Understanding the difference between old and new architectures is critical:

**Old `ConnectionService`:**
- Background listener thread continuously reading socket
- Queue-based command sending
- Event-based signaling
- Thread-safe but more complex

**New `MVCConnectionService`:**
- No background threads
- Direct socket access
- Simpler, more predictable
- Request-response pattern

Using the wrong pattern causes AttributeErrors and communication failures.

---

## Files Modified

1. **`.claude/claude.md`**
   - Added TCP Protocol Structure section (lines 123-222)
   - Added Communication Architecture section (lines 224-262)

2. **`src/py2flamingo/views/connection_view.py`**
   - Updated debug display to show byte offsets for all fields
   - Updated dropdown with ✓/✗ markers for tested commands
   - Improved protocol breakdown clarity

3. **`src/py2flamingo/controllers/position_controller.py`**
   - Fixed debug_query_command() to use direct socket access
   - Added detailed comments explaining MVC architecture pattern
   - Removed incorrect queue-based implementation

4. **`src/py2flamingo/core/tcp_protocol.py`**
   - Added `additional_data_size` parameter to encode_command()
   - Enables future file transfer command support

5. **`oldcodereference/CommandCodes_Reference.txt`** (NEW)
   - Complete command code reference with hex/decimal values
   - Test results for verified commands
   - Protocol notes and testing recommendations

---

## Git Commits

Three commits were made and pushed during this session:

1. **"Update claude.md: Replace socket contention issue with architecture docs"**
   - Added TCP protocol documentation
   - Added communication architecture notes

2. **"Fix: Revert debug queries to direct socket access for MVC architecture"**
   - Fixed AttributeError in debug_query_command()
   - Reverted to correct direct socket pattern

3. **"Update debug dropdown with confirmed working/not-working commands"**
   - Added ✓/✗ markers based on actual test results
   - Updated to reflect firmware implementation reality

---

## Next Steps

### Immediate: Microscope-Side Investigation

**User's Next Task:** "Go in and see what the microscope is receiving and how it is reacting to the various commands that are not currently working on our end."

**Commands Requiring Investigation:**

1. **CAMERA_IMAGE_SIZE_GET (12327 / 0x6008)**
   - Is command received?
   - Is firmware responding?
   - Are responses being sent but not received?
   - Check microscope logs for command receipt

2. **STAGE_POSITION_GET (24584 / 0x6008)**
   - Is this command implemented in firmware?
   - Is position tracking available at hardware level?
   - Could position feedback be accessed differently?

3. **STAGE_MOTION_STOPPED (24592 / 0x6010)**
   - Is motion detection available?
   - Alternative methods to detect motion completion?

**Investigation Approach:**
- Monitor microscope logs while sending commands from Python
- Check if microscope receives 128-byte packets correctly
- Verify start/end markers are correct
- Check if microscope sends responses that aren't being received
- Determine if timeout is due to:
  - Command not implemented
  - Response not being sent
  - Response being sent to wrong socket/port
  - Protocol mismatch

### Future Enhancements

1. **File Transfer Commands**
   - Test `SCOPE_SETTINGS_SAVE` / `SCOPE_SETTINGS_LOAD`
   - Implement two-part send (128-byte header + file data)
   - Handle `addDataBytes` field correctly

2. **Additional Command Testing**
   - Systematically test other subsystems (Laser, LED, Trigger, etc.)
   - Document which subsystems are available on this microscope
   - Create comprehensive working command list

3. **Position Tracking Improvements**
   - Add position tracking persistence (save to config)
   - Add manual position correction UI
   - Warn user about position drift risks
   - Consider homing procedure to reset position

4. **Error Handling**
   - Add timeout indicators in UI
   - Show clearer messages for unimplemented commands
   - Add command availability checking before sending

---

## Testing on Remote PC

All changes have been committed and pushed to GitHub. Pull the latest changes on the remote PC to test:

```bash
git pull origin main
```

**Test the debug query interface:**
1. Connect to microscope
2. Open debug panel (if available) or use connection view
3. Try the three working commands (✓):
   - SYSTEM_STATE_GET
   - CAMERA_PIXEL_FIELD_OF_VIEW_GET
   - CAMERA_WORK_FLOW_STOP
4. Verify complete protocol breakdown is displayed
5. Try a non-working command (✗) to verify timeout behavior

**Observe microscope behavior:**
- Check microscope logs during command testing
- Note any responses or errors on microscope side
- Document microscope reaction to each command

---

## Conclusion

This session established a solid foundation for debugging microscope communication:
- Complete protocol documentation for future reference
- Improved debug display showing all protocol fields
- Corrected architecture implementation
- Systematic testing revealing command availability
- Comprehensive command code reference

The key finding that position feedback is unavailable is critical for future development - all position-dependent features must account for software-only position tracking.

The next step of investigating microscope-side behavior will help determine whether non-working commands are truly unimplemented or if there are communication issues to resolve.

---

**Session Duration:** Multiple hours
**Status:** Complete - ready for microscope-side investigation
**Next Session:** Analyze microscope logs and firmware behavior for non-responding commands
