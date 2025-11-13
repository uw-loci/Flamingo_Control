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

from py2flamingo.views import ConnectionView, WorkflowView, SampleInfoView, ImageControlsWindow
from py2flamingo.views.enhanced_stage_control_view import EnhancedStageControlView
from py2flamingo.views.camera_live_viewer import CameraLiveViewer


class MainWindow(QMainWindow):
    """Main application window that composes all views.

    This class creates the main window UI by composing views created
    in the Views layer. It uses a tab widget to organize the
    ConnectionView, WorkflowView, SampleInfoView, and EnhancedStageControlView into separate tabs.

    The window includes:
    - Menu bar (File → Exit, View → Camera/Image Controls, Help → About)
    - Tab widget with Connection, Workflow, Sample Info, and Stage Control tabs
    - Scroll areas for each tab to handle content overflow
    - Status bar for application messages
    - Intelligent auto-sizing based on screen dimensions (90% of screen height)
    - Access to independent windows: Camera Live Viewer and Image Controls

    Example:
        main_window = MainWindow(connection_view, workflow_view, sample_info_view,
                                 enhanced_stage_control_view=stage_view,
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
                 enhanced_stage_control_view=None,
                 camera_live_viewer=None,
                 image_controls_window=None,
                 stage_chamber_visualization_window=None):
        """Initialize main window with view components.

        Args:
            connection_view: ConnectionView instance for connection management
            workflow_view: WorkflowView instance for workflow operations
            sample_info_view: Optional SampleInfoView instance for sample configuration
            status_indicator_widget: Optional status indicator widget
            enhanced_stage_control_view: Optional EnhancedStageControlView instance
            camera_live_viewer: Optional CameraLiveViewer instance
            image_controls_window: Optional ImageControlsWindow instance
            stage_chamber_visualization_window: Optional StageChamberVisualizationWindow instance
        """
        super().__init__()

        self.connection_view = connection_view
        self.workflow_view = workflow_view
        self.sample_info_view = sample_info_view
        self.status_indicator_widget = status_indicator_widget
        self.enhanced_stage_control_view = enhanced_stage_control_view
        self.camera_live_viewer = camera_live_viewer
        self.image_controls_window = image_controls_window
        self.stage_chamber_visualization_window = stage_chamber_visualization_window

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
        if self.enhanced_stage_control_view is not None:
            self.tabs.addTab(self._wrap_in_scroll_area(self.enhanced_stage_control_view), "Stage Control")

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

            # Set window to 90% of screen height and reasonable width
            target_width = min(1200, int(screen_width * 0.8))  # Max 1200px or 80% width
            target_height = int(screen_height * 0.9)  # 90% of screen height

            self.resize(target_width, target_height)

            # Center the window on screen
            x = (screen_width - target_width) // 2
            y = (screen_height - target_height) // 2
            self.move(x, y)

    def _setup_menu(self):
        """Create menu bar with File, View, and Help menus.

        Creates:
        - File menu with Exit action
        - View menu with Image Controls action
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

    def _on_tab_changed(self, index: int) -> None:
        """Handle tab change event - log which tab user switched to.

        Args:
            index: Index of the newly selected tab
        """
        import logging
        logger = logging.getLogger(__name__)
        tab_name = self.tabs.tabText(index)
        logger.info(f"User switched to tab: {tab_name}")

    def closeEvent(self, event):
        """Handle window close event.

        This method is called when the user closes the main window.
        It properly closes all child windows and stops background threads
        before exiting the application.

        Args:
            event: QCloseEvent instance
        """
        import logging
        logger = logging.getLogger(__name__)
        logger.info("Main window closing - cleaning up...")

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

        # Accept the close event
        event.accept()

        # Quit the application to ensure clean exit
        QApplication.quit()
