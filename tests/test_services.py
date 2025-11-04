"""
Unit tests for MVC Services Layer (Phase 4).

Tests MVCConnectionService, MVCWorkflowService, and StatusService
with mocked dependencies.
"""

import unittest
from unittest.mock import Mock, MagicMock, patch, call
from pathlib import Path
import socket
from datetime import datetime
import time
import tempfile
import shutil
import json


class TestMVCConnectionService(unittest.TestCase):
    """Tests for MVCConnectionService."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock dependencies
        self.mock_tcp = Mock()
        self.mock_encoder = Mock()

        # Import and create service (direct import to avoid numpy dependency)
        from py2flamingo.services.connection_service import MVCConnectionService
        self.service = MVCConnectionService(
            tcp_connection=self.mock_tcp,
            encoder=self.mock_encoder
        )

    def test_init(self):
        """Test service initialization."""
        self.assertIsNotNone(self.service.tcp_connection)
        self.assertIsNotNone(self.service.encoder)
        self.assertIsNotNone(self.service.model)
        self.assertIsNotNone(self.service.logger)

    def test_connect_success(self):
        """Test successful connection."""
        from py2flamingo.models.connection import ConnectionConfig

        # Mock TCP connection to return sockets
        mock_cmd_sock = Mock()
        mock_live_sock = Mock()
        self.mock_tcp.connect.return_value = (mock_cmd_sock, mock_live_sock)

        # Create config
        config = ConnectionConfig("192.168.1.100", 53717)

        # Connect
        self.service.connect(config)

        # Verify connection was called
        self.mock_tcp.connect.assert_called_once_with(
            "192.168.1.100", 53717, timeout=2.0
        )

        # Verify status is CONNECTED
        self.assertTrue(self.service.is_connected())

    def test_connect_invalid_config(self):
        """Test connection with invalid config."""
        from py2flamingo.models.connection import ConnectionConfig

        # Invalid IP
        config = ConnectionConfig("invalid_ip", 53717)

        # Should raise ValueError
        with self.assertRaises(ValueError) as ctx:
            self.service.connect(config)

        self.assertIn("Invalid config", str(ctx.exception))

    def test_connect_already_connected(self):
        """Test connecting when already connected."""
        from py2flamingo.models.connection import ConnectionConfig, ConnectionState

        # Mock already connected
        self.service.model.status.state = ConnectionState.CONNECTED

        config = ConnectionConfig("192.168.1.100", 53717)

        # Should raise ConnectionError
        with self.assertRaises(ConnectionError) as ctx:
            self.service.connect(config)

        self.assertIn("Already connected", str(ctx.exception))

    def test_connect_timeout(self):
        """Test connection timeout."""
        from py2flamingo.models.connection import ConnectionConfig

        # Mock timeout
        self.mock_tcp.connect.side_effect = socket.timeout("Connection timed out")

        config = ConnectionConfig("192.168.1.100", 53717)

        # Should raise TimeoutError
        with self.assertRaises(TimeoutError) as ctx:
            self.service.connect(config)

        self.assertIn("Connection timeout", str(ctx.exception))

    def test_connect_network_error(self):
        """Test connection network error."""
        from py2flamingo.models.connection import ConnectionConfig

        # Mock connection error
        self.mock_tcp.connect.side_effect = socket.error("Connection refused")

        config = ConnectionConfig("192.168.1.100", 53717)

        # Should raise ConnectionError
        with self.assertRaises(ConnectionError) as ctx:
            self.service.connect(config)

        self.assertIn("Connection failed", str(ctx.exception))

    def test_disconnect_success(self):
        """Test successful disconnect."""
        from py2flamingo.models.connection import ConnectionConfig, ConnectionState

        # First connect
        config = ConnectionConfig("192.168.1.100", 53717)
        mock_cmd_sock = Mock()
        mock_live_sock = Mock()
        self.mock_tcp.connect.return_value = (mock_cmd_sock, mock_live_sock)
        self.service.connect(config)

        # Now disconnect
        self.service.disconnect()

        # Verify TCP disconnect was called
        self.mock_tcp.disconnect.assert_called_once()

        # Verify status is DISCONNECTED
        self.assertFalse(self.service.is_connected())

    def test_disconnect_not_connected(self):
        """Test disconnect when not connected."""
        # Should raise RuntimeError
        with self.assertRaises(RuntimeError) as ctx:
            self.service.disconnect()

        self.assertIn("Not connected", str(ctx.exception))

    def test_reconnect(self):
        """Test reconnect functionality."""
        from py2flamingo.models.connection import ConnectionConfig

        # Mock TCP connection
        mock_cmd_sock = Mock()
        mock_live_sock = Mock()
        self.mock_tcp.connect.return_value = (mock_cmd_sock, mock_live_sock)

        # First connect
        config1 = ConnectionConfig("192.168.1.100", 53717)
        self.service.connect(config1)

        # Reconnect with new config
        config2 = ConnectionConfig("192.168.1.101", 53718)
        self.service.reconnect(config2)

        # Verify disconnect then connect
        self.mock_tcp.disconnect.assert_called()
        self.assertEqual(self.mock_tcp.connect.call_count, 2)

    def test_is_connected_false(self):
        """Test is_connected when not connected."""
        self.assertFalse(self.service.is_connected())

    def test_send_command_success(self):
        """Test sending command successfully."""
        from py2flamingo.models.connection import ConnectionConfig, ConnectionState
        from py2flamingo.models.command import Command

        # Connect first
        config = ConnectionConfig("192.168.1.100", 53717)
        mock_cmd_sock = Mock()
        mock_live_sock = Mock()
        self.mock_tcp.connect.return_value = (mock_cmd_sock, mock_live_sock)
        self.service.connect(config)

        # Mock encoder
        encoded_cmd = b'\x00' * 128
        self.mock_encoder.encode_command.return_value = encoded_cmd

        # Mock socket response
        mock_cmd_sock.recv.return_value = b'\x01' * 128

        # Create and send command
        cmd = Command(code=12292)
        response = self.service.send_command(cmd)

        # Verify encoding
        self.mock_encoder.encode_command.assert_called_once()

        # Verify send
        mock_cmd_sock.sendall.assert_called_once_with(encoded_cmd)

        # Verify response
        self.assertEqual(len(response), 128)

    def test_send_command_not_connected(self):
        """Test sending command when not connected."""
        from py2flamingo.models.command import Command

        cmd = Command(code=12292)

        # Should raise RuntimeError
        with self.assertRaises(RuntimeError) as ctx:
            self.service.send_command(cmd)

        self.assertIn("Not connected", str(ctx.exception))

    def test_send_command_network_error(self):
        """Test sending command with network error."""
        from py2flamingo.models.connection import ConnectionConfig
        from py2flamingo.models.command import Command

        # Connect first
        config = ConnectionConfig("192.168.1.100", 53717)
        mock_cmd_sock = Mock()
        mock_live_sock = Mock()
        self.mock_tcp.connect.return_value = (mock_cmd_sock, mock_live_sock)
        self.service.connect(config)

        # Mock socket error
        mock_cmd_sock.sendall.side_effect = socket.error("Connection lost")

        # Create and send command
        cmd = Command(code=12292)

        # Should raise ConnectionError
        with self.assertRaises(ConnectionError) as ctx:
            self.service.send_command(cmd)

        self.assertIn("Failed to send command", str(ctx.exception))

    def test_get_status(self):
        """Test get_status method."""
        status = self.service.get_status()
        self.assertIsNotNone(status)
        self.assertIsNotNone(status.state)


class TestMVCWorkflowService(unittest.TestCase):
    """Tests for MVCWorkflowService."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock connection service
        self.mock_conn_service = Mock()

        # Import and create service (direct import to avoid numpy dependency)
        from py2flamingo.services.workflow_service import MVCWorkflowService
        self.service = MVCWorkflowService(
            connection_service=self.mock_conn_service
        )

        # Create temp workflow file
        self.temp_workflow = Path("/tmp/test_workflow.txt")
        self.temp_workflow.write_text("Test workflow content\n" * 10)

    def tearDown(self):
        """Clean up temp files."""
        if self.temp_workflow.exists():
            self.temp_workflow.unlink()

    def test_init(self):
        """Test service initialization."""
        self.assertIsNotNone(self.service.connection_service)
        self.assertIsNotNone(self.service.logger)

    def test_load_workflow_success(self):
        """Test loading workflow file."""
        workflow_bytes = self.service.load_workflow(self.temp_workflow)

        self.assertIsInstance(workflow_bytes, bytes)
        self.assertGreater(len(workflow_bytes), 0)

    def test_load_workflow_not_found(self):
        """Test loading non-existent workflow."""
        non_existent = Path("/tmp/does_not_exist.txt")

        with self.assertRaises(FileNotFoundError) as ctx:
            self.service.load_workflow(non_existent)

        self.assertIn("Workflow file not found", str(ctx.exception))

    def test_load_workflow_too_large(self):
        """Test loading workflow that's too large."""
        # Create large file (>10MB)
        large_file = Path("/tmp/large_workflow.txt")
        try:
            # Write 11MB of data
            with open(large_file, 'wb') as f:
                f.write(b'x' * (11 * 1024 * 1024))

            with self.assertRaises(ValueError) as ctx:
                self.service.load_workflow(large_file)

            self.assertIn("too large", str(ctx.exception))

        finally:
            if large_file.exists():
                large_file.unlink()

    def test_start_workflow_success(self):
        """Test starting workflow."""
        # Mock connected
        self.mock_conn_service.is_connected.return_value = True
        self.mock_conn_service.send_command.return_value = b'\x00' * 128

        workflow_data = b"Test workflow data"

        result = self.service.start_workflow(workflow_data)

        self.assertTrue(result)
        self.mock_conn_service.send_command.assert_called_once()

    def test_start_workflow_not_connected(self):
        """Test starting workflow when not connected."""
        # Mock not connected
        self.mock_conn_service.is_connected.return_value = False

        workflow_data = b"Test workflow data"

        with self.assertRaises(RuntimeError) as ctx:
            self.service.start_workflow(workflow_data)

        self.assertIn("Not connected", str(ctx.exception))

    def test_start_workflow_send_error(self):
        """Test starting workflow with send error."""
        # Mock connected
        self.mock_conn_service.is_connected.return_value = True
        self.mock_conn_service.send_command.side_effect = ConnectionError("Send failed")

        workflow_data = b"Test workflow data"

        with self.assertRaises(ConnectionError):
            self.service.start_workflow(workflow_data)

    def test_stop_workflow_success(self):
        """Test stopping workflow."""
        # Mock connected
        self.mock_conn_service.is_connected.return_value = True
        self.mock_conn_service.send_command.return_value = b'\x00' * 128

        result = self.service.stop_workflow()

        self.assertTrue(result)
        self.mock_conn_service.send_command.assert_called_once()

    def test_stop_workflow_not_connected(self):
        """Test stopping workflow when not connected."""
        # Mock not connected
        self.mock_conn_service.is_connected.return_value = False

        with self.assertRaises(RuntimeError) as ctx:
            self.service.stop_workflow()

        self.assertIn("Not connected", str(ctx.exception))

    def test_get_workflow_status(self):
        """Test getting workflow status."""
        # Mock connected
        self.mock_conn_service.is_connected.return_value = True
        self.mock_conn_service.send_command.return_value = b'\x00' * 128

        status = self.service.get_workflow_status()

        self.assertIsInstance(status, str)
        self.mock_conn_service.send_command.assert_called_once()


