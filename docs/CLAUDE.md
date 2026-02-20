# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Py2Flamingo is control software for Flamingo light sheet microscope systems (Huisken Lab). It communicates with the microscope's ControlSystem over TCP/IP, manages acquisition workflows, and displays images via either a standalone PyQt5 GUI or as a Napari dock widget.

**Key Constraints:**
- Requires Flamingo firmware v2.16.2 on the instrument side
- Python 3.8-3.11 only
- Network access to microscope (Morgridge network or VPN for production systems)
- Must have `microscope_settings/FlamingoMetaData.txt` and `workflows/Zstack.txt` on disk

## Running the Application

From the repository root:

```bash
# Activate virtual environment first (recommended)
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Standalone mode (PyQt GUI only)
cd Flamingo_Control/src
python -m py2flamingo --mode standalone

# Napari mode (embeds GUI as dock widget)
cd Flamingo_Control/src
python -m py2flamingo --mode napari
```

## Testing

All tests are designed to run without hardware via mocking:

```bash
# From tests directory
cd Flamingo_Control/tests
python run_tests.py

# Run specific test module
python run_tests.py tcp_communication
python run_tests.py queue_event_management
python run_tests.py connection_service

# Using unittest directly from project root
python -m unittest discover -s tests -p 'test_*.py' -v

# Using pytest with coverage
pytest tests/ --cov=src/py2flamingo --cov-report=html
```

**Test categories:**
- `test_tcp_communication.py` - Low-level socket/protocol tests
- `test_queue_event_management.py` - Threading and synchronization
- `test_connection_service.py` - Integration tests
- `mock_microscope_server.py` - Manual testing mock server

Install test dependencies: `pip install -r requirements-dev.txt`

## Claude Reports

Reports are stored **outside** this git repository at `/home/msnelson/LSControl/claude-reports/` (one level above `Flamingo_Control/`). This directory is private and must NEVER be deleted, moved, or placed inside any git repository.

- Location: `/home/msnelson/LSControl/claude-reports/<descriptive_name>.md`
- Naming: use lowercase with underscores (e.g., `main_thread_starvation_fix.md`)
- One report per file, one topic per report
- **Do NOT** create `claude-report.md`, `claude-report/`, or `claude-reports/` inside this repository
- **Do NOT** delete or modify existing files in the claude-reports directory without explicit user request
- Existing reports in that directory serve as examples of the expected format

## Architecture

### Modular Design (Post-Refactor)

The codebase underwent a major refactor (2025-08) to separate concerns while maintaining backward compatibility:

**Core Directories:**
- `src/py2flamingo/controllers/` - User-facing actions (snapshot, locate sample, multi-angle acquisition)
- `src/py2flamingo/services/` - Reusable logic (TCP connection, workflow assembly, configuration)
- `src/py2flamingo/models/` - Data structures (Position, Workflow, Microscope, Settings)
- `src/py2flamingo/core/` - Application glue (queue/event managers, legacy adapter)
- `src/py2flamingo/views/` - Display abstraction (viewer interface, Napari adapter, widgets)

**Entry Points:**
- `src/py2flamingo/__main__.py` - CLI entry point, handles `--mode` flag
- `src/py2flamingo/GUI.py` - Main control panel (`Py2FlamingoGUI` class)
- `src/py2flamingo/napari.py` - Napari integration (`NapariFlamingoGui` dockable widget)

### Queue and Event System

All inter-thread communication uses centralized queues/events managed via:
- `core/queue_manager.py` - Creates/manages 8 standard queues (image, command, visualize, etc.)
- `core/events.py` - Creates/manages 6 events (system_idle, terminate, etc.)
- `core/legacy_adapter.py` - Provides backward-compatible global object exports

**Critical:** Legacy code and external integrations import from `py2flamingo.global_objects`, which now proxies to `legacy_adapter.py`. Never create duplicate Queue/Event instances—always import from the adapter.

