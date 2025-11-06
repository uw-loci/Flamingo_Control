# Protocol Analysis: Missing TRIGGER_CALL_BACK Flag Causing Command Timeouts

**Date:** 2025-11-05
**Issue:** Commands that worked in old code (CAMERA_IMAGE_SIZE_GET, etc.) now timeout in new implementation
**Root Cause:** Missing `COMMAND_DATA_BITS_TRIGGER_CALL_BACK` flag (0x80000000) in cmdBits6 parameter
**Status:** Fixed

---

## Executive Summary

Commands like `CAMERA_IMAGE_SIZE_GET` that worked in the old codebase were timing out in the new implementation. Investigation revealed that the old code set a critical flag (`0x80000000`) in the `cmdBits6` parameter that tells the microscope to send a response. The new code was sending all parameters as `0`, causing the microscope to receive the command but not respond, resulting in 3-second timeouts.

**Fix:** Set `params[6] = 0x80000000` (TRIGGER_CALL_BACK flag) for all query/GET commands.

---

## Investigation Process

### Step 1: Identify Commands That Worked in Old Code

**Tested Commands:**
- ✓ SYSTEM_STATE_GET (40967) - Working (this was already working)
- ✗ CAMERA_IMAGE_SIZE_GET (12327) - Timing out
- ✗ STAGE_POSITION_GET (24584) - Timing out
- ✗ STAGE_MOTION_STOPPED (24592) - Timing out

**Old Code Usage:**
```python
# From oldcodereference/microscope_interactions.py:63
command_queue.put(COMMAND_CODES_CAMERA_IMAGE_SIZE_GET)
send_event.set()
time.sleep(0.1)
frame_size = other_data_queue.get()  # Successfully received response
```

The old code **did use** CAMERA_IMAGE_SIZE_GET and **did receive** responses.

---

### Step 2: Trace Old Code Command Sending

**Path traced:**
1. `microscope_interactions.py` → `command_queue.put(COMMAND_CODES_CAMERA_IMAGE_SIZE_GET)`
2. `threads.py:664` → `command = command_queue.get()`
3. `threads.py:681` → `handle_non_workflow_command(client, command, command_data)`
4. `threads.py:634` → `py2flamingo.functions.tcpip_nuc.command_to_nuc(client, command)`

**Critical Finding in tcpip_nuc.py:**

```python
# oldcodereference/tcpip_nuc.py:107-150
def command_to_nuc(client: socket, command: int, command_data=[0, 0, 0, 0.0]):
    data0, data1, data2, value = command_data

    # ... setup code ...

    int32Data0 = np.int32(data0)  # [6] cmdBits0 (Param 0)
    int32Data1 = np.int32(data1)  # [7] cmdBits1 (Param 1)
    int32Data2 = np.int32(data2)  # [8] cmdBits2 (Param 2)

    cmdDataBits0 = np.uint32(0x80000000)  # [9] cmdBits6 (Param 6) ← CRITICAL!

    doubleData = float(value)     # [10] Value field

    s = struct.Struct("I I I I I I I I I I d I 72s I")
    scmd = s.pack(
        cmd_start,      # [0]  Start marker
        cmd,            # [1]  Command code
        status,         # [2]  Status
        hardwareID,     # [3]  cmdBits0 (Param 0)
        subsystemID,    # [4]  cmdBits1 (Param 1)
        clientID,       # [5]  cmdBits2 (Param 2)
        int32Data0,     # [6]  cmdBits3 (Param 3)
        int32Data1,     # [7]  cmdBits4 (Param 4)
        int32Data2,     # [8]  cmdBits5 (Param 5)
        cmdDataBits0,   # [9]  cmdBits6 (Param 6) = 0x80000000
        doubleData,     # [10] Value (double)
        addDataBytes,   # [11] addDataBytes
        buffer_72,      # [12] Data (72 bytes)
        cmd_end,        # [13] End marker
    )
    client.send(scmd)
```

