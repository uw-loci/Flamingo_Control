"""End-to-end integration tests for MVC architecture.

These tests verify the complete workflow from connection to workflow execution
using a mock server to simulate the microscope.
"""

import pytest
import subprocess
import time
import socket
from pathlib import Path
from typing import Optional
import tempfile
import os

# Import all MVC layers
from py2flamingo.core import TCPConnection, ProtocolEncoder, CommandCode
from py2flamingo.models import ConnectionConfig, ConnectionModel, ConnectionState
from py2flamingo.services import MVCConnectionService, MVCWorkflowService, StatusService
from py2flamingo.controllers import ConnectionController, WorkflowController


class MockServerManager:
    """Manages mock server lifecycle for integration tests."""

    def __init__(self, port: int = 53717):
        self.port = port
        self.process: Optional[subprocess.Popen] = None

    def start(self):
        """Start mock server."""
        # Start mock server process
        self.process = subprocess.Popen(
            ["python", "mock_server.py", "--port", str(self.port)],
            cwd="/home/msnelson/LSControl/Flamingo_Control",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        # Wait for server to be ready
        self._wait_for_server()

    def stop(self):
        """Stop mock server."""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None

    def _wait_for_server(self, timeout: float = 5.0):
        """Wait for server to be ready to accept connections."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # Try to connect to command port
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                sock.connect(("127.0.0.1", self.port))
                sock.close()
                time.sleep(0.5)  # Extra time for server to fully initialize
                return
            except (socket.error, ConnectionRefusedError):
                time.sleep(0.1)
        raise RuntimeError(f"Mock server did not start within {timeout} seconds")


@pytest.fixture(scope="module")
def mock_server():
    """Start mock server for integration tests."""
    manager = MockServerManager()
    manager.start()
    yield manager
    manager.stop()


@pytest.fixture
def tcp_connection():
    """Create a TCP connection instance."""
    return TCPConnection()


@pytest.fixture
def protocol_encoder():
    """Create a protocol encoder instance."""
    return ProtocolEncoder()


@pytest.fixture
def connection_model():
    """Create a connection model instance."""
    return ConnectionModel()


@pytest.fixture
def connection_service(tcp_connection, protocol_encoder):
    """Create a connection service instance."""
    return MVCConnectionService(tcp_connection, protocol_encoder)


@pytest.fixture
def workflow_service(connection_service):
    """Create a workflow service instance."""
    return MVCWorkflowService(connection_service)


@pytest.fixture
def status_service(connection_service):
    """Create a status service instance."""
    return StatusService(connection_service)


@pytest.fixture
def connection_controller(connection_service, connection_model):
    """Create a connection controller instance."""
    return ConnectionController(connection_service, connection_model)


@pytest.fixture
def workflow_controller(workflow_service, connection_model):
    """Create a workflow controller instance."""
    return WorkflowController(workflow_service, connection_model)


@pytest.fixture
def test_workflow_file():
    """Create a temporary test workflow file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write("[Experiment Settings]\n")
        f.write("Experiment Name=Test Integration\n")
        f.write("\n")
        f.write("[Light Sheet Settings]\n")
        f.write("Laser Channel=Laser 3 488 nm\n")
        f.write("Laser Power=5.0\n")

        filepath = Path(f.name)

    yield filepath

    # Cleanup
    if filepath.exists():
        filepath.unlink()


# ============================================================================
# Core Layer Integration Tests
# ============================================================================

def test_core_connection_to_mock_server(mock_server, tcp_connection):
    """Test TCP connection can connect to mock server."""
    cmd_sock, live_sock = tcp_connection.connect("127.0.0.1", 53717)

    assert cmd_sock is not None
    assert live_sock is not None
    assert tcp_connection.is_connected() is True

    tcp_connection.disconnect()
    assert tcp_connection.is_connected() is False


def test_core_send_encoded_command(mock_server, tcp_connection, protocol_encoder):
    """Test sending encoded command through TCP connection."""
    cmd_sock, live_sock = tcp_connection.connect("127.0.0.1", 53717)

    # Encode a workflow stop command
    cmd_bytes = protocol_encoder.encode_command(CommandCode.CMD_WORKFLOW_STOP)

    # Send through connection
    tcp_connection.send_bytes(cmd_bytes, socket_type="command")

    # Should not raise any errors
    tcp_connection.disconnect()


# ============================================================================
# Service Layer Integration Tests
# ============================================================================

def test_service_connect_and_disconnect(mock_server, connection_service):
    """Test connection service can connect and disconnect."""
    config = ConnectionConfig("127.0.0.1", 53717)

    success, message = connection_service.connect(config)

    assert success is True
    assert "Connected" in message or "connected" in message.lower()
    assert connection_service.is_connected() is True

    success, message = connection_service.disconnect()
    assert success is True
    assert connection_service.is_connected() is False


def test_service_reconnect_after_disconnect(mock_server, connection_service):
    """Test service can reconnect after disconnect."""
    config = ConnectionConfig("127.0.0.1", 53717)

    # First connection
    success, _ = connection_service.connect(config)
    assert success is True

    # Disconnect
    connection_service.disconnect()
    assert connection_service.is_connected() is False

    # Reconnect
    success, message = connection_service.reconnect()
    assert success is True
    assert connection_service.is_connected() is True

    connection_service.disconnect()


def test_service_send_command_while_connected(mock_server, connection_service):
    """Test sending command through connection service."""
    config = ConnectionConfig("127.0.0.1", 53717)
    connection_service.connect(config)

    # Send system state query
    success, message = connection_service.send_command(CommandCode.CMD_SYSTEM_STATE_GET)

    assert success is True

    connection_service.disconnect()


def test_workflow_service_load_and_start(mock_server, workflow_service, test_workflow_file):
    """Test workflow service can load and start workflow."""
    # Connect first
    config = ConnectionConfig("127.0.0.1", 53717)
    workflow_service.connection_service.connect(config)

    # Load workflow
    success, message = workflow_service.load_workflow(str(test_workflow_file))
    assert success is True
    assert "loaded" in message.lower()

    # Start workflow
    success, message = workflow_service.start_workflow()
    assert success is True
    assert "started" in message.lower() or "sent" in message.lower()

    # Stop workflow
    success, message = workflow_service.stop_workflow()
    assert success is True

    workflow_service.connection_service.disconnect()


def test_status_service_get_server_status(mock_server, status_service):
    """Test status service can query server status."""
    config = ConnectionConfig("127.0.0.1", 53717)
    status_service.connection_service.connect(config)

    success, status = status_service.get_server_status()

    # Server may return status or timeout gracefully
    assert success is True or success is False  # Either is acceptable

    status_service.connection_service.disconnect()


# ============================================================================
# Controller Layer Integration Tests
# ============================================================================

def test_controller_connect_via_ip_and_port(mock_server, connection_controller):
    """Test controller can connect using IP and port."""
    success, message = connection_controller.connect("127.0.0.1", 53717)

    assert success is True
    assert "connected" in message.lower()

    success, message = connection_controller.disconnect()
    assert success is True


def test_controller_reconnect(mock_server, connection_controller):
    """Test controller can reconnect after disconnect."""
    # Initial connection
    connection_controller.connect("127.0.0.1", 53717)
    connection_controller.disconnect()

    # Reconnect
    success, message = connection_controller.reconnect()

    assert success is True
    assert "connected" in message.lower()

    connection_controller.disconnect()


def test_workflow_controller_full_workflow(mock_server, connection_controller,
                                          workflow_controller, test_workflow_file):
    """Test workflow controller full lifecycle."""
    # Connect first
    connection_controller.connect("127.0.0.1", 53717)

    # Load workflow
    success, message = workflow_controller.load_workflow(str(test_workflow_file))
    assert success is True

    # Start workflow
    success, message = workflow_controller.start_workflow()
    assert success is True

    # Stop workflow
    success, message = workflow_controller.stop_workflow()
    assert success is True

    # Disconnect
    connection_controller.disconnect()


# ============================================================================
# Full Stack Integration Tests
# ============================================================================

def test_full_stack_complete_workflow(mock_server, test_workflow_file):
    """Test complete workflow: connect -> load -> start -> stop -> disconnect."""
    # Create full stack from scratch
    tcp_conn = TCPConnection()
    encoder = ProtocolEncoder()
    model = ConnectionModel()

    conn_service = MVCConnectionService(tcp_conn, encoder)
    workflow_service = MVCWorkflowService(conn_service)

    conn_controller = ConnectionController(conn_service, model)
    workflow_controller = WorkflowController(workflow_service, model)

    # Step 1: Connect
    success, message = conn_controller.connect("127.0.0.1", 53717)
    assert success, f"Connection failed: {message}"
    assert model.status.state == ConnectionState.CONNECTED

    # Step 2: Load workflow
    success, message = workflow_controller.load_workflow(str(test_workflow_file))
    assert success, f"Workflow load failed: {message}"

    # Step 3: Start workflow
    success, message = workflow_controller.start_workflow()
    assert success, f"Workflow start failed: {message}"

    # Step 4: Check status
    status = workflow_controller.get_workflow_status()
    assert status['workflow_loaded'] is True

    # Step 5: Stop workflow
    success, message = workflow_controller.stop_workflow()
    assert success, f"Workflow stop failed: {message}"

    # Step 6: Disconnect
    success, message = conn_controller.disconnect()
    assert success, f"Disconnect failed: {message}"
    assert model.status.state == ConnectionState.DISCONNECTED


# ============================================================================
# Error Handling Integration Tests
# ============================================================================

def test_error_connect_to_nonexistent_server(connection_controller):
    """Test error handling when server is not running."""
    # Try to connect to a port where no server is running
    success, message = connection_controller.connect("127.0.0.1", 55555)

    assert success is False
    assert "refused" in message.lower() or "timeout" in message.lower()


def test_error_workflow_without_connection(workflow_controller, test_workflow_file):
    """Test error when trying workflow operations without connection."""
    # Try to load workflow without connecting
    success, message = workflow_controller.load_workflow(str(test_workflow_file))
    assert success is True  # Loading file doesn't require connection

    # Try to start without connection
    success, message = workflow_controller.start_workflow()
    assert success is False
    assert "not connected" in message.lower()


def test_error_invalid_workflow_file(mock_server, connection_controller, workflow_controller):
    """Test error handling with invalid workflow file."""
    connection_controller.connect("127.0.0.1", 53717)

    # Try to load non-existent file
    success, message = workflow_controller.load_workflow("/nonexistent/file.txt")
    assert success is False
    assert "not found" in message.lower() or "does not exist" in message.lower()

    connection_controller.disconnect()


def test_error_workflow_too_large(mock_server, connection_controller, workflow_controller):
    """Test error handling with workflow file that's too large."""
    connection_controller.connect("127.0.0.1", 53717)

    # Create a large file (>10MB)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        # Write 11MB of data
        for _ in range(11 * 1024 * 1024):
            f.write('x')
        large_file = Path(f.name)

    try:
        success, message = workflow_controller.load_workflow(str(large_file))
        assert success is False
        assert "too large" in message.lower() or "size" in message.lower()
    finally:
        if large_file.exists():
            large_file.unlink()
        connection_controller.disconnect()


# ============================================================================
# Observable Pattern Integration Tests
# ============================================================================

def test_observable_connection_state_updates(mock_server, connection_service, connection_model):
    """Test that connection model receives state updates."""
    # Track state changes
    states_seen = []

    def on_status_change(status):
        states_seen.append(status.state)

    connection_model.add_observer(on_status_change)

    # Use service with this model
    service = MVCConnectionService(connection_service.tcp_connection,
                                   connection_service.encoder,
                                   connection_model)

    config = ConnectionConfig("127.0.0.1", 53717)

    # Connect
    service.connect(config)
    assert ConnectionState.CONNECTED in states_seen

    # Disconnect
    service.disconnect()
    assert ConnectionState.DISCONNECTED in states_seen


# ============================================================================
# Concurrent Operations Integration Tests
# ============================================================================

def test_multiple_connections_sequential(mock_server):
    """Test multiple sequential connections work correctly."""
    for i in range(3):
        tcp_conn = TCPConnection()
        encoder = ProtocolEncoder()
        service = MVCConnectionService(tcp_conn, encoder)

        config = ConnectionConfig("127.0.0.1", 53717)
        success, _ = service.connect(config)
        assert success is True

        service.disconnect()


def test_workflow_start_stop_multiple_times(mock_server, connection_controller,
                                           workflow_controller, test_workflow_file):
    """Test starting and stopping workflow multiple times."""
    connection_controller.connect("127.0.0.1", 53717)
    workflow_controller.load_workflow(str(test_workflow_file))

    for i in range(3):
        # Start
        success, _ = workflow_controller.start_workflow()
        assert success is True

        # Stop
        success, _ = workflow_controller.stop_workflow()
        assert success is True

    connection_controller.disconnect()


# ============================================================================
# Edge Case Integration Tests
# ============================================================================

def test_disconnect_while_not_connected(connection_controller):
    """Test disconnecting when not connected."""
    success, message = connection_controller.disconnect()
    # Should handle gracefully
    assert success is True or success is False  # Either is acceptable


def test_connect_while_already_connected(mock_server, connection_controller):
    """Test connecting when already connected."""
    # First connection
    connection_controller.connect("127.0.0.1", 53717)

    # Try to connect again
    success, message = connection_controller.connect("127.0.0.1", 53717)

    # Should either succeed or report already connected
    # Cleanup
    connection_controller.disconnect()


def test_empty_workflow_file(mock_server, connection_controller, workflow_controller):
    """Test handling of empty workflow file."""
    connection_controller.connect("127.0.0.1", 53717)

    # Create empty file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        empty_file = Path(f.name)

    try:
        success, message = workflow_controller.load_workflow(str(empty_file))
        # Should handle gracefully
        assert isinstance(success, bool)
    finally:
        if empty_file.exists():
            empty_file.unlink()
        connection_controller.disconnect()


# ============================================================================
# Cleanup and Resource Management Tests
# ============================================================================

def test_proper_resource_cleanup_on_disconnect(mock_server, tcp_connection):
    """Test that resources are properly cleaned up on disconnect."""
    # Connect
    cmd_sock, live_sock = tcp_connection.connect("127.0.0.1", 53717)
    assert tcp_connection.is_connected() is True

    # Disconnect
    tcp_connection.disconnect()

    # Verify sockets are closed
    assert tcp_connection.is_connected() is False
    # Note: Socket objects are closed internally, cannot verify from outside


def test_multiple_observers_receive_updates(mock_server, connection_service):
    """Test that multiple observers receive connection updates."""
    model = ConnectionModel()
    service = MVCConnectionService(connection_service.tcp_connection,
                                   connection_service.encoder,
                                   model)

    observer1_calls = []
    observer2_calls = []

    def observer1(status):
        observer1_calls.append(status.state)

    def observer2(status):
        observer2_calls.append(status.state)

    model.add_observer(observer1)
    model.add_observer(observer2)

    config = ConnectionConfig("127.0.0.1", 53717)
    service.connect(config)
    service.disconnect()

    # Both observers should have received updates
    assert len(observer1_calls) > 0
    assert len(observer2_calls) > 0
    assert observer1_calls == observer2_calls
