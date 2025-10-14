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

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QTabWidget,
    QAction, QMessageBox
)
from PyQt5.QtCore import Qt

from py2flamingo.views import ConnectionView, WorkflowView


class MainWindow(QMainWindow):
    """Main application window that composes all views.

    This class creates the main window UI by composing views created
    in the Views layer. It uses a tab widget to organize the
    ConnectionView and WorkflowView into separate tabs.

    The window includes:
    - Menu bar (File → Exit, Help → About)
    - Tab widget with Connection and Workflow tabs
    - Status bar for application messages

    Example:
        main_window = MainWindow(connection_view, workflow_view)
        main_window.setWindowTitle("Flamingo Microscope Control")
        main_window.resize(600, 400)
        main_window.show()
    """

    def __init__(self, connection_view: ConnectionView, workflow_view: WorkflowView):
        """Initialize main window with view components.

        Args:
            connection_view: ConnectionView instance for connection management
            workflow_view: WorkflowView instance for workflow operations
        """
        super().__init__()

        self.connection_view = connection_view
        self.workflow_view = workflow_view

        self._setup_ui()
        self._setup_menu()

    def _setup_ui(self):
        """Create and layout all UI components.

        This method creates:
        - Central widget with vertical layout
        - Tab widget with Connection and Workflow tabs
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

        # Add views as tabs
        self.tabs.addTab(self.connection_view, "Connection")
        self.tabs.addTab(self.workflow_view, "Workflow")

        # Add tabs to layout
        layout.addWidget(self.tabs)

        # Create status bar
        self.statusBar().showMessage("Ready")

    def _setup_menu(self):
        """Create menu bar with File and Help menus.

        Creates:
        - File menu with Exit action
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

    def closeEvent(self, event):
        """Handle window close event.

        This method is called when the user closes the main window.
        It accepts the close event, allowing the window to close.

        Args:
            event: QCloseEvent instance
        """
        # Could add confirmation dialog here if desired
        # For now, just accept the close event
        event.accept()
