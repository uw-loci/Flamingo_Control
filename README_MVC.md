# Flamingo Microscope Control - MVC Interface

## Overview

The Flamingo Microscope Control MVC Interface is a modern, maintainable implementation of the control software for Flamingo light sheet microscope systems. This interface uses a clean Model-View-Controller (MVC) architecture that separates concerns and makes the codebase easier to understand, test, and extend.

**Key Features:**
- Clean MVC architecture with clear separation of concerns
- Comprehensive test coverage (400+ tests)
- Type-safe with full type hints throughout
- Robust error handling and validation
- Observable pattern for reactive UI updates
- Dependency injection for easy testing and extension

## Architecture

### Layer Overview

The MVC interface is organized into distinct layers, each with specific responsibilities:

```
┌─────────────────────────────────────────────────────┐
│                  Application Layer                  │
│  (Entry point, dependency injection, lifecycle)     │
└─────────────────────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
┌───────▼──────┐  ┌─────▼─────┐  ┌──────▼───────┐
│    Views     │  │Controllers│  │   Services   │
│   (PyQt5)    │  │(Orchestr.)│  │ (Bus. Logic) │
└──────────────┘  └───────────┘  └──────────────┘
                         │                │
                    ┌────▼────┐      ┌───▼────┐
                    │ Models  │      │  Core  │
                    │ (Data)  │      │ (TCP)  │
                    └─────────┘      └────────┘
                         │                │
                    ┌────▼────┐      ┌───▼────┐
                    │  Utils  │      │Protocol│
                    │(Parsers)│      │ (Enc.) │
                    └─────────┘      └────────┘
```

### Core Layer

**Location:** `src/py2flamingo/core/`

The foundation of the system - handles low-level TCP communication and protocol encoding.

**Components:**
- `tcp_protocol.py`: Binary protocol encoding/decoding (128-byte command format)
- `tcp_connection.py`: TCP socket management, dual-port connections (command + live)
- `CommandCode` enum: All microscope command constants

**Key Features:**
- Thread-safe operations
- Automatic dual-socket management (command port + live port)
- Clean resource management with proper cleanup
- No external dependencies beyond stdlib

**Example:**
```python
from py2flamingo.core import TCPConnection, ProtocolEncoder, CommandCode

# Create connection
connection = TCPConnection()
cmd_sock, live_sock = connection.connect("127.0.0.1", 53717)

# Encode command
encoder = ProtocolEncoder()
cmd_bytes = encoder.encode_command(CommandCode.CMD_WORKFLOW_START)

# Send command
connection.send_bytes(cmd_bytes, socket_type="command")

# Clean up
connection.disconnect()
```

### Models Layer

**Location:** `src/py2flamingo/models/`

Defines data structures and state management.

**Components:**
- `connection.py`: Connection configuration and state tracking
- `command.py`: Command data structures for different operation types

**Key Features:**
- Immutable configurations using frozen dataclasses
- Observable pattern for reactive UI updates
- Built-in validation with clear error messages
- Type-safe data structures

**Example:**
```python
from py2flamingo.models import ConnectionConfig, ConnectionModel, ConnectionState

# Create immutable configuration
config = ConnectionConfig("192.168.1.100", 53717)
valid, errors = config.validate()

# Observable model
model = ConnectionModel()

def on_change(status):
    print(f"Connection state: {status.state}")

model.add_observer(on_change)
```

### Utils Layer

**Location:** `src/py2flamingo/utils/`

Utility functions for file parsing and data conversion.

**Components:**
- `metadata_parser.py`: Parse FlamingoMetaData.txt configuration files
- `workflow_parser.py`: Parse and validate workflow definition files

**Key Features:**
- Reuses existing `file_handlers.py` utilities
- Returns typed data structures (ConnectionConfig)
- Comprehensive validation and error messages
- Safe handling of encoding issues

