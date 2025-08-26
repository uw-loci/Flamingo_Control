# tests/test_tcp_communication.py
"""
Unit tests for TCP communication with the microscope.

These tests verify the core communication functionality without
requiring an actual microscope connection.
"""
import unittest
from unittest.mock import Mock, patch, MagicMock, call
import socket
import struct
import numpy as np
import tempfile
import os

from src.py2flamingo.services.connection_service import ConnectionService
from tests.test_utils import NoOpThreadManager
from unittest.mock import patch, MagicMock



class TestTCPClient(unittest.TestCase):
    """Test the TCPClient class for basic communication."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.test_ip = "192.168.1.100"
        self.test_port = 53717
        self.client = TCPClient(self.test_ip, self.test_port)
    
    def tearDown(self):
        """Clean up after tests."""
        if self.client:
            self.client.disconnect()
    
    @patch('socket.socket')
    def test_successful_connection(self, mock_socket_class):
        """Test successful connection to microscope."""
        # Create mock sockets
        mock_nuc_socket = MagicMock()
        mock_live_socket = MagicMock()
        
        # Configure socket class to return our mocks
        mock_socket_class.side_effect = [mock_nuc_socket, mock_live_socket]
        
        # Test connection
        nuc, live = self.client.connect()
        
        # Verify socket creation
        self.assertEqual(mock_socket_class.call_count, 2)
        mock_socket_class.assert_called_with(socket.AF_INET, socket.SOCK_STREAM)
        
        # Verify connection attempts
        mock_nuc_socket.connect.assert_called_once_with((self.test_ip, self.test_port))
        mock_live_socket.connect.assert_called_once_with((self.test_ip, self.test_port + 1))
        
        # Verify timeout was set and cleared
        mock_nuc_socket.settimeout.assert_has_calls([call(2), call(None)])
        
        # Verify sockets were returned
        self.assertEqual(nuc, mock_nuc_socket)
        self.assertEqual(live, mock_live_socket)
    
    @patch('socket.socket')
    def test_connection_refused(self, mock_socket_class):
        """Test handling of connection refused error."""
        # Create mock socket that raises ConnectionRefusedError
        mock_socket = MagicMock()
        mock_socket.connect.side_effect = ConnectionRefusedError("Connection refused")
        mock_socket_class.return_value = mock_socket
        
        # Test connection
        nuc, live = self.client.connect()
        
        # Verify failure
        self.assertIsNone(nuc)
        self.assertIsNone(live)
        
        # Verify socket was closed
        mock_socket.close.assert_called()
    
    @patch('socket.socket')
    def test_connection_timeout(self, mock_socket_class):
        """Test handling of connection timeout."""
        # Create mock socket that times out
        mock_socket = MagicMock()
        mock_socket.connect.side_effect = socket.timeout("Connection timed out")
        mock_socket_class.return_value = mock_socket
        
        # Test connection
        nuc, live = self.client.connect()
        
        # Verify failure
        self.assertIsNone(nuc)
        self.assertIsNone(live)
    
    def test_send_command_structure(self):
        """Test command structure formatting."""
        # Create a mock socket
        mock_socket = MagicMock()
        self.client.nuc_socket = mock_socket
        
        # Test command
        command = 24580  # COMMAND_CODES_STAGE_POSITION_SET
        command_data = [1, 0, 0, 10.5]  # X-axis, move to 10.5mm
        
        # Send command
        self.client.send_command(command, command_data)
        
        # Verify the binary structure
        sent_data = mock_socket.send.call_args[0][0]
        
        # Unpack the sent data
        s = struct.Struct("I I I I I I I I I I d I 72s I")
        unpacked = s.unpack(sent_data)
        
        # Verify structure
        self.assertEqual(unpacked[0], 0xF321E654)  # Start marker
        self.assertEqual(unpacked[1], command)      # Command code
        self.assertEqual(unpacked[6], 1)            # Axis (X)
        self.assertEqual(unpacked[10], 10.5)        # Position value
        self.assertEqual(unpacked[13], 0xFEDC4321)  # End marker
    
    def test_send_workflow(self):
        """Test workflow file sending."""
        # Create mock socket
        mock_socket = MagicMock()
        self.client.nuc_socket = mock_socket
        
        # Create a temporary workflow file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            workflow_content = """<Workflow Settings>
