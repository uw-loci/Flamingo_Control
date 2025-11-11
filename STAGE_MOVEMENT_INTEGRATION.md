# Stage Movement Controls - Integration Guide

## Overview

This document provides complete integration instructions for the enhanced stage movement control system for the Flamingo microscope.

**Created:** 2025-11-10
**Author:** Claude (Anthropic)
**Version:** 1.0

## What's New

The enhanced stage control system provides:

1. **MovementController** - Enhanced controller with:
   - Absolute and relative movement methods
   - N7 reference position management
   - Position verification (±0.001 mm tolerance)
   - Real-time position monitoring
   - Qt signals for UI updates

2. **EnhancedStageControlView** - Complete UI with:
   - Real-time position display for X, Y, Z, R
   - Target position inputs with "Go To" buttons
   - Relative movement controls (±0.1, ±1.0, ±10.0 mm)
   - Individual axis homing
   - Emergency stop
   - N7 reference position save/load
   - Position verification status display

3. **StageMapWidget** - 2D visualization showing:
   - Stage boundaries
   - Current position
   - Target position
   - Movement path/vector
   - Real-time updates during motion

## File Locations

### New Files Created

```
src/py2flamingo/
├── controllers/
│   └── movement_controller.py          # Enhanced movement controller with signals
├── views/
│   ├── enhanced_stage_control_view.py  # Complete stage control UI
│   └── widgets/
│       └── stage_map_widget.py         # 2D position visualization
```

### Existing Files Used

```
src/py2flamingo/
├── controllers/
│   ├── position_controller.py          # Existing position controller (wrapped by MovementController)
│   └── motion_tracker.py               # Motion completion tracking
├── services/
│   ├── stage_service.py                # Hardware position queries
│   └── connection_service.py           # Microscope communication
├── core/
│   ├── tcp_protocol.py                 # Protocol encoder/decoder
│   ├── command_codes.py                # Command code definitions
│   └── protocol_encoder.py             # Binary protocol encoding
└── models/
    └── microscope.py                   # Position data model
```

### Configuration Files

```
microscope_settings/
└── n7_reference_position.json          # N7 reference position storage
```

## Integration Steps

### Step 1: Import Required Modules

In your main window or application file where you want to use the enhanced stage controls:

```python
from py2flamingo.controllers.movement_controller import MovementController
from py2flamingo.views.enhanced_stage_control_view import EnhancedStageControlView
from py2flamingo.views.widgets.stage_map_widget import StageMapWidget
```

### Step 2: Initialize Controllers

When setting up your application (typically in `main_window.py` or `application.py`):

```python
# Assuming you already have:
# - connection_service: ConnectionService instance
# - position_controller: PositionController instance

# Create enhanced movement controller
self.movement_controller = MovementController(
    connection_service=self.connection_service,
    position_controller=self.position_controller
)
```

### Step 3: Create Enhanced View

Replace or supplement your existing stage control view:

```python
# Create enhanced stage control view
self.stage_control_view = EnhancedStageControlView(
    movement_controller=self.movement_controller
)

# Add to your tab widget or layout
self.tab_widget.addTab(self.stage_control_view, "Stage Control")
```

### Step 4: Add Map Visualization (Optional)

To add the 2D position map:

```python
# Get stage limits
limits = self.movement_controller.get_stage_limits()

# Create map widget
self.stage_map = StageMapWidget(stage_limits=limits)

# Connect to position updates
self.movement_controller.position_changed.connect(
    self.stage_map.update_position
)

# Add to your layout (e.g., in a separate tab or split view)
self.tab_widget.addTab(self.stage_map, "Position Map")
```

### Step 5: Complete Integration Example

Here's a complete example of integrating into your main window:

```python
# File: src/py2flamingo/main_window.py

from PyQt5.QtWidgets import QMainWindow, QTabWidget, QSplitter
from PyQt5.QtCore import Qt

from py2flamingo.controllers.position_controller import PositionController
from py2flamingo.controllers.movement_controller import MovementController
from py2flamingo.views.enhanced_stage_control_view import EnhancedStageControlView
from py2flamingo.views.widgets.stage_map_widget import StageMapWidget

class MainWindow(QMainWindow):
    def __init__(self, connection_service):
        super().__init__()

        self.connection_service = connection_service

        # Initialize controllers
        self.position_controller = PositionController(connection_service)
        self.movement_controller = MovementController(
            connection_service,
            self.position_controller
        )

        # Setup UI
        self.setup_ui()

    def setup_ui(self):
        """Setup main window UI."""
        # Create tab widget
        self.tab_widget = QTabWidget()

        # Create enhanced stage control view
        self.stage_control_view = EnhancedStageControlView(
            self.movement_controller
        )

        # Option 1: Stage control in single tab
        self.tab_widget.addTab(self.stage_control_view, "Stage Control")

        # Option 2: Stage control with map in split view
        # Create splitter
        splitter = QSplitter(Qt.Horizontal)

        # Add stage control view
        splitter.addWidget(self.stage_control_view)

        # Add map visualization
        limits = self.movement_controller.get_stage_limits()
        self.stage_map = StageMapWidget(limits)
        self.movement_controller.position_changed.connect(
            self.stage_map.update_position
        )
        splitter.addWidget(self.stage_map)

        # Set splitter sizes (60% controls, 40% map)
        splitter.setSizes([600, 400])

        self.tab_widget.addTab(splitter, "Stage Control")

        # Set as central widget
        self.setCentralWidget(self.tab_widget)
```

## API Reference

### MovementController

#### Movement Methods

```python
# Absolute movement (single axis)
movement_controller.move_absolute(axis='x', position_mm=10.5, verify=True)
# axis: 'x', 'y', 'z', or 'r'
# position_mm: target position in mm (or degrees for rotation)
# verify: whether to verify position after movement

# Relative movement (single axis)
movement_controller.move_relative(axis='y', delta_mm=0.5, verify=True)
# delta_mm: amount to move (positive or negative)

# Home single axis
movement_controller.home_axis(axis='x')

# Emergency stop
movement_controller.halt_motion()
```

#### Position Query

```python
# Get current position for all axes
position = movement_controller.get_position()  # Returns Position object
# position.x, position.y, position.z, position.r

# Get single axis position
x = movement_controller.get_position(axis='x')  # Returns float
```

#### N7 Reference Position

```python
# Save current position as N7 reference
success = movement_controller.save_n7_reference()

# Save specific position as N7 reference
position = Position(x=0.0, y=0.0, z=0.0, r=0.0)
success = movement_controller.save_n7_reference(position)

# Get N7 reference position
n7_ref = movement_controller.get_n7_reference()  # Returns Position or None
```

#### Position Verification

```python
# Verify position matches target (within tolerance)
target = Position(x=10.0, y=5.0, z=2.0, r=45.0)
success, message = movement_controller.verify_position(target)
# Returns: (True, "Position verified successfully") or
#          (False, "Position verification failed: X: target=10.000, actual=10.005")
```

#### Position Monitoring

```python
# Start monitoring (polls every 500ms by default)
movement_controller.start_position_monitoring(interval=0.5)

# Stop monitoring
movement_controller.stop_position_monitoring()
```

#### Qt Signals

Connect to these signals for UI updates:

```python
# Position changed (emitted every 500ms when monitoring)
movement_controller.position_changed.connect(callback)
# Signature: callback(x: float, y: float, z: float, r: float)

# Motion started
movement_controller.motion_started.connect(callback)
# Signature: callback(axis_name: str)  # e.g., "X", "Y", "Z", "R"

# Motion stopped (emitted when motion completes)
movement_controller.motion_stopped.connect(callback)
# Signature: callback(axis_name: str)

# Position verified
movement_controller.position_verified.connect(callback)
# Signature: callback(success: bool, message: str)

# Error occurred
movement_controller.error_occurred.connect(callback)
# Signature: callback(message: str)
```

### StageMapWidget

```python
# Create map widget
limits = {'x': {'min': 0, 'max': 26}, 'y': {'min': 0, 'max': 26}}
map_widget = StageMapWidget(stage_limits=limits)

# Update current position
map_widget.set_current_position(x=10.5, y=5.2)

# Set target position (shows path)
map_widget.set_target_position(x=15.0, y=8.0)

# Clear target
map_widget.clear_target()

# Set motion state (visual feedback)
map_widget.set_moving(True)  # Shows pulsing animation
map_widget.set_moving(False)

# Connect to position updates (automatic)
movement_controller.position_changed.connect(map_widget.update_position)
```

## Configuration

### N7 Reference Position File

Location: `/microscope_settings/n7_reference_position.json`

