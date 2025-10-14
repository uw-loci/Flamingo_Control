"""
Tests for Application Layer

This test module covers:
- FlamingoApplication class (dependency injection, component creation)
- MainWindow class (UI composition, menu creation)
- CLI module (argument parsing, validation)

Tests use mocks to avoid creating real Qt applications or network connections.
"""

import os
import sys
import unittest
from unittest.mock import Mock, MagicMock, patch
from pathlib import Path
from io import StringIO

# Set Qt platform to offscreen before importing Qt
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PyQt5.QtWidgets import QApplication

from py2flamingo.application import FlamingoApplication
from py2flamingo.main_window import MainWindow
from py2flamingo import cli


class TestFlamingoApplicationInit(unittest.TestCase):
    """Test FlamingoApplication initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default parameters."""
        app = FlamingoApplication()

        self.assertEqual(app.default_ip, "127.0.0.1")
        self.assertEqual(app.default_port, 53717)
        self.assertIsNone(app.qt_app)
        self.assertIsNone(app.tcp_connection)
        self.assertIsNone(app.connection_model)

    def test_init_with_custom_values(self):
        """Test initialization with custom IP and port."""
        app = FlamingoApplication(default_ip="192.168.1.100", default_port=8080)

        self.assertEqual(app.default_ip, "192.168.1.100")
        self.assertEqual(app.default_port, 8080)

    def test_init_has_logger(self):
        """Test that application has logger configured."""
        app = FlamingoApplication()

        self.assertIsNotNone(app.logger)
        self.assertEqual(app.logger.name, "py2flamingo.application")


class TestFlamingoApplicationDependencyInjection(unittest.TestCase):
    """Test dependency injection and component creation."""

    def setUp(self):
        """Set up test fixtures."""
        self.app = FlamingoApplication()

    @patch('py2flamingo.application.ConnectionView')
    @patch('py2flamingo.application.WorkflowView')
    def _setup_dependencies_with_mocks(self, mock_workflow_view, mock_connection_view):
        """Helper to setup dependencies with mocked views."""
        self.app.setup_dependencies()
        return mock_connection_view, mock_workflow_view

    @patch('py2flamingo.application.ConnectionView')
    @patch('py2flamingo.application.WorkflowView')
    def test_setup_dependencies_creates_core_layer(self, mock_workflow_view, mock_connection_view):
        """Test that setup_dependencies creates core components."""
        self.app.setup_dependencies()

        # Core layer
        self.assertIsNotNone(self.app.tcp_connection)
        self.assertIsNotNone(self.app.protocol_encoder)

    @patch('py2flamingo.application.ConnectionView')
    @patch('py2flamingo.application.WorkflowView')
    def test_setup_dependencies_creates_models_layer(self, mock_workflow_view, mock_connection_view):
        """Test that setup_dependencies creates model components."""
        self.app.setup_dependencies()

        # Models layer
        self.assertIsNotNone(self.app.connection_model)

    @patch('py2flamingo.application.ConnectionView')
    @patch('py2flamingo.application.WorkflowView')
    def test_setup_dependencies_creates_services_layer(self, mock_workflow_view, mock_connection_view):
        """Test that setup_dependencies creates service components."""
        self.app.setup_dependencies()

        # Services layer
        self.assertIsNotNone(self.app.connection_service)
        self.assertIsNotNone(self.app.workflow_service)
        self.assertIsNotNone(self.app.status_service)

    @patch('py2flamingo.application.ConnectionView')
    @patch('py2flamingo.application.WorkflowView')
    def test_setup_dependencies_creates_controllers_layer(self, mock_workflow_view, mock_connection_view):
        """Test that setup_dependencies creates controller components."""
        self.app.setup_dependencies()

        # Controllers layer
        self.assertIsNotNone(self.app.connection_controller)
        self.assertIsNotNone(self.app.workflow_controller)

    @patch('py2flamingo.application.ConnectionView')
    @patch('py2flamingo.application.WorkflowView')
    def test_setup_dependencies_creates_views_layer(self, mock_workflow_view, mock_connection_view):
        """Test that setup_dependencies creates view components."""
        self.app.setup_dependencies()

        # Views layer (these are the mock instances now)
        mock_connection_view.assert_called_once()
        mock_workflow_view.assert_called_once()

    @patch('py2flamingo.application.ConnectionView')
    @patch('py2flamingo.application.WorkflowView')
    def test_setup_dependencies_wiring(self, mock_workflow_view, mock_connection_view):
        """Test that components are properly wired together."""
        self.app.setup_dependencies()

        # Connection service should use tcp_connection and protocol_encoder
        self.assertIs(self.app.connection_service.tcp_connection, self.app.tcp_connection)
        self.assertIs(self.app.connection_service.encoder, self.app.protocol_encoder)

        # Workflow service should use connection_service
        self.assertIs(self.app.workflow_service.connection_service, self.app.connection_service)

        # Status service should use connection_service
        self.assertIs(self.app.status_service.connection_service, self.app.connection_service)

    @patch('py2flamingo.application.ConnectionView')
    @patch('py2flamingo.application.WorkflowView')
    def test_setup_dependencies_sets_default_connection_info(self, mock_workflow_view, mock_connection_view):
        """Test that default connection info is set in view."""
        self.app.default_ip = "10.0.0.1"
        self.app.default_port = 9999

        # Create a mock instance to return from ConnectionView()
        mock_view_instance = Mock()
        mock_connection_view.return_value = mock_view_instance

        self.app.setup_dependencies()

        # Should have called set_connection_info with default values
        mock_view_instance.set_connection_info.assert_called_once_with("10.0.0.1", 9999)


