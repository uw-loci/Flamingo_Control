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
    MVCConnectionService, MVCWorkflowService, StatusService, ConfigurationManager,
    StatusIndicatorService
)
from py2flamingo.controllers import ConnectionController, WorkflowController, PositionController
from py2flamingo.controllers.movement_controller import MovementController
from py2flamingo.controllers.camera_controller import CameraController
from py2flamingo.views import ConnectionView, WorkflowView, SampleInfoView, StageControlView
from py2flamingo.views.live_feed_view import LiveFeedView
from py2flamingo.views.enhanced_stage_control_view import EnhancedStageControlView
from py2flamingo.views.camera_live_viewer import CameraLiveViewer


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
        self.status_indicator_service: Optional[StatusIndicatorService] = None
        self.config_manager: Optional[ConfigurationManager] = None

        # Controllers layer components
        self.connection_controller: Optional[ConnectionController] = None
        self.workflow_controller: Optional[WorkflowController] = None
        self.movement_controller: Optional[MovementController] = None
        self.camera_controller: Optional[CameraController] = None

        # Views layer components
        self.connection_view: Optional[ConnectionView] = None
        self.workflow_view: Optional[WorkflowView] = None
        self.sample_info_view: Optional[SampleInfoView] = None
        self.live_feed_view: Optional[LiveFeedView] = None
        self.stage_control_view: Optional[StageControlView] = None

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

        self.status_indicator_service = StatusIndicatorService(
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

        self.position_controller = PositionController(
            self.connection_service
        )

        # Create enhanced movement controller for stage control
        self.movement_controller = MovementController(
            self.connection_service,
            self.position_controller
        )

        # Create camera controller for live feed
        from py2flamingo.services.camera_service import CameraService
        self.camera_service = CameraService(self.connection_service)
        self.camera_controller = CameraController(self.camera_service)
        self.camera_controller.set_max_display_fps(30.0)

        # Wire motion tracking to status indicator
        from py2flamingo.controllers.position_controller_adapter import wire_motion_tracking
        wire_motion_tracking(self.position_controller, self.status_indicator_service)

        # Views layer - UI components
        self.logger.debug("Creating views layer components...")
        self.connection_view = ConnectionView(
            self.connection_controller,
            config_manager=self.config_manager,
            position_controller=self.position_controller  # For debug features
        )
        self.workflow_view = WorkflowView(self.workflow_controller)

        # Create sample info view
        self.logger.debug("Creating sample info view...")
        self.sample_info_view = SampleInfoView()

        # Create live feed view with visualize queue from connection service
        self.logger.debug("Creating live feed view...")
        visualize_queue = self.connection_service.queue_manager.get_queue('visualize')
        self.live_feed_view = LiveFeedView(
            workflow_controller=self.workflow_controller,
            visualize_queue=visualize_queue,
            display_model=self.display_model,
            position_controller=self.position_controller,
            update_interval_ms=500  # Poll every 500ms
        )

        # Create enhanced stage control view
        self.logger.debug("Creating enhanced stage control view...")
        self.enhanced_stage_control_view = EnhancedStageControlView(
            controller=self.movement_controller
        )

        # Keep old stage control view for compatibility
        self.stage_control_view = StageControlView(
            controller=self.position_controller
        )

        # Create camera live viewer
        self.logger.debug("Creating camera live viewer...")
        self.camera_live_viewer = CameraLiveViewer(
            controller=self.camera_controller
        )

        # Set default connection values in view if provided via CLI
        if self.default_ip is not None and self.default_port is not None:
            self.logger.debug(f"Setting CLI defaults: {self.default_ip}:{self.default_port}")
            self.connection_view.set_connection_info(self.default_ip, self.default_port)

        # Connect signals for position updates
        # When connection is established, request position update from microscope
        if hasattr(self.connection_view, 'connection_established'):
            self.connection_view.connection_established.connect(
                lambda: self._on_connection_established()
            )
            self.logger.debug("Connected connection_established signal to position update")

        # Connect connection status to stage control view
        if hasattr(self.connection_view, 'connection_established'):
            self.connection_view.connection_established.connect(
                lambda: self._on_stage_connection_established()
            )
        if hasattr(self.connection_view, 'connection_closed'):
            self.connection_view.connection_closed.connect(
                lambda: self._on_stage_connection_closed()
            )

        # Connect connection status to status indicator service
        if hasattr(self.connection_view, 'connection_established'):
            self.connection_view.connection_established.connect(
                lambda: self.status_indicator_service.on_connection_established()
            )
            self.logger.debug("Connected connection_established to status indicator service")
        if hasattr(self.connection_view, 'connection_closed'):
            self.connection_view.connection_closed.connect(
                lambda: self.status_indicator_service.on_connection_closed()
            )
            self.logger.debug("Connected connection_closed to status indicator service")

        # Connect workflow events to status indicator service
        if hasattr(self.workflow_view, 'workflow_started'):
            self.workflow_view.workflow_started.connect(
                lambda: self.status_indicator_service.on_workflow_started()
            )
            self.logger.debug("Connected workflow_started to status indicator service")
        if hasattr(self.workflow_view, 'workflow_stopped'):
            self.workflow_view.workflow_stopped.connect(
                lambda: self.status_indicator_service.on_workflow_stopped()
            )
            self.logger.debug("Connected workflow_stopped to status indicator service")

        self.logger.info("Application dependencies setup complete")

    def _on_connection_established(self):
        """Handle connection established event.

        This method is called when connection to the microscope is successfully
        established. It requests the current position from the microscope to
        update the position display.
        """
        self.logger.info("Connection established, requesting position update...")
        if self.live_feed_view and hasattr(self.live_feed_view, 'request_position_update'):
            # Small delay to ensure connection is fully established
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(500, self.live_feed_view.request_position_update)
        else:
            self.logger.warning("Cannot request position: live_feed_view not available")

    def _on_stage_connection_established(self):
        """Handle connection established event for stage control view.

        Updates the stage control view to enable controls and display current position.
        """
        self.logger.info("Updating stage control view - connection established")
        if self.stage_control_view:
            self.stage_control_view.set_connected(True)
            # Update position display
            position = self.position_controller.get_current_position()
            if position:
                self.stage_control_view.update_position(
                    position.x, position.y, position.z, position.r
                )

    def _on_stage_connection_closed(self):
        """Handle connection closed event for stage control view.

        Updates the stage control view to disable controls.
        """
        self.logger.info("Updating stage control view - connection closed")
        if self.stage_control_view:
            self.stage_control_view.set_connected(False)

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
        from py2flamingo.views.widgets.status_indicator_widget import StatusIndicatorWidget

        self.logger.info("Creating main window...")

        # Create status indicator widget
        self.status_indicator_widget = StatusIndicatorWidget()

        # Connect status indicator service to widget
        if self.status_indicator_service:
            self.status_indicator_service.status_changed.connect(
                self.status_indicator_widget.update_status
            )
            self.logger.debug("Connected status indicator service to widget")

        # Create main window with all views
        self.main_window = MainWindow(
            self.connection_view,
            self.workflow_view,
            self.sample_info_view,
            self.live_feed_view,
            self.stage_control_view,
            self.status_indicator_widget,
            enhanced_stage_control_view=self.enhanced_stage_control_view,
            camera_live_viewer=self.camera_live_viewer
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
