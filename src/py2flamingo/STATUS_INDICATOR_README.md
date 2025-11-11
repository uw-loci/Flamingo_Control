# Global Status Indicator System - Implementation Summary

## Overview

A comprehensive global status indicator system has been implemented for the Flamingo microscope GUI. The system shows system state with color-coded indicators and updates automatically based on connection status, workflow state, and stage motion.

## Components Implemented

### 1. StatusIndicatorService (`services/status_indicator_service.py`)
- Manages global system status state
- Tracks connection, motion, and workflow states
- Emits Qt signals when status changes
- Implements priority-based status calculation:
  1. DISCONNECTED (highest priority)
  2. WORKFLOW_RUNNING
  3. MOVING
  4. IDLE (lowest priority)

**Key Methods:**
- `on_connection_established()` - Call when connected
- `on_connection_closed()` - Call when disconnected
- `on_motion_started()` - Call when stage motion begins
- `on_motion_stopped()` - Call when stage motion completes
- `on_workflow_started()` - Call when workflow starts
- `on_workflow_stopped()` - Call when workflow completes

**Signals:**
- `status_changed(GlobalStatus, str)` - Emitted when status changes

### 2. StatusIndicatorWidget (`views/widgets/status_indicator_widget.py`)
- Visual representation of system status
- Two variants provided:
  - **StatusIndicatorWidget**: 15x15px colored square + text
  - **StatusIndicatorBar**: 4x20px vertical bar + text
- Smooth color transitions (300ms animation)
- Tooltip shows current status

**Status Colors:**
- **Grey** `(128, 128, 128)`: Disconnected
- **Steel Blue** `(70, 130, 180)`: Ready/Idle
- **Red** `(220, 50, 50)`: Moving
- **Magenta** `(200, 50, 200)`: Workflow Running

### 3. Integration in Application (`application.py`)
- StatusIndicatorService created in `setup_dependencies()`
- Widget created in `create_main_window()`
- Service signals connected to widget
- Connection events wired (established/closed)
- Workflow events wired (started/stopped) - if signals exist

### 4. Main Window Integration (`main_window.py`)
- Status indicator widget added to status bar
- Uses `addPermanentWidget()` for persistent display

### 5. Motion Tracking Support
- **PositionControllerMotionAdapter** created for non-invasive motion tracking
- Wraps existing PositionController without modifications
- Emits `motion_started` and `motion_stopped` signals
- Can be integrated via multiple approaches (see examples)

## Files Created

```
services/
  status_indicator_service.py         # Status management service

views/widgets/
  status_indicator_widget.py          # Visual indicator widgets

controllers/
  position_controller_adapter.py      # Motion tracking adapter

Documentation:
  STATUS_INDICATOR_README.md          # This file
  STATUS_INDICATOR_INTEGRATION.md     # Detailed integration guide
  MOTION_TRACKING_EXAMPLES.py         # Motion tracking examples
  test_status_indicator.py            # Test script
```

## Files Modified

```
services/__init__.py                  # Added exports
application.py                        # Added service, widget, signal wiring
main_window.py                        # Added widget to status bar
```

## Current Integration Status

### COMPLETED:
- [x] StatusIndicatorService implemented with Qt signals
- [x] StatusIndicatorWidget implemented with smooth animations
- [x] Service instantiated in application.py
- [x] Widget added to main window status bar
- [x] Connection events wired (established/closed)
- [x] Workflow events wired (if signals exist in WorkflowView)
- [x] Motion tracking adapter created
- [x] Test script created
- [x] Documentation completed

### PENDING (Choose One Approach):
- [ ] Motion tracking integration - requires choosing approach:
  - **Option A**: Use PositionControllerMotionAdapter (recommended)
  - **Option B**: Modify PositionController to inherit QObject
  - **Option C**: Add signals to StageControlView
  - **Option D**: Use callback listener (most comprehensive)

## Quick Start - Testing the System

