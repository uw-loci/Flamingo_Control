# Meeting Summary: Communication Protocol Documentation

**Date:** 2025-11-10
**Purpose:** Meeting with C++ microscope server developer

---

## Documents Created

### 1. COMMUNICATION_PROTOCOL.md (Main Document)
**Location:** `docs/COMMUNICATION_PROTOCOL.md`

Comprehensive protocol documentation covering:
- 128-byte binary protocol structure
- Connection establishment (dual sockets)
- Threading architecture
- Complete "Get Image Size" example with hex dumps
- Message formatting details (encoding, line endings, endianness)
- Command codes reference
- C++ pseudo-code examples

**Key takeaways for C++ developer:**
- Fixed 128-byte binary format, little-endian throughout
- Start marker: 0xF321E654, End marker: 0xFEDC4321
- params[6] MUST have 0x80000000 (TRIGGER_CALL_BACK) set for query commands
- No line endings in protocol - pure binary
- Workflow files sent as additional data after 128-byte header

### 2. STAGE_POSITION_AXIS_PARAMETER.md (Critical Update)
**Location:** `docs/STAGE_POSITION_AXIS_PARAMETER.md`

**NEW INFORMATION from developer feedback:**

Stage commands **REQUIRE** params[0] to specify which axis:
- params[0] = 1 → X axis
- params[0] = 2 → Y axis
- params[0] = 3 → Z axis
- params[0] = 4 → R axis (rotation)

**Commands affected:**
- STAGE_POSITION_GET (24584) - Query position of ONE axis
- STAGE_POSITION_SET (24580) - Move ONE axis

**This explains why previous attempts failed!**
- Old code sent params[0] = 0 (default)
- Microscope didn't respond (no axis specified)
- Python side timed out

**Document includes:**
- Complete Python examples for each axis
- Hex dumps showing correct params[0] values
- C++ server implementation examples
- Response format details

### 3. THREADING_RACE_CONDITION_ANALYSIS.md
**Location:** `docs/THREADING_RACE_CONDITION_ANALYSIS.md`

Analysis of potential race condition between command response reading and unsolicited message listener.

**Conclusion:**
- CallbackListener class exists but is **NOT ACTIVATED** in current code
- Therefore, NO race condition exists currently
- All socket reads are synchronous (one thread at a time)
- If CallbackListener is activated in future, would need coordination mechanism

**Recommendations:**
- Keep current synchronous approach (works fine)
- If unsolicited messages needed, add 3rd socket for callbacks
- Don't try to use mutex locks (defeats purpose of background listener)

---

## Key Points for Your Meeting

### 1. Protocol Basics
```
128 bytes fixed format:
- Bytes 0-3:   Start marker (0xF321E654)
- Bytes 4-7:   Command code
- Bytes 8-11:  Status
- Bytes 12-39: 7 parameters (4 bytes each)
- Bytes 40-47: Value (double, 8 bytes)
- Bytes 48-51: Additional data size
- Bytes 52-123: Data buffer (72 bytes)
- Bytes 124-127: End marker (0xFEDC4321)

All fields: LITTLE-ENDIAN
```

### 2. Critical Requirements

**For ALL query commands:**
```python
params[6] = 0x80000000  # TRIGGER_CALL_BACK flag
```
Without this, microscope won't send response.

**For stage movement commands:**
```python
params[0] = axis_number  # 1=X, 2=Y, 3=Z, 4=R
params[6] = 0x80000000   # TRIGGER_CALL_BACK flag
```

### 3. Working Examples

**Camera Image Size (Working):**
```
Command code: 12327
params[6]: 0x80000000
Response: params[3]=width, params[4]=height
```

**Stage Position X (Now Working with fix):**
```
Command code: 24584
params[0]: 1 (for X-axis)
params[6]: 0x80000000
Response: params[0]=X position
```

### 4. Connection Architecture

```
Client connects to TWO ports:
- Port N (e.g., 53717): Command socket
  - Client sends 128-byte commands
  - Client receives 128-byte responses
  - Synchronous request/response

- Port N+1 (e.g., 53718): Live image socket
  - Microscope sends image data
  - Asynchronous stream
```

