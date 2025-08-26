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
- ✅ Fast execution - typical run time < 5 seconds
- ✅ Comprehensive coverage - tests core functionality
- ✅ CI/CD ready - can run in automated pipelines

## Test Structure

### Directory Layout
```
tests/
├── __init__.py                     # Makes tests a Python package
├── run_tests.py                    # Main test runner
├── test_tcp_communication.py       # Low-level TCP/IP tests
├── test_queue_event_management.py  # Threading and sync tests
├── test_connection_service.py      # Integration tests
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
python run_tests.py tcp_communication
python run_tests.py queue_event_management
python run_tests.py connection_service
```

### Using unittest directly
```bash
# From project root
python -m unittest discover -s tests -p 'test_*.py' -v
```

### Using pytest (if installed)
```bash
# From project root
pytest tests/ -v

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

### 1. test_tcp_communication.py

Tests the low-level TCP/IP communication layer.

**What it tests:**
- Socket connection establishment to NUC and live ports
- Binary command structure formatting
- Workflow file transmission
- Connection error handling
- Proper resource cleanup

**Key test cases:**
```python
test_successful_connection()      # Verifies dual socket connection
test_connection_refused()         # Handles connection errors
test_connection_timeout()         # Handles network timeouts
test_send_command_structure()     # Verifies binary protocol
test_send_workflow()             # Tests workflow file sending
```

**Example command structure verification:**
```python
# The test verifies this binary structure:
# [Start marker][Command][Status]...[Data][End marker]
# 0xF321E654    24580    0      ... 10.5  0xFEDC4321
```

### 2. test_queue_event_management.py

Tests the threading and synchronization infrastructure.

**What it tests:**
- Queue creation and management
- Event signaling between threads
- Thread-safe operations
- Legacy global object compatibility
- Memory cleanup

**Key test cases:**
```python
test_queue_creation()           # All queues are created
test_thread_safety()            # Concurrent access works
test_event_synchronization()    # Thread coordination
test_legacy_adapter()           # Backward compatibility
test_singleton_behavior()       # Global objects are singletons
```

**Important queues tested:**
- `image_queue`: Camera image data
- `command_queue`: Microscope commands
- `visualize_queue`: Preview images
- `stage_location_queue`: Position updates

### 3. test_connection_service.py

Integration tests for the complete connection flow.

**What it tests:**
- Service initialization
- Complete connection sequence
- Thread management
- Command flow through system
- Settings retrieval
- Error recovery

**Key test cases:**
```python
test_successful_connection_flow()    # Full connection setup
test_send_command()                  # Command queuing
test_send_workflow()                 # Workflow execution
test_get_microscope_settings()       # Settings retrieval
test_stage_movement_sequence()       # Position control
```

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
    # See test_tcp_communication.py for full example
```

## Continuous Integration

The tests are designed for CI/CD pipelines:

### GitHub Actions Example
```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v2
    
    - name: Set up Python
      uses: actions/setup-python@v2
      with:
        python-version: '3.9'
    
    - name: Install dependencies
      run: |
        pip install -r requirements.txt
        pip install -r requirements-dev.txt
    
    - name: Run tests
      run: |
        cd tests
        python run_tests.py
    
    - name: Generate coverage report
      run: |
        coverage run -m unittest discover -s tests
        coverage xml
    
    - name: Upload coverage
      uses: codecov/codecov-action@v1
```

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
# Right: @patch('src.py2flamingo.services.communication.tcpip_client.socket.socket')
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