# Flamingo Control System - Claude Project Guide

## Project Overview

The Flamingo Control System is a Python-based GUI application for controlling a light-sheet fluorescence microscope. It provides a modern interface for:

- Stage positioning and movement control (X, Y, Z, rotation)
- Camera control and live viewing
- Workflow creation and execution
- Laser and LED illumination management
- Filter wheel control
- Beam steering (MEMS mirror / Galvo scanner)

### Architecture

**Client-Side (Python):**
- Location: `/home/msnelson/LSControl/Flamingo_Control/src/py2flamingo/`
- Framework: PySide6 (Qt6)
- Pattern: Model-View-Controller (MVC)
- Communication: TCP/IP sockets with binary protocol

**Server-Side (Linux C++):**
- Location: `/home/msnelson/LSControl/Flamingo_Control/oldcodereference/serversidecode/Linux/ControlSystem/`
- Purpose: Reference implementation (not to be modified)
- Protocol: Custom binary RPC-style protocol

---

## Critical Communication Protocol Information

### TCP Connection Details

| Purpose | Port | Max Clients | Description |
|---------|------|-------------|-------------|
| Control Commands | 53717 | 3 | Main command/response channel |
| Live View Data | 53718 | 5 | Image streaming for preview |
| Stack Data | 53719 | 5 | Image streaming for workflows |

**Timeouts:**
- Read: 100ms (control), 500ms (image header), 5000ms (image data)
- Write: 1000ms

### Core Protocol: SCommand Structure

**Fixed 128-byte packet format:**

```python
import struct

class SCommand:
    STRUCT_FORMAT = '<II7i1d72sI'  # Little-endian
    SIZE = 128

    def __init__(self, cmd_code=0):
        self.cmdStart = 0xF321E654      # Validation marker (required)
        self.cmd = cmd_code             # Command code
        self.status = 0                 # 0=fail, 1=success
        self.hardwareID = 0
        self.subsystemID = 0
        self.clientID = 0
        self.int32Data0 = 0             # Generic parameter 1
        self.int32Data1 = 0             # Generic parameter 2
        self.int32Data2 = 0             # Generic parameter 3
        self.cmdDataBits0 = 0x80000000  # Callback flag
        self.doubleData = 0.0           # 8-byte float
        self.additionalDataBytes = 0    # Payload size
        self.buffer = b'\x00' * 72      # 72-byte buffer
        self.cmdEnd = 0xFEDC4321        # Validation marker (required)
```

**With optional payload:**
```
[SCommand - 128 bytes][Additional Data - N bytes]
```
Where N ≤ 33,554,432 (32 MB max).

### Key Command Codes by Subsystem

**Stage Control (0x00006000 - 0x00006FFF):**
- `0x00006001` - HOME (home axis)
- `0x00006002` - HALT (stop motion)
- `0x00006003` - POSITION_SET (absolute move)
- `0x00006007` - POSITION_GET (query position)
- `0x00006008` - VELOCITY_SET (set speed)
- `0x00006009` - MOTION_STOPPED (notification)
- `0x0000600D` - WAIT_FOR_MOTION_TO_STOP (monitor motion)

**Camera Control (0x00003000 - 0x00003FFF):**
- `0x00003006` - SNAPSHOT_GET (single frame)
- `0x00003007` - LIVE_VIEW_START (streaming)
- `0x00003008` - LIVE_VIEW_STOP (stop streaming)
- `0x00003009` - EXPOSURE_SET (exposure time in μs)
- `0x00003004` - WORK_FLOW_START (start acquisition)
- `0x00003005` - WORK_FLOW_STOP (stop acquisition)

**System State (0x0000A000 - 0x0000AFFF):**
- `0x0000A001` - DISCONNECTED
- `0x0000A002` - IDLE
- `0x0000A003` - SNAP_SHOT
- `0x0000A004` - LIVE_VIEW
- `0x0000A005` - WORK_FLOW_RUNNING
- `0x0000A007` - SYSTEM_STATE_GET (query)

**Full reference:** See `/claude-reports/server-side-protocol-reference.md`

### Stage Movement Protocol

**Absolute positioning:**
```python
# Move Z-axis to 15.0 mm
cmd = SCommand(0x00006003)  # POSITION_SET
cmd.int32Data0 = 3          # Axis Z
cmd.doubleData = 15.0       # Position in mm
send_command(cmd)

# Server responds with status in cmd.status
# Then sends position updates every 25ms during motion
# Finally sends MOTION_STOPPED (0x00006009) when done
```

