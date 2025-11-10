# Flamingo Microscope Communication Protocol

## Overview

This document describes how Flamingo_Control communicates with the microscope server, including protocol details, threading architecture, and concrete examples. This is intended for developers working with the microscope server software (C++) to understand exactly what is being sent and how it's formatted.

**Document Version:** 1.0
**Date:** 2025-11-10
**Target Audience:** Microscope server developers (C++)

---

## Table of Contents

1. [Protocol Structure](#protocol-structure)
2. [Connection Establishment](#connection-establishment)
3. [Threading Architecture](#threading-architecture)
4. [Concrete Example: Get Image Size](#concrete-example-get-image-size)
5. [Message Formatting Details](#message-formatting-details)
6. [Command Codes Reference](#command-codes-reference)

---

## Protocol Structure

### Binary Command Format

The Flamingo protocol uses a **fixed 128-byte binary structure** for all commands and responses. This format ensures data integrity through start/end markers.

#### Byte Layout (128 bytes total)

| Offset | Size | Type   | Field Name      | Description                                    |
|--------|------|--------|-----------------|------------------------------------------------|
| 0-3    | 4    | uint32 | startMarker     | Always 0xF321E654 (little-endian)             |
| 4-7    | 4    | uint32 | commandCode     | Command/response code (see reference)         |
| 8-11   | 4    | uint32 | statusCode      | Status/error code                             |
| 12-15  | 4    | int32  | params[0]       | Parameter 0 (cmdBits0/int32Data0)             |
| 16-19  | 4    | int32  | params[1]       | Parameter 1 (cmdBits1/int32Data1)             |
| 20-23  | 4    | int32  | params[2]       | Parameter 2 (cmdBits2/int32Data2)             |
| 24-27  | 4    | int32  | params[3]       | Parameter 3 (cmdBits3/hardwareID)             |
| 28-31  | 4    | int32  | params[4]       | Parameter 4 (cmdBits4/subsystemID)            |
| 32-35  | 4    | int32  | params[5]       | Parameter 5 (cmdBits5/clientID)               |
| 36-39  | 4    | int32  | params[6]       | Parameter 6 (cmdBits6/cmdDataBits0) - FLAGS!  |
| 40-47  | 8    | double | value           | Floating point value                          |
| 48-51  | 4    | uint32 | addDataBytes    | Size of additional data following this packet |
| 52-123 | 72   | bytes  | data            | Data buffer (null-padded)                     |
| 124-127| 4    | uint32 | endMarker       | Always 0xFEDC4321 (little-endian)             |

**TOTAL: 128 bytes**

### Endianness

**All multi-byte fields use LITTLE-ENDIAN byte order.**

Example: The start marker 0xF321E654 is transmitted as bytes: `54 E6 21 F3`

### Python struct Format

The protocol is encoded/decoded using Python's `struct` module:

```python
struct.Struct("I I I I I I I I I I d I 72s I")
#              │ │ │ └──────7 params─────┘ │ │  │   │
#              │ │ │                        │ │  │   └─ End marker
#              │ │ │                        │ │  └───── Data (72 bytes)
#              │ │ │                        │ └──────── addDataBytes
#              │ │ │                        └─────────── value (double)
#              │ │ └──────────────────────────────────── status
#              │ └────────────────────────────────────── command code
#              └──────────────────────────────────────── Start marker
```

Where:
- `I` = unsigned int (4 bytes, little-endian)
- `i` = signed int (4 bytes, little-endian)
- `d` = double (8 bytes, IEEE 754, little-endian)
- `72s` = 72-byte string/buffer

### Critical: params[6] FLAGS

**params[6]** is a **bit field** used for command flags. The most critical flag is:

```python
TRIGGER_CALL_BACK = 0x80000000  # Bit 31 set
```

**IMPORTANT:** All query/GET commands MUST have `params[6] |= 0x80000000` set, or the microscope will not send a response and the command will timeout.

Other flags in params[6]:
- `0x00000001` - EXPERIMENT_TIME_REMAINING
- `0x00000002` - STAGE_POSITIONS_IN_BUFFER
- `0x00000004` - MAX_PROJECTION
- `0x00000008` - SAVE_TO_DISK
- `0x00000010` - STAGE_NOT_UPDATE_CLIENT
- `0x00000020` - STAGE_ZSWEEP

---

## Connection Establishment

### Dual Socket Architecture

The microscope uses **TWO TCP sockets** on sequential ports:

1. **Command Socket** (e.g., port 53717)
   - Sends commands
   - Receives 128-byte responses
   - Receives additional data (e.g., settings files)
   - Handles query/response communication

2. **Live Socket** (command port + 1, e.g., 53718)
   - Receives live image data streams
   - Separate from command/response to avoid blocking

### Connection Sequence

```
┌─────────────┐                                      ┌──────────────┐
│   Client    │                                      │   Microscope │
│ (Python)    │                                      │   (C++)      │
└──────┬──────┘                                      └──────┬───────┘
       │                                                     │
       │  1. Create command socket                          │
       │─────────────────────────────────────────────────>  │
       │     TCP connect to 127.0.0.1:53717                 │
       │     (with 2 second timeout)                        │
       │                                                     │
       │  <────────────────────────────────────────────────┤
       │     TCP accept                                     │
       │                                                     │
       │  2. Create live socket                             │
       │─────────────────────────────────────────────────>  │
       │     TCP connect to 127.0.0.1:53718                 │
       │                                                     │
       │  <────────────────────────────────────────────────┤
       │     TCP accept                                     │
       │                                                     │
       │  3. Clear command socket timeout                   │
       │     (set to None for blocking I/O)                 │
       │                                                     │
       │  4. Connection established                         │
       │                                                     │
```

### Code Flow (src/py2flamingo/core/tcp_connection.py:46-112)

```python
def connect(self, ip: str, port: int, timeout: float = 2.0):
    # 1. Create and connect command socket
    self._command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self._command_socket.settimeout(timeout)  # 2 second timeout
    self._command_socket.connect((ip, port))
    self._command_socket.settimeout(None)     # Clear timeout after connection

    # 2. Create and connect live socket (port + 1)
    live_port = port + 1
    self._live_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self._live_socket.settimeout(timeout)
    self._live_socket.connect((ip, live_port))
    self._live_socket.settimeout(None)

    return self._command_socket, self._live_socket
```

### Error Handling

Common connection errors:
- `socket.timeout` - Connection timeout (server not responding)
- `ConnectionRefusedError` - Server not listening on port
- `OSError: Network is unreachable` - Network configuration issue

---

## Threading Architecture

### Overview

Flamingo_Control uses a **multi-threaded architecture** to handle concurrent operations:

1. **Main Thread** - GUI event loop (PyQt5)
2. **Command Sender Thread** - Sends commands from queue
3. **Command Receiver Thread** - Receives responses (128 bytes)
4. **Live Receiver Thread** - Receives image data
5. **Processing Thread** - Processes received data
6. **Callback Listener Thread** - Listens for unsolicited messages

### Thread Responsibilities

```
┌──────────────────────────────────────────────────────────────┐
│                         MAIN THREAD                          │
│                      (PyQt5 GUI Event Loop)                  │
│                                                              │
│  User clicks button → Controller → Service                  │
└────────────┬─────────────────────────────────────────────────┘
             │
             ├── Queues command
             │
┌────────────▼─────────────────────────────────────────────────┐
│                    COMMAND SENDER THREAD                     │
│  • Monitors command queue                                    │
│  • Encodes commands using ProtocolEncoder                    │
│  • Sends via command_socket.sendall()                        │
│  • Non-blocking (daemon thread)                              │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                   COMMAND RECEIVER THREAD                    │
│  • Blocks on command_socket.recv(128)                        │
│  • Reads 128-byte responses                                  │
│  • Parses responses using ProtocolDecoder                    │
│  • Puts parsed data in response queue                        │
│  • Non-blocking (daemon thread)                              │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                     LIVE RECEIVER THREAD                     │
│  • Blocks on live_socket.recv(buffer_size)                   │
│  • Receives image data streams                               │
│  • Puts image data in live queue                             │
│  • Non-blocking (daemon thread)                              │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                     PROCESSING THREAD                        │
│  • Monitors response queue                                   │
│  • Dispatches data to appropriate handlers                   │
│  • Updates models/observers                                  │
│  • Non-blocking (daemon thread)                              │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                   CALLBACK LISTENER THREAD                   │
│  • Listens for unsolicited messages                          │
│  • Handles STAGE_MOTION_STOPPED (0x6010)                     │
│  • Dispatches to registered handlers                         │
│  • Non-blocking (daemon thread, 0.5s socket timeout)        │
└──────────────────────────────────────────────────────────────┘
```

### Thread Synchronization

**Key Synchronization Mechanisms:**

1. **Queue Manager** (py2flamingo/core/queue_manager.py)
   - Thread-safe queues for inter-thread communication
   - Queues: `command`, `response`, `live_data`, `other_data`

2. **Event Manager** (py2flamingo/core/events.py)
   - Threading events for signaling
   - Events: `send`, `receive`, `disconnect`

3. **Socket Locks** (threading.Lock)
   - Protects socket send/receive operations
   - Prevents concurrent access to sockets

### Callback Listener Details

The callback listener runs in a background thread monitoring the command socket for **unsolicited messages** (messages not in response to a query):

**Location:** `src/py2flamingo/services/callback_listener.py:111-162`

```python
def _listen_loop(self):
    # Set socket to non-blocking with short timeout
    self.command_socket.settimeout(0.5)

    while not self._stop_event.is_set():
        try:
            # Try to receive a message (128 bytes)
            data = self._receive_message(128)

            if data:
                # Parse and dispatch to registered handler
                parsed = self._parse_response(data)
                command_code = parsed['command_code']

                if command_code in self._handlers:
                    self._handlers[command_code](parsed)

        except socket.timeout:
            continue  # Expected - allows checking stop_event
```

**Known Unsolicited Messages:**
- `STAGE_MOTION_STOPPED` (0x6010 / 24592) - Stage finished moving

---

## Concrete Example: Get Image Size

This section traces a complete request/response cycle for the `CAMERA_IMAGE_SIZE_GET` command (code 12327 / 0x3027).

### High-Level Flow

```
User clicks "Connect"
  → ConnectionView (GUI)
    → ConnectionController.connect()
      → MVCConnectionService.connect()
        → TCPConnection.connect()
          [Sockets established]

User requests image size
  → [Some UI action or initialization]
    → CameraService.get_image_size()
      → MicroscopeCommandService._query_command()
        → ProtocolEncoder.encode_command()
          [128-byte command created]
        → command_socket.sendall(cmd_bytes)
          [Command sent to microscope]
        → command_socket.recv(128)
          [Response received]
        → ProtocolDecoder.decode_command()
          [Response parsed]
        → Extract params[3] and params[4]
          [width and height returned]
```

### Step-by-Step Trace

#### 1. User/GUI Layer

**File:** `src/py2flamingo/views/connection_view.py:162`

User might trigger this through a debug command dropdown or initialization sequence.

#### 2. Service Layer - CameraService

**File:** `src/py2flamingo/services/camera_service.py:42-71`

```python
def get_image_size(self) -> Tuple[int, int]:
    """Get camera image dimensions in pixels."""
    self.logger.info("Getting camera image size...")

    result = self._query_command(
        CameraCommandCode.IMAGE_SIZE_GET,  # 12327
        "CAMERA_IMAGE_SIZE_GET"
    )

    if not result['success']:
        raise RuntimeError(f"Failed to get image size: {result.get('error')}")

    params = result['parsed']['params']
    width = params[3]   # X dimension in Param[3]
    height = params[4]  # Y dimension in Param[4]

    self.logger.info(f"Camera image size: {width}x{height} pixels")
    return (width, height)
```

#### 3. Base Service - Query Command

**File:** `src/py2flamingo/services/microscope_command_service.py:35-125`

```python
def _query_command(self, command_code: int, command_name: str,
                   params=None, value=0.0):
    """Send query command and return parsed response."""

    # Ensure params[6] has TRIGGER_CALL_BACK flag
    if params is None:
        params = [0] * 7

    # CRITICAL: Set bit 31 to trigger response
    params[6] = 0x80000000  # CommandDataBits.TRIGGER_CALL_BACK

    # Encode command
    cmd_bytes = self.connection.encoder.encode_command(
        code=command_code,
        status=0,
        params=params,
        value=value,
        data=b''
    )

    # Send command
    command_socket = self.connection._command_socket
    command_socket.sendall(cmd_bytes)

    # Read 128-byte response
    ack_response = self._receive_full_bytes(command_socket, 128, timeout=3.0)

    # Parse response
    parsed = self._parse_response(ack_response)

    return {
        'success': True,
        'parsed': parsed,
        'raw_response': ack_response
    }
```

#### 4. Protocol Encoding

**File:** `src/py2flamingo/core/tcp_protocol.py:122-268`

```python
def encode_command(self, code: int, status: int = 0,
                   params: List[int] = None, value: float = 0.0,
                   data: bytes = b'', additional_data_size: int = 0):
    """Encode command into 128-byte binary format."""

    # Prepare params (pad to 7 elements)
    if params is None:
        params = [0] * 7
    elif len(params) < 7:
        params = list(params) + [0] * (7 - len(params))

    # Ensure data is exactly 72 bytes
    if len(data) > 72:
        data = data[:72]
    else:
        data = data.ljust(72, b'\x00')

    # Pack into binary structure
    command_bytes = struct.pack(
        "I I I I I I I I I I d I 72s I",  # Format string
        0xF321E654,             # Start marker
        code,                   # Command code (12327 for IMAGE_SIZE_GET)
        status,                 # Status (0)
        params[0],              # 0
        params[1],              # 0
        params[2],              # 0
        params[3],              # 0
        params[4],              # 0
        params[5],              # 0
        params[6],              # 0x80000000 (TRIGGER_CALL_BACK)
        value,                  # 0.0
        additional_data_size,   # 0
        data,                   # 72 null bytes
        0xFEDC4321              # End marker
    )

    return command_bytes  # Exactly 128 bytes
```

#### 5. Network Transmission

The 128 bytes are sent via `socket.sendall()`:

```python
command_socket.sendall(cmd_bytes)
```

**IMPORTANT:** `sendall()` ensures all 128 bytes are sent, handling partial sends automatically.

#### 6. What the Microscope Receives

**Hex dump of the command packet:**

```
Offset  00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F  ASCII
------  -----------------------------------------------  ----------------
0x0000  54 E6 21 F3 27 30 00 00 00 00 00 00 00 00 00 00  T.!.'0..........
0x0010  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  ................
0x0020  00 00 00 00 00 00 00 80 00 00 00 00 00 00 00 00  ................
0x0030  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  ................
0x0040  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  ................
0x0050  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  ................
0x0060  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  ................
0x0070  00 00 00 00 21 43 DC FE                          ....!C..
```

**Breakdown:**
- `54 E6 21 F3` - Start marker (0xF321E654 little-endian)
- `27 30 00 00` - Command code (0x00003027 = 12327 little-endian)
- `00 00 00 00` - Status (0)
- Next 28 bytes - params[0-6], all zeros except...
- `00 00 00 80` at offset 0x24 - params[6] = 0x80000000 (TRIGGER_CALL_BACK)
- `00 00 00 00 00 00 00 00` at offset 0x28 - value (0.0 double)
- `00 00 00 00` at offset 0x30 - addDataBytes (0)
- 72 null bytes - data buffer
- `21 43 DC FE` - End marker (0xFEDC4321 little-endian)

#### 7. Microscope Response

The microscope sends back a 128-byte response with the same structure:

**Expected response structure:**

```
Offset  Field           Value                   Notes
------  -----           -----                   -----
0x0000  startMarker     0xF321E654              Same as request
0x0004  commandCode     12327 (0x3027)          Echo of request command
0x0008  statusCode      0 or error code         0 = success
0x000C  params[0]       0
0x0010  params[1]       0
0x0014  params[2]       0
0x0018  params[3]       2048                    IMAGE WIDTH in pixels
0x001C  params[4]       2048                    IMAGE HEIGHT in pixels
0x0020  params[5]       0
0x0024  params[6]       0x80000000              Echo of TRIGGER_CALL_BACK
0x0028  value           0.0
0x0030  addDataBytes    0
0x0034  data            72 null bytes
0x007C  endMarker       0xFEDC4321
```

**Example hex dump (for 2048x2048 camera):**

```
Offset  00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F
------  -----------------------------------------------
0x0000  54 E6 21 F3 27 30 00 00 00 00 00 00 00 00 00 00
0x0010  00 00 00 00 00 00 00 00 00 08 00 00 00 08 00 00
        ^params[3]=2048^  ^params[4]=2048^
0x0020  00 00 00 00 00 00 00 80 00 00 00 00 00 00 00 00
0x0030  00 00 00 00 00 00 00 00 ...
0x0070  ... 21 43 DC FE
```

Note: `00 08 00 00` = 0x00000800 = 2048 (little-endian)

#### 8. Response Reception and Parsing

**File:** `src/py2flamingo/services/microscope_command_service.py:157-183`

```python
def _receive_full_bytes(self, sock, num_bytes, timeout=3.0):
    """Receive exact number of bytes from socket."""
    sock.settimeout(timeout)
    data = b''
    while len(data) < num_bytes:
        chunk = sock.recv(num_bytes - len(data))
        if not chunk:
            raise RuntimeError(f"Socket closed (got {len(data)}/{num_bytes})")
        data += chunk
    return data
```

**File:** `src/py2flamingo/services/microscope_command_service.py:185-247`

```python
def _parse_response(self, response: bytes):
    """Parse 128-byte protocol response."""
    if len(response) != 128:
        raise ValueError(f"Invalid response size: {len(response)}")

    start_marker = struct.unpack('<I', response[0:4])[0]
    command_code = struct.unpack('<I', response[4:8])[0]
    status_code = struct.unpack('<I', response[8:12])[0]

    # Unpack 7 parameters
    params = []
    for i in range(7):
        offset = 12 + (i * 4)
        param = struct.unpack('<i', response[offset:offset+4])[0]
        params.append(param)

    value = struct.unpack('<d', response[40:48])[0]
    add_data_bytes = struct.unpack('<I', response[48:52])[0]
    end_marker = struct.unpack('<I', response[124:128])[0]

    # Validate markers
    if start_marker != 0xF321E654:
        logger.warning(f"Invalid start marker: 0x{start_marker:08X}")
    if end_marker != 0xFEDC4321:
        logger.warning(f"Invalid end marker: 0x{end_marker:08X}")

    return {
        'start_marker': start_marker,
        'command_code': command_code,
        'status_code': status_code,
        'params': params,        # params[3]=width, params[4]=height
        'value': value,
        'reserved': add_data_bytes,
        'end_marker': end_marker
    }
```

#### 9. Return to Caller

**File:** `src/py2flamingo/services/camera_service.py:66-70`

```python
params = result['parsed']['params']
width = params[3]   # Extract width from params[3]
height = params[4]  # Extract height from params[4]

self.logger.info(f"Camera image size: {width}x{height} pixels")
return (width, height)
```

### Summary of Data Flow

```
CameraService.get_image_size()
  ↓
MicroscopeCommandService._query_command(12327, "CAMERA_IMAGE_SIZE_GET")
  ↓
ProtocolEncoder.encode_command(code=12327, params=[0,0,0,0,0,0,0x80000000])
  ↓
[128 bytes] = 54 E6 21 F3 27 30 00 00 ... 00 00 00 80 ... 21 43 DC FE
  ↓
command_socket.sendall(128 bytes) → TCP → Microscope C++ Server
  ↓
                     [Microscope processes command]
  ↓
Microscope C++ Server → TCP → 128 bytes response
  ↓
command_socket.recv(128) → receives response
  ↓
ProtocolDecoder.decode_command(response)
  ↓
{
  'command_code': 12327,
  'status_code': 0,
  'params': [0, 0, 0, 2048, 2048, 0, 0x80000000],
  'value': 0.0,
  ...
}
  ↓
Extract params[3]=2048 (width), params[4]=2048 (height)
  ↓
Return (2048, 2048)
```

---

## Message Formatting Details

### Text Encoding

**For 72-byte data buffer:**

- Encoding: **UTF-8** (Python default)
- Padding: Null bytes (`\x00`)
- Max length: 72 bytes

**Example (workflow filename):**

```python
data = "Snapshot.txt".encode('utf-8')  # b'Snapshot.txt'
data = data.ljust(72, b'\x00')         # Pad to 72 bytes
```

**Important:** The 72-byte data field is **padded with null bytes**, not spaces or line endings.

### Line Endings (Historical Issues)

**Previous version issues:**

The user mentioned issues with "enter/return formatting at the end of lines in windows vs linux and the text encoding."

**Current implementation:**
- **Binary protocol commands (128 bytes):** No line endings - fixed binary structure
- **Workflow files sent as additional data:** Read as binary (`rb` mode), sent as-is
- **Settings files received:** Text data after 128-byte ack, decoded as UTF-8

**Example workflow send (src/py2flamingo/services/communication/tcp_client.py:65-82):**

```python
def send_workflow(self, workflow_file: str, command: int):
    # Read workflow file as BINARY (no line ending conversion)
    with open(workflow_file, "rb") as f:
        data = f.read()

    # Send header (128 bytes)
    header = struct.pack(
        "I I I I I I I I I I d I 72s I",
        0xF321E654,     # Start
        command,        # WORKFLOW_START (12292)
        0, 0, 0, 0, 0, 0, 0, 1,  # params, note params[3]=1 indicates data follows
        0.0,            # value
        len(data),      # addDataBytes = size of workflow file
        b"".ljust(72, b"\x00"),
        0xFEDC4321      # End
    )

    # Send header then data
    self.nuc_socket.send(header)
    self.nuc_socket.send(data)
```

**Key points for C++ developers:**
1. The 128-byte command structure has **no line endings** - it's pure binary
2. Workflow files are sent **as-is** with their original line endings (CRLF on Windows, LF on Linux)
3. The `addDataBytes` field specifies exactly how many bytes follow the 128-byte header
4. Read workflow data using `recv(addDataBytes)`, not line-by-line reading

### Socket Settings

**Python side:**

```python
# After connection established
socket.settimeout(None)  # Blocking I/O
socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)  # Keep-alive

# For sends
socket.sendall(data)  # Ensures all bytes sent, handles partial sends

# For receives (command responses)
data = b''
while len(data) < expected_size:
    chunk = socket.recv(expected_size - len(data))
    if not chunk:
        raise ConnectionError("Socket closed")
    data += chunk
```

**Recommendations for C++ server:**
- Use `recv()` in a loop until all expected bytes received
- For 128-byte commands, read exactly 128 bytes
- For additional data, read exactly `addDataBytes` bytes
- Don't rely on packet boundaries - TCP is a stream protocol

---

## Command Codes Reference

### Camera Commands (0x3000 range)

| Code  | Hex    | Name                          | Description                                |
|-------|--------|-------------------------------|--------------------------------------------|
| 12292 | 0x3004 | CAMERA_WORKFLOW_START         | Start workflow execution                   |
| 12293 | 0x3005 | CAMERA_WORKFLOW_STOP          | Stop current workflow                      |
| 12294 | 0x3006 | CAMERA_SNAPSHOT               | Take single image                          |
| 12295 | 0x3007 | CAMERA_LIVE_VIEW_START        | Start continuous imaging                   |
| 12296 | 0x3008 | CAMERA_LIVE_VIEW_STOP         | Stop continuous imaging                    |
| 12327 | 0x3027 | CAMERA_IMAGE_SIZE_GET         | Query camera resolution (returns in params[3,4]) |
| 12343 | 0x3037 | CAMERA_PIXEL_FIELD_OF_VIEW_GET| Query pixel size (returns in value field) |

### Stage Commands (0x6000 range)

| Code  | Hex    | Name                  | Description                         |
|-------|--------|-----------------------|-------------------------------------|
| 24580 | 0x6004 | STAGE_POSITION_SET    | Move single axis to position       |
| 24584 | 0x6008 | STAGE_POSITION_GET    | Query single axis position         |
| 24592 | 0x6010 | STAGE_MOTION_STOPPED  | Unsolicited: stage finished moving |

**CRITICAL:** Stage commands require `params[0]` (int32Data0) to specify which axis:

| params[0] | Axis | Description |
|-----------|------|-------------|
| 1 | X | X-axis position |
| 2 | Y | Y-axis position |
| 3 | Z | Z-axis (focus) position |
| 4 | R | R-axis (rotation) position |

**Without setting params[0], the command will NOT return a response!**

See [STAGE_POSITION_AXIS_PARAMETER.md](STAGE_POSITION_AXIS_PARAMETER.md) for detailed examples.

### System Commands (0xA000 range)

| Code  | Hex    | Name                       | Description                      |
|-------|--------|----------------------------|----------------------------------|
| 4105  | 0x1009 | SCOPE_SETTINGS_LOAD        | Load settings from microscope    |
| 40962 | 0xA002 | SYSTEM_STATE_IDLE          | Set system to idle state         |
| 40967 | 0xA007 | SYSTEM_STATE_GET           | Query system state               |

### Response Data Locations

Different commands return data in different fields:

| Command                        | Data Location                      | Type   |
|--------------------------------|------------------------------------|--------|
| CAMERA_IMAGE_SIZE_GET          | params[3] = width, params[4] = height | int32 |
| CAMERA_PIXEL_FIELD_OF_VIEW_GET | value = pixel_size_mm              | double |
| STAGE_POSITION_GET             | params[0] = position (for axis specified in request params[0]) | int32  |
| SYSTEM_STATE_GET               | params[0] = state_code             | int32  |
| SCOPE_SETTINGS_LOAD            | Additional text data after 128-byte ack | UTF-8 text |

**Note:** STAGE_POSITION_GET queries ONE axis at a time. To get all positions, send 4 separate commands with params[0] = 1, 2, 3, 4.

---

## Code References

### Key Files

| File | Purpose | Lines |
|------|---------|-------|
| `src/py2flamingo/core/tcp_protocol.py` | Protocol encoder/decoder | 1-454 |
| `src/py2flamingo/core/tcp_connection.py` | Socket management | 1-367 |
| `src/py2flamingo/services/connection_service.py` | Connection orchestration | 1-895 |
| `src/py2flamingo/services/microscope_command_service.py` | Base command service | 1-248 |
| `src/py2flamingo/services/camera_service.py` | Camera-specific commands | 1-180 |
| `src/py2flamingo/services/callback_listener.py` | Unsolicited message handler | 1-240 |
| `src/py2flamingo/controllers/connection_controller.py` | Connection logic | 1-350+ |
| `src/py2flamingo/views/connection_view.py` | GUI connection view | 1-200+ |

### Testing

To test the protocol, run the mock server and test client:

```bash
# Terminal 1: Start mock server
cd Flamingo_Control
python mock_server.py

# Terminal 2: Run test
python test_connection.py
```

The mock server (`mock_server.py:1-200+`) implements the protocol on the server side and can be used as a reference for C++ implementation.

---

## Appendix A: Complete Example Code

### Sending IMAGE_SIZE_GET Command (Python)

```python
import socket
import struct

# Connect to microscope
ip = "127.0.0.1"
port = 53717
command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
command_socket.connect((ip, port))

# Prepare command
command_code = 12327  # CAMERA_IMAGE_SIZE_GET
params = [0, 0, 0, 0, 0, 0, 0x80000000]  # TRIGGER_CALL_BACK in params[6]

# Encode 128-byte command
cmd_bytes = struct.pack(
    "I I I I I I I I I I d I 72s I",
    0xF321E654,      # Start marker
    command_code,    # Command
    0,               # Status
    *params,         # 7 parameters
    0.0,             # Value
    0,               # addDataBytes
    b'\x00' * 72,    # Data buffer
    0xFEDC4321       # End marker
)

# Send command
command_socket.sendall(cmd_bytes)

# Receive response (128 bytes)
response = b''
while len(response) < 128:
    chunk = command_socket.recv(128 - len(response))
    if not chunk:
        raise ConnectionError("Connection closed")
    response += chunk

# Parse response
start = struct.unpack('<I', response[0:4])[0]
resp_code = struct.unpack('<I', response[4:8])[0]
status = struct.unpack('<I', response[8:12])[0]

# Extract image size from params[3] and params[4]
width = struct.unpack('<i', response[24:28])[0]   # params[3]
height = struct.unpack('<i', response[28:32])[0]  # params[4]

print(f"Image size: {width} x {height} pixels")

command_socket.close()
```

### C++ Server Pseudo-code

```cpp
// Server-side handling of IMAGE_SIZE_GET

#include <cstdint>
#include <arpa/inet.h>  // For ntohs, htonl, etc.

#pragma pack(push, 1)
struct Command {
    uint32_t startMarker;      // 0xF321E654
    uint32_t commandCode;
    uint32_t statusCode;
    int32_t  params[7];
    double   value;
    uint32_t addDataBytes;
    uint8_t  data[72];
    uint32_t endMarker;        // 0xFEDC4321
};
#pragma pack(pop)

void handleImageSizeGet(int socket) {
    Command response = {};

    // Fill response
    response.startMarker = 0xF321E654;
    response.commandCode = 12327;  // Echo command code
    response.statusCode = 0;       // Success

    // Set image dimensions
    response.params[3] = 2048;     // Width
    response.params[4] = 2048;     // Height
    response.params[6] = 0x80000000; // Echo TRIGGER_CALL_BACK

    response.value = 0.0;
    response.addDataBytes = 0;
    response.endMarker = 0xFEDC4321;

    // Send response (128 bytes)
    send(socket, &response, sizeof(Command), 0);
}

void processCommand(int socket, const Command& cmd) {
    // Validate markers
    if (cmd.startMarker != 0xF321E654 || cmd.endMarker != 0xFEDC4321) {
        // Invalid command
        return;
    }

    // Check for TRIGGER_CALL_BACK flag
    bool needsResponse = (cmd.params[6] & 0x80000000) != 0;

    switch (cmd.commandCode) {
        case 12327:  // CAMERA_IMAGE_SIZE_GET
            if (needsResponse) {
                handleImageSizeGet(socket);
            }
            break;

        case 12343:  // CAMERA_PIXEL_FIELD_OF_VIEW_GET
            // ... handle other commands
            break;
    }
}
```

---

## Appendix B: Troubleshooting

### Common Issues

**1. Command times out (no response)**

**Cause A:** params[6] doesn't have TRIGGER_CALL_BACK flag (0x80000000) set

**Solution:**
```python
params[6] = 0x80000000  # Or use bitwise OR to combine flags
```

**Cause B:** Stage commands missing axis specification in params[0]

**Solution:**
```python
# For STAGE_POSITION_GET or STAGE_POSITION_SET
params[0] = 1  # 1=X, 2=Y, 3=Z, 4=R
params[6] = 0x80000000  # Also need TRIGGER_CALL_BACK
```

**2. Invalid start/end markers**

**Cause:** Byte order mismatch (big-endian vs little-endian)

**Solution:** Ensure all fields use little-endian:
```python
struct.unpack('<I', ...)  # '<' means little-endian
```

**3. Connection refused**

**Cause:** Server not listening on specified port

**Solution:** Check server is running and listening on correct port

**4. Socket closed during receive**

**Cause:** Server crashed or disconnected

**Solution:** Check server logs, add error handling

**5. Partial data received**

**Cause:** Not reading in loop until all bytes received

**Solution:**
```python
data = b''
while len(data) < expected_size:
    chunk = sock.recv(expected_size - len(data))
    data += chunk
```

---

## Revision History

| Version | Date       | Changes                                      |
|---------|------------|----------------------------------------------|
| 1.0     | 2025-11-10 | Initial version with complete protocol docs  |
| 1.1     | 2025-11-10 | Added CRITICAL info: Stage commands require params[0] axis specification (feedback from C++ developer) |

---

**For questions or issues, contact:** [Your team contact information]

**Repository:** `Flamingo_Control/`
**Generated from codebase version:** Current main branch
