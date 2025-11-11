# Camera Live Feed Implementation

This document describes the camera live feed viewer implementation for the Flamingo microscope control system.

## Overview

The camera live feed system provides real-time image streaming with the following features:

- **Live view streaming** from camera data port (53718)
- **Exposure time control** (100µs to 1s)
- **Auto-scaling** with manual override for display intensity
- **Frame rate monitoring** with configurable display FPS limiting
- **Image information overlay** showing dimensions, exposure, intensity range
- **Crosshair and zoom controls** for sample alignment
- **Thread-safe design** using Qt signals/slots

## Architecture

The implementation follows the MVC pattern with three main components:

### 1. CameraService (Enhanced)
**File**: `/src/py2flamingo/services/camera_service.py`

**Purpose**: Low-level hardware interface for camera operations

**Key Features**:
- Connects to image data port (53718) in addition to control port (53717)
- Parses 40-byte `ImageHeader` structure before each frame
- Receives 16-bit image data and converts to numpy arrays
- Provides callback mechanism for image delivery
- Manages background thread for continuous streaming
- Tracks frame rate from timestamps

**Key Classes**:
- `ImageHeader`: 40-byte header structure (10 × uint32)
  - `image_size`: Total bytes of image data
  - `image_width`, `image_height`: Dimensions in pixels
  - `image_scale_min`, `image_scale_max`: For display normalization
  - `timestamp_ms`: Frame timestamp
  - `frame_number`: Sequential frame counter
  - `exposure_us`: Exposure time in microseconds
  - Reserved fields for future use

**New Methods**:
```python
# Set callback for image delivery
camera_service.set_image_callback(callback_function)

# Start live view with data streaming
camera_service.start_live_view_streaming(data_port=53718)

# Stop live view and close data connection
camera_service.stop_live_view_streaming()

# Check if streaming active
is_active = camera_service.is_streaming()

# Get current frame rate
fps = camera_service.get_frame_rate()
```

### 2. CameraController
**File**: `/src/py2flamingo/controllers/camera_controller.py`

**Purpose**: Manages camera state, buffering, and display parameters

**Key Features**:
- State management (IDLE, LIVE_VIEW, ACQUIRING, ERROR)
- Frame buffering (keeps last N frames)
- Display intensity scaling with auto-scale option
- Frame rate limiting for display (max 30 FPS default)
- Qt signal emission for thread-safe UI updates
- Exposure time management

**Qt Signals**:
- `new_image(np.ndarray, ImageHeader)`: New image available
- `state_changed(CameraState)`: Camera state changed
- `error_occurred(str)`: Error message
- `frame_rate_updated(float)`: FPS update

**Key Methods**:
```python
# Control live view
controller.start_live_view()
controller.stop_live_view()

# Configure display
controller.set_exposure_time(exposure_us)
controller.set_display_range(min_val, max_val)
controller.set_auto_scale(enabled)
controller.set_max_display_fps(fps)

# Query state
state = controller.state
fps = controller.get_frame_rate()
frames = controller.get_buffered_frames()
```

### 3. CameraLiveViewer
**File**: `/src/py2flamingo/views/camera_live_viewer.py`

**Purpose**: Qt widget providing UI for live camera feed

**UI Components**:

**Controls Group**:
- Start/Stop Live View buttons
- Exposure time spinbox (µs) with ms conversion
- Auto-scale intensity checkbox
- Min/Max intensity sliders (0-65535)
- Crosshair toggle
- Zoom control (100%-400%)

**Display Group**:
- QLabel showing live image
- Auto-scaling to fit window
- Maintains aspect ratio
- Crosshair overlay (optional)

**Info Group**:
- Status indicator (Idle/Active/Error)
- Image dimensions and frame number
- Frame rate (FPS)
- Actual exposure time from camera
- Intensity range from image

**Features**:
- 16-bit to 8-bit conversion with proper scaling
- Configurable display update rate (30 FPS default)
- Thread-safe image updates via Qt signals
- Proper cleanup on widget close

## Data Flow

