# Flamingo Workflow System - Technical Reference

Comprehensive documentation of the Flamingo microscope workflow system, including file formats,
parameter calculations, UI inputs, and execution flow.

**Last Updated:** 2026-01-15

---

## Table of Contents

1. [Overview](#overview)
2. [Workflow File Format](#workflow-file-format)
3. [Parameter Calculations](#parameter-calculations)
4. [User-Entered vs Calculated Values](#user-entered-vs-calculated-values)
5. [UI Panel Reference](#ui-panel-reference)
6. [Workflow Execution Flow](#workflow-execution-flow)
7. [Stage-Camera Synchronization](#stage-camera-synchronization)
8. [Z-Stack Acquisition Modes](#z-stack-acquisition-modes)
9. [System Limits and Constraints](#system-limits-and-constraints)
   - [TIFF File Size Limit (4GB)](#tiff-file-size-limit-critical)
10. [Workflow Completion Detection](#workflow-completion-detection)
11. [Two-Point Position UI](#two-point-position-ui)

---

## Overview

The workflow system manages microscope acquisition configurations through:
- **Text-based workflow files** with nested XML-like structure
- **Parameter calculation logic** for auto-computing Z velocity, number of planes, etc.
- **Stage-camera synchronization** using trigger-based continuous motion
- **Multiple acquisition modes**: ZStack, ZSweep, Tile, OPT, and combinations

---

## Workflow File Format

### File Structure

Workflow files use a nested tag structure with key-value pairs:

```
<Workflow Settings>
    <Experiment Settings>
        Plane spacing (um) = 2.5
        Frame rate (f/s) = 100.0
        ...
    </Experiment Settings>

    <Camera Settings>
        Exposure time (us) = 10000
        ...
    </Camera Settings>

    <Stack Settings>
        Number of planes = 100
        Change in Z axis (mm) = 0.2475
        Z stage velocity (mm/s) = 0.25
        ...
    </Stack Settings>

    <Start Position>
        X (mm) = 0.0
        Y (mm) = 0.0
        Z (mm) = 5.0
        Angle (degrees) = 0.0
    </Start Position>

    <End Position>
        X (mm) = 0.0
        Y (mm) = 0.0
        Z (mm) = 5.2475
        Angle (degrees) = 0.0
    </End Position>

    <Illumination Source>
        Laser 1 405 nm = 5.00 0
        Laser 2 488 nm = 5.00 1
        ...
    </Illumination Source>

    <Illumination Path>
        Left path = ON
        Right path = OFF
    </Illumination Path>

    <Illumination Options>
        Run stack with multiple lasers on = false
    </Illumination Options>
</Workflow Settings>
```

### Complete Parameter Reference

#### Experiment Settings (19 parameters)

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `Plane spacing (um)` | float | Z-spacing between planes in micrometers | 2.5 |
| `Frame rate (f/s)` | float | Camera frame rate in fps | 100.0 |
| `Exposure time (us)` | float | Camera exposure in microseconds | 10000 |
| `Duration (dd:hh:mm:ss)` | string | Total experiment duration | 00:00:00:01 |
| `Interval (dd:hh:mm:ss)` | string | Interval between timepoints | 00:00:00:01 |
| `Sample` | string | Sample name/identifier | "" |
| `Number of angles` | int | Multi-angle acquisition count | 1 |
| `Angle step size` | float | Rotation angle increment (degrees) | 0 |
| `Region` | string | Region of interest identifier | "" |
| `Save image drive` | string | Full path to save location | /media/deploy/ctlsm1 |
| `Save image directory` | string | Subdirectory name | "" |
| `Comments` | string | User notes | "" |
| `Save max projection` | bool | Save MIP | false |
| `Display max projection` | bool | Display MIP during acquisition | true |
| `Save image data` | string | Format: NotSaved/Tiff/BigTiff/Raw | Tiff |
| `Save to subfolders` | bool | Organize by channel/plane | false |
| `Work flow live view enabled` | bool | Display live preview | true |

#### Camera Settings (4 parameters)

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `Exposure time (us)` | float | Camera exposure time | 10000 |
| `Frame rate (f/s)` | float | Camera frame rate | 100.0 |
| `AOI width` | int | Area of Interest width (pixels) | 2048 |
| `AOI height` | int | Area of Interest height (pixels) | 2048 |

#### Stack Settings (17 parameters)

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `Stack index` | int | Index/identifier for this stack | 0 |
| `Change in Z axis (mm)` | float | Total Z range in millimeters | 0.01 |
| `Number of planes` | int | Total number of Z-planes | 1 |
| `Number of planes saved` | int | Subset to save (optional) | - |
| `Z stage velocity (mm/s)` | float | Speed of Z-stage movement | 0.4 |
| `Rotational stage velocity (°/s)` | float | Rotation speed | 0.0 |
| `Auto update stack calculations` | bool | Recalculate based on parameters | true |
| `Date time stamp` | string | Acquisition timestamp | - |
| `Stack file name` | string | Output filename | - |
| `Camera 1 capture percentage` | int | Percentage of planes for camera 1 | 100 |
| `Camera 1 capture mode` | int | 0=full, 1=front, 2=back, 3=none | 0 |
| `Camera 1 capture range` | string | Specific planes for camera 1 | - |
| `Camera 2 capture percentage` | int | Percentage of planes for camera 2 | 100 |
| `Camera 2 capture mode` | int | 0=full, 1=front, 2=back, 3=none | 0 |
| `Camera 2 capture range` | string | Specific planes for camera 2 | - |
| `Stack option` | string | None/ZStack/ZStack Movie/Tile/ZSweep/OPT/OPT ZStacks/Bidirectional | None |
| `Stack option settings 1` | int | Tile rows or custom parameter 1 | 0 |
| `Stack option settings 2` | int | Tile columns or custom parameter 2 | 0 |

#### Illumination Source Format

Format: `SourceName = PowerValue OnOffFlag`
- PowerValue: Numeric (e.g., 5.00, 10.67, 0.00)
- OnOffFlag: 1 (enabled) or 0 (disabled)

Example:
```
Laser 1 405 nm = 5.00 0
Laser 2 488 nm = 10.00 1
Laser 3 561 nm = 5.00 1
Laser 4 640 nm = 0.00 0
LED_RGB_Board = 50.00 0
```

#### Illumination Options

The `<Illumination Options>` section controls how multi-channel acquisitions are executed:

| Parameter | Values | Description |
|-----------|--------|-------------|
| `Run stack with multiple lasers on` | `true` / `false` | Controls simultaneous vs sequential laser firing |

**Behavior:**
- `false` (default) — **Sequential acquisition**: when multiple lasers are enabled, the system runs a separate Z-stack for each laser channel. The filter wheel switches between channels. This is the standard acquisition mode.
- `true` — **Simultaneous acquisition**: all enabled lasers fire at the same time during a single Z-stack pass. No filter wheel changes occur.

**Setting this option:**
- Controlled via the **Advanced Illumination** dialog checkbox ("Run stack with multiple lasers on")
- Accessible from the Illumination panel's "Advanced..." button
- Also set automatically by Tile Collection Dialog based on the user's Advanced Illumination preference

---

## Parameter Calculations

### Z Velocity Calculation

**Primary Formula (Auto Mode):**
```
Z_Velocity (mm/s) = Plane_Spacing (mm) × Frame_Rate (fps)
```

Where:
- `Plane_Spacing` = spacing between Z planes (converted from µm to mm)
- `Frame_Rate` = camera frame rate in frames per second
- Result: Z velocity in mm/s

**Implementation (from C++ CheckStackBase.cpp):**
```cpp
const double ZVelocity = (planeSpacing / 1000.0) * frameRate;
```

**Example:**
- Plane spacing: 2.5 µm = 0.0025 mm
- Frame rate: 100 fps
- Z velocity = 0.0025 × 100 = 0.25 mm/s

### Number of Planes Calculation

**From Delta Z and Plane Spacing:**
```
Number_of_Planes = round(Delta_Z / Plane_Spacing_mm) + 1
```

**Implementation:**
```cpp
const int planes = (int)(m_deltaZ / (planeSpacing / 1000.0) + 0.5);
```

### Preset Z Velocity Mode

When user provides a preset Z velocity (non-zero, valid value):
```
Stack_Acquisition_Time = Delta_Z / Z_Velocity
Number_of_Planes = round(Stack_Acquisition_Time × Frame_Rate)
```

### Stack Acquisition Time

```
Total_Time = Z_Travel_Time + Per_Plane_Overhead + Disk_Save_Overhead + Stage_Motion_Time

Where:
- Z_Travel_Time = Delta_Z / Z_Velocity
- Per_Plane_Overhead = num_planes × 0.01165 ms
- Disk_Save_Overhead = 66ms (RAW) or 120ms (TIFF)
- Stage_Motion_Time = varies by axis distance
```

---

## User-Entered vs Calculated Values

### User-Entered Values (Input)

| Category | Parameters |
|----------|------------|
| **Experiment** | Plane spacing (µm), Frame rate (fps), Duration, Interval |
| **Stack** | Delta Z (mm), Exposure time |
| **Optional** | Z velocity (preset mode), Number of planes saved |
| **Save** | Drive, Directory, Sample name, Format |
| **Position** | X, Y, Z start/end positions, Angle |
| **Illumination** | Laser powers, LED settings, Path selection |

### Automatically Calculated Values (Output)

| Parameter | Formula | Calculated When |
|-----------|---------|-----------------|
| **Z Velocity** | plane_spacing_mm × frame_rate | Auto mode (default) |
| **Number of Planes** | delta_Z / plane_spacing + 1 | Always |
| **Z Range** | (num_planes - 1) × plane_spacing | Always |
| **Frame Rate** (display) | 1,000,000 / exposure_us | Always |
| **Stack Time** | delta_Z / velocity + overhead | On "Check Stack" |
| **End Z Position** | start_Z + delta_Z | On validation |

### "Check Stack" Button Behavior

The "Check Stack" button triggers validation and calculation:

1. **Validates workflow settings** are not empty
2. **Routes to specific handler** based on Stack Option:
   - ZStack → `CheckStackZStack`
   - ZStack Movie → `CheckStackZStackMovie`
   - ZStack API → `CheckStackZStackAPI`
   - Tile → `CheckStackTile`
   - ZSweep → `CheckStackZSweep`
3. **Calculates Z velocity** if in auto mode
4. **Calculates number of planes** from delta Z
5. **Validates Z velocity** against system limits (0.001-1.0 mm/s)
6. **Returns error count** and messages

---

## UI Panel Reference

### Z-Stack Panel (`zstack_panel.py`)

| Field | Type | Range | Calculated? |
|-------|------|-------|-------------|
| Number of Planes | SpinBox | 1-10,000 | No |
| Z Step | SpinBox | 0.1-100.0 µm | No |
| Z Range | Label | - | **Yes** |
| Z Velocity | SpinBox | 0.01-2.0 mm/s | **Should be auto** |
| Stack Option | ComboBox | None/ZStack/Tile/etc. | No |
| Tiles X/Y | SpinBox | 1-100 | No (conditional) |
| Rotational Velocity | SpinBox | 0.0-10.0 °/s | No |
| Est. Time | Label | - | **Yes** |
| Return to Start | CheckBox | - | No |

### Camera Panel (`camera_panel.py`)

| Field | Type | Range | Calculated? |
|-------|------|-------|-------------|
| Exposure Time | SpinBox | 0.1-100,000 µs | No |
| Frame Rate | Label | - | **Yes** |
| AOI Width | SpinBox | 1-2048 px | No |
| AOI Height | SpinBox | 1-2048 px | No |
| Camera 1 Percentage | SpinBox | 0-100% | No |
| Camera 1 Mode | ComboBox | Full/Front/Back/None | No |

### Dual Position Panel (`dual_position_panel.py`)

Provides two-point position input with mode-dependent field visibility.

| Field | Position A | Position B |
|-------|------------|------------|
| X Position | SpinBox -50.0 to 50.0 mm | Mode-dependent |
| Y Position | SpinBox -50.0 to 50.0 mm | Mode-dependent |
| Z Position | SpinBox 0.0 to 30.0 mm | Always editable |
| R Position | SpinBox 0.0 to 360.0° | Always greyed |
| Use Current | Button | Button |
| Load Saved | ComboBox (presets) | ComboBox (presets) |

**Modes:**
- **snapshot**: Position B hidden
- **zstack**: Position B shows Z only
- **tiling**: Position B shows X, Y, Z

### Illumination Panel (`illumination_panel.py`)

| Field | Type | Range |
|-------|------|-------|
| Laser Enable | CheckBox | per laser |
| Laser Power | SpinBox | 0.0-100.0% |
| LED Enable | CheckBox | - |
| LED Color | ComboBox | Red/Green/Blue/White |
| LED Intensity | SpinBox | 0.0-100.0% |

### Save Panel (`save_panel.py`)

| Field | Type |
|-------|------|
| Save Images | CheckBox |
| Save Drive | ComboBox (editable, with Refresh button) |
| Local Path... | Button (configure local mount for drive) |
| Directory | LineEdit |
| Sample Name | LineEdit |
| Format | ComboBox (TIFF/BigTIFF/Raw/NotSaved) |
| Save MIP | CheckBox |
| Display MIP | CheckBox |
| Save Subfolders | CheckBox |
| Live View | CheckBox |
| Comments | TextEdit |

**Local Path Mapping:**
The "Local Path..." button configures a mapping between server storage paths
(e.g., `/media/deploy/ctlsm1`) and local mount points (e.g., `G:\CTLSM1`).
This enables post-collection folder reorganization from flattened structure
(required by server) to nested structure (required by MIP Overview).

Mappings are stored in configuration service under `drive_path_mappings` key.

**Save Directory Sanitization:**
The workflow view validates that save directories don't contain path separators
(`/` or `\`). If found, they are replaced with underscores and the user is
prompted to review. This prevents server directory creation failures since
the server can only create single-level directories.

---

## Workflow Execution Flow

### Startup Sequence

1. **WorkflowControl::startWorkflow()** - Entry point
   - Validates workflow settings not empty
   - Runs stack validation via `checkStacks()`
   - Sets `m_workflowRun = true`
   - Launches workflow thread

2. **WorkflowControl::runWorkflowThread()** - Thread entry
   - Broadcasts "WORK_FLOW_RUNNING" status
   - Calls `runWorkflow()`
   - Returns system to idle when complete

3. **WorkflowControl::runWorkflow()** - Main loop
   - Iterates through each workflow stack
   - Determines acquisition type (ZStack, ZSweep, Tile, OPT, etc.)
   - Calls corresponding workflow object's `runWorkflow()` method

### Per-Stack Execution (WorkflowBase::runWorkflow)

1. **Illumination Setup**
   - Turn off all illumination
   - Set laser/LED for current acquisition

2. **Camera Configuration**
   - Set exposure time
   - Verify workflow settings

3. **For Each Stack**:
   - Create data file path
   - Execute `stackTakeControl()`

### Stack Control Sequence (WorkflowBase::stackTakeControl)

1. **Move to Start Position**
   - `setPositionStart()` - moves XYZ axes
   - Sets velocity for each axis
   - Waits for motion to stop

2. **Filter Wheel Setup**
   - Position filter wheel
   - Wait for target position

3. **Configure Move-to-End-on-Trigger**
   - `setPositionEnd()` - configures end position
   - Sets Z velocity from settings
   - Issues `STAGE_MOVE_TO_END_ON_TRIGGER` command

4. **Illumination Path Setup**
   - Enable left/right path with waveform
   - Configure laser/LED power

5. **Image Acquisition**
   - `stackTake()` - initiates camera capture thread
   - `stackWaitForComplete()` - waits for all frames

6. **Cleanup**
   - Disable illumination paths
   - Turn off lasers/LEDs
   - Cancel stage motion
   - Reset velocities

---

## Stage-Camera Synchronization

### Trigger-Based Continuous Motion

The system uses **trigger-based continuous motion** for Z-sweep acquisitions:

#### PI Stage Macro for Z-Sweep:
```
MAC BEG ZSweep
  SVO axis 1                    # Servo on
  WAC DIO? 1 = 1                # Wait for external trigger
  MOV axis pointB               # Move to end position
  WAC ONT? axis = 1             # Wait for on-target
  MOV axis pointA               # Move back to start
  WAC ONT? axis = 1             # Wait for on-target
  JRC -4 ONT? axis = 1          # Loop back if still on-target
MAC END
MAC START ZSweep
```

#### Key Synchronization Features:

- `WAC DIO? 1 = 1` - Waits for external TTL trigger signal
- `JRC -4` - Loops back to wait for next trigger
- Camera sends TTL triggers during acquisition
- Stage responds to each trigger with continuous motion
- Creates **true continuous Z-movement** with triggered frame captures

### Motion Monitoring

A separate thread (`threadMotionMonitor`) continuously polls stage position during Z-sweep:
- Updates position data in real-time
- Records Z position with each captured frame
- Enables post-processing reconstruction

---

## Z-Stack Acquisition Modes

### Continuous Z-Sweep (ZSweep Mode)

- Stage moves **continuously** from start to end position
- Camera triggers at regular intervals via TTL
- Each trigger captures one frame
- Stage position recorded with each frame
- **True continuous acquisition** - no stepping motion

**Used for:** High-speed volumetric imaging

### Step-by-Step Z-Stack (Standard Mode)

For each plane:
1. Wait for stage trigger signal
2. Move to next Z position incrementally
3. Wait for stage to reach position
4. Trigger camera to capture frame
5. Read stage position
6. Move to next position

**Used for:** High-precision imaging, focus stacking

### Acquisition Flow (AcquisitionBase::AcquireStack)

```
Setup:
  → setAcquisitionSettings()
  → setAcquisitionControls()
  → acquisitionInit()
  → Start worker threads:
      - threadImaging (main capture)
      - threadMaxProjection
      - threadStackStreaming
      - threadStackStorage
      - threadStackStorageImageQueue

For each frame (0 to num_planes):
  → Log frame index
  → Get stage positions
  → Wait for next buffer
  → Get image from camera buffer
  → Copy to storage buffer
  → Requeue buffer
  → Queue for storage

Cleanup:
  → Stop all threads
  → Terminate acquisition
  → Cancel camera images
  → Stop stage macro
```

---

## System Limits and Constraints

### Z Velocity Limits (SystemLimits.h)

| Limit | Value |
|-------|-------|
| Z_VELOCITY_MIN | 0.001 mm/s |
| Z_VELOCITY_MAX | 1.0 mm/s |

### Auto-Calculation Triggers

Z velocity is auto-calculated when:
- `AUTO_UPDATE` flag is set
- Velocity is 0
- Velocity exceeds max (1.0 mm/s)
- Velocity is below min (0.001 mm/s)

### Validation Checks

The `checkZVelocity()` function clamps velocity to system limits and logs warnings if adjustments are needed.

### TIFF File Size Limit (CRITICAL)

**Standard TIFF files are limited to 4GB (4,294,967,296 bytes)** due to 32-bit file offsets.
Workflows that exceed this limit will FAIL during acquisition.

| Image Size | Bytes/Image | Max Safe Planes | Max Z-Range @ 2.5µm |
|------------|-------------|-----------------|---------------------|
| 2048×2048 | 8 MB | ~480 | 1.2 mm |
| 1024×1024 | 2 MB | ~1,920 | 4.8 mm |
| 4096×4096 | 32 MB | ~120 | 0.3 mm |

**Calculation:**
```python
image_bytes = width × height × 2  # 16-bit = 2 bytes/pixel
max_planes = 4,294,967,296 ÷ image_bytes × 0.95  # 5% safety margin
```

**Symptoms of exceeding limit:**
- Server log: `Bytes written not equal to buffer size (-1, 8388608)`
- Server log: `system fault detected, disk full, stopping experiment`
- Acquisition stops after ~500 planes for 2048×2048 images
- "Disk full" error despite terabytes of free space

**Pre-flight Validation:**
Python validates TIFF size before workflow execution:
```python
from py2flamingo.services.tiff_size_validator import validate_workflow_params

estimate = validate_workflow_params(
    z_range_mm=4.0,
    z_step_um=2.5,
    image_width=2048,
    image_height=2048
)

if estimate.exceeds_limit:
    print(f"WARNING: {estimate.num_planes} planes = {estimate.estimated_gb:.1f} GB")
    print(f"Max safe planes: {estimate.max_safe_planes}")
```

**Solutions:**
1. Reduce Z-range to stay under ~1.2mm for 2048×2048
2. Increase Z step size (fewer planes)
3. Use camera binning/smaller AOI
4. Split acquisition into multiple smaller Z-stacks

---

## Workflow Completion Detection

### Callback-Based Detection (Recommended)

The C++ GUI uses callback-based completion detection. When a workflow finishes, the server
sends a `CAMERA_STACK_COMPLETE` (0x3011) callback containing completion data.

**Callback Message Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `int32Data0` | int | Images acquired |
| `int32Data1` | int | Images expected |
| `int32Data2` | int | Error count |
| `doubleData` | double | Acquisition time (microseconds) |

**Progress Callbacks:**
| Code | Name | Description |
|------|------|-------------|
| `0x3011` | CAMERA_STACK_COMPLETE | Workflow finished |
| `0x9004` | UI_SET_GAUGE_VALUE | Progress bar update (images acquired/expected) |
| `0x9008` | UI_IMAGES_SAVED_TO_STORAGE | Images written to disk notification |

**Implementation:**
```python
# Register for completion callback
connection_service.register_callback(0x3011, on_stack_complete)

def on_stack_complete(message):
    acquired = message.int32_data0
    expected = message.int32_data1
    errors = message.int32_data2
    time_us = message.double_data
    # Workflow completed!
```

### Polling-Based Detection (Fallback)

If callbacks are not available, poll `SYSTEM_STATE_GET` (0xa007):
- Returns state 0 = IDLE (workflow complete)
- Returns state 1 = BUSY (workflow running)
- Poll interval: 10 seconds recommended

**Note:** Excessive polling can cause server issues. Prefer callback-based detection.

---

## Two-Point Position UI

### Overview

The DualPositionPanel provides an intuitive "pick two points" interface for defining workflow
regions. Instead of entering abstract parameters (number of planes, step size), users define
**Point A** (start) and **Point B** (end) - the system calculates everything else.

### DualPositionPanel (`dual_position_panel.py`)

| Field | Position A | Position B (varies by mode) |
|-------|------------|----------------------------|
| X (mm) | Always editable | Editable (Tiling), Greyed (Z-Stack) |
| Y (mm) | Always editable | Editable (Tiling), Greyed (Z-Stack) |
| Z (mm) | Always editable | Always editable |
| R (deg) | Always editable | Greyed (all modes) |

**Modes:**
- **Snapshot**: Position B hidden
- **Z-Stack**: Position B shows Z only (X, Y, R greyed out)
- **Tiling**: Position B shows X, Y, Z (R greyed out)

### Position Sources

Each position can be set via:
1. **Manual entry** - Type values directly
2. **Use Current** button - Capture current hardware position
3. **Load Saved** dropdown - Load from saved position presets

### Auto-Calculation

When positions change, panels auto-calculate:

| Panel | Calculated From | Formula |
|-------|-----------------|---------|
| ZStackPanel | Z range (Z_A to Z_B) | `num_planes = ceil(z_range / z_step) + 1` |
| TilingPanel | XY range (A to B corners) | `tiles = ceil(range / (fov * (1 - overlap)))` |

### Integration

```python
# In WorkflowView
self._position_panel = DualPositionPanel()
self._position_panel.position_a_changed.connect(self._on_position_changed)
self._position_panel.position_b_changed.connect(self._on_position_changed)

def _on_position_changed(self, position):
    if self._current_type == WorkflowType.ZSTACK:
        z_min, z_max = self._position_panel.get_z_range()
        self._zstack_panel.set_z_range_from_positions(z_min, z_max)
    elif self._current_type == WorkflowType.TILE:
        x_min, x_max, y_min, y_max = self._position_panel.get_xy_range()
        self._tiling_panel.set_from_positions(x_min, x_max, y_min, y_max)
```

---

## Key Source Files

### C++ Reference (oldcodereference)

| File | Purpose |
|------|---------|
| `Workflow/CheckStackBase.cpp` | Z velocity calculation, validation |
| `Workflow/StackControl.cpp` | Routes check stack to handlers |
| `Workflow/WorkflowControl.cpp` | Main workflow orchestration |
| `Workflow/WorkflowBase.cpp` | Base workflow operations |
| `Workflow/WorkflowZSweep.cpp` | Z-sweep specific settings |
| `Stages/PIStageBase.cpp` | PI stage macros, motion control |
| `Camera/AcquisitionBase.cpp` | Main image acquisition loop |
| `SystemIDs/SystemLimits.h` | Z velocity min/max limits |
| `Workflow/WorkflowSettings.h` | All workflow parameter enums |

### Python Implementation

| File | Purpose |
|------|---------|
| `views/workflow_panels/dual_position_panel.py` | Two-point position UI with mode switching |
| `views/workflow_panels/zstack_panel.py` | Z-Stack UI configuration |
| `views/workflow_panels/tiling_panel.py` | Tiling/mosaic UI configuration |
| `views/workflow_panels/camera_panel.py` | Camera settings UI |
| `views/workflow_panels/illumination_panel.py` | Laser/LED UI |
| `views/workflow_panels/save_panel.py` | Save settings UI |
| `views/workflow_view.py` | Main workflow builder UI |
| `services/workflow_queue_service.py` | Sequential workflow execution with callbacks |
| `services/connection_service.py` | TCP connection and callback registration |
| `services/position_preset_service.py` | Saved position presets |
| `core/command_codes.py` | Command codes including STACK_COMPLETE |
| `utils/workflow_parser.py` | Parse workflow files |
| `utils/file_handlers.py` | Read/write workflow files |
| `workflows/workflow_repository.py` | Load/save all formats |
| `controllers/workflow_controller.py` | UI-to-file conversion |

---

## Implementation Notes for Collect Tiles Workflow

### Required Changes

1. **Z Velocity Auto-Calculation**
   - Implement formula: `Z_velocity = plane_spacing_mm × frame_rate`
   - Make Z velocity field read-only when in auto mode
   - Add "Auto" checkbox to enable/disable auto-calculation

2. **Input Fields Required**
   - Plane spacing (µm) - user entered
   - Frame rate (fps) OR exposure time (µs) - user entered
   - Delta Z (mm) - user entered OR calculated from tile overlap
   - Number of planes - calculated, display only

3. **90-Degree Angle Overlap Calculation**
   - When dual-view tiles available, calculate overlapping volume
   - Use intersection of two brightfield Z-stacks
   - Generate workflow Z-stacks to cover the overlap volume

4. **Per-Tile Workflow Generation**
   - Each selected tile generates one workflow text
   - Workflow contains tile-specific start/end positions
   - All tiles share same acquisition parameters

---

## Implementation Plan

### Changes to ZStackPanel

The `ZStackPanel` needs to be modified to auto-calculate Z velocity based on the C++ formula:

**Required Changes:**

1. **Add Auto-Calculation Mode**
   - Add "Auto Calculate Z Velocity" checkbox (default: checked)
   - When auto mode enabled, Z velocity field becomes read-only
   - Formula: `Z_velocity = (plane_spacing_um / 1000) × frame_rate`

2. **Add Frame Rate Input or Connection**
   - Option A: Add frame rate field directly to ZStackPanel
   - Option B: Accept frame rate via signal from CameraPanel
   - Need exposure time to calculate: `frame_rate = 1,000,000 / exposure_us`

3. **Update Calculation Logic**
   ```python
   def _calculate_z_velocity(self) -> float:
       """Calculate Z velocity from plane spacing and frame rate."""
       plane_spacing_mm = self._z_step.value() / 1000.0  # µm to mm
       frame_rate = self._frame_rate  # fps from camera or input
       z_velocity = plane_spacing_mm * frame_rate

       # Clamp to system limits
       z_velocity = max(0.001, min(1.0, z_velocity))
       return z_velocity
   ```

4. **Add Validation (Check Stack)**
   - Validate Z velocity within limits (0.001 - 1.0 mm/s)
   - Show warning if velocity needs clamping
   - Calculate and display estimated acquisition time

### Changes to TileCollectionDialog

**Required Changes:**

1. **Add CameraPanel or Exposure Settings**
   - Add CameraPanel to get exposure time and frame rate
   - Or add simple exposure time spinbox with frame rate display

2. **Connect Frame Rate to ZStackPanel**
   - Pass frame rate to ZStackPanel for auto-calculation
   - Update Z velocity when exposure changes

3. **Update Workflow Text Generation**
   - Use calculated Z velocity instead of hardcoded value
   - Include plane spacing and frame rate from UI

4. **90-Degree Angle Overlap Calculation (Future)**
   - When both left and right panels have selected tiles
   - Calculate overlap volume between two brightfield Z-stacks
   - Generate workflow Z-stacks to cover the intersection volume
   - This requires:
     - Computing 3D bounding boxes for each angle's tiles
     - Finding intersection volume in world coordinates
     - Determining Z range per tile based on overlap geometry

### Changes to WorkflowView

**Required Changes:**

1. **Pass Frame Rate to ZStackPanel**
   - Connect CameraPanel.settings_changed signal to ZStackPanel
   - Update ZStackPanel frame rate when camera settings change

2. **Enable Auto-Calculation by Default**
   - ZStackPanel should default to auto-calculate mode
   - User can disable to enter manual Z velocity

3. **Add Check Stack Button (Optional)**
   - Add "Validate Settings" or "Check Stack" button
   - Runs validation and shows calculated parameters
   - Displays warnings for any parameter adjustments

### Shared Changes

1. **Add Frame Rate Property to ZStackPanel**
   ```python
   def set_frame_rate(self, frame_rate: float) -> None:
       """Set frame rate for Z velocity calculation."""
       self._frame_rate = frame_rate
       if self._auto_calculate.isChecked():
           self._update_z_velocity()
   ```

2. **Add Auto-Calculate Property to StackSettings**
   ```python
   @dataclass
   class StackSettings:
       num_planes: int
       z_step_um: float
       z_velocity_mm_s: float
       auto_calculate_velocity: bool = True  # New field
       ...
   ```

3. **System Limits Constants**
   ```python
   # In workflow_constants.py
   Z_VELOCITY_MIN_MM_S = 0.001
   Z_VELOCITY_MAX_MM_S = 1.0
   ```

---

**Document Version:** 1.0
**Based on C++ Flamingo Control System analysis**
