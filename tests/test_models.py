"""
Unit tests for py2flamingo models (connection and command).

Test coverage:
    - ConnectionConfig: validation, IP format, port ranges
    - ConnectionState: enum values
    - ConnectionStatus: dataclass creation
    - ConnectionModel: observer pattern, state changes
    - Command: base class, serialization
    - WorkflowCommand: inheritance, workflow data
    - StatusCommand: query type handling
    - PositionCommand: coordinate handling
"""

import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from py2flamingo.models.connection import (
    ConnectionConfig,
    ConnectionState,
    ConnectionStatus,
    ConnectionModel
)
from py2flamingo.models.command import (
    Command,
    WorkflowCommand,
    StatusCommand,
    PositionCommand
)


class TestConnectionConfig(unittest.TestCase):
    """Tests for ConnectionConfig dataclass."""

    def test_create_with_defaults(self):
        """Test creating config with default values."""
        config = ConnectionConfig("192.168.1.100", 53717)
        self.assertEqual(config.ip_address, "192.168.1.100")
        self.assertEqual(config.port, 53717)
        self.assertEqual(config.live_port, 53718)  # port + 1
        self.assertEqual(config.timeout, 2.0)

    def test_create_with_explicit_live_port(self):
        """Test creating config with explicit live port."""
        config = ConnectionConfig("192.168.1.100", 53717, 53720)
        self.assertEqual(config.port, 53717)
        self.assertEqual(config.live_port, 53720)

    def test_create_with_custom_timeout(self):
        """Test creating config with custom timeout."""
        config = ConnectionConfig("192.168.1.100", 53717, 53718, 5.0)
        self.assertEqual(config.timeout, 5.0)

    def test_validate_valid_config(self):
        """Test validation passes for valid config."""
        config = ConnectionConfig("192.168.1.100", 53717, 53718, 2.0)
        valid, errors = config.validate()
        self.assertTrue(valid)
        self.assertEqual(len(errors), 0)

    def test_validate_invalid_ip_format(self):
        """Test validation catches invalid IP format."""
        # Invalid IP - not numeric
        config = ConnectionConfig("not-an-ip", 53717)
        valid, errors = config.validate()
        self.assertFalse(valid)
        self.assertIn("Invalid IP address format", errors[0])

    def test_validate_invalid_ip_octets(self):
        """Test validation catches IP with invalid octets."""
        # Invalid IP - octet > 255
        config = ConnectionConfig("192.168.1.300", 53717)
        valid, errors = config.validate()
        self.assertFalse(valid)
        self.assertIn("Invalid IP address format", errors[0])

    def test_validate_port_too_low(self):
        """Test validation catches port < 1."""
        config = ConnectionConfig("192.168.1.100", 0)
        valid, errors = config.validate()
        self.assertFalse(valid)
        self.assertTrue(any("Port out of range" in e for e in errors))

    def test_validate_port_too_high(self):
        """Test validation catches port > 65535."""
        config = ConnectionConfig("192.168.1.100", 99999)
        valid, errors = config.validate()
        self.assertFalse(valid)
        self.assertTrue(any("Port out of range" in e for e in errors))

    def test_validate_live_port_out_of_range(self):
        """Test validation catches invalid live port."""
        config = ConnectionConfig("192.168.1.100", 53717, 99999)
        valid, errors = config.validate()
        self.assertFalse(valid)
        self.assertTrue(any("Live port out of range" in e for e in errors))

    def test_validate_ports_same(self):
        """Test validation catches when ports are the same."""
        config = ConnectionConfig("192.168.1.100", 53717, 53717)
        valid, errors = config.validate()
        self.assertFalse(valid)
        self.assertTrue(any("must be different" in e for e in errors))

    def test_validate_negative_timeout(self):
        """Test validation catches negative timeout."""
        config = ConnectionConfig("192.168.1.100", 53717, 53718, -1.0)
        valid, errors = config.validate()
        self.assertFalse(valid)
        self.assertTrue(any("Timeout must be positive" in e for e in errors))

    def test_validate_zero_timeout(self):
        """Test validation catches zero timeout."""
        config = ConnectionConfig("192.168.1.100", 53717, 53718, 0.0)
        valid, errors = config.validate()
        self.assertFalse(valid)
        self.assertTrue(any("Timeout must be positive" in e for e in errors))

    def test_validate_multiple_errors(self):
        """Test validation returns all errors at once."""
        config = ConnectionConfig("invalid", 99999, 99999, -1.0)
        valid, errors = config.validate()
        self.assertFalse(valid)
        # Should have at least: IP, port, live_port, timeout errors
        self.assertGreaterEqual(len(errors), 4)

    def test_config_is_immutable(self):
        """Test that ConnectionConfig is frozen (immutable)."""
        config = ConnectionConfig("192.168.1.100", 53717)
        with self.assertRaises(AttributeError):
            config.ip_address = "192.168.1.200"

    def test_valid_localhost_ip(self):
        """Test validation accepts localhost IP."""
        config = ConnectionConfig("127.0.0.1", 53717)
        valid, errors = config.validate()
        self.assertTrue(valid)

    def test_valid_zero_ip(self):
        """Test validation accepts 0.0.0.0."""
        config = ConnectionConfig("0.0.0.0", 53717)
        valid, errors = config.validate()
        self.assertTrue(valid)


