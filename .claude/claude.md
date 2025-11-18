# Claude Code Project Guidelines

## Environment Setup

### Python Virtual Environment

This project uses a Python virtual environment located at `.venv/` in the project root.

**To activate the virtual environment:**
```bash
source .venv/bin/activate
```

**To install/update dependencies:**
```bash
source .venv/bin/activate
pip install -r requirements.txt
```

**To run tests or scripts:**
```bash
source .venv/bin/activate
python test_3d_visualization.py  # or any other script
```

**Important Notes:**
- Always activate the virtual environment before running Python commands
- The `.venv/` directory is already configured with project dependencies
- Use `requirements.txt` for production dependencies
- Use `requirements-minimal-3d.txt` for minimal 3D visualization setup

## Development Workflow

### Remote Testing Requirement

**IMPORTANT**: This project is tested on a **remote computer** that is physically connected to the microscope hardware, NOT on the local development machine.

**Workflow Requirements:**
1. **Always commit changes** after making modifications
2. **Always push to GitHub** immediately after committing
3. **User tests on remote PC** - changes cannot be tested locally
4. **Wait for test results** before proceeding with additional changes

**Why This Matters:**
- The microscope hardware is only accessible from the remote PC
- Local testing is not possible for hardware-dependent features
- Changes must be pushed to GitHub for the user to pull and test
- Do not make multiple sets of changes without getting test feedback

**Best Practice:**
```
1. Make focused changes to address specific issue
2. git commit with clear description
3. git push origin main
4. STOP and wait for user feedback from remote testing
5. Analyze test results before next iteration
```

## Documentation Structure

### Root Directory - User-Facing Documentation ONLY
The main `Flamingo_Control/` directory should contain **only** documentation that end users need:
- `README.md` - Project overview and quick start
- `INSTALLATION.md` - Installation and setup instructions
- Usage guides and how-to documents

**Do NOT place technical reports, implementation details, or development logs in the root directory.**

### Claude Reports Directory - Technical Documentation
All technical reports, implementation details, session summaries, and development documentation should be placed **OUTSIDE the repository** in:

```
/home/msnelson/LSControl/claude-reports/
```

**IMPORTANT:** This directory is at the **same level** as `Flamingo_Control/`, NOT inside it. Reports should NOT be committed to GitHub.

**Naming Convention:**
All files in `claude-reports/` must follow this naming pattern:
```
YYYY-MM-DD-descriptive-name.md
```

Examples:
- `2024-11-05-position-display-fix.md`
- `2024-11-04-mvc-architecture-implementation.md`
- `2024-11-05-network-path-solution.md`

### What Goes in claude-reports/

**Include:**
- Implementation reports and technical summaries
- Bug fix documentation and root cause analysis
- Architecture decisions and design documentation
- Session summaries and work logs
- Integration verification reports
- Code refactoring summaries
- API documentation for internal components
- Development insights and lessons learned

**Do NOT Include:**
- User-facing installation guides
- Usage tutorials for end users
- Project README content
- Marketing or overview materials

## File Organization Rules

### Creating New Documentation

When creating any technical or development documentation:

1. **Always** place it in `claude-reports/`
2. **Always** include the date in the filename: `YYYY-MM-DD-`
3. Use lowercase with hyphens: `network-path-solution` not `Network_Path_Solution`
4. Be descriptive but concise in the filename

### Updating Existing Documentation

- User docs (README, INSTALLATION): Update in place in root
- Technical docs: Create a new dated file in `claude-reports/`
- Reference the previous report if updating/superseding it

## Example Structure

```
LSControl/
├── Flamingo_Control/             # Git repository (goes to GitHub)
│   ├── README.md                # User-facing project overview
│   ├── INSTALLATION.md          # User-facing setup guide
│   ├── .claude/
│   │   └── claude.md           # This file
│   └── src/                     # Source code
└── claude-reports/               # Technical docs (NOT in git)
    ├── 2024-11-04-mvc-refactor.md
    ├── 2024-11-05-position-fix.md
    ├── 2024-11-05-network-paths.md
    └── 2024-11-05-gui-improvements.md
```

## Why This Structure?

1. **Clean Root**: Users see only what they need without wading through development history
2. **Chronological**: Date-prefixed files naturally sort chronologically
3. **Discoverable**: All technical docs in one place (`claude-reports/`)
4. **Organized**: Clear separation between user docs and developer docs
5. **Maintainable**: Easy to archive or reference historical implementations

## Commit Guidelines

