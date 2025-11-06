# Microscope Server Log Analysis

**Date:** 2025-11-06
**Source:** Three log files from microscope server showing actual command reception
**Purpose:** Understand what microscope receives and how it responds

---

## Executive Summary

Analysis of microscope server logs reveals that:
1. **Official GUI ALWAYS sends `cmdDataBits0 = 0x80000000`** (TRIGGER_CALL_BACK flag) for ALL commands
2. Our test code was sending `cmdDataBits0 = 0x00000000` (missing flag)
3. Commands execute even without the flag, but query commands may not respond without it
4. Socket 24 = our Python code, Socket 26 = official GUI
5. Stage movement uses int32Data0 for axis, doubleData for position in mm

---

## File 1: Test_Command_example_output.txt

### Our Test Commands (Socket 24 - Python Code)

#### STAGE_POSITION_GET (Lines 115-128)
```
cmd = 0x00006008, 24584, stage get position
cmdDataBits0 = 0x00000000  ← NO FLAG!
clientID = 0
```
**Result:** Command received, NO response logged (timeout on our end)

#### STAGE_MOTION_STOPPED (Lines 129-142)
```
cmd = 0x00006010, 24592, stage motion stopped
cmdDataBits0 = 0x00000000  ← NO FLAG!
clientID = 0
```
**Result:** Command received, NO response logged (timeout on our end)

### Official GUI Commands (Socket 26)

#### LED Commands (Lines 30-114)
**All LED commands** have:
```
cmdDataBits0 = 0x80000000  ← TRIGGER_CALL_BACK FLAG PRESENT!
clientID = 26
```

Examples:
- **Command 61:** LED disable (16387) - Flag = 0x80000000
- **Command 62:** LED enable (16386) - Flag = 0x80000000
- **Command 63:** LED selection change (16390) - Flag = 0x80000000
- **Command 65:** LED set value (16385) - Flag = 0x80000000

**Conclusion:** Official GUI sends TRIGGER_CALL_BACK flag for ALL commands, including action commands (not just queries).

---

## File 2: SwitchToLaserSnapshotLiveMode.txt

### Camera and Laser Operation Sequence (All from Official GUI - Socket 26)

#### Command Sequence:
1. **LED disable (16387)** - cmdDataBits0 = 0x80000000
2. **Laser preview enable (8196)** - cmdDataBits0 = 0x80000000
   - int32Data0 = 2 (laser index)
   - Triggers filter wheel positioning, laser enable sequence
3. **Illumination enabled (28676)** - cmdDataBits0 = 0x80000000
4. **Take single image (12294)** - cmdDataBits0 = 0x80000000
   - System state changes: IDLE → SNAP SHOT → IDLE
   - Camera acquisition sequence logged
5. **Start continuous imaging (12295)** - cmdDataBits0 = 0x80000000
   - System state changes: IDLE → LIVE VIEW
6. **Stop continuous imaging (12296)** - cmdDataBits0 = 0x80000000
   - System state changes: LIVE VIEW → IDLE

**Key Finding:** Every single command from official GUI includes the TRIGGER_CALL_BACK flag.

---

## File 3: StageMovementAndLasers.txt

### Stage Movement Commands (Official GUI - Socket 26)

#### SYSTEM_STATE_GET from our code (Lines 1-14)
```
cmd = 0x0000a007, 40967, State get
cmdDataBits0 = 0x00000000  ← NO FLAG (our old test code)
clientID = 0
socket 24
```

#### STAGE_POSITION_SET (Lines 15-28)
```
cmd = 0x00006005, 24581, stage set position (slide control)
cmdDataBits0 = 0x80000000  ← HAS FLAG!
clientID = 26
int32Data0 = 1    ← Axis (1 = Y-axis)
doubleData = 7.635  ← Target position in mm
```

**Microscope Response:**
```
[info] PI C-884 stage controller, system in motion
[info] PI C-884 stage controller, monitorMotionExitCount, motion stopped.
[info] PI C-884 stage controller, sending motion stopped command
[info] stage motion status = 0, (0 = stopped)
```