### 1. Test without GUI (Service Only)
```bash
cd /home/msnelson/LSControl/Flamingo_Control/src
python -c "from py2flamingo.test_status_indicator import test_service_only; test_service_only()"
```

### 2. Test with GUI (Full Test Window)
```bash
cd /home/msnelson/LSControl/Flamingo_Control/src
python -m py2flamingo.test_status_indicator
```

This opens a test window with:
- Both widget variants displayed
- Test buttons for each event
- Automatic test sequence
- Status change logging

### 3. Run with Full Application
```bash
cd /home/msnelson/LSControl/Flamingo_Control/src
python -m py2flamingo
```

Look for the status indicator in the bottom-right of the status bar.

## Integration Steps for Motion Tracking

### Recommended: Use Adapter (Non-invasive)

Add to `application.py` in `setup_dependencies()`:

```python
# After creating position_controller:
from py2flamingo.controllers.position_controller_adapter import (
    create_motion_tracking_adapter
)

# Create adapter with automatic connection to status indicator
self.position_motion_adapter = create_motion_tracking_adapter(
    self.position_controller,
    self.status_indicator_service
)

# Use adapter for stage control view
self.stage_control_view = StageControlView(
    controller=self.position_motion_adapter  # Use adapter instead
)
```

## Usage Examples

### Manual Status Updates
```python
from py2flamingo.services import StatusIndicatorService

# Create service
service = StatusIndicatorService()

# Simulate events
service.on_connection_established()  # Blue - Ready
service.on_motion_started()          # Red - Moving
service.on_motion_stopped()          # Blue - Ready
service.on_workflow_started()        # Magenta - Workflow Running
service.on_workflow_stopped()        # Blue - Ready
service.on_connection_closed()       # Grey - Disconnected

# Query current status
current = service.get_current_status()  # Returns GlobalStatus enum
is_busy = service.is_busy()  # Returns True if moving or running workflow
```

### Connect to Signals
```python
from py2flamingo.services import StatusIndicatorService, GlobalStatus

service = StatusIndicatorService()

def on_status_changed(status: GlobalStatus, description: str):
    print(f"Status: {status.value} - {description}")

service.status_changed.connect(on_status_changed)
```

## Architecture

```
┌─────────────────────────────────────────┐
│          FlamingoApplication            │
├─────────────────────────────────────────┤
│                                         │
│  ┌────────────────────────────────┐    │
│  │  StatusIndicatorService        │    │
│  │  - Tracks state flags          │    │
│  │  - Emits status_changed signal │    │
│  └────────┬──────────────────▲────┘    │
│           │                  │         │
│           │ signals          │ calls   │
│           │                  │         │
│           ▼                  │         │
│  ┌────────────────┐  ┌───────┴──────┐ │
│  │ StatusWidget   │  │ Event Sources│ │
│  │ - Displays     │  │ - Connection │ │
│  │   indicator    │  │ - Workflow   │ │
│  │ - Updates UI   │  │ - Motion     │ │
│  └────────────────┘  └──────────────┘ │
│                                         │
└─────────────────────────────────────────┘
```

### Event Flow

```
Connection Established
    └─> ConnectionView.connection_established (signal)
        └─> StatusIndicatorService.on_connection_established()
            └─> status_changed(IDLE, "Ready") (signal)
                └─> StatusIndicatorWidget.update_status()
                    └─> UI shows Blue "Ready"

Motion Started
    └─> PositionMotionAdapter.move_absolute()
        └─> motion_started (signal)
            └─> StatusIndicatorService.on_motion_started()
                └─> status_changed(MOVING, "Moving") (signal)
                    └─> StatusIndicatorWidget.update_status()
                        └─> UI shows Red "Moving"

Motion Stopped
    └─> Callback listener receives STAGE_MOTION_STOPPED (0x6010)
        └─> StatusIndicatorService.on_motion_stopped()
            └─> status_changed(IDLE, "Ready") (signal)
                └─> StatusIndicatorWidget.update_status()
                    └─> UI shows Blue "Ready"
```