**Example:**
```python
from py2flamingo.utils import parse_metadata_file, parse_workflow_file

# Parse metadata
config = parse_metadata_file("microscope_settings/FlamingoMetaData.txt")
print(f"Connecting to {config.ip_address}:{config.port}")

# Parse workflow
workflow = parse_workflow_file("workflows/Snapshot.txt")
```

### Services Layer

**Location:** `src/py2flamingo/services/`

Business logic - connects models and core to provide high-level operations.

**Components:**
- `MVCConnectionService`: Connection lifecycle management
- `MVCWorkflowService`: Workflow execution and management
- `StatusService`: Microscope status queries with caching

**Key Features:**
- Dependency injection for testability
- Comprehensive error handling
- Observable state updates
- Logging throughout

**Example:**
```python
from py2flamingo.core import TCPConnection, ProtocolEncoder
from py2flamingo.services import MVCConnectionService, MVCWorkflowService
from py2flamingo.models import ConnectionConfig

# Create services
tcp = TCPConnection()
encoder = ProtocolEncoder()
conn_service = MVCConnectionService(tcp, encoder)
workflow_service = MVCWorkflowService(conn_service)

# Connect
config = ConnectionConfig("127.0.0.1", 53717)
success, message = conn_service.connect(config)

# Send workflow
success, message = workflow_service.load_workflow("workflows/Snapshot.txt")
success, message = workflow_service.start_workflow()
```

### Controllers Layer

**Location:** `src/py2flamingo/controllers/`

Orchestrates UI interactions with services - translates user actions into service calls.

**Components:**
- `ConnectionController`: Handles connect/disconnect/reconnect actions
- `WorkflowController`: Manages workflow loading, starting, and stopping

**Key Features:**
- Returns (success, message) tuples for UI feedback
- User-friendly error messages (no technical jargon)
- Input validation before calling services
- Coordinates multiple services when needed

**Example:**
```python
from py2flamingo.controllers import ConnectionController, WorkflowController

# Create controllers
conn_controller = ConnectionController(conn_service, model)
workflow_controller = WorkflowController(workflow_service, model)

# User actions
success, message = conn_controller.connect("127.0.0.1", 53717)
if success:
    print(f"Success: {message}")
else:
    print(f"Error: {message}")
```

### Views Layer

**Location:** `src/py2flamingo/views/`

PyQt5 UI components - displays state and captures user input.

**Components:**
- `ConnectionView`: Connection management UI
- `WorkflowView`: Workflow selection and execution UI

**Key Features:**
- Reusable PyQt5 widgets
- Signal/slot architecture for events
- Automatic button state management
- Color-coded status messages

**Example:**
```python
from py2flamingo.views import ConnectionView, WorkflowView

# Create views with controllers
connection_view = ConnectionView(conn_controller)
workflow_view = WorkflowView(workflow_controller)

# Views handle button clicks, display messages, update state automatically
```

### Application Layer

**Location:** `src/py2flamingo/`

Brings everything together - manages application lifecycle and dependency injection.

**Components:**
- `application.py`: FlamingoApplication class with dependency injection
- `main_window.py`: MainWindow that composes all views
- `cli.py`: Command-line argument parsing and validation
- `__main__.py`: Entry point for running the application

**Key Features:**
- Automatic dependency wiring
- Clean lifecycle management
- CLI argument support
- Proper resource cleanup

## Installation

### Prerequisites

- Python 3.8-3.11
- Virtual environment (recommended)
- Access to Flamingo microscope network (or use mock server for testing)

### Setup

```bash
# Clone repository
cd /home/msnelson/LSControl/Flamingo_Control

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt  # for testing

# Verify installation
python -m py2flamingo --help
```

## Usage

### Starting the Application

```bash
# Basic usage (default: 127.0.0.1:53717)
python -m py2flamingo

# Connect to specific IP and port
python -m py2flamingo --ip 192.168.1.100 --port 53717

# Auto-load workflow on startup
python -m py2flamingo --workflow workflows/Snapshot.txt

# Enable debug logging
python -m py2flamingo --log-level DEBUG
```

