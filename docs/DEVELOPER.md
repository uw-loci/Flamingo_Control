# Developer Guide

Guide for developers and contributors to the Flamingo Microscope Control software.

---

## Table of Contents

- [Development Setup](#development-setup)
- [Architecture Overview](#architecture-overview)
- [Testing](#testing)
- [Contributing](#contributing)
- [Code Style](#code-style)

---

## Development Setup

### Prerequisites

- Python 3.8-3.11
- Git
- Virtual environment tool
- Code editor (VS Code, PyCharm, etc.)

### Initial Setup

```bash
# Clone repository
git clone https://github.com/uw-loci/Flamingo_Control.git
cd Flamingo_Control

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements-minimal.txt

# Install development dependencies
pip install -r requirements-dev.txt

# Or install manually:
pip install PyQt5 numpy pytest pytest-cov black flake8 mypy
```

### Running the Application

```bash
# Set Python path
export PYTHONPATH=src  # Windows: $env:PYTHONPATH="src"

# Run MVC interface
python -m py2flamingo

# Run with specific microscope
python -m py2flamingo --ip 10.129.37.22 --port 53717

# Enable debug logging
python -m py2flamingo --log-level DEBUG

# Legacy standalone mode
python -m py2flamingo --mode standalone

# Napari integration (requires napari installed)
python -m py2flamingo --mode napari
```

---

## Architecture Overview

### MVC Pattern

The codebase follows Model-View-Controller architecture:

```
src/py2flamingo/
├── models/          # Data structures (ConnectionModel, WorkflowModel, etc.)
├── views/           # UI components (ConnectionView, WorkflowView, etc.)
├── controllers/     # Business logic (ConnectionController, WorkflowController, etc.)
├── services/        # Reusable services (ConfigurationManager, TCP connection, etc.)
├── core/            # Core infrastructure (queues, events, protocol)
└── utils/           # Utilities (parsers, formatters, validators)
```

### Key Components

#### Models (`models/`)
Data structures with minimal logic:
- `ConnectionModel` - Connection state
- `WorkflowModel` - Workflow configuration
- `Position` - 3D/4D position data
- `Command` - Microscope commands

#### Views (`views/`)
UI components (PyQt5):
- `ConnectionView` - Connection configuration UI
- `WorkflowView` - Workflow management UI
- `ViewerWidget` - Image display
- Viewer abstractions (Napari, standalone)

#### Controllers (`controllers/`)
Business logic that coordinates models and services:
- `ConnectionController` - Connection management, testing
- `WorkflowController` - Workflow loading, execution
- `PositionController` - Stage movement
- `ImageController` - Image acquisition

#### Services (`services/`)
Reusable business logic:
- `ConfigurationManager` - Discover/load microscope configs
- `MVCConnectionService` - TCP connection management
- `MVCWorkflowService` - Workflow processing
- `StatusService` - Status monitoring

#### Core (`core/`)
Infrastructure and communication:
- `TCPConnection` - Low-level TCP socket management
- `ProtocolEncoder` - Binary protocol encoding
- `QueueManager` - Inter-thread queue management
- `EventManager` - Event signaling

### Communication Flow

```
┌──────────────┐
│     User     │
└───────┬──────┘
        │ Interaction
        v
┌──────────────┐
│     View     │ (PyQt5 UI)
└───────┬──────┘
        │ User action
        v
┌──────────────┐
│  Controller  │ (Business logic)
└───────┬──────┘
        │ Update/query
        v
┌──────────────┐     ┌──────────────┐
│    Model     │────→│   Service    │ (Connection, workflow)
└──────────────┘     └───────┬──────┘
                             │ TCP/IP
                             v
                     ┌──────────────┐
                     │  Microscope  │
                     └──────────────┘
```

### Queue and Event System

All inter-thread communication uses centralized queues/events:

**Standard Queues:**
- `image_queue` - Camera image data
- `visualize_queue` - Processed images for display
- `command_queue` - Commands to send
- `command_data_queue` - Command parameters
- `stage_location_queue` - Position updates

**Standard Events:**
- `system_idle` - System ready for commands
- `terminate_event` - Stop all operations
- `send_event` - Signal command ready to send
- `visualize_event` - Signal update display
- `processing_event` - Processing in progress

**Access via legacy adapter:**
```python
from py2flamingo.core.legacy_adapter import (
    image_queue,
    terminate_event,
    system_idle
)
```

**Never create duplicate Queue/Event instances!**

---

## Testing

### Test Structure

```
tests/
├── test_models.py              # Model tests
├── test_views.py               # View tests
├── test_controllers.py         # Controller tests
├── test_services.py            # Service tests
├── test_tcp_communication.py   # TCP/protocol tests
├── test_utils.py               # Test utilities
└── integration/                # Integration tests
```

### Running Tests

```bash
# All unit tests
PYTHONPATH=src pytest tests/ --ignore=tests/integration -v

# Specific test file
PYTHONPATH=src pytest tests/test_services.py -v

# Specific test class
PYTHONPATH=src pytest tests/test_services.py::TestConfigurationManager -v

# Specific test
PYTHONPATH=src pytest tests/test_services.py::TestConfigurationManager::test_discover_configurations -v

# With coverage
PYTHONPATH=src pytest tests/ --cov=src/py2flamingo --cov-report=html

# Integration tests (optional)
PYTHONPATH=src pytest tests/integration/ -v
```

### Testing Without Hardware

Use the mock server for testing without a microscope:

#### Step 1: Start Mock Server

```bash
# Terminal 1
source .venv/bin/activate
python mock_server.py
```

Expected output:
```
Mock Flamingo server started on 127.0.0.1:53717
Command port: 53717, Live port: 53718
Press Ctrl+C to stop
```

#### Step 2: Create Test Configuration

```bash
# Create localhost configuration
cat > microscope_settings/FlamingoMetaData_test.txt << 'EOF'
<MetaData>
  <Experiment>
    Name = Test
  </Experiment>
</MetaData>
<Instrument>
  <Type>
    Microscope name = Mock Server
    Microscope address = 127.0.0.1 53717
  </Type>
</Instrument>
EOF
```

#### Step 3: Run Application

```bash
# Terminal 2
source .venv/bin/activate
export PYTHONPATH=src
python -m py2flamingo
```

#### Step 4: Test Connection

1. Select "Mock Server" from configuration dropdown
2. Click "Test Connection" → Should succeed
3. Click "Connect" → Status shows "Connected"
4. Send commands/workflows → Check mock server terminal for received data

### Writing Tests

#### Test Template

```python
import unittest
from unittest.mock import Mock, patch

class TestMyFeature(unittest.TestCase):
    """Test MyFeature class."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_dependency = Mock()
        self.feature = MyFeature(self.mock_dependency)

    def test_basic_functionality(self):
        """Test basic functionality."""
        # Arrange
        self.mock_dependency.do_something.return_value = "result"

        # Act
        result = self.feature.execute()

        # Assert
        self.assertEqual(result, "expected")
        self.mock_dependency.do_something.assert_called_once()

    def test_error_handling(self):
        """Test error handling."""
        self.mock_dependency.do_something.side_effect = ValueError("error")

        with self.assertRaises(ValueError):
            self.feature.execute()
```

#### Testing Best Practices

1. **Use Mocks** - Don't require hardware
2. **Test Behavior** - Not implementation details
3. **Clear Names** - Test names should describe what they test
4. **Arrange-Act-Assert** - Structure tests clearly
5. **One Assertion Per Test** - Keep tests focused
6. **Cleanup** - Use setUp/tearDown for fixtures

---

## Contributing

### Branching Strategy

```bash
# Create feature branch
git checkout -b feature/my-feature

# Make changes and commit
git add .
git commit -m "Add my feature"

# Push to your fork
git push origin feature/my-feature

# Open pull request on GitHub
```

### Commit Messages

Follow conventional commits:

```
feat: Add configuration management system
fix: Correct connection timeout handling
docs: Update installation guide
test: Add tests for ConfigurationManager
refactor: Simplify TCP connection logic
```

### Pull Request Process

1. **Fork** repository
2. **Create branch** for your feature
3. **Write tests** for new functionality
4. **Update documentation** if needed
5. **Ensure tests pass** (`pytest tests/`)
6. **Submit pull request** with clear description

### Code Review Checklist

- [ ] Tests pass
- [ ] New features have tests
- [ ] Documentation updated
- [ ] Code follows style guide
- [ ] No breaking changes (or documented)
- [ ] Commit messages clear

---

## Code Style

### Python Style

Follow PEP 8 with these guidelines:

```python
# Good: Clear, typed, documented
def test_connection(
    self,
    ip: str,
    port: int,
    timeout: float = 2.0
) -> Tuple[bool, str]:
    """Test connection to microscope.

    Args:
        ip: IP address to test
        port: Port number
        timeout: Timeout in seconds

    Returns:
        Tuple of (success, message)
    """
    # Implementation here
    pass


# Bad: No types, no docstring
def test_connection(self, ip, port, timeout=2.0):
    # What does this do?
    pass
```

### Type Hints

Use type hints for all public functions:

```python
from typing import List, Optional, Tuple, Dict

def discover_configurations(self) -> List[MicroscopeConfiguration]:
    """Discover microscope configurations."""
    pass

def get_configuration(self, name: str) -> Optional[MicroscopeConfiguration]:
    """Get configuration by name."""
    pass
```

### Docstrings

Use Google-style docstrings:

```python
def connect(self, config: ConnectionConfig) -> None:
    """Connect to microscope using configuration.

    Args:
        config: Connection configuration containing IP and port

    Raises:
        ConnectionError: If connection fails
        ValueError: If configuration is invalid
        TimeoutError: If connection times out

    Example:
        >>> config = ConnectionConfig("10.129.37.22", 53717)
        >>> controller.connect(config)
    """
    pass
```

### Code Organization

```python
# Standard library imports
import os
import sys
from pathlib import Path
from typing import List, Optional

# Third-party imports
import numpy as np
from PyQt5.QtWidgets import QWidget

# Local imports
from ..models.connection import ConnectionConfig
from ..services.connection_service import MVCConnectionService
from ..core.tcp_connection import TCPConnection
```

### Naming Conventions

```python
# Classes: PascalCase
class ConnectionController:
    pass

# Functions/methods: snake_case
def test_connection(self, ip: str) -> bool:
    pass

# Constants: UPPER_SNAKE_CASE
DEFAULT_TIMEOUT = 2.0
MAX_RETRIES = 3

# Private: leading underscore
def _validate_ip(self, ip: str) -> bool:
    pass

# Internal: double underscore (name mangling)
def __internal_method(self) -> None:
    pass
```

### Error Handling

```python
# Good: Specific exceptions with context
def connect(self, config: ConnectionConfig) -> None:
    """Connect to microscope."""
    if not self._validate_config(config):
        raise ValueError(f"Invalid configuration: {config}")

    try:
        self._tcp.connect(config.ip_address, config.port)
    except socket.timeout:
        raise TimeoutError(
            f"Connection timeout to {config.ip_address}:{config.port}"
        )
    except socket.error as e:
        raise ConnectionError(
            f"Failed to connect: {e}"
        )


# Bad: Generic exceptions
def connect(self, config):
    try:
        self._tcp.connect(config.ip, config.port)
    except:  # Too broad!
        raise Exception("Connection failed")  # No context!
```

### Logging

```python
import logging

logger = logging.getLogger(__name__)

class MyClass:
    def __init__(self):
        self._logger = logging.getLogger(__name__)

    def do_something(self):
        self._logger.debug("Starting operation")
        try:
            result = self._operation()
            self._logger.info("Operation completed successfully")
            return result
        except Exception as e:
            self._logger.error(f"Operation failed: {e}")
            raise
```

### Testing Style

```python
class TestConnectionController(unittest.TestCase):
    """Test ConnectionController class."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_service = Mock()
        self.controller = ConnectionController(self.mock_service)

    def test_connect_success(self):
        """Test successful connection."""
        # Arrange
        self.mock_service.connect.return_value = None

        # Act
        success, message = self.controller.connect("127.0.0.1", 53717)

        # Assert
        self.assertTrue(success)
        self.assertIn("success", message.lower())
        self.mock_service.connect.assert_called_once()
```

---

## Common Development Tasks

### Adding a New Feature

1. **Plan** - Design the feature (models, views, controllers)
2. **Test First** - Write failing tests
3. **Implement** - Make tests pass
4. **Document** - Update docs
5. **PR** - Submit pull request

### Adding a New Controller

```python
# controllers/my_controller.py
from typing import Tuple
import logging

from ..services.my_service import MyService
from ..models.my_model import MyModel

class MyController:
    """Controller for my feature."""

    def __init__(self, service: MyService, model: MyModel):
        """Initialize controller.

        Args:
            service: Service instance
            model: Model instance
        """
        self._service = service
        self._model = model
        self._logger = logging.getLogger(__name__)

    def do_action(self, param: str) -> Tuple[bool, str]:
        """Perform action.

        Args:
            param: Action parameter

        Returns:
            Tuple of (success, message)
        """
        self._logger.info(f"Starting action with param: {param}")

        try:
            result = self._service.execute(param)
            self._model.update_state(result)
            return (True, "Action completed successfully")
        except Exception as e:
            self._logger.error(f"Action failed: {e}")
            return (False, f"Action failed: {e}")
```

### Adding a New Test

```python
# tests/test_my_controller.py
import unittest
from unittest.mock import Mock

from py2flamingo.controllers.my_controller import MyController

class TestMyController(unittest.TestCase):
    """Test MyController class."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_service = Mock()
        self.mock_model = Mock()
        self.controller = MyController(self.mock_service, self.mock_model)

    def test_do_action_success(self):
        """Test successful action."""
        self.mock_service.execute.return_value = "result"

        success, message = self.controller.do_action("test")

        self.assertTrue(success)
        self.assertIn("success", message.lower())
        self.mock_service.execute.assert_called_once_with("test")
```

---

## Debugging

### Enable Debug Logging

```bash
# Run with debug logging
python -m py2flamingo --log-level DEBUG

# Or set in code
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Common Issues

**Import Errors:**
```bash
# Ensure PYTHONPATH is set
export PYTHONPATH=src
```

**Qt Issues:**
```bash
# Install Qt dependencies (Linux)
sudo apt-get install libxcb-xinerama0 libxcb-cursor0

# Check Qt platform
export QT_DEBUG_PLUGINS=1
python -m py2flamingo
```

**Connection Issues:**
```bash
# Test network configuration
./check_network.sh  # Linux/macOS
.\check_network.ps1  # Windows

# Test connection programmatically
python -c "from py2flamingo.controllers import ConnectionController; ..."
```

---

## Resources

- **GitHub Repository:** https://github.com/uw-loci/Flamingo_Control
- **Issue Tracker:** https://github.com/uw-loci/Flamingo_Control/issues
- **PyQt5 Documentation:** https://doc.qt.io/qtforpython/
- **Python Testing:** https://docs.pytest.org/

---

**Last Updated:** 2025-10-14
**Maintainers:** UW-LOCI Team