class TestFlamingoApplicationMainWindow(unittest.TestCase):
    """Test main window creation."""

    def setUp(self):
        """Set up test fixtures."""
        self.app = FlamingoApplication()

    @patch('py2flamingo.main_window.MainWindow')
    @patch('py2flamingo.application.ConnectionView')
    @patch('py2flamingo.application.WorkflowView')
    def test_create_main_window(self, mock_workflow_view, mock_connection_view, mock_main_window):
        """Test that create_main_window creates MainWindow instance."""
        self.app.setup_dependencies()
        self.app.create_main_window()

        # Should have called MainWindow constructor
        mock_main_window.assert_called_once()

    @patch('py2flamingo.main_window.MainWindow')
    @patch('py2flamingo.application.ConnectionView')
    @patch('py2flamingo.application.WorkflowView')
    def test_main_window_has_views(self, mock_workflow_view, mock_connection_view, mock_main_window):
        """Test that main window receives view instances."""
        # Setup mocks
        mock_conn_view_instance = Mock()
        mock_work_view_instance = Mock()
        mock_connection_view.return_value = mock_conn_view_instance
        mock_workflow_view.return_value = mock_work_view_instance

        self.app.setup_dependencies()
        self.app.create_main_window()

        # Main window should be called with view instances
        mock_main_window.assert_called_once_with(mock_conn_view_instance, mock_work_view_instance)

    @patch('py2flamingo.main_window.MainWindow')
    @patch('py2flamingo.application.ConnectionView')
    @patch('py2flamingo.application.WorkflowView')
    def test_main_window_has_title(self, mock_workflow_view, mock_connection_view, mock_main_window):
        """Test that main window title is set."""
        mock_window_instance = Mock()
        mock_main_window.return_value = mock_window_instance

        self.app.setup_dependencies()
        self.app.create_main_window()

        # Should have called setWindowTitle
        mock_window_instance.setWindowTitle.assert_called_once()
        call_args = mock_window_instance.setWindowTitle.call_args[0][0]
        self.assertIn("Flamingo", call_args)


class TestFlamingoApplicationShutdown(unittest.TestCase):
    """Test application shutdown."""

    def setUp(self):
        """Set up test fixtures."""
        self.app = FlamingoApplication()

    @patch('py2flamingo.application.ConnectionView')
    @patch('py2flamingo.application.WorkflowView')
    def test_shutdown_disconnects_if_connected(self, mock_workflow_view, mock_connection_view):
        """Test that shutdown disconnects if connected."""
        self.app.setup_dependencies()

        # Mock connection service
        self.app.connection_service.is_connected = Mock(return_value=True)
        self.app.connection_service.disconnect = Mock()

        self.app.shutdown()

        # Should have called disconnect
        self.app.connection_service.disconnect.assert_called_once()

    @patch('py2flamingo.application.ConnectionView')
    @patch('py2flamingo.application.WorkflowView')
    def test_shutdown_no_disconnect_if_not_connected(self, mock_workflow_view, mock_connection_view):
        """Test that shutdown doesn't disconnect if not connected."""
        self.app.setup_dependencies()

        # Mock connection service
        self.app.connection_service.is_connected = Mock(return_value=False)
        self.app.connection_service.disconnect = Mock()

        self.app.shutdown()

        # Should not have called disconnect
        self.app.connection_service.disconnect.assert_not_called()

    @patch('py2flamingo.application.ConnectionView')
    @patch('py2flamingo.application.WorkflowView')
    def test_shutdown_handles_disconnect_error(self, mock_workflow_view, mock_connection_view):
        """Test that shutdown handles disconnect errors gracefully."""
        self.app.setup_dependencies()

        # Mock connection service to raise error on disconnect
        self.app.connection_service.is_connected = Mock(return_value=True)
        self.app.connection_service.disconnect = Mock(side_effect=Exception("Test error"))

        # Should not raise exception
        try:
            self.app.shutdown()
        except Exception:
            self.fail("shutdown() raised exception unexpectedly")


