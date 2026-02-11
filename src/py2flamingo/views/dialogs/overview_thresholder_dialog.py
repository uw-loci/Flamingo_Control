"""2D Overview Thresholder Dialog.

Dialog for automatically pre-selecting tiles in the LED 2D Overview
based on image analysis. Detects "sample" vs "background" tiles using:
- Variance analysis (low variance = background)
- Edge/focus detection (high edge content = sample)
- Intensity thresholding

Background tiles typically have low variability and uniform intensity.
Sample tiles have more texture, edges, and varying intensity.
"""

import logging
from typing import Optional, List, Tuple, Set
import numpy as np

from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QSlider, QComboBox,
    QCheckBox, QPushButton, QGroupBox, QFormLayout, QSpinBox,
    QDoubleSpinBox, QFrame, QSizePolicy, QMessageBox
)
from py2flamingo.services.window_geometry_manager import PersistentDialog
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage, QPainter, QPen, QColor, QIcon

logger = logging.getLogger(__name__)


def calculate_tile_variance(image: np.ndarray, tiles_x: int, tiles_y: int) -> np.ndarray:
    """Calculate variance for each tile in the image.

    Args:
        image: Input image (grayscale or RGB)
        tiles_x: Number of tiles in X
        tiles_y: Number of tiles in Y

    Returns:
        2D array of variance values [tiles_y, tiles_x]
    """
    # Convert to grayscale if needed
    if len(image.shape) == 3:
        gray = np.mean(image, axis=2)
    else:
        gray = image.astype(np.float64)

    h, w = gray.shape
    tile_h = h // tiles_y
    tile_w = w // tiles_x

    variances = np.zeros((tiles_y, tiles_x))

    for ty in range(tiles_y):
        for tx in range(tiles_x):
            y_start = ty * tile_h
            y_end = (ty + 1) * tile_h
            x_start = tx * tile_w
            x_end = (tx + 1) * tile_w

            tile = gray[y_start:y_end, x_start:x_end]
            variances[ty, tx] = np.var(tile)

    return variances


def calculate_tile_edges(image: np.ndarray, tiles_x: int, tiles_y: int) -> np.ndarray:
    """Calculate edge content for each tile using Laplacian variance.

    Higher values indicate more edges/texture (likely sample, not background).

    Args:
        image: Input image (grayscale or RGB)
        tiles_x: Number of tiles in X
        tiles_y: Number of tiles in Y

    Returns:
        2D array of edge scores [tiles_y, tiles_x]
    """
    # Convert to grayscale if needed
    if len(image.shape) == 3:
        gray = np.mean(image, axis=2).astype(np.float64)
    else:
        gray = image.astype(np.float64)

    h, w = gray.shape
    tile_h = h // tiles_y
    tile_w = w // tiles_x

    # Laplacian kernel for edge detection
    kernel = np.array([[0, 1, 0],
                       [1, -4, 1],
                       [0, 1, 0]], dtype=np.float64)

    edge_scores = np.zeros((tiles_y, tiles_x))

    for ty in range(tiles_y):
        for tx in range(tiles_x):
            y_start = ty * tile_h
            y_end = (ty + 1) * tile_h
            x_start = tx * tile_w
            x_end = (tx + 1) * tile_w

            tile = gray[y_start:y_end, x_start:x_end]

            # Apply Laplacian via convolution (simple version)
            # Pad tile for convolution
            padded = np.pad(tile, 1, mode='edge')
            laplacian = np.zeros_like(tile)

            for i in range(tile.shape[0]):
                for j in range(tile.shape[1]):
                    region = padded[i:i+3, j:j+3]
                    laplacian[i, j] = np.sum(region * kernel)

            edge_scores[ty, tx] = np.var(laplacian)

    return edge_scores


def calculate_tile_intensity(image: np.ndarray, tiles_x: int, tiles_y: int) -> np.ndarray:
    """Calculate mean intensity for each tile.

    Args:
        image: Input image (grayscale or RGB)
        tiles_x: Number of tiles in X
        tiles_y: Number of tiles in Y

    Returns:
        2D array of mean intensity values [tiles_y, tiles_x]
    """
    # Convert to grayscale if needed
    if len(image.shape) == 3:
        gray = np.mean(image, axis=2)
    else:
        gray = image.astype(np.float64)

    h, w = gray.shape
    tile_h = h // tiles_y
    tile_w = w // tiles_x

    intensities = np.zeros((tiles_y, tiles_x))

    for ty in range(tiles_y):
        for tx in range(tiles_x):
            y_start = ty * tile_h
            y_end = (ty + 1) * tile_h
            x_start = tx * tile_w
            x_end = (tx + 1) * tile_w

            tile = gray[y_start:y_end, x_start:x_end]
            intensities[ty, tx] = np.mean(tile)

    return intensities


