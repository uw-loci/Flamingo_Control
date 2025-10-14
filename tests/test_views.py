"""
Tests for PyQt5 view components.

This module tests the ConnectionView and WorkflowView widgets.
Uses pytest-qt for Qt-specific testing.
"""

import os
# Set offscreen platform before importing Qt
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import pytest
from pathlib import Path
from unittest.mock import Mock, MagicMock
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from py2flamingo.views import ConnectionView, WorkflowView


# Fixtures

@pytest.fixture
def qapp():
    """Create QApplication instance for tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def mock_connection_controller():
    """Create mock ConnectionController."""
    controller = Mock()
    controller.connect = Mock(return_value=(True, "Connected successfully"))
    controller.disconnect = Mock(return_value=(True, "Disconnected successfully"))
    return controller


@pytest.fixture
def mock_workflow_controller():
    """Create mock WorkflowController."""
    controller = Mock()
    controller.load_workflow = Mock(return_value=(True, "Workflow loaded"))
    controller.start_workflow = Mock(return_value=(True, "Workflow started"))
    controller.stop_workflow = Mock(return_value=(True, "Workflow stopped"))
    return controller


@pytest.fixture
def connection_view(qapp, mock_connection_controller, qtbot):
    """Create ConnectionView with mock controller."""
    view = ConnectionView(mock_connection_controller)
    qtbot.addWidget(view)
    return view


@pytest.fixture
def workflow_view(qapp, mock_workflow_controller, qtbot):
    """Create WorkflowView with mock controller."""
    view = WorkflowView(mock_workflow_controller)
    qtbot.addWidget(view)
    return view


# ConnectionView Tests

class TestConnectionViewCreation:
    """Test ConnectionView UI creation and initialization."""

    def test_view_creates_widgets(self, connection_view):
        """Test that all widgets are created."""
        assert connection_view.ip_input is not None
        assert connection_view.port_input is not None
        assert connection_view.connect_btn is not None
        assert connection_view.disconnect_btn is not None
        assert connection_view.status_label is not None
        assert connection_view.message_label is not None

    def test_initial_ip_value(self, connection_view):
        """Test default IP address is set."""
        assert connection_view.ip_input.text() == "127.0.0.1"

    def test_initial_port_value(self, connection_view):
        """Test default port is set."""
        assert connection_view.port_input.value() == 53717

    def test_initial_button_state(self, connection_view):
        """Test initial button enabled/disabled state."""
        assert connection_view.connect_btn.isEnabled()
        assert not connection_view.disconnect_btn.isEnabled()

    def test_initial_status_text(self, connection_view):
        """Test initial status label text."""
        assert "Not connected" in connection_view.status_label.text()


class TestConnectionViewConnecting:
    """Test connection operations in ConnectionView."""

    def test_connect_button_calls_controller(self, connection_view, mock_connection_controller, qtbot):
        """Test that clicking connect calls controller with IP and port."""
        # Set custom values
        connection_view.ip_input.setText("192.168.1.100")
        connection_view.port_input.setValue(8080)

        # Click connect
        qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)

        # Verify controller called
        mock_connection_controller.connect.assert_called_once_with("192.168.1.100", 8080)

    def test_connect_success_updates_ui(self, connection_view, mock_connection_controller, qtbot):
        """Test successful connection updates UI correctly."""
        # Click connect
        qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)

        # Verify UI updated
        assert "Connected" in connection_view.status_label.text()
        assert not connection_view.connect_btn.isEnabled()
        assert connection_view.disconnect_btn.isEnabled()
        assert not connection_view.ip_input.isEnabled()
        assert not connection_view.port_input.isEnabled()

    def test_connect_success_shows_message(self, connection_view, mock_connection_controller, qtbot):
        """Test success message is displayed."""
        qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)
        assert "Connected successfully" in connection_view.message_label.text()

    def test_connect_failure_shows_error(self, connection_view, mock_connection_controller, qtbot):
        """Test connection failure shows error message."""
        # Mock failure
        mock_connection_controller.connect.return_value = (False, "Connection failed")

        # Click connect
        qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)

        # Verify error displayed
        assert "Connection failed" in connection_view.message_label.text()

    def test_connect_failure_keeps_disconnected_state(self, connection_view, mock_connection_controller, qtbot):
        """Test failed connection keeps UI in disconnected state."""
        mock_connection_controller.connect.return_value = (False, "Connection failed")

        qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)

        # Should still be disconnected
        assert connection_view.connect_btn.isEnabled()
        assert not connection_view.disconnect_btn.isEnabled()


class TestConnectionViewDisconnecting:
    """Test disconnection operations in ConnectionView."""

    def test_disconnect_button_calls_controller(self, connection_view, mock_connection_controller, qtbot):
        """Test that clicking disconnect calls controller."""
        # First connect
        qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)

        # Then disconnect
        qtbot.mouseClick(connection_view.disconnect_btn, Qt.LeftButton)

        # Verify controller called
        mock_connection_controller.disconnect.assert_called_once()

    def test_disconnect_success_updates_ui(self, connection_view, mock_connection_controller, qtbot):
        """Test successful disconnect updates UI correctly."""
        # Connect first
        qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)

        # Then disconnect
        qtbot.mouseClick(connection_view.disconnect_btn, Qt.LeftButton)

        # Verify UI restored to disconnected state
        assert "Not connected" in connection_view.status_label.text()
        assert connection_view.connect_btn.isEnabled()
        assert not connection_view.disconnect_btn.isEnabled()
        assert connection_view.ip_input.isEnabled()
        assert connection_view.port_input.isEnabled()

    def test_disconnect_shows_message(self, connection_view, mock_connection_controller, qtbot):
        """Test disconnect success message."""
        # Connect then disconnect
        qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)
        qtbot.mouseClick(connection_view.disconnect_btn, Qt.LeftButton)

        assert "Disconnected successfully" in connection_view.message_label.text()


class TestConnectionViewHelperMethods:
    """Test helper methods in ConnectionView."""

    def test_get_connection_info(self, connection_view):
        """Test getting connection info from UI."""
        connection_view.ip_input.setText("10.0.0.1")
        connection_view.port_input.setValue(9999)

        ip, port = connection_view.get_connection_info()
        assert ip == "10.0.0.1"
        assert port == 9999

    def test_set_connection_info(self, connection_view):
        """Test setting connection info in UI."""
        connection_view.set_connection_info("192.168.1.50", 7777)

        assert connection_view.ip_input.text() == "192.168.1.50"
        assert connection_view.port_input.value() == 7777

    def test_clear_message(self, connection_view):
        """Test clearing message display."""
        connection_view.message_label.setText("Test message")
        connection_view.clear_message()
        assert connection_view.message_label.text() == ""

    def test_show_error_message_color(self, connection_view):
        """Test error messages are displayed in red."""
        connection_view._show_message("Error occurred", is_error=True)
        # Check that red color is in style
        assert "red" in connection_view.message_label.styleSheet().lower()

    def test_show_success_message_color(self, connection_view):
        """Test success messages are displayed in green."""
        connection_view._show_message("Operation successful", is_error=False)
        # Check that green color is in style
        assert "green" in connection_view.message_label.styleSheet().lower()


# WorkflowView Tests

class TestWorkflowViewCreation:
    """Test WorkflowView UI creation and initialization."""

    def test_view_creates_widgets(self, workflow_view):
        """Test that all widgets are created."""
        assert workflow_view.file_path_input is not None
        assert workflow_view.browse_btn is not None
        assert workflow_view.start_btn is not None
        assert workflow_view.stop_btn is not None
        assert workflow_view.status_label is not None
        assert workflow_view.message_label is not None

    def test_file_path_input_readonly(self, workflow_view):
        """Test file path input is read-only."""
        assert workflow_view.file_path_input.isReadOnly()

    def test_initial_button_state(self, workflow_view):
        """Test initial button enabled/disabled state."""
        assert workflow_view.browse_btn.isEnabled()
        assert not workflow_view.start_btn.isEnabled()
        assert not workflow_view.stop_btn.isEnabled()

    def test_initial_status_text(self, workflow_view):
        """Test initial status label text."""
        assert "No workflow loaded" in workflow_view.status_label.text()

    def test_initial_workflow_path_none(self, workflow_view):
        """Test initial workflow path is None."""
        assert workflow_view.get_workflow_path() is None


class TestWorkflowViewFileSelection:
    """Test file selection in WorkflowView."""

    def test_browse_button_exists(self, workflow_view):
        """Test browse button is created."""
        assert workflow_view.browse_btn is not None

    def test_load_workflow_success_updates_status(self, workflow_view, mock_workflow_controller):
        """Test successful workflow load updates status."""
        # Simulate file selection (can't actually click file dialog in test)
        workflow_path = Path("/test/workflow.txt")
        workflow_view._current_workflow_path = workflow_path
        workflow_view.file_path_input.setText(str(workflow_path))

        # Simulate controller call
        success, message = mock_workflow_controller.load_workflow(workflow_path)
        workflow_view._show_message(message, is_error=not success)
        workflow_view._update_status(workflow_loaded=True, workflow_running=False)

        # Verify status updated
        assert "Workflow loaded" in workflow_view.status_label.text()
        assert workflow_view.start_btn.isEnabled()

    def test_load_workflow_updates_path_display(self, workflow_view):
        """Test workflow path is displayed after selection."""
        workflow_path = Path("/test/workflow.txt")
        workflow_view._current_workflow_path = workflow_path
        workflow_view.file_path_input.setText(str(workflow_path))

        assert str(workflow_path) in workflow_view.file_path_input.text()


class TestWorkflowViewStartStop:
    """Test workflow start/stop operations."""

    def test_start_without_file_shows_error(self, workflow_view, qtbot):
        """Test starting without file shows error."""
        # Enable start button manually (normally would be disabled)
        workflow_view.start_btn.setEnabled(True)

        # Click start with no file
        qtbot.mouseClick(workflow_view.start_btn, Qt.LeftButton)

        # Should show error
        assert "No workflow file selected" in workflow_view.message_label.text()

    def test_start_with_file_calls_controller(self, workflow_view, mock_workflow_controller, qtbot):
        """Test starting workflow calls controller."""
        # Setup workflow
        workflow_view._current_workflow_path = Path("/test/workflow.txt")
        workflow_view._update_status(workflow_loaded=True, workflow_running=False)

        # Click start
        qtbot.mouseClick(workflow_view.start_btn, Qt.LeftButton)

        # Verify controller called
        mock_workflow_controller.start_workflow.assert_called_once()

    def test_start_success_updates_ui(self, workflow_view, mock_workflow_controller, qtbot):
        """Test successful workflow start updates UI."""
        # Setup
        workflow_view._current_workflow_path = Path("/test/workflow.txt")
        workflow_view._update_status(workflow_loaded=True, workflow_running=False)

        # Start
        qtbot.mouseClick(workflow_view.start_btn, Qt.LeftButton)

        # Verify UI
        assert "Workflow running" in workflow_view.status_label.text()
        assert not workflow_view.start_btn.isEnabled()
        assert workflow_view.stop_btn.isEnabled()
        assert not workflow_view.browse_btn.isEnabled()

    def test_stop_calls_controller(self, workflow_view, mock_workflow_controller, qtbot):
        """Test stopping workflow calls controller."""
        # Setup running workflow
        workflow_view._current_workflow_path = Path("/test/workflow.txt")
        workflow_view._update_status(workflow_loaded=True, workflow_running=True)

        # Click stop
        qtbot.mouseClick(workflow_view.stop_btn, Qt.LeftButton)

        # Verify controller called
        mock_workflow_controller.stop_workflow.assert_called_once()

    def test_stop_success_updates_ui(self, workflow_view, mock_workflow_controller, qtbot):
        """Test successful workflow stop updates UI."""
        # Setup running workflow
        workflow_view._current_workflow_path = Path("/test/workflow.txt")
        workflow_view._update_status(workflow_loaded=True, workflow_running=True)

        # Stop
        qtbot.mouseClick(workflow_view.stop_btn, Qt.LeftButton)

        # Verify UI restored to loaded state
        assert "Workflow loaded" in workflow_view.status_label.text()
        assert workflow_view.start_btn.isEnabled()
        assert not workflow_view.stop_btn.isEnabled()
        assert workflow_view.browse_btn.isEnabled()


class TestWorkflowViewConnectionState:
    """Test workflow view response to connection state changes."""

    def test_disconnected_disables_start(self, workflow_view):
        """Test disconnection disables start button."""
        # Setup with workflow loaded
        workflow_view._current_workflow_path = Path("/test/workflow.txt")
        workflow_view._update_status(workflow_loaded=True, workflow_running=False)

        # Simulate disconnection
        workflow_view.update_for_connection_state(connected=False)

        # Start should be disabled
        assert not workflow_view.start_btn.isEnabled()

    def test_connected_with_workflow_enables_start(self, workflow_view):
        """Test connection with loaded workflow enables start."""
        # Setup with workflow loaded
        workflow_view._current_workflow_path = Path("/test/workflow.txt")

        # Simulate connection
        workflow_view.update_for_connection_state(connected=True)

        # Start should be enabled
        assert workflow_view.start_btn.isEnabled()

    def test_connected_without_workflow_keeps_start_disabled(self, workflow_view):
        """Test connection without workflow keeps start disabled."""
        # No workflow loaded
        workflow_view.update_for_connection_state(connected=True)

        # Start should still be disabled
        assert not workflow_view.start_btn.isEnabled()


class TestWorkflowViewHelperMethods:
    """Test helper methods in WorkflowView."""

    def test_get_workflow_path(self, workflow_view):
        """Test getting workflow path."""
        test_path = Path("/test/workflow.txt")
        workflow_view._current_workflow_path = test_path

        assert workflow_view.get_workflow_path() == test_path

    def test_clear_workflow(self, workflow_view):
        """Test clearing workflow."""
        # Setup workflow
        workflow_view._current_workflow_path = Path("/test/workflow.txt")
        workflow_view.file_path_input.setText("/test/workflow.txt")

        # Clear
        workflow_view.clear_workflow()

        # Verify cleared
        assert workflow_view.get_workflow_path() is None
        assert workflow_view.file_path_input.text() == ""
        assert "No workflow loaded" in workflow_view.status_label.text()

    def test_clear_message(self, workflow_view):
        """Test clearing message display."""
        workflow_view.message_label.setText("Test message")
        workflow_view.clear_message()
        assert workflow_view.message_label.text() == ""

    def test_show_error_message_color(self, workflow_view):
        """Test error messages are displayed in red."""
        workflow_view._show_message("Error occurred", is_error=True)
        assert "red" in workflow_view.message_label.styleSheet().lower()

    def test_show_success_message_color(self, workflow_view):
        """Test success messages are displayed in green."""
        workflow_view._show_message("Operation successful", is_error=False)
        assert "green" in workflow_view.message_label.styleSheet().lower()


class TestWorkflowViewStatusUpdates:
    """Test status update logic in WorkflowView."""

    def test_status_no_workflow(self, workflow_view):
        """Test status display with no workflow."""
        workflow_view._update_status(workflow_loaded=False, workflow_running=False)

        assert "No workflow loaded" in workflow_view.status_label.text()
        assert not workflow_view.start_btn.isEnabled()
        assert not workflow_view.stop_btn.isEnabled()

    def test_status_workflow_loaded(self, workflow_view):
        """Test status display with workflow loaded."""
        workflow_view._update_status(workflow_loaded=True, workflow_running=False)

        assert "Workflow loaded" in workflow_view.status_label.text()
        assert workflow_view.start_btn.isEnabled()
        assert not workflow_view.stop_btn.isEnabled()

    def test_status_workflow_running(self, workflow_view):
        """Test status display with workflow running."""
        workflow_view._update_status(workflow_loaded=True, workflow_running=True)

        assert "Workflow running" in workflow_view.status_label.text()
        assert not workflow_view.start_btn.isEnabled()
        assert workflow_view.stop_btn.isEnabled()


# Integration-style tests

class TestViewsIntegration:
    """Test views work together correctly."""

    def test_connection_view_independent(self, connection_view):
        """Test connection view works independently."""
        # Should be able to create and interact with connection view alone
        assert connection_view is not None
        assert connection_view.connect_btn.isEnabled()

    def test_workflow_view_independent(self, workflow_view):
        """Test workflow view works independently."""
        # Should be able to create and interact with workflow view alone
        assert workflow_view is not None
        assert workflow_view.browse_btn.isEnabled()

    def test_both_views_can_coexist(self, qapp, mock_connection_controller, mock_workflow_controller, qtbot):
        """Test both views can be created and used together."""
        conn_view = ConnectionView(mock_connection_controller)
        work_view = WorkflowView(mock_workflow_controller)

        qtbot.addWidget(conn_view)
        qtbot.addWidget(work_view)

        assert conn_view is not None
        assert work_view is not None
