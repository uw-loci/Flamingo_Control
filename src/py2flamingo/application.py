"""
Application Layer - Dependency Injection and Component Wiring

This module provides the main FlamingoApplication class that handles:
- Dependency injection and component creation
- Wiring all MVC layers together (Core → Models → Services → Controllers → Views)
- Application lifecycle management
- Clean resource management

The FlamingoApplication class follows the dependency injection pattern,
creating components in the correct order and passing dependencies through
constructor injection.
"""

import sys
import logging
from typing import Optional
from PyQt5.QtWidgets import QApplication

from py2flamingo.core import ProtocolEncoder, TCPConnection
from py2flamingo.models import ConnectionModel
from py2flamingo.services import MVCConnectionService, MVCWorkflowService, StatusService
from py2flamingo.controllers import ConnectionController, WorkflowController
from py2flamingo.views import ConnectionView, WorkflowView


class FlamingoApplication:
    """Main application class handling dependency injection and lifecycle.

    This class creates and wires all application components using dependency
    injection, following the MVC architecture:

    Core Layer (tcp_connection, protocol_encoder)
        ↓
    Models Layer (connection_model)
        ↓
    Services Layer (connection_service, workflow_service, status_service)
        ↓
    Controllers Layer (connection_controller, workflow_controller)
        ↓
    Views Layer (connection_view, workflow_view)
        ↓
    Main Window (composition of views)

    Example:
        app = FlamingoApplication(default_ip="127.0.0.1", default_port=53717)
        sys.exit(app.run())
    """

    def __init__(self, default_ip: str = "127.0.0.1", default_port: int = 53717):
        """Initialize application with default connection settings.

        Args:
            default_ip: Default server IP address
            default_port: Default server port
        """
        self.default_ip = default_ip
        self.default_port = default_port

        # Qt application
        self.qt_app: Optional[QApplication] = None
        self.main_window = None

        # Core layer components (initialized in setup_dependencies)
        self.tcp_connection: Optional[TCPConnection] = None
        self.protocol_encoder: Optional[ProtocolEncoder] = None

        # Models layer components
        self.connection_model: Optional[ConnectionModel] = None

        # Services layer components
        self.connection_service: Optional[MVCConnectionService] = None
        self.workflow_service: Optional[MVCWorkflowService] = None
        self.status_service: Optional[StatusService] = None

        # Controllers layer components
        self.connection_controller: Optional[ConnectionController] = None
        self.workflow_controller: Optional[WorkflowController] = None

        # Views layer components
        self.connection_view: Optional[ConnectionView] = None
        self.workflow_view: Optional[WorkflowView] = None

        # Setup logging
        self.logger = logging.getLogger(__name__)

    def setup_dependencies(self):
        """Create and wire all application components using dependency injection.

        Components are created in dependency order:
        1. Core layer (no dependencies)
        2. Models layer (no dependencies)
        3. Services layer (depend on Core and Models)
        4. Controllers layer (depend on Services and Models)
        5. Views layer (depend on Controllers)

        This method ensures all components are properly initialized and
        dependencies are injected correctly.
        """
        self.logger.info("Setting up application dependencies...")

        # Core layer - foundation components
        self.logger.debug("Creating core layer components...")
        self.tcp_connection = TCPConnection()
        self.protocol_encoder = ProtocolEncoder()

        # Models layer - data models
        self.logger.debug("Creating models layer components...")
        self.connection_model = ConnectionModel()

        # Services layer - business logic
        self.logger.debug("Creating services layer components...")
        self.connection_service = MVCConnectionService(
            self.tcp_connection,
            self.protocol_encoder
        )

        self.workflow_service = MVCWorkflowService(
            self.connection_service
        )

        self.status_service = StatusService(
            self.connection_service
        )

        # Controllers layer - coordinate services and views
        self.logger.debug("Creating controllers layer components...")
        self.connection_controller = ConnectionController(
            self.connection_service,
            self.connection_model
        )

        self.workflow_controller = WorkflowController(
            self.workflow_service,
            self.connection_model
        )

        # Views layer - UI components
        self.logger.debug("Creating views layer components...")
        self.connection_view = ConnectionView(self.connection_controller)
        self.workflow_view = WorkflowView(self.workflow_controller)

        # Set default connection values in view
        self.connection_view.set_connection_info(self.default_ip, self.default_port)

        self.logger.info("Application dependencies setup complete")

    def create_main_window(self):
        """Create main application window by composing views.

        This method imports the MainWindow class and creates the main
        window, passing in the views created during dependency setup.

        The MainWindow is responsible for:
        - Composing ConnectionView and WorkflowView
        - Creating menu bar and status bar
        - Managing window lifecycle
        """
        from py2flamingo.main_window import MainWindow

        self.logger.info("Creating main window...")
        self.main_window = MainWindow(self.connection_view, self.workflow_view)
        self.main_window.setWindowTitle("Flamingo Microscope Control")
        self.main_window.resize(600, 400)
        self.logger.info("Main window created")

    def run(self) -> int:
        """Start the application.

        This method:
        1. Creates Qt application
        2. Sets up all dependencies
        3. Creates and shows main window
        4. Runs Qt event loop
        5. Returns exit code

        Returns:
            Exit code from Qt application (0 = success)
        """
        self.logger.info("Starting Flamingo application...")

        # Create Qt application
        self.qt_app = QApplication(sys.argv)
        self.qt_app.setApplicationName("Flamingo Microscope Control")

        # Setup all dependencies
        self.setup_dependencies()

        # Create and show main window
        self.create_main_window()
        self.main_window.show()

        self.logger.info("Application running, entering event loop...")

        # Run Qt event loop and return exit code
        exit_code = self.qt_app.exec_()

        # Cleanup on exit
        self.shutdown()

        return exit_code

    def shutdown(self):
        """Clean up resources before application exit.

        This method:
        - Disconnects from microscope if connected
        - Cleans up network resources
        - Logs shutdown

        Called automatically at application exit.
        """
        self.logger.info("Shutting down application...")

        # Disconnect if connected
        if self.connection_service and self.connection_service.is_connected():
            self.logger.info("Disconnecting from microscope...")
            try:
                self.connection_service.disconnect()
            except Exception as e:
                self.logger.error(f"Error during disconnect: {e}")

        self.logger.info("Application shutdown complete")
