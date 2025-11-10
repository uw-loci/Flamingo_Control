# Stage Position Commands - Axis Parameter Requirement

## Critical Update from Microscope Server Developer

**Date:** 2025-11-10
**Source:** Feedback from microscope server developer

---

## Summary

For **STAGE_POSITION_GET** and **ALL movement commands**, you MUST set `int32Data0` (params[0]) to specify which axis:

| params[0] Value | Axis | Description |
|-----------------|------|-------------|
| 1 | X | Query X-axis only |
| 2 | Y | Query Y-axis only |
| 3 | Z | Query Z-axis (focus) only |
| 4 | R | Query R-axis (rotation) only |
| 0xFF (255) | ALL | Query all four axes in single command |

**Without setting params[0], the command will NOT return a response.**

**Recommended:** Use `params[0] = 0xFF` to get all positions at once.

---

## Command Code Reference

| Command | Code | Hex | Description |
|---------|------|-----|-------------|
| STAGE_POSITION_GET | 24584 | 0x6008 | Query single axis position |
| STAGE_POSITION_SET | 24580 | 0x6004 | Move single axis |

---

## Examples

### Example 1: Query X-Axis Position

```python
import struct

# Command: STAGE_POSITION_GET for X-axis
command_code = 24584
params = [
    1,              # params[0] = 1 for X-axis
    0,              # params[1]
    0,              # params[2]
    0,              # params[3]
    0,              # params[4]
    0,              # params[5]
    0x80000000      # params[6] = TRIGGER_CALL_BACK flag (REQUIRED!)
]

# Encode command (128 bytes)
cmd_bytes = struct.pack(
    "I I I I I I I I I I d I 72s I",
    0xF321E654,      # Start marker
    command_code,    # 24584 (STAGE_POSITION_GET)
    0,               # Status
    *params,         # 7 parameters (params[0]=1 for X)
    0.0,             # Value
    0,               # addDataBytes
    b'\x00' * 72,    # Data buffer
    0xFEDC4321       # End marker
)

# Send command
command_socket.sendall(cmd_bytes)

# Receive response (128 bytes)
response = receive_full(command_socket, 128)

# Parse response - X position returned in params[0]
x_position = struct.unpack('<i', response[12:16])[0]
print(f"X position: {x_position}")
```

**Hex dump of command (first 48 bytes):**
```
Offset  00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F
------  -----------------------------------------------
0x0000  54 E6 21 F3 08 60 00 00 00 00 00 00 01 00 00 00
        ^Start Mark ^Cmd=24584   ^Status=0   ^params[0]=1 (X-axis!)
0x0010  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
0x0020  00 00 00 00 00 00 00 80 00 00 00 00 00 00 00 00
                                ^params[6]=0x80000000
```

### Example 2: Query Z-Axis Position

```python
# Command: STAGE_POSITION_GET for Z-axis (focus)
params = [
    3,              # params[0] = 3 for Z-axis
    0, 0, 0, 0, 0,
    0x80000000      # TRIGGER_CALL_BACK
]

cmd_bytes = struct.pack(
    "I I I I I I I I I I d I 72s I",
    0xF321E654, 24584, 0,
    *params,
    0.0, 0, b'\x00' * 72,
    0xFEDC4321
)

command_socket.sendall(cmd_bytes)
response = receive_full(command_socket, 128)

# Z position returned in params[0] of response
z_position = struct.unpack('<i', response[12:16])[0]
print(f"Z position: {z_position}")
```

### Example 3: Query All Axes (RECOMMENDED - Single Command)

