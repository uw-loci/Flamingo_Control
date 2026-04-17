"""Zoomable image label widget.

Self-contained QLabel with zoom/pan support and tile click detection,
extracted from led_2d_overview_result.py.
"""

import logging
from typing import List, Optional

from PyQt5.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QWheelEvent,
)
from PyQt5.QtWidgets import QLabel, QRubberBand, QScrollArea

logger = logging.getLogger(__name__)


class ZoomableImageLabel(QLabel):
    """Image label with zoom and pan support."""

    # Signal emitted when user left-clicks on a tile (tile_x_idx, tile_y_idx)
    tile_clicked = pyqtSignal(int, int)

    # Signal emitted when user right-clicks on a tile (tile_x_idx, tile_y_idx)
    tile_right_clicked = pyqtSignal(int, int)

    # Signal emitted when user Shift+drags a rectangle to select tiles
    tiles_rect_selected = pyqtSignal(set)

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
        self._interactive = False  # Whether current display uses fast scaling
        self._tile_coords: List[tuple] = []  # (x, y, tile_x_idx, tile_y_idx)
        # Stride info for overlapping tiles (None = equal grid)
        self._stride_x: Optional[int] = None
        self._stride_y: Optional[int] = None
        self._tile_pixel_w: Optional[int] = None
        self._tile_pixel_h: Optional[int] = None
        self._rect_selecting = False  # Shift+drag rectangle selection mode
        self._rect_start = QPoint()  # Start position for rectangle selection
        self._rubber_band: Optional[QRubberBand] = None

        # Deferred smooth scaling timer (fires after interaction stops)
        self._smooth_timer = QTimer()
        self._smooth_timer.setSingleShot(True)
        self._smooth_timer.setInterval(150)
        self._smooth_timer.timeout.connect(self._apply_smooth_scaling)

        self.setMouseTracking(True)
        self.setCursor(Qt.OpenHandCursor)
        self.setAlignment(Qt.AlignCenter)

    # Minimum tile display size (pixels) before coordinate labels appear
    _LABEL_MIN_TILE_PX = 80

    def set_tile_grid(self, tiles_x: int, tiles_y: int, invert_x: bool = False):
        """Set tile grid dimensions for click detection."""
        self._tiles_x = tiles_x
        self._tiles_y = tiles_y
        self._invert_x = invert_x
        # Reset stride (caller may set it via set_tile_stride after)
        self._stride_x = None
        self._stride_y = None
        self._tile_pixel_w = None
        self._tile_pixel_h = None

    def set_tile_stride(self, stride_x: int, stride_y: int, tile_w: int, tile_h: int):
        """Set tile stride for overlapping tiles.

        When tiles overlap, the stride (distance between tile origins) is
        less than the tile size. This allows correct click-to-tile mapping
        and coordinate label positioning.
        """
        self._stride_x = stride_x
        self._stride_y = stride_y
        self._tile_pixel_w = tile_w
        self._tile_pixel_h = tile_h

    def set_tile_coordinates(self, coords: List[tuple], invert_x: bool = False):
        """Set tile coordinate labels for on-demand rendering when zoomed in.

        Args:
            coords: List of (x, y, tile_x_idx, tile_y_idx) tuples.
            invert_x: Whether X axis is inverted for display.
        """
        self._tile_coords = coords
        self._invert_x = invert_x
        self.update()  # Trigger repaint to show/hide labels

    def set_scroll_area(self, scroll_area: QScrollArea):
        """Set reference to parent scroll area for panning."""
        self._scroll_area = scroll_area

    def setPixmap(self, pixmap: QPixmap, interactive: bool = False):
        """Set the pixmap and store original for zooming.

        Args:
            pixmap: The pixmap to display.
            interactive: If True, use fast scaling initially and defer
                smooth scaling by 150ms. Use for rapid updates like
                tile clicks and contrast slider drags.
        """
        self._original_pixmap = pixmap
        self._interactive = interactive
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

        transform = (
            Qt.FastTransformation if self._interactive else Qt.SmoothTransformation
        )
        scaled = self._original_pixmap.scaled(
            new_width, new_height, Qt.KeepAspectRatio, transform
        )
        super().setPixmap(scaled)

        # Resize the label to match the scaled pixmap
        self.setFixedSize(scaled.size())

        # Schedule deferred smooth scaling if we used fast mode
        if self._interactive:
            self._smooth_timer.start()

    def _apply_smooth_scaling(self):
        """Re-render with smooth scaling after interaction stops."""
        self._interactive = False
        self._update_display()

    def paintEvent(self, event: QPaintEvent):
        """Paint the label, then overlay coordinate labels when zoomed in."""
        super().paintEvent(event)

        if (
            not self._tile_coords
            or self._tiles_x <= 0
            or self._tiles_y <= 0
            or self._original_pixmap is None
        ):
            return

        # Only draw labels when tiles are large enough on screen to read
        display_w = self.width()
        display_h = self.height()

        # Use stride-based tile size if available
        if self._stride_x is not None and self._stride_y is not None:
            orig_w = self._original_pixmap.width()
            orig_h = self._original_pixmap.height()
            scale_x = display_w / orig_w if orig_w else 1
            scale_y = display_h / orig_h if orig_h else 1
            tile_display_stride_x = self._stride_x * scale_x
            tile_display_stride_y = self._stride_y * scale_y
            tile_display_w = (
                self._tile_pixel_w * scale_x
                if self._tile_pixel_w
                else tile_display_stride_x
            )
            tile_display_h = (
                self._tile_pixel_h * scale_y
                if self._tile_pixel_h
                else tile_display_stride_y
            )
        else:
            tile_display_stride_x = display_w / self._tiles_x
            tile_display_stride_y = display_h / self._tiles_y
            tile_display_w = tile_display_stride_x
            tile_display_h = tile_display_stride_y

        if min(tile_display_w, tile_display_h) < self._LABEL_MIN_TILE_PX:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Font size: 8.5% of tile display height, min 10px
        font_px = max(10, int(tile_display_h * 0.085))
        font = QFont("Arial")
        font.setPixelSize(font_px)
        font.setBold(True)
        painter.setFont(font)
        fm = QFontMetrics(font)
        line_height = fm.height()

        # Get the visible rect from the scroll area to skip off-screen tiles
        visible_rect = None
        if self._scroll_area:
            vp = self._scroll_area.viewport().rect()
            # Map viewport rect to label coordinates
            top_left = self.mapFrom(self._scroll_area.viewport(), vp.topLeft())
            bottom_right = self.mapFrom(self._scroll_area.viewport(), vp.bottomRight())
            from PyQt5.QtCore import QRect

            visible_rect = QRect(top_left, bottom_right)

        painter.setPen(QColor(255, 255, 255))

        for coord in self._tile_coords:
            if len(coord) < 4:
                continue
            x_val, y_val, tile_x_idx, tile_y_idx = coord[:4]

            if self._invert_x:
                display_x_idx = (self._tiles_x - 1) - tile_x_idx
            else:
                display_x_idx = tile_x_idx

            tile_left = display_x_idx * tile_display_stride_x
            tile_top = tile_y_idx * tile_display_stride_y
            tile_cx = tile_left + tile_display_w / 2
            tile_cy = tile_top + tile_display_h / 2

            # Skip tiles not visible in viewport
            if visible_rect is not None:
                if (
                    tile_left + tile_display_w < visible_rect.left()
                    or tile_left > visible_rect.right()
                    or tile_top + tile_display_h < visible_rect.top()
                    or tile_top > visible_rect.bottom()
                ):
                    continue

            text1 = f"X:{x_val:.2f}"
            text2 = f"Y:{y_val:.2f}"
            t1w = fm.horizontalAdvance(text1)
            t2w = fm.horizontalAdvance(text2)

            painter.drawText(
                int(tile_cx - t1w / 2), int(tile_cy - line_height * 0.1), text1
            )
            painter.drawText(
                int(tile_cx - t2w / 2), int(tile_cy + line_height * 0.9), text2
            )

        painter.end()

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
        """Start panning on left click, or rect selection on Shift+left click."""
        if event.button() == Qt.LeftButton:
            if (
                event.modifiers() & Qt.ShiftModifier
                and self._tiles_x > 0
                and self._tiles_y > 0
            ):
                # Shift+left-click: start rectangle selection
                self._rect_selecting = True
                self._rect_start = event.pos()
                if self._rubber_band is None:
                    self._rubber_band = QRubberBand(QRubberBand.Rectangle, self)
                self._rubber_band.setGeometry(QRect(self._rect_start, QSize()))
                self._rubber_band.show()
                event.accept()
                return

            self._panning = True
            self._pan_start = event.globalPos()
            self._click_start = event.pos()  # Track local position for click detection
            self._drag_distance = 0
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        """Stop panning and detect clicks."""
        if event.button() == Qt.LeftButton:
            if self._rect_selecting:
                # Finish rectangle selection
                self._rect_selecting = False
                if self._rubber_band:
                    self._rubber_band.hide()
                self._handle_rect_selection(self._rect_start, event.pos())
                event.accept()
                return

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

    def _pos_to_tile_index(self, pos: QPoint):
        """Convert a widget position to (tile_x_idx, tile_y_idx).

        Uses stride-based mapping when tiles overlap, otherwise
        equal-division of the pixmap.

        Returns:
            (tile_x_idx, tile_y_idx) or None if outside valid range.
        """
        if self._original_pixmap is None:
            return None

        # Convert click position to original image coordinates
        img_x = pos.x() / self._zoom
        img_y = pos.y() / self._zoom

        # Use stride if available, else equal-division
        if self._stride_x is not None and self._stride_y is not None:
            stride_x = float(self._stride_x)
            stride_y = float(self._stride_y)
        else:
            img_w = self._original_pixmap.width()
            img_h = self._original_pixmap.height()
            stride_x = img_w / self._tiles_x
            stride_y = img_h / self._tiles_y

        # Calculate display tile index from stride
        display_x_idx = int(img_x / stride_x)
        tile_y_idx = int(img_y / stride_y)

        # Clamp to valid range
        display_x_idx = max(0, min(display_x_idx, self._tiles_x - 1))
        tile_y_idx = max(0, min(tile_y_idx, self._tiles_y - 1))

        # Convert display index back to tile index if inverted
        if self._invert_x:
            tile_x_idx = (self._tiles_x - 1) - display_x_idx
        else:
            tile_x_idx = display_x_idx

        return tile_x_idx, tile_y_idx

    def _handle_tile_click(self, pos: QPoint):
        """Calculate which tile was clicked and emit signal."""
        result = self._pos_to_tile_index(pos)
        if result is None:
            return
        tile_x_idx, tile_y_idx = result
        logger.debug(f"Tile clicked: ({tile_x_idx}, {tile_y_idx})")
        self.tile_clicked.emit(tile_x_idx, tile_y_idx)

    def _handle_tile_right_click(self, pos: QPoint):
        """Calculate which tile was right-clicked and emit signal for move to center Z."""
        result = self._pos_to_tile_index(pos)
        if result is None:
            return
        tile_x_idx, tile_y_idx = result
        logger.debug(f"Tile right-clicked: ({tile_x_idx}, {tile_y_idx})")
        self.tile_right_clicked.emit(tile_x_idx, tile_y_idx)

    def _handle_rect_selection(self, start: QPoint, end: QPoint):
        """Calculate all tiles within the drag rectangle and emit signal."""
        if self._original_pixmap is None or self._tiles_x <= 0 or self._tiles_y <= 0:
            return

        # Convert pixel positions to original image coordinates
        img_x1 = start.x() / self._zoom
        img_y1 = start.y() / self._zoom
        img_x2 = end.x() / self._zoom
        img_y2 = end.y() / self._zoom

        # Normalize so (x1,y1) is top-left
        if img_x1 > img_x2:
            img_x1, img_x2 = img_x2, img_x1
        if img_y1 > img_y2:
            img_y1, img_y2 = img_y2, img_y1

        # Use stride if available, else equal-division
        if self._stride_x is not None and self._stride_y is not None:
            stride_x = float(self._stride_x)
            stride_y = float(self._stride_y)
        else:
            img_w = self._original_pixmap.width()
            img_h = self._original_pixmap.height()
            stride_x = img_w / self._tiles_x
            stride_y = img_h / self._tiles_y

        # Find display tile index range
        display_x_min = max(0, int(img_x1 / stride_x))
        display_x_max = min(self._tiles_x - 1, int(img_x2 / stride_x))
        ty_min = max(0, int(img_y1 / stride_y))
        ty_max = min(self._tiles_y - 1, int(img_y2 / stride_y))

        # Build set of selected tiles, converting display index to tile index
        selected = set()
        for dy in range(ty_min, ty_max + 1):
            for dx in range(display_x_min, display_x_max + 1):
                if self._invert_x:
                    tx = (self._tiles_x - 1) - dx
                else:
                    tx = dx
                selected.add((tx, dy))

        if selected:
            logger.debug(f"Rectangle selected {len(selected)} tiles")
            self.tiles_rect_selected.emit(selected)

    def mouseMoveEvent(self, event: QMouseEvent):
        """Pan while dragging."""
        if self._rect_selecting and self._rubber_band:
            # Update rubber band geometry during shift+drag
            self._rubber_band.setGeometry(
                QRect(self._rect_start, event.pos()).normalized()
            )
            event.accept()
            return

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

        logger.debug(
            f"fit_to_view: image={img_w}x{img_h}, view={view_w}x{view_h}, "
            f"scale_x={scale_x:.4f}, scale_y={scale_y:.4f}, zoom={self._zoom:.4f}"
        )

        self._update_display()

    @property
    def zoom_level(self) -> float:
        return self._zoom
