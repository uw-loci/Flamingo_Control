"""
Tests for PyQt5 view components.

This module tests the ConnectionView and WorkflowView widgets.
Uses pytest-qt for Qt-specific testing.
"""

import os

# Set offscreen platform before importing Qt
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from py2flamingo.views import ConnectionView, WorkflowView
from py2flamingo.views.colors import ERROR_COLOR, SUCCESS_COLOR

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
    # A successful connect() drives _load_and_display_settings(), which calls
    # get_microscope_settings() and then iterates/len()s the result. Return a
    # real dict so the success path runs (a bare Mock breaks len()/iteration and
    # would push the view into its error/"Communication Error" branch).
    controller.get_microscope_settings = Mock(
        return_value={"Stage": {"X": 0.0, "Y": 0.0, "Z": 0.0}}
    )
    # ConnectionView routes connection-loss notifications through these; the
    # default Mock auto-creates them, so nothing extra is needed here.
    return controller


@pytest.fixture
def mock_workflow_controller():
    """Create mock WorkflowController."""
    controller = Mock()
    controller.start_workflow_from_ui = Mock(return_value=(True, "Workflow started"))
    controller.stop_workflow = Mock(return_value=(True, "Workflow stopped"))
    # WorkflowView pulls connection_service off the controller and the SavePanel
    # queries it for available drives. Return a real list so the panel does not
    # try to iterate a bare Mock.
    controller._connection_service.query_available_drives = Mock(return_value=[])
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

    def test_connect_button_calls_controller(
        self, connection_view, mock_connection_controller, qtbot
    ):
        """Test that clicking connect calls controller with IP and port."""
        # Set custom values
        connection_view.ip_input.setText("192.168.1.100")
        connection_view.port_input.setValue(8080)

        # Click connect
        qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)

        # Verify controller called
        mock_connection_controller.connect.assert_called_once_with(
            "192.168.1.100", 8080
        )

    def test_connect_success_updates_ui(
        self, connection_view, mock_connection_controller, qtbot
    ):
        """Test successful connection updates UI correctly."""
        # Click connect
        qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)

        # Verify UI updated
        assert "Connected" in connection_view.status_label.text()
        assert not connection_view.connect_btn.isEnabled()
        assert connection_view.disconnect_btn.isEnabled()
        assert not connection_view.ip_input.isEnabled()
        assert not connection_view.port_input.isEnabled()

    def test_connect_success_shows_message(
        self, connection_view, mock_connection_controller, qtbot
    ):
        """Test success message is displayed."""
        qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)
        assert "Connected successfully" in connection_view.message_label.text()

    def test_connect_failure_shows_error(
        self, connection_view, mock_connection_controller, qtbot
    ):
        """Test connection failure shows error message."""
        # Mock failure
        mock_connection_controller.connect.return_value = (False, "Connection failed")

        # Click connect
        qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)

        # Verify error displayed
        assert "Connection failed" in connection_view.message_label.text()

    def test_connect_failure_keeps_disconnected_state(
        self, connection_view, mock_connection_controller, qtbot
    ):
        """Test failed connection keeps UI in disconnected state."""
        mock_connection_controller.connect.return_value = (False, "Connection failed")

        qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)

        # Should still be disconnected
        assert connection_view.connect_btn.isEnabled()
        assert not connection_view.disconnect_btn.isEnabled()


class TestConnectionViewDisconnecting:
    """Test disconnection operations in ConnectionView."""

    def test_disconnect_button_calls_controller(
        self, connection_view, mock_connection_controller, qtbot
    ):
        """Test that clicking disconnect calls controller."""
        # First connect
        qtbot.mouseClick(connection_view.connect_btn, Qt.LeftButton)

        # Then disconnect
        qtbot.mouseClick(connection_view.disconnect_btn, Qt.LeftButton)

        # Verify controller called
        mock_connection_controller.disconnect.assert_called_once()

    def test_disconnect_success_updates_ui(
        self, connection_view, mock_connection_controller, qtbot
    ):
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

    def test_disconnect_shows_message(
        self, connection_view, mock_connection_controller, qtbot
    ):
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
        """Test error messages use the shared error color constant."""
        connection_view._show_message("Error occurred", is_error=True)
        assert ERROR_COLOR in connection_view.message_label.styleSheet()

    def test_show_success_message_color(self, connection_view):
        """Test success messages use the shared success color constant."""
        connection_view._show_message("Operation successful", is_error=False)
        assert SUCCESS_COLOR in connection_view.message_label.styleSheet()


# WorkflowView Tests


# NOTE: The old "load a workflow file by typing/browsing a path" flow
# (file_path_input, browse_btn, get_workflow_path, clear_workflow,
# _update_status(workflow_loaded=..., workflow_running=...)) was removed. The
# current WorkflowView is a comprehensive workflow *builder* with private
# widgets (_start_btn, _stop_btn, _status_label, _message_label), a Start/Stop
# flow that calls controller.start_workflow_from_ui / stop_workflow, and a
# workflow.txt Load…/Save…/preset flow (_template_combo, refresh_presets,
# get_workflow_dict / set_workflow_dict). Tests below reflect that API.