<Work Flow Type>
Stack
<Start Position>
X (mm) = 10.0
Y (mm) = 20.0
Z (mm) = 5.0
Angle (degrees) = 0.0
</Workflow Settings>"""
            f.write(workflow_content)
            workflow_file = f.name
        
        try:
            # Send workflow
            command = 12292  # COMMAND_CODES_CAMERA_WORK_FLOW_START
            self.client.send_workflow(workflow_file, command)
            
            # Verify two sends (header + data)
            self.assertEqual(mock_socket.send.call_count, 2)
            
            # Verify header structure
            header = mock_socket.send.call_args_list[0][0][0]
            s = struct.Struct("I I I I I I I I I I d I 72s I")
            unpacked = s.unpack(header)
            
            self.assertEqual(unpacked[0], 0xF321E654)  # Start marker
            self.assertEqual(unpacked[1], command)      # Command
            self.assertEqual(unpacked[9], 1)            # cmdDataBits0
            self.assertEqual(unpacked[11], len(workflow_content.encode()))  # File size
            
            # Verify workflow data was sent
            workflow_data = mock_socket.send.call_args_list[1][0][0]
            self.assertEqual(workflow_data.decode(), workflow_content)
            
        finally:
            # Clean up
            os.unlink(workflow_file)
    
    def test_disconnect(self):
        """Test proper disconnection and cleanup."""
        # Create mock sockets
        mock_nuc = MagicMock()
        mock_live = MagicMock()
        self.client.nuc_socket = mock_nuc
        self.client.live_socket = mock_live
        
        # Disconnect
        self.client.disconnect()
        
        # Verify sockets were closed
        mock_nuc.close.assert_called_once()
        mock_live.close.assert_called_once()
        
        # Verify references cleared
        self.assertIsNone(self.client.nuc_socket)
        self.assertIsNone(self.client.live_socket)


class TestMicroscopeIntegration(unittest.TestCase):
    """Integration tests for complete microscope communication flow."""
    
    def test_complete_workflow_execution(self):
        """Test a complete workflow execution sequence using ConnectionService."""

        from unittest.mock import patch, MagicMock
        from src.py2flamingo.services.connection_service import ConnectionService
        from src.py2flamingo.core.events import EventManager
        from src.py2flamingo.core.queue_manager import QueueManager
        # You'll add this tiny helper in tests/test_utils.py (see prior message)
        from tests.test_utils import NoOpThreadManager

        # ---- Arrange ----
        mock_nuc_socket = MagicMock()
        mock_live_socket = MagicMock()

        event_manager = EventManager()
        queue_manager = QueueManager()

        conn_service = ConnectionService(
            ip="192.168.1.100",
            port=53717,
            event_manager=event_manager,
            queue_manager=queue_manager
        )

        # Inject the no-op ThreadManager & stub socket creation
        with patch('src.py2flamingo.services.connection_service.ThreadManager', NoOpThreadManager):
            # If your ConnectionService calls a factory like _create_socket(), stub it to avoid real sockets
            with patch.object(conn_service, '_create_socket', side_effect=[mock_nuc_socket, mock_live_socket]):

                # ---- Act ----
                result = conn_service.connect()
                self.assertTrue(result)
                self.assertTrue(conn_service.is_connected())

                # Send a command (command code & payload shape per your project)
                conn_service.send_command(24580, [1, 0, 0, 10.5])

                # ---- Assert ----
                self.assertFalse(queue_manager.get_queue('command').empty(), "command queue should have an entry")
                self.assertFalse(queue_manager.get_queue('command_data').empty(), "command_data queue should have an entry")
                self.assertTrue(event_manager.is_set('send'), "'send' event should be set to wake sender")


class TestWorkflowParsing(unittest.TestCase):
    """Test workflow file parsing and generation."""
    
    def test_workflow_to_dict(self):
        """Test parsing workflow text to dictionary."""
        workflow_text = """<Workflow Settings>
<Work Flow Type>
Stack
<Start Position>
X (mm) = 10.0
Y (mm) = 20.0
Z (mm) = 5.0
Angle (degrees) = 0.0
<End Position>
X (mm) = 10.0
Y (mm) = 20.0
Z (mm) = 15.0
Angle (degrees) = 0.0
<Stack Settings>
Number of planes = 100
Change in Z axis (mm) = 0.1
<Experiment Settings>
Save image data = Tiff
Comments = Test workflow
</Workflow Settings>"""
        
        # Write to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(workflow_text)
            temp_file = f.name
        
        try:
            # Import and test
            from src.py2flamingo.utils.file_handlers import workflow_to_dict
            
            result = workflow_to_dict(temp_file)
            
            # Verify structure
            self.assertEqual(result['Work Flow Type'], 'Stack')
            self.assertEqual(float(result['Start Position']['X (mm)']), 10.0)
            self.assertEqual(float(result['End Position']['Z (mm)']), 15.0)
            self.assertEqual(int(result['Stack Settings']['Number of planes']), 100)
            self.assertEqual(result['Experiment Settings']['Save image data'], 'Tiff')
            
        finally:
            os.unlink(temp_file)
    
    def test_dict_to_workflow(self):
        """Test converting dictionary back to workflow text."""
        from src.py2flamingo.utils.file_handlers import dict_to_workflow
        
        # Create workflow dict
        workflow_dict = {
            'Work Flow Type': 'Snap',
            'Start Position': {
                'X (mm)': '5.0',
                'Y (mm)': '10.0',
                'Z (mm)': '2.0',
                'Angle (degrees)': '0.0'
            },
            'End Position': {
                'X (mm)': '5.0',
                'Y (mm)': '10.0',
                'Z (mm)': '2.01',
                'Angle (degrees)': '0.0'
            },
            'Experiment Settings': {
                'Save image data': 'NotSaved',
                'Comments': 'Unit test'
            }
        }
        
        # Write to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            temp_file = f.name
        
        try:
            dict_to_workflow(temp_file, workflow_dict)
            
            # Read back and verify
            with open(temp_file, 'r') as f:
                content = f.read()
            
            self.assertIn('<Workflow Settings>', content)
            self.assertIn('Work Flow Type', content)
            self.assertIn('Snap', content)
            self.assertIn('X (mm) = 5.0', content)
            self.assertIn('</Workflow Settings>', content)
            
        finally:
            os.unlink(temp_file)


if __name__ == '__main__':
    unittest.main()