# Workflow Consolidation and UI Redesign Report

## Summary

This update consolidates all workflow operations to use a single funnel point (`WorkflowTransmissionService`) and redesigns the workflow tab UI with simplified panels and advanced settings dialogs.

## Part 1: Workflow Tab UI Redesign

### Problem
The workflow tab had too many visible parameters (~60+ fields), making it overwhelming for users. Many settings were rarely changed.

### Solution
Implemented a 3-phase redesign based on microscopist/UI expert recommendations:

#### Phase 1: Advanced Dialogs
Created 3 new dialogs for rarely-changed settings:

| Dialog | Settings Moved |
|--------|----------------|
| `AdvancedIlluminationDialog` | Light path (L/R), Multi-laser mode, LED color/DAC |
| `AdvancedCameraDialog` | AOI presets/dimensions, Dual camera capture % and mode |
| `AdvancedSaveDialog` | Save drive, Region, Subfolders, Live view, Extended comments |

#### Phase 2: Panel Simplification
Rewrote main panels with compact layouts:

- **IlluminationPanel**: New compact `LaserRow` widgets (checkbox + slider + spinbox per channel)
- **CameraPanel**: Simplified to exposure + frame rate + AOI info display
- **SavePanel**: Essential fields only (Directory, Sample, Format, MIP options)

#### Phase 3: Visibility Matrix
Added workflow-type parameter visibility control:

- Stack option auto-sets based on workflow type (Snapshot=None, ZStack=ZStack, Tile=Tile, etc.)
- Rotational velocity hidden for non-OPT workflows
- Tile settings shown only for Tile workflows

### Files Changed (UI)
- `views/workflow_panels/illumination_panel.py` - Rewritten with LaserRow widgets
- `views/workflow_panels/camera_panel.py` - Simplified layout
- `views/workflow_panels/save_panel.py` - Essential fields only
- `views/workflow_panels/zstack_panel.py` - Added visibility control methods
- `views/workflow_view.py` - Added visibility matrix logic
- `views/dialogs/advanced_illumination_dialog.py` - NEW
- `views/dialogs/advanced_camera_dialog.py` - NEW
- `views/dialogs/advanced_save_dialog.py` - NEW
- `views/dialogs/__init__.py` - Updated exports

## Part 2: Workflow Execution Consolidation

### Problem
Multiple code paths existed for sending workflows to the microscope:
1. `WorkflowView` → `WorkflowController` → `MVCWorkflowService`
2. `MinimalGUI` → `TCPClient.send_workflow()`
3. `ConnectionService.send_workflow()`
4. `ConnectionManager.send_workflow()`
5. `WorkflowService.run_workflow()`

This made maintenance difficult and risked inconsistent behavior.

### Solution
Created `WorkflowTransmissionService` as the **single funnel point** for all workflow transmission.

### New Architecture

```
All Callers → WorkflowTransmissionService → WorkflowCommand → connection.send_command()
```

#### WorkflowTransmissionService API
- `execute_workflow_from_dict(workflow_dict)` - Execute from UI dictionary
- `execute_workflow_from_file(file_path)` - Execute from file path
- `execute_workflow_from_text(workflow_text)` - Execute from text content
- `execute_workflow(workflow)` - Execute from Workflow model
- `stop_workflow()` - Stop running workflow
- `is_executing` - Property to check execution status

### Files Changed (Consolidation)
- `services/workflow_transmission_service.py` - NEW single funnel service
- `services/__init__.py` - Added export
- `controllers/workflow_controller.py` - Updated to use transmission service
- `application.py` - Creates and injects transmission service
- `services/connection_service.py` - Removed legacy `send_workflow` method
- `services/connection_manager.py` - Removed legacy `send_workflow` method
- `services/workflow_service.py` - `run_workflow` now raises DeprecationWarning
- `controllers/multi_angle_collection.py` - Updated to use transmission service
- `workflows/__init__.py` - Updated migration documentation

### Migration Guide

**Before (DEPRECATED):**
```python
connection_service.send_workflow(data)    # REMOVED
connection_manager.send_workflow(data)    # REMOVED
workflow_service.run_workflow(wf, conn)   # DEPRECATED - raises error
```

**After:**
```python
from py2flamingo.services import WorkflowTransmissionService

transmission_service = WorkflowTransmissionService(connection_service)
success, msg = transmission_service.execute_workflow_from_dict(workflow_dict)
```

### Notes
- `TCPClient.send_workflow()` retained for standalone MinimalGUI tool
- TileCollectionDialog execution path now goes through WorkflowController which uses transmission service

## Testing

Verified core imports work correctly:
```
WorkflowTransmissionService imported successfully
WorkflowController imported successfully
All core imports successful!
```

## Files Summary

### New Files (4)
- `services/workflow_transmission_service.py`
- `views/dialogs/advanced_camera_dialog.py`
- `views/dialogs/advanced_illumination_dialog.py`
- `views/dialogs/advanced_save_dialog.py`

### Modified Files (14)
- `application.py`
- `controllers/multi_angle_collection.py`
- `controllers/workflow_controller.py`
- `services/__init__.py`
- `services/connection_manager.py`
- `services/connection_service.py`
- `services/workflow_service.py`
- `views/dialogs/__init__.py`
- `views/dialogs/tile_collection_dialog.py`
- `views/workflow_panels/camera_panel.py`
- `views/workflow_panels/illumination_panel.py`
- `views/workflow_panels/save_panel.py`
- `views/workflow_panels/zstack_panel.py`
- `views/workflow_view.py`
- `workflows/__init__.py`
