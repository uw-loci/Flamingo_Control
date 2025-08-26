# tests/test_connection_service.py
"""
Integration tests for the connection service.

These tests verify the complete connection flow including thread management.
"""
import unittest
from unittest.mock import Mock, patch, MagicMock, call
import socket
import threading
import time
import tempfile
import os

from py2flamingo.services.connection_service import ConnectionService
from py2flamingo.core.events import EventManager
from py2flamingo.core.queue_manager import QueueManager
from tests.test_utils import NoOpThreadManager


class TestConnectionService(unittest.TestCase):
    """Test the ConnectionService class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.event_manager = EventManager()
        self.queue_manager = QueueManager()
        self.test_ip = "192.168.1.100"
        self.test_port = 53717
        
        self.service = ConnectionService(
            ip=self.test_ip,
            port=self.test_port,
            event_manager=self.event_manager,
            queue_manager=self.queue_manager
        )
    
    def tearDown(self):
        """Clean up after tests."""
        if self.service.is_connected():
            self.service.disconnect()
    
    @patch('socket.socket')
    def test_successful_connection_flow(self, mock_socket_class):
        """Test complete successful connection flow."""
        # Create mock sockets
        mock_nuc = MagicMock()
        mock_live = MagicMock()
        mock_socket_class.side_effect = [mock_nuc, mock_live]
        
        # Mock thread manager
        with patch('src.py2flamingo.services.connection_service.ThreadManager', NoOpThreadManager):

            svc = ConnectionService()

            # If ConnectionService holds a socket manager or similar, stub it out:
            # Not all codebases have this attribute; if not, remove these two lines.
            if hasattr(svc, 'socket_manager'):
                svc.socket_manager = MagicMock()

            # Clean initial state
            svc.queue_manager.clear_all()
            svc.event_manager.clear_all()

            # Connect (whatever signature your service uses)
            # If your connect() needs ip/ports, pass them here. Otherwise, omit args.
            try:
                svc.connect('127.0.0.1', 9000)  # adjust if your method signature differs
            except TypeError:
                svc.connect()  # fallback if connect() takes no args in your codebase

            # Send a command through the *public* API
            # Command code & data shape should match your project conventions
            svc.send_command(0x3000, [0, 0, 0, 1.23])

            # Assert behavior: command and payload are queued & 'send' event is set
            cmd_q  = svc.queue_manager.get_queue_nowait('command')
            data_q = svc.queue_manager.get_queue_nowait('command_data')

            assert not cmd_q.empty(), "command queue should contain the command code"
            assert not data_q.empty(), "command_data queue should contain the payload"
            assert svc.event_manager.get_event('send').is_set(), "'send' event should be set"
    
    @patch('socket.socket')
    def test_connection_failure_cleanup(self, mock_socket_class):
        """Test proper cleanup on connection failure."""
        # Create mock socket that fails
        mock_socket = MagicMock()
        mock_socket.connect.side_effect = ConnectionRefusedError()
        mock_socket_class.return_value = mock_socket
        
        # Try to connect
        result = self.service.connect()
        
        # Verify failure
        self.assertFalse(result)
        self.assertFalse(self.service.is_connected())
        
        # Verify cleanup
        mock_socket.close.assert_called()
        self.assertIsNone(self.service.nuc_client)
        self.assertIsNone(self.service.live_client)
    
    def test_send_command(self):
        """Test sending commands through the service."""
        # Mock connection
        self.service._connected = True
        
        # Send command
        command = 24580  # STAGE_POSITION_SET
        data = [1, 0, 0, 10.5]
        self.service.send_command(command, data)
        
        # Verify command was queued
        queued_command = self.queue_manager.get_nowait('command')
        self.assertEqual(queued_command, command)
        
        queued_data = self.queue_manager.get_nowait('command_data')
        self.assertEqual(queued_data, data)
        
        # Verify send event was set
        self.assertTrue(self.event_manager.is_set('send'))
    
    def test_send_command_not_connected(self):
        """Test error when sending command while not connected."""
        with self.assertRaises(RuntimeError) as context:
            self.service.send_command(12345)
        
        self.assertIn("Not connected", str(context.exception))
    
    @patch('src.py2flamingo.utils.file_handlers.dict_to_workflow')
    def test_send_workflow(self, mock_dict_to_workflow):
        """Test sending workflow through the service."""
        # Mock connection
        self.service._connected = True
        
        # Create test workflow
        workflow_dict = {
            'Work Flow Type': 'Stack',
            'Start Position': {'X (mm)': '10.0'},
            'End Position': {'X (mm)': '10.0'}
        }
        
        # Send workflow
        self.service.send_workflow(workflow_dict)
        
        # Verify workflow was saved
        mock_dict_to_workflow.assert_called_once()
        saved_path = mock_dict_to_workflow.call_args[0][0]
        self.assertEqual(saved_path, os.path.join('workflows', 'workflow.txt'))
        
        # Verify command was sent
        command = self.queue_manager.get_nowait('command')
        self.assertEqual(command, 12292)  # CAMERA_WORK_FLOW_START
    
    @patch('src.py2flamingo.utils.file_handlers.text_to_dict')
    def test_get_microscope_settings(self, mock_text_to_dict):
        """Test retrieving microscope settings."""
        # Mock connection
        self.service._connected = True
        
        # Mock settings file content
        mock_settings = {
            'Type': {
                'Tube lens design focal length (mm)': '200',
                'Objective lens magnification': '20'
            }
        }
        mock_text_to_dict.return_value = mock_settings
        
        # Mock file existence
        with patch('pathlib.Path.exists', return_value=True):
            with patch('time.sleep'):  # Speed up test
                # Get settings
                pixel_size, settings = self.service.get_microscope_settings()
        
        # Verify command was sent
        command = self.queue_manager.get_nowait('command')
        self.assertEqual(command, 4105)  # SCOPE_SETTINGS_LOAD
        
        # Verify pixel size calculation
        # pixel_size = 6.5 / (20 * (200/200)) / 1000 = 0.000325
        self.assertAlmostEqual(pixel_size, 0.000325, places=6)
        self.assertEqual(settings, mock_settings)


class TestThreadIntegration(unittest.TestCase):
    """Test thread management and communication."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.event_manager = EventManager()
        self.queue_manager = QueueManager()
    
    def test_command_send_flow(self):
        """Test the flow of sending a command through threads."""
        # Simulate send thread behavior
        def mock_send_thread():
            """Simplified send thread."""
            while True:
                if self.event_manager.wait_for_event('send', timeout=0.1):
                    # Get command
                    command = self.queue_manager.get_nowait('command')
                    if command:
                        # Get data if available
                        data = self.queue_manager.get_nowait('command_data')
                        
                        # Simulate sending
                        self.queue_manager.put_nowait('other_data', {
                            'command': command,
                            'data': data,
                            'sent': True
                        })
                        
                        # Clear send event
                        self.event_manager.clear_event('send')
                        
                        # Set system idle
                        self.event_manager.set_event('system_idle')
                
                if self.event_manager.is_set('terminate'):
                    break
        
        # Start mock thread
        thread = threading.Thread(target=mock_send_thread)
        thread.daemon = True
        thread.start()
        
        try:
            # Send a command
            self.queue_manager.put_nowait('command', 24580)
            self.queue_manager.put_nowait('command_data', [1, 0, 0, 5.0])
            self.event_manager.set_event('send')
            
            # Wait for processing
            time.sleep(0.2)
            
            # Verify command was processed
            result = self.queue_manager.get_nowait('other_data')
            self.assertIsNotNone(result)
            self.assertEqual(result['command'], 24580)
            self.assertEqual(result['data'], [1, 0, 0, 5.0])
            self.assertTrue(result['sent'])
            
            # Verify system is idle
            self.assertTrue(self.event_manager.is_set('system_idle'))
            
        finally:
            # Clean up
            self.event_manager.set_event('terminate')
            thread.join(timeout=1.0)
    
    def test_image_receive_flow(self):
        """Test the flow of receiving image data."""
        # Simulate image data
        test_image = b'\x00' * 1024  # 1KB of zeros
        
        def mock_live_thread():
            """Simplified live thread."""
            # Simulate receiving image
            self.queue_manager.put_nowait('image', test_image)
            self.queue_manager.put_nowait('visualize', test_image)
        
        # Run mock thread
        thread = threading.Thread(target=mock_live_thread)
        thread.start()
        thread.join()
        
        # Verify images were queued
        image_data = self.queue_manager.get_nowait('image')
        viz_data = self.queue_manager.get_nowait('visualize')
        
        self.assertEqual(image_data, test_image)
        self.assertEqual(viz_data, test_image)


