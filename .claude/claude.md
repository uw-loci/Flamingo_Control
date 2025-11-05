# Claude Code Project Guidelines

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
All technical reports, implementation details, session summaries, and development documentation should be placed in:

```
Flamingo_Control/claude-reports/
```

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
Flamingo_Control/
├── README.md                    # User-facing project overview
├── INSTALLATION.md              # User-facing setup guide
├── .claude/
│   └── claude.md               # This file
└── claude-reports/
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
- Commits that add technical reports should include the report in `claude-reports/`
- Commits should reference the report file in the commit message
- Keep user-facing docs (README, INSTALLATION) up to date with actual functionality

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
- `Value`: Position in millimeters or degrees

**Camera Query Commands:**
- `CAMERA_PIXEL_FIELD_OF_VIEW_GET`: Returns pixel size in `Value` field (mm/pixel)
- `CAMERA_IMAGE_SIZE_GET`: Returns dimensions in parameter fields

**System State:**
- `SYSTEM_STATE_GET`: Returns state in `Status` field (1=IDLE, 0=BUSY)
- `cmdBits3` (Param[3]): May contain state code (40962=IDLE)

**File Transfer Commands:**
- `addDataBytes`: Contains size of file being transferred
- Command structure sent first, then file data

#### Packet Validation

Both start and end markers must be correct:
- Start: `0xF321E654`
- End: `0xFEDC4321`

If markers don't match, packet is invalid/corrupted.

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

---

**Last Updated:** 2024-11-05
**Maintained By:** Claude Code assistant