class TestStatusService(unittest.TestCase):
    """Tests for StatusService."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock connection service
        self.mock_conn_service = Mock()

        # Import and create service (direct import to avoid numpy dependency)
        from py2flamingo.services.status_service import StatusService
        self.service = StatusService(
            connection_service=self.mock_conn_service
        )

    def test_init(self):
        """Test service initialization."""
        self.assertIsNotNone(self.service.connection_service)
        self.assertIsNotNone(self.service.logger)
        self.assertEqual(self.service._cache_ttl, 1.0)

    def test_get_server_status_success(self):
        """Test getting server status."""
        # Mock connected
        self.mock_conn_service.is_connected.return_value = True
        self.mock_conn_service.send_command.return_value = b'\x00' * 128

        status = self.service.get_server_status()

        self.assertIsInstance(status, dict)
        self.assertIn('state', status)
        self.assertIn('response_size', status)
        self.assertIn('timestamp', status)

    def test_get_server_status_not_connected(self):
        """Test getting status when not connected."""
        # Mock not connected
        self.mock_conn_service.is_connected.return_value = False

        with self.assertRaises(RuntimeError) as ctx:
            self.service.get_server_status()

        self.assertIn("Not connected", str(ctx.exception))

    def test_get_server_status_cached(self):
        """Test cache functionality."""
        # Mock connected
        self.mock_conn_service.is_connected.return_value = True
        self.mock_conn_service.send_command.return_value = b'\x00' * 128

        # First call - should query
        status1 = self.service.get_server_status()

        # Second call immediately - should use cache
        status2 = self.service.get_server_status()

        # Should only call send_command once
        self.assertEqual(self.mock_conn_service.send_command.call_count, 1)

        # Results should be same
        self.assertEqual(status1, status2)

    def test_get_server_status_cache_expired(self):
        """Test cache expiration."""
        # Mock connected
        self.mock_conn_service.is_connected.return_value = True
        self.mock_conn_service.send_command.return_value = b'\x00' * 128

        # Set short TTL
        self.service._cache_ttl = 0.1

        # First call
        status1 = self.service.get_server_status()

        # Wait for cache to expire
        time.sleep(0.15)

        # Second call - should query again
        status2 = self.service.get_server_status()

        # Should call send_command twice
        self.assertEqual(self.mock_conn_service.send_command.call_count, 2)

    def test_ping_success(self):
        """Test successful ping."""
        # Mock connected
        self.mock_conn_service.is_connected.return_value = True
        self.mock_conn_service.send_command.return_value = b'\x00' * 128

        result = self.service.ping()

        self.assertTrue(result)

    def test_ping_not_connected(self):
        """Test ping when not connected."""
        # Mock not connected
        self.mock_conn_service.is_connected.return_value = False

        with self.assertRaises(RuntimeError):
            self.service.ping()

    def test_ping_connection_error(self):
        """Test ping with connection error."""
        # Mock connected
        self.mock_conn_service.is_connected.return_value = True
        self.mock_conn_service.send_command.side_effect = ConnectionError("Lost connection")

        result = self.service.ping()

        self.assertFalse(result)

    def test_ping_timeout(self):
        """Test ping with timeout."""
        # Mock connected
        self.mock_conn_service.is_connected.return_value = True
        self.mock_conn_service.send_command.side_effect = TimeoutError("Query timed out")

        result = self.service.ping()

        self.assertFalse(result)

    def test_get_position_success(self):
        """Test getting position."""
        # Mock connected
        self.mock_conn_service.is_connected.return_value = True
        self.mock_conn_service.send_command.return_value = b'\x00' * 128

        position = self.service.get_position()

        self.assertIsInstance(position, tuple)
        self.assertEqual(len(position), 3)

    def test_get_position_not_connected(self):
        """Test getting position when not connected."""
        # Mock not connected
        self.mock_conn_service.is_connected.return_value = False

        with self.assertRaises(RuntimeError) as ctx:
            self.service.get_position()

        self.assertIn("Not connected", str(ctx.exception))

    def test_get_position_empty_response(self):
        """Test getting position with empty response."""
        # Mock connected
        self.mock_conn_service.is_connected.return_value = True
        self.mock_conn_service.send_command.return_value = b''

        with self.assertRaises(ValueError) as ctx:
            self.service.get_position()

        self.assertIn("Empty response", str(ctx.exception))

    def test_clear_cache(self):
        """Test clearing cache."""
        # Mock connected
        self.mock_conn_service.is_connected.return_value = True
        self.mock_conn_service.send_command.return_value = b'\x00' * 128

        # Populate cache
        self.service.get_server_status()

        # Clear cache
        self.service.clear_cache()

        # Next call should query again
        self.service.get_server_status()

        # Should have called send_command twice
        self.assertEqual(self.mock_conn_service.send_command.call_count, 2)


import shutil
from pathlib import Path
import json


class TestConfigurationManager(unittest.TestCase):
    """Tests for JSON-based ConfigurationManager service."""

    def setUp(self):
        """Set up test fixtures."""
        # Create temp directory for test config file
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = Path(self.temp_dir) / "test_configurations.json"

        # Import and create service
        from py2flamingo.services.configuration_manager import ConfigurationManager
        self.manager = ConfigurationManager(config_file=str(self.config_file))

    def tearDown(self):
        """Clean up temp directory."""
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)

    def test_init(self):
        """Test ConfigurationManager initialization."""
        self.assertIsNotNone(self.manager)
        self.assertEqual(str(self.manager.config_file), str(self.config_file))

    def test_init_creates_empty_configs(self):
        """Test that initialization with no file creates empty configuration set."""
        configs = self.manager.discover_configurations()
        self.assertEqual(len(configs), 0)

    def test_save_configuration(self):
        """Test saving a new configuration."""
        success, message = self.manager.save_configuration("TestMicroscope", "192.168.1.100", 53717)

        self.assertTrue(success)
        self.assertIn("saved successfully", message.lower())

        # Verify it was saved
        configs = self.manager.discover_configurations()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].name, "TestMicroscope")
        self.assertEqual(configs[0].ip_address, "192.168.1.100")
        self.assertEqual(configs[0].port, 53717)

    def test_save_configuration_creates_json_file(self):
        """Test that saving creates the JSON file."""
        self.assertFalse(self.config_file.exists())

        self.manager.save_configuration("Test", "127.0.0.1", 53717)

        self.assertTrue(self.config_file.exists())

    def test_save_duplicate_configuration_fails(self):
        """Test that saving duplicate configuration name fails."""
        self.manager.save_configuration("Test", "192.168.1.1", 53717)

        success, message = self.manager.save_configuration("Test", "192.168.1.2", 53718)

        self.assertFalse(success)
        self.assertIn("already exists", message.lower())

    def test_save_invalid_ip(self):
        """Test saving with invalid IP address."""
        success, message = self.manager.save_configuration("Test", "invalid.ip", 53717)

        self.assertFalse(success)
        self.assertIn("invalid", message.lower())

    def test_save_invalid_port(self):
        """Test saving with invalid port number."""
        success, message = self.manager.save_configuration("Test", "192.168.1.1", 99999)

        self.assertFalse(success)
        self.assertIn("invalid", message.lower())

    def test_get_configuration(self):
        """Test getting configuration by name."""
        self.manager.save_configuration("Zion", "10.129.37.22", 53717)

        config = self.manager.get_configuration("Zion")

        self.assertIsNotNone(config)
        self.assertEqual(config.name, "Zion")
        self.assertEqual(config.ip_address, "10.129.37.22")
        self.assertEqual(config.port, 53717)

    def test_get_configuration_not_found(self):
        """Test getting non-existent configuration."""
        config = self.manager.get_configuration("NonExistent")

        self.assertIsNone(config)

    def test_get_configuration_names(self):
        """Test getting list of configuration names."""
        self.manager.save_configuration("Zion", "10.129.37.22", 53717)
        self.manager.save_configuration("Alpha", "192.168.1.100", 53717)

        names = self.manager.get_configuration_names()

        self.assertEqual(len(names), 2)
        self.assertIn("Zion", names)
        self.assertIn("Alpha", names)

    def test_get_configuration_names_sorted(self):
        """Test that configuration names are returned sorted."""
        self.manager.save_configuration("Zion", "10.129.37.22", 53717)
        self.manager.save_configuration("Alpha", "192.168.1.100", 53717)
        self.manager.save_configuration("Beta", "192.168.1.101", 53718)

        names = self.manager.get_configuration_names()

        self.assertEqual(names, ["Alpha", "Beta", "Zion"])

    def test_delete_configuration(self):
        """Test deleting a configuration."""
        self.manager.save_configuration("ToDelete", "192.168.1.1", 53717)

        success, message = self.manager.delete_configuration("ToDelete")

        self.assertTrue(success)
        self.assertIn("deleted successfully", message.lower())

        # Verify it was deleted
        config = self.manager.get_configuration("ToDelete")
        self.assertIsNone(config)

    def test_delete_nonexistent_configuration(self):
        """Test deleting a non-existent configuration."""
        success, message = self.manager.delete_configuration("NonExistent")

        self.assertFalse(success)
        self.assertIn("not found", message.lower())

    def test_refresh_reloads_from_json(self):
        """Test that refresh reloads configurations from JSON file."""
        # Save a configuration
        self.manager.save_configuration("Test", "192.168.1.1", 53717)

        # Manually modify the JSON file
        with open(self.config_file, 'r') as f:
            data = json.load(f)

        data['configurations'].append({
            'name': 'ManuallyAdded',
            'ip_address': '192.168.1.2',
            'port': 53718,
            'description': ''
        })

        with open(self.config_file, 'w') as f:
            json.dump(data, f)

        # Refresh should pick up the manual change
        configs = self.manager.refresh()

        names = [c.name for c in configs]
        self.assertIn("ManuallyAdded", names)

    def test_get_default_configuration(self):
        """Test getting default configuration."""
        self.manager.save_configuration("Zion", "10.129.37.22", 53717)
        self.manager.save_configuration("Alpha", "192.168.1.100", 53717)

        default = self.manager.get_default_configuration()

        # Should return first alphabetically
        self.assertIsNotNone(default)
        self.assertEqual(default.name, "Alpha")

    def test_get_default_configuration_empty(self):
        """Test getting default configuration when no configs exist."""
        default = self.manager.get_default_configuration()

        self.assertIsNone(default)

    def test_configuration_to_connection_config(self):
        """Test converting MicroscopeConfiguration to ConnectionConfig."""
        self.manager.save_configuration("Test", "192.168.1.1", 53717)
        config = self.manager.get_configuration("Test")

        conn_config = config.to_connection_config()

        self.assertEqual(conn_config.ip_address, "192.168.1.1")
        self.assertEqual(conn_config.port, 53717)
        self.assertEqual(conn_config.live_port, 53718)  # port + 1

    def test_configuration_str_representation(self):
        """Test string representation of configuration."""
        self.manager.save_configuration("Test", "192.168.1.1", 53717)
        config = self.manager.get_configuration("Test")

        string_rep = str(config)

        self.assertIn("Test", string_rep)
        self.assertIn("192.168.1.1", string_rep)
        self.assertIn("53717", string_rep)
class TestServiceIntegration(unittest.TestCase):
    """Integration tests for services working together."""

    def test_workflow_service_uses_connection_service(self):
        """Test workflow service uses connection service correctly."""
        from py2flamingo.services.connection_service import MVCConnectionService
        from py2flamingo.services.workflow_service import MVCWorkflowService

        # Create mocks
        mock_tcp = Mock()
        mock_encoder = Mock()

        # Create services
        conn_service = MVCConnectionService(mock_tcp, mock_encoder)
        workflow_service = MVCWorkflowService(conn_service)

        # Verify workflow service has connection service
        self.assertIs(workflow_service.connection_service, conn_service)

    def test_status_service_uses_connection_service(self):
        """Test status service uses connection service correctly."""
        from py2flamingo.services.connection_service import MVCConnectionService
        from py2flamingo.services.status_service import StatusService

        # Create mocks
        mock_tcp = Mock()
        mock_encoder = Mock()

        # Create services
        conn_service = MVCConnectionService(mock_tcp, mock_encoder)
        status_service = StatusService(conn_service)

        # Verify status service has connection service
        self.assertIs(status_service.connection_service, conn_service)

    def test_services_share_connection_state(self):
        """Test that services share connection state via connection service."""
        from py2flamingo.services.connection_service import MVCConnectionService
        from py2flamingo.services.workflow_service import MVCWorkflowService
        from py2flamingo.services.status_service import StatusService
        from py2flamingo.models.connection import ConnectionConfig

        # Create mocks
        mock_tcp = Mock()
        mock_encoder = Mock()
        mock_cmd_sock = Mock()
        mock_live_sock = Mock()
        mock_tcp.connect.return_value = (mock_cmd_sock, mock_live_sock)

        # Create services
        conn_service = MVCConnectionService(mock_tcp, mock_encoder)
        workflow_service = MVCWorkflowService(conn_service)
        status_service = StatusService(conn_service)

        # Connect
        config = ConnectionConfig("192.168.1.100", 53717)
        conn_service.connect(config)

        # All services should see connected state
        self.assertTrue(workflow_service.connection_service.is_connected())
        self.assertTrue(status_service.connection_service.is_connected())


if __name__ == '__main__':
    unittest.main()
