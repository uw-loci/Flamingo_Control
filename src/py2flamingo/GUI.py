# ============================================================================
# src/py2flamingo/GUI.py
"""
Main GUI module for Py2Flamingo application.

This module implements the main window and user interface using PyQt5,
following the MVC architecture pattern where the GUI acts as the View
and delegates all business logic to controllers.
"""

import os
from threading import Thread

# Import utilities with corrected paths
import py2flamingo.utils.calculations as calc
import py2flamingo.utils.file_handlers as txt
from .FlamingoConnect import FlamingoConnect, show_warning_message
from py2flamingo.utils.image_processing import convert_to_qimage

# Import global objects (needed for connection setup)
from .global_objects import (
    image_queue,
    command_queue,
    z_plane_queue,
    intensity_queue,
    visualize_queue,
    view_snapshot,
    system_idle,
    processing_event,
    send_event,
    terminate_event,
)

# Controller imports with correct relative paths
try:
    from .controllers.microscope_controller import MicroscopeController
    from .controllers.position_controller import PositionController
    from .controllers.settings_controller import SettingsController
    from .controllers.snapshot_controller import SnapshotController
    from .controllers.sample_controller import SampleController
    from .controllers.ellipse_controller import EllipseController
    from .controllers.multi_angle_controller import MultiAngleController
    CONTROLLERS_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Some controllers not available: {e}")
    CONTROLLERS_AVAILABLE = False

from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import QTimer, Qt

# Default data storage directory
DATA_DIR = "output_png"


class Py2FlamingoGUI(QDialog):
    """
    Main GUI dialog for the Py2Flamingo application.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Py2Flamingo Control")
        self.setMinimumSize(800, 600)

        # Controllers will be initialized after connection
        self.microscope_controller = None
        self.position_controller = None
        self.settings_controller = None
        self.snapshot_controller = None
        self.sample_controller = None
        self.ellipse_controller = None
        self.multi_angle_controller = None

        # Layout
        self.layout = QVBoxLayout()
        self.setLayout(self.layout)

        # Connect/Disconnect buttons
        self.connect_button = QPushButton("Connect to Microscope")
        self.disconnect_button = QPushButton("Disconnect")
        self.layout.addWidget(self.connect_button)
        self.layout.addWidget(self.disconnect_button)
        self.disconnect_button.setEnabled(False)

        # Status label
        self.status_label = QLabel("Not connected.")
        self.layout.addWidget(self.status_label)

        # Connect signals
        self.connect_button.clicked.connect(self.connect_to_microscope)
        self.disconnect_button.clicked.connect(self.disconnect_from_microscope)

        # Timer for updating GUI elements periodically (if needed)
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_gui)

        # Show the GUI
        self.show()

    def connect_to_microscope(self):
        """Connect to the microscope."""
        # Initialize connection dialog
        connection_dialog = FlamingoConnect(
            {
                "queues": (
                    image_queue,
                    command_queue,
                    z_plane_queue,
                    intensity_queue,
                    visualize_queue,
                ),
                "events": (
                    view_snapshot,
                    system_idle,
                    processing_event,
                    send_event,
                    terminate_event,
                ),
            }
        )
        
        # For now, just show a connection status since FlamingoConnect is not a dialog
        try:
            # FlamingoConnect is not a dialog, so we handle it differently
            self.flamingo_connection = connection_dialog
            
            if hasattr(connection_dialog, 'connection_data') and connection_dialog.connection_data:
                # Initialize controllers after successful connection (if available)
                if CONTROLLERS_AVAILABLE:
                    connection_data = connection_dialog.connection_data
                    nuc_client, live_client, wf_zstack, LED_on, LED_off = connection_data
                    
                    # Create controllers with appropriate models and services
                    # Note: These will need proper initialization once the full MVC structure is in place
                    self.microscope_controller = MicroscopeController()
                    # Additional controller initialization would go here...
                
                # Update UI state
                self.connect_button.setEnabled(False)
                self.disconnect_button.setEnabled(True)
                self.status_label.setText("Connected to microscope.")
                # Start update timer if needed (e.g., for live feed)
                self.update_timer.start(1000)  # update every second
            else:
                show_warning_message("Connection data not available.")
                
        except Exception as e:
            show_warning_message(f"Connection failed: {e}")

    def disconnect_from_microscope(self):
        """Disconnect from the microscope."""
        if hasattr(self, 'flamingo_connection'):
            # Perform necessary cleanup and disconnection
            # Additional cleanup would go here...
            self.connect_button.setEnabled(True)
            self.disconnect_button.setEnabled(False)
            self.status_label.setText("Disconnected.")
            self.update_timer.stop()

    def update_gui(self):
        """Periodic GUI updates (if needed)."""
        if self.microscope_controller:
            # Example: update position display
            current_pos = self.microscope_controller.model.current_position
            # ... update any GUI elements with current_pos ...
            pass

    def closeEvent(self, event):
        """Handle window close event."""
        # Ensure proper disconnection on close
        if hasattr(self, 'flamingo_connection'):
            self.disconnect_from_microscope()
        event.accept()


# Alias for backward compatibility
GUI = Py2FlamingoGUI