When committing documentation:
- **DO NOT commit technical reports** - they belong in `claude-reports/` outside the repository
- Commits should reference the report file in the commit message (e.g., "See claude-reports/2024-11-06-stage-control.md")
- Keep user-facing docs (README, INSTALLATION) up to date with actual functionality
- Only commit user-facing documentation in the repository root

## TCP Protocol Structure

### Binary Command/Response Format

The Flamingo microscope uses a **128-byte fixed binary protocol** for all TCP communication (both commands sent TO microscope and responses FROM microscope).

#### Protocol Structure (128 bytes total)

```
Byte Offset | Size | Field Name      | Type    | Description
------------|------|-----------------|---------|----------------------------------
0-3         | 4    | Start Marker    | uint32  | 0xF321E654 (validates packet)
4-7         | 4    | Command Code    | uint32  | Command identifier (see CommandCodes.h)
8-11        | 4    | Status          | uint32  | Status code (1=IDLE, 0=BUSY, etc.)
12-15       | 4    | cmdBits0        | int32   | Parameter 0 (usage varies by command)
16-19       | 4    | cmdBits1        | int32   | Parameter 1
20-23       | 4    | cmdBits2        | int32   | Parameter 2
24-27       | 4    | cmdBits3        | int32   | Parameter 3
28-31       | 4    | cmdBits4        | int32   | Parameter 4
32-35       | 4    | cmdBits5        | int32   | Parameter 5
36-39       | 4    | cmdBits6        | int32   | Parameter 6
40-47       | 8    | Value           | double  | Floating-point value
48-51       | 4    | addDataBytes    | uint32  | Size of additional data after packet
52-123      | 72   | Data            | bytes   | Arbitrary data field (null-padded)
124-127     | 4    | End Marker      | uint32  | 0xFEDC4321 (validates packet)
```

#### Python struct Format String

```python
struct.Struct("I I I I I I I I I I d I 72s I")
#              │ │ │ │ │ │ │ │ │ │ │ │  │  │
#              │ │ │ │ │ │ │ │ │ │ │ │  │  └─ End Marker
#              │ │ │ │ │ │ │ │ │ │ │ │  └──── Data (72 bytes)
#              │ │ │ │ │ │ │ │ │ │ │ └─────── addDataBytes
#              │ │ │ │ │ │ │ │ │ │ └────────── Value (double)
#              │ │ │ │ │ │ │ │ │ └──────────── cmdBits6 (Param[6])
#              │ │ │ │ │ │ │ │ └────────────── cmdBits5 (Param[5])
#              │ │ │ │ │ │ │ └──────────────── cmdBits4 (Param[4])
#              │ │ │ │ │ │ └────────────────── cmdBits3 (Param[3])
#              │ │ │ │ │ └──────────────────── cmdBits2 (Param[2])
#              │ │ │ │ └────────────────────── cmdBits1 (Param[1])
#              │ │ │ └──────────────────────── cmdBits0 (Param[0])
#              │ │ └────────────────────────── Status
#              │ └──────────────────────────── Command Code
#              └────────────────────────────── Start Marker
```

#### Two-Part Responses

Some commands send additional data after the 128-byte structure:

1. **128-byte Binary Acknowledgment** - Standard protocol structure
2. **Additional Data** - Variable-length data (size indicated by `addDataBytes`)

**Examples:**
- `SCOPE_SETTINGS_LOAD (4105)`: Sends 128-byte ack + ~2800 bytes of settings text
- `SCOPE_SETTINGS_SAVE (4104)`: Receives 128-byte command + settings file data