class TestConnectionState(unittest.TestCase):
    """Tests for ConnectionState enum."""

    def test_state_values(self):
        """Test all state values exist."""
        self.assertEqual(ConnectionState.DISCONNECTED.value, "disconnected")
        self.assertEqual(ConnectionState.CONNECTING.value, "connecting")
        self.assertEqual(ConnectionState.CONNECTED.value, "connected")
        self.assertEqual(ConnectionState.ERROR.value, "error")

    def test_state_comparison(self):
        """Test state enum comparison."""
        state1 = ConnectionState.CONNECTED
        state2 = ConnectionState.CONNECTED
        state3 = ConnectionState.DISCONNECTED
        self.assertEqual(state1, state2)
        self.assertNotEqual(state1, state3)


class TestConnectionStatus(unittest.TestCase):
    """Tests for ConnectionStatus dataclass."""

    def test_create_disconnected_status(self):
        """Test creating disconnected status."""
        status = ConnectionStatus(state=ConnectionState.DISCONNECTED)
        self.assertEqual(status.state, ConnectionState.DISCONNECTED)
        self.assertIsNone(status.ip)
        self.assertIsNone(status.port)
        self.assertIsNone(status.connected_at)
        self.assertIsNone(status.last_error)

    def test_create_connected_status(self):
        """Test creating connected status with full info."""
        now = datetime.now()
        status = ConnectionStatus(
            state=ConnectionState.CONNECTED,
            ip="192.168.1.100",
            port=53717,
            connected_at=now
        )
        self.assertEqual(status.state, ConnectionState.CONNECTED)
        self.assertEqual(status.ip, "192.168.1.100")
        self.assertEqual(status.port, 53717)
        self.assertEqual(status.connected_at, now)
        self.assertIsNone(status.last_error)

    def test_create_error_status(self):
        """Test creating error status with error message."""
        status = ConnectionStatus(
            state=ConnectionState.ERROR,
            last_error="Connection timeout"
        )
        self.assertEqual(status.state, ConnectionState.ERROR)
        self.assertEqual(status.last_error, "Connection timeout")