```python
def get_stage_position(command_socket):
    """Query all four axes at once using params[0] = 0xFF."""

    # Build command with params[0] = 0xFF for all axes
    params = [0xFF, 0, 0, 0, 0, 0, 0x80000000]

    cmd_bytes = struct.pack(
        "I I I I I I I I I I d I 72s I",
        0xF321E654,      # Start marker
        24584,           # STAGE_POSITION_GET
        0,               # Status
        *params,         # params[0]=0xFF queries all axes
        0.0,             # Value
        0,               # addDataBytes
        b'\x00' * 72,    # Data buffer
        0xFEDC4321       # End marker
    )

    # Send and receive
    command_socket.sendall(cmd_bytes)
    response = receive_full(command_socket, 128)

    # All positions returned in params[0-3] of response
    x_pos = struct.unpack('<i', response[12:16])[0]   # params[0]
    y_pos = struct.unpack('<i', response[16:20])[0]   # params[1]
    z_pos = struct.unpack('<i', response[20:24])[0]   # params[2]
    r_pos = struct.unpack('<i', response[24:28])[0]   # params[3]

    return {'X': x_pos, 'Y': y_pos, 'Z': z_pos, 'R': r_pos}

# Usage
pos = get_stage_position(command_socket)
print(f"Stage position: X={pos['X']}, Y={pos['Y']}, Z={pos['Z']}, R={pos['R']}")
```

**Hex dump of command:**
```
Offset  00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F
------  -----------------------------------------------
0x0000  54 E6 21 F3 08 60 00 00 00 00 00 00 FF 00 00 00
        ^Start Mark ^Cmd=24584   ^Status=0   ^params[0]=0xFF (ALL AXES!)
```

### Example 4: Move X-Axis (STAGE_POSITION_SET)

```python
# Command: STAGE_POSITION_SET for X-axis
command_code = 24580  # POSITION_SET
target_position = 5000  # Target X position in micrometers (or encoder units)

params = [
    1,              # params[0] = 1 for X-axis
    0,              # params[1]
    0,              # params[2]
    0,              # params[3]
    0,              # params[4]
    0,              # params[5]
    0x80000000      # params[6] = TRIGGER_CALL_BACK
]

# For SET commands, position goes in the 'value' field (double)
cmd_bytes = struct.pack(
    "I I I I I I I I I I d I 72s I",
    0xF321E654,
    command_code,       # 24580 (STAGE_POSITION_SET)
    0,
    *params,
    float(target_position),  # Value = target position!
    0,
    b'\x00' * 72,
    0xFEDC4321
)

command_socket.sendall(cmd_bytes)
response = receive_full(command_socket, 128)

print(f"Move X to {target_position} - command sent")
```

---

## Python Implementation (Using ProtocolEncoder)

### Correct Implementation for StageService

**File:** `src/py2flamingo/services/stage_service.py`

#### Get Position (Single Axis)

```python
def get_position(self, axis: str) -> Optional[int]:
    """
    Query stage position for a single axis.

    Args:
        axis: Axis to query - 'X', 'Y', 'Z', or 'R'

    Returns:
        Position value or None if query fails
    """
    # Map axis name to params[0] value
    axis_map = {'X': 1, 'Y': 2, 'Z': 3, 'R': 4}

    if axis.upper() not in axis_map:
        raise ValueError(f"Invalid axis: {axis}. Must be X, Y, Z, or R")

    axis_code = axis_map[axis.upper()]

    self.logger.info(f"Querying {axis} position from hardware...")

    # Build params with axis code in params[0]
    params = [
        axis_code,      # params[0] = axis to query
        0, 0, 0, 0, 0,  # params[1-5] unused
        0               # params[6] will be set by _query_command
    ]

    result = self._query_command(
        StageCommandCode.POSITION_GET,
        f"STAGE_POSITION_GET_{axis}",
        params=params,
        value=0.0
    )

    if not result['success']:
        self.logger.error(f"Failed to get {axis} position: {result.get('error')}")
        return None

    # Position is returned in params[0] of response
    position = result['parsed']['params'][0]
    self.logger.info(f"{axis} position: {position}")
    return position
```

#### Get All Positions

```python
def get_all_positions(self) -> Optional[Dict[str, int]]:
    """
    Query all four axis positions.

    Returns:
        Dict with keys 'X', 'Y', 'Z', 'R' and position values,
        or None if any query fails
    """
    positions = {}

    for axis in ['X', 'Y', 'Z', 'R']:
        pos = self.get_position(axis)
        if pos is None:
            self.logger.error(f"Failed to get {axis} position")
            return None
        positions[axis] = pos

    self.logger.info(f"Stage position: {positions}")
    return positions
```