class TestWorkflowViewCreation:
    """Test WorkflowView UI creation and initialization."""

    def test_view_creates_widgets(self, workflow_view):
        """Test that the core action widgets are created."""
        assert workflow_view._start_btn is not None
        assert workflow_view._stop_btn is not None
        assert workflow_view._status_label is not None
        assert workflow_view._message_label is not None
        assert workflow_view._template_combo is not None

    def test_initial_button_state(self, workflow_view):
        """Stop is disabled until a workflow starts; Start is available."""
        assert not workflow_view._stop_btn.isEnabled()
        assert workflow_view._start_btn.isEnabled()

    def test_initial_status_text(self, workflow_view):
        """Test initial status label text."""
        assert "Ready to configure workflow" in workflow_view._status_label.text()

    def test_default_workflow_type_is_tile(self, workflow_view):
        """The builder defaults to the Tile Scan workflow type."""
        assert workflow_view.get_current_workflow_type() == "tile"


class TestWorkflowViewStartStop:
    """Test workflow start/stop operations against the controller."""

    def test_start_calls_controller_and_updates_ui(
        self, workflow_view, mock_workflow_controller, qtbot
    ):
        """A successful start calls start_workflow_from_ui and enters running state."""
        # Bypass UI build/validation and drive the start handler directly so the
        # test does not depend on every sub-panel being populated.
        workflow_view._build_workflow = Mock(return_value=Mock())
        workflow_view._validate_workflow = Mock(return_value=[])

        qtbot.mouseClick(workflow_view._start_btn, Qt.LeftButton)

        mock_workflow_controller.start_workflow_from_ui.assert_called_once()
        assert "running" in workflow_view._status_label.text().lower()
        assert not workflow_view._start_btn.isEnabled()
        assert workflow_view._stop_btn.isEnabled()

    def test_start_validation_error_does_not_call_controller(
        self, workflow_view, mock_workflow_controller, qtbot
    ):
        """Validation errors block the controller call and surface a message."""
        workflow_view._build_workflow = Mock(return_value=Mock())
        workflow_view._validate_workflow = Mock(return_value=["No illumination"])

        qtbot.mouseClick(workflow_view._start_btn, Qt.LeftButton)

        mock_workflow_controller.start_workflow_from_ui.assert_not_called()
        assert "No illumination" in workflow_view._message_label.text()

    def test_stop_calls_controller_and_restores_ui(
        self, workflow_view, mock_workflow_controller, qtbot
    ):
        """Stopping calls stop_workflow and returns to the ready state."""
        # Put the view into the running state first.
        workflow_view._set_running_state(True)

        qtbot.mouseClick(workflow_view._stop_btn, Qt.LeftButton)

        mock_workflow_controller.stop_workflow.assert_called_once()
        assert workflow_view._start_btn.isEnabled()
        assert not workflow_view._stop_btn.isEnabled()


class TestWorkflowViewConnectionState:
    """Test workflow view response to connection state changes."""

    def test_disconnected_updates_status(self, workflow_view):
        """Disconnection surfaces a 'not connected' prompt in the status label."""
        workflow_view.update_for_connection_state(connected=False)
        assert "Not connected" in workflow_view._status_label.text()

    def test_connected_enables_start(self, workflow_view):
        """Connection enables the Start button."""
        workflow_view.update_for_connection_state(connected=True)
        assert workflow_view._start_btn.isEnabled()


class TestWorkflowViewHelperMethods:
    """Test helper methods in WorkflowView."""

    def test_clear_message(self, workflow_view):
        """Test clearing message display."""
        workflow_view._message_label.setText("Test message")
        workflow_view.clear_message()
        assert workflow_view._message_label.text() == ""

    def test_show_error_message_color(self, workflow_view):
        """Error messages use the shared error color constant."""
        workflow_view._show_message("Error occurred", is_error=True)
        assert ERROR_COLOR in workflow_view._message_label.styleSheet()

    def test_show_success_message_color(self, workflow_view):
        """Success messages use the shared success color constant."""
        workflow_view._show_message("Operation successful", is_error=False)
        assert SUCCESS_COLOR in workflow_view._message_label.styleSheet()


class TestWorkflowViewWorkflowFile:
    """Test the workflow.txt load/save + preset flow (replaces path-based load)."""

    def test_preset_combo_has_none_entry(self, workflow_view):
        """The preset dropdown always offers a '(None)' entry."""
        assert workflow_view._template_combo.findText("(None)") >= 0

    def test_refresh_presets_runs(self, workflow_view):
        """refresh_presets repopulates the dropdown without error."""
        workflow_view.refresh_presets()
        assert workflow_view._template_combo.count() >= 1

    def test_workflow_dict_round_trips(self, workflow_view):
        """get_workflow_dict returns a section-keyed dict accepted by set_workflow_dict."""
        wf_dict = workflow_view.get_workflow_dict()
        assert "Start Position" in wf_dict
        assert "Stack Settings" in wf_dict
        # Applying it back should not raise.
        workflow_view.set_workflow_dict(
            wf_dict, workflow_view.get_current_workflow_type()
        )


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
        assert workflow_view._start_btn.isEnabled()

    def test_both_views_can_coexist(
        self, qapp, mock_connection_controller, mock_workflow_controller, qtbot
    ):
        """Test both views can be created and used together."""
        conn_view = ConnectionView(mock_connection_controller)
        work_view = WorkflowView(mock_workflow_controller)

        qtbot.addWidget(conn_view)
        qtbot.addWidget(work_view)

        assert conn_view is not None
        assert work_view is not None
