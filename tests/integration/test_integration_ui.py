"""UI integration tests for MVC architecture.

These tests verify that the UI components work together correctly with the
underlying MVC layers, including user interactions and state updates.
"""

import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt

# Set offscreen platform for headless testing
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

# Import MVC components
from py2flamingo.views import ConnectionView, WorkflowView
from py2flamingo.controllers import ConnectionController, WorkflowController
from py2flamingo.services import MVCConnectionService, MVCWorkflowService
from py2flamingo.models import ConnectionModel, ConnectionState
from py2flamingo.core import TCPConnection, ProtocolEncoder
from py2flamingo.application import FlamingoApplication
from py2flamingo.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    """Create QApplication for tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def connection_model():
    """Create connection model."""
    return ConnectionModel()


@pytest.fixture
def mock_connection_service():
    """Create mock connection service."""
    service = Mock(spec=MVCConnectionService)
    service.is_connected.return_value = False
    service.model = ConnectionModel()
    return service


@pytest.fixture
def mock_workflow_service():
    """Create mock workflow service."""
    service = Mock(spec=MVCWorkflowService)
    service.connection_service = Mock()
    service.connection_service.is_connected.return_value = False
    return service


@pytest.fixture
def connection_controller(mock_connection_service, connection_model):
    """Create connection controller with mock service."""
    return ConnectionController(mock_connection_service, connection_model)


@pytest.fixture
def workflow_controller(mock_workflow_service, connection_model):
    """Create workflow controller with mock service."""
    return WorkflowController(mock_workflow_service, connection_model)


@pytest.fixture
def connection_view(qtbot, connection_controller):
    """Create connection view."""
    view = ConnectionView(connection_controller)
    qtbot.addWidget(view)
    return view


@pytest.fixture
def workflow_view(qtbot, workflow_controller):
    """Create workflow view."""
    view = WorkflowView(workflow_controller)
    qtbot.addWidget(view)
    return view


@pytest.fixture
def test_workflow_file():
    """Create a temporary test workflow file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write("[Experiment Settings]\n")
        f.write("Experiment Name=Test UI Integration\n")
        filepath = Path(f.name)
    yield filepath
    if filepath.exists():
        filepath.unlink()


# ============================================================================
# Connection View Integration Tests
# ============================================================================

def test_connection_view_displays_correctly(connection_view):
    """Test that connection view displays all components."""
    assert connection_view.ip_input is not None
    assert connection_view.port_input is not None
    assert connection_view.connect_btn is not None
    assert connection_view.disconnect_btn is not None
    assert connection_view.status_label is not None


def test_connection_view_initial_state(connection_view):
    """Test connection view starts in correct state."""
    assert connection_view.connect_btn.isEnabled() is True
    assert connection_view.disconnect_btn.isEnabled() is False
    assert "Disconnected" in connection_view.status_label.text()


def test_connection_view_connect_button_click(qtbot, connection_view, mock_connection_service):
    """Test clicking connect button triggers connection."""
    mock_connection_service.connect.return_value = (True, "Connected successfully")

    # Set connection info
    connection_view.ip_input.setText("127.0.0.1")
    connection_view.port_input.setValue(53717)

    # Click connect button
    qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)

    # Verify service was called
    mock_connection_service.connect.assert_called_once()


def test_connection_view_disconnect_button_click(qtbot, connection_view, mock_connection_service):
    """Test clicking disconnect button triggers disconnection."""
    # Simulate connected state
    mock_connection_service.is_connected.return_value = True
    mock_connection_service.disconnect.return_value = (True, "Disconnected")

    # Update view state
    connection_view._update_connection_state(True)

    # Click disconnect button
    qtbot.mouseClick(connection_view.disconnect_btn, Qt.LeftButton)

    # Verify service was called
    mock_connection_service.disconnect.assert_called_once()


