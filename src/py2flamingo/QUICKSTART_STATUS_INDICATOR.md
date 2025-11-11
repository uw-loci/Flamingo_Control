# Status Indicator Quick Start Guide

## What You Get

A color-coded status indicator in the bottom-right of your Flamingo GUI that shows:
- **Grey**: Disconnected
- **Blue**: Ready (connected and idle)
- **Red**: Moving (stage motion)
- **Magenta**: Workflow Running

## 1. Verify It's Installed

Check that these files exist:
```bash
cd /home/msnelson/LSControl/Flamingo_Control/src/py2flamingo

# Core files
ls -l services/status_indicator_service.py
ls -l views/widgets/status_indicator_widget.py
ls -l controllers/position_controller_adapter.py
```

All should exist (✓ = already created).

## 2. Run the Application

```bash
cd /home/msnelson/LSControl/Flamingo_Control/src
python3 -m py2flamingo
```

Look for the status indicator in the **bottom-right of the status bar**.

## 3. Test Connection Status

1. **Disconnected (Grey)**: You should see this when the app starts
2. **Connect**: Click connect → should change to **Blue** "Ready"
3. **Disconnect**: Click disconnect → should change back to **Grey**

Color transitions are smooth (300ms animation).

## 4. Add Motion Tracking (OPTIONAL)

The system is fully functional without motion tracking, but to see the **Red "Moving"** status, add motion tracking.

### Quick Option: Use Adapter (Recommended)

Add to `application.py` in the `setup_dependencies()` method, after creating `position_controller`:

```python
# Import adapter
from py2flamingo.controllers.position_controller_adapter import (
    create_motion_tracking_adapter
)

# Create adapter with automatic connection
self.position_motion_adapter = create_motion_tracking_adapter(
    self.position_controller,
    self.status_indicator_service
)

# Use adapter for stage control (if you want motion tracking)
# Change this line (around line 196):
self.stage_control_view = StageControlView(
    controller=self.position_motion_adapter  # ← Use adapter instead of position_controller
)
```

That's it! Now when you move the stage through the UI, the status will show **Red "Moving"**.

## 5. Test Workflow Status (If Signals Exist)

If your `workflow_view` has `workflow_started` and `workflow_stopped` signals:
1. Start a workflow → should change to **Magenta** "Workflow Running"
2. Stop workflow → should change back to **Blue** "Ready"

If workflow status doesn't update, the signals may need to be added to `WorkflowView`.

## 6. Customize (Optional)

### Change Colors
Edit `views/widgets/status_indicator_widget.py`:

```python
STATUS_COLORS = {
    GlobalStatus.DISCONNECTED: QColor(128, 128, 128),  # Grey
    GlobalStatus.IDLE: QColor(70, 130, 180),           # Blue
    GlobalStatus.MOVING: QColor(255, 0, 0),            # Bright red (change this)
    GlobalStatus.WORKFLOW_RUNNING: QColor(200, 50, 200)  # Magenta
}
```

### Use Bar Variant
Edit `application.py` in `create_main_window()`:

```python
# Change this line (around line 286):
from py2flamingo.views.widgets.status_indicator_widget import StatusIndicatorBar

self.status_indicator_widget = StatusIndicatorBar()  # ← Use bar instead
```

## Troubleshooting

### Can't see the indicator
- Check status bar at bottom-right of window
- Look for `[■] Disconnected` text
- If missing, check logs for errors

### Status doesn't update
- Connection status: Check that connection_view emits signals
- Workflow status: Check that workflow_view has signals
- Motion status: Implement motion tracking (step 4)

### Want more details?
Read the full documentation:
- `STATUS_INDICATOR_README.md` - Complete overview
- `STATUS_INDICATOR_INTEGRATION.md` - Detailed integration
- `MOTION_TRACKING_EXAMPLES.py` - Motion tracking code examples

## Test Without Full App

Run the test script:
```bash
cd /home/msnelson/LSControl/Flamingo_Control/src
python3 -m py2flamingo.test_status_indicator
```

This opens a test window where you can click buttons to test each status.

## That's It!

The status indicator is ready to use. It will automatically update based on connection and workflow events. Add motion tracking (step 4) if you want to see the "Moving" status.

**Questions?** Check `STATUS_INDICATOR_README.md` for comprehensive documentation.
