---
paths:
  - "src/py2flamingo/views/**"
  - "src/py2flamingo/controllers/**"
---

# UI guidelines

Loads when you work on views or controllers.

## UI Development Guidelines

### Window Geometry Persistence

New windows and dialogs remember their position between sessions: subclass `PersistentDialog` (instead of `QDialog`) or `PersistentWidget` (instead of `QWidget`) from `py2flamingo.services.window_geometry_manager`. Geometry is saved on hide/close and restored on first show with no constructor changes; a subclass that overrides `showEvent`/`hideEvent`/`closeEvent` calls `super()` so persistence keeps working. The window ID defaults to the class name (`window_id="CustomName"` overrides it).

Geometry is stored in `window_geometry.json` (auto-created, git-ignored). Some older windows (for example `views/camera_live_viewer.py` and `views/image_controls_window.py`) still take a `geometry_manager` argument and call `save_geometry()`/`restore_geometry()` themselves; do not copy that pattern into new code.

## Acquisition Lock System

### Overview

The application provides an **acquisition lock** mechanism to prevent accidental interference with scanning operations. When an acquisition is in progress (e.g., LED 2D Overview scan), microscope controls are automatically disabled while visualization controls remain enabled.

**Important Terminology:**
- **"Acquisition"** = Client-side scanning processes (LED 2D Overview, tile collection, etc.)
- **"Workflow"** = Server-side Flamingo Workflow feature (reserved term - server handles its own locking)

### Using the Acquisition Lock

#### For New Scanning Functions

Any new scanning/acquisition process should use the lock to prevent interference:

```python
def start_scan(self):
    """Start a scanning operation."""
    # Lock microscope controls at the start
    if self._app:
        self._app.start_acquisition("My Scan Name")

    try:
        # ... perform scanning operations ...
        self._do_scan()
    finally:
        # ALWAYS unlock when done (success, error, or cancel)
        if self._app:
            self._app.stop_acquisition("My Scan Name")

def cancel_scan(self):
    """Cancel the scan."""
    self._cancelled = True
    # Unlock will happen in the scan completion handler
```

#### Key Methods (FlamingoApplication)

```python
# Check if acquisition is running
if app.is_acquisition_in_progress:
    # Don't start another acquisition
    return

# Start acquisition (returns False if already in progress)
success = app.start_acquisition("LED 2D Overview")

# Stop acquisition (safe to call even if not in progress)
app.stop_acquisition("LED 2D Overview")
```

#### Signals

```python
# Connect to acquisition state changes
app.acquisition_started.connect(self._on_acquisition_started)
app.acquisition_stopped.connect(self._on_acquisition_stopped)

def _on_acquisition_started(self):
    self.my_stage_slider.setEnabled(False)

def _on_acquisition_stopped(self):
    self.my_stage_slider.setEnabled(True)
```

### Currently Connected Views

The following views automatically disable controls during acquisition:

| View | Controls Disabled |
|------|-------------------|
| **Sample View** | Position sliders (X,Y,Z,R), position edits, illumination panel |
| **Stage Control View** | All movement controls (go-to buttons, jog buttons, spinboxes) |
| **Stage Chamber Visualization** | Position sliders |

### Adding Acquisition Lock to New Views

For any new view with stage movement controls:

1. **Add a disable method to your view:**
```python
def set_stage_controls_enabled(self, enabled: bool) -> None:
    """Enable/disable stage controls during acquisition."""
    self.x_slider.setEnabled(enabled)
    self.y_slider.setEnabled(enabled)
    # ... other movement controls
    # Keep visualization controls enabled!
```

2. **Connect signals in FlamingoApplication.setup_dependencies():**
```python
# In setup_dependencies():
if self.my_new_view:
    self.acquisition_started.connect(
        lambda: self.my_new_view.set_stage_controls_enabled(False)
    )
    self.acquisition_stopped.connect(
        lambda: self.my_new_view.set_stage_controls_enabled(True)
    )
```

Or for views created dynamically (like Sample View):
```python
# When creating the view:
self.acquisition_started.connect(
    lambda: self.my_view.set_stage_controls_enabled(False)
)
self.acquisition_stopped.connect(
    lambda: self.my_view.set_stage_controls_enabled(True)
)
```

### Controls That Should Remain Enabled

During acquisition, these should **stay enabled** so users can monitor progress:
- Napari 3D viewer and visualization
- Image display and contrast adjustment
- MIP visualization controls
- Progress bars and status displays
- Cancel buttons

### Implementation Files

- **State management:** `src/py2flamingo/application.py` (FlamingoApplication class)
- **Example usage:** `src/py2flamingo/workflows/led_2d_overview_workflow.py`
- **View integration:** `src/py2flamingo/views/sample_view.py` (set_stage_controls_enabled)
