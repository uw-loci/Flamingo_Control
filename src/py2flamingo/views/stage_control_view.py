"""
Stage control view for microscope positioning.

This module provides the StageControlView widget for controlling
stage position (X, Y, Z, Rotation).
"""

import logging
from typing import Optional
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QGroupBox, QFormLayout
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

    def setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout()

        # Current Position Display
        position_group = self._create_position_display()
        layout.addWidget(position_group)

        # Rotation Control Section
        rotation_group = self._create_rotation_control()
        layout.addWidget(rotation_group)

        # X, Y, Z Axis Controls
        xyz_group = self._create_xyz_controls()
        layout.addWidget(xyz_group)

        # Status Display
        self.status_label = QLabel("Status: Ready")
        self.status_label.setStyleSheet("color: gray; font-weight: bold;")
        layout.addWidget(self.status_label)

        # Message Display
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        self.message_label.setMinimumHeight(40)
        layout.addWidget(self.message_label)

        # Add stretch to push everything to top
        layout.addStretch()

        self.setLayout(layout)

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
        self.move_rotation_btn.setEnabled(connected)
        self.move_x_btn.setEnabled(connected)
        self.move_y_btn.setEnabled(connected)
        self.move_z_btn.setEnabled(connected)

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
        """
        self._logger.info("Motion complete - updating GUI")

        # Re-enable controls
        self.set_moving(False)

        # Update position display
        position = self._controller.get_current_position()
        if position:
            self.update_position(position.x, position.y, position.z, position.r)
            self.show_success(f"Movement complete! Position: R={position.r:.2f}°")
        else:
            self.show_info("Movement complete")
