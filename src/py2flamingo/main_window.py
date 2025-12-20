"""
Main Window - View Composition and UI Layout

This module provides the MainWindow class that composes all UI views
into a cohesive application window. The MainWindow is responsible for:
- Creating the main application window layout
- Composing ConnectionView and WorkflowView into tabs
- Creating menu bar with File and Help menus
- Creating status bar
- Handling window lifecycle events
"""

from typing import Optional
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QTabWidget,
    QAction, QMessageBox, QScrollArea, QApplication
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QShowEvent, QCloseEvent

from py2flamingo.views import ConnectionView, WorkflowView, SampleInfoView, ImageControlsWindow, StageControlView
from py2flamingo.views.camera_live_viewer import CameraLiveViewer
from py2flamingo.services.window_geometry_manager import WindowGeometryManager


class MainWindow(QMainWindow):
    """Main application window that composes all views.

    This class creates the main window UI by composing views created
    in the Views layer. It uses a tab widget to organize the
    ConnectionView, WorkflowView, SampleInfoView, and StageControlView into separate tabs.

    The window includes:
    - Menu bar (File → Exit, View → Camera/Image Controls, Help → About)
    - Tab widget with Connection, Workflow, Sample Info, and Stage Control tabs
    - Scroll areas for each tab to handle content overflow
    - Status bar for application messages
    - Intelligent auto-sizing based on screen dimensions (90% of screen height)
    - Access to independent windows: Camera Live Viewer and Image Controls

    Example:
        main_window = MainWindow(connection_view, workflow_view, sample_info_view,
                                 stage_control_view=stage_view,
                                 camera_live_viewer=camera_viewer,
                                 image_controls_window=image_controls)
        main_window.setWindowTitle("Flamingo Microscope Control")
        # Window automatically sizes to 90% of screen height and centers itself
        main_window.show()
    """

    def __init__(self,
                 connection_view: ConnectionView,
                 workflow_view: WorkflowView,
                 sample_info_view: Optional[SampleInfoView] = None,
                 status_indicator_widget=None,
                 stage_control_view=None,
                 camera_live_viewer=None,
                 image_controls_window=None,
                 stage_chamber_visualization_window=None,
                 sample_3d_visualization_window=None,
                 app=None,
                 geometry_manager: Optional[WindowGeometryManager] = None):
        """Initialize main window with view components.

        Args:
            connection_view: ConnectionView instance for connection management
            workflow_view: WorkflowView instance for workflow operations
            sample_info_view: Optional SampleInfoView instance for sample configuration
            status_indicator_widget: Optional status indicator widget
            stage_control_view: Optional StageControlView instance
            camera_live_viewer: Optional CameraLiveViewer instance
            image_controls_window: Optional ImageControlsWindow instance
            stage_chamber_visualization_window: Optional StageChamberVisualizationWindow instance
            sample_3d_visualization_window: Optional Sample3DVisualizationWindow instance
            app: Optional FlamingoApplication instance for accessing app-level resources
            geometry_manager: Optional WindowGeometryManager for saving/restoring geometry
        """
        super().__init__()

        self.app = app  # Reference to FlamingoApplication for accessing sample_view etc.
        self._geometry_manager = geometry_manager
        self._geometry_restored = False
        self.connection_view = connection_view
        self.workflow_view = workflow_view
        self.sample_info_view = sample_info_view
        self.status_indicator_widget = status_indicator_widget
        self.stage_control_view = stage_control_view
        self.camera_live_viewer = camera_live_viewer
        self.image_controls_window = image_controls_window
        self.stage_chamber_visualization_window = stage_chamber_visualization_window
        self.sample_3d_visualization_window = sample_3d_visualization_window

        self._setup_ui()
        self._setup_menu()

    def _setup_ui(self):
        """Create and layout all UI components.

        This method creates:
        - Central widget with vertical layout
        - Tab widget with Connection, Workflow, Sample Info, and Live Feed tabs
        - Scroll areas for each tab to handle content overflow
        - Status bar
        """
        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Create layout
        layout = QVBoxLayout()
        central_widget.setLayout(layout)

        # Create tab widget
        self.tabs = QTabWidget()

        # Add views as tabs with scroll areas
        self.tabs.addTab(self._wrap_in_scroll_area(self.connection_view), "Connection")
        self.tabs.addTab(self._wrap_in_scroll_area(self.workflow_view), "Workflow")

        # Add sample info tab if available
        if self.sample_info_view is not None:
            self.tabs.addTab(self._wrap_in_scroll_area(self.sample_info_view), "Sample Info")

        # Add stage control tab (enhanced view only)
        if self.stage_control_view is not None:
            self.tabs.addTab(self._wrap_in_scroll_area(self.stage_control_view), "Stage Control")

        # Add tabs to layout
        layout.addWidget(self.tabs)

        # Connect tab change signal for logging
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Create status bar with status indicator
        status_bar = self.statusBar()

        # Add status indicator widget to status bar if provided
        if self.status_indicator_widget is not None:
            # Add to left side of status bar (permanent widget)
            status_bar.addPermanentWidget(self.status_indicator_widget)

        status_bar.showMessage("Ready")

        # Set intelligent default window size based on screen dimensions
        self._set_default_size()

    def _wrap_in_scroll_area(self, widget: QWidget) -> QScrollArea:
        """Wrap a widget in a scroll area for overflow handling.

        Args:
            widget: Widget to wrap in scroll area

        Returns:
            QScrollArea containing the widget
        """
        scroll_area = QScrollArea()
        scroll_area.setWidget(widget)
        scroll_area.setWidgetResizable(True)  # Allow widget to resize with scroll area
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        return scroll_area

    def _set_default_size(self):
        """Set default window size based on available screen space.

        Sets window to approximately 90% of screen height and a reasonable width,
        ensuring the interface is visible without extending beyond screen boundaries.
        """
        # Get available screen geometry (excluding taskbar/dock)
        screen = QApplication.primaryScreen()
        if screen:
            available_geometry = screen.availableGeometry()
            screen_width = available_geometry.width()
            screen_height = available_geometry.height()

            # Set window to 90% of screen height and compact width
            target_width = min(600, int(screen_width * 0.4))  # Max 600px or 40% width (compact UI)
            target_height = int(screen_height * 0.9)  # 90% of screen height

            self.resize(target_width, target_height)

            # Center the window on screen
            x = (screen_width - target_width) // 2
            y = (screen_height - target_height) // 2
            self.move(x, y)

    def _setup_menu(self):
        """Create menu bar with File, View, Tools, Extensions, and Help menus.

        Creates:
        - File menu with Exit action
        - View menu with Image Controls action
        - Tools menu with debug/test functions (requires connection)
        - Extensions menu with advanced features (requires connection + Sample View)
        - Help menu with About action
        """
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.setStatusTip("Exit application")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # View menu
        view_menu = menubar.addMenu("&View")

        # Camera Live Viewer action
        if self.camera_live_viewer is not None:
            camera_viewer_action = QAction("&Camera Live Viewer...", self)
            camera_viewer_action.setShortcut("Ctrl+L")
            camera_viewer_action.setStatusTip("Open Camera Live Viewer window")
            camera_viewer_action.triggered.connect(self._show_camera_viewer)
            view_menu.addAction(camera_viewer_action)

        # Image Controls action
        if self.image_controls_window is not None:
            image_controls_action = QAction("&Image Controls...", self)
            image_controls_action.setShortcut("Ctrl+I")
            image_controls_action.setStatusTip("Open Image Controls window")
            image_controls_action.triggered.connect(self._show_image_controls)
            view_menu.addAction(image_controls_action)

        # Stage Chamber Visualization action
        if self.stage_chamber_visualization_window is not None:
            chamber_viz_action = QAction("&Stage Chamber Visualization...", self)
            chamber_viz_action.setShortcut("Ctrl+M")
            chamber_viz_action.setStatusTip("Open Stage Chamber Visualization window")
            chamber_viz_action.triggered.connect(self._show_stage_chamber_visualization)
            view_menu.addAction(chamber_viz_action)

        # 3D Sample Visualization action
        if self.sample_3d_visualization_window is not None:
            sample_3d_action = QAction("&3D Sample Visualization...", self)
            sample_3d_action.setShortcut("Ctrl+3")
            sample_3d_action.setStatusTip("Open 3D Sample Visualization window with rotation-aware data accumulation")
            sample_3d_action.triggered.connect(self._show_sample_3d_visualization)
            view_menu.addAction(sample_3d_action)

        # Tools menu (requires connection)
        tools_menu = menubar.addMenu("&Tools")

        self.voxel_test_action = QAction("3D &Voxel Test", self)
        self.voxel_test_action.setStatusTip("Run 3D voxel movement and rotation test")
        self.voxel_test_action.triggered.connect(self._on_voxel_test)
        self.voxel_test_action.setEnabled(False)
        tools_menu.addAction(self.voxel_test_action)

        self.volume_scan_action = QAction("&Volume Scan", self)
        self.volume_scan_action.setStatusTip("Run volume scan with serpentine movement")
        self.volume_scan_action.triggered.connect(self._on_volume_scan)
        self.volume_scan_action.setEnabled(False)
        tools_menu.addAction(self.volume_scan_action)

        self.calibrate_action = QAction("&Calibrate Objective...", self)
        self.calibrate_action.setStatusTip("Calibrate objective center position")
        self.calibrate_action.triggered.connect(self._on_calibrate_objective)
        self.calibrate_action.setEnabled(False)
        tools_menu.addAction(self.calibrate_action)

        # Extensions menu (requires connection + Sample View open)
        extensions_menu = menubar.addMenu("&Extensions")

        self.led_2d_overview_action = QAction("&LED 2D Overview...", self)
        self.led_2d_overview_action.setStatusTip("Create 2D focus-stacked overview maps at two rotation angles")
        self.led_2d_overview_action.triggered.connect(self._on_led_2d_overview)
        self.led_2d_overview_action.setEnabled(False)
        extensions_menu.addAction(self.led_2d_overview_action)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        about_action = QAction("&About", self)
        about_action.setStatusTip("About Flamingo Control")
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _show_about(self):
        """Show About dialog with application information."""
        QMessageBox.about(
            self,
            "About Flamingo Microscope Control",
            "<h3>Flamingo Microscope Control</h3>"
            "<p>MVC Architecture Edition</p>"
            "<p>Control software for Flamingo light sheet microscopes.</p>"
            "<p>Built with PyQt5 and Python.</p>"
        )

    def _show_camera_viewer(self):
        """Show Camera Live Viewer window."""
        if self.camera_live_viewer is not None:
            self.camera_live_viewer.show()
            self.camera_live_viewer.raise_()  # Bring to front
            self.camera_live_viewer.activateWindow()  # Give it focus

    def _show_image_controls(self):
        """Show Image Controls window."""
        if self.image_controls_window is not None:
            self.image_controls_window.show()
            self.image_controls_window.raise_()  # Bring to front
            self.image_controls_window.activateWindow()  # Give it focus

    def _show_stage_chamber_visualization(self):
        """Show Stage Chamber Visualization window."""
        if self.stage_chamber_visualization_window is not None:
            self.stage_chamber_visualization_window.show()
            self.stage_chamber_visualization_window.raise_()  # Bring to front
            self.stage_chamber_visualization_window.activateWindow()  # Give it focus

    def _show_sample_3d_visualization(self):
        """Show 3D Sample Visualization window."""
        if self.sample_3d_visualization_window is not None:
            self.sample_3d_visualization_window.show()
            self.sample_3d_visualization_window.raise_()  # Bring to front
            self.sample_3d_visualization_window.activateWindow()  # Give it focus

    # ========== Tools Menu Handlers ==========

    def update_menu_states(self, connected: bool = False):
        """Update Tools and Extensions menu item states based on connection and Sample View.

        Args:
            connected: Whether microscope is connected
        """
        # Tools menu requires connection
        self.voxel_test_action.setEnabled(connected)
        self.volume_scan_action.setEnabled(connected)
        self.calibrate_action.setEnabled(connected)

        # Extensions menu requires connection AND Sample View open
        sample_view_open = self.app is not None and self.app.sample_view is not None
        self.led_2d_overview_action.setEnabled(connected and sample_view_open)

    def _on_voxel_test(self):
        """Handle 3D voxel rotation test menu action."""
        import logging
        logger = logging.getLogger(__name__)
        logger.info("3D Voxel Rotation Test menu action triggered")

        if not self.app:
            QMessageBox.warning(self, "Error", "Application not available")
            return

        # Disable action during test
        self.voxel_test_action.setEnabled(False)
        self.voxel_test_action.setText("3D Voxel Test (Running...)")

        try:
            from tests.test_3d_voxel_rotation import test_3d_voxel_rotation

            # Ensure Sample View is open
            if self.app.sample_view is None:
                self.app._open_sample_view()

            # Run test with FlamingoApplication
            test_3d_voxel_rotation(self.app)

            # Re-enable action after delay (test runs async)
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(70000, self._reset_voxel_test_action)

        except ImportError as e:
            logger.error(f"Could not import test module: {e}")
            QMessageBox.critical(self, "Error", "Test module not found.")
            self._reset_voxel_test_action()
        except Exception as e:
            logger.error(f"Error starting test: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to start test: {e}")
            self._reset_voxel_test_action()

    def _reset_voxel_test_action(self):
        """Reset voxel test action after async test completes."""
        self.voxel_test_action.setEnabled(True)
        self.voxel_test_action.setText("3D &Voxel Test")

    def _on_volume_scan(self):
        """Handle volume scan menu action."""
        import logging
        logger = logging.getLogger(__name__)
        logger.info("Volume Scan menu action triggered")

        if not self.app:
            QMessageBox.warning(self, "Error", "Application not available")
            return

        # Disable action during scan
        self.volume_scan_action.setEnabled(False)
        self.volume_scan_action.setText("Volume Scan (Running...)")

        try:
            from tests.test_3d_movement_simple import test_voxel_movement

            # Get position controller from app
            position_controller = getattr(self.app, 'position_controller', None)
            if not position_controller:
                raise RuntimeError("Position controller not available")

            # Run the volume scan
            success = test_voxel_movement(position_controller, self, mode='volume_scan')

            if success:
                QMessageBox.information(self, "Success", "Volume scan completed successfully!")
            else:
                QMessageBox.warning(self, "Warning", "Volume scan completed with warnings.")

        except ImportError as e:
            logger.error(f"Could not import test module: {e}")
            QMessageBox.critical(self, "Error", "Test module not found.")
        except Exception as e:
            logger.error(f"Error during volume scan: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Volume scan failed: {e}")
        finally:
            self._reset_volume_scan_action()

    def _reset_volume_scan_action(self):
        """Reset volume scan action after scan completes."""
        self.volume_scan_action.setEnabled(True)
        self.volume_scan_action.setText("&Volume Scan")

    def _on_calibrate_objective(self):
        """Handle calibrate objective menu action."""
        import logging
        logger = logging.getLogger(__name__)
        logger.info("Calibrate Objective menu action triggered")

        if not self.app:
            QMessageBox.warning(self, "Error", "Application not available")
            return

        # Show instructions dialog
        reply = QMessageBox.information(
            self,
            "Calibrate Objective XY Center",
            "This calibration helps show where the camera is capturing in 3D.\n\n"
            "Instructions:\n\n"
            "1. Open the Camera Live View\n"
            "2. Use the stage controls to move until you can see\n"
            "   the very tip of the sample holder (fine extension)\n"
            "3. Center the tip in the camera view\n"
            "4. Click OK to save this position\n\n"
            "Note: Rotation (R) should not affect centering.",
            QMessageBox.Ok | QMessageBox.Cancel
        )

        if reply != QMessageBox.Ok:
            return

        try:
            # Get current position from movement controller
            movement_controller = getattr(self.app, 'movement_controller', None)
            if not movement_controller:
                raise RuntimeError("Movement controller not available")

            current_pos = movement_controller.get_position()
            if not current_pos:
                raise RuntimeError("Could not read current position")

            # Save as preset
            preset_service = getattr(self.app, 'position_preset_service', None)
            if preset_service:
                preset_service.save_preset(
                    name="Tip of sample mount",
                    position=current_pos,
                    description="Calibrated objective center position"
                )
                logger.info(f"Saved calibration: {current_pos}")
                QMessageBox.information(
                    self, "Calibration Saved",
                    f"Position saved:\nX={current_pos.x:.3f}, Y={current_pos.y:.3f}, "
                    f"Z={current_pos.z:.3f}, R={current_pos.r:.1f}°"
                )
            else:
                QMessageBox.warning(self, "Warning", "Preset service not available")

        except Exception as e:
            logger.error(f"Error during calibration: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Calibration failed: {e}")

    # ========== Extensions Menu Handlers ==========

    def _on_led_2d_overview(self):
        """Handle LED 2D Overview menu action."""
        import logging
        logger = logging.getLogger(__name__)
        logger.info("LED 2D Overview menu action triggered")

        if not self.app or not self.app.sample_view:
            QMessageBox.warning(
                self, "Sample View Required",
                "Please open the Sample View before using this extension."
            )
            return

        try:
            from py2flamingo.views.dialogs.led_2d_overview_dialog import LED2DOverviewDialog

            # Create non-modal dialog (use show() not exec_())
            # Keep reference to prevent garbage collection
            self._led_2d_overview_dialog = LED2DOverviewDialog(
                app=self.app,
                parent=None  # No parent so it's independent window
            )
            self._led_2d_overview_dialog.show()

        except ImportError as e:
            logger.error(f"Could not import LED 2D Overview dialog: {e}")
            QMessageBox.critical(
                self, "Error",
                "LED 2D Overview dialog not available.\n"
                "The extension may not be fully implemented yet."
            )
        except Exception as e:
            logger.error(f"Error opening LED 2D Overview: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to open dialog: {e}")

    def _on_tab_changed(self, index: int) -> None:
        """Handle tab change event - log which tab user switched to.

        Args:
            index: Index of the newly selected tab
        """
        import logging
        logger = logging.getLogger(__name__)
        tab_name = self.tabs.tabText(index)
        logger.info(f"User switched to tab: {tab_name}")

    def showEvent(self, event: QShowEvent) -> None:
        """Handle window show event - restore geometry on first show.

        Args:
            event: QShowEvent instance
        """
        super().showEvent(event)

        # Restore geometry only on first show
        if not self._geometry_restored and self._geometry_manager:
            restored = self._geometry_manager.restore_geometry("MainWindow", self)
            if restored:
                import logging
                logging.getLogger(__name__).info("Restored MainWindow geometry from saved state")
            self._geometry_restored = True

    def closeEvent(self, event: QCloseEvent) -> None:
        """Handle window close event.

        This method is called when the user closes the main window.
        It saves window geometry, properly closes all child windows,
        and stops background threads before exiting the application.

        Args:
            event: QCloseEvent instance
        """
        import logging
        logger = logging.getLogger(__name__)
        logger.info("Main window closing - cleaning up...")

        # Save window geometry before closing
        if self._geometry_manager:
            try:
                self._geometry_manager.save_geometry("MainWindow", self)
                self._geometry_manager.save_all()  # Persist to disk
                logger.info("Saved MainWindow geometry and persisted to disk")
            except Exception as e:
                logger.error(f"Error saving MainWindow geometry: {e}")

        # Stop camera acquisition if running
        if self.camera_live_viewer is not None:
            try:
                # Stop live view to terminate background acquisition thread
                if hasattr(self.camera_live_viewer, 'camera_controller'):
                    self.camera_live_viewer.camera_controller.stop_live_view()
                    logger.info("Stopped camera acquisition")
            except Exception as e:
                logger.error(f"Error stopping camera acquisition: {e}")

        # Close all child windows properly (not just hide)
        # Set a flag to force actual closure instead of hiding
        if self.camera_live_viewer is not None:
            try:
                # Directly close without triggering closeEvent's hide logic
                self.camera_live_viewer.destroy()
                logger.info("Closed camera live viewer")
            except Exception as e:
                logger.error(f"Error closing camera live viewer: {e}")

        if self.image_controls_window is not None:
            try:
                self.image_controls_window.destroy()
                logger.info("Closed image controls window")
            except Exception as e:
                logger.error(f"Error closing image controls window: {e}")

        if self.stage_chamber_visualization_window is not None:
            try:
                self.stage_chamber_visualization_window.close()
                logger.info("Closed stage chamber visualization window")
            except Exception as e:
                logger.error(f"Error closing stage chamber visualization: {e}")

        if self.sample_3d_visualization_window is not None:
            try:
                self.sample_3d_visualization_window.close()
                logger.info("Closed 3D sample visualization window")
            except Exception as e:
                logger.error(f"Error closing 3D sample visualization: {e}")

        # Accept the close event
        event.accept()

        # Quit the application to ensure clean exit
        QApplication.quit()
