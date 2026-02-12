"""Zoomable image label widget.

Self-contained QLabel with zoom/pan support and tile click detection,
extracted from led_2d_overview_result.py.
"""

import logging
from typing import Optional

from PyQt5.QtWidgets import QLabel, QScrollArea
from PyQt5.QtCore import Qt, QSize, QPoint, pyqtSignal
from PyQt5.QtGui import QPixmap, QWheelEvent, QMouseEvent


logger = logging.getLogger(__name__)


class ZoomableImageLabel(QLabel):
    """Image label with zoom and pan support."""

    # Signal emitted when user left-clicks on a tile (tile_x_idx, tile_y_idx)
    tile_clicked = pyqtSignal(int, int)

    # Signal emitted when user right-clicks on a tile (tile_x_idx, tile_y_idx)
    tile_right_clicked = pyqtSignal(int, int)

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

        elif event.button() == Qt.RightButton:
            # Right-click - move to tile center Z
            if self._tiles_x > 0 and self._tiles_y > 0:
                self._handle_tile_right_click(event.pos())
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

    def _handle_tile_right_click(self, pos: QPoint):
        """Calculate which tile was right-clicked and emit signal for move to center Z."""
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

        logger.debug(f"Tile right-clicked: ({tile_x_idx}, {tile_y_idx})")
        self.tile_right_clicked.emit(tile_x_idx, tile_y_idx)

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
        """Fit image to view size, scaling down to fit entirely in viewport."""
        if self._original_pixmap is None:
            logger.debug("fit_to_view: no pixmap")
            return

        img_w = self._original_pixmap.width()
        img_h = self._original_pixmap.height()

        if img_w <= 0 or img_h <= 0:
            logger.debug(f"fit_to_view: invalid image size {img_w}x{img_h}")
            return

        view_w = view_size.width()
        view_h = view_size.height()

        if view_w <= 0 or view_h <= 0:
            logger.debug(f"fit_to_view: invalid view size {view_w}x{view_h}")
            return

        # Calculate zoom to fit entire image in view with small margin
        margin = 10
        scale_x = (view_w - margin) / img_w
        scale_y = (view_h - margin) / img_h
        self._zoom = min(scale_x, scale_y)

        # Clamp to reasonable range
        self._zoom = max(self._min_zoom, min(self._max_zoom, self._zoom))

        logger.debug(f"fit_to_view: image={img_w}x{img_h}, view={view_w}x{view_h}, "
                    f"scale_x={scale_x:.4f}, scale_y={scale_y:.4f}, zoom={self._zoom:.4f}")

        self._update_display()

    @property
    def zoom_level(self) -> float:
        return self._zoom