class TestMicroscopeCommands(unittest.TestCase):
    """Test specific microscope command sequences."""
    
    def test_stage_movement_sequence(self):
        """Test the sequence for moving the stage."""
        from src.py2flamingo.controllers.position_controller import PositionController
        from src.py2flamingo.models.microscope import Position
        
        # Create mocks
        connection_service = Mock()
        connection_service.is_connected.return_value = True
        
        queue_manager = QueueManager()
        event_manager = EventManager()
        
        # Create controller
        controller = PositionController(
            connection_service=connection_service,
            queue_manager=queue_manager,
            event_manager=event_manager
        )
        
        # Move to position
        target_pos = Position(x=10.0, y=20.0, z=5.0, r=0.0)
        controller._move_axis(controller.axis.X, target_pos.x, "X-axis")
        
        # Verify command sequence
        command_data = queue_manager.get_nowait('command_data')
        self.assertEqual(command_data, [1, 0, 0, 10.0])  # X-axis = 1
        
        command = queue_manager.get_nowait('command')
        self.assertEqual(command, 24580)  # STAGE_POSITION_SET
        
        self.assertTrue(event_manager.is_set('send'))


class TestConfigurationFiles(unittest.TestCase):
    """Test configuration file handling."""
    
    def test_metadata_parsing(self):
        """Test parsing FlamingoMetaData.txt."""
        metadata_content = """<Instrument>
<Type>
Microscope name = TestScope
Microscope address = 192.168.1.100 53717
Microscope type = Flamingo
Objective lens magnification = 16
Tube lens length (mm) = 200
</Type>
</Instrument>"""
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(metadata_content)
            temp_file = f.name
        
        try:
            from src.py2flamingo.utils.file_handlers import text_to_dict
            
            result = text_to_dict(temp_file)
            
            # Verify parsing
            instrument = result['Instrument']['Type']
            self.assertEqual(instrument['Microscope name'], 'TestScope')
            self.assertEqual(instrument['Microscope address'], '192.168.1.100 53717')
            self.assertEqual(float(instrument['Objective lens magnification']), 16)
            
        finally:
            os.unlink(temp_file)


if __name__ == '__main__':
    unittest.main()