**IMPORTANT:** When reading these responses:
- Always read the 128-byte ack first
- Check `addDataBytes` field or use `select()` to detect additional data
- Read additional data in chunks until socket is empty
- Do NOT decode the 128-byte ack as UTF-8 text (it's binary protocol)
- Only decode the additional data as text if it's a text response

#### Field Usage by Command Type

Different commands use the fields differently:

**Position Commands (STAGE_POSITION_SET):**
- `cmdBits0` (Param[0]): Axis code (1=X, 2=Y, 3=Z, 4=R)
- `cmdBits6` (Param[6]): **MUST** be `0x80000000` (TRIGGER_CALL_BACK) for response
- `Value`: Position in millimeters or degrees

**Camera Query Commands:**
- `cmdBits6` (Param[6]): **MUST** be `0x80000000` (TRIGGER_CALL_BACK) for response
- `CAMERA_PIXEL_FIELD_OF_VIEW_GET`: Returns pixel size in `Value` field (mm/pixel)
- `CAMERA_IMAGE_SIZE_GET`: Returns dimensions in parameter fields

**System State:**
- `SYSTEM_STATE_GET`: Returns state in `Status` field (1=IDLE, 0=BUSY)
- `cmdBits3` (Param[3]): May contain state code (40962=IDLE)

**File Transfer Commands:**
- `addDataBytes`: Contains size of file being transferred
- Command structure sent first, then file data

**Workflow Commands (WORKFLOW_START):**
- `cmdBits6` (Param[6]): Workflow behavior flags (see below)
- `addDataBytes`: Size of workflow file data
- Old code used `0x00000001` (EXPERIMENT_TIME_REMAINING)

#### Command Data Bits Flags (params[6] / cmdBits6)

The `cmdBits6` field (params[6]) contains bit flags that control command behavior.
These flags can be combined using bitwise OR (`|`). From `CommandCodes.h`:

```
enum COMMAND_DATA_BITS {
    TRIGGER_CALL_BACK           = 0x80000000,  // Query commands - triggers response
    EXPERIMENT_TIME_REMAINING   = 0x00000001,  // Timelapse/long experiments
    STAGE_POSITIONS_IN_BUFFER   = 0x00000002,  // Multi-position workflows
    MAX_PROJECTION              = 0x00000004,  // Z-stack MIP computation
    SAVE_TO_DISK                = 0x00000008,  // Save images (vs. live view only)
    STAGE_NOT_UPDATE_CLIENT     = 0x00000010,  // Suppress position updates
    STAGE_ZSWEEP                = 0x00000020,  // Z-stack operation
}
```

**Usage Examples:**

Query command (MUST have response):
```python
params[6] = 0x80000000  # TRIGGER_CALL_BACK
```

Z-stack with MIP saved to disk:
```python
params[6] = 0x00000020 | 0x00000004 | 0x00000008  # ZSWEEP | MAX_PROJ | SAVE
```

Multi-position timelapse:
```python
params[6] = 0x00000002 | 0x00000008 | 0x00000001  # POSITIONS | SAVE | TIME
```

**CRITICAL: Query/GET Commands Require TRIGGER_CALL_BACK Flag:**
- For query commands (e.g., `CAMERA_IMAGE_SIZE_GET`, `STAGE_POSITION_GET`), `cmdBits6` (Param[6]) **MUST** be set to `0x80000000`
- This is the `COMMAND_DATA_BITS_TRIGGER_CALL_BACK` flag from `CommandCodes.h`
- Without this flag, the microscope receives the command but **does not send a response**
- Result: 3-second timeout waiting for response that never arrives
- **Always set params[6] = 0x80000000 for any GET/query command**
- **DO NOT use TRIGGER_CALL_BACK for workflow commands** - use workflow-specific flags

Example (correct):
```python
cmd_bytes = encoder.encode_command(
    code=CAMERA_IMAGE_SIZE_GET,
    status=0,
    params=[0, 0, 0, 0, 0, 0, 0x80000000],  # TRIGGER_CALL_BACK flag
    value=0.0,
    data=b''
)
```

Example (incorrect - will timeout):
```python
cmd_bytes = encoder.encode_command(
    code=CAMERA_IMAGE_SIZE_GET,
    status=0,
    params=[0, 0, 0, 0, 0, 0, 0],  # Missing TRIGGER_CALL_BACK - no response!
    value=0.0,
    data=b''
)
```

#### Packet Validation

Both start and end markers must be correct:
- Start: `0xF321E654`
- End: `0xFEDC4321`

If markers don't match, packet is invalid/corrupted.

#### Log File Analysis - Client ID Identification

**IMPORTANT:** When analyzing server log files to debug command issues:

- **Working C++ GUI commands**: `clientID ≠ 0` (typically `clientID = 24` or other non-zero values)
- **Python GUI commands**: `clientID = 0`

#### Field Name Mapping - C++ Server Logs vs Python params Array

**CRITICAL:** The C++ `SCommand` struct has hardwareID/subsystemID/clientID BEFORE int32Data0/int32Data1/int32Data2!

```
C++ SCommand Struct Field Order (128 bytes):
  Bytes 0-3:   cmdStart (start marker 0xF321E654)
  Bytes 4-7:   cmd (command code)
  Bytes 8-11:  status
  Bytes 12-15: hardwareID         ← Server logs call this "hardwareID"
  Bytes 16-19: subsystemID        ← Server logs call this "subsystemID"
  Bytes 20-23: clientID           ← Server logs call this "clientID"
  Bytes 24-27: int32Data0         ← Server logs call this "int32Data0" (LASER INDEX here!)
  Bytes 28-31: int32Data1         ← Server logs call this "int32Data1"
  Bytes 32-35: int32Data2         ← Server logs call this "int32Data2"
  Bytes 36-39: cmdDataBits0       ← Server logs call this "cmdDataBits0"
  Bytes 40-47: doubleData
  Bytes 48-51: additionalDataBytes
  Bytes 52-123: buffer[72]
  Bytes 124-127: cmdEnd (end marker 0xFEDC4321)

Python params Array Usage (MATCHES C++ struct order directly):
  params[0] = hardwareID     (typically 0)                          → byte offset 12-15
  params[1] = subsystemID    (typically 0)                          → byte offset 16-19
  params[2] = clientID       (0 for Python GUI, non-zero for C++)  → byte offset 20-23
  params[3] = int32Data0     (axis/laser_index)                    → byte offset 24-27 ← CRITICAL!
  params[4] = int32Data1                                            → byte offset 28-31
  params[5] = int32Data2                                            → byte offset 32-35
  params[6] = cmdDataBits0   (typically 0x80000000 for queries)    → byte offset 36-39
```

**Example Usage:**

Stage position query (X-axis):
```python
params = [0, 0, 0, 1, 0, 0, 0x80000000]  # axis=1 in params[3]
```

Laser enable (laser 3):
```python
params = [0, 0, 0, 3, 0, 0, 0x80000000]  # laser_index=3 in params[3]
```

**Why This Matters:**
The params array is packed DIRECTLY into the C++ struct - no remapping!
- params[0] → hardwareID at byte offset 12
- params[3] → int32Data0 at byte offset 24 (where axis/laser index goes)

#### Implementation

See `src/py2flamingo/core/tcp_protocol.py`:
- `ProtocolEncoder.encode_command()` - Creates 128-byte packets
- `ProtocolDecoder.decode_command()` - Parses 128-byte packets

### Communication Architecture

**Queue-Based Communication Pattern:**

The system uses a queue-based architecture to avoid socket contention between threads:

```
Application Code
    ↓ (put command)
Command Queue
    ↓ (send thread reads)
TCP Socket → Microscope
    ↓ (response)
Listener Thread
    ↓ (parse & route)
Other Data Queue
    ↓ (get response)
Application Code
```

**Key Components:**
- `command` queue: Commands to send to microscope
- `send` event: Triggers send thread to process command queue
- `other_data` queue: Responses from microscope (populated by listener)
- `command_listen_thread`: Continuously reads socket, routes responses to queues

**Why This Pattern:**
- Prevents race conditions (only listener thread reads from socket)
- Multiple threads can send commands safely via queue
- Listener routes responses based on command code
- No blocking - uses event signaling

**Implementation:**
All command sending (including debug queries) uses this pattern:
1. Clear `other_data` queue
2. Put command on `command` queue
3. Set `send` event
4. Wait for response on `other_data` queue

See `position_controller.py:debug_query_command()` for reference implementation.

## Error Handling Guidelines

### Unified Error Format Requirements

**IMPORTANT:** All new code and refactored code MUST use the unified error handling framework defined in `src/py2flamingo/core/errors.py`. This ensures consistent error reporting, better debugging, and a professional user experience.

### Error Handling Principles

1. **Use FlamingoError and Subclasses**: Never raise generic Python exceptions (ValueError, RuntimeError, etc.) directly
2. **Include Rich Context**: Every error MUST include WHERE, WHAT, and WHY information
3. **Separate User vs Technical Messages**: User-friendly messages for UI, technical details for logs
4. **Use Error Codes**: Enable programmatic error handling with specific error codes
5. **Log at Appropriate Levels**: Use severity levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)

