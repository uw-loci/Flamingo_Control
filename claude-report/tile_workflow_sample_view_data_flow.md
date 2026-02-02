# Claude Report: Tile Workflow → Sample View Data Flow Fix

**Date:** 2026-01-29

## Issue Summary

All 8 tile workflows executed successfully, but **zero frames reached Sample View**. The log showed this warning 8 times (once per workflow):
```
WARNING - Camera controller not available for tile position tracking
```

No "Activated tile mode", "Starting display timer", "Routed Z-plane", or "drain" messages appeared at all.

## Root Causes

### Bug 1 (Primary): `set_camera_controller()` never called

`WorkflowController.set_camera_controller()` existed at `workflow_controller.py:84` but was **never called anywhere in the codebase**. The `_camera_controller` field stayed `None`, so `set_active_tile_position()` logged the warning and returned without doing anything.

### Bug 2: Thread-safety — callback runs in background thread

`WorkflowQueueService._execute_queue()` runs in a `threading.Thread`. The `on_workflow_start` callback is called from this thread, which chains to `CameraController.set_active_tile_position()`, which calls `self._display_timer.start()`. **QTimer operations from a non-GUI thread are undefined behavior** — they silently fail or crash.

## Solution

### 1. Wire the camera controller (`application.py`)

Added one line after the existing wiring pattern:
```python
self.workflow_controller.set_camera_controller(self.camera_controller)
```

### 2. Thread-safe tile position setting (`workflow_controller.py`)

- Made `WorkflowController` inherit from `QObject`
- Added two `pyqtSignal`s: `_tile_position_requested(dict)` and `_tile_position_clear_requested()`
- `set_active_tile_position()` now emits the signal instead of calling the camera controller directly
- `_clear_tile_position()` now emits the clear signal instead of calling directly
- Added `@pyqtSlot` methods `_apply_tile_position()` and `_apply_clear_tile_position()` that run on the main thread via Qt's signal/slot mechanism

This ensures QTimer operations in `CameraController` always execute on the GUI thread.

## Files Modified

| File | Changes |
|------|---------|
| `application.py` | Added `workflow_controller.set_camera_controller(camera_controller)` wiring |
| `controllers/workflow_controller.py` | Added QObject inheritance, pyqtSignal/pyqtSlot for thread-safe tile position marshaling |

## Context

The previous commit (722020e) added the frame-draining and channel UX code for Sample View integration, but the data flow was broken because the camera controller was never wired to the workflow controller. This fix completes the wiring and adds thread-safety for the worker thread → GUI thread boundary.
