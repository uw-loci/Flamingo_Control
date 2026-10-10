---
paths:
  - "src/py2flamingo/core/**"
  - "src/py2flamingo/services/**"
  - "src/py2flamingo/controllers/**"
  - "src/py2flamingo/*.py"
---

# TCP protocol and socket reader reference

Loads when you work on the protocol, service or controller code. It describes the wire format, the connection sequence and the async socket reader.

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
- `params[3]` (int32Data0): Axis code (1=X, 2=Y, 3=Z, 4=R)
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

## Connection Initialization Flow

### Signal Ordering for Connection

When connecting to the microscope, signals must be emitted in a specific order to avoid race conditions:

```
User clicks Connect
    ↓
TCP connection established
    ↓
connection_established.emit()     ← Triggers: enable controls, status indicator
    ↓
Settings retrieval (PAUSES SocketReader for synchronous I/O)
    ↓
settings_loaded.emit()            ← Triggers: position queries
    ↓
Position queries complete
```

**Key Signals (ConnectionView):**
- `connection_established` - TCP connection succeeded (immediate feedback)
- `settings_loaded` - Settings retrieval completed (safe to use async socket operations)
- `connection_error` - Communication error occurred (e.g., settings timeout)

**Why This Order Matters:**

The SocketReader is **paused** during settings retrieval to allow synchronous text reading. Any async operations (like position queries) that start before settings complete will have their responses lost because the reader isn't processing messages.

**Implementation:**
- `connection_established` → enables controls, sets status indicator
- `settings_loaded` → starts position queries via `_on_settings_loaded()`
- `connection_error` → sets ERROR status, re-enables Connect button

See `src/py2flamingo/views/connection_view.py` and `src/py2flamingo/application.py`.

---

## Async Socket Reader Architecture

### Overview

The Flamingo Control system uses a **background socket reader** for non-blocking command/response handling. This prevents socket buffer buildup and ensures unsolicited callbacks (like `STAGE_MOTION_STOPPED`) are never missed.

### Why Async Reading?

**Problem with Synchronous Reading:**
- GUI freezes during blocking socket reads
- Socket buffer fills up during concurrent operations (live view + stage movement)
- Unsolicited callbacks can be missed or delayed
- Position updates sent at 40Hz during motion can overwhelm the buffer

**Solution - Background Reader:**
- Dedicated thread continuously drains the command socket
- Messages are parsed and routed to appropriate queues
- Commands wait on response queues (non-blocking to GUI)
- Callbacks are delivered via registered handlers

### Architecture Components

```
┌─────────────────────────────────────────────────────────────────┐
│                      Application Layer                           │
│  ┌─────────────────┐   ┌─────────────────┐   ┌───────────────┐ │
│  │ MicroscopeCmd   │   │ MotionTracker   │   │ Other Services│ │
│  │ Service         │   │                 │   │               │ │
│  └────────┬────────┘   └────────┬────────┘   └───────┬───────┘ │
│           │                     │                     │         │
│           │ send_command_async  │ register_callback   │         │
│           ▼                     ▼                     ▼         │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    TCPConnection                          │  │
│  │  ┌─────────────────────────────────────────────────────┐ │  │
│  │  │                   CommandClient                      │ │  │
│  │  │  ┌─────────────┐     ┌──────────────────────────┐   │ │  │
│  │  │  │ SocketReader│────▶│   MessageDispatcher      │   │ │  │
│  │  │  │ (bg thread) │     │                          │   │ │  │
│  │  │  └──────┬──────┘     │  ┌──────────────────┐   │   │ │  │
│  │  │         │            │  │ Pending Requests │   │   │ │  │
│  │  │         │            │  │ (response queues)│   │   │ │  │
│  │  │         │            │  └──────────────────┘   │   │ │  │
│  │  │         │            │  ┌──────────────────┐   │   │ │  │
│  │  │         │            │  │ Callback Handlers│   │   │ │  │
│  │  │         │            │  │ (unsolicited)    │   │   │ │  │
│  │  │         │            │  └──────────────────┘   │   │ │  │
│  │  │         │            └──────────────────────────┘   │ │  │
│  │  └─────────┼───────────────────────────────────────────┘ │  │
│  └────────────┼─────────────────────────────────────────────┘  │
│               │                                                 │
└───────────────┼─────────────────────────────────────────────────┘
                │
                ▼
        ┌───────────────┐
        │ Command Socket│
        │ (TCP 53717)   │
        └───────────────┘
```

### Key Classes

#### `SocketReader` (`src/py2flamingo/core/socket_reader.py`)

Background thread that continuously reads 128-byte messages from the command socket.

```python
class SocketReader:
    MESSAGE_SIZE = 128
    START_MARKER = 0xF321E654
    END_MARKER = 0xFEDC4321

    def _read_loop(self):
        """Main loop - reads messages, handles additional data, dispatches"""
        while self._running:
            data = self._receive_message()  # 128 bytes
            message = self._parse_message(data)

            if message.is_valid:
                # CRITICAL: Read additional data BEFORE next message
                if message.additional_data_size > 0:
                    additional = self._read_additional_data(message.additional_data_size)
                    message.additional_data = additional

                self._dispatcher.dispatch(message)
```

#### `MessageDispatcher`

Routes parsed messages to appropriate destinations:

```python
class MessageDispatcher:
    def dispatch(self, message: ParsedMessage):
        # 1. Check if response to pending request
        if message.command_code in self._pending_requests:
            self._pending_requests[command_code].put(message)
            return

        # 2. Check if unsolicited callback with handler
        if message.command_code in self._callback_handlers:
            for handler in handlers:
                handler(message)
            return

        # 3. Unhandled - log for debugging
        logger.debug(f"Unhandled message 0x{command_code:04X}")
```

#### `ParsedMessage` Dataclass

Structured representation of a 128-byte protocol message:

```python
@dataclass
class ParsedMessage:
    raw_data: bytes           # Original 128 bytes
    start_marker: int         # 0xF321E654
    command_code: int         # Command identifier
    status_code: int          # Response status
    hardware_id: int          # params[0]
    subsystem_id: int         # params[1]
    client_id: int            # params[2]
    int32_data0: int          # params[3] - axis, laser index, etc.
    int32_data1: int          # params[4]
    int32_data2: int          # params[5]
    cmd_data_bits: int        # params[6] - flags
    value: float              # Double value (position, power, etc.)
    additional_data_size: int # Bytes following this message
    data_field: bytes         # 72-byte data buffer
    end_marker: int           # 0xFEDC4321
    timestamp: float          # When received
    additional_data: Optional[bytes] = None  # Extra data after message
```

### Handling Additional Data

**CRITICAL:** Some commands return extra data beyond the 128-byte response. This data **MUST** be read before the next message or the reader will lose sync.

```
Normal Message:
┌──────────────────────────────────┐
│     128-byte Message             │
│  (start marker ... end marker)   │
└──────────────────────────────────┘

Message with Additional Data:
┌──────────────────────────────────┐ ┌────────────────────┐
│     128-byte Message             │ │  Additional Data   │
│  (addDataBytes = N)              │ │  (N bytes)         │
└──────────────────────────────────┘ └────────────────────┘
```

**Commands that return additional data:**
- `SCOPE_SETTINGS_LOAD (4105)` - ~2800 bytes settings text
- Various query commands with string responses

**The SocketReader handles this automatically:**
```python
if message.additional_data_size > 0:
    additional = self._read_additional_data(message.additional_data_size)
    message.additional_data = additional
```

### Unsolicited Callbacks

The microscope sends some messages without being asked. These are **critical** to capture:

| Command Code | Name | Description |
|--------------|------|-------------|
| `0x6010` (24592) | `STAGE_MOTION_STOPPED` | Stage finished moving |

**Registering a callback handler:**
```python
# In MotionTracker
connection.register_callback(
    0x6010,  # STAGE_MOTION_STOPPED
    self._on_motion_stopped
)

def _on_motion_stopped(self, message: ParsedMessage):
    if message.status_code == 1:  # Success
        self.logger.info("Motion complete!")
        self._callback_queue.put(message)
```

### Resync Mechanism

If the reader gets out of sync (e.g., missed some bytes), it will see invalid markers. After 5 consecutive invalid messages, it attempts to resync:

```python
def _try_resync(self):
    """Scan for start marker to realign message boundaries"""
    search_data = self._socket.recv(512)
    marker_pos = search_data.find(START_MARKER_BYTES)
    if marker_pos >= 0:
        # Found marker - realign and continue
        ...
```

### Usage Examples

#### Sending a Command with Response

```python
# MicroscopeCommandService automatically uses async when available
result = service._query_command(
    command_code=STAGE_POSITION_GET,
    command_name="POSITION_GET",
    params=[0, 0, 0, 1, 0, 0, TRIGGER_CALL_BACK],  # Axis=X
    value=0.0
)
```

**What happens internally:**
1. Service encodes 128-byte command
2. Registers pending request with dispatcher (returns Queue)
3. Sends command via socket
4. Background reader receives response
5. Dispatcher puts response in the Queue
6. Service gets response from Queue (with timeout)

#### Waiting for Motion Complete

```python
# MotionTracker uses callback queue
tracker = MotionTracker(connection=connection)
success = tracker.wait_for_motion_complete(timeout=30.0)
```

**What happens internally:**
1. MotionTracker registers callback for `STAGE_MOTION_STOPPED`
2. When motion completes, microscope sends callback
3. Background reader receives and dispatches to handler
4. Handler puts message in internal queue
5. `wait_for_motion_complete` polls queue until message arrives

### Configuration

The async reader is **enabled by default**:

```python
# In TCPConnection.__init__
def __init__(self, use_async_reader: bool = True):
    ...

# To disable (use synchronous mode):
connection = TCPConnection(use_async_reader=False)
```

### Logging and Debugging

The async reader logs useful debug information:

```
INFO - Started async socket reader
INFO - Registered callback handler for 0x6010
DEBUG - Read 2800 additional bytes for SCOPE_SETTINGS_LOAD
DEBUG - Dispatched response for 0x6008
WARNING - Invalid message markers: start=0x00000000 (consecutive: 1)
INFO - Attempting to resync stream...
INFO - Resync successful
INFO - SocketReader read loop exiting. Stats: {'messages_read': 150, ...}
```

### Statistics

`SocketReader`, `MessageDispatcher`, and `CommandClient` (`src/py2flamingo/core/socket_reader.py`) each expose `get_stats()` with counters for debugging.