def test_connection_view_updates_on_success(qtbot, connection_view, mock_connection_service):
    """Test view updates correctly on successful connection."""
    mock_connection_service.connect.return_value = (True, "Connected to 127.0.0.1:53717")
    mock_connection_service.is_connected.return_value = True

    connection_view.ip_input.setText("127.0.0.1")
    connection_view.port_input.setValue(53717)

    qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)
    qtbot.wait(100)

    # Check UI state after connection
    assert connection_view.connect_btn.isEnabled() is False
    assert connection_view.disconnect_btn.isEnabled() is True


def test_connection_view_displays_error(qtbot, connection_view, mock_connection_service):
    """Test view displays error message on connection failure."""
    mock_connection_service.connect.return_value = (False, "Connection refused")

    connection_view.ip_input.setText("127.0.0.1")
    connection_view.port_input.setValue(53717)

    qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)
    qtbot.wait(100)

    # Check error is displayed
    assert "refused" in connection_view.message_label.text().lower()


def test_connection_view_validates_ip_format(connection_view):
    """Test view validates IP address format."""
    # Valid IP
    connection_view.ip_input.setText("192.168.1.1")
    ip, port = connection_view.get_connection_info()
    assert ip == "192.168.1.1"

    # Another valid IP
    connection_view.ip_input.setText("127.0.0.1")
    ip, port = connection_view.get_connection_info()
    assert ip == "127.0.0.1"


def test_connection_view_get_and_set_info(connection_view):
    """Test getting and setting connection info."""
    # Set info
    connection_view.set_connection_info("10.0.0.1", 8080)

    # Get info
    ip, port = connection_view.get_connection_info()

    assert ip == "10.0.0.1"
    assert port == 8080


# ============================================================================
# Workflow View Integration Tests
# ============================================================================

def test_workflow_view_displays_correctly(workflow_view):
    """Test that workflow view displays all components."""
    assert workflow_view.file_path_input is not None
    assert workflow_view.browse_btn is not None
    assert workflow_view.start_btn is not None
    assert workflow_view.stop_btn is not None
    assert workflow_view.status_label is not None


def test_workflow_view_initial_state(workflow_view):
    """Test workflow view starts in correct state."""
    assert workflow_view.start_btn.isEnabled() is False
    assert workflow_view.stop_btn.isEnabled() is False
    assert workflow_view.file_path_input.text() == ""


def test_workflow_view_browse_button_opens_dialog(qtbot, workflow_view):
    """Test browse button attempts to open file dialog."""
    with patch('PyQt5.QtWidgets.QFileDialog.getOpenFileName') as mock_dialog:
        mock_dialog.return_value = ("/path/to/workflow.txt", "")

        qtbot.mouseClick(workflow_view.browse_btn, Qt.LeftButton)

        # Verify dialog was opened
        mock_dialog.assert_called_once()


def test_workflow_view_start_button_click(qtbot, workflow_view, mock_workflow_service,
                                         test_workflow_file):
    """Test clicking start button triggers workflow start."""
    mock_workflow_service.connection_service.is_connected.return_value = True
    mock_workflow_service.start_workflow.return_value = (True, "Workflow started")

    # Set workflow path
    workflow_view.file_path_input.setText(str(test_workflow_file))
    workflow_view.update_for_connection_state(connected=True)

    # Click start button
    qtbot.mouseClick(workflow_view.start_btn, Qt.LeftButton)

    # Verify service was called
    mock_workflow_service.start_workflow.assert_called_once()


def test_workflow_view_stop_button_click(qtbot, workflow_view, mock_workflow_service):
    """Test clicking stop button triggers workflow stop."""
    mock_workflow_service.stop_workflow.return_value = (True, "Workflow stopped")

    # Enable stop button (simulating running workflow)
    workflow_view.stop_btn.setEnabled(True)

    # Click stop button
    qtbot.mouseClick(workflow_view.stop_btn, Qt.LeftButton)

    # Verify service was called
    mock_workflow_service.stop_workflow.assert_called_once()


def test_workflow_view_updates_on_connection_state(workflow_view):
    """Test view updates when connection state changes."""
    # Not connected
    workflow_view.update_for_connection_state(connected=False)
    assert workflow_view.start_btn.isEnabled() is False

    # Connected with workflow loaded
    workflow_view.file_path_input.setText("/path/to/workflow.txt")
    workflow_view.update_for_connection_state(connected=True)
    assert workflow_view.start_btn.isEnabled() is True


