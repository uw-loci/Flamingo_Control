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
from PyQt5.QtCore import QObject, pyqtSignal

from py2flamingo.core import ProtocolEncoder, TCPConnection, QueueManager
from py2flamingo.core.events import EventManager
from py2flamingo.models import (
    ConnectionModel, WorkflowModel, WorkflowType, Position, ImageDisplayModel
)
from py2flamingo.services import (
    MVCConnectionService, MVCWorkflowService, StatusService, ConfigurationManager,
    StatusIndicatorService, WindowGeometryManager
)
from py2flamingo.controllers import ConnectionController, WorkflowController, PositionController
from py2flamingo.controllers.movement_controller import MovementController
from py2flamingo.controllers.camera_controller import CameraController
from py2flamingo.views import ConnectionView, WorkflowView, SampleInfoView, ImageControlsWindow, StageControlView, SampleView
from py2flamingo.views.camera_live_viewer import CameraLiveViewer
from py2flamingo.views.stage_chamber_visualization_window import StageChamberVisualizationWindow
from py2flamingo.views.sample_3d_visualization_window import Sample3DVisualizationWindow


class FlamingoApplication(QObject):
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

    Signals:
        acquisition_started: Emitted when an acquisition process begins (e.g., LED 2D Overview scan)
        acquisition_stopped: Emitted when an acquisition process ends

    Example:
        app = FlamingoApplication(default_ip="127.0.0.1", default_port=53717)
        sys.exit(app.run())
    """

    # Signals for acquisition state management
    # These are used to lock/unlock microscope controls during scanning operations
    acquisition_started = pyqtSignal()
    acquisition_stopped = pyqtSignal()

    def __init__(self, default_ip: Optional[str] = None, default_port: Optional[int] = None):
        """Initialize application with optional default connection settings.

        Args:
            default_ip: Default server IP address (None = user selects via GUI)
            default_port: Default server port (None = user selects via GUI)
        """
        super().__init__()

        self.default_ip = default_ip
        self.default_port = default_port

        # Acquisition state tracking
        # When True, microscope controls should be disabled to prevent interference
        self._acquisition_in_progress = False

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
        self.geometry_manager: Optional[WindowGeometryManager] = None

        # Controllers layer components
        self.connection_controller: Optional[ConnectionController] = None
        self.workflow_controller: Optional[WorkflowController] = None
        self.movement_controller: Optional[MovementController] = None
        self.camera_controller: Optional[CameraController] = None

        # Views layer components
        self.connection_view: Optional[ConnectionView] = None
        self.workflow_view: Optional[WorkflowView] = None
        self.sample_info_view: Optional[SampleInfoView] = None
        self.stage_control_view: Optional[StageControlView] = None
        self.camera_live_viewer: Optional[CameraLiveViewer] = None
        self.image_controls_window: Optional[ImageControlsWindow] = None
        self.stage_chamber_visualization_window: Optional[StageChamberVisualizationWindow] = None
        self.sample_3d_visualization_window: Optional[Sample3DVisualizationWindow] = None
        self.sample_view: Optional['SampleView'] = None  # Unified sample viewing interface

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
        self.event_manager = EventManager()

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
            self.connection_service,
            self.event_manager
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

        self.geometry_manager = WindowGeometryManager(
            config_file="window_geometry.json"
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
            self.workflow_model,
            connection_service=self.connection_service
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
        from py2flamingo.services.configuration_service import ConfigurationService
        from py2flamingo.services.laser_led_service import LaserLEDService
        from py2flamingo.controllers.laser_led_controller import LaserLEDController

        # Configuration service for laser/LED settings
        self.config_service = ConfigurationService()

        # Laser/LED service and controller
        self.laser_led_service = LaserLEDService(self.connection_service, self.config_service)
        self.laser_led_controller = LaserLEDController(self.laser_led_service)

        # Camera service and controller with laser/LED coordination
        self.camera_service = CameraService(self.connection_service)
        self.camera_controller = CameraController(self.camera_service, self.laser_led_controller)
        self.camera_controller.set_max_display_fps(30.0)

        # Wire motion tracking from MovementController to status indicator
        # This connects the enhanced stage controller's motion signals to the global status indicator
        from py2flamingo.controllers.position_controller_adapter import wire_motion_tracking
        wire_motion_tracking(self.movement_controller, self.status_indicator_service)

        # Views layer - UI components
        self.logger.debug("Creating views layer components...")
        self.connection_view = ConnectionView(
            self.connection_controller,
            config_manager=self.config_manager,
            position_controller=self.position_controller  # For debug features
        )
        self.workflow_view = WorkflowView(self.workflow_controller)
        # Set app reference for save drive persistence
        self.workflow_view.set_app(self)

        # Create sample info view
        self.logger.debug("Creating sample info view...")
        self.sample_info_view = SampleInfoView()

        # Create stage control view
        self.logger.debug("Creating stage control view...")
        self.stage_control_view = StageControlView(
            movement_controller=self.movement_controller
        )

        # Create independent image controls window FIRST
        self.logger.debug("Creating image controls window...")
        self.image_controls_window = ImageControlsWindow(geometry_manager=self.geometry_manager)
        # Window starts hidden - user can open it via menu
        self.image_controls_window.hide()

        # Create camera live viewer with laser/LED control and image controls reference
        self.logger.debug("Creating camera live viewer...")
        self.camera_live_viewer = CameraLiveViewer(
            camera_controller=self.camera_controller,
            laser_led_controller=self.laser_led_controller,
            image_controls_window=self.image_controls_window,
            geometry_manager=self.geometry_manager
        )

        # Camera live viewer also starts hidden - user can open it via menu
        self.camera_live_viewer.hide()

        # Create stage chamber visualization window
        self.logger.debug("Creating stage chamber visualization window...")
        self.stage_chamber_visualization_window = StageChamberVisualizationWindow(
            movement_controller=self.movement_controller,
            geometry_manager=self.geometry_manager
        )
        # Window starts hidden - user can open it via menu
        self.stage_chamber_visualization_window.hide()

        # Create 3D sample visualization window
        self.logger.debug("Creating 3D sample visualization window...")
        self.sample_3d_visualization_window = Sample3DVisualizationWindow(
            movement_controller=self.movement_controller,
            camera_controller=self.camera_controller,
            laser_led_controller=self.laser_led_controller,
            geometry_manager=self.geometry_manager
        )
        # Window starts hidden - user can open it via menu
        self.sample_3d_visualization_window.hide()

        # Connect image controls to camera live viewer
        if self.image_controls_window and self.camera_live_viewer:
            # Connect display transformation signals
            self.image_controls_window.rotation_changed.connect(
                self.camera_live_viewer.set_rotation
            )
            self.image_controls_window.flip_horizontal_changed.connect(
                self.camera_live_viewer.set_flip_horizontal
            )
            self.image_controls_window.flip_vertical_changed.connect(
                self.camera_live_viewer.set_flip_vertical
            )
            self.image_controls_window.colormap_changed.connect(
                self.camera_live_viewer.set_colormap
            )
            self.image_controls_window.intensity_range_changed.connect(
                self.camera_live_viewer.set_intensity_range
            )
            self.image_controls_window.auto_scale_changed.connect(
                self.camera_live_viewer.set_auto_scale
            )
            self.image_controls_window.zoom_changed.connect(
                self.camera_live_viewer.set_zoom
            )
            self.image_controls_window.crosshair_changed.connect(
                self.camera_live_viewer.set_crosshair
            )
            self.logger.debug("Connected image controls to camera live viewer")

        # Set default connection values in view if provided via CLI
        if self.default_ip is not None and self.default_port is not None:
            self.logger.debug(f"Setting CLI defaults: {self.default_ip}:{self.default_port}")
            self.connection_view.set_connection_info(self.default_ip, self.default_port)

        # Connect connection status to enhanced stage control view
        if hasattr(self.connection_view, 'connection_established'):
            self.connection_view.connection_established.connect(
                lambda: self._on_stage_connection_established()
            )
            self.logger.debug("Connected connection_established to enhanced stage control")
        if hasattr(self.connection_view, 'connection_closed'):
            self.connection_view.connection_closed.connect(
                lambda: self._on_stage_connection_closed()
            )
            self.logger.debug("Connected connection_closed to enhanced stage control")

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

        # Connect workflow view to position controller for "Use Current Position" button
        if hasattr(self.workflow_view, 'set_position_callback') and self.position_controller:
            self.workflow_view.set_position_callback(
                self.position_controller.get_current_position
            )
            self.logger.debug("Connected workflow view to position controller")

        # Connect workflow view to connection state for enable/disable
        if hasattr(self.connection_view, 'connection_established') and hasattr(self.workflow_view, 'update_for_connection_state'):
            self.connection_view.connection_established.connect(
                lambda: self.workflow_view.update_for_connection_state(True)
            )
            self.logger.debug("Connected connection_established to workflow view")
        if hasattr(self.connection_view, 'connection_closed') and hasattr(self.workflow_view, 'update_for_connection_state'):
            self.connection_view.connection_closed.connect(
                lambda: self.workflow_view.update_for_connection_state(False)
            )
            self.logger.debug("Connected connection_closed to workflow view")

        # Connect Sample View request signal
        if hasattr(self.connection_view, 'sample_view_requested'):
            self.connection_view.sample_view_requested.connect(self._open_sample_view)
            self.logger.debug("Connected sample_view_requested signal")

        # Connect acquisition lock signals to stage control view
        if self.stage_control_view:
            self.acquisition_started.connect(
                lambda: self.stage_control_view._set_controls_enabled(False)
            )
            self.acquisition_stopped.connect(
                lambda: self.stage_control_view._set_controls_enabled(True)
            )
            self.logger.debug("Connected acquisition signals to stage control view")

        # Connect acquisition lock signals to stage chamber visualization
        if self.stage_chamber_visualization_window:
            if hasattr(self.stage_chamber_visualization_window, '_set_sliders_enabled'):
                self.acquisition_started.connect(
                    lambda: self.stage_chamber_visualization_window._set_sliders_enabled(False)
                )
                self.acquisition_stopped.connect(
                    lambda: self.stage_chamber_visualization_window._set_sliders_enabled(True)
                )
                self.logger.debug("Connected acquisition signals to stage chamber visualization")

        self.logger.info("Application dependencies setup complete")


    def _on_stage_connection_established(self):
        """Handle connection established event for enhanced stage control view.

        Enables controls and queries initial position from hardware.
        Queries position using blocking I/O in background thread to avoid freezing the GUI.
        """
        self.logger.info("Connection established - enabling stage controls")

        # Update main window menu states
        if self.main_window:
            self.main_window.update_menu_states(connected=True)

        # Reinitialize motion tracker now that connection is established
        if self.position_controller:
            self.position_controller.reinitialize_motion_tracker()

        # Enable controls immediately (connection is established)
        if self.stage_control_view:
            self.stage_control_view._set_controls_enabled(True)

        # Query position in background thread to avoid blocking GUI
        # StageService.get_position() uses blocking socket I/O
        def query_and_update_position():
            """Query position from hardware and update views (runs in background thread)."""
            try:
                from py2flamingo.services.stage_service import StageService
                stage_service = StageService(self.connection_service)

                # Query position from hardware (blocking call - queries all 4 axes)
                position = stage_service.get_position()

                if position:
                    self.logger.info(f"Queried position from hardware: {position}")

                    # Update position controller's cached position
                    self.position_controller._current_position = position

                    # Update enhanced stage control view via Qt signal (thread-safe)
                    # This triggers immediate UI update
                    if self.stage_control_view:
                        self.movement_controller.position_changed.emit(
                            position.x, position.y, position.z, position.r
                        )
                else:
                    self.logger.warning("Failed to query position from hardware - no response")

            except Exception as e:
                self.logger.error(f"Error querying position from hardware: {e}", exc_info=True)

        # Run query in background thread to avoid blocking GUI during socket I/O
        import threading
        query_thread = threading.Thread(
            target=query_and_update_position,
            daemon=True,
            name="PositionQuery"
        )
        query_thread.start()

    def _on_stage_connection_closed(self):
        """Handle connection closed event for enhanced stage control view.

        Disables controls when disconnected from microscope.
        """
        self.logger.info("Connection closed - disabling stage controls")

        # Update main window menu states
        if self.main_window:
            self.main_window.update_menu_states(connected=False)

        if self.stage_control_view:
            self.stage_control_view._set_controls_enabled(False)

    def _open_sample_view(self):
        """Open the integrated Sample View window.

        Creates the SampleView if it doesn't exist, then shows and raises it.
        """
        self.logger.info("Opening Sample View")

        if self.sample_view is None:
            self.logger.debug("Creating new SampleView instance")
            # Get voxel_storage from 3D visualization window if available
            voxel_storage = None
            if self.sample_3d_visualization_window:
                voxel_storage = getattr(self.sample_3d_visualization_window, 'voxel_storage', None)

            self.sample_view = SampleView(
                camera_controller=self.camera_controller,
                movement_controller=self.movement_controller,
                laser_led_controller=self.laser_led_controller,
                voxel_storage=voxel_storage,
                image_controls_window=self.image_controls_window,
                sample_3d_window=self.sample_3d_visualization_window,
                geometry_manager=self.geometry_manager,
            )

            # Connect acquisition lock signals to Sample View
            self.acquisition_started.connect(
                lambda: self.sample_view.set_stage_controls_enabled(False)
            )
            self.acquisition_stopped.connect(
                lambda: self.sample_view.set_stage_controls_enabled(True)
            )
            self.logger.debug("Connected acquisition signals to Sample View")

        self.sample_view.show()
        self.sample_view.raise_()
        self.sample_view.activateWindow()
        self.logger.debug("Sample View shown and raised")

        # Update menu states now that Sample View is open
        if self.main_window and self.connection_service:
            connected = self.connection_service.is_connected()
            self.main_window.update_menu_states(connected=connected)

    def create_main_window(self):
        """Create main application window by composing views.

        This method imports the MainWindow class and creates the main
        window, passing in the views created during dependency setup.

        The MainWindow is responsible for:
        - Composing ConnectionView, WorkflowView, SampleInfoView, StageControlView, and CameraLiveViewer
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
            connection_view=self.connection_view,
            workflow_view=self.workflow_view,
            sample_info_view=self.sample_info_view,
            status_indicator_widget=self.status_indicator_widget,
            stage_control_view=self.stage_control_view,
            camera_live_viewer=self.camera_live_viewer,
            image_controls_window=self.image_controls_window,
            stage_chamber_visualization_window=self.stage_chamber_visualization_window,
            sample_3d_visualization_window=self.sample_3d_visualization_window,
            app=self,  # Pass FlamingoApplication reference for accessing sample_view etc.
            geometry_manager=self.geometry_manager
        )
        self.main_window.setWindowTitle("Flamingo Microscope Control")
        # Window size is automatically set based on screen dimensions
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
        - Saves window geometry state
        - Disconnects from microscope if connected
        - Cleans up network resources
        - Logs shutdown

        Called automatically at application exit.
        """
        self.logger.info("Shutting down application...")

        # Save window geometry state
        if self.geometry_manager:
            self.logger.info("Saving window geometry...")
            try:
                # Explicitly save SampleView geometry if it exists and is visible
                # (hideEvent may not fire during app shutdown)
                if self.sample_view and self.sample_view.isVisible():
                    self.geometry_manager.save_geometry("SampleView", self.sample_view)
                    self.logger.debug("Saved SampleView geometry during shutdown")

                self.geometry_manager.save_all()
            except Exception as e:
                self.logger.error(f"Error saving window geometry: {e}")

        # Disconnect if connected
        if self.connection_service and self.connection_service.is_connected():
            self.logger.info("Disconnecting from microscope...")
            try:
                self.connection_service.disconnect()
            except Exception as e:
                self.logger.error(f"Error during disconnect: {e}")

        self.logger.info("Application shutdown complete")

    # -------------------------------------------------------------------------
    # Configuration Properties
    # -------------------------------------------------------------------------

    @property
    def microscope_settings(self):
        """Get microscope settings service for stage limits and other per-microscope config.

        This property provides access to the MicroscopeSettingsService which contains
        stage movement limits, position history settings, and other microscope-specific
        configuration loaded from {microscope_name}_settings.json.

        Returns:
            MicroscopeSettingsService instance, or None if config_service not available
        """
        if hasattr(self, 'config_service') and self.config_service:
            return self.config_service.microscope_settings
        return None

    # -------------------------------------------------------------------------
    # Acquisition State Management
    # -------------------------------------------------------------------------

    @property
    def is_acquisition_in_progress(self) -> bool:
        """Check if an acquisition is currently in progress.

        When True, microscope controls (stage movement, etc.) should be disabled
        to prevent interference with the acquisition process.

        Returns:
            True if an acquisition is running, False otherwise
        """
        return self._acquisition_in_progress

    def start_acquisition(self, source: str = "unknown") -> bool:
        """Signal that an acquisition process is starting.

        This locks microscope controls to prevent interference during scanning.
        Views connected to the acquisition_started signal should disable
        stage movement controls, position presets, etc.

        Args:
            source: Identifier for the acquisition source (for logging)

        Returns:
            True if acquisition started, False if already in progress
        """
        if self._acquisition_in_progress:
            self.logger.warning(f"Acquisition already in progress, cannot start '{source}'")
            return False

        self._acquisition_in_progress = True
        self.logger.info(f"Acquisition started: {source}")
        self.acquisition_started.emit()
        return True

    def stop_acquisition(self, source: str = "unknown"):
        """Signal that an acquisition process has ended.

        This unlocks microscope controls. Views connected to the
        acquisition_stopped signal should re-enable stage movement controls.

        Args:
            source: Identifier for the acquisition source (for logging)
        """
        if not self._acquisition_in_progress:
            self.logger.debug(f"No acquisition in progress to stop ({source})")
            return

        self._acquisition_in_progress = False
        self.logger.info(f"Acquisition stopped: {source}")
        self.acquisition_stopped.emit()
