# Status Indicator System Integration Guide

This document provides instructions for integrating the global status indicator system into the Flamingo microscope GUI.

## Overview

The status indicator system consists of three main components:

1. **StatusIndicatorService** (`services/status_indicator_service.py`): Manages global status state
2. **StatusIndicatorWidget** (`views/widgets/status_indicator_widget.py`): Visual representation
3. **Integration in FlamingoApplication** (`application.py`): Wires everything together

## Current Integration Status

### COMPLETED:
- StatusIndicatorService created with Qt signals
- StatusIndicatorWidget created with smooth color transitions
- Service instantiated in `application.py`
- Widget added to main window status bar
- Connection events wired (connection established/closed)
- Workflow events wired (workflow started/stopped) - IF signals exist in WorkflowView

### PENDING:
- Stage motion events (requires adding signals to PositionController or StageControlView)
- Testing with actual microscope

## Status Color Mapping

| Status | Color | Code | Description |
|--------|-------|------|-------------|
| DISCONNECTED | Grey | `(128, 128, 128)` | Not connected to microscope |
| IDLE | Steel Blue | `(70, 130, 180)` | Connected and ready |
| MOVING | Red | `(220, 50, 50)` | Stage motion in progress |
| WORKFLOW_RUNNING | Magenta | `(200, 50, 200)` | Acquisition workflow active |

## Integration Points

### 1. Connection Events (COMPLETED)

The connection events are wired in `application.py`:

```python
# Connect connection status to status indicator service
if hasattr(self.connection_view, 'connection_established'):
    self.connection_view.connection_established.connect(
        lambda: self.status_indicator_service.on_connection_established()
    )
if hasattr(self.connection_view, 'connection_closed'):
    self.connection_view.connection_closed.connect(
        lambda: self.status_indicator_service.on_connection_closed()
    )
```

### 2. Workflow Events (COMPLETED - if signals exist)

The workflow events are wired in `application.py`:

```python
# Connect workflow events to status indicator service
if hasattr(self.workflow_view, 'workflow_started'):
    self.workflow_view.workflow_started.connect(
        lambda: self.status_indicator_service.on_workflow_started()
    )
if hasattr(self.workflow_view, 'workflow_stopped'):
    self.workflow_view.workflow_stopped.connect(
        lambda: self.status_indicator_service.on_workflow_stopped()
    )
```

**Note:** These connections use `hasattr()` checks, so they will only wire if the signals exist in WorkflowView.

### 3. Stage Motion Events (REQUIRES IMPLEMENTATION)

Stage motion tracking requires adding signals to track when motion starts and stops.

#### Option A: Add signals to PositionController (Recommended)

Modify `/home/msnelson/LSControl/Flamingo_Control/src/py2flamingo/controllers/position_controller.py`:

```python
from PyQt5.QtCore import QObject, pyqtSignal

class PositionController(QObject):
    """Controller for managing microscope stage positions."""

    # Add Qt signals
    motion_started = pyqtSignal()
    motion_stopped = pyqtSignal()

    def __init__(self, connection_service):
        super().__init__()  # Initialize QObject
        # ... rest of existing __init__ code ...

    def move_absolute(self, axis, position_mm, wait=True):
        """Move axis to absolute position."""
        # Emit motion started signal
        self.motion_started.emit()

        # ... existing movement code ...

        # After motion completes (or in motion callback)
        self.motion_stopped.emit()
```

Then wire in `application.py`:

```python
# In setup_dependencies(), after creating position_controller:
self.position_controller.motion_started.connect(
    lambda: self.status_indicator_service.on_motion_started()
)
self.position_controller.motion_stopped.connect(
    lambda: self.status_indicator_service.on_motion_stopped()
)
```

#### Option B: Use MovementController

The codebase has a `MovementController` with motion signals already defined:

```python
# In controllers/movement_controller.py:
motion_started = pyqtSignal(str)  # axis name
motion_stopped = pyqtSignal(str)  # axis name
```

If this controller is integrated:

```python
# In application.py:
self.movement_controller.motion_started.connect(
    lambda axis: self.status_indicator_service.on_motion_started()
)
self.movement_controller.motion_stopped.connect(
    lambda axis: self.status_indicator_service.on_motion_stopped()
)
```

#### Option C: Hook into STAGE_MOTION_STOPPED callbacks

Listen to the callback listener for STAGE_MOTION_STOPPED (0x6010 / 24592) messages:

```python
# In connection_service.py or application.py:
def _on_stage_motion_stopped(self, response):
    """Handle motion stopped callback from microscope."""
    self.status_indicator_service.on_motion_stopped()

# Register handler:
if self.connection_service._callback_listener:
    self.connection_service._callback_listener.register_handler(
        24592,  # STAGE_MOTION_STOPPED
        self._on_stage_motion_stopped
    )
```