### Required Error Components

Every error MUST include:

```python
from py2flamingo.core.errors import (
    FlamingoError, ErrorCode, ErrorContext, ErrorSeverity,
    ConnectionError, HardwareError, ValidationError, TimeoutError
)

# Create context for WHERE and WHAT
context = ErrorContext(
    module="module_name",           # Which module
    function="function_name",       # Which function
    operation="what_was_attempted", # What operation
    component="affected_component", # Which component (e.g., "Y-axis")
    attempted_value=value,          # What value caused the error
    valid_range="0.0-12.0mm"       # What the valid range is
)

# Raise appropriate error type
raise HardwareError(
    code=ErrorCode.HARDWARE_STAGE_LIMIT_EXCEEDED,
    message="Position out of range",  # User-friendly
    technical_details=f"Y={value}mm exceeds max 12.0mm",  # Technical
    context=context,
    severity=ErrorSeverity.ERROR
)
```

### Error Categories and When to Use Them

| Category | Use For | Example Codes |
|----------|---------|---------------|
| **ConnectionError** | Network/socket issues | CONNECTION_TIMEOUT, CONNECTION_REFUSED |
| **HardwareError** | Microscope hardware problems | HARDWARE_STAGE_MOVEMENT_FAILED |
| **ValidationError** | Input validation failures | VALIDATION_OUT_OF_RANGE |
| **TimeoutError** | Operation timeouts | TIMEOUT_MOTION_COMPLETE |