**Key Discovery:** Line 150 sets `cmdDataBits0 = np.uint32(0x80000000)` which is packed into position [9] of the struct, which corresponds to `cmdBits6` (the 7th parameter field).

---

### Step 3: Verify Flag Definition

**CommandCodes.h (oldcodereference):**

```cpp
// Line 233-238
enum COMMAND_DATA_BITS
{
    COMMAND_DATA_BITS_TRIGGER_CALL_BACK                 = 0x80000000,
    COMMAND_DATA_BITS_EXPERIMENT_TIME_REMAINING         = 0x00000001,
    COMMAND_DATA_BITS_STAGE_POSITIONS_IN_BUFFER         = 0x00000002,
    COMMAND_DATA_BITS_MAX_PROJECTION                    = 0x00000004,
    // ... more flags ...
};
```

**Confirmed:** `0x80000000` is `COMMAND_DATA_BITS_TRIGGER_CALL_BACK`.

**Meaning:** This flag tells the microscope firmware to send a response back to the client. Without it, the microscope receives and processes the command but doesn't send any acknowledgment or data back.

---

### Step 4: Compare New Code Implementation

**New Code (position_controller.py:459-465):**

```python
# BEFORE FIX:
cmd_bytes = self.connection.encoder.encode_command(
    code=command_code,
    status=0,
    params=[0, 0, 0, 0, 0, 0, 0],  # ← All zeros! Missing TRIGGER_CALL_BACK
    value=0.0,
    data=b''
)
```

**Problem:** All parameters are `0`, including `params[6]` (cmdBits6). The microscope receives the command but the TRIGGER_CALL_BACK flag is not set, so it doesn't send a response.

**Result:** Client waits for 3 seconds, then times out.

---

## The Fix

### 1. Added CommandDataBits Class

**File:** `src/py2flamingo/core/tcp_protocol.py`

```python
class CommandDataBits:
    """
    Command data bits flags for the cmdBits6 parameter field.

    These flags control command behavior, particularly response handling.
    From CommandCodes.h enum COMMAND_DATA_BITS.
    """

    # Trigger callback/response from microscope (CRITICAL for query commands)
    TRIGGER_CALL_BACK = 0x80000000

    # Other flags from CommandCodes.h
    EXPERIMENT_TIME_REMAINING = 0x00000001
    STAGE_POSITIONS_IN_BUFFER = 0x00000002
    MAX_PROJECTION = 0x00000004
```

### 2. Updated debug_query_command()

**File:** `src/py2flamingo/controllers/position_controller.py`

```python
# Import at top of file
from py2flamingo.core.tcp_protocol import CommandDataBits

# In debug_query_command():
# AFTER FIX:
cmd_bytes = self.connection.encoder.encode_command(
    code=command_code,
    status=0,
    params=[0, 0, 0, 0, 0, 0, CommandDataBits.TRIGGER_CALL_BACK],  # ← Now set!
    value=0.0,
    data=b''
)
```

### 3. Updated Documentation

**File:** `.claude/claude.md`

Added critical section explaining:
- TRIGGER_CALL_BACK flag must be set for query commands
- What happens without the flag (timeout)
- Examples of correct vs. incorrect usage

---

## Protocol Structure Clarification

The 128-byte protocol structure with parameter field mapping:

```
Byte Offset | Field Name      | Struct Index | Python params[] Index
------------|-----------------|--------------|----------------------
12-15       | cmdBits0        | [3]          | params[0]
16-19       | cmdBits1        | [4]          | params[1]
20-23       | cmdBits2        | [5]          | params[2]
24-27       | cmdBits3        | [6]          | params[3]
28-31       | cmdBits4        | [7]          | params[4]
32-35       | cmdBits5        | [8]          | params[5]
36-39       | cmdBits6        | [9]          | params[6] ← TRIGGER_CALL_BACK goes here!
```

**Critical:** `params[6]` (the 7th parameter) maps to `cmdBits6`, which is struct position [9], and **must** be set to `0x80000000` for GET/query commands.