**Position query:**
```python
# Get current position
cmd = SCommand(0x00006007)  # POSITION_GET
cmd.int32Data0 = 3          # Axis Z
response = send_and_receive(cmd)
position = response.doubleData  # Position in mm
```

**Axis codes:**
- 1 = X-axis (mm)
- 2 = Y-axis (mm)
- 3 = Z-axis (mm)
- 4 = R-axis (degrees)
- 0xFF = All axes

### Image Data Protocol

**Live View Sequence:**

1. **Start live view on control port:**
```python
cmd = SCommand(0x00003007)  # LIVE_VIEW_START
send_on_port_53717(cmd)
```

2. **Connect to data port 53718**

3. **Receive images in loop:**
```python
while live_view_active:
    # Receive header (40 bytes)
    header = receive_bytes(40)
    image_header = parse_image_header(header)

    # Receive image data
    data_size = image_header.imageWidth * image_header.imageHeight * 2
    image_data = receive_bytes(data_size)

    # Parse as 16-bit pixels
    pixels = np.frombuffer(image_data, dtype=np.uint16)
    image = pixels.reshape((image_header.imageHeight, image_header.imageWidth))
```

4. **Stop live view:**
```python
cmd = SCommand(0x00003008)  # LIVE_VIEW_STOP
send_on_port_53717(cmd)
```

**ImageHeader structure (40 bytes):**
```python
struct ImageHeader:
    uint32 imageSize          # Total data size
    uint32 imageWidth         # Width in pixels
    uint32 imageHeight        # Height in pixels
    uint32 imageScaleMin      # Display min (0)
    uint32 imageScaleMax      # Display max (1023)
    uint32 deviceIndex        # Camera 1 or 2
    uint32 optionsSettings1   # Display flags
    uint32 optionsSettings2   # Crosshair position
    int32  imageIndexStart    # Stack start
    int32  imageIndexStop     # Stack stop
```

### Workflow Execution Protocol

**Workflow settings format (text-based):**
```
<Workflow Settings>
  <Experiment Settings>
    2  Plane spacing (um) = 2.5
    2  Frame rate (f/s) = 100.0
    2  Exposure time (us) = 9500
    2  Sample = MySample
  </Experiment Settings>
  <Stack Settings>
    2  Change in Z axis (mm) = 5.0
    2  Number of planes = 200
  </Stack Settings>
  <Start Position>
    2  X (mm) = 10.5
    2  Z (mm) = 15.0
  </Start Position>
  <End Position>
    2  X (mm) = 10.5
    2  Z (mm) = 20.0
  </End Position>
</Workflow Settings>
```

**Starting a workflow:**
```python
# Prepare workflow settings string
workflow_settings = build_workflow_settings(...)

# Send command with settings as payload
cmd = SCommand(0x00003004)  # WORK_FLOW_START
cmd.additionalDataBytes = len(workflow_settings)
send_bytes(struct.pack(cmd) + workflow_settings.encode())

# Monitor for callbacks:
# - SYSTEM_STATE_WORK_FLOW_RUNNING (0x0000A005)
# - UI_SET_WORK_FLOW_INDEX (current stack)
# - CAMERA_STACK_COMPLETE (per stack completion)
# - SYSTEM_STATE_IDLE (0x0000A002) when done
```

---

## Project Structure

```
Flamingo_Control/
├── src/py2flamingo/           # Python source code
│   ├── core/                  # Core protocol and communication
│   │   ├── tcp_connection.py  # TCP socket management
│   │   ├── protocol_encoder.py # SCommand encoding/decoding
│   │   └── command_codes.py   # Command code definitions
│   ├── models/                # Data models
│   ├── services/              # Business logic
│   │   ├── configuration_manager.py
│   │   └── connection_service.py
│   ├── controllers/           # MVC controllers
│   │   ├── connection_controller.py
│   │   ├── movement_controller.py
│   │   └── workflow_controller.py
│   ├── views/                 # UI views (PySide6)
│   │   ├── connection_view.py
│   │   ├── movement_view.py
│   │   ├── workflow_view.py
│   │   └── live_viewer_view.py
│   └── application.py         # Main application
├── tests/                     # Unit and integration tests
├── oldcodereference/          # C++ server reference (READ ONLY)
│   └── serversidecode/
│       └── Linux/ControlSystem/
└── claude-reports/            # Analysis and documentation
    └── server-side-protocol-reference.md
```

