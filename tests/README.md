"""
# Py2Flamingo Test Suite

This directory contains unit tests for the Py2Flamingo microscope control software.

## Running Tests

### Run all tests:
```bash
cd tests
python run_tests.py
```

### Run specific test module:
```bash
python run_tests.py tcp_communication
python run_tests.py queue_event_management
python run_tests.py connection_service
```

### Run with unittest directly:
```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

## Test Structure

### test_tcp_communication.py
Tests the low-level TCP/IP communication with the microscope:
- Socket connection establishment
- Command structure formatting
- Workflow file sending
- Error handling and cleanup

### test_queue_event_management.py
Tests the threading and synchronization infrastructure:
- Queue creation and management
- Event signaling and waiting
- Thread-safe operations
- Legacy adapter compatibility

### test_connection_service.py
Integration tests for the complete connection flow:
- Service initialization
- Thread management
- Command sending sequences
- Microscope settings retrieval

## Mock Microscope Server

For testing without a real microscope, use the mock server:

```python
# tests/mock_microscope_server.py
import socket
import struct
import threading
import time

class MockMicroscopeServer:
    def __init__(self, host='127.0.0.1', port=53717):
        self.host = host
        self.port = port
        self.running = False
        
    def start(self):
        self.running = True
        self.nuc_thread = threading.Thread(target=self._nuc_handler)
        self.live_thread = threading.Thread(target=self._live_handler)
        
        self.nuc_thread.start()
        self.live_thread.start()
        
    def _nuc_handler(self):
        '''Handle command connections'''
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind((self.host, self.port))
        server.listen(1)
        
        while self.running:
            client, addr = server.accept()
            # Handle commands
            while self.running:
                try:
                    data = client.recv(1024)
                    if not data:
                        break
                    # Echo back or process command
                    client.send(b'OK')
                except:
                    break
            client.close()
                    
    def _live_handler(self):
        '''Handle live data connections'''
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind((self.host, self.port + 1))
        server.listen(1)
        
        while self.running:
            client, addr = server.accept()
            # Send mock image data periodically
            while self.running:
                try:
                    # Send mock 512x512 uint16 image
                    mock_image = b'\\x00' * (512 * 512 * 2)
                    client.send(mock_image)
                    time.sleep(0.1)
                except:
                    break
            client.close()
    
    def stop(self):
        self.running = False

# Usage:
if __name__ == '__main__':
    server = MockMicroscopeServer()
    server.start()
    print("Mock microscope server running on 127.0.0.1:53717")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
```

## Writing New Tests

When adding new functionality, follow these patterns:

1. **Unit tests** for individual components:
   ```python
   def test_specific_functionality(self):
       # Arrange
       component = MyComponent()
       
       # Act
       result = component.do_something()
       
       # Assert
       self.assertEqual(result, expected)
   ```

2. **Integration tests** for component interactions:
   ```python
   def test_component_integration(self):
       # Create components
       service_a = ServiceA()
       service_b = ServiceB()
       
       # Test interaction
       service_a.send_to_b(data)
       result = service_b.receive()
       
       # Verify
       self.assertEqual(result, expected)
   ```

3. **Mock external dependencies**:
   ```python
   @patch('socket.socket')
   def test_with_mock_socket(self, mock_socket):
       # Configure mock
       mock_socket.return_value.recv.return_value = b'response'
       
       # Test
       result = my_function()
       
       # Verify
       mock_socket.return_value.send.assert_called_with(expected_data)
   ```

## Test Coverage

To check test coverage:
```bash
pip install coverage
coverage run -m unittest discover -s tests
coverage report
coverage html  # Creates htmlcov/index.html
```

## Continuous Integration

These tests are designed to run in CI environments without requiring
actual microscope hardware. All external dependencies are mocked.
"""