Format:
```json
{
  "microscope": "N7",
  "description": "Reference starting position for N7 microscope",
  "timestamp": "2025-11-10T17:30:00Z",
  "position": {
    "x_mm": 13.0,
    "y_mm": 13.0,
    "z_mm": 13.0,
    "r_degrees": 0.0
  },
  "notes": "This file stores the current/reference position of the N7 microscope."
}
```

The file is automatically created when you click "Set Current as N7 Reference" in the UI.

### Position Verification Tolerance

Default tolerance settings (in `movement_controller.py`):

```python
@dataclass
class PositionTolerance:
    linear_mm: float = 0.001  # ±0.001 mm for X, Y, Z
    rotation_deg: float = 0.01  # ±0.01 degrees for rotation
```

To customize:

```python
movement_controller.tolerance.linear_mm = 0.002  # ±2 microns
movement_controller.tolerance.rotation_deg = 0.05  # ±0.05 degrees
```

### Position Monitoring Interval

Default: 500ms (0.5 seconds)

To customize:

```python
movement_controller.start_position_monitoring(interval=0.25)  # 250ms
```

## Protocol Details

### Movement Commands

The system uses the Flamingo TCP protocol defined in:
- `src/py2flamingo/core/tcp_protocol.py`
- `src/py2flamingo/core/command_codes.py`

**Stage Position Set (0x6005):**
```
Command Code: 24580 (0x6004) or 24581 (0x6005)
params[0]: axis code (1=X, 2=Y, 3=Z, 4=R)
params[6]: CommandDataBits.TRIGGER_CALL_BACK (0x80000000)
value: position in mm (or degrees for rotation)
```

**Stage Position Get (0x6008):**
```
Command Code: 24584 (0x6008)
params[3]: axis code (1=X, 2=Y, 3=Z, 4=R)
params[6]: CommandDataBits.TRIGGER_CALL_BACK (0x80000000)
Response: position in doubleData field
```

**Motion Stopped Callback (0x6010):**
```
Command Code: 24592 (0x6010)
Sent by microscope when motion completes
Tracked by MotionTracker class
```

### Position Monitoring

During motion:
- Server sends position updates every 25ms (callback rate)
- GUI polls cached position every 500ms for display updates
- Motion completion detected via MOTION_STOPPED callback

### Error Handling

The system handles:
- Connection failures (raises RuntimeError)
- Invalid positions (raises ValueError with bounds info)
- Motion timeout (30 seconds default)
- Position verification failures (emits warning signal)
- Emergency stop (sets flag, prevents new movements)

## Testing

### Manual Testing Steps

1. **Connection Test:**
   - Launch application
   - Connect to microscope
   - Verify "Ready" status in stage control view

2. **Position Display Test:**
   - Check that current position displays correctly
   - Position should update from home position or last known position

3. **Absolute Movement Test:**
   - Enter target position (e.g., X=10.0)
   - Click "Go To X"
   - Verify:
     - Status shows "Moving X..."
     - Position updates in real-time
     - Status shows "X motion complete" when done
     - Position matches target (within tolerance)

4. **Relative Movement Test:**
   - Click jog button (e.g., "+1.0" for X)
   - Verify position increases by 1.0 mm
   - Click "−0.1" button
   - Verify position decreases by 0.1 mm

5. **Home Test:**
   - Click "Home X" button
   - Verify X moves to home position from settings
   - Click "Home All Axes"
   - Verify all axes return to home

6. **N7 Reference Test:**
   - Move to a known position
   - Click "Set Current as N7 Reference"
   - Verify file saved to `/microscope_settings/n7_reference_position.json`
   - Move to different position
   - Click "Go To N7 Reference"
   - Verify stage returns to saved position

7. **Emergency Stop Test:**
   - Start a long movement
   - Click "EMERGENCY STOP"
   - Verify motion halts
   - Verify status shows "EMERGENCY STOPPED"
   - Verify controls are disabled

8. **Map Visualization Test:**
   - Move stage to various positions
   - Verify map marker updates
   - Set target position
   - Verify path arrow appears
   - Start movement
   - Verify marker pulses during motion

### Automated Testing

Create test file: `tests/test_movement_controller.py`