### 5. Text Encoding (Historical Issues)

Previous version had problems with:
- Windows vs Linux line endings (CRLF vs LF)
- Text encoding

**Current implementation:**
- 128-byte command structure: NO line endings (pure binary)
- Workflow files: Sent as-is in binary mode (preserves line endings)
- Settings data: Received as UTF-8 text after 128-byte ack

### 6. Example Message Flow

```
Python                           C++ Server
======                           ==========
1. Send IMAGE_SIZE_GET
   [128 bytes]
   params[6]=0x80000000    ────>  [Receives command]
                                  [Checks params[6] & 0x80000000]
                                  [Prepares response]
                           <────  [Sends 128 bytes]
2. Receive response               params[3]=2048 (width)
   Parse width/height             params[4]=2048 (height)
```

---

## Questions to Ask C++ Developer

1. **Units for stage positions:** Are position values in micrometers, millimeters, or encoder counts?

2. **Position range:** What are the valid min/max values for X, Y, Z, R?

3. **Relative vs absolute:** Does STAGE_POSITION_SET accept absolute or relative positions?

4. **Get all axes at once:** Is there a command to get all four axes in a single query? Or must we query individually (current implementation)?

5. **Motion completion:** After STAGE_POSITION_SET, should we expect a STAGE_MOTION_STOPPED unsolicited message? On which socket?

6. **Error codes:** What status codes can be returned in statusCode field (offset 8-11)?

7. **Unsolicited messages:** Should we implement a 3rd socket for unsolicited messages? Or is the current approach (no background listener) acceptable?

---

## Testing Plan

After meeting, test these commands with real hardware:

- [ ] CAMERA_IMAGE_SIZE_GET (should already work)
- [ ] CAMERA_PIXEL_FIELD_OF_VIEW_GET (should already work)
- [ ] STAGE_POSITION_GET with params[0] = 1 (X)
- [ ] STAGE_POSITION_GET with params[0] = 2 (Y)
- [ ] STAGE_POSITION_GET with params[0] = 3 (Z)
- [ ] STAGE_POSITION_GET with params[0] = 4 (R)
- [ ] STAGE_POSITION_SET with each axis
- [ ] Verify response position matches requested movement
- [ ] Confirm position units

---

## Code Changes Needed

Based on documentation, update:

**File:** `src/py2flamingo/services/stage_service.py`

**Method:** `get_position()` needs to specify axis:
```python
def get_position(self, axis: str) -> Optional[int]:
    """Query stage position for single axis."""
    axis_map = {'X': 1, 'Y': 2, 'Z': 3, 'R': 4}
    axis_code = axis_map[axis.upper()]

    params = [axis_code, 0, 0, 0, 0, 0, 0]  # params[0] = axis!

    result = self._query_command(
        StageCommandCode.POSITION_GET,
        f"STAGE_POSITION_GET_{axis}",
        params=params
    )

    return result['parsed']['params'][0]  # Position in response params[0]
```

**File:** `src/py2flamingo/views/connection_view.py:166`

Change from:
```python
self.debug_command_combo.addItem("✗ STAGE_POSITION_GET (24584)", ...)
```

To:
```python
self.debug_command_combo.addItem("✓ STAGE_POSITION_GET (24584)", ...)
```

---

## Documents Reference

| Document | Purpose | Key Information |
|----------|---------|-----------------|
| COMMUNICATION_PROTOCOL.md | Complete protocol spec | Protocol structure, examples, C++ pseudo-code |
| STAGE_POSITION_AXIS_PARAMETER.md | Stage command details | params[0] axis specification, all examples |
| THREADING_RACE_CONDITION_ANALYSIS.md | Threading analysis | Why no race condition, future considerations |
| MEETING_SUMMARY.md | This document | Quick reference for meeting |

---

**For the meeting, bring up:**
- COMMUNICATION_PROTOCOL.md on screen for reference
- STAGE_POSITION_AXIS_PARAMETER.md for specific examples
- Ask the questions listed above
- Take notes on units, error codes, and any other protocol details

Good luck with your meeting!