class TestMainWindowCreation(unittest.TestCase):
    """Test MainWindow creation and UI setup."""

    @classmethod
    def setUpClass(cls):
        """Create QApplication once for all tests."""
        cls.qt_app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        """Set up test fixtures."""
        # Import views to create real QWidgets (required for QTabWidget)
        from py2flamingo.controllers import ConnectionController, WorkflowController
        from py2flamingo.views import ConnectionView, WorkflowView

        # Create mock controllers
        mock_conn_controller = Mock()
        mock_workflow_controller = Mock()

        # Create real views with mock controllers
        self.connection_view = ConnectionView(mock_conn_controller)
        self.workflow_view = WorkflowView(mock_workflow_controller)

    def test_main_window_init(self):
        """Test MainWindow initialization."""
        window = MainWindow(self.connection_view, self.workflow_view)

        self.assertIs(window.connection_view, self.connection_view)
        self.assertIs(window.workflow_view, self.workflow_view)

    def test_main_window_has_tabs(self):
        """Test that main window creates tab widget."""
        window = MainWindow(self.connection_view, self.workflow_view)

        self.assertIsNotNone(window.tabs)
        self.assertEqual(window.tabs.count(), 2)

    def test_main_window_has_status_bar(self):
        """Test that main window has status bar."""
        window = MainWindow(self.connection_view, self.workflow_view)

        status_bar = window.statusBar()
        self.assertIsNotNone(status_bar)

    def test_main_window_has_menu_bar(self):
        """Test that main window has menu bar."""
        window = MainWindow(self.connection_view, self.workflow_view)

        menu_bar = window.menuBar()
        self.assertIsNotNone(menu_bar)

    def test_main_window_has_file_menu(self):
        """Test that main window has File menu."""
        window = MainWindow(self.connection_view, self.workflow_view)

        actions = window.menuBar().actions()
        menu_titles = [action.text() for action in actions]

        self.assertIn("&File", menu_titles)

    def test_main_window_has_help_menu(self):
        """Test that main window has Help menu."""
        window = MainWindow(self.connection_view, self.workflow_view)

        actions = window.menuBar().actions()
        menu_titles = [action.text() for action in actions]

        self.assertIn("&Help", menu_titles)

    def test_close_event_accepts(self):
        """Test that close event is accepted."""
        window = MainWindow(self.connection_view, self.workflow_view)

        # Mock close event
        event = Mock()

        window.closeEvent(event)

        # Should accept the event
        event.accept.assert_called_once()


class TestCLIArgumentParsing(unittest.TestCase):
    """Test CLI argument parsing."""

    def test_parse_args_defaults(self):
        """Test parsing with default values."""
        args = cli.parse_args([])

        self.assertEqual(args.ip, "127.0.0.1")
        self.assertEqual(args.port, 53717)
        self.assertIsNone(args.workflow)
        self.assertFalse(args.headless)
        self.assertEqual(args.log_level, "INFO")

    def test_parse_args_custom_ip(self):
        """Test parsing custom IP address."""
        args = cli.parse_args(["--ip", "192.168.1.100"])

        self.assertEqual(args.ip, "192.168.1.100")

    def test_parse_args_custom_port(self):
        """Test parsing custom port."""
        args = cli.parse_args(["--port", "8080"])

        self.assertEqual(args.port, 8080)

    def test_parse_args_workflow(self):
        """Test parsing workflow path."""
        args = cli.parse_args(["--workflow", "/path/to/workflow.txt"])

        self.assertEqual(args.workflow, "/path/to/workflow.txt")

    def test_parse_args_headless(self):
        """Test parsing headless flag."""
        args = cli.parse_args(["--headless"])

        self.assertTrue(args.headless)

    def test_parse_args_log_level(self):
        """Test parsing log level."""
        args = cli.parse_args(["--log-level", "DEBUG"])

        self.assertEqual(args.log_level, "DEBUG")

    def test_parse_args_all_options(self):
        """Test parsing all options together."""
        args = cli.parse_args([
            "--ip", "10.0.0.1",
            "--port", "9999",
            "--workflow", "test.txt",
            "--headless",
            "--log-level", "WARNING"
        ])

        self.assertEqual(args.ip, "10.0.0.1")
        self.assertEqual(args.port, 9999)
        self.assertEqual(args.workflow, "test.txt")
        self.assertTrue(args.headless)
        self.assertEqual(args.log_level, "WARNING")


