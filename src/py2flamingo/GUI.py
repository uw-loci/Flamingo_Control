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

import py2flamingo.functions.calculations as calc
import py2flamingo.functions.microscope_connect as mc
import py2flamingo.functions.microscope_interactions as scope
import py2flamingo.functions.text_file_parsing as txt
from .FlamingoConnect import FlamingoConnect, show_warning_message
from py2flamingo.functions.image_display import convert_to_qimage
from py2flamingo.functions.run_workflow_basic import run_workflow
from .global_objects import (
    command_data_queue,
    command_queue,
    image_queue,
    intensity_queue,
    other_data_queue,
    processing_event,
    send_event,
    stage_location_queue,
    system_idle,
    terminate_event,
    view_snapshot,
    visualize_event,
    visualize_queue,
    z_plane_queue,
)

# MVC Controller imports
from ..controllers.position_controller import PositionController
from ..controllers.sample_controller import SampleController
from ..controllers.multi_angle_controller import MultiAngleController
from ..controllers.settings_controller import SettingsController
from ..controllers.snapshot_controller import SnapshotController
from ..controllers.ellipse_controller import EllipseController
from ..controllers.microscope_controller import MicroscopeController

from ..models.microscope import Position
import numpy as np
# MVC Service imports
from ..services.communication.connection_manager import ConnectionManager
from ..services.workflow_service import WorkflowService