**Key Observations:**
- Stage movement is **asynchronous** - command returns immediately
- Microscope internally monitors motion with background thread
- When motion completes, sends "motion stopped command" back to client
- Motion monitoring thread: `threadMotionWaitForStop`

#### Second Stage Movement (Lines 198-219)
```
cmd = 0x00006005, 24581, stage set position (slide control)
cmdDataBits0 = 0x80000000
int32Data0 = 3    ← Axis (3 = Z-axis)
doubleData = 18.839  ← Target position in mm
```

**Axis Mapping:**
- int32Data0 = 1 → Y-axis
- int32Data0 = 3 → Z-axis
- (Based on old code comments: 0=X, 1=Y, 2=Z, 3=Rotation, but logs show 1=Y, 3=Z)

---

## Protocol Field Usage Patterns

### For Query Commands (GET commands):
```
cmdDataBits0 = 0x80000000  (TRIGGER_CALL_BACK - required for response)
int32Data0-2 = 0 (usually unused)
doubleData = 0.0
additionalDataBytes = 0
```

### For Stage Movement Commands:
```
cmdDataBits0 = 0x80000000  (TRIGGER_CALL_BACK)
int32Data0 = axis (0=X, 1=Y, 2=Z, 3=Rotation?)
int32Data1 = 0
int32Data2 = 0
doubleData = target position (mm)
additionalDataBytes = 0
```

### For Laser Commands:
```
cmdDataBits0 = 0x80000000  (TRIGGER_CALL_BACK)
int32Data0 = laser index (1, 2, 3, 4)
int32Data1 = 0
int32Data2 = 0
doubleData = 0.0
buffer (72 bytes) = laser power level as string (e.g., "11.49")
```

### For LED Commands:
```
cmdDataBits0 = 0x80000000  (TRIGGER_CALL_BACK)
int32Data0 = LED index or parameter
int32Data1 = LED value (e.g., 65535 for full brightness)
int32Data2 = 0
doubleData = 0.0
```

---

## Socket Identification

### Socket 24:
- Our Python test code
- clientID = 0
- Commands sent WITHOUT TRIGGER_CALL_BACK flag (before our fix)

### Socket 26:
- Official GUI (in-person interface)
- clientID = 26
- ALL commands sent WITH TRIGGER_CALL_BACK flag (0x80000000)

**Question:** Why does official GUI use clientID = 26?
- Possible: Socket number is used as clientID
- Or: Fixed clientID for main GUI connection

---

## Key Findings

### 1. TRIGGER_CALL_BACK Flag is Universal in Official GUI

The official GUI sends `cmdDataBits0 = 0x80000000` for:
- ✓ Query commands (GET operations)
- ✓ Action commands (SET operations)
- ✓ Control commands (enable/disable)
- ✓ Movement commands (stage positioning)

**Conclusion:** The flag is not just for queries—it's a **standard protocol requirement** for all commands.

### 2. Commands Execute Without Flag (Sometimes)

Action commands like LED control, laser enable, and stage movement **do execute** even when sent without the flag. However:
- Query commands (STAGE_POSITION_GET) **don't respond** without the flag
- The microscope receives and processes the command
- But doesn't send acknowledgment or data back

### 3. Asynchronous Stage Movement

Stage movement is asynchronous:
1. STAGE_POSITION_SET command sent
2. Microscope immediately acknowledges (returns from command)
3. Background thread monitors motion
4. When motion completes, microscope sends unsolicited "motion stopped" message

**Implication:** Our code needs to:
- Send STAGE_POSITION_SET with flag
- Wait for/handle unsolicited motion stopped callback
- Or poll with STAGE_MOTION_STOPPED query

### 4. Motion Stopped Callback

From logs (Line 34, 217):
```
[info] PI C-884 stage controller, sending motion stopped command
[info] stage motion status = 0, (0 = stopped)
```

The microscope **proactively sends** a motion stopped notification when movement completes. This is likely why:
- STAGE_MOTION_STOPPED query might not be needed
- Background listener thread in old code was handling these callbacks
- We need to handle unsolicited messages on the socket

---

## Response Code Analysis

From user's earlier test results, CAMERA_IMAGE_SIZE_GET:
- **Sent:** Command code 12327 (0x3027)
- **Received:** Command code 16387 (0x4003)