**Standard Queues:**
- `image_queue` - Camera image data (from microscope)
- `visualize_queue` - Images for display (processed)
- `command_queue` - Commands to send to microscope
- `command_data_queue` - Command parameters
- `stage_location_queue` - Position updates
- `z_plane_queue` - Z-plane processing data
- `intensity_queue` - Intensity calculations
- `other_data_queue` - Miscellaneous microscope data

**Standard Events:**
- `system_idle` - System is ready for new commands (set when idle)
- `terminate_event` - Stop all threads/acquisitions
- `send_event` - Signal to send queued command
- `visualize_event` - Signal to update display
- `processing_event` - Processing task in progress
- `view_snapshot` - Display snapshot result

### Communication Flow

1. **Connection Setup** (`FlamingoConnect.py`, `services/connection_manager.py`):
   - Dual TCP sockets: command port (e.g., 53717) + live imaging port (53718)
   - Four threads: command sender, command listener, image data listener, image processor
   - **Protocol:** 128-byte binary structure (little-endian, see `core/tcp_protocol.py`)
     - Start marker (0xF321E654), command code, status, 7 params, double value, 72-byte data buffer, end marker (0xFEDC4321)
     - Critical: params[6] must be 0x80000000 (TRIGGER_CALL_BACK) for query commands

2. **Stage Position System**:
   - Hardware position queries supported via `STAGE_POSITION_GET` (0x6008)
   - Single-axis movements query only moved axis (fast feedback)
   - Multi-axis movements (home, presets) query all 4 axes (complete verification)
   - Position verified from hardware after motion-stopped callback
   - See `services/stage_service.py` and `controllers/position_controller.py`

3. **Workflow Execution**:
   - Controllers create `WorkflowModel` via `services/workflow_service.py`
   - Workflow converted to text format and sent via `send_workflow()` command
   - If firmware doesn't support workflows, send stepwise commands instead

4. **Image Display**:
   - Images arrive on `image_queue` or `visualize_queue`
   - `views/widgets/viewer_widget.py` polls both queues
   - Forwards to active viewer (PyQt label or Napari layer)

5. **Cancel/Terminate**:
   - User clicks Cancel → sets `terminate_event`
   - All long-running operations must check `terminate_event.is_set()`
   - Cleanup via `clear_all_events_queues()` from legacy adapter

### Viewer Abstraction

Display is decoupled via `views/viewer_interface.py`:
- `ViewerInterface` - Abstract base class
- `NapariViewer` - Napari implementation (uses layers)
- `NDVViewer` - Standalone PyQt implementation (uses QLabel)

To add a new viewer, implement `ViewerInterface` and update `__main__.py`.

### LED 2D Overview Feature

Extension for quick sample orientation scanning. Creates 2D overview maps at dual rotation angles (R and R+90).

**Key Files:**
- `views/dialogs/led_2d_overview_dialog.py` - Configuration dialog (60KB)
- `workflows/led_2d_overview_workflow.py` - Scan execution engine (52KB)
- `views/dialogs/led_2d_overview_result.py` - Results display window (68KB)
- `views/dialogs/tile_collection_dialog.py` - Workflow generation from selected tiles (73KB)
- `views/dialogs/overview_thresholder_dialog.py` - Auto-selection tool (20KB)

**Architecture:** Clean extension with minimal integration:
- Menu entry in `main_window.py` (lines 267-276)
- Application state signals for acquisition locking
- Service dependencies via injection (PositionPresetService, CameraService, StageService)
- No direct database modifications, global state pollution, or monkey-patching
- Could be extracted to separate package with ~90% import path changes only

**Data Flow:**
```
LED2DOverviewDialog → LED2DOverviewWorkflow → LED2DOverviewResultWindow → TileCollectionDialog
```

See [LED 2D Overview Guide](led_2d_overview.md) for complete documentation.

## Important Configuration Files

**Required for operation:**
- `microscope_settings/FlamingoMetaData.txt` - IP/port config (generated by instrument during workflow)
- `workflows/Zstack.txt` - Default workflow template (seeds GUI settings)
- `microscope_settings/[microscope]_start_position.txt` - Sample holder tip position (created after first "Find Sample")