---

## Development Guidelines

### DO NOT Modify
- **`oldcodereference/`** - Reference server code only, never edit
- The server is running on a separate Linux system

### Key Implementation Files

**TCP Communication:**
- `src/py2flamingo/core/tcp_connection.py` - Socket wrapper
- `src/py2flamingo/core/protocol_encoder.py` - SCommand encode/decode

**Controllers:**
- `src/py2flamingo/controllers/connection_controller.py` - Connection management
- `src/py2flamingo/controllers/movement_controller.py` - Stage control
- `src/py2flamingo/controllers/workflow_controller.py` - Workflow execution

**Views:**
- `src/py2flamingo/views/connection_view.py` - Connection UI
- `src/py2flamingo/views/movement_view.py` - Movement controls + map
- `src/py2flamingo/views/live_viewer_view.py` - Camera live view
- `src/py2flamingo/views/workflow_view.py` - Workflow builder

### Testing Strategy

**Unit Tests:**
- Protocol encoding/decoding
- Command code validation
- Data model logic

**Integration Tests:**
- Mock server for TCP testing
- Full communication flow
- UI event handling

**Test Files:**
- `tests/test_tcp_protocol.py`
- `tests/test_tcp_connection.py`
- `tests/test_services.py`
- `tests/test_controllers.py`
- `tests/test_views.py`

---

## Important Protocol Notes

### Endianness
- All multi-byte values are **little-endian**
- Use `struct.pack('<...', ...)` for encoding
- Use `struct.unpack('<...', ...)` for decoding

### Validation
- **Always** set `cmdStart = 0xF321E654` and `cmdEnd = 0xFEDC4321`
- Server validates these markers and rejects invalid packets

### Callback Flag
- `cmdDataBits0 = 0x80000000` triggers callbacks
- Callbacks are asynchronous notifications from server
- Client must listen for unsolicited messages

### Stage Motion Monitoring
- Position updates sent every 25ms during motion
- Updates have `STAGE_POSITIONS_IN_BUFFER` flag set
- Buffer contains: `"1=X\n2=Y\n3=Z\n"`
- Motion complete indicated by `MOTION_STOPPED` command

### Image Data Timing
- Header send timeout: 500ms
- Image data send timeout: 5000ms
- Queue size: 64 images (older frames dropped if full)

---

## Current Development Status

### Implemented
- TCP connection management
- SCommand protocol encoder/decoder
- Command code definitions
- Basic connection UI
- Configuration management
- Service layer architecture

### In Progress
- Movement controller with map visualization
- Live viewer with image display
- Workflow builder and execution
- All GUI views (movement, live viewer, workflow)

### Planned
- Multi-laser control
- Filter wheel integration
- Beam steering controls (MEMS/Galvo)
- LED illumination controls
- Settings persistence
- Advanced workflow features (tiling, OPT, etc.)

---

## Quick Reference Commands

### Test Connection
```python
# Send system state query
cmd = SCommand(0x0000A007)  # SYSTEM_STATE_GET
response = send_and_receive(cmd)
state = response.int32Data0  # Current system state
```

### Move Stage
```python
# Move to position
cmd = SCommand(0x00006003)
cmd.int32Data0 = axis  # 1=X, 2=Y, 3=Z, 4=R
cmd.doubleData = position  # mm or degrees
send_command(cmd)
```

### Start Live View
```python
# On control port (53717)
cmd = SCommand(0x00003007)
send_command(cmd)

# Then connect to data port (53718) and receive images
```

### Execute Workflow
```python
# Build workflow settings string
settings = build_workflow_settings(...)

# Send with payload
cmd = SCommand(0x00003004)
cmd.additionalDataBytes = len(settings)
send_packet(cmd, settings)
```

---

## Resources

- **Protocol Reference:** `claude-reports/server-side-protocol-reference.md`
- **Server Code (READ ONLY):** `oldcodereference/serversidecode/`
- **Test Suite:** `tests/`
- **Metadata Files:** Server expects specific formats for hardware configuration

---

## Notes for Claude

- The server-side code is C++ reference implementation - analyze but never modify
- Focus development on Python client in `src/py2flamingo/`
- All communication uses the binary SCommand protocol
- Testing should use mocks/stubs for server communication
- GUI development uses PySide6 (Qt6 for Python)
- Follow MVC pattern established in existing code

---

**Last Updated:** 2025-11-10
