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

from py2flamingo.core import ProtocolEncoder, TCPConnection, QueueManager
from py2flamingo.models import (
    ConnectionModel, WorkflowModel, WorkflowType, Position, ImageDisplayModel
)
from py2flamingo.services import (
    MVCConnectionService, MVCWorkflowService, StatusService, ConfigurationManager
)
from py2flamingo.controllers import ConnectionController, WorkflowController
from py2flamingo.views import ConnectionView, WorkflowView
from py2flamingo.views.live_feed_view import LiveFeedView


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

    def __init__(self, default_ip: Optional[str] = None, default_port: Optional[int] = None):
        """Initialize application with optional default connection settings.

        Args:
            default_ip: Default server IP address (None = user selects via GUI)
            default_port: Default server port (None = user selects via GUI)
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
        self.workflow_model: Optional[WorkflowModel] = None
        self.display_model: Optional[ImageDisplayModel] = None

        # Services layer components
        self.connection_service: Optional[MVCConnectionService] = None
        self.workflow_service: Optional[MVCWorkflowService] = None
        self.status_service: Optional[StatusService] = None
        self.config_manager: Optional[ConfigurationManager] = None

        # Controllers layer components
        self.connection_controller: Optional[ConnectionController] = None
        self.workflow_controller: Optional[WorkflowController] = None

        # Views layer components
        self.connection_view: Optional[ConnectionView] = None
        self.workflow_view: Optional[WorkflowView] = None
        self.live_feed_view: Optional[LiveFeedView] = None

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
        self.queue_manager = QueueManager()

        # Models layer - data models
        self.logger.debug("Creating models layer components...")
        self.connection_model = ConnectionModel()

        # Create a default workflow model for state tracking
        self.workflow_model = WorkflowModel.create_snapshot(
            position=Position(x=0.0, y=0.0, z=0.0, r=0.0)
        )

        # Create image display settings model
        self.display_model = ImageDisplayModel()

        # Services layer - business logic
        self.logger.debug("Creating services layer components...")
        self.connection_service = MVCConnectionService(
            self.tcp_connection,
            self.protocol_encoder,
            self.queue_manager
        )

        self.workflow_service = MVCWorkflowService(
            self.connection_service
        )

        self.status_service = StatusService(
            self.connection_service
        )

        self.config_manager = ConfigurationManager(
            config_file="saved_configurations.json"
        )

        # Controllers layer - coordinate services and views
        self.logger.debug("Creating controllers layer components...")
        self.connection_controller = ConnectionController(
            self.connection_service,
            self.connection_model,
            self.config_manager
        )

        self.workflow_controller = WorkflowController(
            self.workflow_service,
            self.connection_model,
            self.workflow_model
        )

        # Views layer - UI components
        self.logger.debug("Creating views layer components...")
        self.connection_view = ConnectionView(
            self.connection_controller,
            config_manager=self.config_manager
        )
        self.workflow_view = WorkflowView(self.workflow_controller)

        # Create live feed view with visualize queue from connection service
        self.logger.debug("Creating live feed view...")
        visualize_queue = self.connection_service.queue_manager.get_queue('visualize')
        self.live_feed_view = LiveFeedView(
            workflow_controller=self.workflow_controller,
            visualize_queue=visualize_queue,
            display_model=self.display_model,
            update_interval_ms=500  # Poll every 500ms
        )

        # Set default connection values in view if provided via CLI
        if self.default_ip is not None and self.default_port is not None:
            self.logger.debug(f"Setting CLI defaults: {self.default_ip}:{self.default_port}")
            self.connection_view.set_connection_info(self.default_ip, self.default_port)

        self.logger.info("Application dependencies setup complete")

    def create_main_window(self):
        """Create main application window by composing views.

        This method imports the MainWindow class and creates the main
        window, passing in the views created during dependency setup.

        The MainWindow is responsible for:
        - Composing ConnectionView, WorkflowView, and LiveFeedView
        - Creating menu bar and status bar
        - Managing window lifecycle
        """
        from py2flamingo.main_window import MainWindow

        self.logger.info("Creating main window...")
        self.main_window = MainWindow(
            self.connection_view,
            self.workflow_view,
            self.live_feed_view
        )
        self.main_window.setWindowTitle("Flamingo Microscope Control")
        self.main_window.resize(1000, 700)  # Larger to accommodate live feed
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