def test_workflow_view_displays_status(workflow_view):
    """Test view displays workflow status."""
    workflow_view._show_message("Workflow running", success=True)
    assert "running" in workflow_view.message_label.text().lower()


def test_workflow_view_clears_workflow(workflow_view, test_workflow_file):
    """Test clearing workflow resets view state."""
    workflow_view.file_path_input.setText(str(test_workflow_file))

    workflow_view.clear_workflow()

    assert workflow_view.file_path_input.text() == ""
    assert workflow_view.start_btn.isEnabled() is False


def test_workflow_view_get_workflow_path(workflow_view, test_workflow_file):
    """Test getting workflow path from view."""
    workflow_view.file_path_input.setText(str(test_workflow_file))

    path = workflow_view.get_workflow_path()

    assert path == str(test_workflow_file)


# ============================================================================
# Main Window Integration Tests
# ============================================================================

@pytest.fixture
def main_window(qtbot, connection_controller, workflow_controller):
    """Create main window with controllers."""
    # Create views with mock controllers
    connection_view = ConnectionView(connection_controller)
    workflow_view = WorkflowView(workflow_controller)

    window = MainWindow(connection_view, workflow_view)
    qtbot.addWidget(window)
    return window


def test_main_window_displays_tabs(main_window):
    """Test main window displays both connection and workflow tabs."""
    # Main window uses tab widget
    assert main_window.tab_widget is not None
    assert main_window.tab_widget.count() == 2


def test_main_window_has_menu_bar(main_window):
    """Test main window has menu bar."""
    menu_bar = main_window.menuBar()
    assert menu_bar is not None


def test_main_window_has_status_bar(main_window):
    """Test main window has status bar."""
    status_bar = main_window.statusBar()
    assert status_bar is not None


def test_main_window_closes_cleanly(qtbot, main_window):
    """Test main window closes without errors."""
    # Show and close window
    main_window.show()
    qtbot.wait(100)
    main_window.close()

    # No assertions needed - test passes if no exceptions


# ============================================================================
# Application Integration Tests
# ============================================================================

def test_application_creates_all_components(qapp):
    """Test FlamingoApplication creates all MVC components."""
    with patch.object(FlamingoApplication, 'create_main_window'):
        app = FlamingoApplication()
        app.setup_dependencies()

        # Verify all components created
        assert app.tcp_connection is not None
        assert app.encoder is not None
        assert app.connection_service is not None
        assert app.workflow_service is not None
        assert app.connection_controller is not None
        assert app.workflow_controller is not None


def test_application_dependency_injection_wiring(qapp):
    """Test application wires dependencies correctly."""
    with patch.object(FlamingoApplication, 'create_main_window'):
        app = FlamingoApplication()
        app.setup_dependencies()

        # Verify service dependencies
        assert app.connection_service.tcp_connection == app.tcp_connection
        assert app.connection_service.encoder == app.encoder

        # Verify workflow service uses connection service
        assert app.workflow_service.connection_service == app.connection_service

        # Verify controllers use services
        assert app.connection_controller.service == app.connection_service
        assert app.workflow_controller.service == app.workflow_service


# ============================================================================
# End-to-End UI Workflow Tests
# ============================================================================

