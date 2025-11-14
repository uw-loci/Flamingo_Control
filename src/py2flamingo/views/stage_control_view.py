"""
Stage Control View with movement controls.

This view provides stage control including:
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
    QGridLayout, QFrame, QMessageBox, QComboBox, QListWidget,
    QLineEdit, QInputDialog, QDialog, QDialogButtonBox, QSizePolicy
)
from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtGui import QFont

from py2flamingo.controllers.movement_controller import MovementController
from py2flamingo.models.microscope import Position


class SetHomePositionDialog(QDialog):
    """Dialog for setting home position with bounds validation."""

    def __init__(self, current_position, stage_limits, parent=None):
        """
        Initialize set home position dialog.

        Args:
            current_position: Current stage position (Position object)
            stage_limits: Stage limits dict from controller
            parent: Parent widget
        """
        super().__init__(parent)
        self.setWindowTitle("Set Home Position")
        self.current_position = current_position
        self.stage_limits = stage_limits

        self._setup_ui()

    def _setup_ui(self):
        """Create dialog UI."""
        layout = QVBoxLayout()

        # Info label
        info_label = QLabel(
            "Set the home position for this microscope.\n"
            "Default values are the current stage position.\n"
            "Position must be within stage limits."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(info_label)

        # Form for X, Y, Z, R inputs
        form_layout = QFormLayout()

        # X axis
        self.x_spinbox = QDoubleSpinBox()
        self.x_spinbox.setRange(
            self.stage_limits['x']['min'],
            self.stage_limits['x']['max']
        )
        self.x_spinbox.setDecimals(3)
        self.x_spinbox.setSuffix(" mm")
        self.x_spinbox.setValue(self.current_position.x)
        self.x_spinbox.setSingleStep(0.1)
        form_layout.addRow(
            f"X ({self.stage_limits['x']['min']:.2f} to {self.stage_limits['x']['max']:.2f} mm):",
            self.x_spinbox
        )

        # Y axis
        self.y_spinbox = QDoubleSpinBox()
        self.y_spinbox.setRange(
            self.stage_limits['y']['min'],
            self.stage_limits['y']['max']
        )
        self.y_spinbox.setDecimals(3)
        self.y_spinbox.setSuffix(" mm")
        self.y_spinbox.setValue(self.current_position.y)
        self.y_spinbox.setSingleStep(0.1)
        form_layout.addRow(
            f"Y ({self.stage_limits['y']['min']:.2f} to {self.stage_limits['y']['max']:.2f} mm):",
            self.y_spinbox
        )

        # Z axis
        self.z_spinbox = QDoubleSpinBox()
        self.z_spinbox.setRange(
            self.stage_limits['z']['min'],
            self.stage_limits['z']['max']
        )
        self.z_spinbox.setDecimals(3)
        self.z_spinbox.setSuffix(" mm")
        self.z_spinbox.setValue(self.current_position.z)
        self.z_spinbox.setSingleStep(0.1)
        form_layout.addRow(
            f"Z ({self.stage_limits['z']['min']:.2f} to {self.stage_limits['z']['max']:.2f} mm):",
            self.z_spinbox
        )

        # R axis
        self.r_spinbox = QDoubleSpinBox()
        self.r_spinbox.setRange(
            self.stage_limits['r']['min'],
            self.stage_limits['r']['max']
        )
        self.r_spinbox.setDecimals(2)
        self.r_spinbox.setSuffix("°")
        self.r_spinbox.setValue(self.current_position.r)
        self.r_spinbox.setSingleStep(1.0)
        form_layout.addRow(
            f"R ({self.stage_limits['r']['min']:.1f} to {self.stage_limits['r']['max']:.1f}°):",
            self.r_spinbox
        )

        layout.addLayout(form_layout)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)
        self.setMinimumWidth(450)

    def get_position(self):
        """
        Get the position from the dialog.

        Returns:
            Position object with values from spinboxes
        """
        return Position(
            x=self.x_spinbox.value(),
            y=self.y_spinbox.value(),
            z=self.z_spinbox.value(),
            r=self.r_spinbox.value()
        )


class StageControlView(QWidget):
    """
    UI view for stage control and movement.

    Features:
    - Real-time position display for X, Y, Z, R
    - Target position inputs with Go To buttons
    - Relative movement buttons (jog controls with dropdown increment selector)
    - Individual axis homing and home all
    - Set/Go To home position
    - Emergency stop
    - Position presets (save/load/delete named positions)
    - Position history dialog
    - Position verification status
    """

    def __init__(self, movement_controller: MovementController):
        """
        Initialize stage control view.

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

        self.logger.info("StageControlView initialized")

    def setup_ui(self) -> None:
        """Create and layout all UI components."""
        main_layout = QVBoxLayout()
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Top section: Current Position and Jog Controls side-by-side
        top_layout = QHBoxLayout()
        top_layout.addWidget(self._create_position_display())
        top_layout.addWidget(self._create_relative_controls())
        top_layout.addStretch()  # Push widgets to the left
        main_layout.addLayout(top_layout)

        # Target Position & Go To Controls
        main_layout.addWidget(self._create_goto_controls())

        # Home & Stop Controls
        main_layout.addWidget(self._create_safety_controls())

        # Saved Position Presets
        main_layout.addWidget(self._create_preset_controls())

        # Status Display
        main_layout.addWidget(self._create_status_display())

        main_layout.addStretch()
        self.setLayout(main_layout)

    def _create_position_display(self) -> QGroupBox:
        """Create current position display group (compact)."""
        group = QGroupBox("Current Position")
        position_layout = QFormLayout()
        position_layout.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        position_layout.setLabelAlignment(Qt.AlignRight)

        # Position labels with status indicators (more compact)
        label_style = "background-color: #e8f5e9; padding: 5px; border: 1px solid #4caf50; border-radius: 3px; font-size: 10pt; font-weight: bold; min-width: 80px;"

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

        group.setLayout(position_layout)
        group.setMaximumWidth(220)
        group.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        return group

    def _create_goto_controls(self) -> QGroupBox:
        """Create target position and Go To button controls."""
        group = QGroupBox("Position Control")
        layout = QVBoxLayout()

        # Get stage limits
        limits = self.movement_controller.get_stage_limits()

        # Create grid layout for better column control
        # Column 0: Label (narrow), Column 1: Input, Column 2: Go To, Column 3: Home
        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnStretch(0, 0)  # Labels: minimum width
        grid.setColumnStretch(1, 1)  # Input fields: some stretch
        grid.setColumnStretch(2, 0)  # Go To buttons: no stretch
        grid.setColumnStretch(3, 1)  # Home buttons: more space for longer labels

        # Headers
        axis_header = QLabel("<b>Axis</b>")
        axis_header.setMaximumWidth(30)
        grid.addWidget(axis_header, 0, 0)
        grid.addWidget(QLabel("<b>Target</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Go To Axis</b>"), 0, 2)
        grid.addWidget(QLabel("<b>Home Axis</b>"), 0, 3)

        # X-axis row
        row = 1
        x_label = QLabel("X:")
        x_label.setMaximumWidth(20)
        grid.addWidget(x_label, row, 0)

        self.x_target_spin = QDoubleSpinBox()
        self.x_target_spin.setRange(limits['x']['min'], limits['x']['max'])
        self.x_target_spin.setDecimals(3)
        self.x_target_spin.setSingleStep(0.1)
        self.x_target_spin.setSuffix(" mm")
        self.x_target_spin.setMaximumWidth(110)
        self.x_target_spin.valueChanged.connect(lambda: self._update_position_label_colors())
        grid.addWidget(self.x_target_spin, row, 1)

        self.x_goto_btn = QPushButton("Go X")
        self.x_goto_btn.clicked.connect(lambda: self._on_goto_clicked('x'))
        self.x_goto_btn.setStyleSheet("background-color: #2196f3; color: white; padding: 5px; font-weight: bold; font-size: 9pt;")
        self.x_goto_btn.setMaximumWidth(60)
        grid.addWidget(self.x_goto_btn, row, 2)

        self.x_home_btn = QPushButton("Go to X Home")
        self.x_home_btn.clicked.connect(lambda: self._on_home_axis_clicked('x'))
        self.x_home_btn.setStyleSheet("background-color: #ff9800; color: white; padding: 5px; font-size: 9pt;")
        grid.addWidget(self.x_home_btn, row, 3)

        # Y-axis row
        row = 2
        y_label = QLabel("Y:")
        y_label.setMaximumWidth(20)
        grid.addWidget(y_label, row, 0)

        self.y_target_spin = QDoubleSpinBox()
        self.y_target_spin.setRange(limits['y']['min'], limits['y']['max'])
        self.y_target_spin.setDecimals(3)
        self.y_target_spin.setSingleStep(0.1)
        self.y_target_spin.setSuffix(" mm")
        self.y_target_spin.setMaximumWidth(110)
        self.y_target_spin.valueChanged.connect(lambda: self._update_position_label_colors())
        grid.addWidget(self.y_target_spin, row, 1)

        self.y_goto_btn = QPushButton("Go Y")
        self.y_goto_btn.clicked.connect(lambda: self._on_goto_clicked('y'))
        self.y_goto_btn.setStyleSheet("background-color: #2196f3; color: white; padding: 5px; font-weight: bold; font-size: 9pt;")
        self.y_goto_btn.setMaximumWidth(60)
        grid.addWidget(self.y_goto_btn, row, 2)

        self.y_home_btn = QPushButton("Go to Y Home")
        self.y_home_btn.clicked.connect(lambda: self._on_home_axis_clicked('y'))
        self.y_home_btn.setStyleSheet("background-color: #ff9800; color: white; padding: 5px; font-size: 9pt;")
        grid.addWidget(self.y_home_btn, row, 3)

        # Z-axis row
        row = 3
        z_label = QLabel("Z:")
        z_label.setMaximumWidth(20)
        grid.addWidget(z_label, row, 0)

        self.z_target_spin = QDoubleSpinBox()
        self.z_target_spin.setRange(limits['z']['min'], limits['z']['max'])
        self.z_target_spin.setDecimals(3)
        self.z_target_spin.setSingleStep(0.1)
        self.z_target_spin.setSuffix(" mm")
        self.z_target_spin.setMaximumWidth(110)
        self.z_target_spin.valueChanged.connect(lambda: self._update_position_label_colors())
        grid.addWidget(self.z_target_spin, row, 1)

        self.z_goto_btn = QPushButton("Go Z")
        self.z_goto_btn.clicked.connect(lambda: self._on_goto_clicked('z'))
        self.z_goto_btn.setStyleSheet("background-color: #2196f3; color: white; padding: 5px; font-weight: bold; font-size: 9pt;")
        self.z_goto_btn.setMaximumWidth(60)
        grid.addWidget(self.z_goto_btn, row, 2)

        self.z_home_btn = QPushButton("Go to Z Home")
        self.z_home_btn.clicked.connect(lambda: self._on_home_axis_clicked('z'))
        self.z_home_btn.setStyleSheet("background-color: #ff9800; color: white; padding: 5px; font-size: 9pt;")
        grid.addWidget(self.z_home_btn, row, 3)

        # R-axis row
        row = 4
        r_label = QLabel("R:")
        r_label.setMaximumWidth(20)
        grid.addWidget(r_label, row, 0)

        self.r_target_spin = QDoubleSpinBox()
        self.r_target_spin.setRange(limits['r']['min'], limits['r']['max'])
        self.r_target_spin.setDecimals(2)
        self.r_target_spin.setSingleStep(1.0)
        self.r_target_spin.setSuffix("°")
        self.r_target_spin.setMaximumWidth(110)
        self.r_target_spin.valueChanged.connect(lambda: self._update_position_label_colors())
        grid.addWidget(self.r_target_spin, row, 1)

        self.r_goto_btn = QPushButton("Go R")
        self.r_goto_btn.clicked.connect(lambda: self._on_goto_clicked('r'))
        self.r_goto_btn.setStyleSheet("background-color: #2196f3; color: white; padding: 5px; font-weight: bold; font-size: 9pt;")
        self.r_goto_btn.setMaximumWidth(60)
        grid.addWidget(self.r_goto_btn, row, 2)

        self.r_home_btn = QPushButton("Go to R Home")
        self.r_home_btn.clicked.connect(lambda: self._on_home_axis_clicked('r'))
        self.r_home_btn.setStyleSheet("background-color: #ff9800; color: white; padding: 5px; font-size: 9pt;")
        grid.addWidget(self.r_home_btn, row, 3)

        layout.addLayout(grid)

        # Add separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        # Add "Go To Position" button that moves all 4 axes at once
        self.goto_position_btn = QPushButton("Go To Position (All 4 Axes)")
        self.goto_position_btn.clicked.connect(self._on_goto_position_clicked)
        self.goto_position_btn.setStyleSheet(
            "background-color: #4caf50; color: white; padding: 8px; "
            "font-weight: bold; font-size: 10pt; border-radius: 4px;"
        )
        layout.addWidget(self.goto_position_btn)

        group.setLayout(layout)
        group.setMaximumWidth(400)
        group.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        return group

    def _create_relative_controls(self) -> QGroupBox:
        """Create relative movement (jog) controls with dropdown increment selector."""
        group = QGroupBox("Jog Controls")
        layout = QVBoxLayout()
        layout.setSpacing(8)

        # Increment selector dropdown
        increment_layout = QHBoxLayout()
        increment_layout.addWidget(QLabel("Increment:"))

        self.jog_increment_combo = QComboBox()
        self.jog_increment_combo.addItems(["0.1 mm", "1.0 mm", "10.0 mm"])
        self.jog_increment_combo.setCurrentIndex(1)  # Default to 1.0 mm
        self.jog_increment_combo.setStyleSheet("padding: 4px; font-weight: bold;")
        increment_layout.addWidget(self.jog_increment_combo)
        increment_layout.addStretch()
        layout.addLayout(increment_layout)

        # Create compact grid for jog buttons (just - and + for each axis)
        grid = QGridLayout()
        grid.setSpacing(4)

        # Headers
        grid.addWidget(QLabel("<b>Axis</b>"), 0, 0)
        grid.addWidget(QLabel("<b>−</b>"), 0, 1, Qt.AlignCenter)
        grid.addWidget(QLabel("<b>+</b>"), 0, 2, Qt.AlignCenter)

        # X-axis jog buttons
        row = 1
        grid.addWidget(QLabel("X (mm):"), row, 0)
        grid.addWidget(self._create_jog_button("−", lambda: self._jog_with_increment('x', -1)), row, 1)
        grid.addWidget(self._create_jog_button("+", lambda: self._jog_with_increment('x', 1)), row, 2)

        # Y-axis jog buttons
        row = 2
        grid.addWidget(QLabel("Y (mm):"), row, 0)
        grid.addWidget(self._create_jog_button("−", lambda: self._jog_with_increment('y', -1)), row, 1)
        grid.addWidget(self._create_jog_button("+", lambda: self._jog_with_increment('y', 1)), row, 2)

        # Z-axis jog buttons
        row = 3
        grid.addWidget(QLabel("Z (mm):"), row, 0)
        grid.addWidget(self._create_jog_button("−", lambda: self._jog_with_increment('z', -1)), row, 1)
        grid.addWidget(self._create_jog_button("+", lambda: self._jog_with_increment('z', 1)), row, 2)

        # R-axis jog buttons (uses different increments: 1°, 10°, 45°)
        row = 4
        grid.addWidget(QLabel("R (°):"), row, 0)
        grid.addWidget(self._create_jog_button("−", lambda: self._jog_with_rotation_increment(-1)), row, 1)
        grid.addWidget(self._create_jog_button("+", lambda: self._jog_with_rotation_increment(1)), row, 2)

        layout.addLayout(grid)

        # Show Position History button
        self.show_history_btn = QPushButton("Show Position History")
        self.show_history_btn.clicked.connect(self._on_show_history_clicked)
        self.show_history_btn.setStyleSheet(
            "background-color: #2196f3; color: white; padding: 8px; "
            "font-weight: bold; font-size: 9pt;"
        )
        layout.addWidget(self.show_history_btn)

        group.setLayout(layout)
        group.setMaximumWidth(220)
        group.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        return group

    def _create_jog_button(self, text: str, callback) -> QPushButton:
        """Create a compact jog button with consistent styling."""
        btn = QPushButton(text)
        btn.setMinimumWidth(50)
        btn.setMaximumWidth(50)
        btn.clicked.connect(callback)

        # Style based on direction
        if text.startswith('−') or text.startswith('-') or text == '−':
            btn.setStyleSheet("background-color: #ffccbc; padding: 6px; font-weight: bold; font-size: 11pt;")
        else:
            btn.setStyleSheet("background-color: #c8e6c9; padding: 6px; font-weight: bold; font-size: 11pt;")

        return btn

    def _create_safety_controls(self) -> QGroupBox:
        """Create home all and emergency stop controls."""
        group = QGroupBox("Safety Controls")
        layout = QHBoxLayout()

        # Home All button
        self.home_all_btn = QPushButton("🏠 Home All Axes")
        self.home_all_btn.clicked.connect(self._on_home_all_clicked)
        self.home_all_btn.setStyleSheet(
            "background-color: #4caf50; color: white; padding: 10px; "
            "font-weight: bold; font-size: 10pt; border-radius: 4px;"
        )
        layout.addWidget(self.home_all_btn)

        # Set Home Position button
        self.set_home_btn = QPushButton("📍 Set Home Position")
        self.set_home_btn.clicked.connect(self._on_set_home_clicked)
        self.set_home_btn.setStyleSheet(
            "background-color: #2196f3; color: white; padding: 10px; "
            "font-weight: bold; font-size: 10pt; border-radius: 4px;"
        )
        layout.addWidget(self.set_home_btn)

        # Emergency Stop button
        self.estop_btn = QPushButton("🛑 EMERGENCY STOP")
        self.estop_btn.clicked.connect(self._on_emergency_stop_clicked)
        self.estop_btn.setStyleSheet(
            "background-color: #f44336; color: white; padding: 12px; "
            "font-weight: bold; font-size: 11pt; border-radius: 6px; border: 3px solid #b71c1c;"
        )
        layout.addWidget(self.estop_btn)

        group.setLayout(layout)
        group.setMaximumWidth(600)
        group.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        return group

    def _create_preset_controls(self) -> QGroupBox:
        """Create saved position preset controls."""
        group = QGroupBox("Saved Position Presets")
        layout = QVBoxLayout()

        # Preset list
        self.preset_list = QListWidget()
        self.preset_list.setMaximumHeight(100)
        layout.addWidget(QLabel("Saved Positions:"))
        layout.addWidget(self.preset_list)

        # Preset action buttons
        preset_button_layout = QHBoxLayout()

        self.save_preset_btn = QPushButton("Save Current")
        self.save_preset_btn.clicked.connect(self._on_save_preset_clicked)
        self.save_preset_btn.setStyleSheet("background-color: #2196f3; color: white; padding: 6px; font-size: 9pt;")
        preset_button_layout.addWidget(self.save_preset_btn)

        self.goto_preset_btn = QPushButton("Go To")
        self.goto_preset_btn.clicked.connect(self._on_goto_preset_clicked)
        self.goto_preset_btn.setStyleSheet("background-color: #4caf50; color: white; padding: 6px; font-size: 9pt;")
        self.goto_preset_btn.setEnabled(False)
        preset_button_layout.addWidget(self.goto_preset_btn)

        self.delete_preset_btn = QPushButton("Delete")
        self.delete_preset_btn.clicked.connect(self._on_delete_preset_clicked)
        self.delete_preset_btn.setStyleSheet("background-color: #f44336; color: white; padding: 6px; font-size: 9pt;")
        self.delete_preset_btn.setEnabled(False)
        preset_button_layout.addWidget(self.delete_preset_btn)

        layout.addLayout(preset_button_layout)

        # Enable/disable goto and delete based on selection
        self.preset_list.itemSelectionChanged.connect(self._on_preset_selection_changed)

        group.setLayout(layout)
        group.setMaximumWidth(500)
        group.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
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
        group.setMaximumWidth(600)
        group.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
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
        import logging
        logger = logging.getLogger(__name__)
        logger.info("Go To Position button clicked")

        try:
            # Read all target values from spin boxes
            target_x = self.x_target_spin.value()
            target_y = self.y_target_spin.value()
            target_z = self.z_target_spin.value()
            target_r = self.r_target_spin.value()

            logger.info(f"Target position: X={target_x:.3f}, Y={target_y:.3f}, Z={target_z:.3f}, R={target_r:.2f}")

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
                logger.info("User confirmed Go To Position movement")

                # Create Position object
                target_position = Position(
                    x=target_x,
                    y=target_y,
                    z=target_z,
                    r=target_r
                )

                # Move to position with validation
                logger.info("Calling move_to_position...")
                self.movement_controller.position_controller.move_to_position(
                    target_position,
                    validate=True
                )
                logger.info("move_to_position call completed")

                self.message_label.setText("Moving to target position...")
                self.message_label.setStyleSheet("color: blue; padding: 6px;")
            else:
                logger.info("User cancelled Go To Position movement")

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

    def _jog_with_increment(self, axis: str, direction: int) -> None:
        """Handle jog with increment from dropdown."""
        # Get increment from dropdown (e.g., "1.0 mm" -> 1.0)
        increment_text = self.jog_increment_combo.currentText()
        increment = float(increment_text.split()[0])  # Extract number from "1.0 mm"
        delta = increment * direction
        self._jog(axis, delta)

    def _jog_with_rotation_increment(self, direction: int) -> None:
        """Handle rotation jog with special increments mapped from dropdown."""
        # Map dropdown index to rotation increments
        increment_index = self.jog_increment_combo.currentIndex()
        rotation_increments = [1.0, 10.0, 45.0]  # Maps to 0.1mm, 1mm, 10mm
        increment = rotation_increments[increment_index]
        delta = increment * direction
        self._jog('r', delta)

    def _on_save_preset_clicked(self) -> None:
        """Handle save preset button click."""
        try:
            # Get current position
            position = self.movement_controller.position_controller.get_current_position()
            if position is None:
                QMessageBox.warning(self, "No Position", "No current position available to save")
                return

            # Ask user for preset name
            name, ok = QInputDialog.getText(
                self,
                "Save Position Preset",
                "Enter name for this position:",
                QLineEdit.Normal,
                ""
            )

            if ok and name:
                name = name.strip()
                if not name:
                    QMessageBox.warning(self, "Invalid Name", "Preset name cannot be empty")
                    return

                # Check if preset already exists
                if self.movement_controller.position_controller.preset_service.preset_exists(name):
                    reply = QMessageBox.question(
                        self,
                        "Overwrite Preset",
                        f"Preset '{name}' already exists. Overwrite?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No
                    )
                    if reply != QMessageBox.Yes:
                        return

                # Save preset
                self.movement_controller.position_controller.preset_service.save_preset(name, position)
                self.message_label.setText(f"Saved preset '{name}'")
                self.message_label.setStyleSheet("color: green; padding: 6px;")
                self._refresh_preset_list()

        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save preset: {str(e)}")

    def _on_goto_preset_clicked(self) -> None:
        """Handle go to preset button click."""
        try:
            # Get selected preset
            selected_items = self.preset_list.selectedItems()
            if not selected_items:
                QMessageBox.warning(self, "No Selection", "No preset selected")
                return

            preset_name = selected_items[0].text()
            preset = self.movement_controller.position_controller.preset_service.get_preset(preset_name)

            if preset is None:
                QMessageBox.warning(self, "Not Found", f"Preset '{preset_name}' not found")
                return

            # Move to preset position
            position = preset.to_position()
            self.movement_controller.position_controller.move_to_position(position, validate=True)
            self.message_label.setText(f"Moving to preset '{preset_name}'...")
            self.message_label.setStyleSheet("color: blue; padding: 6px;")

        except Exception as e:
            QMessageBox.critical(self, "Movement Error", f"Failed to move to preset: {str(e)}")

    def _on_delete_preset_clicked(self) -> None:
        """Handle delete preset button click."""
        try:
            # Get selected preset
            selected_items = self.preset_list.selectedItems()
            if not selected_items:
                QMessageBox.warning(self, "No Selection", "No preset selected")
                return

            preset_name = selected_items[0].text()

            # Confirm deletion
            reply = QMessageBox.question(
                self,
                "Delete Preset",
                f"Delete preset '{preset_name}'?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply == QMessageBox.Yes:
                self.movement_controller.position_controller.preset_service.delete_preset(preset_name)
                self.message_label.setText(f"Deleted preset '{preset_name}'")
                self.message_label.setStyleSheet("color: gray; padding: 6px;")
                self._refresh_preset_list()

        except Exception as e:
            QMessageBox.critical(self, "Delete Error", f"Failed to delete preset: {str(e)}")

    def _on_preset_selection_changed(self) -> None:
        """Handle preset list selection change."""
        has_selection = len(self.preset_list.selectedItems()) > 0
        self.goto_preset_btn.setEnabled(has_selection)
        self.delete_preset_btn.setEnabled(has_selection)

    def _refresh_preset_list(self) -> None:
        """Refresh the preset list display."""
        self.preset_list.clear()
        presets = self.movement_controller.position_controller.preset_service.list_presets()
        for preset in presets:
            self.preset_list.addItem(preset.name)

    def _on_set_home_clicked(self) -> None:
        """Handle Set Home Position button click."""
        try:
            # Get current position
            current_pos = self.movement_controller.position_controller.get_current_position()
            if current_pos is None:
                QMessageBox.warning(self, "No Position", "Cannot get current position")
                return

            # Get stage limits
            stage_limits = self.movement_controller.get_stage_limits()

            # Show dialog
            dialog = SetHomePositionDialog(current_pos, stage_limits, self)
            if dialog.exec_() == QDialog.Accepted:
                new_home = dialog.get_position()

                # Save home position
                if self.movement_controller.position_controller.set_home_position(new_home):
                    QMessageBox.information(self, "Success", "Home position updated")
                    self.message_label.setText("Home position updated")
                    self.message_label.setStyleSheet("color: green; padding: 6px;")
                else:
                    QMessageBox.critical(self, "Error", "Failed to save home position")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to set home position: {str(e)}")

    def _on_show_history_clicked(self) -> None:
        """Handle Show Position History button click."""
        from py2flamingo.views.position_history_dialog import PositionHistoryDialog

        # Create and show the position history dialog
        dialog = PositionHistoryDialog(self.movement_controller, parent=self)
        dialog.exec_()  # Modal dialog

        # NOTE: Future enhancement - consider adding a quick "Undo" button
        # to return to previous position without opening the full history dialog.
        # This would be similar to a simple back button functionality.

    # ============================================================================
    # Signal Slots
    # ============================================================================

    def _update_position_label_colors(self) -> None:
        """Update position label colors based on whether current differs from target."""
        # Green style: position matches target
        green_style = "background-color: #e8f5e9; padding: 8px; border: 2px solid #4caf50; border-radius: 4px; font-size: 11pt; font-weight: bold;"
        # Orange style: position differs from target
        orange_style = "background-color: #fff3e0; padding: 8px; border: 2px solid #ff9800; border-radius: 4px; font-size: 11pt; font-weight: bold;"

        tolerance = 0.001  # 1 micron

        # Check X axis
        current_x = float(self.x_pos_label.text().replace(" mm", ""))
        target_x = self.x_target_spin.value()
        if abs(current_x - target_x) > tolerance:
            self.x_pos_label.setStyleSheet(orange_style)
        else:
            self.x_pos_label.setStyleSheet(green_style)

        # Check Y axis
        current_y = float(self.y_pos_label.text().replace(" mm", ""))
        target_y = self.y_target_spin.value()
        if abs(current_y - target_y) > tolerance:
            self.y_pos_label.setStyleSheet(orange_style)
        else:
            self.y_pos_label.setStyleSheet(green_style)

        # Check Z axis
        current_z = float(self.z_pos_label.text().replace(" mm", ""))
        target_z = self.z_target_spin.value()
        if abs(current_z - target_z) > tolerance:
            self.z_pos_label.setStyleSheet(orange_style)
        else:
            self.z_pos_label.setStyleSheet(green_style)

        # Check Rotation (larger tolerance)
        current_r = float(self.r_pos_label.text().replace("°", ""))
        target_r = self.r_target_spin.value()
        if abs(current_r - target_r) > 0.01:  # 0.01 degree tolerance
            self.r_pos_label.setStyleSheet(orange_style)
        else:
            self.r_pos_label.setStyleSheet(green_style)

    @pyqtSlot(float, float, float, float)
    def _on_position_changed(self, x: float, y: float, z: float, r: float) -> None:
        """Update position display and target fields when position changes."""
        # Update current position display
        self.x_pos_label.setText(f"{x:.3f} mm")
        self.y_pos_label.setText(f"{y:.3f} mm")
        self.z_pos_label.setText(f"{z:.3f} mm")
        self.r_pos_label.setText(f"{r:.2f}°")

        # Update label colors based on target difference
        self._update_position_label_colors()

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
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[StageControlView] Received motion_stopped signal for: {axis_name}")

        self.motion_status_label.setText(f"{axis_name} motion complete - Ready")
        self.motion_status_label.setStyleSheet(
            "background-color: #e8f5e9; color: #2e7d32; padding: 8px; "
            "border: 2px solid #4caf50; font-weight: bold; font-size: 10pt;"
        )

        # Re-enable controls
        logger.info("[StageControlView] Re-enabling controls")
        self._set_controls_enabled(True)
        logger.info("[StageControlView] Controls re-enabled")

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
        self.set_home_btn.setEnabled(enabled)

        # Preset buttons
        self.save_preset_btn.setEnabled(enabled)
        # goto_preset_btn and delete_preset_btn are managed by selection state

        # Jog controls
        # Note: Individual jog buttons are in a grid, we enable/disable via button references
        # stored during creation - they follow the general enabled state

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

        # Load saved position presets
        try:
            self._refresh_preset_list()
        except Exception as e:
            self.logger.error(f"Error loading preset list: {e}")

    def closeEvent(self, event) -> None:
        """Handle widget close event."""
        # Stop position monitoring
        self.movement_controller.stop_position_monitoring()
        event.accept()