**Challenge:** Detecting motion START is harder with this approach. You'd need to:
1. Call `on_motion_started()` before sending any move command
2. Listen for STAGE_MOTION_STOPPED to call `on_motion_stopped()`

### 4. Visual Indicator Widget

The widget is automatically added to the main window status bar in `main_window.py`:

```python
# In _setup_ui():
status_bar = self.statusBar()

# Add status indicator widget to status bar if provided
if self.status_indicator_widget is not None:
    # Add to left side of status bar (permanent widget)
    status_bar.addPermanentWidget(self.status_indicator_widget)
```

## Testing the Integration

### Manual Testing Steps

1. **Start the application:**
   ```bash
   cd /home/msnelson/LSControl/Flamingo_Control/src
   python -m py2flamingo
   ```

2. **Verify initial state:**
   - Status indicator should show Grey with "Disconnected" text
   - Tooltip should say "System Status: Disconnected"

3. **Test connection:**
   - Connect to microscope
   - Status should change to Blue with "Ready" text
   - Color transition should be smooth (300ms animation)

4. **Test workflow (if signals exist):**
   - Start a workflow/acquisition
   - Status should change to Magenta with "Workflow Running" text
   - When workflow completes, status should return to Blue "Ready"

5. **Test motion (after implementing signals):**
   - Move any stage axis
   - Status should change to Red with "Moving" text
   - When motion completes, status should return to Blue "Ready"

6. **Test disconnection:**
   - Disconnect from microscope
   - Status should return to Grey with "Disconnected" text

### Programmatic Testing

You can test the status indicator service directly:

```python
from py2flamingo.services import StatusIndicatorService, GlobalStatus

# Create service
service = StatusIndicatorService()

# Connect to signal
def on_status_changed(status, description):
    print(f"Status changed: {status.value} - {description}")

service.status_changed.connect(on_status_changed)

# Simulate events
service.on_connection_established()  # Should emit IDLE
service.on_motion_started()          # Should emit MOVING
service.on_motion_stopped()          # Should emit IDLE
service.on_workflow_started()        # Should emit WORKFLOW_RUNNING
service.on_workflow_stopped()        # Should emit IDLE
service.on_connection_closed()       # Should emit DISCONNECTED
```

## Alternative Widget Variants

Two widget variants are provided:

1. **StatusIndicatorWidget** (default): Small 15x15px colored square next to text
2. **StatusIndicatorBar**: Thin 4x20px vertical colored bar next to text

To use the bar variant instead, modify `application.py`:

```python
# In create_main_window():
from py2flamingo.views.widgets.status_indicator_widget import StatusIndicatorBar

# Create status indicator widget (bar variant)
self.status_indicator_widget = StatusIndicatorBar()
```

## Priority Order

The status indicator follows this priority (highest to lowest):

1. **DISCONNECTED** - Always shown when not connected (overrides all)
2. **WORKFLOW_RUNNING** - Shown when workflow is active (overrides motion)
3. **MOVING** - Shown when stage is moving
4. **IDLE** - Default state when connected but not busy

This means:
- You cannot see MOVING status during a workflow (workflow takes precedence)
- DISCONNECTED always overrides everything
- IDLE is the fallback when connected and nothing is happening

## Troubleshooting

### Widget not visible
- Check that `status_indicator_widget` is passed to MainWindow constructor
- Check that the widget is added to status bar in `main_window.py`
- Look for any Qt layout warnings in logs

### Status not updating
- Check that signals are connected (look for "Connected ... to status indicator service" in logs)
- Verify that the view emitting the signal actually has that signal defined
- Check for exceptions in signal handlers

### Colors not transitioning smoothly
- Ensure Qt event loop is running
- Check if QPropertyAnimation is working (may need Qt5 >= 5.6)
- Verify no exceptions during color animation

### Workflow signals not connecting
- Check if `workflow_view` has `workflow_started` and `workflow_stopped` signals
- If not, you'll need to add them to WorkflowView similar to connection_view signals

## Future Enhancements

Potential improvements:

1. **Add pulsing animation** for MOVING and WORKFLOW_RUNNING states
2. **Add progress information** (e.g., "Moving X axis", "Workflow 45% complete")
3. **Add detailed tooltip** with current position, workflow name, etc.
4. **Add click-to-view-details** popup with full system status
5. **Add logging integration** to track status changes over time
6. **Add status history** (show last N status changes in tooltip)

## Files Modified

- Created: `services/status_indicator_service.py`
- Created: `views/widgets/status_indicator_widget.py`
- Modified: `services/__init__.py` (added exports)
- Modified: `application.py` (instantiate service, create widget, wire signals)
- Modified: `main_window.py` (add widget to status bar)

## Files to Modify (for Motion Tracking)

Choose one approach:

- **Option A:** Modify `controllers/position_controller.py` (add QObject inheritance and signals)
- **Option B:** Integrate existing `controllers/movement_controller.py` into application
- **Option C:** Add callback registration in connection setup