class TestConnectionModel(unittest.TestCase):
    """Tests for ConnectionModel observer pattern."""

    def test_initial_state(self):
        """Test model starts in disconnected state."""
        model = ConnectionModel()
        self.assertEqual(model.status.state, ConnectionState.DISCONNECTED)

    def test_set_status(self):
        """Test setting status updates model."""
        model = ConnectionModel()
        new_status = ConnectionStatus(
            state=ConnectionState.CONNECTED,
            ip="192.168.1.100",
            port=53717
        )
        model.status = new_status
        self.assertEqual(model.status.state, ConnectionState.CONNECTED)
        self.assertEqual(model.status.ip, "192.168.1.100")

    def test_add_observer(self):
        """Test adding an observer."""
        model = ConnectionModel()
        observer = Mock()
        model.add_observer(observer)
        self.assertIn(observer, model._observers)

    def test_observer_called_on_status_change(self):
        """Test observer is called when status changes."""
        model = ConnectionModel()
        observer = Mock()
        model.add_observer(observer)

        new_status = ConnectionStatus(state=ConnectionState.CONNECTING)
        model.status = new_status

        observer.assert_called_once_with(new_status)

    def test_multiple_observers(self):
        """Test multiple observers are all notified."""
        model = ConnectionModel()
        observer1 = Mock()
        observer2 = Mock()
        observer3 = Mock()

        model.add_observer(observer1)
        model.add_observer(observer2)
        model.add_observer(observer3)

        new_status = ConnectionStatus(state=ConnectionState.CONNECTED)
        model.status = new_status

        observer1.assert_called_once_with(new_status)
        observer2.assert_called_once_with(new_status)
        observer3.assert_called_once_with(new_status)

    def test_remove_observer(self):
        """Test removing an observer."""
        model = ConnectionModel()
        observer = Mock()
        model.add_observer(observer)
        model.remove_observer(observer)
        self.assertNotIn(observer, model._observers)

    def test_removed_observer_not_called(self):
        """Test removed observer is not called on status change."""
        model = ConnectionModel()
        observer = Mock()
        model.add_observer(observer)
        model.remove_observer(observer)

        new_status = ConnectionStatus(state=ConnectionState.CONNECTED)
        model.status = new_status

        observer.assert_not_called()

    def test_remove_nonexistent_observer(self):
        """Test removing observer that was never added (should not error)."""
        model = ConnectionModel()
        observer = Mock()
        # Should not raise exception
        model.remove_observer(observer)

    def test_add_duplicate_observer(self):
        """Test adding same observer twice only adds once."""
        model = ConnectionModel()
        observer = Mock()
        model.add_observer(observer)
        model.add_observer(observer)
        self.assertEqual(model._observers.count(observer), 1)

    def test_observer_receives_correct_status(self):
        """Test observer receives the actual status object."""
        model = ConnectionModel()
        received_status = []

        def observer(status):
            received_status.append(status)

        model.add_observer(observer)

        status1 = ConnectionStatus(state=ConnectionState.CONNECTING)
        model.status = status1
        self.assertEqual(received_status[0], status1)

        status2 = ConnectionStatus(state=ConnectionState.CONNECTED)
        model.status = status2
        self.assertEqual(received_status[1], status2)


class TestCommand(unittest.TestCase):
    """Tests for Command base class."""

    def test_create_simple_command(self):
        """Test creating a simple command."""
        cmd = Command(code=12292)
        self.assertEqual(cmd.code, 12292)
        self.assertIsInstance(cmd.timestamp, datetime)
        self.assertEqual(len(cmd.parameters), 0)

    def test_create_command_with_parameters(self):
        """Test creating command with parameters."""
        params = {'timeout': 5.0, 'retry': 3}
        cmd = Command(code=12292, parameters=params)
        self.assertEqual(cmd.parameters['timeout'], 5.0)
        self.assertEqual(cmd.parameters['retry'], 3)

    def test_command_to_dict(self):
        """Test command serialization to dict."""
        cmd = Command(code=12292, parameters={'test': 'value'})
        result = cmd.to_dict()

        self.assertEqual(result['code'], 12292)
        self.assertIn('timestamp', result)
        self.assertEqual(result['parameters']['test'], 'value')
        self.assertEqual(result['type'], 'Command')

    def test_timestamp_is_set(self):
        """Test that timestamp is automatically set."""
        before = datetime.now()
        cmd = Command(code=12292)
        after = datetime.now()

        self.assertGreaterEqual(cmd.timestamp, before)
        self.assertLessEqual(cmd.timestamp, after)


class TestWorkflowCommand(unittest.TestCase):
    """Tests for WorkflowCommand class."""

    def test_create_workflow_command(self):
        """Test creating a workflow command."""
        path = Path("workflows/Zstack.txt")
        cmd = WorkflowCommand(code=12292, workflow_path=path)
        self.assertEqual(cmd.code, 12292)
        self.assertEqual(cmd.workflow_path, path)
        self.assertIsNone(cmd.workflow_data)

    def test_create_with_workflow_data(self):
        """Test creating workflow command with data."""
        path = Path("workflows/Zstack.txt")
        data = b"workflow content"
        cmd = WorkflowCommand(code=12292, workflow_path=path, workflow_data=data)
        self.assertEqual(cmd.workflow_data, data)

    def test_workflow_command_to_dict(self):
        """Test workflow command serialization."""
        path = Path("workflows/Zstack.txt")
        data = b"workflow content"
        cmd = WorkflowCommand(code=12292, workflow_path=path, workflow_data=data)
        result = cmd.to_dict()

        self.assertEqual(result['code'], 12292)
        self.assertEqual(result['workflow_path'], str(path))
        self.assertEqual(result['workflow_size'], len(data))
        self.assertEqual(result['type'], 'WorkflowCommand')

    def test_workflow_command_inherits_from_command(self):
        """Test that WorkflowCommand is a Command."""
        cmd = WorkflowCommand(code=12292)
        self.assertIsInstance(cmd, Command)


