# Stage Movement Controls - Quick Start Guide

## 5-Minute Integration

### Prerequisites

You already have:
- `ConnectionService` instance
- `PositionController` instance
- Main window or tab widget

### Step 1: Create Movement Controller (1 minute)

```python
from py2flamingo.controllers.movement_controller import MovementController

# Add to your initialization code
self.movement_controller = MovementController(
    connection_service=self.connection_service,
    position_controller=self.position_controller
)
```

### Step 2: Create Enhanced View (1 minute)

```python
from py2flamingo.views.enhanced_stage_control_view import EnhancedStageControlView

# Create view
self.stage_view = EnhancedStageControlView(self.movement_controller)

# Add to your tab widget
self.tab_widget.addTab(self.stage_view, "Stage Control")
```

### Step 3: Run and Test (3 minutes)

1. Launch your application
2. Connect to microscope
3. Go to "Stage Control" tab
4. Test features:
   - View current position (updates in real-time)
   - Enter target position and click "Go To"
   - Try jog buttons for relative movement
   - Click "Home All Axes"

Done! You now have complete stage control.

## Optional: Add Map Visualization

```python
from py2flamingo.views.widgets.stage_map_widget import StageMapWidget

# Create map
limits = self.movement_controller.get_stage_limits()
self.map_widget = StageMapWidget(limits)

# Connect to updates
self.movement_controller.position_changed.connect(
    self.map_widget.update_position
)

# Add to layout
self.tab_widget.addTab(self.map_widget, "Position Map")
```

## Common Tasks

### Move to Absolute Position

```python
# GUI: Enter value in target field, click "Go To X"
# Code:
self.movement_controller.move_absolute('x', 10.5)
```

### Relative Movement (Jog)

```python
# GUI: Click jog button (e.g., "+1.0")
# Code:
self.movement_controller.move_relative('y', 1.0)  # +1mm
self.movement_controller.move_relative('z', -0.5)  # -0.5mm
```

### Home Axes

```python
# GUI: Click "Home X" or "Home All Axes"
# Code:
self.movement_controller.home_axis('x')  # Single axis
self.movement_controller.position_controller.go_home()  # All axes
```

### Save N7 Reference

```python
# GUI: Click "Set Current as N7 Reference"
# Code:
self.movement_controller.save_n7_reference()
```

### Go to N7 Reference

```python
# GUI: Click "Go To N7 Reference"
# Code:
n7_ref = self.movement_controller.get_n7_reference()
if n7_ref:
    self.movement_controller.position_controller.move_to_position(n7_ref)
```

### Emergency Stop

```python
# GUI: Click "🛑 EMERGENCY STOP"
# Code:
self.movement_controller.halt_motion()
```

## Features Overview

### What You Get

✅ **Real-Time Position Display**
- Updates every 500ms
- Shows X, Y, Z (mm) and R (degrees)
- Color-coded status indicators

✅ **Absolute Positioning**
- Target input fields with validation
- "Go To" buttons for each axis
- Bounds checking (prevents invalid moves)

✅ **Relative Movement (Jog)**
- ±0.1, ±1.0, ±10.0 mm buttons
- ±1°, ±10°, ±45° for rotation
- Instant feedback

✅ **Homing**
- Individual axis homing
- "Home All Axes" button
- Uses positions from ScopeSettings.txt

✅ **N7 Reference Position**
- Save current position as reference
- Quick return to reference
- Stored in JSON file

✅ **Position Verification**
- Automatic after each move
- ±0.001 mm tolerance (1 micron)
- Visual feedback (✓ or ⚠)

✅ **Emergency Stop**
- Immediate halt
- Disables controls
- Requires clear before resuming

✅ **Map Visualization** (optional)
- 2D X-Y position plot
- Shows current and target
- Movement path arrows
- Real-time updates

## API Quick Reference

```python
# Movement
movement_controller.move_absolute(axis, position_mm)
movement_controller.move_relative(axis, delta_mm)
movement_controller.home_axis(axis)
movement_controller.halt_motion()

# Position
pos = movement_controller.get_position()  # All axes
x = movement_controller.get_position(axis='x')  # Single axis

# N7 Reference
movement_controller.save_n7_reference()
n7_ref = movement_controller.get_n7_reference()

# Verification
success, msg = movement_controller.verify_position(target_pos)

# Signals
movement_controller.position_changed.connect(callback)
movement_controller.motion_started.connect(callback)
movement_controller.motion_stopped.connect(callback)
movement_controller.position_verified.connect(callback)
movement_controller.error_occurred.connect(callback)
```

## File Locations

```
New Files:
  src/py2flamingo/controllers/movement_controller.py
  src/py2flamingo/views/enhanced_stage_control_view.py
  src/py2flamingo/views/widgets/stage_map_widget.py

Configuration:
  microscope_settings/n7_reference_position.json

Documentation:
  STAGE_MOVEMENT_INTEGRATION.md (detailed guide)
  STAGE_CONTROL_QUICKSTART.md (this file)

Examples:
  examples/stage_control_example.py
```

## Troubleshooting

**Problem:** Position shows 0.000

**Solution:** Start position monitoring:
```python
movement_controller.start_position_monitoring()
```

**Problem:** Movement doesn't work

**Solution:** Check connection:
```python
if not movement_controller.is_connected():
    print("Not connected!")
```

**Problem:** "Movement already in progress" error

**Solution:** Wait for current movement to complete, or use emergency stop

**Problem:** Position verification fails

**Solution:** Increase tolerance if needed:
```python
movement_controller.tolerance.linear_mm = 0.002
```

## Next Steps

1. ✅ Integrate into your application (5 minutes)
2. ✅ Test basic movements
3. ✅ Set N7 reference position
4. 📚 Read full guide: `STAGE_MOVEMENT_INTEGRATION.md`
5. 🔍 Try example code: `examples/stage_control_example.py`

## Support

See `STAGE_MOVEMENT_INTEGRATION.md` for:
- Complete API reference
- Detailed integration steps
- Protocol details
- Advanced features
- Troubleshooting guide