class TestCLIValidation(unittest.TestCase):
    """Test CLI argument validation."""

    def test_validate_args_valid(self):
        """Test validation with valid arguments."""
        args = cli.parse_args([])

        result = cli.validate_args(args)

        self.assertTrue(result)

    def test_validate_args_invalid_port_low(self):
        """Test validation with port too low."""
        args = cli.parse_args(["--port", "0"])

        with patch('sys.stdout', new=StringIO()):
            result = cli.validate_args(args)

        self.assertFalse(result)

    def test_validate_args_invalid_port_high(self):
        """Test validation with port too high."""
        args = cli.parse_args(["--port", "70000"])

        with patch('sys.stdout', new=StringIO()):
            result = cli.validate_args(args)

        self.assertFalse(result)

    def test_validate_args_empty_ip(self):
        """Test validation with empty IP address."""
        args = cli.parse_args(["--ip", ""])

        with patch('sys.stdout', new=StringIO()):
            result = cli.validate_args(args)

        self.assertFalse(result)

    def test_validate_args_workflow_not_found(self):
        """Test validation with non-existent workflow file."""
        args = cli.parse_args(["--workflow", "/nonexistent/file.txt"])

        with patch('sys.stdout', new=StringIO()):
            result = cli.validate_args(args)

        self.assertFalse(result)

    def test_validate_args_headless_warning(self):
        """Test that headless mode shows warning."""
        args = cli.parse_args(["--headless"])

        with patch('sys.stdout', new=StringIO()) as mock_stdout:
            result = cli.validate_args(args)
            output = mock_stdout.getvalue()

        # Should still return True but show warning
        self.assertTrue(result)
        self.assertIn("Warning", output)


class TestCLIMain(unittest.TestCase):
    """Test CLI main entry point."""

    @patch('py2flamingo.cli.FlamingoApplication')
    def test_main_creates_application(self, mock_app_class):
        """Test that main creates FlamingoApplication."""
        # Mock the application instance
        mock_app_instance = Mock()
        mock_app_instance.run.return_value = 0
        mock_app_class.return_value = mock_app_instance

        # Run main
        exit_code = cli.main([])

        # Should have created application
        mock_app_class.assert_called_once_with(
            default_ip="127.0.0.1",
            default_port=53717
        )

    @patch('py2flamingo.cli.FlamingoApplication')
    def test_main_runs_application(self, mock_app_class):
        """Test that main runs the application."""
        # Mock the application instance
        mock_app_instance = Mock()
        mock_app_instance.run.return_value = 0
        mock_app_class.return_value = mock_app_instance

        # Run main
        exit_code = cli.main([])

        # Should have called run
        mock_app_instance.run.assert_called_once()

    @patch('py2flamingo.cli.FlamingoApplication')
    def test_main_returns_exit_code(self, mock_app_class):
        """Test that main returns application exit code."""
        # Mock the application instance
        mock_app_instance = Mock()
        mock_app_instance.run.return_value = 42
        mock_app_class.return_value = mock_app_instance

        # Run main
        exit_code = cli.main([])

        self.assertEqual(exit_code, 42)

    def test_main_invalid_port_returns_error(self):
        """Test that main returns error code for invalid port."""
        with patch('sys.stdout', new=StringIO()):
            exit_code = cli.main(["--port", "0"])

        self.assertEqual(exit_code, 1)

    @patch('py2flamingo.cli.FlamingoApplication')
    def test_main_handles_exception(self, mock_app_class):
        """Test that main handles exceptions gracefully."""
        # Mock the application to raise exception
        mock_app_class.side_effect = Exception("Test error")

        with patch('sys.stdout', new=StringIO()):
            exit_code = cli.main([])

        # Should return error code
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
