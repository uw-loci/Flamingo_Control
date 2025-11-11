"""
Enhanced Stage Control View with complete movement controls.

This view provides comprehensive stage control including:
- Real-time position display
- Target position input fields with "Go To" buttons
- Relative movement controls (±0.1, ±1.0, ±10.0 mm)
- Home buttons for each axis
- Emergency stop button
- N7 reference position management
- Position verification status display
- Map visualization with current/target position
"""

import logging
from typing import Optional
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QGroupBox, QFormLayout, QDoubleSpinBox,
    QGridLayout, QFrame, QMessageBox
)
from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtGui import QFont

from py2flamingo.controllers.movement_controller import MovementController
from py2flamingo.models.microscope import Position


class EnhancedStageControlView(QWidget):
    """
    Enhanced UI view for stage control with complete movement functionality.

    Features:
    - Real-time position display for X, Y, Z, R
    - Target position inputs with Go To buttons
    - Relative movement buttons (jog controls)
    - Individual axis homing
    - Emergency stop
    - N7 reference position save/load
    - Position verification status
    """

    def __init__(self, movement_controller: MovementController):
        """
        Initialize enhanced stage control view.

        Args:
            movement_controller: MovementController instance
        """
        super().__init__()

        self.movement_controller = movement_controller
        self.logger = logging.getLogger(__name__)

        # Connect signals
        self.movement_controller.position_changed.connect(self._on_position_changed)
        self.movement_controller.motion_started.connect(self._on_motion_started)
        self.movement_controller.motion_stopped.connect(self._on_motion_stopped)
        self.movement_controller.position_verified.connect(self._on_position_verified)
        self.movement_controller.error_occurred.connect(self._on_error)

        # Start position monitoring
        self.movement_controller.start_position_monitoring(interval=0.5)

        self.setup_ui()

        # Request initial position update immediately
        self._request_initial_position()

        self.logger.info("EnhancedStageControlView initialized")

    def setup_ui(self) -> None:
        """Create and layout all UI components."""
        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)

        # Title
        title = QLabel("Stage Control - Complete Movement Interface")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title)

        # Current Position Display
        main_layout.addWidget(self._create_position_display())

        # Target Position & Go To Controls
        main_layout.addWidget(self._create_goto_controls())

        # Relative Movement Controls
        main_layout.addWidget(self._create_relative_controls())

        # Home & Stop Controls
        main_layout.addWidget(self._create_safety_controls())

        # N7 Reference Position
        main_layout.addWidget(self._create_n7_reference_controls())

        # Status Display
        main_layout.addWidget(self._create_status_display())

        main_layout.addStretch()
        self.setLayout(main_layout)

    def _create_position_display(self) -> QGroupBox:
        """Create current position display group with action buttons."""
        group = QGroupBox("Current Position")
        main_layout = QHBoxLayout()

        # Left side: Position display (compact)
        position_layout = QFormLayout()

        # Position labels with status indicators
        label_style = "background-color: #e8f5e9; padding: 8px; border: 2px solid #4caf50; border-radius: 4px; font-size: 11pt; font-weight: bold;"

        self.x_pos_label = QLabel("0.000 mm")
        self.x_pos_label.setStyleSheet(label_style)
        position_layout.addRow("X Position:", self.x_pos_label)

        self.y_pos_label = QLabel("0.000 mm")
        self.y_pos_label.setStyleSheet(label_style)
        position_layout.addRow("Y Position:", self.y_pos_label)

        self.z_pos_label = QLabel("0.000 mm")
        self.z_pos_label.setStyleSheet(label_style)
        position_layout.addRow("Z Position:", self.z_pos_label)

        self.r_pos_label = QLabel("0.00°")
        self.r_pos_label.setStyleSheet(label_style)
        position_layout.addRow("Rotation:", self.r_pos_label)

        main_layout.addLayout(position_layout)

        # Right side: Action buttons
        button_layout = QVBoxLayout()
        button_layout.addStretch()

        self.show_history_btn = QPushButton("Show Position History")
        self.show_history_btn.clicked.connect(self._on_show_history_clicked)
        self.show_history_btn.setStyleSheet(
            "background-color: #2196f3; color: white; padding: 10px; "
            "font-weight: bold; font-size: 10pt;"
        )
        button_layout.addWidget(self.show_history_btn)

        button_layout.addStretch()
        main_layout.addLayout(button_layout)

        group.setLayout(main_layout)
        return group

    def _create_goto_controls(self) -> QGroupBox:
        """Create target position and Go To button controls."""
        group = QGroupBox("Absolute Positioning - Go To Target")
        layout = QVBoxLayout()

        # Get stage limits
        limits = self.movement_controller.get_stage_limits()

        # Create grid layout for better column control
        # Column 0: Label, Column 1: Input, Column 2: Go To, Column 3: Home
        grid = QGridLayout()
        grid.setSpacing(8)

        # Headers
        grid.addWidget(QLabel("<b>Axis</b>"), 0, 0)
        grid.addWidget(QLabel("<b>Target</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Go To Axis</b>"), 0, 2)
        grid.addWidget(QLabel("<b>Home Axis</b>"), 0, 3)

        # X-axis row
        row = 1
        grid.addWidget(QLabel("X:"), row, 0)

        self.x_target_spin = QDoubleSpinBox()
        self.x_target_spin.setRange(limits['x']['min'], limits['x']['max'])
        self.x_target_spin.setDecimals(3)
        self.x_target_spin.setSingleStep(0.1)
        self.x_target_spin.setSuffix(" mm")
        self.x_target_spin.setMinimumWidth(100)
        grid.addWidget(self.x_target_spin, row, 1)

        self.x_goto_btn = QPushButton("Go To X")
        self.x_goto_btn.clicked.connect(lambda: self._on_goto_clicked('x'))
        self.x_goto_btn.setStyleSheet("background-color: #2196f3; color: white; padding: 6px; font-weight: bold;")
        grid.addWidget(self.x_goto_btn, row, 2)

        self.x_home_btn = QPushButton("Home X")
        self.x_home_btn.clicked.connect(lambda: self._on_home_axis_clicked('x'))
        self.x_home_btn.setStyleSheet("background-color: #ff9800; color: white; padding: 6px;")
        grid.addWidget(self.x_home_btn, row, 3)

        # Y-axis row
        row = 2
        grid.addWidget(QLabel("Y:"), row, 0)

        self.y_target_spin = QDoubleSpinBox()
        self.y_target_spin.setRange(limits['y']['min'], limits['y']['max'])
        self.y_target_spin.setDecimals(3)
        self.y_target_spin.setSingleStep(0.1)
        self.y_target_spin.setSuffix(" mm")
        self.y_target_spin.setMinimumWidth(100)
        grid.addWidget(self.y_target_spin, row, 1)

        self.y_goto_btn = QPushButton("Go To Y")
        self.y_goto_btn.clicked.connect(lambda: self._on_goto_clicked('y'))
        self.y_goto_btn.setStyleSheet("background-color: #2196f3; color: white; padding: 6px; font-weight: bold;")
        grid.addWidget(self.y_goto_btn, row, 2)

        self.y_home_btn = QPushButton("Home Y")
        self.y_home_btn.clicked.connect(lambda: self._on_home_axis_clicked('y'))
        self.y_home_btn.setStyleSheet("background-color: #ff9800; color: white; padding: 6px;")
        grid.addWidget(self.y_home_btn, row, 3)

        # Z-axis row
        row = 3
        grid.addWidget(QLabel("Z:"), row, 0)

        self.z_target_spin = QDoubleSpinBox()
        self.z_target_spin.setRange(limits['z']['min'], limits['z']['max'])
        self.z_target_spin.setDecimals(3)
        self.z_target_spin.setSingleStep(0.1)
        self.z_target_spin.setSuffix(" mm")
        self.z_target_spin.setMinimumWidth(100)
        grid.addWidget(self.z_target_spin, row, 1)

        self.z_goto_btn = QPushButton("Go To Z")
        self.z_goto_btn.clicked.connect(lambda: self._on_goto_clicked('z'))
        self.z_goto_btn.setStyleSheet("background-color: #2196f3; color: white; padding: 6px; font-weight: bold;")
        grid.addWidget(self.z_goto_btn, row, 2)

        self.z_home_btn = QPushButton("Home Z")
        self.z_home_btn.clicked.connect(lambda: self._on_home_axis_clicked('z'))
        self.z_home_btn.setStyleSheet("background-color: #ff9800; color: white; padding: 6px;")
        grid.addWidget(self.z_home_btn, row, 3)

        # R-axis row
        row = 4
        grid.addWidget(QLabel("R:"), row, 0)

        self.r_target_spin = QDoubleSpinBox()
        self.r_target_spin.setRange(0, 360)
        self.r_target_spin.setDecimals(2)
        self.r_target_spin.setSingleStep(1.0)
        self.r_target_spin.setSuffix("°")
        self.r_target_spin.setMinimumWidth(100)
        grid.addWidget(self.r_target_spin, row, 1)

        self.r_goto_btn = QPushButton("Go To R")
        self.r_goto_btn.clicked.connect(lambda: self._on_goto_clicked('r'))
        self.r_goto_btn.setStyleSheet("background-color: #2196f3; color: white; padding: 6px; font-weight: bold;")
        grid.addWidget(self.r_goto_btn, row, 2)

        self.r_home_btn = QPushButton("Home R")
        self.r_home_btn.clicked.connect(lambda: self._on_home_axis_clicked('r'))
        self.r_home_btn.setStyleSheet("background-color: #ff9800; color: white; padding: 6px;")
        grid.addWidget(self.r_home_btn, row, 3)

        layout.addLayout(grid)

        # Add separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        # Add "Go To Position" button that moves all 4 axes at once
        goto_position_layout = QHBoxLayout()
        goto_position_layout.addStretch()

        self.goto_position_btn = QPushButton("Go To Position (All 4 Axes)")
        self.goto_position_btn.clicked.connect(self._on_goto_position_clicked)
        self.goto_position_btn.setStyleSheet(
            "background-color: #4caf50; color: white; padding: 10px; "
            "font-weight: bold; font-size: 11pt; border-radius: 4px;"
        )
        self.goto_position_btn.setMinimumWidth(300)
        goto_position_layout.addWidget(self.goto_position_btn)

        goto_position_layout.addStretch()
        layout.addLayout(goto_position_layout)

        group.setLayout(layout)
        return group

    def _create_relative_controls(self) -> QGroupBox:
        """Create relative movement (jog) controls."""
        group = QGroupBox("Relative Movement Controls (Jog)")
        layout = QVBoxLayout()

        info = QLabel("Click buttons to move by the specified increment")
        info.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(info)

        # Create grid for jog buttons
        grid = QGridLayout()
        grid.setSpacing(5)

        # Headers
        grid.addWidget(QLabel("Axis"), 0, 0)
        grid.addWidget(QLabel("±0.1"), 0, 1, 1, 2, Qt.AlignCenter)
        grid.addWidget(QLabel("±1.0"), 0, 3, 1, 2, Qt.AlignCenter)
        grid.addWidget(QLabel("±10.0"), 0, 5, 1, 2, Qt.AlignCenter)

        # X-axis jog buttons
        row = 1
        grid.addWidget(QLabel("<b>X (mm)</b>"), row, 0)
        grid.addWidget(self._create_jog_button("−0.1", lambda: self._jog('x', -0.1)), row, 1)
        grid.addWidget(self._create_jog_button("+0.1", lambda: self._jog('x', 0.1)), row, 2)
        grid.addWidget(self._create_jog_button("−1.0", lambda: self._jog('x', -1.0)), row, 3)
        grid.addWidget(self._create_jog_button("+1.0", lambda: self._jog('x', 1.0)), row, 4)
        grid.addWidget(self._create_jog_button("−10.0", lambda: self._jog('x', -10.0)), row, 5)
        grid.addWidget(self._create_jog_button("+10.0", lambda: self._jog('x', 10.0)), row, 6)

        # Y-axis jog buttons
        row = 2
        grid.addWidget(QLabel("<b>Y (mm)</b>"), row, 0)
        grid.addWidget(self._create_jog_button("−0.1", lambda: self._jog('y', -0.1)), row, 1)
        grid.addWidget(self._create_jog_button("+0.1", lambda: self._jog('y', 0.1)), row, 2)
        grid.addWidget(self._create_jog_button("−1.0", lambda: self._jog('y', -1.0)), row, 3)
        grid.addWidget(self._create_jog_button("+1.0", lambda: self._jog('y', 1.0)), row, 4)
        grid.addWidget(self._create_jog_button("−10.0", lambda: self._jog('y', -10.0)), row, 5)
        grid.addWidget(self._create_jog_button("+10.0", lambda: self._jog('y', 10.0)), row, 6)

        # Z-axis jog buttons
        row = 3
        grid.addWidget(QLabel("<b>Z (mm)</b>"), row, 0)
        grid.addWidget(self._create_jog_button("−0.1", lambda: self._jog('z', -0.1)), row, 1)
        grid.addWidget(self._create_jog_button("+0.1", lambda: self._jog('z', 0.1)), row, 2)
        grid.addWidget(self._create_jog_button("−1.0", lambda: self._jog('z', -1.0)), row, 3)
        grid.addWidget(self._create_jog_button("+1.0", lambda: self._jog('z', 1.0)), row, 4)
        grid.addWidget(self._create_jog_button("−10.0", lambda: self._jog('z', -10.0)), row, 5)
        grid.addWidget(self._create_jog_button("+10.0", lambda: self._jog('z', 10.0)), row, 6)

        # R-axis jog buttons (different increments: 1°, 10°, 45°)
        row = 4
        grid.addWidget(QLabel("<b>R (°)</b>"), row, 0)
        grid.addWidget(self._create_jog_button("−1°", lambda: self._jog('r', -1.0)), row, 1)
        grid.addWidget(self._create_jog_button("+1°", lambda: self._jog('r', 1.0)), row, 2)
        grid.addWidget(self._create_jog_button("−10°", lambda: self._jog('r', -10.0)), row, 3)
        grid.addWidget(self._create_jog_button("+10°", lambda: self._jog('r', 10.0)), row, 4)
        grid.addWidget(self._create_jog_button("−45°", lambda: self._jog('r', -45.0)), row, 5)
        grid.addWidget(self._create_jog_button("+45°", lambda: self._jog('r', 45.0)), row, 6)

        layout.addLayout(grid)
        group.setLayout(layout)
        return group

    def _create_jog_button(self, text: str, callback) -> QPushButton:
        """Create a jog button with consistent styling."""
        btn = QPushButton(text)
        btn.setMinimumWidth(60)
        btn.clicked.connect(callback)

        # Style based on direction
        if text.startswith('−') or text.startswith('-'):
            btn.setStyleSheet("background-color: #ffccbc; padding: 5px; font-weight: bold;")
        else:
            btn.setStyleSheet("background-color: #c8e6c9; padding: 5px; font-weight: bold;")

        return btn

    def _create_safety_controls(self) -> QGroupBox:
        """Create home all and emergency stop controls."""
        group = QGroupBox("Safety Controls")
        layout = QHBoxLayout()

        # Home All button
        self.home_all_btn = QPushButton("🏠 Home All Axes")
        self.home_all_btn.clicked.connect(self._on_home_all_clicked)
        self.home_all_btn.setStyleSheet(
            "background-color: #4caf50; color: white; padding: 12px; "
            "font-weight: bold; font-size: 11pt; border-radius: 6px;"
        )
        layout.addWidget(self.home_all_btn)

        # Emergency Stop button
        self.estop_btn = QPushButton("🛑 EMERGENCY STOP")
        self.estop_btn.clicked.connect(self._on_emergency_stop_clicked)
        self.estop_btn.setStyleSheet(
            "background-color: #f44336; color: white; padding: 12px; "
            "font-weight: bold; font-size: 11pt; border-radius: 6px; border: 3px solid #b71c1c;"
        )
        layout.addWidget(self.estop_btn)

        group.setLayout(layout)
        return group

    def _create_n7_reference_controls(self) -> QGroupBox:
        """Create N7 reference position controls."""
        group = QGroupBox("N7 Reference Position")
        layout = QVBoxLayout()

        # Display current N7 reference
        self.n7_ref_label = QLabel("Not set")
        self.n7_ref_label.setStyleSheet("background-color: #fff3cd; padding: 6px; border: 1px solid #ff9800;")
        layout.addWidget(QLabel("Current N7 Reference:"))
        layout.addWidget(self.n7_ref_label)

        # Update display
        self._update_n7_reference_display()

        # Buttons
        btn_layout = QHBoxLayout()

        self.save_n7_btn = QPushButton("Set Current as N7 Reference")
        self.save_n7_btn.clicked.connect(self._on_save_n7_clicked)
        self.save_n7_btn.setStyleSheet("background-color: #2196f3; color: white; padding: 8px;")
        btn_layout.addWidget(self.save_n7_btn)

        self.goto_n7_btn = QPushButton("Go To N7 Reference")
        self.goto_n7_btn.clicked.connect(self._on_goto_n7_clicked)
        self.goto_n7_btn.setStyleSheet("background-color: #4caf50; color: white; padding: 8px;")
        btn_layout.addWidget(self.goto_n7_btn)

        layout.addLayout(btn_layout)
        group.setLayout(layout)
        return group

    def _create_status_display(self) -> QGroupBox:
        """Create status display area."""
        group = QGroupBox("Status")
        layout = QVBoxLayout()

        # Motion status
        self.motion_status_label = QLabel("Ready")
        self.motion_status_label.setStyleSheet(
            "background-color: #e8f5e9; color: #2e7d32; padding: 8px; "
            "border: 2px solid #4caf50; font-weight: bold; font-size: 10pt;"
        )
        layout.addWidget(self.motion_status_label)

        # Verification status
        self.verify_status_label = QLabel("")
        self.verify_status_label.setStyleSheet("padding: 6px; font-size: 9pt;")
        layout.addWidget(self.verify_status_label)

        # Message display
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet("padding: 6px; font-size: 9pt;")
        layout.addWidget(self.message_label)

        group.setLayout(layout)
        return group

    # ============================================================================
    # Event Handlers
    # ============================================================================

    def _on_goto_clicked(self, axis: str) -> None:
        """Handle Go To button click."""
        try:
            # Get target value from spin box
            spin_boxes = {
                'x': self.x_target_spin,
                'y': self.y_target_spin,
                'z': self.z_target_spin,
                'r': self.r_target_spin
            }

            target = spin_boxes[axis].value()

            # Send movement command
            self.movement_controller.move_absolute(axis, target, verify=True)
            self.message_label.setText(f"Moving {axis.upper()} to {target:.3f}...")
            self.message_label.setStyleSheet("color: blue; padding: 6px;")

        except Exception as e:
            QMessageBox.critical(self, "Movement Error", str(e))

    def _on_goto_position_clicked(self) -> None:
        """Handle Go To Position button click - moves all 4 axes at once."""
        try:
            # Read all target values from spin boxes
            target_x = self.x_target_spin.value()
            target_y = self.y_target_spin.value()
            target_z = self.z_target_spin.value()
            target_r = self.r_target_spin.value()

            # Show confirmation dialog
            reply = QMessageBox.question(
                self,
                "Go To Position",
                f"Move to the following position?\n\n"
                f"X: {target_x:.3f} mm\n"
                f"Y: {target_y:.3f} mm\n"
                f"Z: {target_z:.3f} mm\n"
                f"R: {target_r:.2f}°",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply == QMessageBox.Yes:
                # Create Position object
                target_position = Position(
                    x=target_x,
                    y=target_y,
                    z=target_z,
                    r=target_r
                )

                # Move to position with validation
                self.movement_controller.position_controller.move_to_position(
                    target_position,
                    validate=True
                )

                self.message_label.setText("Moving to target position...")
                self.message_label.setStyleSheet("color: blue; padding: 6px;")

        except Exception as e:
            QMessageBox.critical(self, "Movement Error", str(e))

    def _on_home_axis_clicked(self, axis: str) -> None:
        """Handle individual axis home button click."""
        try:
            self.movement_controller.home_axis(axis)
            self.message_label.setText(f"Homing {axis.upper()} axis...")
            self.message_label.setStyleSheet("color: blue; padding: 6px;")

        except Exception as e:
            QMessageBox.critical(self, "Home Error", str(e))

    def _on_home_all_clicked(self) -> None:
        """Handle Home All button click."""
        reply = QMessageBox.question(
            self,
            "Home All Axes",
            "Move all axes to home position?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                self.movement_controller.position_controller.go_home()
                self.message_label.setText("Homing all axes...")
                self.message_label.setStyleSheet("color: blue; padding: 6px;")

            except Exception as e:
                QMessageBox.critical(self, "Home Error", str(e))

    def _on_emergency_stop_clicked(self) -> None:
        """Handle emergency stop button click.

        Stops current motion immediately and automatically clears the stop
        flag after 2 seconds so user can resume normal operation.
        """
        # Disable button to prevent multiple rapid clicks
        self.estop_btn.setEnabled(False)

        # Stop current motion
        self.movement_controller.halt_motion()

        # Show emergency stop status
        self.motion_status_label.setText("EMERGENCY STOPPED - Clearing in 2s...")
        self.motion_status_label.setStyleSheet(
            "background-color: #ffebee; color: #c62828; padding: 8px; "
            "border: 3px solid #f44336; font-weight: bold; font-size: 10pt;"
        )

        # Automatically clear emergency stop after 2 seconds
        # This allows the user to resume normal operation without manual intervention
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(2000, self._clear_emergency_stop)

    def _clear_emergency_stop(self) -> None:
        """Clear emergency stop flag and restore normal operation."""
        self.movement_controller.position_controller.clear_emergency_stop()

        # Re-enable emergency stop button
        self.estop_btn.setEnabled(True)

        # Show cleared status
        self.motion_status_label.setText("Emergency stop cleared - Ready")
        self.motion_status_label.setStyleSheet(
            "background-color: #e8f5e9; color: #2e7d32; padding: 8px; "
            "border: 2px solid #4caf50; font-weight: bold; font-size: 10pt;"
        )
        self.logger.info("Emergency stop cleared - normal operation resumed")

    def _jog(self, axis: str, delta: float) -> None:
        """Handle jog button click."""
        try:
            self.movement_controller.move_relative(axis, delta, verify=False)
            sign = "+" if delta > 0 else ""
            self.message_label.setText(f"Jogging {axis.upper()} by {sign}{delta:.3f}...")
            self.message_label.setStyleSheet("color: blue; padding: 6px;")

        except Exception as e:
            QMessageBox.warning(self, "Jog Error", str(e))

    def _on_save_n7_clicked(self) -> None:
        """Handle Set N7 Reference button click."""
        reply = QMessageBox.question(
            self,
            "Save N7 Reference",
            "Save current position as N7 reference?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            if self.movement_controller.save_n7_reference():
                self._update_n7_reference_display()
                QMessageBox.information(self, "Success", "N7 reference position saved")
            else:
                QMessageBox.critical(self, "Error", "Failed to save N7 reference")

    def _on_goto_n7_clicked(self) -> None:
        """Handle Go To N7 Reference button click."""
        n7_ref = self.movement_controller.get_n7_reference()

        if n7_ref is None:
            QMessageBox.warning(self, "No Reference", "N7 reference position not set")
            return

        reply = QMessageBox.question(
            self,
            "Go To N7 Reference",
            f"Move to N7 reference position?\n\n"
            f"X: {n7_ref.x:.3f} mm\n"
            f"Y: {n7_ref.y:.3f} mm\n"
            f"Z: {n7_ref.z:.3f} mm\n"
            f"R: {n7_ref.r:.2f}°",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                self.movement_controller.position_controller.move_to_position(n7_ref, validate=True)
                self.message_label.setText("Moving to N7 reference position...")
                self.message_label.setStyleSheet("color: blue; padding: 6px;")

            except Exception as e:
                QMessageBox.critical(self, "Movement Error", str(e))

    def _on_show_history_clicked(self) -> None:
        """Handle Show Position History button click."""
        from py2flamingo.views.position_history_dialog import PositionHistoryDialog

        # Create and show the position history dialog
        dialog = PositionHistoryDialog(self.movement_controller, parent=self)
        dialog.exec_()  # Modal dialog

    def _update_n7_reference_display(self) -> None:
        """Update N7 reference position display."""
        n7_ref = self.movement_controller.get_n7_reference()

        if n7_ref:
            text = f"X={n7_ref.x:.3f}, Y={n7_ref.y:.3f}, Z={n7_ref.z:.3f}, R={n7_ref.r:.2f}°"
            self.n7_ref_label.setText(text)
        else:
            self.n7_ref_label.setText("Not set")

    # ============================================================================
    # Signal Slots
    # ============================================================================

    @pyqtSlot(float, float, float, float)
    def _on_position_changed(self, x: float, y: float, z: float, r: float) -> None:
        """Update position display and target fields when position changes."""
        # Update current position display
        self.x_pos_label.setText(f"{x:.3f} mm")
        self.y_pos_label.setText(f"{y:.3f} mm")
        self.z_pos_label.setText(f"{z:.3f} mm")
        self.r_pos_label.setText(f"{r:.2f}°")

        # Update "Go To Target" fields to match current position
        # This links the two sections so users always see current position in target fields
        self.x_target_spin.setValue(x)
        self.y_target_spin.setValue(y)
        self.z_target_spin.setValue(z)
        self.r_target_spin.setValue(r)

    @pyqtSlot(str)
    def _on_motion_started(self, axis_name: str) -> None:
        """Update status when motion starts."""
        self.motion_status_label.setText(f"Moving {axis_name}...")
        self.motion_status_label.setStyleSheet(
            "background-color: #fff3cd; color: #ff6f00; padding: 8px; "
            "border: 2px solid #ff9800; font-weight: bold; font-size: 10pt;"
        )
        self.verify_status_label.setText("")

        # Disable controls during movement
        self._set_controls_enabled(False)

    @pyqtSlot(str)
    def _on_motion_stopped(self, axis_name: str) -> None:
        """Update status when motion completes."""
        self.motion_status_label.setText(f"{axis_name} motion complete - Ready")
        self.motion_status_label.setStyleSheet(
            "background-color: #e8f5e9; color: #2e7d32; padding: 8px; "
            "border: 2px solid #4caf50; font-weight: bold; font-size: 10pt;"
        )

        # Re-enable controls
        self._set_controls_enabled(True)

    @pyqtSlot(bool, str)
    def _on_position_verified(self, success: bool, message: str) -> None:
        """Update status when position verification completes."""
        if success:
            self.verify_status_label.setText("✓ " + message)
            self.verify_status_label.setStyleSheet(
                "background-color: #e8f5e9; color: #2e7d32; padding: 6px; "
                "border: 1px solid #4caf50; font-size: 9pt;"
            )
        else:
            self.verify_status_label.setText("⚠ " + message)
            self.verify_status_label.setStyleSheet(
                "background-color: #fff3cd; color: #ff6f00; padding: 6px; "
                "border: 1px solid #ff9800; font-size: 9pt;"
            )

    @pyqtSlot(str)
    def _on_error(self, message: str) -> None:
        """Update status when error occurs."""
        self.message_label.setText(f"Error: {message}")
        self.message_label.setStyleSheet("color: red; padding: 6px;")

        self.motion_status_label.setText("Error - Ready")
        self.motion_status_label.setStyleSheet(
            "background-color: #ffebee; color: #c62828; padding: 8px; "
            "border: 2px solid #f44336; font-weight: bold; font-size: 10pt;"
        )

        # Re-enable controls
        self._set_controls_enabled(True)

    def _set_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable movement controls."""
        # Go To buttons
        self.x_goto_btn.setEnabled(enabled)
        self.y_goto_btn.setEnabled(enabled)
        self.z_goto_btn.setEnabled(enabled)
        self.r_goto_btn.setEnabled(enabled)
        self.goto_position_btn.setEnabled(enabled)  # All 4 axes at once

        # Home buttons
        self.x_home_btn.setEnabled(enabled)
        self.y_home_btn.setEnabled(enabled)
        self.z_home_btn.setEnabled(enabled)
        self.r_home_btn.setEnabled(enabled)
        self.home_all_btn.setEnabled(enabled)

        # N7 buttons
        self.save_n7_btn.setEnabled(enabled)
        self.goto_n7_btn.setEnabled(enabled)

        # Emergency stop always enabled
        self.estop_btn.setEnabled(True)

    def _request_initial_position(self) -> None:
        """Request and display the initial position from the microscope immediately."""
        try:
            # Get current position from the controller
            position = self.movement_controller.get_position()
            if position:
                # Update the display with the current position
                self._on_position_changed(position.x, position.y, position.z, position.r)
                self.logger.info(f"Initial position loaded: {position}")
            else:
                self.logger.warning("No initial position available")
        except Exception as e:
            self.logger.error(f"Error requesting initial position: {e}")

    def closeEvent(self, event) -> None:
        """Handle widget close event."""
        # Stop position monitoring
        self.movement_controller.stop_position_monitoring()
        event.accept()