# PyQt5 imports
from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtGui import QDoubleValidator, QIntValidator, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class CoordinateDialog(QDialog):
    """Dialog for entering coordinates and sample parameters."""
    
    def __init__(self, start_position, z_default, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Coordinate Dialog")
        self.setModal(True)

        # Create the layout and add the fields and buttons
        layout = QGridLayout()
        fields_layout = QHBoxLayout()
        sample_layout = QFormLayout()
        buttons_layout = QHBoxLayout()

        # Create the coordinate fields
        self.field_x_mm = QLineEdit()
        self.field_y_mm = QLineEdit()
        self.field_z_mm = QLineEdit()
        self.field_r_deg = QLineEdit()
        self.sample_count = QLineEdit()
        self.z_search_depth = QLineEdit()

        # Set the initial values from the start_position
        self.field_x_mm.setText(str(start_position[0]))
        self.field_y_mm.setText(str(start_position[1]))
        self.field_z_mm.setText(str(start_position[2]))
        self.field_r_deg.setText(str(start_position[3]))
        self.sample_count.setText("0")
        self.z_search_depth.setText(str(z_default))

        # Add the fields to the fields layout
        fields_layout.addWidget(QLabel("X (mm):"))
        fields_layout.addWidget(self.field_x_mm)
        fields_layout.addWidget(QLabel("Y (mm):"))
        fields_layout.addWidget(self.field_y_mm)
        fields_layout.addWidget(QLabel("Z (mm):"))
        fields_layout.addWidget(self.field_z_mm)
        fields_layout.addWidget(QLabel("R (degrees):"))
        fields_layout.addWidget(self.field_r_deg)

        # Add the fields to the sample layout
        sample_layout.addRow(QLabel("Sample count:"), self.sample_count)
        sample_layout.addRow(QLabel("Z search depth (mm):"), self.z_search_depth)

        # Create the buttons
        self.button_ok = QPushButton("OK")
        self.button_cancel = QPushButton("Cancel")

        # Connect the button actions
        self.button_ok.clicked.connect(self.accept)
        self.button_cancel.clicked.connect(self.reject)

        # Add the buttons to the buttons layout
        buttons_layout.addWidget(self.button_ok)
        buttons_layout.addWidget(self.button_cancel)

        # Add the layouts to the main layout
        layout.addLayout(fields_layout, 0, 0)
        layout.addLayout(sample_layout, 1, 0)
        layout.addLayout(buttons_layout, 2, 0)

        self.setLayout(layout)


class MultiAngleDialog(QDialog):
    """Dialog for multi-angle collection parameters."""
    
    def __init__(self, sample_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Multi-Angle Collection Dialog")
        self.setModal(True)

        # Create the layout
        layout = QFormLayout()

        # Create the fields
        self.field_sample_name = QLineEdit(sample_name)
        self.field_increment_angle = QLineEdit("45")
        self.field_workflow_source = QLineEdit("MultiAngle.txt")
        self.field_sample_comment = QLineEdit("")

        # Add fields to layout
        layout.addRow(QLabel("Sample Name:"), self.field_sample_name)
        layout.addRow(QLabel("Angle Increment (degrees):"), self.field_increment_angle)
        layout.addRow(QLabel("Workflow File:"), self.field_workflow_source)
        layout.addRow(QLabel("Comment:"), self.field_sample_comment)

        # Create buttons
        buttons_layout = QHBoxLayout()
        self.button_okay = QPushButton("OK")
        self.button_cancel = QPushButton("Cancel")
        
        self.button_cancel.clicked.connect(self.reject)
        
        buttons_layout.addWidget(self.button_okay)
        buttons_layout.addWidget(self.button_cancel)
        
        layout.addRow(buttons_layout)
        self.setLayout(layout)


class GUI(QMainWindow):
    """
    Main GUI window for Py2Flamingo application.
    
    This class serves as the View in the MVC architecture,
    delegating all business logic to appropriate controllers.
    """
    
    def __init__(self):
        super().__init__()
        
        # Initialize controllers and services
        self._initialize_controllers()
        
        # Store reference to microscope connection
        self.microscope_connection = None
        
        # Thread management
        self.threads = []
        self.take_snapshot_thread = None
        self.set_home_thread = None
        self.go_to_position_thread = None
        self.locate_sample_thread = None
        self.trace_ellipse_thread = None
        self.multi_angle_collection_thread = None
        
        # Set up the GUI
        self.setWindowTitle("Py2Flamingo Control")
        self.setGeometry(100, 100, 1000, 800)
        
        # Initialize UI components
        self._setup_ui()
        
        # Start timer for periodic updates
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_display)
        self.timer.start(100)  # Update every 100ms
    
    def _initialize_controllers(self):
        """Initialize all MVC controllers and services."""
        # Initialize services first
        self.connection_manager = None  # Will be set when connected
        self.workflow_service = WorkflowService()
        
        # Initialize controllers
        self.microscope_controller = None  # Will be set when connected
        self.snapshot_controller = None
        self.position_controller = None
        self.sample_controller = None
        self.settings_controller = None
        self.ellipse_controller = None
        self.multi_angle_controller = None
    
    def _setup_controllers_with_connection(self):
        """Set up controllers after microscope connection is established."""
        if self.connection_manager is None:
            return
            
        # Create microscope controller
        self.microscope_controller = MicroscopeController(
            self.microscope_connection.microscope_model,
            self.connection_manager
        )
        
        # Create other controllers
        self.snapshot_controller = SnapshotController(
            self.microscope_controller,
            self.workflow_service,
            self.connection_manager
        )
        
        self.position_controller = PositionController(
            self.microscope_controller,
            self.connection_manager
        )
        
        self.sample_controller = SampleController(
            self.microscope_controller,
            self.snapshot_controller,
            self.connection_manager
        )
        
        self.settings_controller = SettingsController(
            self.microscope_controller,
            self.connection_manager
        )
        
        self.ellipse_controller = EllipseController(
            self.microscope_controller,
            self.sample_controller,
            self.connection_manager
        )
        
        self.multi_angle_controller = MultiAngleController(
            self.microscope_controller,
            self.workflow_service,
            self.connection_manager
        )
        
        # Subscribe to model updates
        self.microscope_controller.subscribe(self._update_position_display)
    
    def _setup_ui(self):
        """Set up the user interface."""
        # Create central widget and main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QHBoxLayout(central_widget)
        
        # Create left panel for controls
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        
        # Add connection widget
        self._create_connection_widget(left_layout)
        
        # Add position fields
        self._create_position_fields(left_layout)
        
        # Add laser controls
        self._create_laser_controls(left_layout)
        
        # Add sample name field
        self._create_sample_field(left_layout)
        
        # Add control buttons
        self._create_control_buttons(left_layout)
        
        # Add status label
        self.status_label = QLabel("Status: Not connected")
        left_layout.addWidget(self.status_label)
        
        # Add stretch to push everything to top
        left_layout.addStretch()
        
        # Create right panel for image display
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        # Image display
        self.image_label = QLabel()
        self.image_label.setMinimumSize(512, 512)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("border: 1px solid black;")
        right_layout.addWidget(self.image_label)
        
        # Position display under image
        self.position_display = QLabel("Position: X: 0.000, Y: 0.000, Z: 0.000, R: 0.000")
        right_layout.addWidget(self.position_display)
        
        # Add panels to main layout
        layout.addWidget(left_panel, 1)
        layout.addWidget(right_panel, 2)
    
    def _create_connection_widget(self, layout):
        """Create microscope connection widget."""
        connection_group = QWidget()
        connection_layout = QVBoxLayout(connection_group)
        
        self.connect_button = QPushButton("Connect to Microscope")
        self.connect_button.clicked.connect(self.connect_to_microscope)
        connection_layout.addWidget(self.connect_button)
        
        layout.addWidget(connection_group)
    
    def _create_position_fields(self, layout):
        """Create position input fields."""
        position_group = QWidget()
        position_layout = QFormLayout(position_group)
        
        # Create validators
        float_validator = QDoubleValidator()
        float_validator.setDecimals(3)
        
        # Create fields
        self.field_x_mm = QLineEdit("0.000")
        self.field_x_mm.setValidator(float_validator)
        
        self.field_y_mm = QLineEdit("0.000")
        self.field_y_mm.setValidator(float_validator)
        
        self.field_z_mm = QLineEdit("0.000")
        self.field_z_mm.setValidator(float_validator)
        
        self.field_r_deg = QLineEdit("0.000")
        self.field_r_deg.setValidator(float_validator)
        
        # Add to layout
        position_layout.addRow("X (mm):", self.field_x_mm)
        position_layout.addRow("Y (mm):", self.field_y_mm)
        position_layout.addRow("Z (mm):", self.field_z_mm)
        position_layout.addRow("R (deg):", self.field_r_deg)
        
        layout.addWidget(position_group)
    
    def _create_laser_controls(self, layout):
        """Create laser control widgets."""
        laser_group = QWidget()
        laser_layout = QVBoxLayout(laser_group)
        
        # Laser selection radio buttons
        self.laser_radio_buttons = []
        lasers = ["Laser 1 640 nm", "Laser 2 561 nm", "Laser 3 488 nm", "Laser 4 405 nm"]
        
        for laser in lasers:
            radio = QRadioButton(laser)
            self.laser_radio_buttons.append(radio)
            laser_layout.addWidget(radio)
        
        # Default selection
        if self.laser_radio_buttons:
            self.laser_radio_buttons[2].setChecked(True)  # 488nm default
        
        # Laser power
        power_layout = QHBoxLayout()
        power_layout.addWidget(QLabel("Laser Power (%):"))
        
        self.laser_power = QLineEdit("5.0")
        self.laser_power.setValidator(QDoubleValidator(0.0, 100.0, 1))
        power_layout.addWidget(self.laser_power)
        
        laser_layout.addLayout(power_layout)
        layout.addWidget(laser_group)
    
    def _create_sample_field(self, layout):
        """Create sample name field."""
        sample_layout = QHBoxLayout()
        sample_layout.addWidget(QLabel("Sample Name:"))
        
        self.sample_name = QLineEdit("default_sample")
        sample_layout.addWidget(self.sample_name)
        
        layout.addLayout(sample_layout)
    
    def _create_control_buttons(self, layout):
        """Create control buttons."""
        button_layout = QVBoxLayout()
        
        # Style definitions
        coordinate_button_style = """
            QPushButton {
                background-color: paleturquoise;
            }
            QPushButton:hover {
                border: 2px solid red;
            }
            QPushButton:pressed {
                border: 2px solid white;
            }
        """
        
        find_focus_button_style = """
            QPushButton {
                background-color: beige;
            }
            QPushButton:hover {
                border: 2px solid red;
            }
            QPushButton:pressed {
                border: 2px solid white;
            }
        """
        
        data_collection_style = """
            QPushButton {
                background-color: rgb(249, 132, 229);
            }
            QPushButton:hover {
                border: 2px solid red;
            }
            QPushButton:pressed {
                border: 2px solid white;
            }
        """
        
        # Create buttons
        buttons = [
            ("Find Sample", self.locate_sample_dialog, find_focus_button_style),
            ("Go to XYZR", self.go_to_position, coordinate_button_style),
            ("Take IF Snapshot", self.take_snapshot, coordinate_button_style),
            ("Copy Current Position", self.copy_current_position, coordinate_button_style),
            ("Set Home", self.set_home_action, coordinate_button_style),
            ("Track Sample by Angle", self.trace_ellipse_action, data_collection_style),
            ("Multi-angle Collection", self.multi_angle_dialog, data_collection_style),
            ("Stop Program", self.close_program, None),
        ]
        
        for text, callback, style in buttons:
            btn = QPushButton(text)
            btn.clicked.connect(callback)
            if style:
                btn.setStyleSheet(style)
            button_layout.addWidget(btn)
        
        layout.addLayout(button_layout)
    
    def connect_to_microscope(self):
        """Connect to the microscope."""
        try:
            # Create connection dialog
            self.microscope_connection = FlamingoConnect(self)
            
            if self.microscope_connection.exec() == QDialog.Accepted:
                # Get connection data
                connection_data = self.microscope_connection.connection_data
                
                # Create connection manager
                self.connection_manager = ConnectionManager(
                    connection_data[0],  # IP address
                    connection_data[1]   # Port
                )
                
                # Connect
                self.connection_manager.connect()
                
                # Set up controllers
                self._setup_controllers_with_connection()
                
                # Update UI
                self.connect_button.setEnabled(False)
                self.status_label.setText("Status: Connected")
                
        except Exception as e:
            show_warning_message(f"Failed to connect: {str(e)}")
    
    def check_for_active_thread(self):
        """Check if any thread is currently active."""
        # Clean up finished threads
        self.threads = [t for t in self.threads if t.is_alive()]
        
        # Check specific threads
        active_threads = [
            self.take_snapshot_thread,
            self.set_home_thread,
            self.go_to_position_thread,
            self.locate_sample_thread,
            self.trace_ellipse_thread,
            self.multi_angle_collection_thread
        ]
        
        return any(t and t.is_alive() for t in active_threads)
    
    def check_field(self, fields):
        """Validate that fields contain valid numbers."""
        for field in fields:
            try:
                float(field.text())
            except ValueError:
                show_warning_message(f"Invalid value in field: {field.text()}")
                return False
        return True
    
    def get_selected_radio_value(self):
        """Get the selected laser from radio buttons."""
        for radio in self.laser_radio_buttons:
            if radio.isChecked():
                return radio.text()
        show_warning_message("No laser selected")
        return False
    
    def take_snapshot(self):
        """Take a snapshot using the snapshot controller."""
        print("Take snapshot button pressed")
        
        if self.check_for_active_thread():
            print("Something else is still running!")
            return
        
        if not self.snapshot_controller:
            show_warning_message("Not connected to microscope")
            return
        
        # Validate position fields
        if not self.check_field([self.field_x_mm, self.field_y_mm, 
                                self.field_z_mm, self.field_r_deg]):
            return
        
        # Get values
        try:
            position = Position(
                x=float(self.field_x_mm.text()),
                y=float(self.field_y_mm.text()),
                z=float(self.field_z_mm.text()),
                r=float(self.field_r_deg.text())
            )
            
            laser_channel = self.get_selected_radio_value()
            if not laser_channel:
                return
                
            laser_power = float(self.laser_power.text())
            
        except ValueError as e:
            show_warning_message(f"Invalid input: {str(e)}")
            return
        
        # Create thread for snapshot
        self.take_snapshot_thread = Thread(
            target=self._run_snapshot,
            args=(position, laser_channel, laser_power)
        )
        self.take_snapshot_thread.daemon = True
        self.take_snapshot_thread.start()
        self.threads.append(self.take_snapshot_thread)
    
    def _run_snapshot(self, position, laser_channel, laser_power):
        """Run snapshot in thread."""
        try:
            self.snapshot_controller.take_snapshot(
                position=position,
                laser_channel=laser_channel,
                laser_power=laser_power
            )
        except Exception as e:
            print(f"Snapshot error: {e}")
    
    def set_home_action(self):
        """Set home position using settings controller."""
        print("Setting Home position")
        
        if self.check_for_active_thread():
            print("Something else is still running!")
            return
        
        if not self.settings_controller:
            show_warning_message("Not connected to microscope")
            return
        
        # Create thread
        self.set_home_thread = Thread(
            target=self._run_set_home
        )
        self.set_home_thread.daemon = True
        self.set_home_thread.start()
        self.threads.append(self.set_home_thread)
    
    def _run_set_home(self):
        """Run set home in thread."""
        try:
            current_position = self.microscope_controller.model.current_position
            self.settings_controller.set_home_position(current_position)
            print("Home position set successfully")
        except Exception as e:
            print(f"Set home error: {e}")
    
    def go_to_position(self):
        """Go to specified position using position controller."""
        print("Go to position button pressed")
        
        if self.check_for_active_thread():
            print("Something else is still running!")
            return
        
        if not self.position_controller:
            show_warning_message("Not connected to microscope")
            return
        
        # Validate fields
        if not self.check_field([self.field_x_mm, self.field_y_mm,
                                self.field_z_mm, self.field_r_deg]):
            return
        
        # Get position
        try:
            position = Position(
                x=float(self.field_x_mm.text()),
                y=float(self.field_y_mm.text()),
                z=float(self.field_z_mm.text()),
                r=float(self.field_r_deg.text())
            )
        except ValueError as e:
            show_warning_message(f"Invalid position: {str(e)}")
            return
        
        # Create thread
        self.go_to_position_thread = Thread(
            target=self._run_go_to_position,
            args=(position,)
        )
        self.go_to_position_thread.daemon = True
        self.go_to_position_thread.start()
        self.threads.append(self.go_to_position_thread)
    
    def _run_go_to_position(self, position):
        """Run go to position in thread."""
        try:
            self.position_controller.move_to_position(position)
        except Exception as e:
            print(f"Go to position error: {e}")
    
    def locate_sample_dialog(self):
        """Open locate sample dialog."""
        print("Locate sample button pressed")
        
        if self.check_for_active_thread():
            print("Something else is still running!")
            return
        
        if not self.sample_controller:
            show_warning_message("Not connected to microscope")
            return
        
        # Get current position for dialog
        current_pos = self.microscope_controller.model.current_position
        start_position = [current_pos.x, current_pos.y, current_pos.z, current_pos.r]
        
        # Create dialog
        dialog = CoordinateDialog(start_position, 1.2, self)
        
        if dialog.exec() == QDialog.Accepted:
            # Get values from dialog
            try:
                search_position = Position(
                    x=float(dialog.field_x_mm.text()),
                    y=float(dialog.field_y_mm.text()),
                    z=float(dialog.field_z_mm.text()),
                    r=float(dialog.field_r_deg.text())
                )
                
                sample_count = int(dialog.sample_count.text())
                z_search_depth = float(dialog.z_search_depth.text())
                
                laser_channel = self.get_selected_radio_value()
                if not laser_channel:
                    return
                    
                laser_power = float(self.laser_power.text())
                
            except ValueError as e:
                show_warning_message(f"Invalid input: {str(e)}")
                return
            
            # Create thread
            self.locate_sample_thread = Thread(
                target=self._run_locate_sample,
                args=(search_position, sample_count, z_search_depth,
                      laser_channel, laser_power)
            )
            self.locate_sample_thread.daemon = True
            self.locate_sample_thread.start()
            self.threads.append(self.locate_sample_thread)
    
    def _run_locate_sample(self, position, sample_count, z_search_depth,
                          laser_channel, laser_power):
        """Run locate sample in thread."""
        try:
            result = self.sample_controller.locate_sample(
                start_position=position,
                z_search_depth_mm=z_search_depth,
                sample_count=sample_count,
                laser_channel=laser_channel,
                laser_power=laser_power
            )
            
            if result:
                # Update GUI fields with found position
                self.field_x_mm.setText(f"{result.x:.3f}")
                self.field_y_mm.setText(f"{result.y:.3f}")
                self.field_z_mm.setText(f"{result.z:.3f}")
                self.field_r_deg.setText(f"{result.r:.3f}")
                
        except Exception as e:
            print(f"Locate sample error: {e}")
    
    def trace_ellipse_action(self):
        """Start ellipse tracing using ellipse controller."""
        print("Trace ellipse button pressed")
        
        if self.check_for_active_thread():
            print("Something else is still running!")
            return
        
        if not self.ellipse_controller:
            show_warning_message("Not connected to microscope")
            return
        
        # Check for sample name
        sample_name = self.sample_name.text()
        if not sample_name:
            show_warning_message("Please enter a sample name")
            return
        
        # Get parameters
        try:
            laser_channel = self.get_selected_radio_value()
            if not laser_channel:
                return
                
            laser_power = float(self.laser_power.text())
            
        except ValueError as e:
            show_warning_message(f"Invalid input: {str(e)}")
            return
        
        # Create thread
        self.trace_ellipse_thread = Thread(
            target=self._run_trace_ellipse,
            args=(sample_name, laser_channel, laser_power)
        )
        self.trace_ellipse_thread.daemon = True
        self.trace_ellipse_thread.start()
        self.threads.append(self.trace_ellipse_thread)
    
    def _run_trace_ellipse(self, sample_name, laser_channel, laser_power):
        """Run trace ellipse in thread."""
        try:
            self.ellipse_controller.trace_ellipse(
                sample_name=sample_name,
                laser_channel=laser_channel,
                laser_power=laser_power
            )
        except Exception as e:
            print(f"Trace ellipse error: {e}")
    
    def multi_angle_dialog(self):
        """Open multi-angle collection dialog."""
        print("Multi-angle collection button pressed")
        
        if self.check_for_active_thread():
            print("Something else is still running!")
            return
        
        if not self.multi_angle_controller:
            show_warning_message("Not connected to microscope")
            return
        
        # Check sample bounds exist
        sample_name = self.sample_name.text()
        top_file = os.path.join("sample_txt", sample_name, f"top_bounds_{sample_name}.txt")
        bottom_file = os.path.join("sample_txt", sample_name, f"bottom_bounds_{sample_name}.txt")
        
        if not os.path.exists(top_file) or not os.path.exists(bottom_file):
            show_warning_message(
                f"Bounds files not found for sample {sample_name}.\n"
                "Please run 'Track sample by angle' first."
            )
            return
        
        # Create dialog
        dialog = MultiAngleDialog(sample_name, self)
        
        # Connect OK button
        dialog.button_okay.clicked.connect(
            lambda: self._start_multi_angle_collection(dialog)
        )
        
        dialog.exec()
    
    def _start_multi_angle_collection(self, dialog):
        """Start multi-angle collection from dialog."""
        try:
            sample_name = dialog.field_sample_name.text()
            angle_increment = float(dialog.field_increment_angle.text())
            workflow_file = dialog.field_workflow_source.text()
            comment = dialog.field_sample_comment.text()
            
            # Check workflow exists
            if not os.path.exists(os.path.join("workflows", workflow_file)):
                show_warning_message(f"Workflow file {workflow_file} not found")
                return
            
            dialog.accept()
            
            # Create thread
            self.multi_angle_collection_thread = Thread(
                target=self._run_multi_angle_collection,
                args=(sample_name, angle_increment, workflow_file, comment)
            )
            self.multi_angle_collection_thread.daemon = True
            self.multi_angle_collection_thread.start()
            self.threads.append(self.multi_angle_collection_thread)
            
        except ValueError as e:
            show_warning_message(f"Invalid input: {str(e)}")
    
    def _run_multi_angle_collection(self, sample_name, angle_increment, 
                                   workflow_file, comment):
        """Run multi-angle collection in thread."""
        try:
            self.multi_angle_controller.run_collection(
                sample_name=sample_name,
                angle_step_size_deg=angle_increment,
                workflow_filename=workflow_file,
                comment=comment
            )
        except Exception as e:
            print(f"Multi-angle collection error: {e}")
    
    def copy_current_position(self):
        """Copy current microscope position to input fields."""
        if not self.microscope_controller:
            show_warning_message("Not connected to microscope")
            return
        
        current_pos = self.microscope_controller.model.current_position
        self.field_x_mm.setText(f"{current_pos.x:.3f}")
        self.field_y_mm.setText(f"{current_pos.y:.3f}")
        self.field_z_mm.setText(f"{current_pos.z:.3f}")
        self.field_r_deg.setText(f"{current_pos.r:.3f}")
    
    def predict_sample_focus_at_angle(self):
        """Predict sample focus at given angle using ellipse data."""
        print("Predict sample focus at angle")
        
        if self.check_for_active_thread():
            print("Something else is still running!")
            return
        
        if not self.ellipse_controller:
            show_warning_message("Not connected to microscope")
            return
        
        # Validate angle field
        if not self.check_field([self.field_r_deg]):
            return
        
        try:
            angle = float(self.field_r_deg.text())
            if not 0 <= angle <= 360:
                show_warning_message("Angle must be between 0 and 360 degrees")
                return
            
            sample_name = self.sample_name.text()
            
            # Check bounds files exist
            top_file = os.path.join("sample_txt", sample_name, f"top_bounds_{sample_name}.txt")
            bottom_file = os.path.join("sample_txt", sample_name, f"bottom_bounds_{sample_name}.txt")
            
            if not os.path.exists(top_file) or not os.path.exists(bottom_file):
                show_warning_message(
                    f"Bounds files not found for sample {sample_name}.\n"
                    "Please run 'Track sample by angle' first."
                )
                return
            
            # Get predicted position
            predicted_pos = self.ellipse_controller.predict_position_at_angle(
                sample_name, angle
            )
            
            if predicted_pos:
                # Update fields
                self.field_x_mm.setText(f"{predicted_pos.x:.3f}")
                self.field_y_mm.setText(f"{predicted_pos.y:.3f}")
                self.field_z_mm.setText(f"{predicted_pos.z:.3f}")
                
                # Take snapshot at predicted position
                self.take_snapshot()
                
        except Exception as e:
            show_warning_message(f"Error predicting position: {str(e)}")
    
    def cancel_action(self):
        """Cancel current running process."""
        print("Cancel action requested")
        
        # Set termination events
        terminate_event.set()
        
        # Wait briefly for threads to respond
        import time
        time.sleep(0.5)
        
        # Reset events
        terminate_event.clear()
        
        print("Cancellation signal sent")
    
    def close_program(self):
        """Close the program gracefully."""
        print("Closing program")
        
        # Cancel any running threads
        self.cancel_action()
        
        # Disconnect from microscope
        if self.connection_manager:
            try:
                self.connection_manager.disconnect()
            except:
                pass
        
        # Close the application
        QApplication.quit()
    
    def update_display(self):
        """Update the display with latest data from queues."""
        # Check for new images
        if not image_queue.empty():
            try:
                image_data = image_queue.get_nowait()
                
                # Convert to QImage and display
                if isinstance(image_data, np.ndarray):
                    qimage = convert_to_qimage(image_data)
                    pixmap = QPixmap.fromImage(qimage)
                    
                    # Scale to fit label
                    scaled_pixmap = pixmap.scaled(
                        self.image_label.size(),
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                    self.image_label.setPixmap(scaled_pixmap)
                    
            except:
                pass
        
        # Check for position updates
        if not stage_location_queue.empty():
            try:
                position_data = stage_location_queue.get_nowait()
                if len(position_data) >= 4:
                    self.position_display.setText(
                        f"Position: X: {position_data[0]:.3f}, "
                        f"Y: {position_data[1]:.3f}, "
                        f"Z: {position_data[2]:.3f}, "
                        f"R: {position_data[3]:.3f}"
                    )
            except:
                pass
        
        # Check other status updates
        if not other_data_queue.empty():
            try:
                data = other_data_queue.get_nowait()
                # Handle various status updates
                if isinstance(data, str):
                    self.status_label.setText(f"Status: {data}")
            except:
                pass
    
    def _update_position_display(self, model):
        """Update position display when model changes."""
        pos = model.current_position
        self.position_display.setText(
            f"Position: X: {pos.x:.3f}, Y: {pos.y:.3f}, "
            f"Z: {pos.z:.3f}, R: {pos.r:.3f}"
        )
        
        # Update status
        self.status_label.setText(f"Status: {model.state.value}")
    
    def basic_workflow_action(self):
        """Run a basic workflow collection."""
        print("Basic workflow action")
        
        if self.check_for_active_thread():
            print("Something else is still running!")
            return
        
        if not self.microscope_controller:
            show_warning_message("Not connected to microscope")
            return
        
        # TODO: Implement workflow dialog and execution
        show_warning_message("Basic workflow not yet implemented in MVC version")
    
    def add_your_code(self):
        """Placeholder for adding custom functionality."""
        if self.check_for_active_thread():
            print("Something else is still running!")
            return
        
        # Custom code can be added here
        show_warning_message("Add your custom code here")
    
    def closeEvent(self, event):
        """Handle window close event."""
        # Cancel any running threads
        self.cancel_action()
        
        # Stop timer
        self.timer.stop()
        
        # Disconnect if connected
        if self.connection_manager:
            try:
                self.connection_manager.disconnect()
            except:
                pass
        
        event.accept()