## Status Priority Logic

The service implements priority-based status calculation:

```python
if not connected:
    return DISCONNECTED  # Always highest priority

if workflow_running:
    return WORKFLOW_RUNNING  # Overrides motion

if moving:
    return MOVING

return IDLE  # Default connected state
```

This means:
- Disconnected always overrides everything
- Workflow running overrides motion (you won't see "Moving" during workflow)
- Motion only shows when not in workflow
- Idle is the fallback connected state

## Widget Customization

### Change Colors
Edit `STATUS_COLORS` in `status_indicator_widget.py`:

```python
STATUS_COLORS = {
    GlobalStatus.DISCONNECTED: QColor(128, 128, 128),  # Grey
    GlobalStatus.IDLE: QColor(70, 130, 180),           # Steel Blue
    GlobalStatus.MOVING: QColor(255, 100, 0),          # Orange (example)
    GlobalStatus.WORKFLOW_RUNNING: QColor(200, 50, 200)  # Magenta
}
```

### Change Animation Duration
Edit `_animate_color_change()` in `status_indicator_widget.py`:

```python
self.color_animation.setDuration(500)  # 500ms instead of 300ms
```

### Use Bar Variant Instead
In `application.py`:

```python
from py2flamingo.views.widgets.status_indicator_widget import StatusIndicatorBar

self.status_indicator_widget = StatusIndicatorBar()  # Instead of StatusIndicatorWidget
```

## Troubleshooting

### Widget Not Visible
1. Check that widget is passed to MainWindow: `main_window.py` line ~302
2. Check status bar has widget: Look for `addPermanentWidget()` call
3. Look for Qt layout warnings in logs

### Status Not Updating
1. Check signal connections in logs: "Connected ... to status indicator service"
2. Verify signals exist: `hasattr(self.connection_view, 'connection_established')`
3. Look for exceptions in signal handlers

### Motion Not Tracked
1. Choose and implement a motion tracking approach (see MOTION_TRACKING_EXAMPLES.py)
2. Verify adapter is being used for moves
3. Check callback listener is active

### Colors Not Smooth
1. Ensure Qt event loop is running
2. Check PyQt5 version (need >= 5.6 for QPropertyAnimation)
3. Look for animation exceptions in logs

## Future Enhancements

Potential improvements:

1. **Pulsing animation** for MOVING and WORKFLOW_RUNNING
2. **Progress information** (e.g., "Workflow 45% complete")
3. **Detailed tooltip** with position, workflow details
4. **Click-to-expand** detailed status dialog
5. **Status history** in tooltip (last N changes)
6. **Audio notifications** for state changes
7. **Status logging** to file for debugging

## API Reference

### GlobalStatus Enum
- `DISCONNECTED`: Not connected to microscope
- `IDLE`: Connected and ready
- `MOVING`: Stage motion in progress
- `WORKFLOW_RUNNING`: Acquisition workflow active

### StatusIndicatorService Methods
- `on_connection_established()`: Notify connection established
- `on_connection_closed()`: Notify connection closed
- `on_motion_started()`: Notify motion started
- `on_motion_stopped()`: Notify motion stopped
- `on_workflow_started()`: Notify workflow started
- `on_workflow_stopped()`: Notify workflow stopped
- `get_current_status()`: Get current GlobalStatus
- `get_status_description()`: Get human-readable description
- `is_busy()`: Check if moving or running workflow

### StatusIndicatorWidget Methods
- `update_status(status, description)`: Update displayed status

## Support

For questions or issues:
1. Check `STATUS_INDICATOR_INTEGRATION.md` for detailed integration steps
2. Review `MOTION_TRACKING_EXAMPLES.py` for motion tracking approaches
3. Run `test_status_indicator.py` to verify installation
4. Check logs for error messages

## License

Part of the Flamingo microscope control software.