```
Microscope (port 53718)
        |
        | 40-byte ImageHeader + Image Data (16-bit)
        v
CameraService._data_receiver_loop()
        |
        | Parse header, convert to numpy array
        v
CameraService callback
        |
        | Via callback function
        v
CameraController._on_image_received()
        |
        | Buffer, rate limit, emit Qt signal
        v
CameraController.new_image signal
        |
        | Thread-safe Qt signal/slot
        v
CameraLiveViewer._on_new_image()
        |
        | Scale to 8-bit, add overlay, display
        v
QLabel (UI Display)
```

## Integration Instructions

### 1. Create Camera Service

First, ensure you have a connection service instance:

```python
from py2flamingo.services.connection_service import ConnectionService
from py2flamingo.services.camera_service import CameraService

# Assuming you have a connection service
connection = ConnectionService(ip="192.168.1.100", port=53717)
connection.connect()

# Create camera service
camera_service = CameraService(connection)
```

### 2. Create Camera Controller

```python
from py2flamingo.controllers.camera_controller import CameraController

# Create controller with service
camera_controller = CameraController(camera_service)

# Optional: Configure display settings
camera_controller.set_max_display_fps(30.0)
camera_controller.set_auto_scale(True)
```

### 3. Create and Show Viewer Widget

```python
from py2flamingo.views.camera_live_viewer import CameraLiveViewer

# Create viewer widget
viewer = CameraLiveViewer(camera_controller)

# Add to your layout or show as standalone
viewer.show()
```

### 4. Integration with Existing LiveFeedView

To integrate with the existing `LiveFeedView`, you can add the `CameraLiveViewer` as a new group or replace existing components:

```python
from py2flamingo.views.live_feed_view import LiveFeedView
from py2flamingo.views.camera_live_viewer import CameraLiveViewer

# In LiveFeedView.setup_ui() method, add:

# Camera Live Feed section
camera_live_group = QGroupBox("Camera Live Feed")
camera_live_layout = QVBoxLayout()

self.camera_live_viewer = CameraLiveViewer(self.camera_controller)
camera_live_layout.addWidget(self.camera_live_viewer)

camera_live_group.setLayout(camera_live_layout)
right_layout.addWidget(camera_live_group)
```

### 5. Cleanup on Close

Ensure proper cleanup when the application closes:

```python
# In your main window's closeEvent or cleanup method:
def closeEvent(self, event):
    # Stop camera if streaming
    if camera_controller.state == CameraState.LIVE_VIEW:
        camera_controller.stop_live_view()

    # Call viewer cleanup
    viewer.cleanup()

    event.accept()
```

## Protocol Details

### ImageHeader Structure (40 bytes)

The image header is sent before each frame on the data port (53718):

| Offset | Type    | Field              | Description                           |
|--------|---------|--------------------|---------------------------------------|
| 0      | uint32  | image_size         | Total image data size in bytes        |
| 4      | uint32  | image_width        | Width in pixels                       |
| 8      | uint32  | image_height       | Height in pixels                      |
| 12     | uint32  | image_scale_min    | Min intensity for display scaling     |
| 16     | uint32  | image_scale_max    | Max intensity for display scaling     |
| 20     | uint32  | timestamp_ms       | Timestamp in milliseconds             |
| 24     | uint32  | frame_number       | Sequential frame number               |
| 28     | uint32  | exposure_us        | Exposure time in microseconds         |
| 32     | uint32  | reserved1          | Reserved for future use               |
| 36     | uint32  | reserved2          | Reserved for future use               |

All values are little-endian format.

### Image Data Format

- **Bit depth**: 16-bit unsigned integers (uint16)
- **Byte order**: Little-endian
- **Size**: `image_width × image_height × 2` bytes
- **Layout**: Row-major order (standard numpy/C layout)

### Connection Sequence

1. **Connect to data port (53718)**:
   ```python
   data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
   data_socket.connect((ip, 53718))
   ```

2. **Send LIVE_VIEW_START command** on control port (53717):
   ```python
   camera_service.start_live_view()  # Uses existing control socket
   ```

3. **Receive frames** on data socket:
   ```
   Loop while streaming:
       - Receive 40 bytes (header)
       - Parse header using ImageHeader.from_bytes()
       - Receive header.image_size bytes (image data)
       - Convert to numpy array: np.frombuffer(data, dtype=uint16)
       - Reshape: array.reshape((height, width))
       - Deliver to callback
   ```

