# Subsystem Services Architecture

**Date:** 2025-11-06
**Purpose:** Clean API layer for microscope commands organized by hardware subsystem

---

## Overview

Created a new service layer that provides clean, typed APIs for microscope operations. Instead of dealing with raw protocol dictionaries, code can now call simple methods that return exactly what's needed.

## Architecture

```
Controllers (UI Logic)
    ↓
Subsystem Services (Domain Methods)
    ↓
MicroscopeCommandService (Base Class - Protocol Handling)
    ↓
ConnectionService (Socket Management)
```

## Class Hierarchy

### Base: `MicroscopeCommandService`
**Location:** `src/py2flamingo/services/microscope_command_service.py`

Provides common functionality:
- `_query_command()` - Send query, receive parsed response
- `_send_command()` - Send action command
- `_receive_full_bytes()` - Socket read with timeout
- `_parse_response()` - Parse 128-byte protocol

**Key Features:**
- Automatically adds TRIGGER_CALL_BACK flag (0x80000000)
- Reads additional data bytes (critical for buffer management)
- Validates start/end markers
- Error handling and logging

### CameraService
**Location:** `src/py2flamingo/services/camera_service.py`

**Clean API Methods:**
```python
camera_service.get_image_size() → (2048, 2048)
camera_service.get_pixel_field_of_view() → 0.000253  # mm/pixel
camera_service.take_snapshot() → None
camera_service.start_live_view() → None
camera_service.stop_live_view() → None
```

**Implementation Example:**
```python
def get_image_size(self) -> Tuple[int, int]:
    """Get camera image dimensions in pixels."""
    result = self._query_command(
        CameraCommandCode.IMAGE_SIZE_GET,
        "CAMERA_IMAGE_SIZE_GET"
    )

    if not result['success']:
        raise RuntimeError(f"Failed to get image size: {result.get('error')}")

    params = result['parsed']['params']
    width = params[3]   # X dimension in Param[3]
    height = params[4]  # Y dimension in Param[4]

    return (width, height)
```

### StageService
**Location:** `src/py2flamingo/services/stage_service.py`

**Clean API Methods:**
```python
stage_service.get_position() → Position | None
stage_service.move_to_position(axis, position_mm) → None
stage_service.is_motion_stopped() → bool | None
```

**Implementation Example:**
```python
def move_to_position(self, axis: int, position_mm: float) -> None:
    """Move stage to absolute position on specified axis."""
    result = self._send_movement_command(
        StageCommandCode.POSITION_SET_SLIDER,
        "STAGE_POSITION_SET",
        axis=axis,
        position_mm=position_mm
    )

    if not result['success']:
        raise RuntimeError(f"Failed to move stage: {result.get('error')}")
```

---

## Usage Examples

### Example 1: Get Camera Image Size

**Before (Raw Protocol):**
```python
result = position_controller.debug_query_command(12327, "CAMERA_IMAGE_SIZE_GET")
if result['success']:
    params = result['parsed']['params']
    width = params[3]
    height = params[4]
    print(f"Image size: {width}x{height}")
else:
    print(f"Error: {result.get('error')}")
```

**After (Clean API):**
```python
from py2flamingo.services.camera_service import CameraService

camera = CameraService(connection)
width, height = camera.get_image_size()
print(f"Image size: {width}x{height}")
```

### Example 2: Get Pixel Field of View

**Before (Raw Protocol):**
```python
result = position_controller.debug_query_command(12343, "CAMERA_PIXEL_FIELD_OF_VIEW_GET")
if result['success']:
    pixel_size_mm = result['parsed']['value']
    pixel_size_um = pixel_size_mm * 1000
    print(f"Pixel size: {pixel_size_um:.3f} µm")
```

**After (Clean API):**
```python
pixel_size_mm = camera.get_pixel_field_of_view()
pixel_size_um = pixel_size_mm * 1000
print(f"Pixel size: {pixel_size_um:.3f} µm")
```

### Example 3: Complete Workflow

**Setting up live view with correct parameters:**
```python
from py2flamingo.services.camera_service import CameraService

# Initialize service
camera = CameraService(connection)

# Get camera parameters
width, height = camera.get_image_size()
pixel_size_mm = camera.get_pixel_field_of_view()

# Calculate field of view
fov_x_mm = width * pixel_size_mm
fov_y_mm = height * pixel_size_mm

print(f"Camera: {width}x{height} pixels")
print(f"Pixel size: {pixel_size_mm * 1000:.3f} µm/pixel")
print(f"Field of view: {fov_x_mm:.2f} x {fov_y_mm:.2f} mm")

# Allocate buffer for images
image_buffer = numpy.zeros((height, width), dtype=numpy.uint16)

# Start live view
camera.start_live_view()

# ... receive and process images ...

# Stop live view
camera.stop_live_view()
```

### Example 4: Stage Movement

**Moving stage with proper parameters:**
```python
from py2flamingo.services.stage_service import StageService, AxisCode

# Initialize service
stage = StageService(connection)

# Move Y axis to 10.5 mm
stage.move_to_position(AxisCode.Y_AXIS, 10.5)
print("Stage moving to Y=10.5mm...")

# Motion is asynchronous - monitor for completion
# (Will need callback handler or polling - see microscope logs)
```

---

## Benefits

### 1. Type Safety
**Before:**
```python
result = debug_query(12327, "CAMERA_IMAGE_SIZE_GET")
# Returns: Dict[str, Any] - no type checking
```

**After:**
```python
width, height = camera.get_image_size()
# Returns: Tuple[int, int] - type checked!
```

