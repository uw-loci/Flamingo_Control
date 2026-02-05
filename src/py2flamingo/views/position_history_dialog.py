"""
Position History Dialog - View and Navigate Position History

This dialog displays the position history from the microscope stage,
allowing users to:
- View the last N positions visited
- Select and navigate to previous positions
- See the spatial extent (min/max) explored at the current rotation angle
"""

import logging
from typing import Optional, List
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QGroupBox, QMessageBox
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon

from py2flamingo.services.window_geometry_manager import PersistentDialog
from py2flamingo.models.microscope import Position
from py2flamingo.views.colors import SUCCESS_COLOR


class PositionHistoryDialog(PersistentDialog):
    """Dialog for viewing and navigating position history.

    Features:
    - Scrollable list of historical positions (most recent at top)
    - Click to select a position
    - "Go to highlighted position" button to move all 4 axes
    - Min/Max display for X, Y, Z at current rotation angle
    """

    def __init__(self, movement_controller, parent=None):
        """Initialize position history dialog.

        Args:
            movement_controller: MovementController instance
            parent: Parent widget
        """
        super().__init__(parent)
        self.movement_controller = movement_controller
        self.logger = logging.getLogger(__name__)

        self.setWindowTitle("Position History")
        self.setWindowIcon(QIcon())  # Clear inherited napari icon
        self.setMinimumSize(800, 600)

        self.setup_ui()
        self.refresh_history()

    def setup_ui(self):
        """Create and layout UI components."""
        layout = QHBoxLayout()
        self.setLayout(layout)

        # Left column: Position list (takes up most of the space)
        left_layout = QVBoxLayout()
        layout.addLayout(left_layout, stretch=3)

        # Position list title
        title = QLabel("<b>Position History</b> (Most Recent First)")
        title.setStyleSheet("font-size: 12pt; padding: 5px;")
        left_layout.addWidget(title)

        # Position list widget
        self.position_list = QListWidget()
        self.position_list.setSelectionMode(QListWidget.SingleSelection)
        self.position_list.itemSelectionChanged.connect(self._on_selection_changed)
        left_layout.addWidget(self.position_list)

        # Right column: Actions and info
        right_layout = QVBoxLayout()
        layout.addLayout(right_layout, stretch=1)

        # Go to position button
        self.goto_btn = QPushButton("Go To Highlighted Position")
        self.goto_btn.setEnabled(False)
        self.goto_btn.clicked.connect(self._on_goto_clicked)
        self.goto_btn.setStyleSheet(
            f"background-color: {SUCCESS_COLOR}; color: white; padding: 12px; "
            "font-weight: bold; font-size: 11pt;"
        )
        right_layout.addWidget(self.goto_btn)

        right_layout.addSpacing(20)

        # Spatial extent info (min/max at current rotation)
        extent_group = QGroupBox("Spatial Extent at Current Rotation")
        extent_layout = QVBoxLayout()
        extent_group.setLayout(extent_layout)

        extent_info = QLabel(
            "Shows the range explored in X, Y, Z\n"
            "at positions matching the current\n"
            "rotation angle (±1°)."
        )
        extent_info.setStyleSheet("color: #666; font-style: italic; padding: 5px;")
        extent_info.setWordWrap(True)
        extent_layout.addWidget(extent_info)

        self.x_extent_label = QLabel("X: —")
        self.y_extent_label = QLabel("Y: —")
        self.z_extent_label = QLabel("Z: —")
        self.extent_count_label = QLabel("Positions: 0")

        for label in [self.x_extent_label, self.y_extent_label,
                      self.z_extent_label, self.extent_count_label]:
            label.setStyleSheet("padding: 5px; font-family: monospace;")
            extent_layout.addWidget(label)

        right_layout.addWidget(extent_group)

        right_layout.addStretch()

        # Close button at bottom
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        close_btn.setStyleSheet("padding: 8px;")
        right_layout.addWidget(close_btn)

    def refresh_history(self):
        """Refresh the position history display."""
        self.position_list.clear()

        # Get history from controller
        history = self.movement_controller.position_controller.get_position_history()

        if not history:
            item = QListWidgetItem("No position history available")
            item.setFlags(Qt.NoItemFlags)  # Not selectable
            self.position_list.addItem(item)
            self._update_extent_info([])
            return

        # Add positions in reverse order (most recent first)
        for idx, pos in enumerate(reversed(history)):
            # Format: "1. X=8.430, Y=18.438, Z=18.839, R=135.66°"
            display_text = (
                f"{idx + 1}. X={pos.x:.3f}, Y={pos.y:.3f}, "
                f"Z={pos.z:.3f}, R={pos.r:.2f}°"
            )
            item = QListWidgetItem(display_text)
            # Store the Position object in the item for later retrieval
            item.setData(Qt.UserRole, pos)
            self.position_list.addItem(item)

        self.logger.info(f"Loaded {len(history)} positions into history dialog")

        # Update extent info based on current position
        self._update_extent_info(history)

    def _on_selection_changed(self):
        """Handle position selection change."""
        has_selection = len(self.position_list.selectedItems()) > 0
        self.goto_btn.setEnabled(has_selection)

    def _on_goto_clicked(self):
        """Handle 'Go to highlighted position' button click."""
        selected_items = self.position_list.selectedItems()
        if not selected_items:
            return

        # Get the Position object from the selected item
        position = selected_items[0].data(Qt.UserRole)

        if position is None:
            QMessageBox.warning(self, "Error", "Invalid position selected")
            return

        # Confirm with user
        reply = QMessageBox.question(
            self,
            "Confirm Movement",
            f"Move to position:\n\n"
            f"X: {position.x:.3f} mm\n"
            f"Y: {position.y:.3f} mm\n"
            f"Z: {position.z:.3f} mm\n"
            f"R: {position.r:.2f}°\n\n"
            f"Move all 4 axes to this position?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                # Move to the selected position
                self.movement_controller.position_controller.move_to_position(
                    position,
                    validate=True
                )
                self.logger.info(f"Moving to historical position: {position}")
                QMessageBox.information(
                    self,
                    "Moving",
                    "Moving to selected position...\n\n"
                    "The dialog will remain open so you can\n"
                    "select another position if needed."
                )
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Movement Error",
                    f"Failed to move to position:\n{str(e)}"
                )
                self.logger.error(f"Failed to move to historical position: {e}")

    def _update_extent_info(self, history: List[Position]):
        """Update the spatial extent information for current rotation.

        Args:
            history: List of Position objects
        """
        if not history:
            self.x_extent_label.setText("X: —")
            self.y_extent_label.setText("Y: —")
            self.z_extent_label.setText("Z: —")
            self.extent_count_label.setText("Positions: 0")
            return

        # Get current rotation angle
        current_pos = self.movement_controller.get_position()
        if current_pos is None:
            current_rotation = 0.0
        else:
            current_rotation = current_pos.r

        # Filter positions that match current rotation (±1°)
        rotation_tolerance = 1.0  # degrees
        matching_positions = [
            pos for pos in history
            if abs(pos.r - current_rotation) <= rotation_tolerance
        ]

        if not matching_positions:
            self.x_extent_label.setText(f"X: — (no positions at R≈{current_rotation:.2f}°)")
            self.y_extent_label.setText("Y: —")
            self.z_extent_label.setText("Z: —")
            self.extent_count_label.setText("Positions: 0")
            return

        # Calculate min/max for X, Y, Z
        x_values = [pos.x for pos in matching_positions]
        y_values = [pos.y for pos in matching_positions]
        z_values = [pos.z for pos in matching_positions]

        x_min, x_max = min(x_values), max(x_values)
        y_min, y_max = min(y_values), max(y_values)
        z_min, z_max = min(z_values), max(z_values)

        # Display with ranges
        x_range = x_max - x_min
        y_range = y_max - y_min
        z_range = z_max - z_min

        self.x_extent_label.setText(
            f"X: {x_min:.3f} to {x_max:.3f} mm (Δ={x_range:.3f})"
        )
        self.y_extent_label.setText(
            f"Y: {y_min:.3f} to {y_max:.3f} mm (Δ={y_range:.3f})"
        )
        self.z_extent_label.setText(
            f"Z: {z_min:.3f} to {z_max:.3f} mm (Δ={z_range:.3f})"
        )
        self.extent_count_label.setText(
            f"Positions: {len(matching_positions)} at R≈{current_rotation:.2f}°"
        )

        self.logger.debug(
            f"Extent at R={current_rotation:.2f}°: "
            f"X=[{x_min:.3f}, {x_max:.3f}], "
            f"Y=[{y_min:.3f}, {y_max:.3f}], "
            f"Z=[{z_min:.3f}, {z_max:.3f}]"
        )