def test_ui_complete_workflow_simulation(qtbot, test_workflow_file):
    """Test complete UI workflow: connect -> load -> start -> stop -> disconnect."""
    # Create real stack with mocked TCP
    model = ConnectionModel()

    # Mock services
    conn_service = Mock(spec=MVCConnectionService)
    conn_service.model = model
    conn_service.is_connected.return_value = False
    conn_service.connect.return_value = (True, "Connected")
    conn_service.disconnect.return_value = (True, "Disconnected")

    workflow_service = Mock(spec=MVCWorkflowService)
    workflow_service.connection_service = conn_service
    workflow_service.load_workflow.return_value = (True, "Workflow loaded")
    workflow_service.start_workflow.return_value = (True, "Workflow started")
    workflow_service.stop_workflow.return_value = (True, "Workflow stopped")
    workflow_service.get_workflow_status.return_value = {'workflow_loaded': True}

    # Create controllers
    conn_controller = ConnectionController(conn_service, model)
    workflow_controller = WorkflowController(workflow_service, model)

    # Create views
    conn_view = ConnectionView(conn_controller)
    workflow_view = WorkflowView(workflow_controller)

    qtbot.addWidget(conn_view)
    qtbot.addWidget(workflow_view)

    # Step 1: Connect
    conn_view.ip_input.setText("127.0.0.1")
    conn_view.port_input.setValue(53717)
    qtbot.mouseClick(conn_view.connect_btn, Qt.LeftButton)
    qtbot.wait(100)

    # Simulate successful connection
    conn_service.is_connected.return_value = True
    conn_view._update_connection_state(True)

    assert conn_view.disconnect_btn.isEnabled() is True

    # Step 2: Load workflow
    workflow_view.file_path_input.setText(str(test_workflow_file))
    workflow_view.update_for_connection_state(connected=True)

    assert workflow_view.start_btn.isEnabled() is True

    # Step 3: Start workflow
    qtbot.mouseClick(workflow_view.start_btn, Qt.LeftButton)
    qtbot.wait(100)

    # Verify start was called
    workflow_service.start_workflow.assert_called()

    # Step 4: Stop workflow
    workflow_view.stop_btn.setEnabled(True)
    qtbot.mouseClick(workflow_view.stop_btn, Qt.LeftButton)
    qtbot.wait(100)

    # Verify stop was called
    workflow_service.stop_workflow.assert_called()

    # Step 5: Disconnect
    qtbot.mouseClick(conn_view.disconnect_btn, Qt.LeftButton)
    qtbot.wait(100)

    conn_service.disconnect.assert_called()


def test_ui_error_handling_flow(qtbot):
    """Test UI handles errors gracefully throughout workflow."""
    model = ConnectionModel()

    # Mock service that returns errors
    conn_service = Mock(spec=MVCConnectionService)
    conn_service.model = model
    conn_service.is_connected.return_value = False
    conn_service.connect.return_value = (False, "Connection refused")

    conn_controller = ConnectionController(conn_service, model)
    conn_view = ConnectionView(conn_controller)

    qtbot.addWidget(conn_view)

    # Try to connect with error
    conn_view.ip_input.setText("127.0.0.1")
    conn_view.port_input.setValue(53717)
    qtbot.mouseClick(conn_view.connect_btn, Qt.LeftButton)
    qtbot.wait(100)

    # Error should be displayed
    assert "refused" in conn_view.message_label.text().lower()

    # Buttons should remain in disconnected state
    assert conn_view.connect_btn.isEnabled() is True
    assert conn_view.disconnect_btn.isEnabled() is False


def test_ui_concurrent_button_clicks(qtbot, connection_view):
    """Test UI handles rapid button clicks gracefully."""
    # Click connect button multiple times rapidly
    for _ in range(5):
        qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)

    # Should not crash or cause errors
    # Test passes if no exceptions raised


def test_ui_state_consistency_after_errors(qtbot, test_workflow_file):
    """Test UI state remains consistent after various errors."""
    model = ConnectionModel()

    workflow_service = Mock(spec=MVCWorkflowService)
    workflow_service.connection_service = Mock()
    workflow_service.connection_service.is_connected.return_value = False
    workflow_service.load_workflow.return_value = (False, "File not found")

    workflow_controller = WorkflowController(workflow_service, model)
    workflow_view = WorkflowView(workflow_controller)

    qtbot.addWidget(workflow_view)

    # Try to load non-existent file
    workflow_view.file_path_input.setText("/nonexistent/file.txt")
    qtbot.mouseClick(workflow_view.browse_btn, Qt.LeftButton)

    # View state should remain consistent
    assert workflow_view.start_btn.isEnabled() is False
