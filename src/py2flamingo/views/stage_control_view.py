"""
Stage control view for microscope positioning.

This module provides the StageControlView widget for controlling
stage position (X, Y, Z, Rotation).
"""

import logging
from typing import Optional
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QGroupBox, QFormLayout,
    QListWidget, QListWidgetItem, QComboBox, QInputDialog,
    QScrollArea
)
from PyQt5.QtCore import Qt


class StageControlView(QWidget):
    """UI view for controlling microscope stage position.

    This widget provides UI components for:
    - Displaying current stage position (X, Y, Z, R)
    - Rotation control (safest axis - no chamber collision risk)
    - Movement status display
    - Future: X, Y, Z axis controls with bounds checking

    The view is dumb - all logic is handled by the controller.
    """

    def __init__(self, controller):
        """Initialize stage control view with controller.

        Args:
            controller: PositionController for handling movement logic
        """
        super().__init__()
        self._controller = controller
        self._logger = logging.getLogger(__name__)
        self._logger.info("StageControlView initialized")

        # Register motion complete callback
        self._controller.set_motion_complete_callback(self._on_motion_complete)

        self.setup_ui()

        # Load saved presets into list
        self._refresh_preset_list()

    def setup_ui(self) -> None:
        """Create and layout UI components."""
        # Create a container widget for all controls
        container_widget = QWidget()
        container_layout = QVBoxLayout(container_widget)

        # Current Position Display
        position_group = self._create_position_display()
        container_layout.addWidget(position_group)

        # Rotation Control Section
        rotation_group = self._create_rotation_control()
        container_layout.addWidget(rotation_group)

        # X, Y, Z Axis Controls
        xyz_group = self._create_xyz_controls()
        container_layout.addWidget(xyz_group)

        # Jog Controls
        jog_group = self._create_jog_controls()
        container_layout.addWidget(jog_group)

        # Saved Presets
        preset_group = self._create_preset_controls()
        container_layout.addWidget(preset_group)

        # Undo Control
        undo_layout = self._create_undo_control()
        container_layout.addLayout(undo_layout)

        # Home and Emergency Stop Controls
        safety_layout = self._create_safety_controls()
        container_layout.addLayout(safety_layout)

        # Status Display
        self.status_label = QLabel("Status: Ready")
        self.status_label.setStyleSheet("color: gray; font-weight: bold;")
        container_layout.addWidget(self.status_label)

        # Message Display
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        self.message_label.setMinimumHeight(40)
        container_layout.addWidget(self.message_label)

        # Add stretch to push everything to top
        container_layout.addStretch()

        # Create scroll area and add container widget
        scroll_area = QScrollArea()
        scroll_area.setWidget(container_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # Set scroll area as main layout
        main_layout = QVBoxLayout()
        main_layout.addWidget(scroll_area)
        self.setLayout(main_layout)

    def _create_position_display(self) -> QGroupBox:
        """Create current position display group.

        Returns:
            QGroupBox containing current position labels
        """
        group = QGroupBox("Current Position")
        layout = QFormLayout()

        # Position labels (read-only display)
        self.x_label = QLabel("Unknown")
        self.y_label = QLabel("Unknown")
        self.z_label = QLabel("Unknown")
        self.r_label = QLabel("Unknown")

        # Style the labels
        label_style = "background-color: lavender; padding: 5px; border: 1px solid gray;"
        self.x_label.setStyleSheet(label_style)
        self.y_label.setStyleSheet(label_style)
        self.z_label.setStyleSheet(label_style)
        self.r_label.setStyleSheet(label_style)

        layout.addRow("X (mm):", self.x_label)
        layout.addRow("Y (mm):", self.y_label)
        layout.addRow("Z (mm):", self.z_label)
        layout.addRow("R (degrees):", self.r_label)

        group.setLayout(layout)
        return group

    def _create_rotation_control(self) -> QGroupBox:
        """Create rotation control group.

        Returns:
            QGroupBox containing rotation control widgets
        """
        group = QGroupBox("Rotation Control (Safest Axis)")
        layout = QVBoxLayout()

        # Input form
        form_layout = QFormLayout()

        self.rotation_input = QLineEdit()
        self.rotation_input.setPlaceholderText("Enter rotation in degrees (0-360)")
        form_layout.addRow("Target Rotation (°):", self.rotation_input)

        layout.addLayout(form_layout)

        # Move button
        button_layout = QHBoxLayout()
        self.move_rotation_btn = QPushButton("Move to Rotation")
        self.move_rotation_btn.clicked.connect(self._on_move_rotation_clicked)
        self.move_rotation_btn.setEnabled(False)  # Disabled until connected
        button_layout.addWidget(self.move_rotation_btn)

        layout.addLayout(button_layout)

        # Info label
        info_label = QLabel(
            "Note: Rotation is the safest axis as the stage won't hit chamber edges.\n"
            "Movement is asynchronous - status will update when complete."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; font-style: italic; font-size: 9pt;")
        layout.addWidget(info_label)

        group.setLayout(layout)
        return group

    def _create_xyz_controls(self) -> QGroupBox:
        """Create X, Y, Z axis control group.

        Returns:
            QGroupBox containing X, Y, Z axis control widgets
        """
        group = QGroupBox("X, Y, Z Axis Control")
        layout = QVBoxLayout()

        # Get stage limits from controller
        try:
            limits = self._controller.get_stage_limits()
        except Exception as e:
            self._logger.warning(f"Could not load stage limits: {e}")
            limits = {
                'x': {'min': 0.0, 'max': 26.0},
                'y': {'min': 0.0, 'max': 26.0},
                'z': {'min': 0.0, 'max': 26.0}
            }

        # Input form
        form_layout = QFormLayout()

        # Store limits for validation
        self._stage_limits = limits

        # X axis input
        x_layout = QHBoxLayout()
        self.x_input = QLineEdit()
        self.x_input.setPlaceholderText(f"{limits['x']['min']:.1f} - {limits['x']['max']:.1f}")
        self.x_input.textChanged.connect(lambda: self._validate_input_bounds('x'))
        x_layout.addWidget(self.x_input)
        self.x_limits_label = QLabel(f"[{limits['x']['min']:.1f}, {limits['x']['max']:.1f}] mm")
        self.x_limits_label.setStyleSheet("color: #666; font-size: 9pt;")
        x_layout.addWidget(self.x_limits_label)
        form_layout.addRow("X Position (mm):", x_layout)

        # Y axis input
        y_layout = QHBoxLayout()
        self.y_input = QLineEdit()
        self.y_input.setPlaceholderText(f"{limits['y']['min']:.1f} - {limits['y']['max']:.1f}")
        self.y_input.textChanged.connect(lambda: self._validate_input_bounds('y'))
        y_layout.addWidget(self.y_input)
        self.y_limits_label = QLabel(f"[{limits['y']['min']:.1f}, {limits['y']['max']:.1f}] mm")
        self.y_limits_label.setStyleSheet("color: #666; font-size: 9pt;")
        y_layout.addWidget(self.y_limits_label)
        form_layout.addRow("Y Position (mm):", y_layout)

        # Z axis input
        z_layout = QHBoxLayout()
        self.z_input = QLineEdit()
        self.z_input.setPlaceholderText(f"{limits['z']['min']:.1f} - {limits['z']['max']:.1f}")
        self.z_input.textChanged.connect(lambda: self._validate_input_bounds('z'))
        z_layout.addWidget(self.z_input)
        self.z_limits_label = QLabel(f"[{limits['z']['min']:.1f}, {limits['z']['max']:.1f}] mm")
        self.z_limits_label.setStyleSheet("color: #666; font-size: 9pt;")
        z_layout.addWidget(self.z_limits_label)
        form_layout.addRow("Z Position (mm):", z_layout)

        layout.addLayout(form_layout)

        # Move buttons in a horizontal layout
        button_layout = QHBoxLayout()

        self.move_x_btn = QPushButton("Move X")
        self.move_x_btn.clicked.connect(self._on_move_x_clicked)
        self.move_x_btn.setEnabled(False)
        button_layout.addWidget(self.move_x_btn)

        self.move_y_btn = QPushButton("Move Y")
        self.move_y_btn.clicked.connect(self._on_move_y_clicked)
        self.move_y_btn.setEnabled(False)
        button_layout.addWidget(self.move_y_btn)

        self.move_z_btn = QPushButton("Move Z")
        self.move_z_btn.clicked.connect(self._on_move_z_clicked)
        self.move_z_btn.setEnabled(False)
        button_layout.addWidget(self.move_z_btn)

        layout.addLayout(button_layout)

        # Warning label
        warning_label = QLabel(
            "⚠️  WARNING: X, Y, Z movements can cause collisions!\n"
            "Ensure stage position is safe before moving.\n"
            "Rotation is the safest axis to move first."
        )
        warning_label.setWordWrap(True)
        warning_label.setStyleSheet(
            "color: #cc6600; font-style: italic; font-size: 9pt; "
            "background-color: #fff3cd; padding: 8px; border: 1px solid #cc6600; border-radius: 4px;"
        )
        layout.addWidget(warning_label)

        group.setLayout(layout)
        return group

    def _create_jog_controls(self) -> QGroupBox:
        """Create incremental jog control group.

        Returns:
            QGroupBox containing jog control widgets
        """
        group = QGroupBox("Jog Controls (Incremental Movement)")
        layout = QVBoxLayout()

        # Step size selector
        step_layout = QHBoxLayout()
        step_layout.addWidget(QLabel("Step Size:"))

        self.jog_step_combo = QComboBox()
        self.jog_step_combo.addItems([
            "0.01 mm / 1°",
            "0.1 mm / 10°",
            "1.0 mm / 45°",
            "10.0 mm / 90°"
        ])
        self.jog_step_combo.setCurrentIndex(1)  # Default to 0.1mm / 10°
        step_layout.addWidget(self.jog_step_combo)
        step_layout.addStretch()
        layout.addLayout(step_layout)

        # Create jog buttons for each axis
        # X axis jog
        x_jog_layout = QHBoxLayout()
        x_jog_layout.addWidget(QLabel("X:"))
        self.jog_x_minus = QPushButton("-")
        self.jog_x_minus.setMaximumWidth(40)
        self.jog_x_minus.clicked.connect(lambda: self._on_jog_clicked('x', -1))
        self.jog_x_minus.setEnabled(False)
        x_jog_layout.addWidget(self.jog_x_minus)

        self.jog_x_plus = QPushButton("+")
        self.jog_x_plus.setMaximumWidth(40)
        self.jog_x_plus.clicked.connect(lambda: self._on_jog_clicked('x', 1))
        self.jog_x_plus.setEnabled(False)
        x_jog_layout.addWidget(self.jog_x_plus)
        x_jog_layout.addStretch()
        layout.addLayout(x_jog_layout)

        # Y axis jog
        y_jog_layout = QHBoxLayout()
        y_jog_layout.addWidget(QLabel("Y:"))
        self.jog_y_minus = QPushButton("-")
        self.jog_y_minus.setMaximumWidth(40)
        self.jog_y_minus.clicked.connect(lambda: self._on_jog_clicked('y', -1))
        self.jog_y_minus.setEnabled(False)
        y_jog_layout.addWidget(self.jog_y_minus)

        self.jog_y_plus = QPushButton("+")
        self.jog_y_plus.setMaximumWidth(40)
        self.jog_y_plus.clicked.connect(lambda: self._on_jog_clicked('y', 1))
        self.jog_y_plus.setEnabled(False)
        y_jog_layout.addWidget(self.jog_y_plus)
        y_jog_layout.addStretch()
        layout.addLayout(y_jog_layout)

        # Z axis jog
        z_jog_layout = QHBoxLayout()
        z_jog_layout.addWidget(QLabel("Z:"))
        self.jog_z_minus = QPushButton("-")
        self.jog_z_minus.setMaximumWidth(40)
        self.jog_z_minus.clicked.connect(lambda: self._on_jog_clicked('z', -1))
        self.jog_z_minus.setEnabled(False)
        z_jog_layout.addWidget(self.jog_z_minus)

        self.jog_z_plus = QPushButton("+")
        self.jog_z_plus.setMaximumWidth(40)
        self.jog_z_plus.clicked.connect(lambda: self._on_jog_clicked('z', 1))
        self.jog_z_plus.setEnabled(False)
        z_jog_layout.addWidget(self.jog_z_plus)
        z_jog_layout.addStretch()
        layout.addLayout(z_jog_layout)

        # Rotation jog
        r_jog_layout = QHBoxLayout()
        r_jog_layout.addWidget(QLabel("R:"))
        self.jog_r_minus = QPushButton("-")
        self.jog_r_minus.setMaximumWidth(40)
        self.jog_r_minus.clicked.connect(lambda: self._on_jog_clicked('r', -1))
        self.jog_r_minus.setEnabled(False)
        r_jog_layout.addWidget(self.jog_r_minus)

        self.jog_r_plus = QPushButton("+")
        self.jog_r_plus.setMaximumWidth(40)
        self.jog_r_plus.clicked.connect(lambda: self._on_jog_clicked('r', 1))
        self.jog_r_plus.setEnabled(False)
        r_jog_layout.addWidget(self.jog_r_plus)
        r_jog_layout.addStretch()
        layout.addLayout(r_jog_layout)

        group.setLayout(layout)
        return group

    def _create_preset_controls(self) -> QGroupBox:
        """Create saved preset control group.

        Returns:
            QGroupBox containing preset management widgets
        """
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
        self.save_preset_btn.setEnabled(False)
        preset_button_layout.addWidget(self.save_preset_btn)

        self.goto_preset_btn = QPushButton("Go To")
        self.goto_preset_btn.clicked.connect(self._on_goto_preset_clicked)
        self.goto_preset_btn.setEnabled(False)
        preset_button_layout.addWidget(self.goto_preset_btn)

        self.delete_preset_btn = QPushButton("Delete")
        self.delete_preset_btn.clicked.connect(self._on_delete_preset_clicked)
        self.delete_preset_btn.setEnabled(False)
        preset_button_layout.addWidget(self.delete_preset_btn)

        layout.addLayout(preset_button_layout)

        # Enable/disable goto and delete based on selection
        self.preset_list.itemSelectionChanged.connect(self._on_preset_selection_changed)

        group.setLayout(layout)
        return group

    def _create_undo_control(self) -> QHBoxLayout:
        """Create undo control layout.

        Returns:
            QHBoxLayout with undo button
        """
        layout = QHBoxLayout()

        self.undo_btn = QPushButton("⟲ Undo (Return to Previous Position)")
        self.undo_btn.clicked.connect(self._on_undo_clicked)
        self.undo_btn.setEnabled(False)
        self.undo_btn.setStyleSheet(
            "background-color: #f8f9fa; border: 1px solid #6c757d; "
            "padding: 8px; font-weight: bold;"
        )
        layout.addWidget(self.undo_btn)

        return layout

    def _create_safety_controls(self) -> QHBoxLayout:
        """Create safety control layout (Home and Emergency Stop).

        Returns:
            QHBoxLayout with safety buttons
        """
        layout = QHBoxLayout()

        # Home button
        self.home_btn = QPushButton("🏠 Home (Return to Home Position)")
        self.home_btn.clicked.connect(self._on_home_clicked)
        self.home_btn.setEnabled(False)
        self.home_btn.setStyleSheet(
            "background-color: #e8f5e9; border: 2px solid #4caf50; "
            "padding: 8px; font-weight: bold; color: #2e7d32;"
        )
        layout.addWidget(self.home_btn)

        # Emergency Stop button
        self.emergency_stop_btn = QPushButton("🛑 EMERGENCY STOP")
        self.emergency_stop_btn.clicked.connect(self._on_emergency_stop_clicked)
        self.emergency_stop_btn.setEnabled(False)
        self.emergency_stop_btn.setStyleSheet(
            "background-color: #ffebee; border: 3px solid #f44336; "
            "padding: 10px; font-weight: bold; font-size: 11pt; color: #c62828;"
        )
        layout.addWidget(self.emergency_stop_btn)

        # Clear Emergency Stop button (initially hidden)
        self.clear_estop_btn = QPushButton("Clear Emergency Stop")
        self.clear_estop_btn.clicked.connect(self._on_clear_emergency_stop_clicked)
        self.clear_estop_btn.setVisible(False)
        self.clear_estop_btn.setStyleSheet(
            "background-color: #fff3cd; border: 2px solid #ff9800; "
            "padding: 8px; font-weight: bold; color: #e65100;"
        )
        layout.addWidget(self.clear_estop_btn)

        return layout

    def _on_move_rotation_clicked(self) -> None:
        """Handle move rotation button click.

        Validates input and delegates to controller.
        """
        try:
            rotation_str = self.rotation_input.text().strip()
            if not rotation_str:
                self.show_error("Please enter a rotation value")
                return

            rotation = float(rotation_str)

            # Basic validation
            if rotation < 0 or rotation > 360:
                self.show_error("Rotation must be between 0 and 360 degrees")
                return

            # Show moving status
            self.set_moving(True, "Rotation")
            self.clear_message()

            # Delegate to controller (sends command and waits for callback in background)
            self._controller.move_rotation(rotation)

            # Movement command sent successfully
            self.show_success(f"Moving to rotation {rotation:.2f}°...")
            self._logger.info(f"Movement command sent, waiting for motion complete callback...")

            # Position will be updated when motion complete callback fires
            # Controls will be re-enabled by _on_motion_complete()

        except ValueError as e:
            self.show_error(f"Invalid rotation value: {str(e)}")
            self.set_moving(False)
        except RuntimeError as e:
            self.show_error(str(e))
            self.set_moving(False)
        except Exception as e:
            self.show_error(f"Unexpected error: {str(e)}")
            self.set_moving(False)

    def _on_move_x_clicked(self) -> None:
        """Handle move X button click."""
        try:
            x_str = self.x_input.text().strip()
            if not x_str:
                self.show_error("Please enter an X position value")
                return

            x = float(x_str)

            # Show moving status
            self.set_moving(True, "X-axis")
            self.clear_message()

            # Delegate to controller (sends command and waits for callback in background)
            self._controller.move_x(x)

            # Movement command sent successfully
            self.show_success(f"Moving to X={x:.3f}mm...")
            self._logger.info(f"X movement command sent, waiting for motion complete callback...")

        except ValueError as e:
            # This catches both float conversion errors and bounds validation errors
            self.show_error(f"Invalid X position: {str(e)}")
            self.set_moving(False)
        except RuntimeError as e:
            self.show_error(str(e))
            self.set_moving(False)
        except Exception as e:
            self.show_error(f"Unexpected error: {str(e)}")
            self.set_moving(False)

    def _on_move_y_clicked(self) -> None:
        """Handle move Y button click."""
        try:
            y_str = self.y_input.text().strip()
            if not y_str:
                self.show_error("Please enter a Y position value")
                return

            y = float(y_str)

            # Show moving status
            self.set_moving(True, "Y-axis")
            self.clear_message()

            # Delegate to controller (sends command and waits for callback in background)
            self._controller.move_y(y)

            # Movement command sent successfully
            self.show_success(f"Moving to Y={y:.3f}mm...")
            self._logger.info(f"Y movement command sent, waiting for motion complete callback...")

        except ValueError as e:
            self.show_error(f"Invalid Y position: {str(e)}")
            self.set_moving(False)
        except RuntimeError as e:
            self.show_error(str(e))
            self.set_moving(False)
        except Exception as e:
            self.show_error(f"Unexpected error: {str(e)}")
            self.set_moving(False)

    def _on_move_z_clicked(self) -> None:
        """Handle move Z button click."""
        try:
            z_str = self.z_input.text().strip()
            if not z_str:
                self.show_error("Please enter a Z position value")
                return

            z = float(z_str)

            # Show moving status
            self.set_moving(True, "Z-axis")
            self.clear_message()

            # Delegate to controller (sends command and waits for callback in background)
            self._controller.move_z(z)

            # Movement command sent successfully
            self.show_success(f"Moving to Z={z:.3f}mm...")
            self._logger.info(f"Z movement command sent, waiting for motion complete callback...")

        except ValueError as e:
            self.show_error(f"Invalid Z position: {str(e)}")
            self.set_moving(False)
        except RuntimeError as e:
            self.show_error(str(e))
            self.set_moving(False)
        except Exception as e:
            self.show_error(f"Unexpected error: {str(e)}")
            self.set_moving(False)

    def _on_jog_clicked(self, axis: str, direction: int) -> None:
        """Handle jog button click.

        Args:
            axis: Axis to jog ('x', 'y', 'z', or 'r')
            direction: Direction (-1 for minus, +1 for plus)
        """
        try:
            # Get step size from combo box
            step_index = self.jog_step_combo.currentIndex()
            step_sizes_mm = [0.01, 0.1, 1.0, 10.0]
            step_sizes_deg = [1.0, 10.0, 45.0, 90.0]

            # Calculate step based on axis
            if axis == 'r':
                step = step_sizes_deg[step_index] * direction
            else:
                step = step_sizes_mm[step_index] * direction

            # Show moving status
            self.set_moving(True, f"{axis.upper()}-axis jog")
            self.clear_message()

            # Delegate to controller
            if axis == 'x':
                self._controller.jog_x(step)
            elif axis == 'y':
                self._controller.jog_y(step)
            elif axis == 'z':
                self._controller.jog_z(step)
            elif axis == 'r':
                self._controller.jog_rotation(step)

            sign = "+" if direction > 0 else ""
            self.show_success(f"Jogging {axis.upper()} by {sign}{step:.3f}...")

        except ValueError as e:
            self.show_error(f"Jog failed: {str(e)}")
            self.set_moving(False)
        except RuntimeError as e:
            self.show_error(str(e))
            self.set_moving(False)
        except Exception as e:
            self.show_error(f"Unexpected error: {str(e)}")
            self.set_moving(False)

    def _on_save_preset_clicked(self) -> None:
        """Handle save preset button click."""
        try:
            # Get current position
            position = self._controller.get_current_position()
            if position is None:
                self.show_error("No current position available to save")
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
                    self.show_error("Preset name cannot be empty")
                    return

                # Check if preset already exists
                if self._controller.preset_service.preset_exists(name):
                    from PyQt5.QtWidgets import QMessageBox
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
                self._controller.preset_service.save_preset(name, position)
                self.show_success(f"Saved preset '{name}'")
                self._refresh_preset_list()

        except Exception as e:
            self.show_error(f"Failed to save preset: {str(e)}")

    def _on_goto_preset_clicked(self) -> None:
        """Handle go to preset button click."""
        try:
            # Get selected preset
            selected_items = self.preset_list.selectedItems()
            if not selected_items:
                self.show_error("No preset selected")
                return

            preset_name = selected_items[0].text()
            preset = self._controller.preset_service.get_preset(preset_name)

            if preset is None:
                self.show_error(f"Preset '{preset_name}' not found")
                return

            # Show moving status
            self.set_moving(True, "to preset")
            self.clear_message()

            # Move to preset position
            position = preset.to_position()
            self._controller.move_to_position(position, validate=True)

            self.show_success(f"Moving to preset '{preset_name}'...")

        except ValueError as e:
            self.show_error(f"Invalid preset position: {str(e)}")
            self.set_moving(False)
        except RuntimeError as e:
            self.show_error(str(e))
            self.set_moving(False)
        except Exception as e:
            self.show_error(f"Unexpected error: {str(e)}")
            self.set_moving(False)

    def _on_delete_preset_clicked(self) -> None:
        """Handle delete preset button click."""
        try:
            # Get selected preset
            selected_items = self.preset_list.selectedItems()
            if not selected_items:
                self.show_error("No preset selected")
                return

            preset_name = selected_items[0].text()

            # Confirm deletion
            from PyQt5.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                "Delete Preset",
                f"Delete preset '{preset_name}'?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply == QMessageBox.Yes:
                self._controller.preset_service.delete_preset(preset_name)
                self.show_success(f"Deleted preset '{preset_name}'")
                self._refresh_preset_list()

        except Exception as e:
            self.show_error(f"Failed to delete preset: {str(e)}")

    def _on_preset_selection_changed(self) -> None:
        """Handle preset list selection change."""
        has_selection = len(self.preset_list.selectedItems()) > 0
        self.goto_preset_btn.setEnabled(has_selection and self._controller.connection.is_connected())
        self.delete_preset_btn.setEnabled(has_selection)

    def _on_undo_clicked(self) -> None:
        """Handle undo button click."""
        try:
            if not self._controller.has_position_history():
                self.show_info("No position history available")
                return

            # Show moving status
            self.set_moving(True, "to previous position")
            self.clear_message()

            # Undo to previous position
            previous = self._controller.undo_position()

            if previous:
                self.show_success(f"Returning to previous position...")
            else:
                self.show_info("No position history available")
                self.set_moving(False)

        except RuntimeError as e:
            self.show_error(str(e))
            self.set_moving(False)
        except Exception as e:
            self.show_error(f"Unexpected error: {str(e)}")
            self.set_moving(False)

    def _refresh_preset_list(self) -> None:
        """Refresh the preset list display."""
        self.preset_list.clear()
        presets = self._controller.preset_service.list_presets()
        for preset in presets:
            self.preset_list.addItem(preset.name)

    def _update_undo_button_state(self) -> None:
        """Update undo button enabled state based on history."""
        has_history = self._controller.has_position_history()
        is_connected = self._controller.connection.is_connected()
        self.undo_btn.setEnabled(has_history and is_connected)

    def _on_home_clicked(self) -> None:
        """Handle home button click."""
        try:
            # Check if home position is available
            home_pos = self._controller.get_home_position()
            if home_pos is None:
                self.show_error("Home position not available in settings")
                return

            # Confirm home operation
            from PyQt5.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                "Return to Home",
                f"Move to home position?\n\n"
                f"X: {home_pos.x:.3f} mm\n"
                f"Y: {home_pos.y:.3f} mm\n"
                f"Z: {home_pos.z:.3f} mm\n"
                f"R: {home_pos.r:.2f}°",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )

            if reply != QMessageBox.Yes:
                return

            # Show moving status
            self.set_moving(True, "to home position")
            self.clear_message()

            # Move to home
            self._controller.go_home()

            self.show_success(f"Moving to home position...")

        except RuntimeError as e:
            self.show_error(str(e))
            self.set_moving(False)
        except Exception as e:
            self.show_error(f"Unexpected error: {str(e)}")
            self.set_moving(False)

    def _on_emergency_stop_clicked(self) -> None:
        """Handle emergency stop button click."""
        try:
            self._logger.warning("Emergency stop activated by user")

            # Activate emergency stop
            self._controller.emergency_stop()

            # Update UI
            self.show_error("⚠️ EMERGENCY STOP ACTIVATED - All movements halted")
            self.status_label.setText("Status: EMERGENCY STOPPED")
            self.status_label.setStyleSheet("color: red; font-weight: bold; font-size: 12pt;")

            # Disable all movement controls
            self._set_emergency_stop_ui(True)

        except Exception as e:
            self.show_error(f"Error during emergency stop: {str(e)}")

    def _on_clear_emergency_stop_clicked(self) -> None:
        """Handle clear emergency stop button click."""
        try:
            # Confirm clearing emergency stop
            from PyQt5.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                "Clear Emergency Stop",
                "Clear emergency stop and allow movements to resume?\n\n"
                "WARNING: Stage position may be uncertain after emergency stop.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply != QMessageBox.Yes:
                return

            # Clear emergency stop
            self._controller.clear_emergency_stop()

            # Update UI
            self.show_success("Emergency stop cleared - movements can resume")
            self.status_label.setText("Status: Ready (position may be uncertain)")
            self.status_label.setStyleSheet("color: orange; font-weight: bold;")

            # Re-enable controls
            self._set_emergency_stop_ui(False)

        except Exception as e:
            self.show_error(f"Error clearing emergency stop: {str(e)}")

    def _set_emergency_stop_ui(self, emergency_stopped: bool) -> None:
        """
        Update UI for emergency stop state.

        Args:
            emergency_stopped: True if emergency stop is active
        """
        # Show/hide emergency stop controls
        self.emergency_stop_btn.setVisible(not emergency_stopped)
        self.clear_estop_btn.setVisible(emergency_stopped)

        # Disable all movement controls during emergency stop
        if emergency_stopped:
            self.move_rotation_btn.setEnabled(False)
            self.move_x_btn.setEnabled(False)
            self.move_y_btn.setEnabled(False)
            self.move_z_btn.setEnabled(False)

            self.jog_x_minus.setEnabled(False)
            self.jog_x_plus.setEnabled(False)
            self.jog_y_minus.setEnabled(False)
            self.jog_y_plus.setEnabled(False)
            self.jog_z_minus.setEnabled(False)
            self.jog_z_plus.setEnabled(False)
            self.jog_r_minus.setEnabled(False)
            self.jog_r_plus.setEnabled(False)

            self.goto_preset_btn.setEnabled(False)
            self.undo_btn.setEnabled(False)
            self.home_btn.setEnabled(False)
        else:
            # Re-enable based on connection status
            self.set_connected(self._controller.connection.is_connected())

    def _validate_input_bounds(self, axis: str) -> None:
        """
        Validate input field value against stage limits and provide visual feedback.

        Args:
            axis: Axis to validate ('x', 'y', or 'z')
        """
        # Get the appropriate input field and limits
        if axis == 'x':
            input_field = self.x_input
            limits = self._stage_limits['x']
        elif axis == 'y':
            input_field = self.y_input
            limits = self._stage_limits['y']
        elif axis == 'z':
            input_field = self.z_input
            limits = self._stage_limits['z']
        else:
            return

        # Get the text value
        text = input_field.text().strip()

        # Clear styling if empty
        if not text:
            input_field.setStyleSheet("")
            return

        # Try to parse as float and check bounds
        try:
            value = float(text)
            min_val = limits['min']
            max_val = limits['max']

            if min_val <= value <= max_val:
                # Within bounds - green border
                input_field.setStyleSheet(
                    "border: 2px solid #28a745; background-color: #f0fff4;"
                )
            else:
                # Out of bounds - red border
                input_field.setStyleSheet(
                    "border: 2px solid #dc3545; background-color: #fff5f5;"
                )
        except ValueError:
            # Invalid number - yellow border
            input_field.setStyleSheet(
                "border: 2px solid #ffc107; background-color: #fffef0;"
            )

    def update_position(self, x: float, y: float, z: float, r: float) -> None:
        """Update position display.

        Args:
            x: X position in mm
            y: Y position in mm
            z: Z position in mm
            r: Rotation in degrees
        """
        self.x_label.setText(f"{x:.3f}")
        self.y_label.setText(f"{y:.3f}")
        self.z_label.setText(f"{z:.3f}")
        self.r_label.setText(f"{r:.2f}")

    def set_connected(self, connected: bool) -> None:
        """Update UI state based on connection status.

        Args:
            connected: True if connected to microscope
        """
        # Movement buttons
        self.move_rotation_btn.setEnabled(connected)
        self.move_x_btn.setEnabled(connected)
        self.move_y_btn.setEnabled(connected)
        self.move_z_btn.setEnabled(connected)

        # Jog buttons
        self.jog_x_minus.setEnabled(connected)
        self.jog_x_plus.setEnabled(connected)
        self.jog_y_minus.setEnabled(connected)
        self.jog_y_plus.setEnabled(connected)
        self.jog_z_minus.setEnabled(connected)
        self.jog_z_plus.setEnabled(connected)
        self.jog_r_minus.setEnabled(connected)
        self.jog_r_plus.setEnabled(connected)

        # Preset buttons
        self.save_preset_btn.setEnabled(connected)
        has_selection = len(self.preset_list.selectedItems()) > 0
        self.goto_preset_btn.setEnabled(connected and has_selection)

        # Undo button
        self._update_undo_button_state()

        # Safety buttons
        self.home_btn.setEnabled(connected)
        self.emergency_stop_btn.setEnabled(connected)

        if connected:
            self.status_label.setText("Status: Connected - Ready to move")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.status_label.setText("Status: Disconnected")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")

    def set_moving(self, moving: bool, axis: Optional[str] = None) -> None:
        """Update UI state for movement.

        Args:
            moving: True if stage is currently moving
            axis: Optional name of axis that is moving
        """
        # Disable all movement buttons during movement to prevent concurrent commands
        self.move_rotation_btn.setEnabled(not moving)
        self.move_x_btn.setEnabled(not moving)
        self.move_y_btn.setEnabled(not moving)
        self.move_z_btn.setEnabled(not moving)

        # Disable jog buttons during movement
        self.jog_x_minus.setEnabled(not moving)
        self.jog_x_plus.setEnabled(not moving)
        self.jog_y_minus.setEnabled(not moving)
        self.jog_y_plus.setEnabled(not moving)
        self.jog_z_minus.setEnabled(not moving)
        self.jog_z_plus.setEnabled(not moving)
        self.jog_r_minus.setEnabled(not moving)
        self.jog_r_plus.setEnabled(not moving)

        # Disable preset goto, undo, and home during movement
        has_selection = len(self.preset_list.selectedItems()) > 0
        self.goto_preset_btn.setEnabled(not moving and has_selection)
        self.undo_btn.setEnabled(not moving and self._controller.has_position_history())
        self.home_btn.setEnabled(not moving)

        # Emergency stop always enabled when connected (even during movement)
        # (already enabled in set_connected)

        if moving:
            axis_text = f" {axis}" if axis else ""
            self.status_label.setText(f"Status: Moving{axis_text}...")
            self.status_label.setStyleSheet("color: orange; font-weight: bold;")
        else:
            self.status_label.setText("Status: Ready")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")

    def show_success(self, message: str) -> None:
        """Display success message.

        Args:
            message: Success message to display
        """
        self.message_label.setText(message)
        self.message_label.setStyleSheet("color: green;")
        self._logger.info(f"Success: {message}")

    def show_error(self, message: str) -> None:
        """Display error message.

        Args:
            message: Error message to display
        """
        self.message_label.setText(f"Error: {message}")
        self.message_label.setStyleSheet("color: red;")
        self._logger.error(f"Error: {message}")

    def show_info(self, message: str) -> None:
        """Display info message.

        Args:
            message: Info message to display
        """
        self.message_label.setText(message)
        self.message_label.setStyleSheet("color: blue;")
        self._logger.info(f"Info: {message}")

    def clear_message(self) -> None:
        """Clear any displayed message."""
        self.message_label.setText("")

    def _on_motion_complete(self) -> None:
        """
        Handle motion complete callback from controller.

        This is called from a background thread when the microscope
        sends the motion-stopped callback.
        """
        # Must use QTimer to update GUI from background thread safely
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(0, self._update_after_motion_complete)

    def _update_after_motion_complete(self) -> None:
        """
        Update GUI after motion complete (called on GUI thread).

        Note: Position is queried from hardware AFTER motion completes to verify
        the stage reached the target position. This is the correct sequence.
        """
        # Re-enable controls
        self.set_moving(False)

        # Update position display with verified position from hardware
        position = self._controller.get_current_position()
        if position:
            self.update_position(position.x, position.y, position.z, position.r)
            # Show full verified position (all 4 axes) so user can see final state
            self._logger.info(
                f"Movement complete, verified position from hardware: "
                f"X={position.x:.3f}, Y={position.y:.3f}, Z={position.z:.3f}, R={position.r:.2f}°"
            )
            self.show_success(
                f"Movement complete! Verified: X={position.x:.3f}, Y={position.y:.3f}, "
                f"Z={position.z:.3f}, R={position.r:.2f}°"
            )
        else:
            self._logger.warning("Movement complete but could not verify position from hardware")
            self.show_info("Movement complete (position verification unavailable)")

        # Update undo button state (history may have changed)
        self._update_undo_button_state()
