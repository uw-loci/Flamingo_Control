"""
Position panel for workflow configuration.

Provides UI for setting start position with "Use Current" button.
"""

import logging
from typing import Optional, Callable

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QPushButton, QGroupBox, QGridLayout
)
from PyQt5.QtCore import pyqtSignal

from py2flamingo.models.microscope import Position


class PositionPanel(QWidget):
    """
    Panel for configuring workflow start position.

    Provides:
    - X, Y, Z, R position display/input
    - "Use Current" button to capture current stage position

    Signals:
        position_changed: Emitted when position values change
    """

    position_changed = pyqtSignal(object)  # Emits Position

    def __init__(self,
                 get_current_position_callback: Optional[Callable[[], Optional[Position]]] = None,
                 parent: Optional[QWidget] = None):
        """
        Initialize position panel.

        Args:
            get_current_position_callback: Function to get current stage position
            parent: Parent widget
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._get_current_position = get_current_position_callback
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Position group
        group = QGroupBox("Start Position")
        group_layout = QVBoxLayout()

        # Position inputs in grid
        grid = QGridLayout()
        grid.setSpacing(8)

        # X position
        grid.addWidget(QLabel("X (mm):"), 0, 0)
        self.x_spinbox = QDoubleSpinBox()
        self.x_spinbox.setRange(-50.0, 50.0)
        self.x_spinbox.setDecimals(3)
        self.x_spinbox.setSingleStep(0.1)
        self.x_spinbox.valueChanged.connect(self._on_position_changed)
        grid.addWidget(self.x_spinbox, 0, 1)

        # Y position
        grid.addWidget(QLabel("Y (mm):"), 0, 2)
        self.y_spinbox = QDoubleSpinBox()
        self.y_spinbox.setRange(-50.0, 50.0)
        self.y_spinbox.setDecimals(3)
        self.y_spinbox.setSingleStep(0.1)
        self.y_spinbox.valueChanged.connect(self._on_position_changed)
        grid.addWidget(self.y_spinbox, 0, 3)

        # Z position
        grid.addWidget(QLabel("Z (mm):"), 1, 0)
        self.z_spinbox = QDoubleSpinBox()
        self.z_spinbox.setRange(0.0, 30.0)
        self.z_spinbox.setDecimals(3)
        self.z_spinbox.setSingleStep(0.1)
        self.z_spinbox.valueChanged.connect(self._on_position_changed)
        grid.addWidget(self.z_spinbox, 1, 1)

        # R (rotation) position
        grid.addWidget(QLabel("R (deg):"), 1, 2)
        self.r_spinbox = QDoubleSpinBox()
        self.r_spinbox.setRange(0.0, 360.0)
        self.r_spinbox.setDecimals(1)
        self.r_spinbox.setSingleStep(1.0)
        self.r_spinbox.valueChanged.connect(self._on_position_changed)
        grid.addWidget(self.r_spinbox, 1, 3)

        group_layout.addLayout(grid)

        # Use Current button
        btn_layout = QHBoxLayout()
        self.use_current_btn = QPushButton("Use Current Position")
        self.use_current_btn.clicked.connect(self._on_use_current_clicked)
        self.use_current_btn.setToolTip("Capture current stage position")
        btn_layout.addWidget(self.use_current_btn)
        btn_layout.addStretch()

        group_layout.addLayout(btn_layout)
        group.setLayout(group_layout)
        layout.addWidget(group)

    def _on_use_current_clicked(self) -> None:
        """Handle Use Current button click."""
        if self._get_current_position is None:
            self._logger.warning("No position callback set")
            return

        position = self._get_current_position()
        if position is None:
            self._logger.warning("Could not get current position")
            return

        self.set_position(position)
        self._logger.info(f"Captured current position: X={position.x:.3f}, Y={position.y:.3f}, "
                         f"Z={position.z:.3f}, R={position.r:.1f}")

    def _on_position_changed(self) -> None:
        """Handle position value changes."""
        position = self.get_position()
        self.position_changed.emit(position)

    def get_position(self) -> Position:
        """
        Get current position values from UI.

        Returns:
            Position object with current values
        """
        return Position(
            x=self.x_spinbox.value(),
            y=self.y_spinbox.value(),
            z=self.z_spinbox.value(),
            r=self.r_spinbox.value()
        )

    def set_position(self, position: Position) -> None:
        """
        Set position values in UI.

        Args:
            position: Position to set
        """
        # Block signals to prevent multiple emissions
        self.x_spinbox.blockSignals(True)
        self.y_spinbox.blockSignals(True)
        self.z_spinbox.blockSignals(True)
        self.r_spinbox.blockSignals(True)

        self.x_spinbox.setValue(position.x)
        self.y_spinbox.setValue(position.y)
        self.z_spinbox.setValue(position.z)
        self.r_spinbox.setValue(position.r)

        self.x_spinbox.blockSignals(False)
        self.y_spinbox.blockSignals(False)
        self.z_spinbox.blockSignals(False)
        self.r_spinbox.blockSignals(False)

        # Emit single position changed signal
        self.position_changed.emit(position)

    def set_position_callback(self, callback: Callable[[], Optional[Position]]) -> None:
        """
        Set the callback for getting current position.

        Args:
            callback: Function that returns current Position or None
        """
        self._get_current_position = callback
