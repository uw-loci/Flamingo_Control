# ============================================================================
# tests/test_controllers.py
"""
Tests for MVC Controllers Layer.

Tests ConnectionController and WorkflowController with mocked services.
"""

import unittest
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path
from datetime import datetime
import socket

# Import controllers
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from py2flamingo.controllers.connection_controller import ConnectionController
from py2flamingo.controllers.workflow_controller import WorkflowController
from py2flamingo.models.connection import ConnectionConfig, ConnectionState, ConnectionStatus, ConnectionModel


class TestConnectionController(unittest.TestCase):
    """Test ConnectionController class."""

    def setUp(self):
        """Set up test fixtures."""
        # Create mocks
        self.mock_service = Mock()
        self.mock_model = Mock()
        self.mock_model.status = ConnectionStatus(
            state=ConnectionState.DISCONNECTED,
            ip=None,
            port=None,
            connected_at=None,
            last_error=None
        )

        # Create controller
        self.controller = ConnectionController(self.mock_service, self.mock_model)

    def test_init(self):
        """Test controller initialization."""
        self.assertIsNotNone(self.controller)
        self.assertEqual(self.controller._service, self.mock_service)
        self.assertEqual(self.controller._model, self.mock_model)

    def test_connect_success(self):
        """Test successful connection."""
        self.mock_service.is_connected.return_value = False
        self.mock_service.connect.return_value = None

        success, message = self.controller.connect("192.168.1.100", 53717)

        self.assertTrue(success)
        self.assertIn("Connected", message)
        self.assertIn("192.168.1.100", message)
        self.assertIn("53717", message)
        self.mock_service.connect.assert_called_once()

    def test_connect_invalid_ip_empty(self):
        """Test connect with empty IP."""
        success, message = self.controller.connect("", 53717)

        self.assertFalse(success)
        self.assertIn("cannot be empty", message)
        self.mock_service.connect.assert_not_called()

    def test_connect_invalid_ip_format(self):
        """Test connect with invalid IP format."""
        test_cases = [
            "not_an_ip",
            "999.999.999.999",
            "192.168.1",
            "192.168.1.1.1",
            "192.168.-1.1",
            "192.168.1.256",
        ]

        for invalid_ip in test_cases:
            success, message = self.controller.connect(invalid_ip, 53717)
            self.assertFalse(success, f"Should reject {invalid_ip}")
            self.assertIn("Invalid IP address format", message)

    def test_connect_valid_ip_formats(self):
        """Test connect with valid IP formats."""
        valid_ips = [
            "127.0.0.1",
            "192.168.1.1",
            "10.0.0.1",
            "255.255.255.255",
            "0.0.0.0",
        ]

        self.mock_service.is_connected.return_value = False
        self.mock_service.connect.return_value = None

        for valid_ip in valid_ips:
            success, message = self.controller.connect(valid_ip, 53717)
            self.assertTrue(success, f"Should accept {valid_ip}")

    def test_connect_invalid_port_type(self):
        """Test connect with invalid port type."""
        success, message = self.controller.connect("127.0.0.1", "not_a_port")

        self.assertFalse(success)
        self.assertIn("must be an integer", message)

    def test_connect_invalid_port_range(self):
        """Test connect with out-of-range port."""
        test_cases = [0, -1, 65536, 70000, 100000]

        for invalid_port in test_cases:
            success, message = self.controller.connect("127.0.0.1", invalid_port)
            self.assertFalse(success, f"Should reject port {invalid_port}")
            self.assertIn("between 1 and 65535", message)

    def test_connect_already_connected(self):
        """Test connect when already connected."""
        self.mock_service.is_connected.return_value = True

        success, message = self.controller.connect("127.0.0.1", 53717)

        self.assertFalse(success)
        self.assertIn("Already connected", message)
        self.mock_service.connect.assert_not_called()

    def test_connect_timeout_error(self):
        """Test connect with timeout error."""
        self.mock_service.is_connected.return_value = False
        self.mock_service.connect.side_effect = TimeoutError()

        success, message = self.controller.connect("127.0.0.1", 53717)

        self.assertFalse(success)
        self.assertIn("timeout", message.lower())
        self.assertIn("server running", message.lower())

    def test_connect_connection_refused(self):
        """Test connect with connection refused."""
        self.mock_service.is_connected.return_value = False
        self.mock_service.connect.side_effect = ConnectionRefusedError()

        success, message = self.controller.connect("127.0.0.1", 53717)

        self.assertFalse(success)
        self.assertIn("refused", message.lower())

    def test_connect_network_unreachable(self):
        """Test connect with network unreachable error."""
        self.mock_service.is_connected.return_value = False
        self.mock_service.connect.side_effect = OSError("Network is unreachable")

        success, message = self.controller.connect("127.0.0.1", 53717)

        self.assertFalse(success)
        self.assertIn("unreachable", message.lower())

    def test_disconnect_success(self):
        """Test successful disconnect."""
        self.mock_service.is_connected.return_value = True
        self.mock_service.disconnect.return_value = None

        success, message = self.controller.disconnect()

        self.assertTrue(success)
        self.assertIn("Disconnected", message)
        self.mock_service.disconnect.assert_called_once()

    def test_disconnect_not_connected(self):
        """Test disconnect when not connected."""
        self.mock_service.is_connected.return_value = False

        success, message = self.controller.disconnect()

        self.assertFalse(success)
        self.assertIn("Not connected", message)
        self.mock_service.disconnect.assert_not_called()

    def test_disconnect_error(self):
        """Test disconnect with error."""
        self.mock_service.is_connected.return_value = True
        self.mock_service.disconnect.side_effect = Exception("Disconnect failed")

        success, message = self.controller.disconnect()

        self.assertFalse(success)
        self.assertIn("error", message.lower())

    def test_reconnect_success(self):
        """Test successful reconnect."""
        # Set previous connection info
        self.mock_model.status = ConnectionStatus(
            state=ConnectionState.DISCONNECTED,
            ip="127.0.0.1",
            port=53717,
            connected_at=None,
            last_error=None
        )
        self.mock_service.is_connected.return_value = False
        self.mock_service.reconnect.return_value = None

        success, message = self.controller.reconnect()

        self.assertTrue(success)
        self.assertIn("Reconnected", message)
        self.mock_service.reconnect.assert_called_once()

    def test_reconnect_no_previous_connection(self):
        """Test reconnect with no previous connection."""
        self.mock_model.status = ConnectionStatus(
            state=ConnectionState.DISCONNECTED,
            ip=None,
            port=None,
            connected_at=None,
            last_error=None
        )

        success, message = self.controller.reconnect()

        self.assertFalse(success)
        self.assertIn("No previous connection", message)
        self.mock_service.reconnect.assert_not_called()

    def test_reconnect_timeout(self):
        """Test reconnect with timeout."""
        self.mock_model.status = ConnectionStatus(
            state=ConnectionState.DISCONNECTED,
            ip="127.0.0.1",
            port=53717,
            connected_at=None,
            last_error=None
        )
        self.mock_service.is_connected.return_value = False
        self.mock_service.reconnect.side_effect = TimeoutError()

        success, message = self.controller.reconnect()

        self.assertFalse(success)
        self.assertIn("timeout", message.lower())

    def test_get_connection_status(self):
        """Test getting connection status."""
        self.mock_model.status = ConnectionStatus(
            state=ConnectionState.CONNECTED,
            ip="127.0.0.1",
            port=53717,
            connected_at=datetime(2025, 1, 1, 12, 0, 0),
            last_error=None
        )
        self.mock_service.is_connected.return_value = True

        status = self.controller.get_connection_status()

        self.assertTrue(status['connected'])
        self.assertEqual(status['state'], 'connected')  # Enum value is lowercase
        self.assertEqual(status['ip'], "127.0.0.1")
        self.assertEqual(status['port'], 53717)
        self.assertIsNotNone(status['connected_at'])
        self.assertIsNone(status['last_error'])

    def test_handle_connection_error_timeout(self):
        """Test error handling for timeout."""
        error = TimeoutError()
        message = self.controller.handle_connection_error(error)

        self.assertIn("timeout", message.lower())
        self.assertIn("server running", message.lower())

    def test_handle_connection_error_refused(self):
        """Test error handling for connection refused."""
        error = ConnectionRefusedError()
        message = self.controller.handle_connection_error(error)

        self.assertIn("refused", message.lower())

    def test_handle_connection_error_network_unreachable(self):
        """Test error handling for network unreachable."""
        error = OSError("Network is unreachable")
        message = self.controller.handle_connection_error(error)

        self.assertIn("unreachable", message.lower())

    def test_handle_connection_error_generic(self):
        """Test error handling for generic error."""
        error = ValueError("Some error")
        message = self.controller.handle_connection_error(error)

        self.assertIn("Invalid value", message)

    def test_test_connection_success(self):
        """Test successful connection test."""
        with patch('socket.socket') as mock_socket_class:
            # Mock successful connection
            mock_socket = Mock()
            mock_socket_class.return_value = mock_socket
            mock_socket.connect.return_value = None

            success, message = self.controller.test_connection("192.168.1.100", 53717)

            self.assertTrue(success)
            self.assertIn("successful", message.lower())
            self.assertIn("192.168.1.100", message)
            self.assertIn("53717", message)

            # Verify socket was closed (may be called multiple times due to finally block)
            mock_socket.close.assert_called()

    def test_test_connection_invalid_ip_empty(self):
        """Test connection test with empty IP."""
        success, message = self.controller.test_connection("", 53717)

        self.assertFalse(success)
        self.assertIn("IP address", message)
        self.assertIn("empty", message.lower())

    def test_test_connection_invalid_ip_format(self):
        """Test connection test with invalid IP format."""
        invalid_ips = [
            "not_an_ip",
            "999.999.999.999",
            "192.168.1",
            "192.168.1.1.1",
            "192.168.-1.1",
        ]

        for invalid_ip in invalid_ips:
            success, message = self.controller.test_connection(invalid_ip, 53717)
            self.assertFalse(success, f"Should reject {invalid_ip}")
            self.assertIn("Invalid IP address", message)

    def test_test_connection_invalid_port_type(self):
        """Test connection test with invalid port type."""
        success, message = self.controller.test_connection("192.168.1.100", "not_a_port")

        self.assertFalse(success)
        self.assertIn("invalid port", message.lower())

    def test_test_connection_invalid_port_range(self):
        """Test connection test with out-of-range port."""
        invalid_ports = [0, -1, 65536, 70000]

        for invalid_port in invalid_ports:
            success, message = self.controller.test_connection("192.168.1.100", invalid_port)
            self.assertFalse(success, f"Should reject port {invalid_port}")
            self.assertIn("invalid port", message.lower())

    def test_test_connection_timeout(self):
        """Test connection test with timeout."""
        with patch('socket.socket') as mock_socket_class:
            mock_socket = Mock()
            mock_socket_class.return_value = mock_socket
            mock_socket.connect.side_effect = socket.timeout("Connection timed out")

            success, message = self.controller.test_connection("192.168.1.100", 53717, timeout=1.0)

            self.assertFalse(success)
            self.assertIn("timeout", message.lower())
            self.assertIn("not responding", message.lower())

            # Socket should still be closed
            mock_socket.close.assert_called()

    def test_test_connection_refused(self):
        """Test connection test with connection refused."""
        with patch('socket.socket') as mock_socket_class:
            mock_socket = Mock()
            mock_socket_class.return_value = mock_socket
            mock_socket.connect.side_effect = ConnectionRefusedError()

            success, message = self.controller.test_connection("192.168.1.100", 53717)

            self.assertFalse(success)
            self.assertIn("refused", message.lower())
            self.assertIn("not listening", message.lower())

            mock_socket.close.assert_called()

    def test_test_connection_host_unreachable(self):
        """Test connection test with host unreachable."""
        with patch('socket.socket') as mock_socket_class:
            mock_socket = Mock()
            mock_socket_class.return_value = mock_socket
            mock_socket.connect.side_effect = OSError("No route to host")

            success, message = self.controller.test_connection("192.168.1.100", 53717)

            self.assertFalse(success)
            self.assertIn("route to host", message.lower())

            mock_socket.close.assert_called()

    def test_test_connection_network_unreachable(self):
        """Test connection test with network unreachable."""
        with patch('socket.socket') as mock_socket_class:
            mock_socket = Mock()
            mock_socket_class.return_value = mock_socket
            mock_socket.connect.side_effect = OSError("Network is unreachable")

            success, message = self.controller.test_connection("192.168.1.100", 53717)

            self.assertFalse(success)
            self.assertIn("network", message.lower())
            self.assertIn("unreachable", message.lower())

            mock_socket.close.assert_called_once()

    def test_test_connection_generic_error(self):
        """Test connection test with generic error."""
        with patch('socket.socket') as mock_socket_class:
            mock_socket = Mock()
            mock_socket_class.return_value = mock_socket
            mock_socket.connect.side_effect = Exception("Unknown error")

            success, message = self.controller.test_connection("192.168.1.100", 53717)

            self.assertFalse(success)
            self.assertIn("error", message.lower())

            mock_socket.close.assert_called_once()

    def test_test_connection_custom_timeout(self):
        """Test connection test with custom timeout."""
        with patch('socket.socket') as mock_socket_class:
            mock_socket = Mock()
            mock_socket_class.return_value = mock_socket
            mock_socket.connect.return_value = None

            success, message = self.controller.test_connection("192.168.1.100", 53717, timeout=5.0)

            self.assertTrue(success)
            # Verify timeout was set
            mock_socket.settimeout.assert_called_once_with(5.0)

    def test_test_connection_validates_before_connecting(self):
        """Test that validation happens before attempting connection."""
        # Invalid IP should not create socket
        with patch('socket.socket') as mock_socket_class:
            success, message = self.controller.test_connection("invalid_ip", 53717)

            self.assertFalse(success)
            # Socket should never be created for invalid input
            mock_socket_class.assert_not_called()

    def test_validate_ip_valid_ips(self):
        """Test IP validation with valid addresses."""
        valid_ips = [
            "127.0.0.1",
            "192.168.1.1",
            "10.0.0.1",
            "255.255.255.255",
            "0.0.0.0",
            "10.129.37.22",
        ]

        for ip in valid_ips:
            is_valid = self.controller._validate_ip(ip)
            self.assertTrue(is_valid, f"Should accept {ip}")

    def test_validate_ip_invalid_ips(self):
        """Test IP validation with invalid addresses."""
        invalid_ips = [
            "",
            "not_an_ip",
            "999.999.999.999",
            "192.168.1",
            "192.168.1.1.1",
            "192.168.-1.1",
            "192.168.1.256",
            "192.168.1.1.1.1",
            "abc.def.ghi.jkl",
        ]

        for ip in invalid_ips:
            is_valid = self.controller._validate_ip(ip)
            self.assertFalse(is_valid, f"Should reject {ip}")