```python
import pytest
from py2flamingo.controllers.movement_controller import MovementController
from py2flamingo.models.microscope import Position

def test_absolute_movement(movement_controller):
    """Test absolute movement command."""
    result = movement_controller.move_absolute('x', 10.0)
    assert result == True

def test_relative_movement(movement_controller):
    """Test relative movement command."""
    result = movement_controller.move_relative('y', 1.5)
    assert result == True

def test_n7_reference_save(movement_controller, tmp_path):
    """Test N7 reference position save/load."""
    # Set reference file to temp path
    movement_controller.n7_reference_file = tmp_path / "n7_ref.json"

    # Save reference
    pos = Position(x=1.0, y=2.0, z=3.0, r=45.0)
    success = movement_controller.save_n7_reference(pos)
    assert success == True

    # Load reference
    loaded = movement_controller.get_n7_reference()
    assert loaded.x == 1.0
    assert loaded.y == 2.0
    assert loaded.z == 3.0
    assert loaded.r == 45.0
```

## Troubleshooting

### Issue: Position not updating

**Symptoms:** Position display shows "0.000 mm" or doesn't update

**Solutions:**
1. Check position monitoring is started:
   ```python
   movement_controller.start_position_monitoring()
   ```

2. Verify connection to microscope:
   ```python
   if not movement_controller.is_connected():
       print("Not connected!")
   ```

3. Check position controller initialization:
   ```python
   pos = movement_controller.position_controller.get_current_position()
   print(f"Position: {pos}")
   ```

### Issue: Movement commands timeout

**Symptoms:** Movement doesn't start, or hangs indefinitely

**Solutions:**
1. Check TRIGGER_CALL_BACK flag is set (automatically handled by MovementController)

2. Verify command socket is connected:
   ```python
   if movement_controller.connection._command_socket is None:
       print("Command socket not connected!")
   ```

3. Check for emergency stop state:
   ```python
   if movement_controller.position_controller.is_emergency_stopped():
       movement_controller.position_controller.clear_emergency_stop()
   ```

### Issue: Position verification fails

**Symptoms:** Warning "Position verification failed" after movement

**Solutions:**
1. Check tolerance settings:
   ```python
   # Increase tolerance if needed
   movement_controller.tolerance.linear_mm = 0.002  # 2 microns
   ```

2. Verify hardware is responding:
   ```python
   pos = movement_controller.stage_service.get_position()
   print(f"Hardware position: {pos}")
   ```

3. Check for mechanical issues (backlash, binding)

### Issue: Map not updating

**Symptoms:** 2D map doesn't show current position or movement

**Solutions:**
1. Verify signal connection:
   ```python
   movement_controller.position_changed.connect(map_widget.update_position)
   ```

2. Check widget visibility:
   ```python
   map_widget.setVisible(True)
   map_widget.update()  # Force repaint
   ```

3. Verify position data is valid:
   ```python
   pos = movement_controller.get_position()
   print(f"Current position: {pos}")
   ```

## Performance Considerations

### Position Monitoring Rate

- Default: 500ms (2 Hz)
- Recommended: 250-1000ms
- Faster rates increase CPU/network load

### Motion Tracking

- Server callback rate: 25ms (40 Hz)
- Handled in background thread
- No impact on GUI responsiveness

### Map Rendering

- Updates triggered by Qt signals
- Uses double buffering (automatic in Qt)
- Typical render time: <5ms

## Future Enhancements

Potential improvements for future versions:

1. **3D Visualization:**
   - Add Z-axis to map (3D view)
   - Show rotation as orientation indicator

2. **Multi-Axis Trajectories:**
   - Coordinated XYZ movements
   - Smooth path interpolation

3. **Position Presets:**
   - Save multiple named positions
   - Quick recall buttons
   - Import/export preset lists

4. **Advanced Verification:**
   - Continuous position feedback during motion
   - Drift detection and correction
   - Repeatability statistics

5. **Safety Enhancements:**
   - Collision detection zones
   - Speed limiting in specific areas
   - Confirmation dialogs for large movements

## Support

For issues or questions:

1. Check this integration guide
2. Review protocol reference: `src/py2flamingo/core/tcp_protocol.py`
3. Enable debug logging:
   ```python
   import logging
   logging.basicConfig(level=logging.DEBUG)
   ```

4. Check log files for detailed error messages

## Changelog

### Version 1.0 (2025-11-10)

- Initial implementation
- MovementController with full movement API
- EnhancedStageControlView with complete UI
- StageMapWidget for 2D visualization
- N7 reference position management
- Position verification (±0.001 mm tolerance)
- Real-time position monitoring
- Emergency stop functionality
- Qt signal-based UI updates