---

## Why SYSTEM_STATE_GET Was Already Working

**Question:** Why did SYSTEM_STATE_GET work without the flag?

**Answer:** Need to investigate further, but possibilities:
1. SYSTEM_STATE_GET might have special handling in firmware that always responds
2. The microscope might send unsolicited state updates
3. Different command handling path in firmware

**Action:** Test SYSTEM_STATE_GET both with and without the flag to understand behavior.

---

## Commands That Will Now Work

With the TRIGGER_CALL_BACK flag added, these commands should now respond:

### Expected to Work:
- ✓ **CAMERA_IMAGE_SIZE_GET (12327)** - Should return image dimensions in parameters
- ? **STAGE_POSITION_GET (24584)** - May still not work if firmware doesn't implement position feedback
- ? **STAGE_MOTION_STOPPED (24592)** - May still not work if firmware doesn't implement motion detection

### Still Unknown:
Commands marked [✗] in testing need retesting:
- CAMERA_IMAGE_SIZE_GET - **RETEST PRIORITY**
- COMMON_SCOPE_SETTINGS - This is a response code, not a query command

---

## Response Handling in Old Code

**How old code received responses:**

```python
# From threads.py:247-268
def handle_camera_frame_size(received, other_data_queue):
    """
    received[10] = Value field (double) - validation check
    received[7]  = cmdBits4/Param[4] - contains frame size
    """
    if received[10] < 0:  # Check Value field
        print("No camera size detected from system. Exiting.")
        exit()
    other_data_queue.put(received[7])  # Put Param[4] in queue
```

**Key Points:**
1. Response comes back in same 128-byte format
2. Frame size is in `received[7]` which is `cmdBits4` (Param[4])
3. Value field `received[10]` is used for validation (< 0 means error)

**New code must:**
- Parse the 128-byte response
- Extract data from appropriate parameter fields
- Check Value field for error conditions
- The current implementation already does this (position_controller.py:500-504)

---

## Testing Plan

### Phase 1: Verify Fix Works (IMMEDIATE)
1. **Test CAMERA_IMAGE_SIZE_GET** with TRIGGER_CALL_BACK flag
   - Should respond within 3 seconds (no timeout)
   - Check which parameter field contains the frame size
   - Compare with old code expectations (Param[4])

2. **Test CAMERA_PIXEL_FIELD_OF_VIEW_GET** (already working)
   - Verify it still works with flag
   - Determine if it was working by luck or different mechanism

3. **Test SYSTEM_STATE_GET** (already working)
   - Test WITH flag (current behavior)
   - Test WITHOUT flag to understand why it works

### Phase 2: Test Other Commands
4. **Test STAGE_POSITION_GET** with flag
   - If it responds, extract position data
   - If still times out, firmware doesn't support position feedback

5. **Test STAGE_MOTION_STOPPED** with flag
   - If responds, implement motion detection
   - If times out, firmware doesn't support motion status

### Phase 3: Update All Query Commands
6. Review all command sending code
7. Add TRIGGER_CALL_BACK flag to all GET/query commands
8. Update command documentation with response format

---

## Files Modified

### 1. `src/py2flamingo/core/tcp_protocol.py`
**Change:** Added `CommandDataBits` class with flag constants
**Lines:** 35-49 (new class)
**Purpose:** Centralize command data bits flags

### 2. `src/py2flamingo/controllers/position_controller.py`
**Changes:**
- Line 19: Added import `from py2flamingo.core.tcp_protocol import CommandDataBits`
- Line 464: Changed params from `[0, 0, 0, 0, 0, 0, 0]` to `[0, 0, 0, 0, 0, 0, CommandDataBits.TRIGGER_CALL_BACK]`
**Purpose:** Fix debug_query_command() to request responses

### 3. `.claude/claude.md`
**Change:** Added critical documentation section about TRIGGER_CALL_BACK flag
**Lines:** 209-236 (new section)
**Purpose:** Ensure future development doesn't repeat this mistake