### Using the GUI

#### Connection Tab

1. **Enter IP Address**: Default is 127.0.0.1 (localhost)
2. **Enter Port**: Default is 53717 (command port)
3. **Click Connect**: Establishes dual-socket connection
4. **Status Updates**: Green = connected, Red = error, Gray = disconnected

#### Workflow Tab

1. **Browse for Workflow**: Click "Browse" to select .txt workflow file
2. **Load Workflow**: Workflow is validated and loaded
3. **Start Workflow**: Click "Start" to send workflow to microscope
4. **Stop Workflow**: Click "Stop" to halt execution
5. **Status Updates**: Shows workflow progress and results

### Programmatic Usage

You can also use the MVC components directly in your own code:

```python
from py2flamingo.core import TCPConnection, ProtocolEncoder
from py2flamingo.services import MVCConnectionService, MVCWorkflowService
from py2flamingo.controllers import ConnectionController, WorkflowController
from py2flamingo.models import ConnectionConfig, ConnectionModel

# Create full stack
tcp = TCPConnection()
encoder = ProtocolEncoder()
model = ConnectionModel()

conn_service = MVCConnectionService(tcp, encoder, model)
workflow_service = MVCWorkflowService(conn_service)

conn_controller = ConnectionController(conn_service, model)
workflow_controller = WorkflowController(workflow_service, model)

# Connect and run workflow
success, msg = conn_controller.connect("127.0.0.1", 53717)
if success:
    workflow_controller.load_workflow("workflows/Snapshot.txt")
    workflow_controller.start_workflow()
    # ... wait for completion ...
    workflow_controller.stop_workflow()
    conn_controller.disconnect()
```

## Testing

The MVC interface has comprehensive test coverage.

### Running Tests

```bash
# All tests (unit + integration)
python -m pytest tests/ -v

# Unit tests only
python -m pytest tests/ --ignore=tests/integration -v

# Integration tests (requires mock server)
python mock_server.py &  # Start mock server first
python -m pytest tests/integration/ -v

# With coverage report
python -m pytest tests/ --cov=src/py2flamingo --cov-report=html
open htmlcov/index.html
```

### Test Structure

- **Unit Tests** (`tests/`): 356 tests covering all layers individually
  - `test_tcp_protocol.py`: Protocol encoding/decoding (35 tests)
  - `test_tcp_connection.py`: Socket management (33 tests)
  - `test_models.py`: Data structures (51 tests)
  - `test_utils_parsers.py`: File parsing (34 tests)
  - `test_services.py`: Business logic (40 tests)
  - `test_controllers.py`: Orchestration (41 tests)
  - `test_views.py`: UI components (45 tests)
  - `test_application.py`: Application lifecycle (41 tests)

- **Integration Tests** (`tests/integration/`): 50 tests
  - `test_integration_e2e.py`: End-to-end workflows (23 tests)
  - `test_integration_ui.py`: UI integration (27 tests)

**Total: 406 tests, all passing**

### Mock Server

For testing without hardware, use the included mock server:

```bash
# Start mock server
python mock_server.py

# Or specify custom port
python mock_server.py --port 12345

# Server simulates all microscope responses
```

## Troubleshooting

### Connection Issues

**Problem:** "Connection refused" error

**Solutions:**
- Verify microscope is powered on and connected to network
- Check IP address is correct (see FlamingoMetaData.txt)
- Ensure firewall allows connections on ports 53717 and 53718
- Test with mock server first: `python mock_server.py`

### Workflow Issues

**Problem:** "Workflow not found" error

**Solutions:**
- Verify workflow file path is correct
- Check file has .txt extension
- Ensure file is valid UTF-8 encoding
- Use absolute path or path relative to execution directory

**Problem:** "Workflow failed to start"

**Solutions:**
- Ensure connected to microscope first
- Verify workflow file format is correct
- Check log output for detailed error messages: `--log-level DEBUG`
- Try with sample workflow: `workflows/Snapshot.txt`

