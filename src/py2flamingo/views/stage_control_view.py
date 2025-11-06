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

            # Delegate to controller
            self._controller.move_rotation(rotation)

            # Movement command sent successfully
            self.show_success(f"Moving to rotation {rotation:.2f}°...")

            # Update position display
            position = self._controller.get_current_position()
            if position:
                self.update_position(position.x, position.y, position.z, position.r)

            # Reset moving status (note: actual movement is asynchronous)
            # TODO: Implement motion-stopped callback to reset this properly
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(2000, lambda: self.set_moving(False))

        except ValueError as e:
            self.show_error(f"Invalid rotation value: {str(e)}")
            self.set_moving(False)
        except RuntimeError as e:
            self.show_error(str(e))
            self.set_moving(False)
        except Exception as e:
            self.show_error(f"Unexpected error: {str(e)}")
            self.set_moving(False)

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
        self.move_rotation_btn.setEnabled(not moving)

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