### 2. Clearer Intent
**Before:**
```python
# What does 12327 mean? What do params[3] and params[4] contain?
result = debug_query(12327, "CAMERA_IMAGE_SIZE_GET")
width = result['parsed']['params'][3]
height = result['parsed']['params'][4]
```

**After:**
```python
# Clear method name and return type
width, height = camera.get_image_size()
```

### 3. Error Handling
**Before:**
```python
result = debug_query(12327, "CAMERA_IMAGE_SIZE_GET")
if not result.get('success'):
    # What went wrong? timeout? connection? parsing error?
    print("Failed")
```

**After:**
```python
try:
    width, height = camera.get_image_size()
except RuntimeError as e:
    # Clear error message
    print(f"Failed to get image size: {e}")
```

### 4. Documentation
Each method has clear docstrings:
```python
def get_image_size(self) -> Tuple[int, int]:
    """
    Get camera image dimensions in pixels.

    Returns:
        Tuple of (width, height) in pixels, e.g., (2048, 2048)

    Raises:
        RuntimeError: If command fails or microscope not connected

    Example:
        >>> width, height = camera_service.get_image_size()
        >>> print(f"Setting up buffer for {width}x{height} image")
    """
```

### 5. Testability
Easy to mock services for testing:
```python
# In tests
class MockCameraService:
    def get_image_size(self):
        return (2048, 2048)

# Use mock service in unit tests
```

---

## Command Code Organization

Commands organized by subsystem (matching CommandCodes.h):

### Camera Commands (0x3000 range)
- 12327 (0x3027) - CAMERA_IMAGE_SIZE_GET
- 12343 (0x3037) - CAMERA_PIXEL_FIELD_OF_VIEW_GET
- 12294 (0x3006) - CAMERA_SNAPSHOT
- 12295 (0x3007) - CAMERA_LIVE_VIEW_START
- 12296 (0x3008) - CAMERA_LIVE_VIEW_STOP

### Stage Commands (0x6000 range)
- 24584 (0x6008) - STAGE_POSITION_GET
- 24592 (0x6010) - STAGE_MOTION_STOPPED
- 24580 (0x6004) - STAGE_POSITION_SET
- 24581 (0x6005) - STAGE_POSITION_SET_SLIDER

---

## Response Field Mapping

Different commands return data in different fields:

### CAMERA_IMAGE_SIZE_GET (12327)
**Returns in:**
- `params[3]` = width (X dimension)
- `params[4]` = height (Y dimension)
- `params[0]` = status flag (1 = valid)

**Example response:**
```
Param[0]: 1     ← Valid
Param[1]: 0
Param[2]: 0
Param[3]: 2048  ← Width (X)
Param[4]: 2048  ← Height (Y)
Param[5]: 0
Param[6]: -2147483648 ← TRIGGER_CALL_BACK flag echoed
Value: 6.5      ← Unknown significance
```

### CAMERA_PIXEL_FIELD_OF_VIEW_GET (12343)
**Returns in:**
- `value` = pixel size in mm/pixel

**Example response:**
```
Value: 0.000253  ← 0.253 µm/pixel
```

### STAGE_POSITION_SET (24580/24581)
**Accepts:**
- `params[0]` = axis (0=X, 1=Y, 2/3=Z)
- `value` = target position in mm

**From microscope logs:**
```
int32Data0 = 1          ← Y axis
doubleData = 7.635      ← Position: 7.635mm
```

---

## Future Enhancements

### Additional Subsystem Services

**LaserService:**
```python
laser_service.set_power(laser_index, power_percent)
laser_service.get_power(laser_index) → float
laser_service.enable_preview(laser_index)
laser_service.disable_all()
```

**LEDService:**
```python
led_service.enable()
led_service.disable()
led_service.set_brightness(led_index, value)
led_service.select_led(led_index)
```

**SystemService:**
```python
system_service.get_state() → MicroscopeState
system_service.wait_for_idle()
system_service.get_configuration() → dict
```

### Motion Callback Handling

Stage movement is asynchronous. Need to implement:
```python
class StageService:
    def wait_for_motion_complete(self, timeout: float = 30.0) → bool:
        """Wait for stage motion to complete."""
        # Listen for unsolicited motion-stopped callback
        # Or poll is_motion_stopped() until True
```

---

## Integration with Existing Code

### Update Controllers

Controllers should use services instead of raw commands:

**Before:**
```python
class WorkflowController:
    def prepare_imaging(self):
        # Get image size via debug query
        result = self.position_controller.debug_query_command(12327, "...")
        # Extract parameters manually
```

**After:**
```python
class WorkflowController:
    def __init__(self, connection):
        self.camera = CameraService(connection)
        self.stage = StageService(connection)

    def prepare_imaging(self):
        width, height = self.camera.get_image_size()
        # Use directly!
```

### Preserve Debug Functionality

Keep `debug_query_command()` for:
- Testing unknown commands
- Debugging protocol issues
- Exploring new commands

But use services for production code.

---

## Conclusion

The subsystem services architecture provides:
- ✓ Clean, typed APIs
- ✓ Organized by hardware subsystem
- ✓ Built-in error handling
- ✓ Proper protocol handling (flags, additional data)
- ✓ Easy to test and mock
- ✓ Self-documenting code

**Next Steps:**
1. Test services on remote PC
2. Add more subsystem services (Laser, LED, System)
3. Update controllers to use services
4. Implement motion callback handling

---

**Architecture Status:** Implemented and ready for testing
**Code Status:** Committed to repository
**Testing:** Requires remote PC with microscope connection