### Service Layer Pattern

```python
class ServiceClass:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.error_logger = ErrorLogger(self.logger)

    def method(self, param):
        context = ErrorContext(
            module="service_name",
            function="method",
            operation="operation_description"
        )

        try:
            # Operation
            result = self._do_something()
            if not result.success:
                # Use the error from result
                self.error_logger.log_error(result.error)
                raise result.error

        except socket.timeout as e:
            # Wrap standard exceptions
            error = self.error_logger.wrap_and_log(
                e,
                ErrorCode.TIMEOUT_COMMAND_RESPONSE,
                "Operation timed out",
                context=context
            )
            raise error from e
```

### Controller Layer Pattern

Controllers can either:

1. **Return tuples with error objects** (for backward compatibility):
```python
def connect(self, ip: str, port: int) -> Tuple[bool, str, Optional[FlamingoError]]:
    try:
        self._service.connect(ip, port)
        return (True, f"Connected to {ip}:{port}", None)
    except FlamingoError as e:
        self.error_logger.log_error(e)
        return (False, e.get_user_message(), e)
```

2. **Raise exceptions** (preferred for new code):
```python
def connect(self, ip: str, port: int) -> None:
    # Let FlamingoError propagate to view layer
    self._service.connect(ip, port)
```

### View Layer Pattern

```python
try:
    self.controller.operation()
    self.show_success("Operation completed")

except FlamingoError as e:
    # Error already logged by lower layers
    formatter = ErrorFormatter()

    # Show appropriate dialog based on severity
    if e.severity in [ErrorSeverity.ERROR, ErrorSeverity.CRITICAL]:
        QMessageBox.critical(
            self,
            f"{e.category.value.title()} Error",
            formatter.format_for_user(e)
        )
    else:
        QMessageBox.warning(
            self,
            f"{e.category.value.title()}",
            formatter.format_for_user(e)
        )

except Exception as e:
    # Unexpected error - should be rare
    self.logger.exception("Unexpected error")
    QMessageBox.critical(
        self,
        "Unexpected Error",
        "An unexpected error occurred. Check the log file for details."
    )
```

### DO NOT Do This

```python
# BAD: Generic exception with string message
raise ValueError("Position out of range")

# BAD: Logging without context
self.logger.error("Failed")

# BAD: Returning None on error
if error:
    return None

# BAD: Catching all exceptions
except Exception as e:
    print(str(e))

# BAD: Tuple returns without error object
return (False, "Connection failed")
```

### Error Code Ranges

Error codes are organized by category:

- **1000-1999**: Connection errors
- **2000-2999**: Hardware errors
- **3000-3999**: Validation errors
- **4000-4999**: Timeout errors
- **5000-5999**: Filesystem errors
- **6000-6999**: Configuration errors
- **7000-7999**: State errors
- **8000-8999**: Protocol errors
- **9000-9999**: System errors

When adding new error codes, add them to the appropriate range in `src/py2flamingo/core/errors.py`.

### Migration from Old Patterns

When refactoring existing code:

1. **Replace generic exceptions** with FlamingoError subclasses
2. **Add ErrorContext** with complete WHERE/WHAT/WHY information
3. **Use ErrorLogger** for consistent logging
4. **Preserve backward compatibility** during transition (can return error objects in tuples)
5. **Update tests** to expect FlamingoError types

### Testing Error Handling

```python
def test_invalid_position():
    """Test that invalid position raises appropriate error."""
    controller = StageController()

    with pytest.raises(HardwareError) as exc_info:
        controller.move_to_position(y=15.0)  # Max is 12.0

    error = exc_info.value
    assert error.code == ErrorCode.HARDWARE_STAGE_LIMIT_EXCEEDED
    assert error.context.component == "Y-axis"
    assert error.context.attempted_value == 15.0
    assert "12.0" in error.context.valid_range
```

### Benefits of Unified Error Handling

1. **Consistent User Experience**: All errors look and behave the same way
2. **Better Debugging**: Rich context shows exactly what went wrong and why
3. **Programmatic Handling**: Error codes enable specific error recovery
4. **Professional Logs**: Structured logging with appropriate severity levels
5. **Maintainable Code**: Clear patterns for error handling throughout codebase

---

**Last Updated:** 2024-11-17
**Maintained By:** Claude Code assistant