#### Move to Position (Single Axis)

```python
def move_axis(self, axis: str, position: float) -> bool:
    """
    Move a single axis to target position.

    Args:
        axis: Axis to move - 'X', 'Y', 'Z', or 'R'
        position: Target position

    Returns:
        True if command successful
    """
    axis_map = {'X': 1, 'Y': 2, 'Z': 3, 'R': 4}

    if axis.upper() not in axis_map:
        raise ValueError(f"Invalid axis: {axis}. Must be X, Y, Z, or R")

    axis_code = axis_map[axis.upper()]

    self.logger.info(f"Moving {axis} to {position}")

    # Build params with axis code in params[0]
    params = [
        axis_code,      # params[0] = axis to move
        0, 0, 0, 0, 0,  # params[1-5] unused
        0               # params[6] will be set by _send_command
    ]

    result = self._send_command(
        StageCommandCode.POSITION_SET,
        f"STAGE_POSITION_SET_{axis}",
        params=params,
        value=float(position)  # Target position goes in value field
    )

    if not result['success']:
        self.logger.error(f"Failed to move {axis}: {result.get('error')}")
        return False

    self.logger.info(f"{axis} move command sent successfully")
    return True
```

---

## Response Format

### STAGE_POSITION_GET Response

When querying position, the response format is:

```
Offset  Field           Description
------  -----           -----------
0x0000  startMarker     0xF321E654
0x0004  commandCode     24584 (echo of request)
0x0008  statusCode      0 = success, non-zero = error
0x000C  params[0]       POSITION VALUE (for requested axis)
0x0010  params[1]       Unused (0)
0x0014  params[2]       Unused (0)
0x0018  params[3]       Unused (0)
0x001C  params[4]       Unused (0)
0x0020  params[5]       Unused (0)
0x0024  params[6]       Echo of TRIGGER_CALL_BACK (0x80000000)
0x0028  value           0.0 (unused)
0x0030  addDataBytes    0
0x0034  data            72 null bytes
0x007C  endMarker       0xFEDC4321
```

**Key Point:** The position value is returned in **params[0]** (offset 0x000C, bytes 12-15).

### Example Response Hex Dump

Query X-axis, current position = 1500:

```
Offset  00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F
------  -----------------------------------------------
0x0000  54 E6 21 F3 08 60 00 00 00 00 00 00 DC 05 00 00
        ^Start Mark ^Cmd=24584   ^Status=0   ^params[0]=1500 (0x05DC)
0x0010  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
0x0020  00 00 00 00 00 00 00 80 00 00 00 00 00 00 00 00
                                ^params[6]=0x80000000
```

Parse: `struct.unpack('<i', bytes[12:16])` → 1500

---

## Units

**IMPORTANT:** Clarify with microscope developer what units the position values use:

- Micrometers (µm)?
- Millimeters (mm)?
- Encoder counts?
- Other?

**TODO:** Update this document once units are confirmed.

---

## Why Previous Implementation Failed

### Old Code (INCORRECT)

```python
# In stage_service.py:86-89 (WRONG!)
result = self._query_command(
    StageCommandCode.POSITION_GET,
    "STAGE_POSITION_GET"
)
# params defaults to [0, 0, 0, 0, 0, 0, 0]
# params[0] = 0 means "no axis specified" → NO RESPONSE!
```

This is why the connection_view.py marked it as:
```python
# Line 166
self.debug_command_combo.addItem("✗ STAGE_POSITION_GET (24584)", ...)
# "✗ = Not implemented in firmware (times out)"
```

**It wasn't a firmware problem - it was missing params[0]!**

---

## C++ Server Implementation Hints

For the C++ developer implementing the server:

### Handling STAGE_POSITION_GET

