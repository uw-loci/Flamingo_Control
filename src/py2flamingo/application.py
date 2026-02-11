"""
Application Layer - Lifecycle Management and Orchestration

This module provides the main FlamingoApplication class that handles:
- Application lifecycle (startup, shutdown, resource cleanup)
- Dependency setup orchestration (delegates to component_factory and signal_wiring)
- Connection lifecycle handlers
- Lazy Sample View creation
- Main window composition

Component creation is handled by services.component_factory.
Signal wiring is handled by services.signal_wiring.
Voxel storage creation is handled by visualization.voxel_storage_factory.
"""

import sys
import logging
from typing import Optional
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QObject, pyqtSignal

from py2flamingo.services.component_factory import (
    create_core_layer, create_models_layer, create_services_layer,
    create_controllers_layer, create_views_layer
)
from py2flamingo.services.signal_wiring import wire_all_signals
from py2flamingo.visualization.voxel_storage_factory import create_voxel_storage


class FlamingoApplication(QObject):
    """Main application class handling dependency injection and lifecycle.

    This class orchestrates component creation and wiring using the MVC
    architecture:

    Core Layer (tcp_connection, protocol_encoder)
        |
    Models Layer (connection_model)
        |
    Services Layer (connection_service, workflow_service, status_service)
        |
    Controllers Layer (connection_controller, workflow_controller)
        |
    Views Layer (connection_view, workflow_view)
        |
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
        self.tcp_connection = None
        self.protocol_encoder = None

        # Models layer components
        self.connection_model = None
        self.workflow_model = None
        self.display_model = None

        # Services layer components
        self.connection_service = None
        self.workflow_service = None
        self.workflow_queue_service = None
        self.status_service = None
        self.status_indicator_service = None
        self.config_manager = None
        self.geometry_manager = None

        # Controllers layer components
        self.connection_controller = None
        self.workflow_controller = None
        self.movement_controller = None
        self.camera_controller = None

        # Views layer components
        self.connection_view = None
        self.workflow_view = None
        self.sample_info_view = None
        self.stage_control_view = None
        self.camera_live_viewer = None
        self.image_controls_window = None
        self.stage_chamber_visualization_window = None
        self.sample_view = None  # Unified sample viewing interface
        self.voxel_storage = None  # DualResolutionVoxelStorage for 3D visualization

        # Setup logging
        self.logger = logging.getLogger(__name__)

    def setup_dependencies(self):
        """Create and wire all application components using dependency injection.

        Delegates to factory functions in services.component_factory for creation
        and services.signal_wiring for signal connections.
        """
        self.logger.info("Setting up application dependencies...")

        # --- Core layer ---
        core = create_core_layer()
        self.tcp_connection = core['tcp_connection']
        self.protocol_encoder = core['protocol_encoder']
        self.queue_manager = core['queue_manager']
        self.event_manager = core['event_manager']

        # --- Models layer ---
        models = create_models_layer()
        self.connection_model = models['connection_model']
        self.workflow_model = models['workflow_model']
        self.display_model = models['display_model']

        # --- Services layer ---
        services = create_services_layer(
            self.tcp_connection, self.protocol_encoder, self.queue_manager,
            self.connection_model, self.event_manager
        )
        self.connection_service = services['connection_service']
        self.workflow_service = services['workflow_service']
        self.status_service = services['status_service']
        self.status_indicator_service = services['status_indicator_service']
        self.config_manager = services['config_manager']
        self.geometry_manager = services['geometry_manager']

        # --- Controllers layer ---
        controllers = create_controllers_layer(
            self.connection_service, self.connection_model, self.config_manager,
            self.workflow_service, self.workflow_model,
            self.status_indicator_service
        )
        self.connection_controller = controllers['connection_controller']
        self.workflow_controller = controllers['workflow_controller']
        self.workflow_queue_service = controllers['workflow_queue_service']
        self.position_controller = controllers['position_controller']
        self.movement_controller = controllers['movement_controller']
        self.config_service = controllers['config_service']
        self.laser_led_service = controllers['laser_led_service']
        self.laser_led_controller = controllers['laser_led_controller']
        self.camera_service = controllers['camera_service']
        self.camera_controller = controllers['camera_controller']

        # --- Views layer ---
        views = create_views_layer(
            self.connection_controller, self.config_manager,
            self.position_controller, self.workflow_service,
            self.workflow_controller, self.movement_controller,
            self.camera_controller, self.laser_led_controller,
            self.geometry_manager
        )
        self.connection_view = views['connection_view']
        self.workflow_view = views['workflow_view']
        self.sample_info_view = views['sample_info_view']
        self.stage_control_view = views['stage_control_view']
        self.image_controls_window = views['image_controls_window']
        self.camera_live_viewer = views['camera_live_viewer']
        self.stage_chamber_visualization_window = views['stage_chamber_visualization_window']
        # Set app reference for save drive persistence
        self.workflow_view.set_app(self)

        # --- Voxel storage for 3D visualization ---
        self.logger.debug("Creating voxel storage for 3D visualization...")
        bundle = create_voxel_storage()
        if bundle is not None:
            self.voxel_storage = bundle.voxel_storage
            self._visualization_config = bundle.config
            self._coord_mapper = bundle.coord_mapper
            self._coord_transformer = bundle.coord_transformer
        else:
            self.voxel_storage = None

        # --- Signal wiring ---
        wire_all_signals(self)

        self.logger.info("Application dependencies setup complete")

    def _on_stage_connection_established(self):
        """Handle connection established event for enhanced stage control view.

        Enables controls immediately when TCP connection succeeds.
        Note: Position queries are deferred to _on_settings_loaded() to avoid
        race condition with settings retrieval that pauses the SocketReader.
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

    def _on_settings_loaded(self):
        """Handle settings loaded event - query initial position from hardware.

        This is triggered AFTER settings retrieval completes, avoiding the race
        condition where position queries would collide with the synchronous
        settings retrieval that pauses the SocketReader.
        """
        self.logger.info("Settings loaded - querying initial position from hardware")

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
        from py2flamingo.views import SampleView

        self.logger.info("Opening Sample View")

        if self.sample_view is None:
            self.logger.debug("Creating new SampleView instance")

            self.sample_view = SampleView(
                camera_controller=self.camera_controller,
                movement_controller=self.movement_controller,
                laser_led_controller=self.laser_led_controller,
                voxel_storage=self.voxel_storage,
                image_controls_window=self.image_controls_window,
                geometry_manager=self.geometry_manager,
                configuration_service=self.config_service,
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
