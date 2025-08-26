# setup_tests.py
"""
Script to set up the test directory structure for Py2Flamingo.

Run this from the project root to create the test structure.
"""
import os
from pathlib import Path


def setup_test_structure():
    """Create the test directory structure."""
    
    # Get project root (where this script is run from)
    project_root = Path.cwd()
    
    # Create tests directory
    tests_dir = project_root / 'tests'
    tests_dir.mkdir(exist_ok=True)
    
    # Create __init__.py
    init_file = tests_dir / '__init__.py'
    if not init_file.exists():
        init_file.write_text('# Test package initialization\n')
        print(f"Created {init_file}")
    
    # Create mock server if it doesn't exist
    mock_server_file = tests_dir / 'mock_microscope_server.py'
    if not mock_server_file.exists():
        mock_server_content = '''# Mock microscope server for testing
import socket
import struct
import threading
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MockMicroscopeServer:
    """Mock Flamingo microscope server for testing."""
    
    def __init__(self, host='127.0.0.1', port=53717):
        self.host = host
        self.port = port
        self.running = False
        self.nuc_server = None
        self.live_server = None
        
    def start(self):
        """Start the mock server."""
        self.running = True
        
        # Start NUC command server
        self.nuc_thread = threading.Thread(target=self._run_nuc_server)
        self.nuc_thread.daemon = True
        self.nuc_thread.start()
        
        # Start live data server
        self.live_thread = threading.Thread(target=self._run_live_server)
        self.live_thread.daemon = True
        self.live_thread.start()
        
        logger.info(f"Mock microscope server started on {self.host}:{self.port}")
        
    def _run_nuc_server(self):
        """Run the NUC command server."""
        self.nuc_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.nuc_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.nuc_server.bind((self.host, self.port))
        self.nuc_server.listen(1)
        
        while self.running:
            try:
                client, addr = self.nuc_server.accept()
                logger.info(f"NUC connection from {addr}")
                
                while self.running:
                    data = client.recv(1024)
                    if not data:
                        break
                    
                    # Simple echo for testing
                    logger.debug(f"Received {len(data)} bytes")
                    
            except Exception as e:
                if self.running:
                    logger.error(f"NUC server error: {e}")
                    
    def _run_live_server(self):
        """Run the live data server."""
        self.live_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.live_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.live_server.bind((self.host, self.port + 1))
        self.live_server.listen(1)
        
        while self.running:
            try:
                client, addr = self.live_server.accept()
                logger.info(f"Live connection from {addr}")
                
                # Send mock image data periodically
                while self.running:
                    # Mock 512x512 uint16 image
                    mock_image = bytes(512 * 512 * 2)
                    client.send(mock_image)
                    time.sleep(0.1)
                    
            except Exception as e:
                if self.running:
                    logger.error(f"Live server error: {e}")
    
    def stop(self):
        """Stop the mock server."""
        self.running = False
        if self.nuc_server:
            self.nuc_server.close()
        if self.live_server:
            self.live_server.close()
        logger.info("Mock server stopped")

if __name__ == '__main__':
    server = MockMicroscopeServer()
    server.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
'''
        mock_server_file.write_text(mock_server_content)
        print(f"Created {mock_server_file}")
    
    # Create docs directory if it doesn't exist
    docs_dir = project_root / 'docs'
    docs_dir.mkdir(exist_ok=True)
    
    # Check if requirements-dev.txt exists
    req_dev = project_root / 'requirements-dev.txt'
    if not req_dev.exists():
        print(f"\nNote: Create {req_dev} with development dependencies")
        print("See the testing documentation for contents")
    
    print("\nTest structure setup complete!")
    print("\nNext steps:")
    print("1. Copy the test files to the tests/ directory")
    print("2. Copy testing.md to the docs/ directory")
    print("3. Install dev dependencies: pip install -r requirements-dev.txt")
    print("4. Run tests: cd tests && python run_tests.py")


if __name__ == '__main__':
    setup_test_structure()