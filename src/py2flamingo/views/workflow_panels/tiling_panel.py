"""
Tiling settings panel for workflow configuration.

Provides UI for tile/mosaic acquisition parameters.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSpinBox, QDoubleSpinBox, QComboBox, QGroupBox, QGridLayout, QFrame
)
from PyQt5.QtCore import pyqtSignal

from py2flamingo.models.data.workflow import TileSettings


class TilingPanel(QWidget):
    """
    Panel for configuring tile/mosaic acquisition settings.

    Provides:
    - Tiles X/Y count
    - Overlap percentage
    - Scan pattern selection
    - Calculated scan area display

    Signals:
        settings_changed: Emitted when tiling settings change
    """

    settings_changed = pyqtSignal(object)  # Emits TileSettings

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize tiling panel.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._tile_size_um = 520.0  # Default tile size in microns (camera FOV ~0.52mm)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Tiling settings group
        group = QGroupBox("Tiling / Mosaic Settings")
        grid = QGridLayout()
        grid.setSpacing(8)

        # Tiles X
        grid.addWidget(QLabel("Tiles X:"), 0, 0)
        self._tiles_x = QSpinBox()
        self._tiles_x.setRange(1, 100)
        self._tiles_x.setValue(3)
        self._tiles_x.valueChanged.connect(self._on_settings_changed)
        grid.addWidget(self._tiles_x, 0, 1)

        # Tiles Y
        grid.addWidget(QLabel("Tiles Y:"), 0, 2)
        self._tiles_y = QSpinBox()
        self._tiles_y.setRange(1, 100)
        self._tiles_y.setValue(3)
        self._tiles_y.valueChanged.connect(self._on_settings_changed)
        grid.addWidget(self._tiles_y, 0, 3)

        # Total tiles display
        grid.addWidget(QLabel("Total Tiles:"), 1, 0)
        self._total_tiles_label = QLabel("9")
        self._total_tiles_label.setStyleSheet("font-weight: bold; color: #2980b9;")
        grid.addWidget(self._total_tiles_label, 1, 1)

        # Overlap percentage
        grid.addWidget(QLabel("Overlap:"), 1, 2)
        self._overlap = QDoubleSpinBox()
        self._overlap.setRange(0.0, 50.0)
        self._overlap.setValue(10.0)
        self._overlap.setSuffix(" %")
        self._overlap.setDecimals(1)
        self._overlap.valueChanged.connect(self._on_settings_changed)
        grid.addWidget(self._overlap, 1, 3)

        # Scan pattern
        grid.addWidget(QLabel("Scan Pattern:"), 2, 0)
        self._pattern = QComboBox()
        self._pattern.addItems(["Raster", "Snake", "Spiral"])
        self._pattern.currentIndexChanged.connect(self._on_settings_changed)
        grid.addWidget(self._pattern, 2, 1)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        grid.addWidget(separator, 3, 0, 1, 4)

        # Calculated scan area
        grid.addWidget(QLabel("Scan Area X:"), 4, 0)
        self._area_x_label = QLabel("5.53 mm")
        self._area_x_label.setStyleSheet("font-weight: bold;")
        grid.addWidget(self._area_x_label, 4, 1)

        grid.addWidget(QLabel("Scan Area Y:"), 4, 2)
        self._area_y_label = QLabel("5.53 mm")
        self._area_y_label.setStyleSheet("font-weight: bold;")
        grid.addWidget(self._area_y_label, 4, 3)

        # End position (relative)
        grid.addWidget(QLabel("End Offset X:"), 5, 0)
        self._end_x_label = QLabel("+5.53 mm")
        grid.addWidget(self._end_x_label, 5, 1)

        grid.addWidget(QLabel("End Offset Y:"), 5, 2)
        self._end_y_label = QLabel("+5.53 mm")
        grid.addWidget(self._end_y_label, 5, 3)

        # Info text
        info_label = QLabel("Tile scan will acquire a mosaic of images. "
                           "End position is calculated from start position + scan area.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: gray; font-size: 9pt;")
        grid.addWidget(info_label, 6, 0, 1, 4)

        group.setLayout(grid)
        layout.addWidget(group)

        # Initial calculation
        self._update_calculations()

    def _on_settings_changed(self) -> None:
        """Handle any settings change."""
        self._update_calculations()
        settings = self.get_settings()
        self.settings_changed.emit(settings)

    def _update_calculations(self) -> None:
        """Update calculated values (total tiles, scan area)."""
        tiles_x = self._tiles_x.value()
        tiles_y = self._tiles_y.value()
        overlap = self._overlap.value() / 100.0

        # Total tiles
        total = tiles_x * tiles_y
        self._total_tiles_label.setText(str(total))

        # Scan area calculation
        # Each tile covers tile_size_um, minus overlap with previous tile
        effective_tile_x = self._tile_size_um * (1 - overlap)
        effective_tile_y = self._tile_size_um * (1 - overlap)

        # Total scan area
        scan_x_um = self._tile_size_um + (tiles_x - 1) * effective_tile_x
        scan_y_um = self._tile_size_um + (tiles_y - 1) * effective_tile_y

        scan_x_mm = scan_x_um / 1000.0
        scan_y_mm = scan_y_um / 1000.0

        self._area_x_label.setText(f"{scan_x_mm:.2f} mm")
        self._area_y_label.setText(f"{scan_y_mm:.2f} mm")

        self._end_x_label.setText(f"+{scan_x_mm:.2f} mm")
        self._end_y_label.setText(f"+{scan_y_mm:.2f} mm")

    def get_settings(self) -> TileSettings:
        """
        Get current tiling settings.

        Returns:
            TileSettings object with current values
        """
        return TileSettings(
            num_tiles_x=self._tiles_x.value(),
            num_tiles_y=self._tiles_y.value(),
            overlap_percent=self._overlap.value(),
        )

    def get_workflow_tiling_dict(self) -> Dict[str, Any]:
        """
        Get tiling settings as workflow dictionary format.

        Returns:
            Dictionary for workflow file Stack Settings section
        """
        return {
            'Stack option': 'Tile',
            'Stack option settings 1': self._tiles_x.value(),
            'Stack option settings 2': self._tiles_y.value(),
        }

    def set_settings(self, settings: TileSettings) -> None:
        """
        Set tiling settings from object.

        Args:
            settings: TileSettings to apply
        """
        self._tiles_x.setValue(settings.num_tiles_x)
        self._tiles_y.setValue(settings.num_tiles_y)
        self._overlap.setValue(settings.overlap_percent)

    def set_tile_size(self, tile_size_um: float) -> None:
        """
        Set the tile size for scan area calculations.

        Args:
            tile_size_um: Tile size in micrometers (typically camera FOV)
        """
        self._tile_size_um = tile_size_um
        self._update_calculations()

    def get_tiles_x(self) -> int:
        """Get number of tiles in X."""
        return self._tiles_x.value()

    def get_tiles_y(self) -> int:
        """Get number of tiles in Y."""
        return self._tiles_y.value()

    def get_total_tiles(self) -> int:
        """Get total number of tiles."""
        return self._tiles_x.value() * self._tiles_y.value()

    def get_scan_area_mm(self) -> tuple:
        """
        Get calculated scan area in millimeters.

        Returns:
            Tuple of (scan_x_mm, scan_y_mm)
        """
        tiles_x = self._tiles_x.value()
        tiles_y = self._tiles_y.value()
        overlap = self._overlap.value() / 100.0

        effective_tile_x = self._tile_size_um * (1 - overlap)
        effective_tile_y = self._tile_size_um * (1 - overlap)

        scan_x_um = self._tile_size_um + (tiles_x - 1) * effective_tile_x
        scan_y_um = self._tile_size_um + (tiles_y - 1) * effective_tile_y

        return (scan_x_um / 1000.0, scan_y_um / 1000.0)

    # =========================================================================
    # Two-Point Mode (for DualPositionPanel integration)
    # =========================================================================

    def set_two_point_mode(self, enabled: bool) -> None:
        """
        Enable two-point mode where tile count comes from DualPositionPanel.

        In two-point mode:
        - Tiles X/Y are auto-calculated from position range and overlap
        - User sets corner positions in DualPositionPanel
        - Panel shows calculated tile counts (read-only)

        Args:
            enabled: True to enable two-point mode
        """
        if enabled:
            # Make tiles X/Y read-only
            self._tiles_x.setReadOnly(True)
            self._tiles_y.setReadOnly(True)
            self._tiles_x.setStyleSheet("QSpinBox { background-color: #f0f0f0; }")
            self._tiles_y.setStyleSheet("QSpinBox { background-color: #f0f0f0; }")
            self._tiles_x.setToolTip("Auto-calculated from Position A/B corners")
            self._tiles_y.setToolTip("Auto-calculated from Position A/B corners")
        else:
            # Return to manual mode
            self._tiles_x.setReadOnly(False)
            self._tiles_y.setReadOnly(False)
            self._tiles_x.setStyleSheet("")
            self._tiles_y.setStyleSheet("")
            self._tiles_x.setToolTip("")
            self._tiles_y.setToolTip("")

    def set_from_positions(self, x_min: float, x_max: float,
                           y_min: float, y_max: float) -> None:
        """
        Calculate tile grid from DualPositionPanel positions.

        Called when Position A or B XY values change in two-point mode.
        Calculates number of tiles needed to cover the area with current overlap.

        Args:
            x_min: Minimum X position in mm
            x_max: Maximum X position in mm
            y_min: Minimum Y position in mm
            y_max: Maximum Y position in mm
        """
        x_range_mm = abs(x_max - x_min)
        y_range_mm = abs(y_max - y_min)

        # FOV is tile size (from camera)
        fov_mm = self._tile_size_um / 1000.0
        overlap_factor = 1.0 - self._overlap.value() / 100.0
        effective_step = fov_mm * overlap_factor

        # Calculate tiles needed to cover range
        # Formula: tiles = ceil(range / effective_step) + 1
        # But at minimum we need 1 tile
        if effective_step > 0:
            tiles_x = max(1, int(x_range_mm / effective_step) + 1)
            tiles_y = max(1, int(y_range_mm / effective_step) + 1)
        else:
            tiles_x = 1
            tiles_y = 1

        # Update spinboxes
        self._tiles_x.blockSignals(True)
        self._tiles_y.blockSignals(True)
        self._tiles_x.setValue(tiles_x)
        self._tiles_y.setValue(tiles_y)
        self._tiles_x.blockSignals(False)
        self._tiles_y.blockSignals(False)

        self._update_calculations()

        self._logger.debug(f"Tiles from positions: X range={x_range_mm:.2f}mm -> {tiles_x} tiles, "
                          f"Y range={y_range_mm:.2f}mm -> {tiles_y} tiles "
                          f"(FOV={fov_mm:.2f}mm, overlap={self._overlap.value():.1f}%)")