---

## Lessons Learned

### 1. Protocol Flags Are Critical
Binary protocols often have flags that dramatically change behavior. A single bit can make the difference between "working" and "timing out."

### 2. Struct Field Mapping Is Confusing
The mapping from Python `params[]` array to C struct fields to byte offsets requires careful tracking:
- Python: `params[6]`
- Struct: Position `[9]` in pack/unpack
- C code: `cmdBits6`
- Byte offset: 36-39

### 3. Response Handling Varies By Command
Different commands return data in different parameter fields:
- CAMERA_IMAGE_SIZE_GET: Returns size in Param[4] (cmdBits4)
- CAMERA_PIXEL_FIELD_OF_VIEW_GET: Returns value in Value field (double)
- SYSTEM_STATE_GET: Returns state in Status field AND Param[3]

Each command needs specific response parsing logic.

### 4. Old Code Is Valuable Reference
The old code contained critical knowledge that wasn't documented:
- The 0x80000000 flag requirement
- Which parameter fields contain response data
- Validation logic (checking Value field for errors)

Always reference old implementations when rewriting.

---

## Next Steps

### Immediate (Before User Testing):
1. ✓ Add TRIGGER_CALL_BACK flag to debug queries
2. ✓ Document flag in claude.md
3. **Test CAMERA_IMAGE_SIZE_GET** - User will test on remote PC

### Short Term:
1. Create helper function for query commands that automatically sets flag
2. Add response data extraction helpers for common patterns
3. Document which parameter field each command uses for response data

### Long Term:
1. Build comprehensive command response specification
2. Create unit tests for command encoding with flags
3. Add command builder that knows which flags each command needs

---

## Code Examples

### Correct Usage Pattern

```python
from py2flamingo.core.tcp_protocol import CommandDataBits

# For GET/query commands:
def query_microscope(command_code: int) -> dict:
    """Send a query command and get response."""
    cmd_bytes = encoder.encode_command(
        code=command_code,
        status=0,
        params=[0, 0, 0, 0, 0, 0, CommandDataBits.TRIGGER_CALL_BACK],
        value=0.0,
        data=b''
    )
    socket.sendall(cmd_bytes)
    response = socket.recv(128)
    return parse_response(response)

# For SET/action commands (no response needed):
def command_microscope(command_code: int, params: list) -> None:
    """Send a command, don't wait for response."""
    cmd_bytes = encoder.encode_command(
        code=command_code,
        status=0,
        params=params,  # No TRIGGER_CALL_BACK needed
        value=0.0,
        data=b''
    )
    socket.sendall(cmd_bytes)
```

### Helper Function Recommendation

```python
def create_query_command(command_code: int, value: float = 0.0) -> bytes:
    """
    Create a query command with TRIGGER_CALL_BACK flag automatically set.

    Use this for all GET/query commands to ensure they receive responses.
    """
    return encoder.encode_command(
        code=command_code,
        status=0,
        params=[0, 0, 0, 0, 0, 0, CommandDataBits.TRIGGER_CALL_BACK],
        value=value,
        data=b''
    )
```

---

## Conclusion

The missing `COMMAND_DATA_BITS_TRIGGER_CALL_BACK` flag (0x80000000) in `cmdBits6` (params[6]) was causing query commands to timeout. The microscope was receiving the commands but not responding because it didn't know a response was expected.

**Fix Applied:**
- Added `CommandDataBits` class with flag constants
- Updated `debug_query_command()` to set flag in params[6]
- Documented requirement in claude.md for future reference

**Expected Result:**
Commands like CAMERA_IMAGE_SIZE_GET should now respond within milliseconds instead of timing out after 3 seconds.

**User Action Required:**
Test the updated code on the remote PC to verify CAMERA_IMAGE_SIZE_GET now works.

---

**Analysis completed:** 2025-11-05
**Ready for testing:** Yes
**Breaking changes:** None (fix only affects debug queries)
