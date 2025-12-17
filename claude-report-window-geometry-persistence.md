# Claude Report: Window Geometry Persistence

**Date:** 2025-12-17
**Status:** Complete

---

## Summary

Implemented window geometry and layout state persistence so that windows remember their positions, sizes, and splitter configurations between application sessions.

---

## Approach

### Storage Method: Hybrid JSON + Qt Geometry

Using a combination of:
1. **Qt's `saveGeometry()`/`restoreGeometry()`** - Converts window state to `QByteArray`, handles platform-specific quirks (maximized state, multi-monitor setups)
2. **JSON file storage** - Consistent with existing codebase patterns (`saved_configurations.json`, `microscope_settings/*.json`)

The `QByteArray` from Qt is base64-encoded for JSON storage.

### File Location

```
Flamingo_Control/window_geometry.json
```

### JSON Structure

```json
{
  "version": "1.0",
  "windows": {
    "MainWindow": {
      "geometry": "<base64-encoded QByteArray>",
      "state": "<base64-encoded QByteArray for QMainWindow>",
      "splitters": {}
    },
    "CameraLiveViewer": {
      "geometry": "<base64>",
      "splitters": {}
    },
    "Sample3DVisualizationWindow": {
      "geometry": "<base64>",
      "splitters": {
        "main_splitter": [270, 930]
      }
    }
  }
}
```

---

## Windows Implemented

| Window | Class | Has Splitter | Status |
|--------|-------|--------------|--------|
| Main Window | `MainWindow` | No | Implemented |
| Camera Live Viewer | `CameraLiveViewer` | No | Implemented |
| Image Controls | `ImageControlsWindow` | No | Implemented |
| Stage Chamber Viz | `StageChamberVisualizationWindow` | No | Implemented |
| 3D Sample Viz | `Sample3DVisualizationWindow` | Yes | Implemented (with splitter) |
| Sample View | `SampleView` | No | Implemented |
| LED 2D Overview Result | `LED2DOverviewResultWindow` | Yes | Not implemented (dynamically created) |
| Position History | `PositionHistoryDialog` | No | Not implemented (modal dialog) |
| LED 2D Overview | `LED2DOverviewDialog` | No | Not implemented (non-modal) |

---

## Implementation Details

### Files Created

1. **`src/py2flamingo/services/window_geometry_manager.py`**
   - `WindowGeometryManager` class - handles JSON storage and Qt geometry serialization
   - `GeometryPersistenceMixin` class - optional mixin for windows (not used, direct implementation preferred)

### Files Modified

1. **`src/py2flamingo/services/__init__.py`**
   - Added exports for `WindowGeometryManager` and `GeometryPersistenceMixin`

2. **`src/py2flamingo/application.py`**
   - Added `geometry_manager` attribute
   - Creates `WindowGeometryManager` instance in `setup_dependencies()`
   - Passes `geometry_manager` to all windows
   - Calls `geometry_manager.save_all()` in `shutdown()`

3. **`src/py2flamingo/main_window.py`**
   - Added `geometry_manager` parameter to `__init__`
   - Added `showEvent()` to restore geometry on first show
   - Updated `closeEvent()` to save geometry before closing

4. **`src/py2flamingo/views/camera_live_viewer.py`**
   - Added `geometry_manager` parameter to `__init__`
   - Updated `showEvent()` to restore geometry on first show
   - Updated `hideEvent()` to save geometry when hiding

5. **`src/py2flamingo/views/image_controls_window.py`**
   - Added `geometry_manager` parameter to `__init__`
   - Updated `showEvent()` to restore geometry
   - Updated `hideEvent()` to save geometry

6. **`src/py2flamingo/views/stage_chamber_visualization_window.py`**
   - Added `geometry_manager` parameter to `__init__`
   - Updated `showEvent()` to restore geometry
   - Updated `hideEvent()` to save geometry
   - Updated `closeEvent()` to save geometry

7. **`src/py2flamingo/views/sample_3d_visualization_window.py`**
   - Added `geometry_manager` parameter to `__init__`
   - Changed `splitter` local variable to `self.main_splitter` for access
   - Added `showEvent()` to restore geometry AND splitter state
   - Updated `closeEvent()` to save geometry AND splitter state

8. **`src/py2flamingo/views/sample_view.py`**
   - Added `geometry_manager` parameter to `__init__`
   - Added `showEvent()` to restore geometry
   - Added `closeEvent()` to save geometry

---

## Implementation Patterns

### For Windows that Hide Instead of Close (floating windows)
- Save geometry in `hideEvent()` (called when window is hidden)
- Restore geometry in `showEvent()` on first show only
- This ensures geometry is saved when user clicks X (which just hides the window)

### For Windows that Actually Close
- Save geometry in `closeEvent()`
- Restore geometry in `showEvent()` on first show only

### For Windows with Splitters
- Store splitter as instance variable (e.g., `self.main_splitter`)
- Save splitter state alongside geometry
- Restore splitter state after restoring geometry

---

## Testing Results

All imports verified working:
- `WindowGeometryManager` - OK
- `MainWindow` - OK
- `CameraLiveViewer` - OK
- `ImageControlsWindow` - OK
- `StageChamberVisualizationWindow` - OK
- `Sample3DVisualizationWindow` - OK
- `SampleView` - OK
- `FlamingoApplication` - OK

---

## Future Enhancements

The following windows could be enhanced with geometry persistence in the future:

1. **LED2DOverviewResultWindow** - Would require passing geometry_manager through LED2DOverviewDialog
2. **PositionHistoryDialog** - Modal dialog, lower priority
3. **LED2DOverviewDialog** - Non-modal dialog, moderate priority

---

## Usage

The system is automatic. Users simply:
1. Open windows and position/resize them as desired
2. Close the application
3. On next launch, windows restore to their previous positions

The `window_geometry.json` file is created automatically on first run and updated whenever windows are closed/hidden.

---

## References

- [PyQt5 Window Geometry Tutorial](https://www.pythonguis.com/tutorials/restore-window-geometry-pyqt5/)
- [Qt 5.15 Restoring Geometry Documentation](https://doc.qt.io/qt-5/restoring-geometry.html)
- Existing pattern: `ConfigurationManager` in `services/configuration_manager.py`