class TestStatusCommand(unittest.TestCase):
    """Tests for StatusCommand class."""

    def test_create_status_command(self):
        """Test creating a status command."""
        cmd = StatusCommand(code=40967)
        self.assertEqual(cmd.code, 40967)
        self.assertEqual(cmd.query_type, "system_state")  # default

    def test_create_with_custom_query_type(self):
        """Test creating status command with custom query type."""
        cmd = StatusCommand(code=40967, query_type="position")
        self.assertEqual(cmd.query_type, "position")

    def test_status_command_to_dict(self):
        """Test status command serialization."""
        cmd = StatusCommand(code=40967, query_type="position")
        result = cmd.to_dict()

        self.assertEqual(result['code'], 40967)
        self.assertEqual(result['query_type'], "position")
        self.assertEqual(result['type'], 'StatusCommand')

    def test_status_command_inherits_from_command(self):
        """Test that StatusCommand is a Command."""
        cmd = StatusCommand(code=40967)
        self.assertIsInstance(cmd, Command)


class TestPositionCommand(unittest.TestCase):
    """Tests for PositionCommand class."""

    def test_create_position_command_defaults(self):
        """Test creating position command with default coordinates."""
        cmd = PositionCommand(code=24580)
        self.assertEqual(cmd.code, 24580)
        self.assertEqual(cmd.x, 0.0)
        self.assertEqual(cmd.y, 0.0)
        self.assertEqual(cmd.z, 0.0)

    def test_create_position_command_with_coordinates(self):
        """Test creating position command with coordinates."""
        cmd = PositionCommand(code=24580, x=100.5, y=200.3, z=50.0)
        self.assertEqual(cmd.x, 100.5)
        self.assertEqual(cmd.y, 200.3)
        self.assertEqual(cmd.z, 50.0)

    def test_position_command_to_dict(self):
        """Test position command serialization."""
        cmd = PositionCommand(code=24580, x=10.0, y=20.0, z=30.0)
        result = cmd.to_dict()

        self.assertEqual(result['code'], 24580)
        self.assertEqual(result['x'], 10.0)
        self.assertEqual(result['y'], 20.0)
        self.assertEqual(result['z'], 30.0)
        self.assertEqual(result['type'], 'PositionCommand')

    def test_position_command_inherits_from_command(self):
        """Test that PositionCommand is a Command."""
        cmd = PositionCommand(code=24580)
        self.assertIsInstance(cmd, Command)

    def test_negative_coordinates(self):
        """Test position command with negative coordinates."""
        cmd = PositionCommand(code=24580, x=-10.5, y=-20.3, z=-30.0)
        self.assertEqual(cmd.x, -10.5)
        self.assertEqual(cmd.y, -20.3)
        self.assertEqual(cmd.z, -30.0)


class TestCommandInheritance(unittest.TestCase):
    """Tests for command class inheritance."""

    def test_all_commands_have_timestamp(self):
        """Test all command types have timestamps."""
        cmd1 = Command(code=1)
        cmd2 = WorkflowCommand(code=2)
        cmd3 = StatusCommand(code=3)
        cmd4 = PositionCommand(code=4)

        for cmd in [cmd1, cmd2, cmd3, cmd4]:
            self.assertIsInstance(cmd.timestamp, datetime)

    def test_all_commands_have_parameters(self):
        """Test all command types have parameters dict."""
        cmd1 = Command(code=1)
        cmd2 = WorkflowCommand(code=2)
        cmd3 = StatusCommand(code=3)
        cmd4 = PositionCommand(code=4)

        for cmd in [cmd1, cmd2, cmd3, cmd4]:
            self.assertIsInstance(cmd.parameters, dict)

    def test_all_commands_can_serialize(self):
        """Test all command types can serialize to dict."""
        cmd1 = Command(code=1)
        cmd2 = WorkflowCommand(code=2)
        cmd3 = StatusCommand(code=3)
        cmd4 = PositionCommand(code=4)

        for cmd in [cmd1, cmd2, cmd3, cmd4]:
            result = cmd.to_dict()
            self.assertIn('code', result)
            self.assertIn('timestamp', result)
            self.assertIn('type', result)


if __name__ == '__main__':
    unittest.main()
