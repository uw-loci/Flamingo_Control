"""
Dual Position panel for workflow configuration.

Provides UI for setting start and end positions with context-aware visibility
based on workflow type (snapshot, zstack, tiling).

For Z-Stack: Point A = full XYZR, Point B = Z only (calculates num_planes)
For Tiling: Point A = start corner, Point B = end corner (calculates tiles)

Includes "Load Saved Position" dropdown for each position, using the
PositionPresetService to load from saved presets.
"""

import logging
from typing import Optional, Callable, Tuple, TYPE_CHECKING

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QPushButton, QGroupBox, QGridLayout, QComboBox
)
from PyQt5.QtCore import pyqtSignal

from py2flamingo.models.microscope import Position

if TYPE_CHECKING:
    from py2flamingo.services.position_preset_service import PositionPresetService


class DualPositionPanel(QWidget):
    """
    Panel for configuring workflow start and end positions.

    Provides:
    - Position A (Start): X, Y, Z, R - always visible
    - Position B (End): visibility/editability controlled by mode
    - "Use Current" buttons to capture current stage position

    Modes:
    - snapshot: Position B hidden
    - zstack: Position B shows only Z (X, Y, R greyed)
    - tiling: Position B shows X, Y, Z (R greyed)

    Signals:
        position_a_changed: Emitted when Position A values change
        position_b_changed: Emitted when Position B values change
    """

    position_a_changed = pyqtSignal(object)  # Emits Position
    position_b_changed = pyqtSignal(object)  # Emits Position

    def __init__(self,
                 get_current_position_callback: Optional[Callable[[], Optional[Position]]] = None,
                 preset_service: Optional['PositionPresetService'] = None,
                 parent: Optional[QWidget] = None):
        """
        Initialize dual position panel.

        Args:
            get_current_position_callback: Function to get current stage position
            preset_service: Service for managing saved position presets
            parent: Parent widget
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._get_current_position = get_current_position_callback
        self._preset_service = preset_service
        self._mode = "snapshot"  # "snapshot", "zstack", "tiling"
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Position A (Start) - always fully visible
        self._group_a = self._create_position_group("Position A (Start)", "a")
        layout.addWidget(self._group_a)

        # Position B (End) - visibility controlled by mode
        self._group_b = self._create_position_group("Position B (End)", "b")
        layout.addWidget(self._group_b)

        # Initially hide Position B (snapshot mode)
        self._group_b.setVisible(False)

    def _create_position_group(self, title: str, suffix: str) -> QGroupBox:
        """
        Create a position input group with X, Y, Z, R spinboxes.

        Args:
            title: Group box title
            suffix: Suffix for widget names ('a' or 'b')

        Returns:
            Configured QGroupBox
        """
        group = QGroupBox(title)
        group_layout = QVBoxLayout()

        # Position inputs in grid
        grid = QGridLayout()
        grid.setSpacing(8)

        # X position
        x_label = QLabel("X (mm):")
        grid.addWidget(x_label, 0, 0)
        x_spin = QDoubleSpinBox()
        x_spin.setRange(-50.0, 50.0)
        x_spin.setDecimals(3)
        x_spin.setSingleStep(0.1)
        x_spin.valueChanged.connect(lambda: self._on_position_changed(suffix))
        setattr(self, f"_x_{suffix}", x_spin)
        setattr(self, f"_x_{suffix}_label", x_label)
        grid.addWidget(x_spin, 0, 1)

        # Y position
        y_label = QLabel("Y (mm):")
        grid.addWidget(y_label, 0, 2)
        y_spin = QDoubleSpinBox()
        y_spin.setRange(-50.0, 50.0)
        y_spin.setDecimals(3)
        y_spin.setSingleStep(0.1)
        y_spin.valueChanged.connect(lambda: self._on_position_changed(suffix))
        setattr(self, f"_y_{suffix}", y_spin)
        setattr(self, f"_y_{suffix}_label", y_label)
        grid.addWidget(y_spin, 0, 3)

        # Z position
        z_label = QLabel("Z (mm):")
        grid.addWidget(z_label, 1, 0)
        z_spin = QDoubleSpinBox()
        z_spin.setRange(0.0, 30.0)
        z_spin.setDecimals(3)
        z_spin.setSingleStep(0.1)
        z_spin.valueChanged.connect(lambda: self._on_position_changed(suffix))
        setattr(self, f"_z_{suffix}", z_spin)
        setattr(self, f"_z_{suffix}_label", z_label)
        grid.addWidget(z_spin, 1, 1)

        # R (rotation) position
        r_label = QLabel("R (deg):")
        grid.addWidget(r_label, 1, 2)
        r_spin = QDoubleSpinBox()
        r_spin.setRange(0.0, 360.0)
        r_spin.setDecimals(1)
        r_spin.setSingleStep(1.0)
        r_spin.valueChanged.connect(lambda: self._on_position_changed(suffix))
        setattr(self, f"_r_{suffix}", r_spin)
        setattr(self, f"_r_{suffix}_label", r_label)
        grid.addWidget(r_spin, 1, 3)

        group_layout.addLayout(grid)

        # Buttons row: Use Current and Load Saved
        btn_layout = QHBoxLayout()

        # Use Current button
        use_current_btn = QPushButton("Use Current")
        use_current_btn.clicked.connect(lambda: self._on_use_current_clicked(suffix))
        use_current_btn.setToolTip("Capture current stage position")
        setattr(self, f"_use_current_btn_{suffix}", use_current_btn)
        btn_layout.addWidget(use_current_btn)

        # Load Saved Position dropdown
        btn_layout.addWidget(QLabel("Load Saved:"))
        preset_combo = QComboBox()
        preset_combo.setMinimumWidth(120)
        preset_combo.addItem("(Select)")
        preset_combo.setToolTip("Load a saved position preset")
        preset_combo.currentIndexChanged.connect(lambda idx: self._on_preset_selected(suffix, idx))
        setattr(self, f"_preset_combo_{suffix}", preset_combo)
        btn_layout.addWidget(preset_combo)

        btn_layout.addStretch()

        group_layout.addLayout(btn_layout)
        group.setLayout(group_layout)

        return group

    def _on_use_current_clicked(self, suffix: str) -> None:
        """Handle Use Current button click for position A or B."""
        if self._get_current_position is None:
            self._logger.warning("No position callback set")
            return

        position = self._get_current_position()
        if position is None:
            self._logger.warning("Could not get current position")
            return

        if suffix == "a":
            self.set_position_a(position)
            self._logger.info(f"Captured Position A: X={position.x:.3f}, Y={position.y:.3f}, "
                             f"Z={position.z:.3f}, R={position.r:.1f}")
        else:
            self.set_position_b(position)
            self._logger.info(f"Captured Position B: X={position.x:.3f}, Y={position.y:.3f}, "
                             f"Z={position.z:.3f}, R={position.r:.1f}")

        # Reset the preset combo to "(Select)" since user manually set position
        preset_combo = getattr(self, f"_preset_combo_{suffix}")
        preset_combo.blockSignals(True)
        preset_combo.setCurrentIndex(0)
        preset_combo.blockSignals(False)

    def _on_preset_selected(self, suffix: str, index: int) -> None:
        """Handle preset selection from dropdown."""
        if index <= 0:  # "(Select)" or invalid
            return

        if self._preset_service is None:
            self._logger.warning("No preset service available")
            return

        preset_combo = getattr(self, f"_preset_combo_{suffix}")
        preset_name = preset_combo.currentText()

        preset = self._preset_service.get_preset(preset_name)
        if preset is None:
            self._logger.warning(f"Preset '{preset_name}' not found")
            return

        position = preset.to_position()

        if suffix == "a":
            self.set_position_a(position)
            self._logger.info(f"Loaded Position A from preset '{preset_name}': "
                             f"X={position.x:.3f}, Y={position.y:.3f}, "
                             f"Z={position.z:.3f}, R={position.r:.1f}")
        else:
            self.set_position_b(position)
            self._logger.info(f"Loaded Position B from preset '{preset_name}': "
                             f"X={position.x:.3f}, Y={position.y:.3f}, "
                             f"Z={position.z:.3f}, R={position.r:.1f}")

    def _on_position_changed(self, suffix: str) -> None:
        """Handle position value changes."""
        if suffix == "a":
            position = self.get_position_a()
            self.position_a_changed.emit(position)
        else:
            position = self.get_position_b()
            self.position_b_changed.emit(position)

    def set_mode(self, mode: str) -> None:
        """
        Set panel mode: 'snapshot', 'zstack', 'tiling'.

        Args:
            mode: Panel mode controlling Position B visibility
        """
        self._mode = mode

        if mode == "snapshot":
            # Hide Point B entirely
            self._group_b.setVisible(False)

        elif mode == "zstack":
            # Show Point B, but only Z is editable (X, Y, R greyed)
            self._group_b.setVisible(True)
            self._x_b.setEnabled(False)
            self._y_b.setEnabled(False)
            self._r_b.setEnabled(False)
            self._z_b.setEnabled(True)
            self._group_b.setTitle("Position B (End Z)")

            # Copy X, Y, R from A to B for visual clarity
            self._x_b.setValue(self._x_a.value())
            self._y_b.setValue(self._y_a.value())
            self._r_b.setValue(self._r_a.value())

        elif mode == "tiling":
            # Show Point B with X, Y, Z editable (R greyed)
            self._group_b.setVisible(True)
            self._x_b.setEnabled(True)
            self._y_b.setEnabled(True)
            self._z_b.setEnabled(True)
            self._r_b.setEnabled(False)
            self._group_b.setTitle("Position B (End Corner)")

            # Copy R from A to B for visual clarity
            self._r_b.setValue(self._r_a.value())

        self._logger.debug(f"Panel mode set to: {mode}")

    def get_mode(self) -> str:
        """Get current panel mode."""
        return self._mode

    def get_position_a(self) -> Position:
        """
        Get Position A values from UI.

        Returns:
            Position object with Position A values
        """
        return Position(
            x=self._x_a.value(),
            y=self._y_a.value(),
            z=self._z_a.value(),
            r=self._r_a.value()
        )

    def get_position_b(self) -> Position:
        """
        Get Position B values from UI.

        Returns:
            Position object with Position B values
        """
        return Position(
            x=self._x_b.value(),
            y=self._y_b.value(),
            z=self._z_b.value(),
            r=self._r_b.value()
        )

    def set_position_a(self, position: Position) -> None:
        """
        Set Position A values in UI.

        Args:
            position: Position to set
        """
        # Block signals to prevent multiple emissions
        self._x_a.blockSignals(True)
        self._y_a.blockSignals(True)
        self._z_a.blockSignals(True)
        self._r_a.blockSignals(True)

        self._x_a.setValue(position.x)
        self._y_a.setValue(position.y)
        self._z_a.setValue(position.z)
        self._r_a.setValue(position.r)

        self._x_a.blockSignals(False)
        self._y_a.blockSignals(False)
        self._z_a.blockSignals(False)
        self._r_a.blockSignals(False)

        # Emit single position changed signal
        self.position_a_changed.emit(position)

        # In zstack mode, copy X, Y, R to B for clarity
        if self._mode == "zstack":
            self._x_b.setValue(position.x)
            self._y_b.setValue(position.y)
            self._r_b.setValue(position.r)

    def set_position_b(self, position: Position) -> None:
        """
        Set Position B values in UI.

        Only sets enabled fields based on mode.

        Args:
            position: Position to set
        """
        # Block signals to prevent multiple emissions
        self._x_b.blockSignals(True)
        self._y_b.blockSignals(True)
        self._z_b.blockSignals(True)
        self._r_b.blockSignals(True)

        # Always set Z (editable in all modes where B is visible)
        self._z_b.setValue(position.z)

        # Set X, Y only in tiling mode
        if self._mode == "tiling":
            self._x_b.setValue(position.x)
            self._y_b.setValue(position.y)

        self._x_b.blockSignals(False)
        self._y_b.blockSignals(False)
        self._z_b.blockSignals(False)
        self._r_b.blockSignals(False)

        # Emit position changed signal
        self.position_b_changed.emit(self.get_position_b())

    def get_z_range(self) -> Tuple[float, float]:
        """
        Get Z range from both positions.

        Returns:
            Tuple of (z_min, z_max) in mm
        """
        z_a = self._z_a.value()
        z_b = self._z_b.value()
        return (min(z_a, z_b), max(z_a, z_b))

    def get_xy_range(self) -> Tuple[float, float, float, float]:
        """
        Get XY range from both positions.

        Returns:
            Tuple of (x_min, x_max, y_min, y_max) in mm
        """
        return (
            min(self._x_a.value(), self._x_b.value()),
            max(self._x_a.value(), self._x_b.value()),
            min(self._y_a.value(), self._y_b.value()),
            max(self._y_a.value(), self._y_b.value())
        )

    def set_position_callback(self, callback: Callable[[], Optional[Position]]) -> None:
        """
        Set the callback for getting current position.

        Args:
            callback: Function that returns current Position or None
        """
        self._get_current_position = callback

    # === Compatibility methods for WorkflowView ===

    def get_position(self) -> Position:
        """
        Get Position A (start position).

        Compatibility method - same as get_position_a().

        Returns:
            Position A (start position)
        """
        return self.get_position_a()

    def set_position(self, position: Position) -> None:
        """
        Set Position A (start position).

        Compatibility method - same as set_position_a().

        Args:
            position: Position to set
        """
        self.set_position_a(position)

    @property
    def position_changed(self) -> pyqtSignal:
        """Alias for position_a_changed for compatibility."""
        return self.position_a_changed

    # =========================================================================
    # Preset Service Integration
    # =========================================================================

    def set_preset_service(self, preset_service: 'PositionPresetService') -> None:
        """
        Set the preset service for loading saved positions.

        Args:
            preset_service: PositionPresetService instance
        """
        self._preset_service = preset_service
        self.refresh_preset_lists()

    def refresh_preset_lists(self) -> None:
        """
        Refresh both preset dropdowns with current preset names.

        Call this when presets are added/deleted externally.
        """
        if self._preset_service is None:
            return

        preset_names = self._preset_service.get_preset_names()

        for suffix in ['a', 'b']:
            preset_combo = getattr(self, f"_preset_combo_{suffix}")
            current_text = preset_combo.currentText()

            preset_combo.blockSignals(True)
            preset_combo.clear()
            preset_combo.addItem("(Select)")
            for name in preset_names:
                preset_combo.addItem(name)

            # Try to restore previous selection
            idx = preset_combo.findText(current_text)
            if idx >= 0:
                preset_combo.setCurrentIndex(idx)
            else:
                preset_combo.setCurrentIndex(0)

            preset_combo.blockSignals(False)

        self._logger.debug(f"Refreshed preset lists with {len(preset_names)} presets")