```cpp
void handleStagePositionGet(int socket, const Command& cmd) {
    // Extract which axis is requested
    int32_t axis = cmd.params[0];  // 1=X, 2=Y, 3=Z, 4=R

    if (axis < 1 || axis > 4) {
        // Invalid axis - send error response
        sendErrorResponse(socket, cmd.commandCode, ERROR_INVALID_PARAMETER);
        return;
    }

    // Check if TRIGGER_CALL_BACK flag is set
    if ((cmd.params[6] & 0x80000000) == 0) {
        // No callback requested - don't send response
        return;
    }

    // Get current position for requested axis
    int32_t position;
    switch (axis) {
        case 1: position = stage_controller.getXPosition(); break;
        case 2: position = stage_controller.getYPosition(); break;
        case 3: position = stage_controller.getZPosition(); break;
        case 4: position = stage_controller.getRPosition(); break;
    }

    // Build response
    Command response = {};
    response.startMarker = 0xF321E654;
    response.commandCode = cmd.commandCode;  // Echo command code
    response.statusCode = 0;  // Success
    response.params[0] = position;  // POSITION GOES HERE!
    response.params[6] = cmd.params[6];  // Echo TRIGGER_CALL_BACK
    response.endMarker = 0xFEDC4321;

    // Send response
    send(socket, &response, sizeof(Command), 0);
}
```

### Handling STAGE_POSITION_SET

```cpp
void handleStagePositionSet(int socket, const Command& cmd) {
    int32_t axis = cmd.params[0];  // 1=X, 2=Y, 3=Z, 4=R
    double target_position = cmd.value;  // Target position from value field!

    if (axis < 1 || axis > 4) {
        sendErrorResponse(socket, cmd.commandCode, ERROR_INVALID_PARAMETER);
        return;
    }

    // Initiate movement
    bool success = false;
    switch (axis) {
        case 1: success = stage_controller.moveX(target_position); break;
        case 2: success = stage_controller.moveY(target_position); break;
        case 3: success = stage_controller.moveZ(target_position); break;
        case 4: success = stage_controller.moveR(target_position); break;
    }

    // Send acknowledgment if TRIGGER_CALL_BACK set
    if (cmd.params[6] & 0x80000000) {
        Command response = {};
        response.startMarker = 0xF321E654;
        response.commandCode = cmd.commandCode;
        response.statusCode = success ? 0 : ERROR_MOVEMENT_FAILED;
        response.params[6] = cmd.params[6];
        response.endMarker = 0xFEDC4321;

        send(socket, &response, sizeof(Command), 0);
    }

    // When movement completes, send STAGE_MOTION_STOPPED unsolicited message
    // (This would be sent from movement completion callback)
}
```

---

## Testing Checklist

- [ ] Test STAGE_POSITION_GET with params[0] = 1 (X-axis)
- [ ] Test STAGE_POSITION_GET with params[0] = 2 (Y-axis)
- [ ] Test STAGE_POSITION_GET with params[0] = 3 (Z-axis)
- [ ] Test STAGE_POSITION_GET with params[0] = 4 (R-axis)
- [ ] Test STAGE_POSITION_GET with params[0] = 0 (should fail/timeout)
- [ ] Test STAGE_POSITION_SET with each axis
- [ ] Verify position units (µm, mm, counts?)
- [ ] Test without TRIGGER_CALL_BACK flag (should get no response)
- [ ] Verify response params[0] contains position value

---

## Update Main Protocol Document

This information should be added to `COMMUNICATION_PROTOCOL.md` in the Stage Commands section.

### Key Changes Needed:

1. Update command reference table to indicate params[0] requirement
2. Add axis code mapping table
3. Update hex dump examples to show params[0] = 1
4. Remove "NOT IMPLEMENTED" note from STAGE_POSITION_GET
5. Add note about single-axis queries (not all axes at once)

---

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025-11-10 | Initial documentation based on developer feedback |

---

**Questions for Follow-up:**

1. What units do position values use? (µm, mm, encoder counts?)
2. What is the valid range for each axis?
3. Does STAGE_POSITION_SET accept relative or absolute positions?
4. Is there a command to get all four axes in a single query, or must we query each individually?
5. After STAGE_POSITION_SET, should we expect a STAGE_MOTION_STOPPED unsolicited message?