**Reference files:**
- `src/py2flamingo/functions/command_list.txt` - Numerical command codes for microscope protocol
- `src/py2flamingo/functions/WORKFLOW_SETTINGS_OPTIONS.txt` - Workflow parameter reference
- `microscope_settings/ScopeSettings.txt` - System configuration (pixel size, etc.)

## Common Development Patterns

### Creating a New Dialog

**All dialogs must remember their window position between sessions.** Use `PersistentDialog` (not `QDialog`) or `PersistentWidget` (not `QWidget`) from `py2flamingo.services.window_geometry_manager`:

```python
from py2flamingo.services.window_geometry_manager import PersistentDialog

class MyNewDialog(PersistentDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        # ... build UI — geometry persistence is automatic
```

- `PersistentDialog` replaces `QDialog`; `PersistentWidget` replaces `QWidget` for top-level windows
- Position/size is saved on hide/close and restored on first show — no manual code needed
- The default geometry manager is set once at startup (`application.py`), so no constructor changes are required
- The window ID defaults to the class name; override with `window_id="CustomName"` if needed
- If a subclass overrides `showEvent`/`hideEvent`/`closeEvent`, it **must** call `super()` to preserve persistence

### Adding a New Controller Action

```python
# In controllers/my_new_action.py
from ..services.connection_service import ConnectionService
from ..core.legacy_adapter import terminate_event, image_queue
from ..models.microscope import Position

def perform_action(connection: ConnectionService, position: Position):
    """Perform new microscope action."""
    # Always check terminate event in loops
    while some_condition and not terminate_event.is_set():
        # Do work
        pass

    # Put results on appropriate queue
    image_queue.put(result_image)
```

### Creating a Workflow

```python
from py2flamingo.services.workflow_service import WorkflowService
from py2flamingo.models.workflow import WorkflowModel, IlluminationSettings
from py2flamingo.models.microscope import Position

service = WorkflowService()
workflow = WorkflowModel(
    start_position=Position(x=10, y=20, z=5, r=0),
    illumination=IlluminationSettings(laser_channel="Laser 3 488 nm", laser_power=5.0)
)

# Convert to dict for microscope
workflow_dict = service.create_snapshot_workflow(workflow)
```

### Testing With Mocks

```python
import unittest
from unittest.mock import Mock, patch

class TestMyFeature(unittest.TestCase):
    @patch('socket.socket')
    def test_connection(self, mock_socket):
        # Configure mock
        mock_socket.return_value.recv.return_value = b'OK'

        # Test code that uses socket
        result = my_function()

        # Verify
        self.assertEqual(result, expected_value)
```

## Known Issues and Workarounds

**Cancel leaves UI busy:**
- Ensure background threads check `terminate_event` frequently
- Call `clear_all_events_queues()` after cancel completes
- Verify `system_idle` event is set when operation finishes

**Napari mode shows no images:**
- Confirm Napari installed: `pip install napari`
- Check `ViewerWidget` is polling both `image_queue` and `visualize_queue`
- Verify `NapariViewer` is receiving `update_image()` calls

**Workflow not executing:**
- Some firmware versions don't accept bundled workflow files
- Fallback: send stepwise commands via `send_command()` instead of `send_workflow()`

## Code Organization Notes

- **Legacy global objects**: Now managed via `core/legacy_adapter.py` but maintain same interface
- **Threading**: All long-running operations must be cancellable via `terminate_event`
- **GUI in Napari mode**: Hides the standalone GUI's image label since Napari canvas is used for display
- **Models are immutable-ish**: Use dataclasses; create new instances rather than mutating
- **Logging**: Use `logging.getLogger(__name__)` in all modules

## External Dependencies

**Runtime:**
- PyQt5 (GUI framework)
- NumPy (image data)
- Napari (optional, for viewer mode)

**Development:**
- pytest, pytest-cov (testing)
- black, flake8, mypy (code quality)
- See `requirements-dev.txt` for full list