4. **Send LIVE_VIEW_STOP command** on control port:
   ```python
   camera_service.stop_live_view()
   ```

5. **Close data socket**

## Performance Considerations

### Frame Rate Limiting

The controller limits display updates to 30 FPS by default to prevent UI overload:

```python
# Change max display FPS
camera_controller.set_max_display_fps(20.0)  # Limit to 20 FPS
```

Even if the camera sends at 40 FPS, the display will skip frames to maintain the target rate.

### Memory Management

The controller maintains a circular buffer of recent frames:

```python
# Default buffer size: 10 frames
# Get buffered frames
frames = camera_controller.get_buffered_frames()

# Clear buffer if needed
camera_controller.clear_buffer()
```

### Thread Safety

All communication between the data receiver thread and UI uses Qt signals/slots, ensuring thread safety:

- **Data thread** (CameraService): Receives raw data from socket
- **Controller** (CameraController): Processes and emits Qt signals
- **UI thread** (CameraLiveViewer): Receives signals and updates display

## Testing Without Hardware

For testing without a microscope, you can create a mock camera service:

```python
import numpy as np
import time
from py2flamingo.services.camera_service import ImageHeader

class MockCameraService:
    """Mock camera service for testing."""

    def __init__(self):
        self._callback = None
        self._streaming = False
        self._thread = None

    def set_image_callback(self, callback):
        self._callback = callback

    def start_live_view_streaming(self, data_port=53718):
        self._streaming = True
        self._thread = threading.Thread(target=self._mock_stream)
        self._thread.start()

    def _mock_stream(self):
        """Generate mock images."""
        frame_num = 0
        while self._streaming:
            # Generate random 16-bit image
            image = np.random.randint(0, 4096, (512, 512), dtype=np.uint16)

            # Create mock header
            header = ImageHeader(
                image_size=512 * 512 * 2,
                image_width=512,
                image_height=512,
                image_scale_min=0,
                image_scale_max=4095,
                timestamp_ms=int(time.time() * 1000),
                frame_number=frame_num,
                exposure_us=10000,
                reserved1=0,
                reserved2=0
            )

            if self._callback:
                self._callback(image, header)

            frame_num += 1
            time.sleep(1/30)  # 30 FPS

    def stop_live_view_streaming(self):
        self._streaming = False
        if self._thread:
            self._thread.join()

    def is_streaming(self):
        return self._streaming

    def get_frame_rate(self):
        return 30.0 if self._streaming else 0.0
```

## Troubleshooting

### No images appearing

1. **Check connection**: Verify microscope is connected and responding
2. **Check data port**: Ensure port 53718 is accessible
3. **Check firewall**: Verify no firewall blocking port 53718
4. **Check logs**: Look for errors in camera service logs

### Low frame rate

1. **Network bandwidth**: Check network connection quality
2. **Display FPS limit**: Verify `max_display_fps` setting
3. **CPU load**: Check if image processing is CPU-bound
4. **Exposure time**: Higher exposure = lower max frame rate

### Image appears wrong

1. **Check scaling**: Verify auto-scale is enabled or manual range is correct
2. **Check dimensions**: Verify image dimensions match camera settings
3. **Check bit depth**: Ensure 16-bit data is being received
4. **Check endianness**: Verify little-endian parsing

## Future Enhancements

Potential improvements for future versions:

1. **Histogram display**: Show intensity histogram for better exposure control
2. **ROI selection**: Allow selecting region of interest for zoomed view
3. **Image saving**: Save individual frames or sequences
4. **Measurement tools**: Add line/circle measurement overlays
5. **False color maps**: Support different color maps beyond grayscale
6. **Hardware binning**: Control camera binning for faster frame rates
7. **Trigger modes**: Support external triggering
8. **Multi-camera**: Support multiple camera streams simultaneously

## References

- Camera command codes: `services/camera_service.py`
- TCP protocol: `core/tcp_protocol.py`
- Connection management: `services/connection_service.py`
- Image transformations: `utils/image_transforms.py`
- Image processing: `utils/image_processing.py`
