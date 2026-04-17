"""LED 2D Overview Result Window.

Displays the results of an LED 2D Overview scan, showing two side-by-side
images at different rotation angles with coordinate overlays.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
from PyQt5.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QAction,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from py2flamingo.services.window_geometry_manager import PersistentWidget

logger = logging.getLogger(__name__)

from py2flamingo.views.widgets.zoomable_image_label import ZoomableImageLabel
from py2flamingo.visualization.zarr_2d_session import (
    ZARR_AVAILABLE,
    detect_session_format,
    load_2d_zarr_session,
    load_2d_zarr_session_lazy,
    save_2d_zarr_session,
)


class ImagePanel(QWidget):
    """Widget displaying a single image with coordinate overlay and zoom/pan."""

    # Max dimension (pixels) for the display copy of the image.
    # Full-res images (e.g. 26624×22528) are downsampled to this size
    # before converting to QPixmap, which makes load and contrast
    # adjustments ~40× faster while preserving enough detail for the
    # overview use case.
    MAX_DISPLAY_DIM = 4096

    # Signal emitted when tile selection changes
    selection_changed = pyqtSignal()

    # Signal emitted when a tile is right-clicked (tile_x_idx, tile_y_idx)
    tile_right_clicked = pyqtSignal(int, int)

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)

        self._title = title
        self._image: Optional[np.ndarray] = None
        self._display_image: Optional[np.ndarray] = None  # Downsampled for display
        self._display_scale: int = 1  # Stride factor used for downsampling
        self._pixmap: Optional[QPixmap] = None
        self._base_pixmap: Optional[QPixmap] = (
            None  # Cached base (image + grid + coords, no selections)
        )
        self._show_grid = True
        self._tiles_x = 0
        self._tiles_y = 0
        self._tile_coords: List[tuple] = (
            []
        )  # (x, y, tile_x_idx, tile_y_idx) for each tile
        self._invert_x = False  # Whether X-axis is inverted for display
        self._selected_tiles: set = set()  # Set of (tile_x_idx, tile_y_idx) tuples
        self._tile_results: List = []  # Store TileResult objects for retrieval
        # Stride info for overlapping tiles (None = equal grid, no overlap)
        self._tile_stride_x: Optional[int] = None  # Pixels between tile origins in X
        self._tile_stride_y: Optional[int] = None  # Pixels between tile origins in Y
        self._tile_w: Optional[int] = None  # Actual tile width in pixels
        self._tile_h: Optional[int] = None  # Actual tile height in pixels

        # Contrast settings - slider values (0-1000 range for precision)
        self._contrast_min_slider = 0  # Maps to _image_min
        self._contrast_max_slider = 1000  # Maps to _image_max_pct
        # Actual image intensity range (set when image is loaded)
        self._image_min = 0.0
        self._image_max_pct = 255.0  # 99.5th percentile

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # Title label
        self.title_label = QLabel(self._title)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        layout.addWidget(self.title_label)

        # Hint label for user interaction
        self.hint_label = QLabel(
            "Click to select, Shift+drag to select area, Right-click to move to Z"
        )
        self.hint_label.setAlignment(Qt.AlignCenter)
        self.hint_label.setStyleSheet(
            "color: #888; font-size: 9pt; font-style: italic;"
        )
        layout.addWidget(self.hint_label)

        # Scroll area with zoomable image
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)  # Don't auto-resize for zoom
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setMinimumSize(200, 200)
        self.scroll_area.setStyleSheet("background-color: #2a2a2a;")  # Dark background

        self.image_label = ZoomableImageLabel()
        self.image_label.set_scroll_area(self.scroll_area)  # Connect for panning
        self.image_label.tile_clicked.connect(self._on_tile_clicked)
        self.image_label.tile_right_clicked.connect(self._on_tile_right_clicked)
        self.image_label.tiles_rect_selected.connect(self._on_tiles_rect_selected)

        self.scroll_area.setWidget(self.image_label)
        layout.addWidget(self.scroll_area, stretch=1)

        # Zoom controls row
        zoom_layout = QHBoxLayout()
        zoom_layout.setSpacing(4)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setStyleSheet("color: gray; font-size: 9pt;")
        zoom_layout.addWidget(self.zoom_label)

        zoom_layout.addStretch()

        # Contrast sliders
        contrast_label = QLabel("Contrast:")
        contrast_label.setStyleSheet("color: gray; font-size: 9pt;")
        zoom_layout.addWidget(contrast_label)

        self._min_slider = QSlider(Qt.Horizontal)
        self._min_slider.setRange(0, 1000)
        self._min_slider.setValue(0)
        self._min_slider.setFixedWidth(80)
        self._min_slider.setToolTip("Black point (minimum display value)")
        self._min_slider.valueChanged.connect(self._on_contrast_changed)
        zoom_layout.addWidget(self._min_slider)

        self._contrast_label = QLabel("0-100%")
        self._contrast_label.setStyleSheet("color: gray; font-size: 9pt;")
        self._contrast_label.setFixedWidth(55)
        zoom_layout.addWidget(self._contrast_label)

        self._max_slider = QSlider(Qt.Horizontal)
        self._max_slider.setRange(0, 1000)
        self._max_slider.setValue(1000)
        self._max_slider.setFixedWidth(80)
        self._max_slider.setToolTip("White point (maximum display value)")
        self._max_slider.valueChanged.connect(self._on_contrast_changed)
        zoom_layout.addWidget(self._max_slider)

        zoom_layout.addStretch()

        fit_btn = QPushButton("Fit")
        fit_btn.setFixedWidth(40)
        fit_btn.setToolTip("Fit image to view")
        fit_btn.clicked.connect(self._fit_to_view)
        zoom_layout.addWidget(fit_btn)

        reset_btn = QPushButton("1:1")
        reset_btn.setFixedWidth(40)
        reset_btn.setToolTip("Reset to 100% zoom")
        reset_btn.clicked.connect(self._reset_zoom)
        zoom_layout.addWidget(reset_btn)

        layout.addLayout(zoom_layout)

        self.setLayout(layout)

    def set_title(self, title: str):
        """Set the panel title."""
        self._title = title
        self.title_label.setText(title)

    def _fit_to_view(self):
        """Fit image to scroll area viewport size."""
        # Use viewport size, not scroll area size
        viewport_size = self.scroll_area.viewport().size()
        self.image_label.fit_to_view(viewport_size)
        self._update_zoom_label()

    def _reset_zoom(self):
        """Reset to 100% zoom."""
        self.image_label.reset_zoom()
        self._update_zoom_label()

    def _update_zoom_label(self):
        """Update zoom percentage display."""
        zoom_pct = int(self.image_label.zoom_level * 100)
        self.zoom_label.setText(f"{zoom_pct}%")

    def _on_contrast_changed(self):
        """Handle contrast slider change - redraw image with new contrast."""
        self._contrast_min_slider = self._min_slider.value()
        self._contrast_max_slider = self._max_slider.value()

        # Ensure min < max (swap if needed)
        if self._contrast_min_slider >= self._contrast_max_slider:
            if self.sender() == self._min_slider:
                self._contrast_min_slider = self._contrast_max_slider - 1
                self._min_slider.blockSignals(True)
                self._min_slider.setValue(self._contrast_min_slider)
                self._min_slider.blockSignals(False)
            else:
                self._contrast_max_slider = self._contrast_min_slider + 1
                self._max_slider.blockSignals(True)
                self._max_slider.setValue(self._contrast_max_slider)
                self._max_slider.blockSignals(False)

        self._update_contrast_label()

        # Redraw image with new contrast settings
        if self._image is not None:
            self._invalidate_base_pixmap()
            self._redraw_overlay()

    def _update_contrast_label(self):
        """Update the contrast percentage label."""
        min_pct = int(self._contrast_min_slider / 10)
        max_pct = int(self._contrast_max_slider / 10)
        self._contrast_label.setText(f"{min_pct}-{max_pct}%")

    def set_tile_stride(
        self,
        stride_x: int,
        stride_y: int,
        tile_w: int,
        tile_h: int,
    ):
        """Set tile stride for overlapping tiles.

        When tiles overlap, the distance between tile origins (stride)
        is less than the tile size. This affects grid line drawing
        and click-to-tile detection.

        Args:
            stride_x: Pixels between tile origins in X.
            stride_y: Pixels between tile origins in Y.
            tile_w: Actual tile width in pixels.
            tile_h: Actual tile height in pixels.
        """
        self._tile_stride_x = stride_x
        self._tile_stride_y = stride_y
        self._tile_w = tile_w
        self._tile_h = tile_h

        # Apply display downsampling to stride values for the ZoomableImageLabel
        ds = self._display_scale
        self.image_label.set_tile_stride(
            stride_x // ds, stride_y // ds, tile_w // ds, tile_h // ds
        )

        # Rebuild overlay with new stride
        self._invalidate_base_pixmap()
        if self._image is not None and self._show_grid:
            self._rebuild_base_pixmap()
            self._pixmap = self._base_pixmap.copy()
            self._draw_selection_overlay()
            self.image_label.setPixmap(self._pixmap)

    def set_image(
        self, image: Optional[np.ndarray], tiles_x: int = 0, tiles_y: int = 0
    ):
        """Set the image to display.

        Args:
            image: Numpy array image (grayscale or RGB)
            tiles_x: Number of tiles in X dimension (for grid overlay)
            tiles_y: Number of tiles in Y dimension (for grid overlay)
        """
        self._image = image
        self._tiles_x = tiles_x
        self._tiles_y = tiles_y
        # Reset stride info (caller should call set_tile_stride after if needed)
        self._tile_stride_x = None
        self._tile_stride_y = None
        self._tile_w = None
        self._tile_h = None

        if image is not None:
            # Downsample large images for display performance.
            # A 26624×22528 image takes ~10s to convert to QPixmap at full res;
            # at 4096px max dim it takes <0.5s with no visible quality loss.
            max_dim = max(image.shape[0], image.shape[1])
            import math

            self._display_scale = max(1, math.ceil(max_dim / self.MAX_DISPLAY_DIM))
            if self._display_scale > 1:
                self._display_image = image[
                    :: self._display_scale, :: self._display_scale
                ].copy()
                logger.info(
                    f"ImagePanel.set_image: image shape={image.shape}, "
                    f"downsampled {self._display_scale}x to {self._display_image.shape}, "
                    f"tiles={tiles_x}x{tiles_y}"
                )
            else:
                self._display_image = image
                logger.info(
                    f"ImagePanel.set_image: image shape={image.shape}, tiles={tiles_x}x{tiles_y}, "
                    f"existing coords={len(self._tile_coords)}, invert_x={self._invert_x}"
                )

            # Calculate image intensity range for contrast sliders
            # Use the display image (representative subsample) for speed
            display = self._display_image
            if len(display.shape) == 2:
                flat = display.ravel()
            else:
                flat = (
                    display[:, :, 0].ravel()
                    if display.shape[2] >= 1
                    else display.ravel()
                )

            self._image_min = float(np.min(flat))
            self._image_max_pct = float(np.percentile(flat, 99.5))

            # Ensure min < max
            if self._image_max_pct <= self._image_min:
                self._image_max_pct = self._image_min + 1

            logger.debug(
                f"Contrast range: min={self._image_min:.1f}, 99.5%={self._image_max_pct:.1f}"
            )

            # Reset sliders to full range
            self._min_slider.blockSignals(True)
            self._max_slider.blockSignals(True)
            self._min_slider.setValue(0)
            self._max_slider.setValue(1000)
            self._contrast_min_slider = 0
            self._contrast_max_slider = 1000
            self._min_slider.blockSignals(False)
            self._max_slider.blockSignals(False)
            self._update_contrast_label()

        # Invalidate cached base pixmap since image/grid changed
        self._invalidate_base_pixmap()

        # Update image label's tile grid for click detection
        self.image_label.set_tile_grid(tiles_x, tiles_y, self._invert_x)

        if image is None:
            self._pixmap = None
            self.image_label.clear()
            return

        # Build base pixmap with grid overlay
        self._rebuild_base_pixmap()

        # Draw selections on top
        self._pixmap = self._base_pixmap.copy()
        self._draw_selection_overlay()
        self.image_label.setPixmap(self._pixmap)

        # Note: Auto-fit is now handled by LED2DOverviewResultWindow.showEvent()
        # which triggers after the window is fully visible

        self._update_zoom_label()

    def set_tile_coordinates(self, coords: List[tuple], invert_x: bool = False):
        """Set tile coordinate data for overlay.

        Args:
            coords: List of (x, y, tile_x_idx, tile_y_idx) tuples for each tile.
                    The tile indices are used to position the labels correctly
                    regardless of the order tiles were captured (e.g., serpentine).
            invert_x: Whether X-axis is inverted for display (low X on right)
        """
        self._tile_coords = coords
        self._invert_x = invert_x

        # Debug logging for tile coordinate issues
        if coords:
            x_indices = [c[2] for c in coords if len(c) >= 4]
            y_indices = [c[3] for c in coords if len(c) >= 4]
            max_x = max(x_indices) if x_indices else -1
            max_y = max(y_indices) if y_indices else -1
            logger.info(
                f"ImagePanel.set_tile_coordinates: {len(coords)} coords, "
                f"tile indices up to ({max_x}, {max_y}), invert_x={invert_x}, "
                f"expected grid={self._tiles_x}x{self._tiles_y}"
            )

        # Invalidate cached base pixmap since coordinates changed
        self._invalidate_base_pixmap()

        # Update image label's tile grid for click detection
        self.image_label.set_tile_grid(self._tiles_x, self._tiles_y, invert_x)

        # Forward coordinates to image label for on-demand rendering when zoomed in
        self.image_label.set_tile_coordinates(coords, invert_x)

        if self._image is not None and self._show_grid:
            # Rebuild base pixmap and redraw
            self._rebuild_base_pixmap()
            self._pixmap = self._base_pixmap.copy()
            self._draw_selection_overlay()
            self.image_label.setPixmap(self._pixmap)

    def set_tile_results(self, tile_results: List):
        """Store TileResult objects for retrieval when collecting tiles."""
        self._tile_results = tile_results

    def _on_tile_clicked(self, tile_x_idx: int, tile_y_idx: int):
        """Handle tile click - toggle selection."""
        tile_key = (tile_x_idx, tile_y_idx)
        if tile_key in self._selected_tiles:
            self._selected_tiles.discard(tile_key)
            logger.debug(f"Deselected tile {tile_key}")
        else:
            self._selected_tiles.add(tile_key)
            logger.debug(f"Selected tile {tile_key}")

        # Redraw to show selection
        self._redraw_overlay()
        self.selection_changed.emit()

    def _on_tile_right_clicked(self, tile_x_idx: int, tile_y_idx: int):
        """Handle tile right-click - propagate signal upward for move to center Z."""
        logger.debug(f"Tile right-clicked in panel: ({tile_x_idx}, {tile_y_idx})")
        self.tile_right_clicked.emit(tile_x_idx, tile_y_idx)

    def _on_tiles_rect_selected(self, tile_set: set):
        """Handle Shift+drag rectangle selection - add all tiles to selection."""
        self._selected_tiles.update(tile_set)
        logger.debug(
            f"Rectangle selected {len(tile_set)} tiles, total now {len(self._selected_tiles)}"
        )
        self._redraw_overlay()
        self.selection_changed.emit()

    def _redraw_overlay(self, interactive: bool = False):
        """Redraw the selection overlay using cached base pixmap (fast path).

        Args:
            interactive: If True, use fast scaling initially and defer
                smooth scaling. Use for rapid updates (tile clicks, slider drags).
        """
        if self._image is None:
            return

        # Use cached base pixmap if available, otherwise rebuild it
        if self._base_pixmap is None:
            self._rebuild_base_pixmap()

        # Copy base pixmap and draw selections on top (fast)
        self._pixmap = self._base_pixmap.copy()
        self._draw_selection_overlay()
        self.image_label.setPixmap(self._pixmap, interactive=interactive)

    def _rebuild_base_pixmap(self):
        """Rebuild the cached base pixmap (image + grid + coordinates, no selections)."""
        if self._display_image is None:
            return
        self._base_pixmap = self._array_to_pixmap(self._display_image)
        if self._show_grid and self._tiles_x > 0 and self._tiles_y > 0:
            self._draw_base_overlay()
        logger.debug(
            f"Rebuilt base pixmap: {self._base_pixmap.width()}x{self._base_pixmap.height()}"
        )

    def _invalidate_base_pixmap(self):
        """Invalidate the cached base pixmap (call when image/grid/coords change)."""
        self._base_pixmap = None

    def select_all_tiles(self):
        """Select all tiles."""
        for y in range(self._tiles_y):
            for x in range(self._tiles_x):
                self._selected_tiles.add((x, y))
        self._redraw_overlay()
        self.selection_changed.emit()

    def clear_selection(self):
        """Clear all tile selections."""
        self._selected_tiles.clear()
        self._redraw_overlay()
        self.selection_changed.emit()

    def get_selected_tile_count(self) -> int:
        """Get the number of selected tiles."""
        return len(self._selected_tiles)

    def get_selected_tiles(self) -> List:
        """Get TileResult objects for selected tiles."""
        selected = []
        for tile in self._tile_results:
            key = (tile.tile_x_idx, tile.tile_y_idx)
            if key in self._selected_tiles:
                selected.append(tile)
        return selected

    def set_show_grid(self, show: bool):
        """Enable or disable grid overlay."""
        self._show_grid = show

        # Invalidate base pixmap since grid visibility changed
        self._invalidate_base_pixmap()

        if self._image is not None:
            # Rebuild base pixmap and redraw
            self._rebuild_base_pixmap()
            self._pixmap = self._base_pixmap.copy()
            self._draw_selection_overlay()
            self.image_label.setPixmap(self._pixmap)

    def _array_to_pixmap(self, image: np.ndarray) -> QPixmap:
        """Convert numpy array to QPixmap with contrast adjustment."""
        if len(image.shape) == 2:
            # Grayscale
            h, w = image.shape
            bytes_per_line = w

            # Apply contrast adjustment using slider values
            # Slider values (0-1000) map to the range [_image_min, _image_max_pct]
            intensity_range = self._image_max_pct - self._image_min
            display_min = (
                self._image_min + (self._contrast_min_slider / 1000.0) * intensity_range
            )
            display_max = (
                self._image_min + (self._contrast_max_slider / 1000.0) * intensity_range
            )

            # Ensure valid range
            if display_max <= display_min:
                display_max = display_min + 1

            # Clip and rescale to 8-bit
            img_float = image.astype(np.float32)
            img_clipped = np.clip(img_float, display_min, display_max)
            img_8bit = (
                (img_clipped - display_min) / (display_max - display_min) * 255
            ).astype(np.uint8)

            # Ensure contiguous array for QImage
            img_8bit = np.ascontiguousarray(img_8bit)
            qimg = QImage(img_8bit.data, w, h, bytes_per_line, QImage.Format_Grayscale8)
        elif len(image.shape) == 3:
            h, w, c = image.shape
            if c == 4:  # RGBA
                bytes_per_line = w * 4
                qimg = QImage(image.data, w, h, bytes_per_line, QImage.Format_RGBA8888)
            else:  # RGB
                bytes_per_line = w * 3
                # Ensure contiguous
                img_cont = np.ascontiguousarray(image)
                qimg = QImage(img_cont.data, w, h, bytes_per_line, QImage.Format_RGB888)
        else:
            # Fallback: create blank image
            qimg = QImage(100, 100, QImage.Format_RGB888)
            qimg.fill(QColor(128, 128, 128))

        return QPixmap.fromImage(qimg.copy())  # Copy to ensure data persists

    def _draw_base_overlay(self):
        """Draw grid lines on the base pixmap.

        Coordinate labels are NOT drawn on the base pixmap — they are only
        useful when zoomed in and are rendered on demand by ZoomableImageLabel.
        """
        if self._base_pixmap is None or self._tiles_x <= 0 or self._tiles_y <= 0:
            return

        # Create painter on base pixmap
        painter = QPainter(self._base_pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Grid line pen
        pen = QPen(QColor(255, 255, 0, 180))  # Yellow, semi-transparent
        pen.setWidth(1)
        painter.setPen(pen)

        w = self._base_pixmap.width()
        h = self._base_pixmap.height()

        # Use stride-based grid if available (tiles overlap), else equal-division
        ds = self._display_scale
        if self._tile_stride_x is not None and self._tile_stride_y is not None:
            stride_x = self._tile_stride_x / ds
            stride_y = self._tile_stride_y / ds
        else:
            stride_x = w / self._tiles_x
            stride_y = h / self._tiles_y

        # Draw vertical grid lines at stride intervals
        for i in range(1, self._tiles_x):
            x = int(i * stride_x)
            painter.drawLine(x, 0, x, h)

        # Draw horizontal grid lines at stride intervals
        for i in range(1, self._tiles_y):
            y = int(i * stride_y)
            painter.drawLine(0, y, w, y)

        logger.debug(
            f"_draw_base_overlay: drew grid lines, {self._tiles_x}x{self._tiles_y} tiles, "
            f"stride=({stride_x:.0f}, {stride_y:.0f})"
        )

        painter.end()

    def _draw_selection_overlay(self):
        """Draw selection highlights on the current pixmap (fast, called on every click)."""
        if self._pixmap is None or self._tiles_x <= 0 or self._tiles_y <= 0:
            logger.debug(
                f"_draw_selection_overlay: skipping - pixmap={self._pixmap is not None}, "
                f"tiles={self._tiles_x}x{self._tiles_y}"
            )
            return
        if not self._selected_tiles:
            logger.debug("_draw_selection_overlay: no selected tiles")
            return  # Nothing to draw

        logger.info(
            f"_draw_selection_overlay: drawing {len(self._selected_tiles)} selections, "
            f"tiles={self._tiles_x}x{self._tiles_y}, invert_x={self._invert_x}, "
            f"pixmap={self._pixmap.width()}x{self._pixmap.height()}"
        )

        painter = QPainter(self._pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self._pixmap.width()
        h = self._pixmap.height()

        # Use stride-based positioning if available
        ds = self._display_scale
        if self._tile_stride_x is not None and self._tile_stride_y is not None:
            stride_x = self._tile_stride_x / ds
            stride_y = self._tile_stride_y / ds
            sel_w = self._tile_w / ds if self._tile_w else stride_x
            sel_h = self._tile_h / ds if self._tile_h else stride_y
        else:
            stride_x = w / self._tiles_x
            stride_y = h / self._tiles_y
            sel_w = stride_x
            sel_h = stride_y

        from PyQt5.QtCore import Qt

        # Outline only - no fill for selected tiles
        painter.setBrush(Qt.NoBrush)

        # Calculate line width based on tile size (thicker for larger tiles)
        # Minimum 16px, scales with tile size for maximum visibility
        line_width = max(16, int(min(sel_w, sel_h) / 8))

        # Bright cyan border - 30% opacity (70% transparent)
        selection_pen = QPen(QColor(0, 255, 255, 76))
        selection_pen.setWidth(line_width)
        painter.setPen(selection_pen)

        selections_drawn = 0
        selections_skipped = 0
        for tile_x_idx, tile_y_idx in self._selected_tiles:
            # Calculate display position
            if self._invert_x:
                display_x_idx = (self._tiles_x - 1) - tile_x_idx
            else:
                display_x_idx = tile_x_idx

            x_pos = int(display_x_idx * stride_x)
            y_pos = int(tile_y_idx * stride_y)

            # Check if selection rectangle is within bounds
            if x_pos < 0 or x_pos >= w or y_pos < 0 or y_pos >= h:
                selections_skipped += 1
                logger.warning(
                    f"Selection out of bounds: tile({tile_x_idx},{tile_y_idx}) -> "
                    f"display({display_x_idx},{tile_y_idx}) -> pos({x_pos},{y_pos}), "
                    f"pixmap={w}x{h}"
                )
                continue

            painter.drawRect(x_pos, y_pos, int(sel_w), int(sel_h))
            selections_drawn += 1

        logger.info(
            f"_draw_selection_overlay: drew {selections_drawn}, skipped {selections_skipped}"
        )
        painter.end()

    def get_image(self) -> Optional[np.ndarray]:
        """Get the current image."""
        return self._image


class LED2DOverviewResultWindow(PersistentWidget):
    """Window displaying LED 2D Overview scan results.

    Shows two side-by-side images for the two rotation angles,
    with grid overlays and coordinate information.
    """

    def __init__(
        self,
        results=None,
        config=None,
        preview_mode: bool = False,
        app=None,
        parent=None,
    ):
        """Initialize the result window.

        Args:
            results: List of RotationResult from workflow (None for preview)
            config: ScanConfiguration used for the scan
            preview_mode: If True, show empty grid preview
            app: FlamingoApplication instance for accessing services
            parent: Parent widget
        """
        super().__init__(parent)

        self._results = results or []
        self._config = config
        self._preview_mode = preview_mode
        self._app = app
        self._zarr_root = None  # Zarr group for on-demand dataset loading
        self._session_folder = None  # Path to loaded session folder
        self._session_format = None  # 'zarr' or 'tiff'

        self.setWindowTitle(
            "LED 2D Overview - Results"
            if not preview_mode
            else "LED 2D Overview - Preview"
        )
        self.setMinimumSize(800, 500)

        # Track first show for auto-fit
        self._first_show = True

        self._setup_ui()

        if results:
            self._display_results()
        elif preview_mode:
            self._display_preview()

    def showEvent(self, event):
        """Handle window show - fit images on first display."""
        super().showEvent(event)

        if self._first_show:
            self._first_show = False
            # Fit both panels after window is visible and laid out
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(50, self._fit_all_panels)

    def _fit_all_panels(self):
        """Fit images in all panels to their viewports."""
        self.left_panel._fit_to_view()
        self.right_panel._fit_to_view()
        logger.debug("Auto-fit applied to all panels on first show")

    def _setup_ui(self):
        """Create the window UI."""
        layout = QVBoxLayout()

        # Splitter for side-by-side images
        splitter = QSplitter(Qt.Horizontal)

        # Left panel (first rotation)
        self.left_panel = ImagePanel("Rotation 1")
        self.left_panel.selection_changed.connect(self._on_selection_changed)
        self.left_panel.tile_right_clicked.connect(
            lambda x, y: self._on_tile_right_clicked(x, y, panel="left")
        )
        splitter.addWidget(self.left_panel)

        # Right panel (second rotation)
        self.right_panel = ImagePanel("Rotation 2")
        self.right_panel.selection_changed.connect(self._on_selection_changed)
        self.right_panel.tile_right_clicked.connect(
            lambda x, y: self._on_tile_right_clicked(x, y, panel="right")
        )
        splitter.addWidget(self.right_panel)

        # Set equal split
        splitter.setSizes([400, 400])

        layout.addWidget(splitter, stretch=1)

        # Compact info row (single line)
        info_layout = QHBoxLayout()
        info_layout.setContentsMargins(4, 2, 4, 2)

        self.info_text = QLabel("No scan data")
        self.info_text.setStyleSheet("color: #666; font-size: 9pt;")
        info_layout.addWidget(self.info_text)

        info_layout.addStretch()
        layout.addLayout(info_layout)

        # Button row
        button_layout = QHBoxLayout()

        # Visualization type dropdown
        viz_label = QLabel("Visualization:")
        button_layout.addWidget(viz_label)

        self.viz_combo = QComboBox()
        self.viz_combo.setMinimumWidth(150)
        self._populate_visualization_types()
        self.viz_combo.currentTextChanged.connect(self._on_visualization_changed)
        button_layout.addWidget(self.viz_combo)

        # Selection buttons
        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.setToolTip("Select all tiles for workflow collection")
        self.select_all_btn.clicked.connect(self._on_select_all)
        button_layout.addWidget(self.select_all_btn)

        self.clear_selection_btn = QPushButton("Clear Selection")
        self.clear_selection_btn.setToolTip("Deselect all tiles")
        self.clear_selection_btn.clicked.connect(self._on_clear_selection)
        button_layout.addWidget(self.clear_selection_btn)

        # Auto-select button (thresholder)
        self.auto_select_btn = QPushButton("Auto-Select...")
        self.auto_select_btn.setToolTip(
            "Automatically select tiles containing sample (not background)"
        )
        self.auto_select_btn.clicked.connect(self._on_auto_select)
        button_layout.addWidget(self.auto_select_btn)

        # Collect tiles button
        self.collect_btn = QPushButton("Collect tiles")
        self.collect_btn.setToolTip("Create workflows for selected tiles")
        self.collect_btn.setEnabled(False)  # Disabled until tiles are selected
        self.collect_btn.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                font-weight: bold;
                padding: 4px 12px;
            }
            QPushButton:hover {
                background-color: #2ecc71;
            }
            QPushButton:disabled {
                background-color: #95a5a6;
            }
        """)
        self.collect_btn.clicked.connect(self._on_collect_tiles)
        button_layout.addWidget(self.collect_btn)

        # Selection count label
        self.selection_label = QLabel("0 tiles selected")
        self.selection_label.setStyleSheet("color: #666; font-size: 9pt;")
        button_layout.addWidget(self.selection_label)

        button_layout.addStretch()

        # Toggle grid button
        self.grid_btn = QPushButton("Toggle Grid")
        self.grid_btn.setCheckable(True)
        self.grid_btn.setChecked(True)
        self.grid_btn.clicked.connect(self._toggle_grid)
        button_layout.addWidget(self.grid_btn)

        # Save button with dropdown menu
        self.save_btn = QPushButton("Save ▼")
        self.save_btn.setToolTip(
            "Save scan results.\n"
            "Click for options: Whole Session, Initial image, or Rotated image."
        )

        # Create dropdown menu for save options
        save_menu = QMenu(self)

        # Whole Session - default option (saves everything for later loading)
        self.save_session_action = QAction("Whole Session", self)
        self.save_session_action.setToolTip(
            "Save all images and metadata to a folder (can be loaded later)"
        )
        self.save_session_action.triggered.connect(self._save_session)
        save_menu.addAction(self.save_session_action)

        save_menu.addSeparator()

        # Initial image (rotation 0 / left panel)
        self.save_initial_action = QAction("Initial image", self)
        self.save_initial_action.setToolTip(
            "Save the initial rotation image (left panel)"
        )
        self.save_initial_action.triggered.connect(
            lambda: self._save_single_rotation(0)
        )
        save_menu.addAction(self.save_initial_action)

        # Rotated image (rotation 1 / right panel)
        self.save_rotated_action = QAction("Rotated image", self)
        self.save_rotated_action.setToolTip("Save the rotated image (right panel)")
        self.save_rotated_action.triggered.connect(
            lambda: self._save_single_rotation(1)
        )
        save_menu.addAction(self.save_rotated_action)

        self.save_btn.setMenu(save_menu)
        button_layout.addWidget(self.save_btn)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def _display_results(self):
        """Display scan results."""
        if not self._results:
            self.info_text.setText("No results to display")
            return

        # Get the currently selected visualization type
        viz_type = self.viz_combo.currentData() or "best_focus"

        # Display first rotation
        if len(self._results) >= 1:
            result1 = self._results[0]
            self.left_panel.set_title(f"R = {result1.rotation_angle}°")

            # Calculate actual grid from tiles (may differ from expected if some tiles missing)
            if result1.tiles:
                actual_tiles_x = max(t.tile_x_idx for t in result1.tiles) + 1
                actual_tiles_y = max(t.tile_y_idx for t in result1.tiles) + 1
            else:
                actual_tiles_x = result1.tiles_x
                actual_tiles_y = result1.tiles_y

            # Debug: log expected vs actual tile counts
            expected_tiles = result1.tiles_x * result1.tiles_y
            actual_tiles = len(result1.tiles)
            logger.info(
                f"LEFT PANEL: R={result1.rotation_angle}°, expected grid={result1.tiles_x}x{result1.tiles_y}={expected_tiles}, "
                f"actual grid={actual_tiles_x}x{actual_tiles_y}, actual tiles={actual_tiles}, invert_x={result1.invert_x}"
            )
            if actual_tiles != expected_tiles:
                logger.warning(
                    f"MISMATCH: Expected {expected_tiles} tiles but got {actual_tiles}!"
                )

            img1 = result1.stitched_images.get(viz_type)
            if img1 is not None:
                logger.info(
                    f"LEFT PANEL: image shape={img1.shape}, viz_type={viz_type}"
                )
                # Use actual grid dimensions to match stitched image
                self.left_panel.set_image(img1, actual_tiles_x, actual_tiles_y)
                # Set tile coordinates with grid indices for correct label positioning
                coords = [(t.x, t.y, t.tile_x_idx, t.tile_y_idx) for t in result1.tiles]
                self.left_panel.set_tile_coordinates(coords, invert_x=result1.invert_x)
                # Store tile results for workflow collection
                self.left_panel.set_tile_results(result1.tiles)

        # Display second rotation
        if len(self._results) >= 2:
            result2 = self._results[1]
            self.right_panel.set_title(f"R = {result2.rotation_angle}°")

            # Calculate actual grid from tiles (may differ from expected if some tiles missing)
            if result2.tiles:
                actual_tiles_x = max(t.tile_x_idx for t in result2.tiles) + 1
                actual_tiles_y = max(t.tile_y_idx for t in result2.tiles) + 1
            else:
                actual_tiles_x = result2.tiles_x
                actual_tiles_y = result2.tiles_y

            logger.info(
                f"RIGHT PANEL: R={result2.rotation_angle}°, expected grid={result2.tiles_x}x{result2.tiles_y}, "
                f"actual grid={actual_tiles_x}x{actual_tiles_y}, actual tiles={len(result2.tiles)}, invert_x={result2.invert_x}"
            )

            img2 = result2.stitched_images.get(viz_type)
            if img2 is not None:
                # Use actual grid dimensions to match stitched image
                self.right_panel.set_image(img2, actual_tiles_x, actual_tiles_y)
                # Set tile coordinates with grid indices for correct label positioning
                coords = [(t.x, t.y, t.tile_x_idx, t.tile_y_idx) for t in result2.tiles]
                self.right_panel.set_tile_coordinates(coords, invert_x=result2.invert_x)
                # Store tile results for workflow collection
                self.right_panel.set_tile_results(result2.tiles)

        # Update info text
        self._update_info_text()

    def _display_preview(self):
        """Display preview grid (empty tiles)."""
        if not self._config:
            self.info_text.setText("No configuration for preview")
            return

        # Calculate tile dimensions
        bbox = self._config.bounding_box
        try:
            from py2flamingo.configs.config_loader import get_hardware_config

            fov = get_hardware_config().fov_mm
        except Exception:
            fov = 0.5182  # mm fallback

        tiles_x = max(1, int((bbox.width / fov) + 1))
        tiles_y = max(1, int((bbox.height / fov) + 1))

        # Create preview images (gray grids)
        preview_w = tiles_x * 100
        preview_h = tiles_y * 100
        preview_img = np.ones((preview_h, preview_w), dtype=np.uint8) * 200

        # Draw tile boundaries
        for i in range(tiles_x + 1):
            x = i * 100
            if x < preview_w:
                preview_img[:, x : x + 2] = 100

        for i in range(tiles_y + 1):
            y = i * 100
            if y < preview_h:
                preview_img[y : y + 2, :] = 100

        # Set titles
        r1 = self._config.starting_r
        r2 = self._config.starting_r + 90

        self.left_panel.set_title(f"Preview: R = {r1}°")
        self.left_panel.set_image(preview_img, tiles_x, tiles_y)

        self.right_panel.set_title(f"Preview: R = {r2}°")
        self.right_panel.set_image(preview_img.copy(), tiles_x, tiles_y)

        # Update info
        self.info_text.setText(
            f"Preview Mode\n"
            f"Tiles: {tiles_x} x {tiles_y} = {tiles_x * tiles_y} per rotation\n"
            f"Total: {tiles_x * tiles_y * 2} tiles\n"
            f"Region: X [{bbox.x_min:.2f} to {bbox.x_max:.2f}], "
            f"Y [{bbox.y_min:.2f} to {bbox.y_max:.2f}] mm"
        )

    def _update_info_text(self):
        """Update the info text with scan details (compact single line)."""
        if not self._results:
            return

        parts = []
        total_tiles = 0

        for i, result in enumerate(self._results):
            total_tiles += len(result.tiles)
            # Calculate actual tile dimensions from tile indices
            if result.tiles:
                max_x = max(t.tile_x_idx for t in result.tiles) + 1
                max_y = max(t.tile_y_idx for t in result.tiles) + 1
                parts.append(f"R{i+1}={result.rotation_angle}° ({max_x}x{max_y})")
            else:
                parts.append(f"R{i+1}={result.rotation_angle}° (no tiles)")

        if self._config:
            bbox = self._config.bounding_box
            parts.append(f"X:[{bbox.x_min:.2f}-{bbox.x_max:.2f}]")
            parts.append(f"Y:[{bbox.y_min:.2f}-{bbox.y_max:.2f}]mm")

        parts.append(f"Total: {total_tiles} tiles")

        self.info_text.setText(" | ".join(parts))

    def _populate_visualization_types(self):
        """Populate the visualization type dropdown."""
        from py2flamingo.models.data.overview_results import VISUALIZATION_TYPES

        self.viz_combo.clear()
        for viz_type, display_name in VISUALIZATION_TYPES:
            self.viz_combo.addItem(display_name, viz_type)

        # Default to "Minimum Intensity" (index 2)
        self.viz_combo.setCurrentIndex(2)

    def _on_visualization_changed(self, display_name: str):
        """Handle visualization type change."""
        viz_type = self.viz_combo.currentData()
        if viz_type:
            logger.info(f"Switching to visualization: {viz_type} ({display_name})")
            self._load_visualization_on_demand(viz_type)
            self._display_visualization(viz_type)

    def _load_visualization_on_demand(self, viz_type: str):
        """Load a visualization type from zarr if not already in memory.

        For zarr sessions, datasets are loaded lazily — only when the user
        switches to a visualization type that hasn't been loaded yet.
        """
        if self._zarr_root is None or not self._results:
            return

        for i, result in enumerate(self._results):
            if viz_type in result.stitched_images:
                continue  # Already loaded

            zarr_key = f"rotation_{i}/stitched_{viz_type}"
            try:
                result.stitched_images[viz_type] = np.array(self._zarr_root[zarr_key])
                logger.info(f"On-demand loaded: {zarr_key}")
            except KeyError:
                logger.debug(f"Dataset not found in zarr: {zarr_key}")

    def _display_visualization(self, visualization_type: str):
        """Display the selected visualization type for both panels.

        Args:
            visualization_type: The visualization type key (e.g., "best_focus", "min_intensity")
        """
        if not self._results:
            return

        # Display first rotation
        if len(self._results) >= 1:
            result1 = self._results[0]
            img1 = result1.stitched_images.get(visualization_type)
            if img1 is not None:
                # Calculate actual grid from tiles (may differ from expected if some tiles missing)
                if result1.tiles:
                    actual_tiles_x = max(t.tile_x_idx for t in result1.tiles) + 1
                    actual_tiles_y = max(t.tile_y_idx for t in result1.tiles) + 1
                else:
                    actual_tiles_x = result1.tiles_x
                    actual_tiles_y = result1.tiles_y

                self.left_panel.set_image(img1, actual_tiles_x, actual_tiles_y)
                # Re-apply coordinates to ensure they're set after image change
                coords = [(t.x, t.y, t.tile_x_idx, t.tile_y_idx) for t in result1.tiles]
                self.left_panel.set_tile_coordinates(coords, invert_x=result1.invert_x)

        # Display second rotation
        if len(self._results) >= 2:
            result2 = self._results[1]
            img2 = result2.stitched_images.get(visualization_type)
            if img2 is not None:
                # Calculate actual grid from tiles (may differ from expected if some tiles missing)
                if result2.tiles:
                    actual_tiles_x = max(t.tile_x_idx for t in result2.tiles) + 1
                    actual_tiles_y = max(t.tile_y_idx for t in result2.tiles) + 1
                else:
                    actual_tiles_x = result2.tiles_x
                    actual_tiles_y = result2.tiles_y

                self.right_panel.set_image(img2, actual_tiles_x, actual_tiles_y)
                # Re-apply coordinates to ensure they're set after image change
                coords = [(t.x, t.y, t.tile_x_idx, t.tile_y_idx) for t in result2.tiles]
                self.right_panel.set_tile_coordinates(coords, invert_x=result2.invert_x)

    def _toggle_grid(self):
        """Toggle grid overlay."""
        show_grid = self.grid_btn.isChecked()
        self.left_panel.set_show_grid(show_grid)
        self.right_panel.set_show_grid(show_grid)

    def _on_select_all(self):
        """Select all tiles in both panels."""
        self.left_panel.select_all_tiles()
        self.right_panel.select_all_tiles()

    def _on_clear_selection(self):
        """Clear selection in both panels."""
        self.left_panel.clear_selection()
        self.right_panel.clear_selection()

    def _on_auto_select(self):
        """Open thresholder dialog for automatic tile selection."""
        # Use the display image (downsampled to ~4096px max) for fast analysis.
        # Per-tile variance/edge/intensity stats are equivalent at lower resolution.
        image = self.left_panel._display_image
        if image is None:
            QMessageBox.warning(
                self, "No Image", "No image loaded. Please wait for scan to complete."
            )
            return

        tiles_x = self.left_panel._tiles_x
        tiles_y = self.left_panel._tiles_y

        if tiles_x <= 0 or tiles_y <= 0:
            QMessageBox.warning(
                self,
                "No Tiles",
                "Tile grid not configured. Please ensure scan completed correctly.",
            )
            return

        # Import here to avoid circular imports
        from .overview_thresholder_dialog import OverviewThresholderDialog

        dialog = OverviewThresholderDialog(
            image=image, tiles_x=tiles_x, tiles_y=tiles_y, parent=self
        )

        # Connect selection signal
        def apply_selection(selected_tiles):
            """Apply selection from thresholder to both panels."""
            # Clear existing selection
            self.left_panel.clear_selection()
            self.right_panel.clear_selection()

            # Apply new selection to both panels
            for tx, ty in selected_tiles:
                self.left_panel._selected_tiles.add((tx, ty))
                self.right_panel._selected_tiles.add((tx, ty))

            # Redraw overlays
            self.left_panel._redraw_overlay()
            self.right_panel._redraw_overlay()

            # Update selection UI
            self._on_selection_changed()

            logger.info(
                f"Auto-select applied {len(selected_tiles)} tiles to both panels"
            )

        dialog.selection_ready.connect(apply_selection)
        dialog.exec_()

    def _on_selection_changed(self):
        """Handle tile selection change - update UI state."""
        left_count = self.left_panel.get_selected_tile_count()
        right_count = self.right_panel.get_selected_tile_count()
        total = left_count + right_count

        self.selection_label.setText(f"{total} tiles selected")
        self.collect_btn.setEnabled(total > 0)

    def _on_tile_right_clicked(self, tile_x_idx: int, tile_y_idx: int, panel: str):
        """Handle tile right-click - move stage to tile position (X, Y, center Z).

        Args:
            tile_x_idx: Tile X index
            tile_y_idx: Tile Y index
            panel: 'left' or 'right' indicating which panel was clicked
        """
        logger.info(
            f"Right-click on tile ({tile_x_idx}, {tile_y_idx}) in {panel} panel"
        )

        # Get the panel and its tile results
        if panel == "left":
            image_panel = self.left_panel
        else:
            image_panel = self.right_panel

        # Find the TileResult for this tile
        tile_results = image_panel._tile_results
        target_tile = None

        for tile in tile_results:
            if tile.tile_x_idx == tile_x_idx and tile.tile_y_idx == tile_y_idx:
                target_tile = tile
                break

        if target_tile is None:
            logger.warning(
                f"Could not find tile ({tile_x_idx}, {tile_y_idx}) in results"
            )
            QMessageBox.warning(
                self,
                "Tile Not Found",
                f"Could not find data for tile ({tile_x_idx}, {tile_y_idx}).",
            )
            return

        # Calculate center Z from z_stack range
        z_center = (target_tile.z_stack_min + target_tile.z_stack_max) / 2
        if target_tile.z_stack_min == 0.0 and target_tile.z_stack_max == 0.0:
            # No Z range data, use tile's Z position
            z_center = target_tile.z

        logger.info(
            f"Moving to tile ({tile_x_idx}, {tile_y_idx}): "
            f"X={target_tile.x:.3f}, Y={target_tile.y:.3f}, Z={z_center:.3f} mm "
            f"(Z range: {target_tile.z_stack_min:.3f} - {target_tile.z_stack_max:.3f})"
        )

        # Move stage to tile position using move_to_position for multi-axis move
        if (
            self._app
            and hasattr(self._app, "movement_controller")
            and self._app.movement_controller
        ):
            try:
                from py2flamingo.models.microscope import Position

                pos_ctrl = self._app.movement_controller.position_controller
                current = pos_ctrl._current_position
                target_position = Position(
                    x=target_tile.x,
                    y=target_tile.y,
                    z=z_center,
                    r=current.r if current else 0.0,
                )
                pos_ctrl.move_to_position(target_position, validate=True)
                self.info_text.setText(
                    f"Moving to X={target_tile.x:.3f}, Y={target_tile.y:.3f}, "
                    f"Z={z_center:.3f} mm (tile {tile_x_idx},{tile_y_idx})"
                )
            except Exception as e:
                logger.error(f"Failed to move to tile position: {e}")
                QMessageBox.warning(self, "Move Failed", f"Failed to move stage: {e}")
        else:
            logger.warning("Movement controller not available")
            QMessageBox.warning(
                self,
                "Not Connected",
                "Cannot move stage - not connected to microscope.",
            )

    def _on_collect_tiles(self):
        """Open dialog to configure and collect workflows for selected tiles."""
        # Gather selected tiles from both panels
        left_tiles = self.left_panel.get_selected_tiles()
        right_tiles = self.right_panel.get_selected_tiles()

        if not left_tiles and not right_tiles:
            QMessageBox.warning(
                self, "No Selection", "Please select at least one tile first."
            )
            return

        # Get rotation angles
        left_rotation = (
            self._results[0].rotation_angle if len(self._results) >= 1 else 0.0
        )
        right_rotation = (
            self._results[1].rotation_angle if len(self._results) >= 2 else 90.0
        )

        # Open collection dialog
        from py2flamingo.views.dialogs.tile_collection_dialog import (
            TileCollectionDialog,
        )

        dialog = TileCollectionDialog(
            left_tiles=left_tiles,
            right_tiles=right_tiles,
            left_rotation=left_rotation,
            right_rotation=right_rotation,
            config=self._config,
            app=self._app,
            parent=self,
        )
        dialog.exec_()

    def _save_image(self, which: str):
        """Save image(s) to file.

        Args:
            which: 'left', 'right', or 'both'
        """
        if which == "both":
            self._save_image("left")
            self._save_image("right")
            return

        panel = self.left_panel if which == "left" else self.right_panel
        image = panel.get_image()

        if image is None:
            QMessageBox.warning(self, "No Image", f"No image in {which} panel")
            return

        # Get save path
        default_name = f"led_2d_overview_{which}.png"
        path, _ = QFileDialog.getSaveFileName(
            self,
            f"Save {which.title()} Image",
            default_name,
            "PNG Images (*.png);;TIFF Images (*.tiff *.tif);;All Files (*)",
        )

        if not path:
            return

        try:
            import cv2

            # Downsample by 4x for smaller file size
            downsample_factor = 4
            original_h, original_w = image.shape[:2]
            new_w = original_w // downsample_factor
            new_h = original_h // downsample_factor

            # Use INTER_AREA for best quality when downsampling
            downsampled = cv2.resize(
                image, (new_w, new_h), interpolation=cv2.INTER_AREA
            )
            logger.info(
                f"Downsampled image from {original_w}x{original_h} to {new_w}x{new_h} (4x)"
            )

            # Ensure image is in the right format for saving
            if len(downsampled.shape) == 3 and downsampled.shape[2] == 3:
                # RGB to BGR for OpenCV
                save_img = cv2.cvtColor(downsampled, cv2.COLOR_RGB2BGR)
            else:
                save_img = downsampled

            cv2.imwrite(path, save_img)
            logger.info(f"Saved image to {path}")
            QMessageBox.information(
                self,
                "Saved",
                f"Image saved to:\n{path}\n\nDownsampled 4x: {new_w}x{new_h} pixels",
            )

        except ImportError:
            # Fallback without OpenCV - use PIL
            try:
                from PIL import Image as PILImage

                # Downsample by 4x
                downsample_factor = 4
                original_h, original_w = image.shape[:2]
                new_w = original_w // downsample_factor
                new_h = original_h // downsample_factor

                pil_img = PILImage.fromarray(image)
                pil_img = pil_img.resize((new_w, new_h), PILImage.Resampling.LANCZOS)
                pil_img.save(path)
                logger.info(
                    f"Saved image to {path} (downsampled 4x to {new_w}x{new_h})"
                )
                QMessageBox.information(
                    self,
                    "Saved",
                    f"Image saved to:\n{path}\n\nDownsampled 4x: {new_w}x{new_h} pixels",
                )
            except ImportError:
                QMessageBox.critical(
                    self, "Error", "Neither OpenCV nor PIL available for saving images"
                )
        except Exception as e:
            logger.error(f"Error saving image: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save image:\n{e}")

    def _save_single_rotation(self, rotation_idx: int):
        """Save a single rotation's stitched image.

        Args:
            rotation_idx: 0 for initial image, 1 for rotated image
        """
        if not self._results or len(self._results) <= rotation_idx:
            name = "Initial" if rotation_idx == 0 else "Rotated"
            QMessageBox.warning(self, "No Image", f"No {name.lower()} image available")
            return

        rotation = self._results[rotation_idx]

        # Get currently selected visualization type
        viz_type = self.viz_combo.currentData() or "best_focus"

        # Get the stitched image for this rotation and viz type
        image = rotation.stitched_images.get(viz_type)
        if image is None:
            # Try fallback to any available image
            for vt, img in rotation.stitched_images.items():
                if img is not None:
                    image = img
                    viz_type = vt
                    break

        if image is None:
            QMessageBox.warning(
                self,
                "No Image",
                f"No stitched image available for rotation {rotation_idx}",
            )
            return

        # Build default filename
        rotation_name = "initial" if rotation_idx == 0 else "rotated"
        angle = rotation.rotation_angle
        default_name = f"led_2d_overview_{rotation_name}_R{angle}_{viz_type}.tiff"

        path, _ = QFileDialog.getSaveFileName(
            self,
            f"Save {'Initial' if rotation_idx == 0 else 'Rotated'} Image",
            default_name,
            "TIFF Images (*.tiff *.tif);;PNG Images (*.png);;All Files (*)",
        )

        if not path:
            return

        try:
            import tifffile

            # Save as 16-bit TIFF if it's grayscale, otherwise downsample and save
            if len(image.shape) == 2 and image.dtype == np.uint16:
                # Save full resolution 16-bit TIFF
                tifffile.imwrite(path, image)
                logger.info(f"Saved 16-bit TIFF to {path}")
                QMessageBox.information(
                    self,
                    "Saved",
                    f"Image saved to:\n{path}\n\n"
                    f"Full resolution: {image.shape[1]}x{image.shape[0]} pixels\n"
                    f"16-bit grayscale",
                )
            else:
                # Downsample for RGB or 8-bit images
                downsample_factor = 4
                original_h, original_w = image.shape[:2]
                new_w = original_w // downsample_factor
                new_h = original_h // downsample_factor

                import cv2

                downsampled = cv2.resize(
                    image, (new_w, new_h), interpolation=cv2.INTER_AREA
                )
                tifffile.imwrite(path, downsampled)
                logger.info(f"Saved downsampled image to {path}")
                QMessageBox.information(
                    self,
                    "Saved",
                    f"Image saved to:\n{path}\n\n"
                    f"Downsampled 4x: {new_w}x{new_h} pixels",
                )

        except ImportError as e:
            # Fallback without tifffile
            logger.warning(f"tifffile not available: {e}")
            try:
                import cv2

                # Downsample
                downsample_factor = 4
                original_h, original_w = image.shape[:2]
                new_w = original_w // downsample_factor
                new_h = original_h // downsample_factor
                downsampled = cv2.resize(
                    image, (new_w, new_h), interpolation=cv2.INTER_AREA
                )

                if len(downsampled.shape) == 3 and downsampled.shape[2] == 3:
                    save_img = cv2.cvtColor(downsampled, cv2.COLOR_RGB2BGR)
                else:
                    save_img = downsampled

                cv2.imwrite(path, save_img)
                logger.info(f"Saved image to {path}")
                QMessageBox.information(
                    self,
                    "Saved",
                    f"Image saved to:\n{path}\n\nDownsampled 4x: {new_w}x{new_h} pixels",
                )
            except ImportError:
                QMessageBox.critical(
                    self, "Error", "Required libraries (tifffile or cv2) not available"
                )
        except Exception as e:
            logger.error(f"Error saving rotation image: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save image:\n{e}")

    def update_tile(self, rotation_idx: int, tile_idx: int, image: np.ndarray):
        """Update a single tile during scanning.

        Args:
            rotation_idx: Which rotation (0 or 1)
            tile_idx: Which tile
            image: Tile image
        """
        # This method allows updating tiles as they're captured
        # during a scan, for live preview
        panel = self.left_panel if rotation_idx == 0 else self.right_panel

        # For now, just redraw the full image
        # A more efficient implementation would update just the tile
        pass

    def _save_session(self):
        """Save all scan results (Zarr if available, TIFF fallback)."""
        import json
        from datetime import datetime
        from pathlib import Path

        if not self._results:
            QMessageBox.warning(self, "No Results", "No scan results to save")
            return

        # Determine default save location
        # Priority: 1) User's saved preference, 2) 2DOverviewSession in project folder
        default_folder = None

        # Check for user's saved preference via configuration service
        if self._app and hasattr(self._app, "config_service"):
            saved_path = self._app.config_service.get_led_2d_session_path()
            if saved_path and Path(saved_path).exists():
                default_folder = saved_path

        # Fall back to default 2DOverviewSession folder in project root
        if not default_folder:
            # Get project root (parent of src directory)
            project_root = Path(__file__).parent.parent.parent.parent.parent
            default_session_folder = project_root / "2DOverviewSession"
            # Create it if it doesn't exist
            try:
                default_session_folder.mkdir(parents=True, exist_ok=True)
                default_folder = str(default_session_folder)
            except Exception as e:
                logger.warning(f"Could not create default session folder: {e}")
                default_folder = str(Path.home())

        # Get save location
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Folder to Save Session",
            default_folder,
            QFileDialog.ShowDirsOnly,
        )
        if not folder:
            return

        # Remember user's choice for future sessions
        if self._app and hasattr(self._app, "config_service"):
            self._app.config_service.set_led_2d_session_path(folder)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Build metadata
        metadata = {
            "version": "1.0",
            "saved_at": datetime.now().isoformat(),
            "config": {},
            "rotations": [],
        }

        # Save config if available
        if self._config:
            metadata["config"] = {
                "bounding_box": {
                    "x_min": self._config.bounding_box.x_min,
                    "x_max": self._config.bounding_box.x_max,
                    "y_min": self._config.bounding_box.y_min,
                    "y_max": self._config.bounding_box.y_max,
                    "z_min": self._config.bounding_box.z_min,
                    "z_max": self._config.bounding_box.z_max,
                },
                "starting_r": self._config.starting_r,
                "led_name": self._config.led_name,
                "led_intensity": self._config.led_intensity,
                "z_step_size": getattr(self._config, "z_step_size", 0.250),
            }

        # Collect rotation metadata (without images)
        for rotation in self._results:
            metadata["rotations"].append(rotation.to_dict())

        if ZARR_AVAILABLE:
            save_path = Path(folder) / f"led_2d_overview_{timestamp}.zarr"
            try:
                # Build hierarchical images dict
                images = {}
                for i, rotation in enumerate(self._results):
                    for vis_type, image in rotation.stitched_images.items():
                        if image is not None:
                            images[f"rotation_{i}/stitched_{vis_type}"] = image

                save_2d_zarr_session(save_path, metadata, images, "led_2d_overview")
            except Exception as e:
                logger.error(f"Error saving zarr session: {e}", exc_info=True)
                QMessageBox.critical(self, "Error", f"Failed to save session:\n{e}")
                return
        else:
            save_path = Path(folder) / f"led_2d_overview_{timestamp}"
            try:
                self._save_session_tiff(save_path, metadata)
            except Exception as e:
                logger.error(f"Error saving session: {e}", exc_info=True)
                QMessageBox.critical(self, "Error", f"Failed to save session:\n{e}")
                return

        logger.info(f"Saved LED 2D Overview session to {save_path}")
        QMessageBox.information(
            self,
            "Session Saved",
            f"Session saved to:\n{save_path}\n\n"
            f"Contains {len(self._results)} rotation(s) with all visualization types.",
        )

    def _save_session_tiff(self, result_folder: "Path", metadata: dict):
        """TIFF fallback for session save when zarr is unavailable."""
        import json
        from pathlib import Path

        import tifffile

        result_folder = Path(result_folder)
        result_folder.mkdir(parents=True, exist_ok=True)

        # Save each rotation's images as TIFF
        for i, rotation in enumerate(self._results):
            rot_folder = result_folder / f"rotation_{i}"
            rot_folder.mkdir()

            for vis_type, image in rotation.stitched_images.items():
                if image is not None:
                    img_path = rot_folder / f"stitched_{vis_type}.tif"
                    tifffile.imwrite(str(img_path), image)

        # Save metadata JSON
        with open(result_folder / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

    @classmethod
    def load_from_folder(cls, folder_path, app=None) -> "LED2DOverviewResultWindow":
        """Load saved results from folder and create result window (Zarr or TIFF).

        For zarr sessions, only the default visualization type is loaded
        eagerly; other types are loaded on demand when the user switches
        the visualization dropdown.

        Args:
            folder_path: Path to saved session folder
            app: Optional FlamingoApplication reference

        Returns:
            LED2DOverviewResultWindow instance with loaded data
        """
        import json
        from pathlib import Path

        folder_path = Path(folder_path)
        fmt = detect_session_format(folder_path)

        zarr_root = None  # Kept alive for on-demand loading

        if fmt == "zarr":
            metadata, zarr_root = load_2d_zarr_session_lazy(folder_path)
        elif fmt == "tiff":
            metadata_path = folder_path / "metadata.json"
            if not metadata_path.exists():
                raise FileNotFoundError(f"No metadata.json found in {folder_path}")

            with open(metadata_path) as f:
                metadata = json.load(f)
        else:
            raise FileNotFoundError(f"No valid session found in {folder_path}")

        # Reconstruct config
        config = None
        if metadata.get("config"):
            from .led_2d_overview_dialog import BoundingBox, ScanConfiguration

            bbox_data = metadata["config"].get("bounding_box", {})
            bounding_box = BoundingBox(
                x_min=bbox_data.get("x_min", 0),
                x_max=bbox_data.get("x_max", 0),
                y_min=bbox_data.get("y_min", 0),
                y_max=bbox_data.get("y_max", 0),
                z_min=bbox_data.get("z_min", 0),
                z_max=bbox_data.get("z_max", 0),
            )

            config = ScanConfiguration(
                bounding_box=bounding_box,
                starting_r=metadata["config"].get("starting_r", 0),
                led_name=metadata["config"].get("led_name", "led_red"),
                led_intensity=metadata["config"].get("led_intensity", 50),
                z_step_size=metadata["config"].get("z_step_size", 0.250),
            )

        # Determine default viz type (matches _populate_visualization_types index 2)
        from py2flamingo.models.data.overview_results import VISUALIZATION_TYPES

        default_viz_type = (
            VISUALIZATION_TYPES[2][0] if len(VISUALIZATION_TYPES) > 2 else "best_focus"
        )

        # Load rotations
        from py2flamingo.models.data.overview_results import RotationResult, TileResult

        results = []
        for i, rot_data in enumerate(metadata.get("rotations", [])):
            if fmt == "zarr":
                # Only load the default viz type eagerly
                stitched_images = {}
                zarr_key = f"rotation_{i}/stitched_{default_viz_type}"
                try:
                    stitched_images[default_viz_type] = np.array(zarr_root[zarr_key])
                    logger.debug(f"Eagerly loaded {zarr_key}")
                except KeyError:
                    logger.warning(f"Default viz key not found: {zarr_key}")
                    # Fall back to first available dataset in this rotation
                    rot_group_key = f"rotation_{i}"
                    if rot_group_key in zarr_root:
                        rot_group = zarr_root[rot_group_key]
                        for child_key in rot_group:
                            child = rot_group[child_key]
                            if hasattr(child, "shape") and len(child.shape) > 0:
                                vis_type = child_key.replace("stitched_", "", 1)
                                stitched_images[vis_type] = np.array(child)
                                logger.debug(
                                    f"Fallback loaded {rot_group_key}/{child_key}"
                                )
                                break
            else:
                # TIFF path — load all (usually just a few files)
                import tifffile

                rot_folder = folder_path / f"rotation_{i}"
                stitched_images = {}
                for vis_type in rot_data.get("stitched_image_types", ["best_focus"]):
                    img_path = rot_folder / f"stitched_{vis_type}.tif"
                    if img_path.exists():
                        stitched_images[vis_type] = tifffile.imread(str(img_path))

            # Reconstruct tiles (for coordinate display)
            tiles = []
            for tile_data in rot_data.get("tiles", []):
                tile = TileResult.from_dict(tile_data)
                tiles.append(tile)

            rotation = RotationResult.from_dict(
                rot_data, stitched_images=stitched_images, tiles=tiles
            )
            results.append(rotation)

        # Create and return window
        window = cls(results=results, config=config, app=app)
        # Store zarr root for on-demand loading of other viz types
        window._zarr_root = zarr_root
        window._session_folder = folder_path
        window._session_format = fmt
        return window
