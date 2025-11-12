"""
Stage Chamber Visualization Window - Standalone window for chamber visualization.

This window displays the StageChamberVisualizationWidget and connects it
to real-time position updates from the movement controller.
"""

import logging
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtGui import QFont

from py2flamingo.views.widgets.stage_chamber_visualization import StageChamberVisualizationWidget


class StageChamberVisualizationWindow(QWidget):
    """
    Standalone window showing stage chamber visualization.

    Displays dual XZ/XY views of the stage position within the sample chamber.
    Updates in real-time as the stage moves.
    """

    def __init__(self, movement_controller, parent=None):
        """
        Initialize stage chamber visualization window.

        Args:
            movement_controller: MovementController instance for position updates
            parent: Parent widget (optional)
        """
        super().__init__(parent)

        self.logger = logging.getLogger(__name__)
        self.movement_controller = movement_controller

        # Window configuration
        self.setWindowTitle("Stage Chamber Visualization")
        self.setMinimumSize(800, 450)
        self.resize(900, 500)

        self._setup_ui()
        self._connect_signals()

        # Request initial position update
        self._request_initial_position()

        self.logger.info("StageChamberVisualizationWindow initialized")

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout()
        layout.setSpacing(10)

        # Title label
        title = QLabel("Stage Position within Sample Chamber")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # Subtitle with instructions
        subtitle = QLabel(
            "XZ View (Left): Top-down perspective  |  "
            "XY View (Right): Side view with objective (faded circles below sample)"
        )
        subtitle.setStyleSheet("color: #666; font-style: italic;")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        # Create visualization widget
        self.visualization_widget = StageChamberVisualizationWidget()
        layout.addWidget(self.visualization_widget)

        # Position info label
        self.position_info = QLabel("Position: Waiting for update...")
        self.position_info.setStyleSheet(
            "background-color: #f0f0f0; padding: 8px; "
            "border: 1px solid #ccc; border-radius: 4px;"
        )
        self.position_info.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.position_info)

        self.setLayout(layout)

    def _connect_signals(self) -> None:
        """Connect to movement controller signals."""
        # Connect position_changed signal to update visualization
        self.movement_controller.position_changed.connect(
            self._on_position_changed
        )

        self.logger.info("Connected to movement controller signals")

    def _request_initial_position(self) -> None:
        """Request and display initial position from the microscope."""
        try:
            # Get current position from controller
            position = self.movement_controller.get_position()
            if position:
                # Update visualization with current position
                self._on_position_changed(position.x, position.y, position.z, position.r)
                self.logger.info(f"Initial position loaded: {position}")
            else:
                self.logger.warning("No initial position available")
                self.position_info.setText("Position: Not available (not connected?)")
        except Exception as e:
            self.logger.error(f"Error requesting initial position: {e}")
            self.position_info.setText(f"Position: Error - {e}")

    @pyqtSlot(float, float, float, float)
    def _on_position_changed(self, x: float, y: float, z: float, r: float) -> None:
        """
        Handle position change signal.

        Args:
            x: X position in mm
            y: Y position in mm
            z: Z position in mm
            r: Rotation in degrees
        """
        # Update visualization widget
        self.visualization_widget.update_position(x, y, z, r)

        # Update position info label
        self.position_info.setText(
            f"Position: X={x:.2f} mm, Y={y:.2f} mm, Z={z:.2f} mm, R={r:.2f}°"
        )

        self.logger.debug(f"Position updated: X={x:.2f}, Y={y:.2f}, Z={z:.2f}, R={r:.2f}")

    def closeEvent(self, event) -> None:
        """Handle window close event."""
        self.logger.info("Stage chamber visualization window closed")
        event.accept()