### UI Issues

**Problem:** Buttons are disabled/greyed out

**Solutions:**
- Check connection status - must be connected for workflow operations
- Ensure workflow is loaded before starting
- Wait for current operation to complete before starting new one

**Problem:** Application crashes on startup

**Solutions:**
- Verify PyQt5 is installed: `pip install PyQt5`
- Check Python version is 3.8-3.11
- Run with debug logging: `python -m py2flamingo --log-level DEBUG`

### Import Errors

**Problem:** `ModuleNotFoundError: No module named 'py2flamingo'`

**Solutions:**
- Ensure you're in project root directory
- Set PYTHONPATH: `export PYTHONPATH=src`
- Or run as module: `python -m py2flamingo` instead of `python src/py2flamingo/...`

## Comparison with Minimal Interface

The MVC interface is a complete reimplementation focusing on architecture and maintainability.

### Key Differences

| Feature | MVC Interface | Minimal Interface |
|---------|---------------|-------------------|
| Architecture | Clean MVC layers | Mixed concerns |
| Testing | 406 comprehensive tests | Basic smoke tests |
| Type Safety | Full type hints | Partial hints |
| Error Handling | Comprehensive with user messages | Basic try/catch |
| Extensibility | Dependency injection | Hard-coded dependencies |
| Documentation | Complete inline docs | Minimal comments |

### When to Use Each

**Use MVC Interface when:**
- Developing new features
- Need comprehensive testing
- Want clear architecture
- Building on existing code

**Use Minimal Interface when:**
- Need quick demo/prototype
- Familiar with existing codebase
- Legacy integrations required

Both interfaces can coexist - they share utilities and core components.

## Development Guide

### Adding New Features

1. **Add Models** (if needed): Define data structures in `models/`
2. **Add Service Methods**: Implement business logic in `services/`
3. **Add Controller Actions**: Coordinate services in `controllers/`
4. **Add View Components**: Create UI in `views/`
5. **Wire in Application**: Update dependency injection in `application.py`
6. **Add Tests**: Unit tests for each layer, integration test for full flow

### Code Style

- **Type hints**: All functions have complete type annotations
- **Docstrings**: Google-style docstrings for all public APIs
- **Error handling**: Specific exceptions with clear messages
- **Logging**: Use `logging.getLogger(__name__)` in all modules
- **Testing**: Maintain >80% code coverage

### Architecture Principles

1. **Separation of Concerns**: Each layer has one responsibility
2. **Dependency Injection**: Pass dependencies in constructors
3. **Observable Pattern**: Models notify observers of changes
4. **Return Tuples**: Controllers return `(success, message)` for UI feedback
5. **No Mocking in Production**: Use real objects, mock only in tests

## API Reference

See inline documentation in each module:

```python
# View docstrings
from py2flamingo.services import MVCConnectionService
help(MVCConnectionService.connect)

# View all methods
import py2flamingo.controllers
help(py2flamingo.controllers.ConnectionController)
```

## Contributing

### Before Contributing

1. Read this documentation
2. Review architecture in `CLAUDE.md`
3. Run existing tests to understand patterns
4. Follow code style guidelines

### Making Changes

1. Create feature branch
2. Write tests first (TDD)
3. Implement feature
4. Ensure all tests pass
5. Update documentation
6. Submit pull request

## License

See LICENSE file in repository root.

## Contact

For questions or issues:
- Check CLAUDE.md for architecture details
- Review existing tests for examples
- Check troubleshooting section above

## Version History

### v2.0.0 (Current) - MVC Refactoring
- Complete MVC architecture implementation
- 406 comprehensive tests
- Full type safety
- Improved error handling
- Clean dependency injection

### v1.x - Original Implementation
- Mixed architecture
- Basic functionality
- Legacy codebase

---

**Last Updated:** 2025-10-10
**Documentation Version:** 1.0