class TestWorkflowController(unittest.TestCase):
    """Test WorkflowController class."""

    def setUp(self):
        """Set up test fixtures."""
        # Create mocks
        self.mock_service = Mock()
        self.mock_connection_model = Mock()
        self.mock_connection_model.status = ConnectionStatus(
            state=ConnectionState.DISCONNECTED,
            ip=None,
            port=None,
            connected_at=None,
            last_error=None
        )

        # Create controller
        self.controller = WorkflowController(self.mock_service, self.mock_connection_model)

    def test_init(self):
        """Test controller initialization."""
        self.assertIsNotNone(self.controller)
        self.assertEqual(self.controller._service, self.mock_service)
        self.assertEqual(self.controller._connection_model, self.mock_connection_model)
        self.assertIsNone(self.controller._current_workflow_path)

    def test_load_workflow_success(self):
        """Test successful workflow loading."""
        # Create temp file
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("Test workflow content")
            temp_path = f.name

        try:
            self.mock_service.load_workflow.return_value = None

            success, message = self.controller.load_workflow(temp_path)

            self.assertTrue(success)
            self.assertIn("loaded successfully", message.lower())
            self.mock_service.load_workflow.assert_called_once()
            self.assertIsNotNone(self.controller._current_workflow_path)
        finally:
            Path(temp_path).unlink()

    def test_load_workflow_empty_path(self):
        """Test load workflow with empty path."""
        success, message = self.controller.load_workflow("")

        self.assertFalse(success)
        self.assertIn("cannot be empty", message)
        self.mock_service.load_workflow.assert_not_called()

    def test_load_workflow_file_not_found(self):
        """Test load workflow with non-existent file."""
        success, message = self.controller.load_workflow("/nonexistent/workflow.txt")

        self.assertFalse(success)
        self.assertIn("not found", message.lower())
        self.mock_service.load_workflow.assert_not_called()

    def test_load_workflow_invalid_extension(self):
        """Test load workflow with non-.txt file."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("# Not a workflow")
            temp_path = f.name

        try:
            success, message = self.controller.load_workflow(temp_path)

            self.assertFalse(success)
            self.assertIn("must be .txt", message)
            self.mock_service.load_workflow.assert_not_called()
        finally:
            Path(temp_path).unlink()

    def test_load_workflow_empty_file(self):
        """Test load workflow with empty file."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            # Write nothing
            temp_path = f.name

        try:
            success, message = self.controller.load_workflow(temp_path)

            self.assertFalse(success)
            self.assertIn("empty", message.lower())
            self.mock_service.load_workflow.assert_not_called()
        finally:
            Path(temp_path).unlink()

    def test_start_workflow_success(self):
        """Test successful workflow start."""
        # Set connected state
        self.mock_connection_model.status = ConnectionStatus(
            state=ConnectionState.CONNECTED,
            ip="127.0.0.1",
            port=53717,
            connected_at=datetime.now(),
            last_error=None
        )

        # Load a workflow first
        self.controller._current_workflow_path = Path("/fake/workflow.txt")
        self.mock_service.start_workflow.return_value = None

        success, message = self.controller.start_workflow()

        self.assertTrue(success)
        self.assertIn("started", message.lower())
        self.mock_service.start_workflow.assert_called_once()

    def test_start_workflow_not_connected(self):
        """Test start workflow when not connected."""
        self.mock_connection_model.status = ConnectionStatus(
            state=ConnectionState.DISCONNECTED,
            ip=None,
            port=None,
            connected_at=None,
            last_error=None
        )

        success, message = self.controller.start_workflow()

        self.assertFalse(success)
        self.assertIn("connect", message.lower())
        self.mock_service.start_workflow.assert_not_called()

    def test_start_workflow_no_workflow_loaded(self):
        """Test start workflow with no workflow loaded."""
        self.mock_connection_model.status = ConnectionStatus(
            state=ConnectionState.CONNECTED,
            ip="127.0.0.1",
            port=53717,
            connected_at=datetime.now(),
            last_error=None
        )
        self.controller._current_workflow_path = None

        success, message = self.controller.start_workflow()

        self.assertFalse(success)
        self.assertIn("No workflow loaded", message)
        self.mock_service.start_workflow.assert_not_called()

    def test_start_workflow_connection_error(self):
        """Test start workflow with connection error."""
        self.mock_connection_model.status = ConnectionStatus(
            state=ConnectionState.CONNECTED,
            ip="127.0.0.1",
            port=53717,
            connected_at=datetime.now(),
            last_error=None
        )
        self.controller._current_workflow_path = Path("/fake/workflow.txt")
        self.mock_service.start_workflow.side_effect = ConnectionError("Lost connection")

        success, message = self.controller.start_workflow()

        self.assertFalse(success)
        self.assertIn("lost", message.lower())

    def test_stop_workflow_success(self):
        """Test successful workflow stop."""
        self.mock_connection_model.status = ConnectionStatus(
            state=ConnectionState.CONNECTED,
            ip="127.0.0.1",
            port=53717,
            connected_at=datetime.now(),
            last_error=None
        )
        self.mock_service.stop_workflow.return_value = None

        success, message = self.controller.stop_workflow()

        self.assertTrue(success)
        self.assertIn("stopped", message.lower())
        self.mock_service.stop_workflow.assert_called_once()

    def test_stop_workflow_not_connected(self):
        """Test stop workflow when not connected."""
        self.mock_connection_model.status = ConnectionStatus(
            state=ConnectionState.DISCONNECTED,
            ip=None,
            port=None,
            connected_at=None,
            last_error=None
        )

        success, message = self.controller.stop_workflow()

        self.assertFalse(success)
        self.assertIn("not connected", message.lower())
        self.mock_service.stop_workflow.assert_not_called()

    def test_get_workflow_status(self):
        """Test getting workflow status."""
        self.controller._current_workflow_path = Path("/fake/workflow.txt")

        # Mock the workflow model's running state
        self.controller._workflow_model = Mock()
        self.controller._workflow_model.is_running.return_value = True
        self.controller._workflow_model.get_execution_time.return_value = 10.5

        status = self.controller.get_workflow_status()

        self.assertTrue(status['loaded'])
        self.assertTrue(status['running'])
        self.assertEqual(status['workflow_name'], "workflow.txt")
        self.assertIn("workflow.txt", status['workflow_path'])

    def test_get_workflow_status_no_workflow(self):
        """Test getting workflow status with no workflow loaded."""
        self.controller._current_workflow_path = None
        self.mock_service.get_workflow_status.return_value = {
            'running': False
        }

        status = self.controller.get_workflow_status()

        self.assertFalse(status['loaded'])
        self.assertFalse(status['running'])
        self.assertIsNone(status['workflow_name'])
        self.assertIsNone(status['workflow_path'])

    def test_validate_workflow_file_valid(self):
        """Test workflow file validation with valid file."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("Valid workflow content")
            temp_path = f.name

        try:
            valid, errors = self.controller.validate_workflow_file(temp_path)

            self.assertTrue(valid)
            self.assertEqual(errors, [])
        finally:
            Path(temp_path).unlink()

    def test_validate_workflow_file_not_found(self):
        """Test workflow file validation with missing file."""
        valid, errors = self.controller.validate_workflow_file("/nonexistent/file.txt")

        self.assertFalse(valid)
        self.assertTrue(any("not found" in err.lower() for err in errors))

    def test_validate_workflow_file_wrong_extension(self):
        """Test workflow file validation with wrong extension."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write("# Not a workflow")
            temp_path = f.name

        try:
            valid, errors = self.controller.validate_workflow_file(temp_path)

            self.assertFalse(valid)
            self.assertTrue(any("must be .txt" in err for err in errors))
        finally:
            Path(temp_path).unlink()

    def test_validate_workflow_file_empty(self):
        """Test workflow file validation with empty file."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            # Write nothing
            temp_path = f.name

        try:
            valid, errors = self.controller.validate_workflow_file(temp_path)

            self.assertFalse(valid)
            self.assertTrue(any("empty" in err.lower() for err in errors))
        finally:
            Path(temp_path).unlink()

    def test_validate_workflow_file_directory(self):
        """Test workflow file validation with directory instead of file."""
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            valid, errors = self.controller.validate_workflow_file(temp_dir)

            self.assertFalse(valid)
            self.assertTrue(any("not a file" in err.lower() for err in errors))


if __name__ == '__main__':
    unittest.main()
