"""LED 2D Overview Result Window.

Displays the results of an LED 2D Overview scan, showing two side-by-side
images at different rotation angles with coordinate overlays.
"""

import logging
from typing import Optional, List
from dataclasses import dataclass
import numpy as np

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSplitter, QGroupBox, QFileDialog, QMessageBox,
    QSizePolicy, QFrame, QComboBox
)
from PyQt5.QtCore import Qt, QSize, QPoint, QPointF, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QFont, QWheelEvent, QMouseEvent


logger = logging.getLogger(__name__)


class ZoomableImageLabel(QLabel):
    """Image label with zoom and pan support."""

    # Signal emitted when user clicks on a tile (tile_x_idx, tile_y_idx)
    tile_clicked = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._zoom = 1.0
        self._min_zoom = 0.01  # Allow zoom out to 1% for large tile grids
        self._max_zoom = 20.0  # Allow more zoom for small images
        self._pan_start = QPoint()
        self._panning = False
        self._original_pixmap: Optional[QPixmap] = None
        self._scroll_area: Optional[QScrollArea] = None
        self._tiles_x = 0
        self._tiles_y = 0
        self._invert_x = False
        self._drag_distance = 0
        self._click_start = QPoint()

        self.setMouseTracking(True)
        self.setCursor(Qt.OpenHandCursor)
        self.setAlignment(Qt.AlignCenter)

    def set_tile_grid(self, tiles_x: int, tiles_y: int, invert_x: bool = False):
        """Set tile grid dimensions for click detection."""
        self._tiles_x = tiles_x
        self._tiles_y = tiles_y
        self._invert_x = invert_x

    def set_scroll_area(self, scroll_area: QScrollArea):
        """Set reference to parent scroll area for panning."""
        self._scroll_area = scroll_area

    def setPixmap(self, pixmap: QPixmap):
        """Set the pixmap and store original for zooming."""
        self._original_pixmap = pixmap
        self._update_display()

    def _update_display(self):
        """Update display with current zoom level."""
        if self._original_pixmap is None:
            return

        # Scale pixmap
        new_width = int(self._original_pixmap.width() * self._zoom)
        new_height = int(self._original_pixmap.height() * self._zoom)

        if new_width < 1 or new_height < 1:
            return

        scaled = self._original_pixmap.scaled(
            new_width, new_height,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        super().setPixmap(scaled)

        # Resize the label to match the scaled pixmap
        self.setFixedSize(scaled.size())

    def wheelEvent(self, event: QWheelEvent):
        """Handle mouse wheel for zoom."""
        delta = event.angleDelta().y()
        old_zoom = self._zoom

        if delta > 0:
            self._zoom = min(self._max_zoom, self._zoom * 1.2)
        else:
            self._zoom = max(self._min_zoom, self._zoom / 1.2)

        if self._zoom != old_zoom:
            self._update_display()

        event.accept()

    def mousePressEvent(self, event: QMouseEvent):
        """Start panning on left click."""
        if event.button() == Qt.LeftButton:
            self._panning = True
            self._pan_start = event.globalPos()
            self._click_start = event.pos()  # Track local position for click detection
            self._drag_distance = 0
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        """Stop panning and detect clicks."""
        if event.button() == Qt.LeftButton:
            self._panning = False
            self.setCursor(Qt.OpenHandCursor)

            # If mouse didn't move much, it's a click - select tile
            if self._drag_distance < 5 and self._tiles_x > 0 and self._tiles_y > 0:
                self._handle_tile_click(event.pos())

            event.accept()

    def _handle_tile_click(self, pos: QPoint):
        """Calculate which tile was clicked and emit signal."""
        if self._original_pixmap is None:
            return

        # Convert click position to original image coordinates
        img_x = pos.x() / self._zoom
        img_y = pos.y() / self._zoom

        # Calculate tile size in original image
        img_w = self._original_pixmap.width()
        img_h = self._original_pixmap.height()
        tile_w = img_w / self._tiles_x
        tile_h = img_h / self._tiles_y

        # Calculate display tile index
        display_x_idx = int(img_x / tile_w)
        tile_y_idx = int(img_y / tile_h)

        # Clamp to valid range
        display_x_idx = max(0, min(display_x_idx, self._tiles_x - 1))
        tile_y_idx = max(0, min(tile_y_idx, self._tiles_y - 1))

        # Convert display index back to tile index if inverted
        if self._invert_x:
            tile_x_idx = (self._tiles_x - 1) - display_x_idx
        else:
            tile_x_idx = display_x_idx

        logger.debug(f"Tile clicked: ({tile_x_idx}, {tile_y_idx})")
        self.tile_clicked.emit(tile_x_idx, tile_y_idx)

    def mouseMoveEvent(self, event: QMouseEvent):
        """Pan while dragging."""
        if self._panning and self._scroll_area:
            delta = event.globalPos() - self._pan_start
            self._pan_start = event.globalPos()
            self._drag_distance += abs(delta.x()) + abs(delta.y())

            h_bar = self._scroll_area.horizontalScrollBar()
            v_bar = self._scroll_area.verticalScrollBar()
            h_bar.setValue(h_bar.value() - delta.x())
            v_bar.setValue(v_bar.value() - delta.y())
            event.accept()

    def reset_zoom(self):
        """Reset zoom to 100%."""
        self._zoom = 1.0
        self._update_display()

    def fit_to_view(self, view_size: QSize):
        """Fit image to view size, scaling up if needed."""
        if self._original_pixmap is None:
            return

        img_w = self._original_pixmap.width()
        img_h = self._original_pixmap.height()

        if img_w <= 0 or img_h <= 0:
            return

        # Calculate zoom to fit, allowing scale up for small images
        scale_x = (view_size.width() - 20) / img_w  # Leave margin
        scale_y = (view_size.height() - 20) / img_h
        self._zoom = min(scale_x, scale_y)

        # Ensure minimum reasonable zoom
        self._zoom = max(0.1, self._zoom)

        self._update_display()

    @property
    def zoom_level(self) -> float:
        return self._zoom


class ImagePanel(QWidget):
    """Widget displaying a single image with coordinate overlay and zoom/pan."""

    # Signal emitted when tile selection changes
    selection_changed = pyqtSignal()

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)

        self._title = title
        self._image: Optional[np.ndarray] = None
        self._pixmap: Optional[QPixmap] = None
        self._base_pixmap: Optional[QPixmap] = None  # Cached base (image + grid + coords, no selections)
        self._show_grid = True
        self._tiles_x = 0
        self._tiles_y = 0
        self._tile_coords: List[tuple] = []  # (x, y, tile_x_idx, tile_y_idx) for each tile
        self._invert_x = False  # Whether X-axis is inverted for display
        self._selected_tiles: set = set()  # Set of (tile_x_idx, tile_y_idx) tuples
        self._tile_results: List = []  # Store TileResult objects for retrieval

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

        # Scroll area with zoomable image
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)  # Don't auto-resize for zoom
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setMinimumSize(200, 200)
        self.scroll_area.setStyleSheet("background-color: #2a2a2a;")  # Dark background

        self.image_label = ZoomableImageLabel()
        self.image_label.set_scroll_area(self.scroll_area)  # Connect for panning
        self.image_label.tile_clicked.connect(self._on_tile_clicked)

        self.scroll_area.setWidget(self.image_label)
        layout.addWidget(self.scroll_area, stretch=1)

        # Zoom controls row
        zoom_layout = QHBoxLayout()
        zoom_layout.setSpacing(4)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setStyleSheet("color: gray; font-size: 9pt;")
        zoom_layout.addWidget(self.zoom_label)

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

    def set_image(self, image: Optional[np.ndarray], tiles_x: int = 0, tiles_y: int = 0):
        """Set the image to display.

        Args:
            image: Numpy array image (grayscale or RGB)
            tiles_x: Number of tiles in X dimension (for grid overlay)
            tiles_y: Number of tiles in Y dimension (for grid overlay)
        """
        self._image = image
        self._tiles_x = tiles_x
        self._tiles_y = tiles_y

        if image is not None:
            logger.info(f"ImagePanel.set_image: image shape={image.shape}, tiles={tiles_x}x{tiles_y}, "
                       f"existing coords={len(self._tile_coords)}, invert_x={self._invert_x}")

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

        # Auto-fit to view after a short delay (allows layout to settle)
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(100, self._fit_to_view)

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
            logger.info(f"ImagePanel.set_tile_coordinates: {len(coords)} coords, "
                       f"tile indices up to ({max_x}, {max_y}), invert_x={invert_x}, "
                       f"expected grid={self._tiles_x}x{self._tiles_y}")

        # Invalidate cached base pixmap since coordinates changed
        self._invalidate_base_pixmap()

        # Update image label's tile grid for click detection
        self.image_label.set_tile_grid(self._tiles_x, self._tiles_y, invert_x)

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

    def _redraw_overlay(self):
        """Redraw the selection overlay using cached base pixmap (fast path)."""
        if self._image is None:
            return

        # Use cached base pixmap if available, otherwise rebuild it
        if self._base_pixmap is None:
            self._rebuild_base_pixmap()

        # Copy base pixmap and draw selections on top (fast)
        self._pixmap = self._base_pixmap.copy()
        self._draw_selection_overlay()
        self.image_label.setPixmap(self._pixmap)

    def _rebuild_base_pixmap(self):
        """Rebuild the cached base pixmap (image + grid + coordinates, no selections)."""
        if self._image is None:
            return
        self._base_pixmap = self._array_to_pixmap(self._image)
        if self._show_grid and self._tiles_x > 0 and self._tiles_y > 0:
            self._draw_base_overlay()
        logger.debug(f"Rebuilt base pixmap: {self._base_pixmap.width()}x{self._base_pixmap.height()}")

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
        """Convert numpy array to QPixmap."""
        if len(image.shape) == 2:
            # Grayscale
            h, w = image.shape
            bytes_per_line = w
            # Normalize to 8-bit if needed
            if image.dtype != np.uint8:
                img_8bit = ((image - image.min()) / (image.max() - image.min() + 1e-10) * 255).astype(np.uint8)
            else:
                img_8bit = image
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
        """Draw grid lines and coordinates on the base pixmap (cached, expensive)."""
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
        tile_w = w / self._tiles_x
        tile_h = h / self._tiles_y

        # Draw vertical lines
        for i in range(1, self._tiles_x):
            x = int(i * tile_w)
            painter.drawLine(x, 0, x, h)

        # Draw horizontal lines
        for i in range(1, self._tiles_y):
            y = int(i * tile_h)
            painter.drawLine(0, y, w, y)

        # Draw coordinate labels if available
        if self._tile_coords:
            # Calculate font size: 8.5% of tile height (half of previous 17%)
            font_pixel_size = int(tile_h * 0.085)
            # Ensure minimum reasonable size
            font_pixel_size = max(font_pixel_size, 10)

            font = QFont("Arial")  # Arial renders more predictably than Courier
            font.setPixelSize(font_pixel_size)
            font.setBold(True)
            painter.setFont(font)

            # Get font metrics
            from PyQt5.QtGui import QFontMetrics
            fm = QFontMetrics(font)
            line_height = fm.height()

            for coord in self._tile_coords:
                # Support both formats: (x, y, tile_x_idx, tile_y_idx) or legacy (x, y, z)
                if len(coord) >= 4:
                    x, y, tile_x_idx, tile_y_idx = coord[:4]
                else:
                    continue

                # Calculate display X index (invert if needed to match tile placement)
                if self._invert_x:
                    display_x_idx = (self._tiles_x - 1) - tile_x_idx
                else:
                    display_x_idx = tile_x_idx

                # Calculate tile boundaries
                tile_left = int(display_x_idx * tile_w)
                tile_top = int(tile_y_idx * tile_h)
                tile_center_x = tile_left + tile_w / 2
                tile_center_y = tile_top + tile_h / 2

                # Draw X,Y coordinates (two lines, centered in tile)
                text1 = f"X:{x:.2f}"
                text2 = f"Y:{y:.2f}"

                # Calculate text widths for horizontal centering
                text1_width = fm.horizontalAdvance(text1)
                text2_width = fm.horizontalAdvance(text2)

                # Position: center both lines vertically in tile
                text1_x = int(tile_center_x - text1_width / 2)
                text2_x = int(tile_center_x - text2_width / 2)
                text1_y = int(tile_center_y - line_height * 0.1)  # Slightly above center
                text2_y = int(tile_center_y + line_height * 0.9)  # Below center

                # White text
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(text1_x, text1_y, text1)
                painter.drawText(text2_x, text2_y, text2)

        painter.end()

    def _draw_selection_overlay(self):
        """Draw selection highlights on the current pixmap (fast, called on every click)."""
        if self._pixmap is None or self._tiles_x <= 0 or self._tiles_y <= 0:
            logger.debug(f"_draw_selection_overlay: skipping - pixmap={self._pixmap is not None}, "
                        f"tiles={self._tiles_x}x{self._tiles_y}")
            return
        if not self._selected_tiles:
            logger.debug("_draw_selection_overlay: no selected tiles")
            return  # Nothing to draw

        logger.info(f"_draw_selection_overlay: drawing {len(self._selected_tiles)} selections, "
                   f"tiles={self._tiles_x}x{self._tiles_y}, invert_x={self._invert_x}, "
                   f"pixmap={self._pixmap.width()}x{self._pixmap.height()}")

        painter = QPainter(self._pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self._pixmap.width()
        h = self._pixmap.height()
        tile_w = w / self._tiles_x
        tile_h = h / self._tiles_y

        from PyQt5.QtGui import QBrush
        # Semi-transparent cyan overlay for selected tiles
        painter.setBrush(QBrush(QColor(0, 255, 255, 80)))
        # Thick cyan border
        selection_pen = QPen(QColor(0, 255, 255, 255))
        selection_pen.setWidth(4)
        painter.setPen(selection_pen)

        for tile_x_idx, tile_y_idx in self._selected_tiles:
            # Calculate display position
            if self._invert_x:
                display_x_idx = (self._tiles_x - 1) - tile_x_idx
            else:
                display_x_idx = tile_x_idx

            x_pos = int(display_x_idx * tile_w)
            y_pos = int(tile_y_idx * tile_h)
            painter.drawRect(x_pos, y_pos, int(tile_w), int(tile_h))

        painter.end()

    def get_image(self) -> Optional[np.ndarray]:
        """Get the current image."""
        return self._image


class LED2DOverviewResultWindow(QWidget):
    """Window displaying LED 2D Overview scan results.

    Shows two side-by-side images for the two rotation angles,
    with grid overlays and coordinate information.
    """

    def __init__(self, results=None, config=None, preview_mode: bool = False, app=None, parent=None):
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

        self.setWindowTitle("LED 2D Overview - Results" if not preview_mode else "LED 2D Overview - Preview")
        self.setMinimumSize(800, 500)

        self._setup_ui()

        if results:
            self._display_results()
        elif preview_mode:
            self._display_preview()

    def _setup_ui(self):
        """Create the window UI."""
        layout = QVBoxLayout()

        # Splitter for side-by-side images
        splitter = QSplitter(Qt.Horizontal)

        # Left panel (first rotation)
        self.left_panel = ImagePanel("Rotation 1")
        self.left_panel.selection_changed.connect(self._on_selection_changed)
        splitter.addWidget(self.left_panel)

        # Right panel (second rotation)
        self.right_panel = ImagePanel("Rotation 2")
        self.right_panel.selection_changed.connect(self._on_selection_changed)
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

        # Save buttons
        self.save_left_btn = QPushButton("Save Left")
        self.save_left_btn.clicked.connect(lambda: self._save_image('left'))
        button_layout.addWidget(self.save_left_btn)

        self.save_right_btn = QPushButton("Save Right")
        self.save_right_btn.clicked.connect(lambda: self._save_image('right'))
        button_layout.addWidget(self.save_right_btn)

        self.save_both_btn = QPushButton("Save Both")
        self.save_both_btn.clicked.connect(lambda: self._save_image('both'))
        button_layout.addWidget(self.save_both_btn)

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

            # Debug: log expected vs actual tile counts
            expected_tiles = result1.tiles_x * result1.tiles_y
            actual_tiles = len(result1.tiles)
            logger.info(f"LEFT PANEL: R={result1.rotation_angle}°, expected grid={result1.tiles_x}x{result1.tiles_y}={expected_tiles}, "
                       f"actual tiles={actual_tiles}, invert_x={result1.invert_x}")
            if actual_tiles != expected_tiles:
                logger.warning(f"MISMATCH: Expected {expected_tiles} tiles but got {actual_tiles}!")

            img1 = result1.stitched_images.get(viz_type)
            if img1 is not None:
                logger.info(f"LEFT PANEL: image shape={img1.shape}, viz_type={viz_type}")
                self.left_panel.set_image(img1, result1.tiles_x, result1.tiles_y)
                # Set tile coordinates with grid indices for correct label positioning
                coords = [(t.x, t.y, t.tile_x_idx, t.tile_y_idx) for t in result1.tiles]
                self.left_panel.set_tile_coordinates(coords, invert_x=result1.invert_x)
                # Store tile results for workflow collection
                self.left_panel.set_tile_results(result1.tiles)

        # Display second rotation
        if len(self._results) >= 2:
            result2 = self._results[1]
            self.right_panel.set_title(f"R = {result2.rotation_angle}°")

            img2 = result2.stitched_images.get(viz_type)
            if img2 is not None:
                self.right_panel.set_image(img2, result2.tiles_x, result2.tiles_y)
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
        fov = 0.5182  # mm (no overlap - tiles are adjacent)

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
                preview_img[:, x:x+2] = 100

        for i in range(tiles_y + 1):
            y = i * 100
            if y < preview_h:
                preview_img[y:y+2, :] = 100

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
        from py2flamingo.workflows.led_2d_overview_workflow import VISUALIZATION_TYPES

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
            self._display_visualization(viz_type)

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
                self.left_panel.set_image(img1, result1.tiles_x, result1.tiles_y)

        # Display second rotation
        if len(self._results) >= 2:
            result2 = self._results[1]
            img2 = result2.stitched_images.get(visualization_type)
            if img2 is not None:
                self.right_panel.set_image(img2, result2.tiles_x, result2.tiles_y)

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

    def _on_selection_changed(self):
        """Handle tile selection change - update UI state."""
        left_count = self.left_panel.get_selected_tile_count()
        right_count = self.right_panel.get_selected_tile_count()
        total = left_count + right_count

        self.selection_label.setText(f"{total} tiles selected")
        self.collect_btn.setEnabled(total > 0)

    def _on_collect_tiles(self):
        """Open dialog to configure and collect workflows for selected tiles."""
        # Gather selected tiles from both panels
        left_tiles = self.left_panel.get_selected_tiles()
        right_tiles = self.right_panel.get_selected_tiles()

        if not left_tiles and not right_tiles:
            QMessageBox.warning(self, "No Selection", "Please select at least one tile first.")
            return

        # Get rotation angles
        left_rotation = self._results[0].rotation_angle if len(self._results) >= 1 else 0.0
        right_rotation = self._results[1].rotation_angle if len(self._results) >= 2 else 90.0

        # Open collection dialog
        from py2flamingo.views.dialogs.tile_collection_dialog import TileCollectionDialog

        dialog = TileCollectionDialog(
            left_tiles=left_tiles,
            right_tiles=right_tiles,
            left_rotation=left_rotation,
            right_rotation=right_rotation,
            config=self._config,
            app=self._app,
            parent=self
        )
        dialog.exec_()

    def _save_image(self, which: str):
        """Save image(s) to file.

        Args:
            which: 'left', 'right', or 'both'
        """
        if which == 'both':
            self._save_image('left')
            self._save_image('right')
            return

        panel = self.left_panel if which == 'left' else self.right_panel
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
            "PNG Images (*.png);;TIFF Images (*.tiff *.tif);;All Files (*)"
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
            downsampled = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
            logger.info(f"Downsampled image from {original_w}x{original_h} to {new_w}x{new_h} (4x)")

            # Ensure image is in the right format for saving
            if len(downsampled.shape) == 3 and downsampled.shape[2] == 3:
                # RGB to BGR for OpenCV
                save_img = cv2.cvtColor(downsampled, cv2.COLOR_RGB2BGR)
            else:
                save_img = downsampled

            cv2.imwrite(path, save_img)
            logger.info(f"Saved image to {path}")
            QMessageBox.information(self, "Saved", f"Image saved to:\n{path}\n\nDownsampled 4x: {new_w}x{new_h} pixels")

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
                logger.info(f"Saved image to {path} (downsampled 4x to {new_w}x{new_h})")
                QMessageBox.information(self, "Saved", f"Image saved to:\n{path}\n\nDownsampled 4x: {new_w}x{new_h} pixels")
            except ImportError:
                QMessageBox.critical(
                    self, "Error",
                    "Neither OpenCV nor PIL available for saving images"
                )
        except Exception as e:
            logger.error(f"Error saving image: {e}")
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
