# Testing Guide for Py2Flamingo

This guide explains how to test the Py2Flamingo microscope control software, including unit tests, integration tests, and testing strategies.

## Table of Contents
- [Overview](#overview)
- [Test Structure](#test-structure)
- [Running Tests](#running-tests)
- [Test Modules](#test-modules)
- [Mock Server](#mock-server)
- [Writing New Tests](#writing-new-tests)
- [Continuous Integration](#continuous-integration)
- [Troubleshooting](#troubleshooting)

## Overview

The Py2Flamingo test suite ensures that:
- TCP/IP communication with the microscope works correctly
- Command formatting follows the Flamingo protocol
- Thread synchronization and queuing systems function properly
- The refactored architecture maintains backward compatibility
- All components work together in integration

**Key Features:**
- ✅ No hardware required - all external dependencies are mocked
- ✅ Run time: about 5 minutes for the whole suite (pytest)
- ✅ Comprehensive coverage - tests core functionality
- ✅ CI/CD ready - can run in automated pipelines

## Test Structure

### Directory Layout
```
tests/
├── __init__.py                     # Makes tests a Python package
├── run_tests.py                    # Main test runner
├── test_*.py                       # About 2,500 tests (pytest, what CI runs);
│                                   # about 1,000 are unittest.TestCase classes,
│                                   # the only ones run_tests.py finds
├── test_tcp_protocol.py            # 128-byte protocol encode/decode
├── test_queue_event_management.py  # QueueManager / EventManager
├── test_verification.py            # Post-update checks against the synthetic microscope
├── mock_microscope_server.py       # Mock server for manual testing
└── README.md                       # Quick reference
```

### Test Categories

1. **Unit Tests**: Test individual components in isolation
2. **Integration Tests**: Test component interactions
3. **Protocol Tests**: Verify binary command structure
4. **Thread Safety Tests**: Ensure concurrent operations work correctly

## Running Tests

### Prerequisites

First, install development dependencies:
```bash
pip install -r requirements-dev.txt
```

Or manually:
```bash
pip install pytest coverage unittest-xml-reporting
```

### Basic Test Execution

```bash
# From project root
cd tests

# Run all tests
python run_tests.py

# Run specific test module
python run_tests.py tcp_protocol
python run_tests.py queue_event_management
```

### Using unittest directly
```bash
# From project root
python -m unittest discover -s tests -p 'test_*.py' -v
```

### Using pytest (the whole suite; what CI runs)
```bash
# From project root: the command .github/workflows/tests.yml runs
QT_QPA_PLATFORM=offscreen python -m pytest tests -q --ignore=tests/integration -p no:cacheprovider

# With coverage
pytest tests/ --cov=src/py2flamingo --cov-report=html
```

### Test Coverage
```bash
# Run tests with coverage
coverage run -m unittest discover -s tests
coverage report
coverage html  # Opens htmlcov/index.html
```

## Test Modules

### 1. test_tcp_protocol.py

The 128-byte command structure (`core/tcp_protocol.py`): encoding, decoding, markers and
field offsets. End-to-end protocol behavior is tested against
`py2flamingo.testing.synthetic_microscope` (for example `test_verification.py`,
`test_light_off_is_honest.py`).

### 2. test_queue_event_management.py

Tests the threading and synchronization infrastructure.

**What it tests:**
- Queue creation and management
- Event signaling between threads
- Thread-safe operations
- Memory cleanup

**Key test cases:**
```python
test_queue_creation()           # All queues are created
test_thread_safety()            # Concurrent access works
test_event_synchronization()    # Thread coordination
```

**Important queues tested:**
- `image_queue`: Camera image data
- `command_queue`: Microscope commands
- `visualize_queue`: Preview images
- `stage_location_queue`: Position updates

### 3. test_connection_service.py

Queue/event hand-off between threads and reading a settings text file with
`text_to_dict`. `MVCConnectionService` itself is tested in `test_services.py` and the
synthetic-microscope tests.

## Mock Server

For manual testing without hardware, use the included mock server:

```bash
# In one terminal
python tests/mock_microscope_server.py

# In another terminal
python -m py2flamingo --mode standalone
```

The mock server:
- Listens on ports 53717 (commands) and 53718 (live data)
- Accepts connections like a real microscope
- Sends mock image data
- Responds to commands

### Customizing the Mock Server

```python
# Create custom responses
class CustomMockServer(MockMicroscopeServer):
    def handle_command(self, command_data):
        # Parse command
        cmd = struct.unpack('I', command_data[4:8])[0]
        
        if cmd == 24580:  # STAGE_POSITION_SET
            # Simulate stage movement
            time.sleep(0.5)
            return b'OK'
        
        return b'UNKNOWN'
```

## Writing New Tests

### Test Template

```python
import unittest
from unittest.mock import Mock, patch

class TestNewFeature(unittest.TestCase):
    """Test the new feature."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Create objects needed for tests
        self.component = MyComponent()
    
    def tearDown(self):
        """Clean up after tests."""
        # Clean up resources
        self.component.cleanup()
    
    def test_normal_operation(self):
        """Test normal operation of feature."""
        # Arrange
        input_data = create_test_data()
        
        # Act
        result = self.component.process(input_data)
        
        # Assert
        self.assertEqual(result.status, 'success')
        self.assertIsNotNone(result.data)
    
    @patch('external.dependency')
    def test_with_mock(self, mock_dep):
        """Test with mocked external dependency."""
        # Configure mock
        mock_dep.return_value = 'mocked_result'
        
        # Test
        result = self.component.use_dependency()
        
        # Verify
        mock_dep.assert_called_once()
        self.assertEqual(result, 'processed_mocked_result')
```

### Best Practices

1. **Test One Thing**: Each test should verify one specific behavior
2. **Use Descriptive Names**: `test_connection_timeout_raises_exception`
3. **Follow AAA Pattern**: Arrange, Act, Assert
4. **Mock External Dependencies**: Don't rely on network, files, or hardware
5. **Test Edge Cases**: Empty data, None values, exceptions
6. **Keep Tests Fast**: Mock slow operations

### Testing Microscope Commands

When testing new microscope commands:

```python
def test_new_microscope_command(self):
    """Test sending new command to microscope."""
    # Create command structure
    command_code = 12345  # Your command code
    command_data = [1, 2, 3, 4.5]  # Your data
    
    # Send through service
    self.service.send_command(command_code, command_data)
    
    # Verify binary structure
    sent_data = self.mock_socket.send.call_args[0][0]
    
    # Unpack and verify
    # See test_tcp_protocol.py for the field layout
```

## Continuous Integration

`.github/workflows/tests.yml` runs on every push and pull request to `main`,
on Ubuntu with Python 3.11 and `QT_QPA_PLATFORM=offscreen`. It installs
`requirements.txt`, `requirements-dev.txt` and the package, then runs the whole
suite with pytest:

```bash
python -m pytest tests -q --ignore=tests/integration -p no:cacheprovider
```

`tests/integration` is left out: its tests start `tests/mock_server.py` from a
hard-coded developer path and use fixtures they do not define. Until
2026-11-22 CI ran `python tests/run_tests.py`, which finds only the
`unittest.TestCase` classes (about 1,000 of 2,500 tests); every test it ran is
also collected by the pytest command. `run_tests.py` still works for local use,
and module-level state is reset under both runners (`tests/conftest.py` and
`run_tests.py`'s `_reset_module_state`).

## Troubleshooting

### Common Issues

**Import Errors**
```bash
# If you see: ModuleNotFoundError: No module named 'src'
# Make sure you're in the tests directory:
cd tests
python run_tests.py
```

**Path Issues**
```python
# The test runner adds src to path automatically
# If running tests differently, add:
import sys
sys.path.insert(0, '../src')
```

**Mock Not Working**
```python
# Ensure you're patching the right path:
# Wrong: @patch('socket.socket')
# Right: patch where it is looked up, e.g. @patch('py2flamingo.core.tcp_connection.socket.socket')
```

**Tests Hanging**
- Check for infinite loops in threaded code
- Ensure all threads have timeout conditions
- Add timeout to test methods:
```python
@timeout(5)  # Fails if test takes > 5 seconds
def test_something(self):
    pass
```

### Debugging Tests

```python
# Add logging to tests
import logging
logging.basicConfig(level=logging.DEBUG)

# Use pdb for debugging
import pdb; pdb.set_trace()

# Print mock calls
print(mock_object.mock_calls)
```

## Test Maintenance

### When to Update Tests

1. **Adding new features**: Write tests first (TDD)
2. **Fixing bugs**: Add test that reproduces the bug
3. **Refactoring**: Ensure tests still pass
4. **Protocol changes**: Update command structure tests

### Test Review Checklist

- [ ] Tests pass locally
- [ ] No hardcoded paths or IPs
- [ ] Mocks are properly configured
- [ ] No real network calls
- [ ] Tests run in < 10 seconds
- [ ] Clear test names and documentation
- [ ] Edge cases covered

## Summary

The test suite provides confidence that:
- The refactored code maintains compatibility with the Flamingo protocol
- Thread synchronization works correctly
- The system can handle errors gracefully
- New changes don't break existing functionality

Regular testing during development helps catch issues early and ensures the microscope control software remains reliable.