class OverviewThresholderDialog(PersistentDialog):
    """Dialog for automatic tile selection based on image analysis.

    Analyzes the 2D Overview image to detect which tiles contain
    sample (vs background) based on variance, edge content, and intensity.

    Signals:
        selection_ready: Emitted with set of (tile_x, tile_y) tuples to select
    """

    selection_ready = pyqtSignal(set)  # Set of (tile_x, tile_y) tuples

    def __init__(self,
                 image: np.ndarray,
                 tiles_x: int,
                 tiles_y: int,
                 parent=None):
        """Initialize the thresholder dialog.

        Args:
            image: The 2D Overview image as numpy array
            tiles_x: Number of tiles in X dimension
            tiles_y: Number of tiles in Y dimension
            parent: Parent widget
        """
        super().__init__(parent)
        self._image = image
        self._tiles_x = tiles_x
        self._tiles_y = tiles_y

        # Pre-calculate metrics for all tiles
        self._variances: Optional[np.ndarray] = None
        self._edge_scores: Optional[np.ndarray] = None
        self._intensities: Optional[np.ndarray] = None

        # Current selection
        self._selected_tiles: Set[Tuple[int, int]] = set()

        self.setWindowTitle("2D Overview Tile Thresholder")
        self.setMinimumSize(800, 600)

        self._setup_ui()
        self._calculate_metrics()
        self._update_preview()

    def _setup_ui(self):
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)

        # Main content: image preview and controls side by side
        content_layout = QHBoxLayout()

        # Left: Image preview with tile overlay
        preview_frame = QFrame()
        preview_frame.setFrameStyle(QFrame.StyledPanel)
        preview_layout = QVBoxLayout(preview_frame)

        self._preview_label = QLabel()
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setMinimumSize(400, 400)
        self._preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        preview_layout.addWidget(self._preview_label)

        # Selection info
        self._info_label = QLabel("0 tiles selected")
        self._info_label.setAlignment(Qt.AlignCenter)
        preview_layout.addWidget(self._info_label)

        content_layout.addWidget(preview_frame, stretch=2)

        # Right: Controls
        controls_frame = QFrame()
        controls_frame.setMaximumWidth(350)
        controls_layout = QVBoxLayout(controls_frame)

        # Method selection
        method_group = QGroupBox("Detection Method")
        method_layout = QFormLayout()

        self._method_combo = QComboBox()
        self._method_combo.addItem("Variance (low = background)", "variance")
        self._method_combo.addItem("Edge Detection (high = sample)", "edge")
        self._method_combo.addItem("Intensity Range", "intensity")
        self._method_combo.addItem("Combined (Variance + Edge)", "combined")
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)
        method_layout.addRow("Method:", self._method_combo)

        method_group.setLayout(method_layout)
        controls_layout.addWidget(method_group)

        # Variance settings
        self._variance_group = QGroupBox("Variance Threshold")
        variance_layout = QFormLayout()

        self._variance_slider = QSlider(Qt.Horizontal)
        self._variance_slider.setRange(0, 1000)
        self._variance_slider.setValue(100)
        self._variance_slider.valueChanged.connect(self._update_preview)
        variance_layout.addRow("Min variance:", self._variance_slider)

        self._variance_value = QLabel("100")
        variance_layout.addRow("Value:", self._variance_value)

        self._variance_group.setLayout(variance_layout)
        controls_layout.addWidget(self._variance_group)

        # Edge settings
        self._edge_group = QGroupBox("Edge Detection Threshold")
        edge_layout = QFormLayout()

        self._edge_slider = QSlider(Qt.Horizontal)
        self._edge_slider.setRange(0, 10000)
        self._edge_slider.setValue(500)
        self._edge_slider.valueChanged.connect(self._update_preview)
        edge_layout.addRow("Min edge score:", self._edge_slider)

        self._edge_value = QLabel("500")
        edge_layout.addRow("Value:", self._edge_value)

        self._edge_group.setLayout(edge_layout)
        controls_layout.addWidget(self._edge_group)
        self._edge_group.hide()

        # Intensity settings
        self._intensity_group = QGroupBox("Intensity Range")
        intensity_layout = QFormLayout()

        self._intensity_min = QSlider(Qt.Horizontal)
        self._intensity_min.setRange(0, 255)
        self._intensity_min.setValue(20)
        self._intensity_min.valueChanged.connect(self._update_preview)
        intensity_layout.addRow("Min intensity:", self._intensity_min)

        self._intensity_max = QSlider(Qt.Horizontal)
        self._intensity_max.setRange(0, 255)
        self._intensity_max.setValue(255)
        self._intensity_max.valueChanged.connect(self._update_preview)
        intensity_layout.addRow("Max intensity:", self._intensity_max)

        self._intensity_group.setLayout(intensity_layout)
        controls_layout.addWidget(self._intensity_group)
        self._intensity_group.hide()

        # Options
        options_group = QGroupBox("Options")
        options_layout = QVBoxLayout()

        self._invert_check = QCheckBox("Invert selection")
        self._invert_check.stateChanged.connect(self._update_preview)
        options_layout.addWidget(self._invert_check)

        self._preview_check = QCheckBox("Show preview overlay")
        self._preview_check.setChecked(True)
        self._preview_check.stateChanged.connect(self._update_preview)
        options_layout.addWidget(self._preview_check)

        options_group.setLayout(options_layout)
        controls_layout.addWidget(options_group)

        # Statistics
        stats_group = QGroupBox("Tile Statistics")
        stats_layout = QFormLayout()

        self._stats_variance = QLabel("-")
        stats_layout.addRow("Variance range:", self._stats_variance)

        self._stats_edge = QLabel("-")
        stats_layout.addRow("Edge range:", self._stats_edge)

        self._stats_intensity = QLabel("-")
        stats_layout.addRow("Intensity range:", self._stats_intensity)

        stats_group.setLayout(stats_layout)
        controls_layout.addWidget(stats_group)

        controls_layout.addStretch()

        content_layout.addWidget(controls_frame, stretch=1)
        layout.addLayout(content_layout)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        apply_btn = QPushButton("Apply Selection")
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self._on_apply)
        button_layout.addWidget(apply_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

    def _calculate_metrics(self):
        """Pre-calculate all metrics for the tiles."""
        logger.info(f"Calculating tile metrics for {self._tiles_x}x{self._tiles_y} grid...")

        self._variances = calculate_tile_variance(
            self._image, self._tiles_x, self._tiles_y
        )
        self._edge_scores = calculate_tile_edges(
            self._image, self._tiles_x, self._tiles_y
        )
        self._intensities = calculate_tile_intensity(
            self._image, self._tiles_x, self._tiles_y
        )

        # Update statistics display
        if self._variances is not None:
            v_min, v_max = self._variances.min(), self._variances.max()
            self._stats_variance.setText(f"{v_min:.1f} - {v_max:.1f}")
            # Set slider range based on actual data
            self._variance_slider.setRange(0, int(v_max * 1.1) + 1)
            self._variance_slider.setValue(int(v_min + (v_max - v_min) * 0.2))

        if self._edge_scores is not None:
            e_min, e_max = self._edge_scores.min(), self._edge_scores.max()
            self._stats_edge.setText(f"{e_min:.1f} - {e_max:.1f}")
            self._edge_slider.setRange(0, int(e_max * 1.1) + 1)
            self._edge_slider.setValue(int(e_min + (e_max - e_min) * 0.3))

        if self._intensities is not None:
            i_min, i_max = self._intensities.min(), self._intensities.max()
            self._stats_intensity.setText(f"{i_min:.1f} - {i_max:.1f}")

        logger.info("Tile metrics calculated")

    def _on_method_changed(self, index: int):
        """Handle detection method change."""
        method = self._method_combo.currentData()

        # Show/hide relevant control groups
        self._variance_group.setVisible(method in ("variance", "combined"))
        self._edge_group.setVisible(method in ("edge", "combined"))
        self._intensity_group.setVisible(method == "intensity")

        self._update_preview()

    def _get_selected_tiles(self) -> Set[Tuple[int, int]]:
        """Calculate which tiles should be selected based on current settings."""
        method = self._method_combo.currentData()
        selected = set()

        if method == "variance":
            threshold = self._variance_slider.value()
            self._variance_value.setText(str(threshold))

            # Select tiles with variance ABOVE threshold (not background)
            for ty in range(self._tiles_y):
                for tx in range(self._tiles_x):
                    if self._variances[ty, tx] >= threshold:
                        selected.add((tx, ty))

        elif method == "edge":
            threshold = self._edge_slider.value()
            self._edge_value.setText(str(threshold))

            # Select tiles with edge score ABOVE threshold
            for ty in range(self._tiles_y):
                for tx in range(self._tiles_x):
                    if self._edge_scores[ty, tx] >= threshold:
                        selected.add((tx, ty))

        elif method == "intensity":
            min_int = self._intensity_min.value()
            max_int = self._intensity_max.value()

            # Select tiles with mean intensity in range
            for ty in range(self._tiles_y):
                for tx in range(self._tiles_x):
                    intensity = self._intensities[ty, tx]
                    if min_int <= intensity <= max_int:
                        selected.add((tx, ty))

        elif method == "combined":
            var_threshold = self._variance_slider.value()
            edge_threshold = self._edge_slider.value()
            self._variance_value.setText(str(var_threshold))
            self._edge_value.setText(str(edge_threshold))

            # Select tiles that pass EITHER threshold
            for ty in range(self._tiles_y):
                for tx in range(self._tiles_x):
                    if (self._variances[ty, tx] >= var_threshold or
                        self._edge_scores[ty, tx] >= edge_threshold):
                        selected.add((tx, ty))

        # Apply inversion if checked
        if self._invert_check.isChecked():
            all_tiles = {(tx, ty) for tx in range(self._tiles_x)
                        for ty in range(self._tiles_y)}
            selected = all_tiles - selected

        return selected

    def _update_preview(self):
        """Update the preview image with selection overlay."""
        # Get current selection
        self._selected_tiles = self._get_selected_tiles()

        # Update info label
        total_tiles = self._tiles_x * self._tiles_y
        self._info_label.setText(
            f"{len(self._selected_tiles)} / {total_tiles} tiles selected"
        )

        # Create preview image
        self._draw_preview()

    def _draw_preview(self):
        """Draw the preview image with tile overlay."""
        if self._image is None:
            return

        # Convert numpy image to QPixmap
        if len(self._image.shape) == 3:
            h, w, c = self._image.shape
            if c == 4:
                fmt = QImage.Format_RGBA8888
            else:
                fmt = QImage.Format_RGB888

            # Ensure contiguous array
            img_data = np.ascontiguousarray(self._image)
            qimg = QImage(img_data.data, w, h, img_data.strides[0], fmt)
        else:
            h, w = self._image.shape
            # Convert grayscale to RGB for display
            rgb = np.stack([self._image] * 3, axis=-1).astype(np.uint8)
            rgb = np.ascontiguousarray(rgb)
            qimg = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888)

        # Scale to fit preview area while maintaining aspect ratio
        preview_size = self._preview_label.size()
        pixmap = QPixmap.fromImage(qimg)

        # Draw overlay if enabled
        if self._preview_check.isChecked() and self._selected_tiles:
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)

            tile_w = w / self._tiles_x
            tile_h = h / self._tiles_y

            # Draw selected tiles with green outline
            painter.setBrush(Qt.NoBrush)
            pen = QPen(QColor(0, 255, 0, 200))
            pen.setWidth(max(2, int(min(tile_w, tile_h) / 20)))
            painter.setPen(pen)

            for tx, ty in self._selected_tiles:
                x = int(tx * tile_w)
                y = int(ty * tile_h)
                painter.drawRect(x, y, int(tile_w), int(tile_h))

            painter.end()

        # Scale to fit
        scaled = pixmap.scaled(
            preview_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        self._preview_label.setPixmap(scaled)

    def _on_apply(self):
        """Apply the current selection."""
        if not self._selected_tiles:
            result = QMessageBox.question(
                self, "No Selection",
                "No tiles are selected. Apply anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if result != QMessageBox.Yes:
                return

        logger.info(f"Applying thresholder selection: {len(self._selected_tiles)} tiles")
        self.selection_ready.emit(self._selected_tiles)
        self.accept()

    def get_selected_tiles(self) -> Set[Tuple[int, int]]:
        """Get the current tile selection.

        Returns:
            Set of (tile_x, tile_y) tuples
        """
        return self._selected_tiles.copy()