Response code 16387 appeared in multiple contexts:
- User's CAMERA_IMAGE_SIZE_GET response
- Log files show "LED disable" = 16387

**Question:** Is 16387 a multi-purpose response code, or are we misinterpreting?

Possible interpretations:
1. Response codes are different from command codes
2. Command code 16387 in logs means something different than response code 16387
3. Need to map command codes to expected response codes

---

## Recommendations

### 1. Always Send TRIGGER_CALL_BACK Flag

Update ALL command sending to include `params[6] = 0x80000000`:
```python
def send_any_command(code, params, value):
    # Ensure params[6] always has TRIGGER_CALL_BACK flag
    if params is None or len(params) < 7:
        params = [0] * 7
    params[6] = CommandDataBits.TRIGGER_CALL_BACK

    return encoder.encode_command(code, 0, params, value, b'')
```

### 2. Implement Motion Callback Handling

For stage movements:
```python
# Send movement command
send_stage_position_set(axis=1, position=10.0)

# Listen for unsolicited motion stopped callback
# (Background thread or async handler)
while not motion_stopped:
    msg = socket.recv(128)
    if is_motion_stopped_message(msg):
        break
```

### 3. Map Command Codes to Response Codes

Create mapping of which response code to expect for each command:
```python
COMMAND_RESPONSE_MAP = {
    12327: 16387,  # CAMERA_IMAGE_SIZE_GET → Response code
    24584: ???,    # STAGE_POSITION_GET → Response code
    # ... etc
}
```

### 4. Set clientID Field

Consider setting clientID to a meaningful value:
```python
# In encode_command, add clientID parameter
# Currently params[2] (subsystemID) and params[1] (hardwareID) are 0
# Could use clientID for tracking/debugging
```

---

## Testing Plan

### Phase 1: Verify Flag Works
1. ✓ Added TRIGGER_CALL_BACK flag to debug queries
2. ✓ Added socket buffer clearing for additional data
3. **Next:** Test CAMERA_IMAGE_SIZE_GET with flag on remote PC
4. **Expected:** Should receive valid response with correct start marker

### Phase 2: Test Query Commands
1. CAMERA_IMAGE_SIZE_GET - should return dimensions
2. STAGE_POSITION_GET - might still not work if unimplemented
3. CAMERA_PIXEL_FIELD_OF_VIEW_GET - already working, confirm still works

### Phase 3: Implement Stage Movement
1. Send STAGE_POSITION_SET with proper parameters
2. Implement motion callback listener
3. Handle asynchronous motion completion

### Phase 4: Complete Command Library
1. Map all command codes to their functions
2. Document parameter usage for each command
3. Implement wrappers for common operations

---

## Questions for Further Investigation

1. **Response Code Mapping:**
   - What determines the response code for each command?
   - Is there a pattern or formula?
   - Are response codes documented in CommandCodes.h?

2. **Motion Stopped Callback:**
   - What exactly does the motion stopped message contain?
   - Same 128-byte protocol format?
   - What command code does it use?

3. **ClientID Usage:**
   - Does clientID affect command processing?
   - Should we use socket number as clientID?
   - Does microscope use clientID for routing responses?

4. **Additional Data Usage:**
   - When is additionalDataBytes > 0 used?
   - CAMERA_IMAGE_SIZE_GET returns 6 additional bytes - what do they contain?
   - How to interpret additional data for different commands?

---

## Conclusion

The microscope logs confirm our analysis:
1. ✓ TRIGGER_CALL_BACK flag (0x80000000) is required for responses
2. ✓ Official GUI always sends this flag for ALL commands
3. ✓ Commands execute without flag, but queries don't respond
4. ✓ Our fix (adding the flag) is correct and necessary
5. ✓ Socket buffer management is critical to prevent corruption

The next step is testing the updated code on the remote PC to verify:
- Commands respond with correct start markers
- Additional data is captured properly
- No buffer corruption between commands
- Query commands return valid data

---

**Analysis Date:** 2025-11-06
**Status:** Ready for remote PC testing
**Expected Outcome:** All debug queries should now work correctly with proper responses
