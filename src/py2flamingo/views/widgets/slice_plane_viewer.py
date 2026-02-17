"""SlicePlaneViewer - 2D MIP slice plane viewer with overlays.

A self-contained QFrame widget that shows MIP (Maximum Intensity Projection)
views with sample holder, objective, and viewing frame position overlays.
Supports pan/zoom interaction and multi-channel display with colormaps.
"""

import numpy as np
from typing import Optional, Dict, Tuple

from PyQt5.QtWidgets import QFrame, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage

# Axis colors matching napari 3D viewer
AXIS_COLORS = {
    'x': '#008B8B',  # Cyan
    'y': '#8B008B',  # Magenta
    'z': '#8B8B00',  # Yellow/Olive
}


class SlicePlaneViewer(QFrame):
    """2D slice plane viewer with colored borders and overlays.

    Shows MIP projection with sample holder, objective, and viewing frame positions.
    Border colors match the napari 3D viewer axis colors.
    Supports pan/zoom interaction and multi-channel display with colormaps.
    """

    # Signal emitted when user double-clicks to move (axis1_value, axis2_value)
    position_clicked = pyqtSignal(float, float)

    # Colormaps for multi-channel display
    CHANNEL_COLORMAPS = {
        'blue': lambda v: np.stack([np.zeros_like(v), np.zeros_like(v), v], axis=-1),
        'cyan': lambda v: np.stack([np.zeros_like(v), v, v], axis=-1),
        'green': lambda v: np.stack([np.zeros_like(v), v, np.zeros_like(v)], axis=-1),
        'red': lambda v: np.stack([v, np.zeros_like(v), np.zeros_like(v)], axis=-1),
        'magenta': lambda v: np.stack([v, np.zeros_like(v), v], axis=-1),
        'yellow': lambda v: np.stack([v, v, np.zeros_like(v)], axis=-1),
        'gray': lambda v: np.stack([v, v, v], axis=-1),
    }

    def __init__(self, plane: str, h_axis: str, v_axis: str,
                 width: int, height: int, h_axis_inverted: bool = False,
                 v_axis_inverted: bool = False, parent=None):
        """
        Initialize slice plane viewer.

        Args:
            plane: Plane identifier ('xz', 'xy', 'yz')
            h_axis: Horizontal axis ('x', 'y', or 'z')
            v_axis: Vertical axis ('x', 'y', or 'z')
            width: Widget width in pixels
            height: Widget height in pixels
            h_axis_inverted: If True, h_range[1] (max) is at left, h_range[0] (min) at right.
                           Use for axes where the stage is inverted (like X axis on Flamingo).
            v_axis_inverted: If True, v_range[0] (min) is at bottom, v_range[1] (max) at top.
                           Use for axes where physical "up" is positive (like Y axis).
            parent: Parent widget
        """
        super().__init__(parent)

        self.plane = plane
        self.h_axis = h_axis
        self.v_axis = v_axis
        self._width = width
        self._height = height
        self._h_axis_inverted = h_axis_inverted
        self._v_axis_inverted = v_axis_inverted

        # Physical coordinate ranges (will be set from config)
        self.h_range = (0.0, 1.0)  # (min, max) in mm
        self.v_range = (0.0, 1.0)  # (min, max) in mm

        # Current MIP data (single channel for backwards compatibility)
        self._mip_data: Optional[np.ndarray] = None
        self._contrast_limits = (0, 65535)

        # Multi-channel MIP data: channel_id -> {data, colormap, contrast, visible}
        self._channel_mips: Dict[int, np.ndarray] = {}
        self._channel_settings: Dict[int, dict] = {}

        # Overlay positions (in physical coordinates, mm)
        self._holder_pos: Optional[Tuple[float, float]] = None  # (h, v)
        self._objective_pos: Optional[Tuple[float, float]] = None
        self._frame_pos: Optional[Tuple[float, float, float, float]] = None  # (h1, v1, h2, v2)

        # Target marker for double-click navigation
        self._target_pos: Optional[Tuple[float, float]] = None  # (h, v) target position
        self._target_active: bool = True  # True = orange (active), False = purple (stale)

        # Focal plane indicator position (for showing current slice position)
        self._focal_plane_pos: Optional[float] = None  # Position along the third axis

        # Pan/zoom state
        self._zoom_level: float = 1.0  # Zoom factor (1.0 = fit to widget)
        self._pan_offset: Tuple[float, float] = (0.0, 0.0)  # Pan offset in pixels
        self._drag_start: Optional[Tuple[int, int]] = None  # For tracking drag gesture
        self._drag_start_pan: Optional[Tuple[float, float]] = None  # Pan offset at drag start
        self._is_dragging: bool = False  # Distinguish click vs drag

        # Mouse position tracking for coordinate display
        self._mouse_pos: Optional[Tuple[float, float]] = None  # Current mouse position in physical coords
        self._show_axis_labels: bool = True  # Show min/max axis labels
        self._show_coordinate_readout: bool = True  # Show mouse position readout

        # Setup UI
        self._setup_ui()

    def _setup_ui(self):
        """Setup the viewer UI with colored borders."""
        self.setFixedSize(self._width, self._height)

        # Get border colors from axis
        h_color = AXIS_COLORS.get(self.h_axis, '#444')
        v_color = AXIS_COLORS.get(self.v_axis, '#444')

        # Create colored border using stylesheet
        # Left/Right borders use horizontal axis color, Top/Bottom use vertical axis color
        self.setStyleSheet(f"""
            SlicePlaneViewer {{
                background-color: #1a1a1a;
                border-left: 3px solid {h_color};
                border-right: 3px solid {h_color};
                border-top: 3px solid {v_color};
                border-bottom: 3px solid {v_color};
            }}
        """)

        # Image label for MIP display
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background-color: transparent; border: none;")
        self.image_label.setText(f"{self.plane.upper()}\n(Double-click to move\nDrag to pan, scroll to zoom)")
        self.image_label.setStyleSheet("color: #666; background-color: transparent;")
        layout.addWidget(self.image_label)

        self.setLayout(layout)

        # Enable mouse tracking for pan/zoom interaction
        self.setMouseTracking(True)
        self.image_label.setMouseTracking(True)

    def set_ranges(self, h_range: Tuple[float, float], v_range: Tuple[float, float]):
        """Set the physical coordinate ranges for the axes."""
        self.h_range = h_range
        self.v_range = v_range

    def set_contrast_limits(self, limits: Tuple[int, int]):
        """Set contrast limits for MIP display."""
        self._contrast_limits = limits
        self._update_display()

    def set_mip_data(self, data: np.ndarray):
        """Set the MIP data to display."""
        self._mip_data = data
        self._update_display()

    def set_holder_position(self, h: float, v: float):
        """Set the sample holder position (in physical coordinates)."""
        self._holder_pos = (h, v)
        self._update_display()

    def set_objective_position(self, h: float, v: float):
        """Set the objective position (in physical coordinates)."""
        self._objective_pos = (h, v)
        self._update_display()

    def set_frame_position(self, h1: float, v1: float, h2: float, v2: float):
        """Set the viewing frame position (rectangle in physical coordinates)."""
        self._frame_pos = (h1, v1, h2, v2)
        self._update_display()

    def set_multi_channel_mip(self, channel_mips: Dict[int, np.ndarray],
                               channel_settings: Dict[int, dict]):
        """Set MIP data for multiple channels with display settings.

        Args:
            channel_mips: Dict mapping channel_id to 2D MIP array
            channel_settings: Dict mapping channel_id to settings dict with keys:
                - 'visible': bool
                - 'colormap': str (blue, cyan, green, red, magenta, yellow, gray)
                - 'contrast_min': int
                - 'contrast_max': int
        """
        self._channel_mips = channel_mips
        self._channel_settings = channel_settings
        self._mip_data = None  # Clear single-channel data when using multi-channel
        self._update_display()

    def set_target_position(self, h: float, v: float, active: bool = True):
        """Set target marker position for double-click navigation.

        Args:
            h: Horizontal coordinate in physical units
            v: Vertical coordinate in physical units
            active: True for orange (active target), False for purple (stale)
        """
        self._target_pos = (h, v)
        self._target_active = active
        self._update_display()

    def set_target_stale(self):
        """Mark the target marker as stale (changes color to purple)."""
        if self._target_pos is not None:
            self._target_active = False
            self._update_display()

    def clear_target_position(self):
        """Clear the target marker."""
        self._target_pos = None
        self._update_display()

    def set_focal_plane_position(self, pos: float):
        """Set the focal plane indicator position.

        Args:
            pos: Position along the third axis in physical units
        """
        self._focal_plane_pos = pos
        self._update_display()

    def reset_view(self):
        """Reset pan and zoom to default values."""
        self._zoom_level = 1.0
        self._pan_offset = (0.0, 0.0)
        self._update_display()

    def set_show_axis_labels(self, show: bool):
        """Enable or disable axis min/max labels on display edges."""
        self._show_axis_labels = show
        self._update_display()

    def set_show_coordinate_readout(self, show: bool):
        """Enable or disable mouse position coordinate readout."""
        self._show_coordinate_readout = show
        self._update_display()

    def _update_display(self):
        """Update the display with current MIP data and overlays."""
        from PyQt5.QtGui import QPainter, QPen, QColor, QBrush, QFont

        # Create image from MIP data
        display_width = self._width - 6  # Account for borders
        display_height = self._height - 6

        # Reserve margin space for axis labels outside the image area
        label_margin_top = 16
        label_margin_bottom = 16
        img_area_height = display_height - label_margin_top - label_margin_bottom

        # Determine if we have multi-channel or single-channel data
        has_multi_channel = bool(self._channel_mips)
        has_single_channel = self._mip_data is not None and self._mip_data.size > 0

        if has_multi_channel:
            # Multi-channel blending with colormaps
            rgb_image = self._blend_channels()
            if rgb_image is not None:
                h, w, _ = rgb_image.shape
                # Ensure contiguous array for QImage
                rgb_image = np.ascontiguousarray(rgb_image)
                qimage = QImage(rgb_image.data, w, h, w * 3, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(qimage)
            else:
                pixmap = QPixmap(display_width, img_area_height)
                pixmap.fill(Qt.black)
        elif has_single_channel:
            # Single channel grayscale (backwards compatibility)
            data = self._mip_data.astype(np.float32)
            min_val, max_val = self._contrast_limits
            if max_val > min_val:
                data = np.clip((data - min_val) / (max_val - min_val), 0, 1)
            else:
                data = np.zeros_like(data)

            # Convert to 8-bit
            data_8bit = (data * 255).astype(np.uint8)

            # Create QImage
            h, w = data_8bit.shape
            qimage = QImage(data_8bit.data, w, h, w, QImage.Format_Grayscale8)
            pixmap = QPixmap.fromImage(qimage)
        else:
            # Create empty pixmap
            pixmap = QPixmap(display_width, img_area_height)
            pixmap.fill(Qt.black)

        # Apply zoom and pan transforms - fit image within the reduced image area
        base_scale = min(display_width / pixmap.width(), img_area_height / pixmap.height()) if pixmap.width() > 0 else 1.0
        effective_scale = base_scale * self._zoom_level

        # Calculate scaled dimensions
        scaled_w = int(pixmap.width() * effective_scale)
        scaled_h = int(pixmap.height() * effective_scale)

        # Scale the pixmap
        if scaled_w > 0 and scaled_h > 0:
            scaled_pixmap = pixmap.scaled(scaled_w, scaled_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            scaled_pixmap = pixmap

        # Create final display pixmap with pan offset applied
        final_pixmap = QPixmap(display_width, display_height)
        final_pixmap.fill(Qt.black)

        painter = QPainter(final_pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Calculate centered position with pan offset, shifted down by top margin
        center_x = (display_width - scaled_pixmap.width()) / 2 + self._pan_offset[0]
        center_y = label_margin_top + (img_area_height - scaled_pixmap.height()) / 2 + self._pan_offset[1]

        # Draw the scaled MIP image
        painter.drawPixmap(int(center_x), int(center_y), scaled_pixmap)

        # Calculate scale factors for overlay positions (relative to the scaled image)
        img_w = scaled_pixmap.width()
        img_h = scaled_pixmap.height()
        h_scale = img_w / (self.h_range[1] - self.h_range[0]) if self.h_range[1] != self.h_range[0] else 1
        v_scale = img_h / (self.v_range[1] - self.v_range[0]) if self.v_range[1] != self.v_range[0] else 1

        def to_pixel(h_coord, v_coord):
            """Convert physical coordinates to pixel coordinates on the final pixmap."""
            if self._h_axis_inverted:
                # Inverted: h_range[1] (max) at left, h_range[0] (min) at right
                px = int((self.h_range[1] - h_coord) * h_scale + center_x)
            else:
                # Normal: h_range[0] (min) at left, h_range[1] (max) at right
                px = int((h_coord - self.h_range[0]) * h_scale + center_x)
            if self._v_axis_inverted:
                # Inverted: v_range[1] (max) at top, v_range[0] (min) at bottom
                py = int((self.v_range[1] - v_coord) * v_scale + center_y)
            else:
                # Normal: v_range[0] (min) at top, v_range[1] (max) at bottom
                py = int((v_coord - self.v_range[0]) * v_scale + center_y)
            return px, py

        # Draw focal plane indicator (cyan dashed line through objective position)
        if self._focal_plane_pos is not None:
            pen = QPen(QColor('#00FFFF'))
            pen.setWidth(1)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            if self.plane in ('xz', 'xy'):
                # Focal plane is a V-axis value: draw horizontal line spanning full width
                _, py = to_pixel(self.h_range[0], self._focal_plane_pos)
                painter.drawLine(int(center_x), py, int(center_x + img_w), py)
            elif self.plane == 'yz':
                # Focal plane is an H-axis value (Z): draw vertical line spanning full height
                px, _ = to_pixel(self._focal_plane_pos, self.v_range[0])
                painter.drawLine(px, int(center_y), px, int(center_y + img_h))

        # Draw target marker (orange active, purple stale)
        if self._target_pos:
            if self._target_active:
                pen = QPen(QColor('#FFA500'))  # Orange for active
                brush = QBrush(QColor('#FFA500'))
            else:
                pen = QPen(QColor('#9370DB'))  # Purple for stale
                brush = QBrush(QColor('#9370DB'))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            px, py = to_pixel(*self._target_pos)
            # Draw crosshair with circle
            painter.drawEllipse(px - 8, py - 8, 16, 16)
            painter.drawLine(px - 12, py, px - 4, py)
            painter.drawLine(px + 4, py, px + 12, py)
            painter.drawLine(px, py - 12, px, py - 4)
            painter.drawLine(px, py + 4, px, py + 12)

        # Draw objective indicator (gold/yellow)
        if self._objective_pos:
            pen = QPen(QColor('#FFCC00'))
            painter.setBrush(Qt.NoBrush)
            px, py = to_pixel(*self._objective_pos)
            obj_radius = max(6, min(40, min(img_w, img_h) // 6))
            if self.plane == 'xy':
                # Front view: objective is a circle in XY plane
                pen.setWidth(2)
                painter.setPen(pen)
                painter.drawEllipse(px - obj_radius, py - obj_radius,
                                    obj_radius * 2, obj_radius * 2)
            elif self.plane == 'xz':
                # Top-down view: objective circle seen edge-on → horizontal line at Z_min
                pen.setWidth(3)
                painter.setPen(pen)
                painter.drawLine(px - obj_radius, py, px + obj_radius, py)
            elif self.plane == 'yz':
                # Side view: objective circle seen edge-on → vertical line at Z_min
                pen.setWidth(3)
                painter.setPen(pen)
                painter.drawLine(px, py - obj_radius, px, py + obj_radius)

        # Draw sample holder position (white cross)
        if self._holder_pos:
            pen = QPen(QColor('#FFFFFF'))
            pen.setWidth(2)
            painter.setPen(pen)
            px, py = to_pixel(*self._holder_pos)
            # Draw as cross
            painter.drawLine(px - 10, py, px + 10, py)
            painter.drawLine(px, py - 10, px, py + 10)

        # Draw viewing frame (cyan dashed rectangle)
        if self._frame_pos:
            pen = QPen(QColor('#00FFFF'))
            pen.setWidth(1)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            px1, py1 = to_pixel(self._frame_pos[0], self._frame_pos[1])
            px2, py2 = to_pixel(self._frame_pos[2], self._frame_pos[3])
            painter.drawRect(min(px1, px2), min(py1, py2),
                           abs(px2 - px1), abs(py2 - py1))

        # Draw axis labels (min/max values at corners)
        if self._show_axis_labels:
            font = QFont("Monospace", 8)
            font.setStyleHint(QFont.Monospace)
            painter.setFont(font)

            # Semi-transparent background for labels
            label_bg = QColor(0, 0, 0, 160)
            label_fg = QColor(200, 200, 200)

            # Format axis labels with units
            h_min_str = f"{self.h_axis.upper()}:{self.h_range[0]:.1f}"
            h_max_str = f"{self.h_axis.upper()}:{self.h_range[1]:.1f}"
            v_min_str = f"{self.v_axis.upper()}:{self.v_range[0]:.1f}"
            v_max_str = f"{self.v_axis.upper()}:{self.v_range[1]:.1f}"

            # Draw labels at corners of the image area
            # Calculate visible image bounds
            img_left = max(0, int(center_x))
            img_right = min(display_width, int(center_x + img_w))
            img_top = max(0, int(center_y))
            img_bottom = min(display_height, int(center_y + img_h))

            # Helper to draw label with background
            def draw_label(text, x, y, align_right=False, align_bottom=False):
                metrics = painter.fontMetrics()
                text_width = metrics.horizontalAdvance(text)
                text_height = metrics.height()
                padding = 2

                if align_right:
                    x = x - text_width - padding * 2
                if align_bottom:
                    y = y - text_height - padding * 2

                # Draw background
                painter.fillRect(int(x), int(y), text_width + padding * 2, text_height + padding,
                               label_bg)
                # Draw text
                painter.setPen(label_fg)
                painter.drawText(int(x + padding), int(y + text_height - padding), text)

            # H-axis labels (below image in bottom margin)
            if self._h_axis_inverted:
                # Inverted: max at left, min at right
                draw_label(h_max_str, img_left + 2, img_bottom + 2)
                draw_label(h_min_str, img_right - 2, img_bottom + 2, align_right=True)
            else:
                # Normal: min at left, max at right
                draw_label(h_min_str, img_left + 2, img_bottom + 2)
                draw_label(h_max_str, img_right - 2, img_bottom + 2, align_right=True)

            # V-axis labels (above and below image in margin area)
            if self._v_axis_inverted:
                # Inverted: max at top, min at bottom
                draw_label(v_max_str, img_left + 2, img_top - 15)
                draw_label(v_min_str, img_right - 2, img_bottom + 2, align_right=True, align_bottom=True)
            else:
                # Normal: min at top, max at bottom
                draw_label(v_min_str, img_left + 2, img_top - 15)
                draw_label(v_max_str, img_right - 2, img_bottom + 2, align_right=True, align_bottom=True)

        # Draw coordinate readout (mouse position)
        if self._show_coordinate_readout and self._mouse_pos is not None:
            font = QFont("Monospace", 9)
            font.setStyleHint(QFont.Monospace)
            font.setBold(True)
            painter.setFont(font)

            h_coord, v_coord = self._mouse_pos
            coord_text = f"{self.h_axis.upper()}:{h_coord:.2f} {self.v_axis.upper()}:{v_coord:.2f} mm"

            metrics = painter.fontMetrics()
            text_width = metrics.horizontalAdvance(coord_text)
            text_height = metrics.height()
            padding = 3

            # Position at top-right corner
            x = display_width - text_width - padding * 2 - 4
            y = 4

            # Draw background with slight transparency
            painter.fillRect(int(x), int(y), text_width + padding * 2, text_height + padding,
                           QColor(0, 0, 0, 180))
            # Draw text in bright color
            painter.setPen(QColor(255, 255, 100))  # Yellow for visibility
            painter.drawText(int(x + padding), int(y + text_height - padding), coord_text)

        painter.end()

        self.image_label.setPixmap(final_pixmap)

    def _blend_channels(self) -> Optional[np.ndarray]:
        """Blend multiple channels into a single RGB image.

        Returns:
            RGB image as uint8 numpy array (H, W, 3) or None if no data
        """
        if not self._channel_mips:
            return None

        # Determine output shape from first available channel
        out_shape = None
        for ch_data in self._channel_mips.values():
            if ch_data is not None and ch_data.size > 0:
                out_shape = ch_data.shape
                break

        if out_shape is None:
            return None

        # Initialize RGB accumulator as float
        rgb_accum = np.zeros((*out_shape, 3), dtype=np.float32)

        for ch_id, ch_data in self._channel_mips.items():
            if ch_data is None or ch_data.size == 0:
                continue

            settings = self._channel_settings.get(ch_id, {})

            # Check visibility
            if not settings.get('visible', True):
                continue

            # Get contrast limits
            contrast_min = settings.get('contrast_min', 0)
            contrast_max = settings.get('contrast_max', 65535)

            # Normalize data using contrast limits
            data = ch_data.astype(np.float32)
            if contrast_max > contrast_min:
                data = np.clip((data - contrast_min) / (contrast_max - contrast_min), 0, 1)
            else:
                data = np.zeros_like(data)

            # Get colormap and apply
            colormap_name = settings.get('colormap', 'gray')
            colormap_fn = self.CHANNEL_COLORMAPS.get(colormap_name, self.CHANNEL_COLORMAPS['gray'])

            # Apply colormap (returns H, W, 3)
            ch_rgb = colormap_fn(data)

            # Additive blending
            rgb_accum += ch_rgb

        # Clip to valid range and convert to uint8
        rgb_accum = np.clip(rgb_accum, 0, 1)
        return (rgb_accum * 255).astype(np.uint8)

    def mousePressEvent(self, event):
        """Handle mouse press for pan gesture start."""
        if event.button() == Qt.LeftButton:
            # Start potential drag
            self._drag_start = (event.x(), event.y())
            self._drag_start_pan = self._pan_offset
            self._is_dragging = False

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle mouse move for pan gesture and coordinate tracking."""
        # Update mouse position for coordinate display
        coords = self._pixel_to_physical(event.x(), event.y())
        if coords != self._mouse_pos:
            self._mouse_pos = coords
            # Only redraw if we're showing the readout and not currently dragging
            if self._show_coordinate_readout and not self._is_dragging:
                self._update_display()

        if self._drag_start is not None:
            # Calculate drag distance
            dx = event.x() - self._drag_start[0]
            dy = event.y() - self._drag_start[1]

            # If moved more than 5 pixels, consider it a drag
            if abs(dx) > 5 or abs(dy) > 5 or self._is_dragging:
                self._is_dragging = True
                # Update pan offset
                self._pan_offset = (
                    self._drag_start_pan[0] + dx,
                    self._drag_start_pan[1] + dy
                )
                self._update_display()

        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        """Handle mouse leaving the widget - clear coordinate display."""
        self._mouse_pos = None
        if self._show_coordinate_readout:
            self._update_display()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        """Handle mouse release - end pan gesture."""
        if event.button() == Qt.LeftButton:
            self._drag_start = None
            self._drag_start_pan = None

        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Handle double-click for click-to-move and set target marker."""
        if event.button() == Qt.LeftButton:
            # Convert click position to physical coordinates
            coords = self._pixel_to_physical(event.x(), event.y())
            if coords is not None:
                h_coord, v_coord = coords

                # Set target marker (active)
                self.set_target_position(h_coord, v_coord, active=True)

                # Emit signal with coordinates
                self.position_clicked.emit(h_coord, v_coord)

        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        """Handle mouse wheel for zoom."""
        # Get scroll delta
        delta = event.angleDelta().y()

        if delta != 0:
            # Zoom factor per scroll step
            zoom_factor = 1.1 if delta > 0 else 0.9

            # Calculate new zoom level (clamp between 0.5x and 10x)
            new_zoom = self._zoom_level * zoom_factor
            new_zoom = max(0.5, min(10.0, new_zoom))

            if new_zoom != self._zoom_level:
                # Get mouse position relative to widget
                mouse_x = event.x() - 3  # Account for border
                mouse_y = event.y() - 3

                # Calculate the display area center
                display_width = self._width - 6
                display_height = self._height - 6
                center_x = display_width / 2
                center_y = display_height / 2

                # Adjust pan to zoom centered on mouse position
                # The mouse position should stay at the same physical location
                scale_change = new_zoom / self._zoom_level

                # Current offset from center to mouse
                offset_x = mouse_x - center_x - self._pan_offset[0]
                offset_y = mouse_y - center_y - self._pan_offset[1]

                # New pan offset to keep mouse at same physical location
                self._pan_offset = (
                    self._pan_offset[0] - offset_x * (scale_change - 1),
                    self._pan_offset[1] - offset_y * (scale_change - 1)
                )

                self._zoom_level = new_zoom
                self._update_display()

        event.accept()

    def _pixel_to_physical(self, px: int, py: int) -> Optional[Tuple[float, float]]:
        """Convert pixel coordinates to physical coordinates.

        Args:
            px: Pixel x coordinate (relative to widget)
            py: Pixel y coordinate (relative to widget)

        Returns:
            (h_coord, v_coord) in physical units, or None if outside image
        """
        # Account for border
        px = px - 3
        py = py - 3

        display_width = self._width - 6
        display_height = self._height - 6

        # Must match the margin constants from _update_display
        label_margin_top = 16
        label_margin_bottom = 16
        img_area_height = display_height - label_margin_top - label_margin_bottom

        # Get original image dimensions from channel data or MIP.
        # When no data is loaded, use display area dimensions to match the
        # empty pixmap created in _update_display (display_width x img_area_height),
        # so pixel-to-physical mapping stays consistent with overlay drawing.
        if self._channel_mips:
            for ch_data in self._channel_mips.values():
                if ch_data is not None and ch_data.size > 0:
                    orig_h, orig_w = ch_data.shape
                    break
            else:
                orig_h, orig_w = img_area_height, display_width
        elif self._mip_data is not None:
            orig_h, orig_w = self._mip_data.shape
        else:
            orig_h, orig_w = img_area_height, display_width

        # Calculate scale to fit image in reduced area (same as _update_display)
        base_scale = min(display_width / orig_w, img_area_height / orig_h) if orig_w > 0 and orig_h > 0 else 1.0
        effective_scale = base_scale * self._zoom_level

        scaled_w = orig_w * effective_scale
        scaled_h = orig_h * effective_scale

        # Image position on display (shifted down by top margin)
        img_x = (display_width - scaled_w) / 2 + self._pan_offset[0]
        img_y = label_margin_top + (img_area_height - scaled_h) / 2 + self._pan_offset[1]

        # Check if click is within image bounds
        if px < img_x or px > img_x + scaled_w or py < img_y or py > img_y + scaled_h:
            # Still calculate coordinates even if outside - useful for navigation
            pass

        # Convert to physical coordinates
        if self._h_axis_inverted:
            # Inverted: left of image is h_max, right is h_min
            h_coord = self.h_range[1] - ((px - img_x) / scaled_w) * (self.h_range[1] - self.h_range[0])
        else:
            # Normal: left of image is h_min, right is h_max
            h_coord = self.h_range[0] + ((px - img_x) / scaled_w) * (self.h_range[1] - self.h_range[0])
        if self._v_axis_inverted:
            # Inverted: top of image is v_max, bottom is v_min
            v_coord = self.v_range[1] - ((py - img_y) / scaled_h) * (self.v_range[1] - self.v_range[0])
        else:
            # Normal: top of image is v_min, bottom is v_max
            v_coord = self.v_range[0] + ((py - img_y) / scaled_h) * (self.v_range[1] - self.v_range[0])

        return (h_coord, v_coord)
