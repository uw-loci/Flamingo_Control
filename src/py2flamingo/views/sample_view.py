"""
Sample View - Integrated Sample Interaction Window.

Combines all elements needed for sample viewing and interaction:
- Live camera feed with embedded display controls
- 3D volume visualization (napari)
- Position sliders for stage control
- Illumination controls (always visible)
- MIP plane views with click-to-move
- Workflow progress placeholder
- Dialog launcher buttons
"""

import logging
import time
import numpy as np
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QSlider, QComboBox, QCheckBox, QProgressBar,
    QSplitter, QSizePolicy, QFrame, QSpinBox,
    QGridLayout, QLineEdit, QTabWidget
)
from PyQt5.QtCore import Qt, pyqtSlot, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage, QFont, QDoubleValidator, QShowEvent, QCloseEvent, QHideEvent, QIcon

from py2flamingo.services.window_geometry_manager import PersistentDialog
from py2flamingo.resources import get_app_icon

if TYPE_CHECKING:
    from py2flamingo.services.window_geometry_manager import WindowGeometryManager
from superqt import QRangeSlider

from py2flamingo.views.laser_led_control_panel import LaserLEDControlPanel
from py2flamingo.views.colors import SUCCESS_COLOR, ERROR_COLOR, WARNING_BG
from py2flamingo.services.position_preset_service import PositionPresetService

# Import camera state for live view control
from py2flamingo.controllers.camera_controller import CameraState

# napari imports for 3D visualization
try:
    import napari
    NAPARI_AVAILABLE = True
except ImportError:
    NAPARI_AVAILABLE = False
    napari = None

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
                 width: int, height: int, parent=None):
        """
        Initialize slice plane viewer.

        Args:
            plane: Plane identifier ('xz', 'xy', 'yz')
            h_axis: Horizontal axis ('x', 'y', or 'z')
            v_axis: Vertical axis ('x', 'y', or 'z')
            width: Widget width in pixels
            height: Widget height in pixels
            parent: Parent widget
        """
        super().__init__(parent)

        self.plane = plane
        self.h_axis = h_axis
        self.v_axis = v_axis
        self._width = width
        self._height = height

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
        from PyQt5.QtGui import QPainter, QPen, QColor, QBrush

        # Create image from MIP data
        display_width = self._width - 6  # Account for borders
        display_height = self._height - 6

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
                pixmap = QPixmap(display_width, display_height)
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
            pixmap = QPixmap(display_width, display_height)
            pixmap.fill(Qt.black)

        # Apply zoom and pan transforms
        base_scale = min(display_width / pixmap.width(), display_height / pixmap.height()) if pixmap.width() > 0 else 1.0
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

        # Calculate centered position with pan offset
        center_x = (display_width - scaled_pixmap.width()) / 2 + self._pan_offset[0]
        center_y = (display_height - scaled_pixmap.height()) / 2 + self._pan_offset[1]

        # Draw the scaled MIP image
        painter.drawPixmap(int(center_x), int(center_y), scaled_pixmap)

        # Calculate scale factors for overlay positions (relative to the scaled image)
        img_w = scaled_pixmap.width()
        img_h = scaled_pixmap.height()
        h_scale = img_w / (self.h_range[1] - self.h_range[0]) if self.h_range[1] != self.h_range[0] else 1
        v_scale = img_h / (self.v_range[1] - self.v_range[0]) if self.v_range[1] != self.v_range[0] else 1

        def to_pixel(h_coord, v_coord):
            """Convert physical coordinates to pixel coordinates on the final pixmap."""
            px = int((h_coord - self.h_range[0]) * h_scale + center_x)
            py = int((v_coord - self.v_range[0]) * v_scale + center_y)
            return px, py

        # Draw focal plane indicator (cyan dashed horizontal line)
        if self._focal_plane_pos is not None:
            pen = QPen(QColor('#00FFFF'))
            pen.setWidth(1)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            # The focal plane is drawn as a line perpendicular to the projection axis
            # For XZ plane: focal plane is Y position, draw horizontal line at that Y
            # For XY plane: focal plane is Z position, draw horizontal line at that Z
            # For YZ plane: focal plane is X position, draw vertical line at that X
            if self.plane == 'xz':
                # Show Y focal position - not applicable (Y is projected out)
                pass
            elif self.plane == 'xy':
                # Show Z focal position as horizontal line
                py = int((self._focal_plane_pos - self.v_range[0]) * v_scale + center_y)
                painter.drawLine(int(center_x), py, int(center_x + img_w), py)
            elif self.plane == 'yz':
                # Show X focal position as vertical line (h_axis is Z, v_axis is Y)
                # Actually for YZ, X is the projected axis, so we'd show X position
                # But X isn't one of our axes here, so skip
                pass

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

        # Draw objective (green circle)
        if self._objective_pos:
            pen = QPen(QColor('#00FF00'))
            pen.setWidth(2)
            painter.setPen(pen)
            px, py = to_pixel(*self._objective_pos)
            painter.drawEllipse(px - 8, py - 8, 16, 16)

        # Draw sample holder position (white cross)
        if self._holder_pos:
            pen = QPen(QColor('#FFFFFF'))
            pen.setWidth(2)
            painter.setPen(pen)
            px, py = to_pixel(*self._holder_pos)
            # Draw as small cross
            painter.drawLine(px - 6, py, px + 6, py)
            painter.drawLine(px, py - 6, px, py + 6)

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
            from PyQt5.QtGui import QFont
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

            # H-axis labels (left and right edges)
            draw_label(h_min_str, img_left + 2, img_bottom - 16)
            draw_label(h_max_str, img_right - 2, img_bottom - 16, align_right=True)

            # V-axis labels (top and bottom edges)
            draw_label(v_min_str, img_left + 2, img_top + 2)
            draw_label(v_max_str, img_left + 2, img_bottom - 32)

        # Draw coordinate readout (mouse position)
        if self._show_coordinate_readout and self._mouse_pos is not None:
            from PyQt5.QtGui import QFont
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

        # Get scaled image dimensions
        pixmap = self.image_label.pixmap()
        if not pixmap:
            return None

        # Calculate the actual image position considering zoom and pan
        base_scale = min(display_width / max(1, pixmap.width()), display_height / max(1, pixmap.height()))
        effective_scale = base_scale * self._zoom_level

        # Estimate original image dimensions from channel data or MIP
        if self._channel_mips:
            for ch_data in self._channel_mips.values():
                if ch_data is not None and ch_data.size > 0:
                    orig_h, orig_w = ch_data.shape
                    break
            else:
                return None
        elif self._mip_data is not None:
            orig_h, orig_w = self._mip_data.shape
        else:
            return None

        scaled_w = orig_w * effective_scale
        scaled_h = orig_h * effective_scale

        # Image position on display
        img_x = (display_width - scaled_w) / 2 + self._pan_offset[0]
        img_y = (display_height - scaled_h) / 2 + self._pan_offset[1]

        # Check if click is within image bounds
        if px < img_x or px > img_x + scaled_w or py < img_y or py > img_y + scaled_h:
            # Still calculate coordinates even if outside - useful for navigation
            pass

        # Convert to physical coordinates
        h_coord = self.h_range[0] + ((px - img_x) / scaled_w) * (self.h_range[1] - self.h_range[0])
        v_coord = self.v_range[0] + ((py - img_y) / scaled_h) * (self.v_range[1] - self.v_range[0])

        return (h_coord, v_coord)


class ViewerControlsDialog(PersistentDialog):
    """Dialog for controlling napari viewer settings.

    Provides controls for:
    - Channel visibility, colormap, opacity, and contrast
    - Rendering mode (MIP, Volume, etc.)
    - Display settings (chamber wireframe, objective indicator)
    - Camera/view reset
    """

    # Signals to emit when settings change
    channel_visibility_changed = pyqtSignal(int, bool)
    channel_colormap_changed = pyqtSignal(int, str)
    channel_opacity_changed = pyqtSignal(int, float)
    channel_contrast_changed = pyqtSignal(int, tuple)
    rendering_mode_changed = pyqtSignal(str)
    # Signal to request plane view update (emitted on any visual change)
    plane_views_update_requested = pyqtSignal()

    def __init__(self, viewer_container, config: dict, parent=None):
        """
        Initialize ViewerControlsDialog.

        Args:
            viewer_container: Object with 'viewer' and 'channel_layers' attributes (SampleView)
            config: Visualization config dict
            parent: Parent widget
        """
        super().__init__(parent)
        self.viewer_container = viewer_container  # SampleView or similar
        self.config = config
        self.logger = logging.getLogger(__name__)

        self.setWindowTitle("Viewer Controls")
        self.setMinimumSize(450, 550)

        # Store widget references for each channel
        self.channel_controls: dict = {}

        self._setup_ui()
        self._sync_from_viewer()

    def _setup_ui(self) -> None:
        """Create the dialog UI with channel controls and display settings."""
        main_layout = QVBoxLayout()

        # Tab widget for organized controls
        tabs = QTabWidget()

        # Tab 1: Channel Controls
        channel_tab = self._create_channel_controls_tab()
        tabs.addTab(channel_tab, "Channels")

        # Tab 2: Display Settings
        display_tab = self._create_display_settings_tab()
        tabs.addTab(display_tab, "Display")

        main_layout.addWidget(tabs)

        # Button bar at bottom
        button_layout = QHBoxLayout()

        # Reset to Defaults button
        self.reset_btn = QPushButton("Reset to Defaults")
        self.reset_btn.clicked.connect(self._reset_to_defaults)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)

        button_layout.addWidget(self.reset_btn)
        button_layout.addStretch()
        button_layout.addWidget(close_btn)

        main_layout.addLayout(button_layout)
        self.setLayout(main_layout)

    def _create_channel_controls_tab(self) -> QWidget:
        """Create channel control widgets for all 4 channels."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Get channel configs from visualization config
        channels_config = self.config.get('channels', [])

        for i in range(4):
            ch_config = channels_config[i] if i < len(channels_config) else {}
            ch_name = ch_config.get('name', f'Channel {i+1}')

            group = QGroupBox(f"Channel {i+1}: {ch_name}")
            ch_layout = QGridLayout()
            ch_layout.setColumnStretch(1, 1)  # Make middle column stretch

            # Row 0: Visibility checkbox
            visible_cb = QCheckBox("Visible")
            visible_cb.setChecked(ch_config.get('default_visible', True))
            ch_layout.addWidget(visible_cb, 0, 0, 1, 3)

            # Row 1: Colormap selector
            ch_layout.addWidget(QLabel("Colormap:"), 1, 0)
            colormap_combo = QComboBox()
            colormap_combo.addItems(['blue', 'cyan', 'green', 'red', 'magenta', 'yellow', 'gray'])
            colormap_combo.setCurrentText(ch_config.get('default_colormap', 'gray'))
            ch_layout.addWidget(colormap_combo, 1, 1, 1, 2)

            # Row 2: Opacity slider
            ch_layout.addWidget(QLabel("Opacity:"), 2, 0)
            opacity_slider = QSlider(Qt.Horizontal)
            opacity_slider.setRange(0, 100)
            opacity_slider.setValue(int(ch_config.get('opacity', 0.8) * 100))
            ch_layout.addWidget(opacity_slider, 2, 1)
            opacity_label = QLabel(f"{opacity_slider.value()}%")
            opacity_label.setMinimumWidth(40)
            ch_layout.addWidget(opacity_label, 2, 2)

            # Row 3: Contrast range slider
            ch_layout.addWidget(QLabel("Contrast:"), 3, 0)
            from superqt import QRangeSlider
            contrast_slider = QRangeSlider(Qt.Horizontal)
            contrast_slider.setRange(0, 65535)
            min_val = ch_config.get('default_contrast_min', 0)
            max_val = ch_config.get('default_contrast_max', 500)
            contrast_slider.setValue((min_val, max_val))
            ch_layout.addWidget(contrast_slider, 3, 1)
            contrast_label = QLabel(f"{min_val} - {max_val}")
            contrast_label.setMinimumWidth(80)
            ch_layout.addWidget(contrast_label, 3, 2)

            group.setLayout(ch_layout)
            layout.addWidget(group)

            # Store references
            self.channel_controls[i] = {
                'visible': visible_cb,
                'colormap': colormap_combo,
                'opacity': opacity_slider,
                'opacity_label': opacity_label,
                'contrast': contrast_slider,
                'contrast_label': contrast_label
            }

            # Connect signals (live updates)
            visible_cb.toggled.connect(lambda v, ch=i: self._on_visibility_changed(ch, v))
            colormap_combo.currentTextChanged.connect(lambda c, ch=i: self._on_colormap_changed(ch, c))
            opacity_slider.valueChanged.connect(
                lambda v, ch=i, lbl=opacity_label: self._on_opacity_changed(ch, v, lbl)
            )
            contrast_slider.valueChanged.connect(
                lambda v, ch=i, lbl=contrast_label: self._on_contrast_changed(ch, v, lbl)
            )

        layout.addStretch()
        return widget

    def _create_display_settings_tab(self) -> QWidget:
        """Create display settings controls."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Rendering mode group
        render_group = QGroupBox("Rendering")
        render_layout = QHBoxLayout()
        render_layout.addWidget(QLabel("Mode:"))
        self.rendering_combo = QComboBox()
        self.rendering_combo.addItems(['mip', 'minip', 'average', 'iso'])
        self.rendering_combo.setCurrentText('mip')
        self.rendering_combo.currentTextChanged.connect(self._on_rendering_mode_changed)
        render_layout.addWidget(self.rendering_combo)
        render_layout.addStretch()
        render_group.setLayout(render_layout)
        layout.addWidget(render_group)

        # Display elements group
        elements_group = QGroupBox("Display Elements")
        elem_layout = QVBoxLayout()

        self.show_chamber_cb = QCheckBox("Show Chamber Wireframe")
        self.show_chamber_cb.setChecked(True)
        self.show_chamber_cb.toggled.connect(self._on_chamber_visibility_changed)
        elem_layout.addWidget(self.show_chamber_cb)

        self.show_objective_cb = QCheckBox("Show Objective Position")
        self.show_objective_cb.setChecked(True)
        self.show_objective_cb.toggled.connect(self._on_objective_visibility_changed)
        elem_layout.addWidget(self.show_objective_cb)

        self.show_focus_frame_cb = QCheckBox("Show XY Focus Frame")
        self.show_focus_frame_cb.setChecked(True)
        self.show_focus_frame_cb.toggled.connect(self._on_focus_frame_visibility_changed)
        elem_layout.addWidget(self.show_focus_frame_cb)

        self.show_axes_cb = QCheckBox("Show Coordinate Axes")
        self.show_axes_cb.setChecked(True)
        self.show_axes_cb.toggled.connect(self._on_axes_visibility_changed)
        elem_layout.addWidget(self.show_axes_cb)

        elements_group.setLayout(elem_layout)
        layout.addWidget(elements_group)

        # Camera controls group
        camera_group = QGroupBox("Camera")
        camera_layout = QVBoxLayout()

        self.reset_view_btn = QPushButton("Reset View to Default")
        self.reset_view_btn.clicked.connect(self._on_reset_view)
        camera_layout.addWidget(self.reset_view_btn)

        camera_group.setLayout(camera_layout)
        layout.addWidget(camera_group)

        layout.addStretch()
        return widget

    def _get_viewer(self):
        """Get the napari viewer from the container."""
        if self.viewer_container:
            return getattr(self.viewer_container, 'viewer', None)
        return None

    def _get_channel_layer(self, channel_id: int):
        """Get the napari layer for a specific channel."""
        if self.viewer_container and hasattr(self.viewer_container, 'channel_layers'):
            return self.viewer_container.channel_layers.get(channel_id)
        return None

    def _on_visibility_changed(self, channel_id: int, visible: bool) -> None:
        """Handle channel visibility toggle."""
        layer = self._get_channel_layer(channel_id)
        if layer:
            layer.visible = visible
        self.channel_visibility_changed.emit(channel_id, visible)
        self.plane_views_update_requested.emit()

    def _on_colormap_changed(self, channel_id: int, colormap: str) -> None:
        """Handle colormap change for a channel."""
        layer = self._get_channel_layer(channel_id)
        if layer:
            try:
                layer.colormap = colormap
            except Exception as e:
                self.logger.warning(f"Failed to set colormap {colormap}: {e}")
        self.channel_colormap_changed.emit(channel_id, colormap)
        self.plane_views_update_requested.emit()

    def _on_opacity_changed(self, channel_id: int, value: int, label: QLabel) -> None:
        """Handle opacity slider change."""
        opacity = value / 100.0
        label.setText(f"{value}%")
        layer = self._get_channel_layer(channel_id)
        if layer:
            layer.opacity = opacity
        self.channel_opacity_changed.emit(channel_id, opacity)

    def _on_contrast_changed(self, channel_id: int, value: tuple, label: QLabel) -> None:
        """Handle contrast range slider change."""
        min_val, max_val = value
        label.setText(f"{min_val} - {max_val}")
        layer = self._get_channel_layer(channel_id)
        if layer:
            layer.contrast_limits = (min_val, max_val)
        self.channel_contrast_changed.emit(channel_id, value)
        self.plane_views_update_requested.emit()

    def _on_rendering_mode_changed(self, mode: str) -> None:
        """Change rendering mode for all channel layers."""
        if self.viewer_container and hasattr(self.viewer_container, 'channel_layers'):
            for layer in self.viewer_container.channel_layers.values():
                try:
                    layer.rendering = mode
                except Exception as e:
                    self.logger.warning(f"Failed to set rendering mode {mode}: {e}")
        self.rendering_mode_changed.emit(mode)

    def _on_chamber_visibility_changed(self, visible: bool) -> None:
        """Toggle chamber wireframe visibility."""
        viewer = self._get_viewer()
        if viewer:
            for layer_name in ['Chamber Z-edges', 'Chamber Y-edges', 'Chamber X-edges']:
                if layer_name in viewer.layers:
                    viewer.layers[layer_name].visible = visible

    def _on_objective_visibility_changed(self, visible: bool) -> None:
        """Toggle objective indicator visibility."""
        viewer = self._get_viewer()
        if viewer and 'Objective' in viewer.layers:
            viewer.layers['Objective'].visible = visible

    def _on_focus_frame_visibility_changed(self, visible: bool) -> None:
        """Toggle XY focus frame visibility."""
        viewer = self._get_viewer()
        if viewer and 'XY Focus Frame' in viewer.layers:
            viewer.layers['XY Focus Frame'].visible = visible

    def _on_axes_visibility_changed(self, visible: bool) -> None:
        """Toggle coordinate axes visibility."""
        viewer = self._get_viewer()
        if viewer and hasattr(viewer, 'axes'):
            viewer.axes.visible = visible

    def _on_reset_view(self) -> None:
        """Reset camera zoom (preserves orientation from 3D window)."""
        viewer = self._get_viewer()
        if viewer:
            # Only set zoom - don't override camera.angles as 3D window has correct orientation
            viewer.camera.zoom = 1.57

    def _sync_from_viewer(self) -> None:
        """Sync dialog controls with current napari viewer state."""
        if not self.viewer_container:
            return

        # Sync channel controls
        for ch_id, controls in self.channel_controls.items():
            layer = self._get_channel_layer(ch_id)
            if layer:
                # Block signals to prevent feedback loops
                controls['visible'].blockSignals(True)
                controls['colormap'].blockSignals(True)
                controls['opacity'].blockSignals(True)
                controls['contrast'].blockSignals(True)

                controls['visible'].setChecked(layer.visible)

                # Get colormap name
                colormap_name = layer.colormap.name if hasattr(layer.colormap, 'name') else str(layer.colormap)
                idx = controls['colormap'].findText(colormap_name)
                if idx >= 0:
                    controls['colormap'].setCurrentIndex(idx)

                controls['opacity'].setValue(int(layer.opacity * 100))
                controls['opacity_label'].setText(f"{int(layer.opacity * 100)}%")

                if hasattr(layer, 'contrast_limits') and layer.contrast_limits:
                    min_val, max_val = layer.contrast_limits
                    controls['contrast'].setValue((int(min_val), int(max_val)))
                    controls['contrast_label'].setText(f"{int(min_val)} - {int(max_val)}")

                controls['visible'].blockSignals(False)
                controls['colormap'].blockSignals(False)
                controls['opacity'].blockSignals(False)
                controls['contrast'].blockSignals(False)

        # Sync display settings
        viewer = self._get_viewer()
        if viewer:
            # Rendering mode from first channel layer
            if hasattr(self.viewer_container, 'channel_layers') and self.viewer_container.channel_layers:
                first_layer = list(self.viewer_container.channel_layers.values())[0]
                self.rendering_combo.blockSignals(True)
                self.rendering_combo.setCurrentText(first_layer.rendering)
                self.rendering_combo.blockSignals(False)

            # Chamber visibility
            chamber_visible = any(
                viewer.layers[name].visible
                for name in ['Chamber Z-edges', 'Chamber Y-edges', 'Chamber X-edges']
                if name in viewer.layers
            )
            self.show_chamber_cb.blockSignals(True)
            self.show_chamber_cb.setChecked(chamber_visible)
            self.show_chamber_cb.blockSignals(False)

            # Objective visibility
            if 'Objective' in viewer.layers:
                self.show_objective_cb.blockSignals(True)
                self.show_objective_cb.setChecked(viewer.layers['Objective'].visible)
                self.show_objective_cb.blockSignals(False)

            # Focus frame visibility
            if 'XY Focus Frame' in viewer.layers:
                self.show_focus_frame_cb.blockSignals(True)
                self.show_focus_frame_cb.setChecked(viewer.layers['XY Focus Frame'].visible)
                self.show_focus_frame_cb.blockSignals(False)

            # Axes visibility
            if hasattr(viewer, 'axes'):
                self.show_axes_cb.blockSignals(True)
                self.show_axes_cb.setChecked(viewer.axes.visible)
                self.show_axes_cb.blockSignals(False)

    def _reset_to_defaults(self) -> None:
        """Reset all settings to config defaults."""
        channels_config = self.config.get('channels', [])

        for i, controls in self.channel_controls.items():
            ch_config = channels_config[i] if i < len(channels_config) else {}

            controls['visible'].setChecked(ch_config.get('default_visible', True))
            controls['colormap'].setCurrentText(ch_config.get('default_colormap', 'gray'))
            controls['opacity'].setValue(int(ch_config.get('opacity', 0.8) * 100))
            controls['contrast'].setValue((
                ch_config.get('default_contrast_min', 0),
                ch_config.get('default_contrast_max', 500)
            ))

        # Reset display settings
        self.show_chamber_cb.setChecked(True)
        self.show_objective_cb.setChecked(True)
        self.show_focus_frame_cb.setChecked(True)
        self.show_axes_cb.setChecked(True)
        self.rendering_combo.setCurrentText('mip')

        # Reset camera
        self._on_reset_view()


class SampleView(QWidget):
    """
    Integrated sample viewing and interaction window.

    Combines live camera, 3D visualization, MIP plane views,
    position sliders, and illumination controls in a single interface.
    """

    def __init__(
        self,
        camera_controller,
        movement_controller,
        laser_led_controller,
        voxel_storage=None,
        image_controls_window=None,
        geometry_manager: 'WindowGeometryManager' = None,
        configuration_service=None,
        parent=None
    ):
        """
        Initialize Sample View.

        Args:
            camera_controller: CameraController instance
            movement_controller: MovementController instance
            laser_led_controller: LaserLEDController instance
            voxel_storage: Optional DualResolutionVoxelStorage instance
            image_controls_window: Optional ImageControlsWindow for advanced settings
            geometry_manager: Optional WindowGeometryManager for saving/restoring geometry
            configuration_service: Optional ConfigurationService for path persistence
            parent: Parent widget
        """
        super().__init__(parent)

        self.camera_controller = camera_controller
        self.movement_controller = movement_controller
        self.laser_led_controller = laser_led_controller
        self.voxel_storage = voxel_storage
        self.image_controls_window = image_controls_window
        self._geometry_manager = geometry_manager
        self._configuration_service = configuration_service
        self._geometry_restored = False
        self._dialog_state_restored = False
        self.logger = logging.getLogger(__name__)

        # napari viewer and channel layers (owned by Sample View)
        self.viewer = None
        self.channel_layers = {}

        # Display state
        self._current_image: Optional[np.ndarray] = None
        self._colormap = "Grayscale"
        self._auto_scale = True
        self._intensity_min = 0
        self._intensity_max = 65535

        # Auto-contrast algorithm parameters
        self._auto_contrast_interval = 1.0  # seconds between adjustments
        self._saturation_threshold = 0.20  # 20% of pixels saturated triggers raise
        self._low_brightness_threshold = 0.05  # <5% bright pixels triggers lower
        self._brightness_reference = 0.70  # 70% of max is "bright"
        self._saturation_percentile = 0.95  # pixels >= 95% of max are "saturated"
        self._last_contrast_adjustment = 0.0  # timestamp of last adjustment
        self._auto_contrast_max = 65535  # current auto-determined max (min stays 0)

        # Stage limits (will be populated from movement controller)
        self._stage_limits = None

        # Position slider scale factors (for int conversion)
        self._slider_scale = 1000  # 3 decimal places

        # Load visualization config for axis inversion settings
        self._config = self._load_visualization_config()
        self._invert_x = self._config.get('stage_control', {}).get('invert_x_default', False)

        # Channel visibility/contrast state for 4 viewers - load from config
        self._channel_states = self._load_channel_settings_from_config()

        # Live view state
        self._live_view_active = False

        # Tile workflow integration state
        self._tile_workflow_active = False
        self._expected_tiles = []  # List of tile positions
        self._accumulated_zstacks = {}  # (x,y) -> frame count
        self._current_channel = 0  # Default to channel 0 (405nm)

        # 3D visualization state (sample holder, objective, focus frame)
        self.holder_position = {'x': 0, 'y': 0, 'z': 0}
        self.rotation_indicator_length = 0
        self.extension_length_mm = 10.0  # Extension extends 10mm upward from tip
        self.extension_diameter_mm = 0.22  # Fine extension (220 micrometers)
        self.STAGE_Y_AT_OBJECTIVE = 7.45  # mm - stage Y at objective focal plane
        self.OBJECTIVE_CHAMBER_Y_MM = 7.0  # mm - objective focal plane in chamber coords
        self.objective_xy_calibration = None  # Will be loaded from presets
        self.current_rotation = {'ry': 0}  # Current rotation angle
        self.coord_mapper = None  # Will be set from voxel_storage

        # Stage position tracking for dynamic 3D updates
        self.last_stage_position = {'x': 0, 'y': 0, 'z': 0, 'r': 0}
        self._pending_stage_update = None

        # Load objective calibration from presets
        self._load_objective_calibration()

        # Setup window - sized for 3-column layout
        self.setWindowTitle("Sample View")
        self.setWindowIcon(get_app_icon())  # Use flamingo icon
        self.setMinimumSize(1000, 800)
        self.resize(1200, 900)

        # Setup UI
        self._setup_ui()

        # Connect signals
        self._connect_signals()

        # Initialize stage limits
        self._init_stage_limits()

        # Embed 3D viewer from existing window (if available)
        self._embed_3d_viewer()

        # Update live view button state
        self._update_live_view_state()

        # Timer to update zoom display and other info
        self._info_timer = QTimer(self)
        self._info_timer.timeout.connect(self._update_info_displays)
        self._info_timer.start(500)  # Update every 500ms

        # Debounced timer for channel availability checks
        self._channel_availability_timer = QTimer(self)
        self._channel_availability_timer.setSingleShot(True)
        self._channel_availability_timer.setInterval(500)
        self._channel_availability_timer.timeout.connect(self._update_channel_availability)

        # Debounced timer for visualization updates during acquisition
        self._visualization_update_timer = QTimer(self)
        self._visualization_update_timer.setSingleShot(True)
        self._visualization_update_timer.setInterval(500)  # Update 500ms after last frame
        self._visualization_update_timer.timeout.connect(self._update_visualization)

        # Throttled timer for stage position → 3D visualization updates (20 FPS max)
        self._stage_update_timer = QTimer(self)
        self._stage_update_timer.setInterval(50)
        self._stage_update_timer.timeout.connect(self._process_pending_stage_update)

        self.logger.info("SampleView initialized")

    def _load_visualization_config(self) -> Dict[str, Any]:
        """Load visualization config from YAML file."""
        config_path = Path(__file__).parent.parent / "configs" / "visualization_3d_config.yaml"
        try:
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config = yaml.safe_load(f)
                self.logger.info(f"Loaded visualization config from {config_path}")
                return config
        except Exception as e:
            self.logger.warning(f"Could not load visualization config: {e}")

        # Return default config if file not found
        return {
            'stage_control': {
                'invert_x_default': False,
                'invert_z_default': False,
            }
        }

    def _load_objective_calibration(self):
        """Load objective XY calibration from position presets.

        The calibration point is saved as "Tip of sample mount" in position presets.
        This represents the stage position when the sample holder tip is centered
        in the live view - i.e., where the optical axis intersects the sample plane.
        """
        try:
            preset_service = PositionPresetService()
            preset_name = self._config.get('focus_frame', {}).get(
                'calibration_preset_name', 'Tip of sample mount'
            )

            if preset_service.preset_exists(preset_name):
                preset = preset_service.get_preset(preset_name)
                self.objective_xy_calibration = {
                    'x': preset.x,
                    'y': preset.y,
                    'z': preset.z,
                    'r': preset.r
                }
                self.logger.info(f"Loaded objective calibration from '{preset_name}': "
                               f"X={preset.x:.3f}, Y={preset.y:.3f}, Z={preset.z:.3f}")
            else:
                # Use default center position if not calibrated
                self.objective_xy_calibration = {
                    'x': self._config.get('stage_control', {}).get('x_default_mm', 6.0),
                    'y': self._config.get('stage_control', {}).get('y_default_mm', 7.0),
                    'z': self._config.get('stage_control', {}).get('z_default_mm', 19.0),
                    'r': 0
                }
                self.logger.info(f"No '{preset_name}' calibration found, using defaults")
        except Exception as e:
            self.logger.warning(f"Failed to load objective calibration: {e}")
            self.objective_xy_calibration = None

    def set_objective_calibration(self, x: float, y: float, z: float, r: float = 0):
        """Set and save the objective XY calibration point.

        Args:
            x, y, z: Stage position in mm when sample holder tip is centered in live view
            r: Rotation angle (stored but not critical for calibration)
        """
        from py2flamingo.models.microscope import Position

        self.objective_xy_calibration = {'x': x, 'y': y, 'z': z, 'r': r}

        # Save to position presets
        try:
            preset_service = PositionPresetService()
            preset_name = self._config.get('focus_frame', {}).get(
                'calibration_preset_name', 'Tip of sample mount'
            )
            position = Position(x=x, y=y, z=z, r=r)
            preset_service.save_preset(
                preset_name, position,
                "Calibration point: sample holder tip centered in live view"
            )
            self.logger.info(f"Saved objective calibration to '{preset_name}': "
                           f"X={x:.3f}, Y={y:.3f}, Z={z:.3f}")
        except Exception as e:
            self.logger.error(f"Failed to save objective calibration: {e}")

        # Update focus frame if it exists
        if self.viewer and 'XY Focus Frame' in self.viewer.layers:
            self._update_xy_focus_frame()

    def _load_channel_settings_from_config(self) -> Dict[int, Dict[str, Any]]:
        """Load channel settings (contrast, visibility) from visualization config.

        Returns:
            Dictionary mapping channel index to settings dict with:
            - visible: bool
            - contrast_min: int
            - contrast_max: int
        """
        channel_states = {}
        channels_config = self._config.get('channels', [])

        for i in range(4):
            # Find channel config by id
            channel_config = None
            for ch in channels_config:
                if ch.get('id') == i:
                    channel_config = ch
                    break

            if channel_config:
                channel_states[i] = {
                    'visible': channel_config.get('default_visible', True),
                    'contrast_min': channel_config.get('default_contrast_min', 0),
                    'contrast_max': channel_config.get('default_contrast_max', 65535),
                }
                self.logger.debug(f"Loaded channel {i} settings from config: {channel_states[i]}")
            else:
                # Default if not in config
                channel_states[i] = {
                    'visible': True,
                    'contrast_min': 0,
                    'contrast_max': 65535,
                }

        # Also load live display settings for the main display
        live_config = self._config.get('live_display', {})
        self._intensity_min = live_config.get('default_contrast_min', 0)
        self._intensity_max = live_config.get('default_contrast_max', 65535)
        self._auto_scale = live_config.get('auto_scale', True)

        self.logger.info(f"Loaded channel and live display settings from config")

        return channel_states

    def _setup_ui(self) -> None:
        """Create and layout all UI components."""
        main_layout = QHBoxLayout()
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # ========== LEFT COLUMN: Live Camera + Display + Illumination ==========
        left_column = QVBoxLayout()
        left_column.setSpacing(6)

        # Live Camera Feed (4:3 aspect ratio)
        left_column.addWidget(self._create_live_feed_section())

        # Display controls row: Range controls + Settings button
        display_row = QHBoxLayout()
        display_row.addWidget(self._create_range_controls(), stretch=1)

        # Small "Live View Settings" button next to display controls
        self.live_settings_btn = QPushButton("Settings")
        self.live_settings_btn.setToolTip("Open Live View Settings dialog")
        self.live_settings_btn.clicked.connect(self._on_live_settings_clicked)
        self.live_settings_btn.setMaximumWidth(70)
        self.live_settings_btn.setStyleSheet("QPushButton { padding: 4px 8px; font-size: 9pt; }")
        display_row.addWidget(self.live_settings_btn)

        left_column.addLayout(display_row)

        # Illumination Controls
        left_column.addWidget(self._create_illumination_section())

        # Live View toggle button (green when stopped, red when active) - compact
        self.live_view_toggle_btn = QPushButton("Start Live")
        self.live_view_toggle_btn.setCheckable(True)
        self.live_view_toggle_btn.clicked.connect(self._on_live_view_toggle)
        self.live_view_toggle_btn.setStyleSheet(
            f"QPushButton {{ background-color: {SUCCESS_COLOR}; color: white; "
            f"font-weight: bold; padding: 6px 12px; font-size: 10pt; }}"
            f"QPushButton:checked {{ background-color: {ERROR_COLOR}; }}"
        )
        self.live_view_toggle_btn.setMaximumWidth(120)
        left_column.addWidget(self.live_view_toggle_btn)

        left_column.addStretch()

        left_widget = QWidget()
        left_widget.setLayout(left_column)
        left_widget.setMinimumWidth(380)
        left_widget.setMaximumWidth(450)
        main_layout.addWidget(left_widget)

        # ========== CENTER COLUMN: 3D View (tall/vertical) ==========
        center_column = QVBoxLayout()
        center_column.setSpacing(6)

        # 3D Volume View (tall for vertical chamber)
        center_column.addWidget(self._create_3d_view_section(), stretch=1)

        # Position Sliders below 3D view
        center_column.addWidget(self._create_position_sliders())

        center_widget = QWidget()
        center_widget.setLayout(center_column)
        main_layout.addWidget(center_widget, stretch=1)

        # ========== RIGHT COLUMN: Plane Views + Channel Controls ==========
        right_column = QVBoxLayout()
        right_column.setSpacing(6)

        # Plane views with XY and YZ side by side (XZ on top)
        right_column.addWidget(self._create_plane_views_section())

        # Channel controls for 4 viewers (contrast + visibility)
        right_column.addWidget(self._create_channel_controls())

        # Viewer Controls button
        self.viewer_controls_btn = QPushButton("Viewer Controls")
        self.viewer_controls_btn.clicked.connect(self._on_viewer_controls_clicked)
        right_column.addWidget(self.viewer_controls_btn)

        # Workflow Progress
        right_column.addWidget(self._create_workflow_progress())

        # Button bar
        right_column.addWidget(self._create_button_bar())

        right_widget = QWidget()
        right_widget.setLayout(right_column)
        right_widget.setMinimumWidth(340)
        right_widget.setMaximumWidth(500)
        main_layout.addWidget(right_widget)

        self.setLayout(main_layout)

    def _create_live_feed_section(self) -> QGroupBox:
        """Create the live camera feed display section with 4:3 aspect ratio."""
        group = QGroupBox("Live Camera Feed")
        layout = QVBoxLayout()
        layout.setSpacing(4)

        # Image display label - constrained to 4:3 aspect ratio
        # Using 320x240 as base size that scales up to fit available space
        self.live_image_label = QLabel("No image - Start live view from main window")
        self.live_image_label.setAlignment(Qt.AlignCenter)
        self.live_image_label.setMinimumSize(320, 240)  # 4:3 minimum
        self.live_image_label.setFixedSize(360, 270)    # 4:3 fixed size for compact layout
        self.live_image_label.setStyleSheet(
            "QLabel { background-color: black; color: gray; border: 1px solid #444; }"
        )
        self.live_image_label.setScaledContents(False)
        layout.addWidget(self.live_image_label, alignment=Qt.AlignCenter)

        # Status row
        status_layout = QHBoxLayout()
        self.live_status_label = QLabel("Status: Idle")
        self.live_status_label.setStyleSheet("color: #888; font-size: 9pt;")
        status_layout.addWidget(self.live_status_label)

        status_layout.addStretch()

        self.fps_label = QLabel("FPS: --")
        self.fps_label.setStyleSheet("color: #888; font-size: 9pt;")
        status_layout.addWidget(self.fps_label)

        layout.addLayout(status_layout)

        group.setLayout(layout)
        return group

    def _create_range_controls(self) -> QWidget:
        """Create Min-Max range control with dual-handle slider and editable spinboxes."""
        widget = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Row 1: Colormap + Auto checkbox
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        row1.addWidget(QLabel("Display:"))
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(["Grayscale", "Hot", "Viridis", "Plasma", "Inferno"])
        self.colormap_combo.setCurrentText("Grayscale")
        self.colormap_combo.currentTextChanged.connect(self._on_colormap_changed)
        self.colormap_combo.setMaximumWidth(90)
        row1.addWidget(self.colormap_combo)

        self.auto_scale_checkbox = QCheckBox("Auto")
        self.auto_scale_checkbox.setChecked(True)
        self.auto_scale_checkbox.stateChanged.connect(self._on_auto_scale_changed)
        row1.addWidget(self.auto_scale_checkbox)

        row1.addStretch()
        main_layout.addLayout(row1)

        # Row 2: Min spinbox + dual-handle range slider + Max spinbox
        row2 = QHBoxLayout()
        row2.setSpacing(4)

        # Min value spinbox (editable)
        self.min_intensity_spinbox = QSpinBox()
        self.min_intensity_spinbox.setRange(0, 65535)
        self.min_intensity_spinbox.setValue(0)
        self.min_intensity_spinbox.setMaximumWidth(70)
        self.min_intensity_spinbox.setEnabled(False)
        self.min_intensity_spinbox.valueChanged.connect(self._on_min_spinbox_changed)
        row2.addWidget(self.min_intensity_spinbox)

        # Dual-handle range slider (from superqt)
        self.range_slider = QRangeSlider(Qt.Horizontal)
        self.range_slider.setRange(0, 65535)
        self.range_slider.setValue((0, 65535))  # (min, max) tuple
        self.range_slider.setEnabled(False)
        self.range_slider.setToolTip("Drag handles to adjust contrast range")
        # Style the range slider handles to be visible
        self.range_slider.setStyleSheet("""
            QRangeSlider {
                qproperty-barColor: #2196F3;
            }
            QRangeSlider::handle {
                background: #1976D2;
                border: 2px solid #0D47A1;
                border-radius: 6px;
                width: 12px;
                height: 12px;
            }
            QRangeSlider::handle:hover {
                background: #1565C0;
            }
        """)
        self.range_slider.valueChanged.connect(self._on_range_slider_changed)
        row2.addWidget(self.range_slider, stretch=1)

        # Max value spinbox (editable)
        self.max_intensity_spinbox = QSpinBox()
        self.max_intensity_spinbox.setRange(0, 65535)
        self.max_intensity_spinbox.setValue(65535)
        self.max_intensity_spinbox.setMaximumWidth(70)
        self.max_intensity_spinbox.setEnabled(False)
        self.max_intensity_spinbox.valueChanged.connect(self._on_max_spinbox_changed)
        row2.addWidget(self.max_intensity_spinbox)

        main_layout.addLayout(row2)

        widget.setLayout(main_layout)
        widget.setStyleSheet("background-color: #f0f0f0; border-radius: 4px;")
        return widget

    def _create_illumination_section(self) -> QGroupBox:
        """Create illumination controls section with minimum width to prevent squishing."""
        group = QGroupBox("Illumination")

        # Use the existing LaserLEDControlPanel
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)

        self.laser_led_panel = LaserLEDControlPanel(self.laser_led_controller)
        self.laser_led_panel.setMinimumWidth(320)  # Prevent squishing
        layout.addWidget(self.laser_led_panel)

        group.setLayout(layout)
        group.setMinimumWidth(340)  # Ensure group doesn't squish
        return group

    def _create_3d_view_section(self) -> QGroupBox:
        """Create 3D volume view section (placeholder for napari) - tall/vertical."""
        group = QGroupBox("3D Volume View")
        layout = QVBoxLayout()

        # Placeholder for napari viewer - tall layout for vertical sample chamber
        # Chamber dimensions: X ~11mm, Y ~20mm (vertical), Z ~13.5mm
        self.viewer_placeholder = QLabel("3D Napari Viewer\n(Will be integrated)")
        self.viewer_placeholder.setAlignment(Qt.AlignCenter)
        self.viewer_placeholder.setStyleSheet(
            "QLabel { background-color: #1a1a2e; color: #888; "
            "border: 2px dashed #444; font-size: 14pt; }"
        )
        self.viewer_placeholder.setMinimumSize(250, 450)  # Tall/vertical orientation
        self.viewer_placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.viewer_placeholder)

        # Info row: Navigation help, Memory/Voxels stats, Zoom, Reset button
        info_row = QHBoxLayout()

        # Navigation help button (left side)
        self.nav_help_btn = QPushButton("?")
        self.nav_help_btn.setFixedSize(24, 24)
        self.nav_help_btn.setStyleSheet("""
            QPushButton {
                background-color: #555;
                color: white;
                border-radius: 12px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #777; }
        """)
        self.nav_help_btn.setToolTip(
            "3D Navigation Controls:\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Left drag        → Rotate view\n"
            "Shift+Left drag  → Pan/translate\n"
            "Scroll wheel     → Zoom in/out\n"
            "Right drag       → Zoom in/out\n"
            "Double-click     → Zoom in 2x"
        )
        info_row.addWidget(self.nav_help_btn)

        info_row.addStretch()

        # Memory usage label
        self.memory_label = QLabel("Memory: -- MB")
        self.memory_label.setStyleSheet("color: #888; font-size: 9pt;")
        info_row.addWidget(self.memory_label)

        info_row.addSpacing(10)

        # Voxel count label
        self.voxel_label = QLabel("Voxels: --")
        self.voxel_label.setStyleSheet("color: #888; font-size: 9pt;")
        info_row.addWidget(self.voxel_label)

        info_row.addStretch()

        # Zoom display
        self.zoom_label = QLabel("Zoom: --")
        self.zoom_label.setStyleSheet("color: #888; font-size: 9pt;")
        info_row.addWidget(self.zoom_label)

        # Reset view button next to zoom
        self.reset_view_btn = QPushButton("⟲ Reset")
        self.reset_view_btn.setToolTip("Reset camera view to defaults (orientation and zoom)")
        self.reset_view_btn.setMaximumWidth(70)
        self.reset_view_btn.setStyleSheet("""
            QPushButton {
                font-size: 9pt;
                padding: 2px 6px;
                border: 1px solid #666;
                border-radius: 3px;
                background: #3a3a5a;
                color: #ccc;
            }
            QPushButton:hover {
                background: #4a4a7a;
                color: #fff;
            }
        """)
        self.reset_view_btn.clicked.connect(self._on_reset_zoom_clicked)
        info_row.addWidget(self.reset_view_btn)

        layout.addLayout(info_row)

        # Quality row: Fast Transform checkbox
        quality_row = QHBoxLayout()
        quality_row.addStretch()

        self.fast_transform_cb = QCheckBox("Fast Transform")
        self.fast_transform_cb.setChecked(True)
        self.fast_transform_cb.setToolTip(
            "Checked: Faster rendering (nearest-neighbor)\n"
            "Unchecked: Smoother rendering (linear interpolation)"
        )
        self.fast_transform_cb.toggled.connect(self._on_transform_quality_changed)
        quality_row.addWidget(self.fast_transform_cb)

        quality_row.addStretch()
        layout.addLayout(quality_row)

        group.setLayout(layout)
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        return group

    def _create_position_sliders(self) -> QGroupBox:
        """Create position control sliders for all axes."""
        group = QGroupBox("Position Sliders")
        layout = QVBoxLayout()
        layout.setSpacing(6)

        # Store slider references
        self.position_sliders: Dict[str, QSlider] = {}
        self.position_edits: Dict[str, QLineEdit] = {}

        # Create slider for each axis
        axes = [
            ('x', 'X', 'mm', 3),
            ('y', 'Y', 'mm', 3),
            ('z', 'Z', 'mm', 3),
            ('r', 'R', '°', 2),
        ]

        for axis_id, axis_name, unit, decimals in axes:
            row = QHBoxLayout()
            row.setSpacing(8)

            # Get axis color (XYZ have colors, R doesn't)
            axis_color = AXIS_COLORS.get(axis_id, '#666666')

            # Axis label with color
            axis_label = QLabel(f"<b>{axis_name}:</b>")
            axis_label.setMinimumWidth(25)
            axis_label.setStyleSheet(f"color: {axis_color}; font-size: 11pt;")
            row.addWidget(axis_label)

            # Min value label
            min_label = QLabel("0.0")
            min_label.setStyleSheet("color: #666; font-size: 9pt;")
            min_label.setMinimumWidth(50)
            min_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(min_label)

            # Slider with colored groove
            slider = QSlider(Qt.Horizontal)
            slider.setMinimum(0)
            slider.setMaximum(100000)  # Will be updated with real limits
            slider.setValue(50000)
            slider.setTickPosition(QSlider.TicksBelow)
            # Style the slider with axis color
            slider.setStyleSheet(f"""
                QSlider::groove:horizontal {{
                    border: 1px solid {axis_color};
                    height: 6px;
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #333, stop:1 {axis_color});
                    margin: 2px 0;
                    border-radius: 3px;
                }}
                QSlider::handle:horizontal {{
                    background: {axis_color};
                    border: 1px solid #333;
                    width: 14px;
                    margin: -5px 0;
                    border-radius: 7px;
                }}
                QSlider::handle:horizontal:hover {{
                    background: white;
                    border: 2px solid {axis_color};
                }}
            """)
            slider.valueChanged.connect(
                lambda val, a=axis_id: self._on_position_slider_changed(a, val)
            )
            slider.sliderReleased.connect(
                lambda a=axis_id: self._on_position_slider_released(a)
            )
            self.position_sliders[axis_id] = slider
            row.addWidget(slider, stretch=1)

            # Max value label
            max_label = QLabel("100.0")
            max_label.setStyleSheet("color: #666; font-size: 9pt;")
            max_label.setMinimumWidth(50)
            row.addWidget(max_label)

            # Current value - editable field with validation
            value_edit = QLineEdit(f"50.000")
            value_edit.setStyleSheet(
                "background-color: #e3f2fd; padding: 4px; "
                "border: 1px solid #2196f3; border-radius: 3px; "
                "font-weight: bold; min-width: 70px; max-width: 80px;"
            )
            value_edit.setAlignment(Qt.AlignCenter)
            # Validator will be set when limits are known
            validator = QDoubleValidator(0.0, 100.0, decimals)
            validator.setNotation(QDoubleValidator.StandardNotation)
            value_edit.setValidator(validator)
            value_edit.editingFinished.connect(
                lambda a=axis_id: self._on_position_edit_finished(a)
            )
            self.position_edits[axis_id] = value_edit
            row.addWidget(value_edit)

            # Unit label
            unit_label = QLabel(unit)
            unit_label.setStyleSheet("font-weight: bold; min-width: 20px;")
            row.addWidget(unit_label)

            # Store min/max labels for later updates
            slider.setProperty('min_label', min_label)
            slider.setProperty('max_label', max_label)
            slider.setProperty('unit', unit)
            slider.setProperty('decimals', decimals)
            slider.setProperty('value_edit', value_edit)

            layout.addLayout(row)

        group.setLayout(layout)
        return group

    def _on_position_edit_finished(self, axis: str) -> None:
        """Handle position edit field value change (when user presses Enter or focus leaves)."""
        if axis not in self.position_edits:
            return

        edit = self.position_edits[axis]
        slider = self.position_sliders[axis]

        try:
            # Parse the entered value
            value_text = edit.text().strip()
            value = float(value_text)

            # Clamp to valid range
            min_val = slider.minimum() / self._slider_scale
            max_val = slider.maximum() / self._slider_scale
            clamped_value = max(min_val, min(max_val, value))

            # Update the edit field if value was clamped
            decimals = slider.property('decimals')
            if clamped_value != value:
                edit.setText(f"{clamped_value:.{decimals}f}")

            # Update slider (without triggering movement yet)
            slider.blockSignals(True)
            slider.setValue(int(clamped_value * self._slider_scale))
            slider.blockSignals(False)

            # Send movement command
            self._send_position_command(axis, clamped_value)

        except ValueError:
            # Invalid input - restore from slider
            current_value = slider.value() / self._slider_scale
            decimals = slider.property('decimals')
            edit.setText(f"{current_value:.{decimals}f}")

    def _create_plane_views_section(self) -> QWidget:
        """Create the three MIP plane views section with proportions based on stage dimensions.

        Stage dimensions: X ~11mm, Y ~20mm (vertical), Z ~13.5mm
        - XZ (Top-Down): ~square (11:13.5) - on top, X horizontal, Z vertical
        - XY (Front View): tall (11:20) - bottom left, X horizontal, Y vertical
        - YZ (Side View): tall (13.5:20) - bottom right, Z horizontal, Y vertical

        Borders are colored to match napari axis colors:
        - X: Cyan (#008B8B)
        - Y: Magenta (#8B008B)
        - Z: Yellow (#8B8B00)
        """
        widget = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(4)
        layout.setContentsMargins(0, 0, 0, 0)

        # Get stage ranges from config
        stage_config = self._config.get('stage_control', {})
        x_range = tuple(stage_config.get('x_range_mm', [1.0, 12.31]))
        y_range = tuple(stage_config.get('y_range_mm', [5.0, 25.0]))
        z_range = tuple(stage_config.get('z_range_mm', [12.5, 26.0]))

        # XZ Plane (Top-Down) - X horizontal, Z vertical
        xz_group = QGroupBox("XZ Plane (Top-Down)")
        xz_layout = QVBoxLayout()
        xz_layout.setContentsMargins(4, 4, 4, 4)
        # Aspect ~11:13.5 ≈ 0.81, use 180x220
        self.xz_plane_viewer = SlicePlaneViewer('xz', 'x', 'z', 180, 220)
        self.xz_plane_viewer.set_ranges(x_range, z_range)
        self.xz_plane_viewer.position_clicked.connect(
            lambda h, v: self._on_plane_click('xz', h, v)
        )
        xz_layout.addWidget(self.xz_plane_viewer, alignment=Qt.AlignCenter)
        xz_group.setLayout(xz_layout)
        layout.addWidget(xz_group)

        # Bottom row: XY and YZ side by side (both tall)
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(4)

        # XY Plane (Front View) - X horizontal, Y vertical
        xy_group = QGroupBox("XY Plane (Front)")
        xy_layout = QVBoxLayout()
        xy_layout.setContentsMargins(4, 4, 4, 4)
        # Aspect ~11:20 ≈ 0.55, use 130x240
        self.xy_plane_viewer = SlicePlaneViewer('xy', 'x', 'y', 130, 240)
        self.xy_plane_viewer.set_ranges(x_range, y_range)
        self.xy_plane_viewer.position_clicked.connect(
            lambda h, v: self._on_plane_click('xy', h, v)
        )
        xy_layout.addWidget(self.xy_plane_viewer, alignment=Qt.AlignCenter)
        xy_group.setLayout(xy_layout)
        bottom_row.addWidget(xy_group)

        # YZ Plane (Side View) - Z horizontal, Y vertical
        yz_group = QGroupBox("YZ Plane (Side)")
        yz_layout = QVBoxLayout()
        yz_layout.setContentsMargins(4, 4, 4, 4)
        # Aspect ~13.5:20 ≈ 0.675, use 160x240
        self.yz_plane_viewer = SlicePlaneViewer('yz', 'z', 'y', 160, 240)
        self.yz_plane_viewer.set_ranges(z_range, y_range)
        self.yz_plane_viewer.position_clicked.connect(
            lambda h, v: self._on_plane_click('yz', h, v)
        )
        yz_layout.addWidget(self.yz_plane_viewer, alignment=Qt.AlignCenter)
        yz_group.setLayout(yz_layout)
        bottom_row.addWidget(yz_group)

        layout.addLayout(bottom_row)

        widget.setLayout(layout)
        return widget

    def _create_channel_controls(self) -> QGroupBox:
        """Create channel visibility and contrast controls for 4 viewer channels."""
        group = QGroupBox("Viewer Channels")
        layout = QVBoxLayout()
        layout.setSpacing(2)
        layout.setContentsMargins(6, 6, 6, 6)

        # Get channel names from visualization config (wavelengths)
        # Falls back to default names if config not available
        channels_config = self._config.get('channels', [])
        default_channel_info = [
            {"name": "405nm", "color": "#9370DB"},  # Violet
            {"name": "488nm", "color": "#00CED1"},  # Cyan
            {"name": "561nm", "color": "#32CD32"},  # Green
            {"name": "640nm", "color": "#DC143C"},  # Red
        ]

        # Store widget references
        self.channel_checkboxes: Dict[int, QCheckBox] = {}
        self.channel_contrast_sliders: Dict[int, QRangeSlider] = {}
        self.channel_min_labels: Dict[int, QLabel] = {}
        self.channel_max_labels: Dict[int, QLabel] = {}

        for i in range(4):
            # Get channel config or use default
            if i < len(channels_config):
                ch_config = channels_config[i]
                # Extract wavelength from name like "405nm (DAPI)" -> "405nm"
                name = ch_config.get('name', default_channel_info[i]['name'])
                if '(' in name:
                    name = name.split('(')[0].strip()
            else:
                name = default_channel_info[i]['name']

            # Get colormap color for the channel
            colormap = channels_config[i].get('default_colormap', 'gray') if i < len(channels_config) else 'gray'
            colormap_colors = {
                'blue': '#9370DB', 'green': '#32CD32', 'red': '#DC143C',
                'magenta': '#FF00FF', 'cyan': '#00CED1', 'gray': '#808080'
            }
            color = colormap_colors.get(colormap, default_channel_info[i]['color'])

            row_layout = QHBoxLayout()
            row_layout.setSpacing(4)

            # Visibility checkbox with wavelength name
            checkbox = QCheckBox(name)
            checkbox.setChecked(self._channel_states[i].get('visible', True))
            checkbox.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: bold; }}")
            checkbox.stateChanged.connect(
                lambda state, ch=i: self._on_channel_visibility_changed(ch, state)
            )
            checkbox.setMinimumWidth(70)
            self.channel_checkboxes[i] = checkbox
            row_layout.addWidget(checkbox)

            # Dual-handle contrast range slider
            # Range is 0-500 for typical fluorescence/brightfield (not full 16-bit)
            slider = QRangeSlider(Qt.Horizontal)
            slider.setRange(0, 500)
            # Load initial values from channel state (clamped to slider range)
            min_val = min(self._channel_states[i].get('contrast_min', 0), 500)
            max_val = min(self._channel_states[i].get('contrast_max', 500), 500)
            slider.setValue((min_val, max_val))
            slider.setToolTip(f"Adjust contrast range for {name}")
            # Style the range slider handles to be visible
            slider.setStyleSheet("""
                QRangeSlider {
                    qproperty-barColor: #2196F3;
                }
                QRangeSlider::handle {
                    background: #1976D2;
                    border: 2px solid #0D47A1;
                    border-radius: 6px;
                    width: 12px;
                    height: 12px;
                }
                QRangeSlider::handle:hover {
                    background: #1565C0;
                }
            """)
            slider.valueChanged.connect(
                lambda val, ch=i: self._on_channel_contrast_changed(ch, val)
            )
            self.channel_contrast_sliders[i] = slider

            # Min/max value labels flanking the slider
            min_label = QLabel(str(min_val))
            min_label.setFixedWidth(28)
            min_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            min_label.setStyleSheet("color: #888; font-size: 9pt;")
            self.channel_min_labels[i] = min_label

            max_label = QLabel(str(max_val))
            max_label.setFixedWidth(28)
            max_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            max_label.setStyleSheet("color: #888; font-size: 9pt;")
            self.channel_max_labels[i] = max_label

            row_layout.addWidget(min_label)
            row_layout.addWidget(slider, stretch=1)
            row_layout.addWidget(max_label)

            # Start channels disabled until data arrives
            checkbox.setEnabled(False)
            checkbox.setChecked(False)
            checkbox.setToolTip(
                f"{name} channel — No data loaded.\n"
                "This channel will activate automatically when 3D volume data is received."
            )
            slider.setEnabled(False)
            min_label.setEnabled(False)
            max_label.setEnabled(False)

            layout.addLayout(row_layout)

        # Auto Contrast button
        auto_contrast_btn = QPushButton("Auto Contrast")
        auto_contrast_btn.setToolTip("Calculate contrast from actual data (2nd-98th percentile)")
        auto_contrast_btn.clicked.connect(self._auto_contrast_channels)
        layout.addWidget(auto_contrast_btn)

        group.setLayout(layout)
        return group

    def _create_workflow_progress(self) -> QWidget:
        """Create workflow progress bar (placeholder - not connected)."""
        widget = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # Status label
        self.workflow_status_label = QLabel("Workflow: Not Running")
        self.workflow_status_label.setStyleSheet("font-weight: bold;")
        self.workflow_status_label.setMinimumWidth(350)
        layout.addWidget(self.workflow_status_label)

        # Progress bar
        self.workflow_progress_bar = QProgressBar()
        self.workflow_progress_bar.setRange(0, 100)
        self.workflow_progress_bar.setValue(0)
        self.workflow_progress_bar.setTextVisible(True)
        self.workflow_progress_bar.setFormat("%p%")
        layout.addWidget(self.workflow_progress_bar, stretch=1)

        # Time remaining
        self.time_remaining_label = QLabel("--:--")
        self.time_remaining_label.setStyleSheet("color: #666;")
        layout.addWidget(self.time_remaining_label)

        widget.setLayout(layout)
        widget.setStyleSheet(
            f"background-color: {WARNING_BG}; border: 1px solid #ffc107; border-radius: 4px;"
        )
        return widget

    def _create_button_bar(self) -> QWidget:
        """Create dialog launcher button bar with data collection controls."""
        widget = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(4)
        layout.setContentsMargins(0, 4, 0, 4)

        # Row 1: Data collection buttons (most important - prominent styling)
        data_row = QHBoxLayout()
        data_row.setSpacing(8)

        # Populate from Live View toggle button
        self.populate_btn = QPushButton("Populate from Live")
        self.populate_btn.setCheckable(True)
        self.populate_btn.setToolTip("Capture frames from Live View and accumulate into 3D volume")
        self.populate_btn.clicked.connect(self._on_populate_toggled)
        self.populate_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 16px; }"
            "QPushButton:checked { background-color: #f44336; }"
            "QPushButton:hover { background-color: #45a049; }"
            "QPushButton:checked:hover { background-color: #da190b; }"
        )
        data_row.addWidget(self.populate_btn)

        # Clear Data button
        self.clear_data_btn = QPushButton("Clear Data")
        self.clear_data_btn.setToolTip("Clear all accumulated 3D volume data")
        self.clear_data_btn.clicked.connect(self._on_clear_data_clicked)
        self.clear_data_btn.setStyleSheet(
            "QPushButton { background-color: #ff9800; color: white; font-weight: bold; padding: 8px 16px; }"
            "QPushButton:hover { background-color: #f57c00; }"
        )
        data_row.addWidget(self.clear_data_btn)

        data_row.addStretch()
        layout.addLayout(data_row)

        # Row 2: Navigation buttons
        nav_row = QHBoxLayout()
        nav_row.setSpacing(8)

        # Saved Positions button
        self.saved_positions_btn = QPushButton("Saved Positions")
        self.saved_positions_btn.clicked.connect(self._on_saved_positions_clicked)
        nav_row.addWidget(self.saved_positions_btn)

        # Stage Control button
        self.stage_control_btn = QPushButton("Stage Control")
        self.stage_control_btn.clicked.connect(self._on_stage_control_clicked)
        nav_row.addWidget(self.stage_control_btn)

        # Export Data button
        self.export_data_btn = QPushButton("Export Data")
        self.export_data_btn.clicked.connect(self._on_export_data_clicked)
        nav_row.addWidget(self.export_data_btn)

        nav_row.addStretch()
        layout.addLayout(nav_row)

        # Row 3: Performance & Session buttons
        perf_row = QHBoxLayout()
        perf_row.setSpacing(8)

        # Load Test Data button
        self.load_test_data_btn = QPushButton("Load Test Data")
        self.load_test_data_btn.setToolTip("Load .zarr, .tif, or .npy test data into viewer")
        self.load_test_data_btn.clicked.connect(self._on_load_test_data_clicked)
        perf_row.addWidget(self.load_test_data_btn)

        # Save Session button
        self.save_session_btn = QPushButton("Save Session")
        self.save_session_btn.setToolTip("Save current 3D data and settings to OME-Zarr session")
        self.save_session_btn.clicked.connect(self._on_save_session_clicked)
        perf_row.addWidget(self.save_session_btn)

        # Load Session button
        self.load_session_btn = QPushButton("Load Session")
        self.load_session_btn.setToolTip("Load a saved OME-Zarr session")
        self.load_session_btn.clicked.connect(self._on_load_session_clicked)
        perf_row.addWidget(self.load_session_btn)

        # Benchmark button
        self.benchmark_btn = QPushButton("Benchmark")
        self.benchmark_btn.setToolTip("Run performance benchmarks on 3D transforms")
        self.benchmark_btn.clicked.connect(self._on_benchmark_clicked)
        self.benchmark_btn.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; }"
            "QPushButton:hover { background-color: #1976D2; }"
        )
        perf_row.addWidget(self.benchmark_btn)

        # Settings button
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setToolTip("Open application settings dialog")
        self.settings_btn.clicked.connect(self._on_settings_clicked)
        self.settings_btn.setStyleSheet(
            "QPushButton { background-color: #607D8B; color: white; }"
            "QPushButton:hover { background-color: #455A64; }"
        )
        perf_row.addWidget(self.settings_btn)

        perf_row.addStretch()
        layout.addLayout(perf_row)

        widget.setLayout(layout)
        return widget

    def _connect_signals(self) -> None:
        """Connect controller signals."""
        # Camera signals
        if self.camera_controller:
            self.camera_controller.new_image.connect(self._on_frame_received)
            self.camera_controller.state_changed.connect(self._on_camera_state_changed)

            # Connect tile Z-stack frame signal for Sample View integration
            if hasattr(self.camera_controller, 'tile_zstack_frame'):
                self.camera_controller.tile_zstack_frame.connect(self._on_tile_zstack_frame)
                self.logger.info("Connected tile Z-stack frame signal for Sample View integration")

        # Movement signals
        if self.movement_controller:
            self.movement_controller.position_changed.connect(self._on_position_changed)

        self.logger.info("SampleView signals connected")

    def _init_stage_limits(self) -> None:
        """Initialize stage limits from movement controller and set current positions."""
        if not self.movement_controller:
            return

        try:
            self._stage_limits = self.movement_controller.get_stage_limits()

            # Update sliders with actual limits
            for axis_id in ['x', 'y', 'z', 'r']:
                if axis_id in self._stage_limits and axis_id in self.position_sliders:
                    limits = self._stage_limits[axis_id]
                    slider = self.position_sliders[axis_id]

                    min_val = limits['min']
                    max_val = limits['max']

                    # Update slider range (scaled to integers)
                    slider.setMinimum(int(min_val * self._slider_scale))
                    slider.setMaximum(int(max_val * self._slider_scale))

                    # Update edit field validator with actual limits
                    if axis_id in self.position_edits:
                        edit = self.position_edits[axis_id]
                        validator = edit.validator()
                        if validator:
                            validator.setRange(min_val, max_val, validator.decimals())

                    # Update min/max labels
                    min_label = slider.property('min_label')
                    max_label = slider.property('max_label')
                    decimals = slider.property('decimals')

                    # For X axis: if inverted, swap the displayed labels (high on left, low on right)
                    if axis_id == 'x' and self._invert_x:
                        # Inverted: show max on left, min on right
                        if min_label:
                            min_label.setText(f"{max_val:.{decimals}f}")
                        if max_label:
                            max_label.setText(f"{min_val:.{decimals}f}")
                        # Mark slider as inverted for value display
                        slider.setProperty('inverted', True)
                        slider.setInvertedAppearance(True)
                    else:
                        # Normal: show min on left, max on right
                        if min_label:
                            min_label.setText(f"{min_val:.{decimals}f}")
                        if max_label:
                            max_label.setText(f"{max_val:.{decimals}f}")
                        slider.setProperty('inverted', False)

            self.logger.info(f"Stage limits initialized (X inverted: {self._invert_x})")

            # Query and set CURRENT stage position (critical for safety!)
            self._load_current_positions()

        except Exception as e:
            self.logger.error(f"Error initializing stage limits: {e}")

    def _load_current_positions(self) -> None:
        """Load current stage positions from movement controller and update sliders.

        This is critical for safety - sliders must reflect actual stage position,
        not default values that could cause dangerous movements.
        """
        if not self.movement_controller:
            self.logger.warning("No movement controller - cannot load current positions")
            return

        try:
            # Get current position from controller (returns Position object)
            current_pos = self.movement_controller.get_position()

            if current_pos is None:
                self.logger.warning("Could not retrieve current position from controller")
                return

            # Extract positions (current_pos is a Position object when axis=None)
            positions = {
                'x': current_pos.x,
                'y': current_pos.y,
                'z': current_pos.z,
                'r': current_pos.r
            }

            self.logger.info(f"Loading current positions: X={positions['x']:.3f}, "
                           f"Y={positions['y']:.3f}, Z={positions['z']:.3f}, R={positions['r']:.2f}")

            # Update internal position tracking (critical for 3D viewer and transforms!)
            self.last_stage_position = positions.copy()
            self.current_rotation['ry'] = positions['r']
            self.logger.info(f"Updated last_stage_position and current_rotation from hardware")

            # Update each slider to current position
            for axis_id, value in positions.items():
                if axis_id in self.position_sliders and value is not None:
                    slider = self.position_sliders[axis_id]
                    edit = self.position_edits[axis_id]

                    # Block signals to prevent triggering movement commands
                    slider.blockSignals(True)
                    slider.setValue(int(value * self._slider_scale))
                    slider.blockSignals(False)

                    # Update value edit field (unit is now a separate label)
                    decimals = slider.property('decimals')
                    edit.blockSignals(True)
                    edit.setText(f"{value:.{decimals}f}")
                    edit.blockSignals(False)

            self.logger.info("Slider positions initialized from current stage position")

        except Exception as e:
            self.logger.error(f"Error loading current positions: {e}")
            import traceback
            traceback.print_exc()

    # ========== Slot Handlers ==========

    @pyqtSlot(object, object)
    def _on_frame_received(self, image: np.ndarray, header) -> None:
        """Handle received camera frame."""
        self._current_image = image
        self._update_live_display()

    def _update_live_display(self) -> None:
        """Update the live image display."""
        if self._current_image is None:
            return

        try:
            image = self._current_image

            # Apply intensity scaling
            if self._auto_scale:
                # Use stabilized auto-contrast (adjusts at most once per interval)
                img_min = 0  # Always use 0 as min for consistency
                img_max = self._calculate_auto_contrast(image)
            else:
                img_min = self._intensity_min
                img_max = self._intensity_max

            # Normalize to 0-255
            if img_max > img_min:
                normalized = ((image.astype(np.float32) - img_min) /
                             (img_max - img_min) * 255).clip(0, 255).astype(np.uint8)
            else:
                normalized = np.zeros_like(image, dtype=np.uint8)

            # Convert to QImage and display
            height, width = normalized.shape
            bytes_per_line = width
            qimage = QImage(normalized.data, width, height, bytes_per_line, QImage.Format_Grayscale8)

            # Scale to fit label while maintaining aspect ratio
            pixmap = QPixmap.fromImage(qimage)
            scaled_pixmap = pixmap.scaled(
                self.live_image_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            self.live_image_label.setPixmap(scaled_pixmap)

        except Exception as e:
            self.logger.error(f"Error updating live display: {e}")

    def _calculate_auto_contrast(self, image: np.ndarray) -> int:
        """Calculate auto-contrast max value with stabilization.

        Uses a percentage-based algorithm that only adjusts once per interval:
        - If image max is much lower than current max: jump directly to data-based value
        - If >20% pixels are saturated (>=95% of current max): raise max to 95% of top 5% mean
        - If <5% pixels are above 70% of current max: lower max by 10%
        - Otherwise: keep current max (stable)

        Args:
            image: The current camera frame

        Returns:
            The contrast max value to use for display
        """
        current_time = time.time()
        time_since_last = current_time - self._last_contrast_adjustment

        # Only recalculate if enough time has passed
        if time_since_last < self._auto_contrast_interval:
            return self._auto_contrast_max

        # Calculate pixel statistics
        total_pixels = image.size
        current_max = self._auto_contrast_max

        # Quick check: if image is very dark compared to current_max, jump directly
        # This handles the case where we start at 65535 but sample is dim
        image_actual_max = int(np.max(image))
        if image_actual_max < current_max * 0.1:  # Actual max is less than 10% of display max
            # Image is very dark - set max based on actual data
            # Use 99th percentile for robustness against hot pixels
            p99 = np.percentile(image, 99)
            new_max = int(p99 / 0.85)  # Set so 99th percentile is at 85% brightness
            new_max = max(100, min(65535, new_max))  # Clamp to reasonable range

            if new_max < current_max * 0.5:  # Only jump if it's a significant change
                self.logger.info(f"Auto-contrast: quick adjustment {current_max} -> {new_max} "
                                f"(image max={image_actual_max}, p99={p99:.0f})")
                self._auto_contrast_max = new_max
                self._last_contrast_adjustment = current_time
                return self._auto_contrast_max

        # Count saturated pixels (>= 95% of current max)
        saturation_level = current_max * self._saturation_percentile
        saturated_count = np.sum(image >= saturation_level)
        saturated_ratio = saturated_count / total_pixels

        if saturated_ratio > self._saturation_threshold:
            # Too many saturated pixels - raise max to 95% of top 5% mean
            # This allows large jumps when transitioning to heavily stained areas
            top_5_percent_count = max(1, int(total_pixels * 0.05))
            # Use partition for efficiency (faster than full sort)
            top_values = np.partition(image.ravel(), -top_5_percent_count)[-top_5_percent_count:]
            top_5_mean = np.mean(top_values)
            new_max = int(top_5_mean / 0.95)  # Set so top 5% mean is at 95%
            new_max = min(65535, max(1000, new_max))  # Clamp to reasonable range

            if new_max != self._auto_contrast_max:
                self.logger.debug(f"Auto-contrast: raising max {self._auto_contrast_max} -> {new_max} "
                                 f"(saturated: {saturated_ratio:.1%}, top 5% mean: {top_5_mean:.0f})")
                self._auto_contrast_max = new_max
                self._last_contrast_adjustment = current_time
        else:
            # Check if we should lower the max (image is too dark)
            brightness_level = current_max * self._brightness_reference
            bright_count = np.sum(image > brightness_level)
            bright_ratio = bright_count / total_pixels

            if bright_ratio < self._low_brightness_threshold:
                # Too few bright pixels - lower max by 10%
                new_max = int(current_max * 0.90)
                new_max = max(1000, new_max)  # Don't go below 1000

                if new_max != self._auto_contrast_max:
                    self.logger.debug(f"Auto-contrast: lowering max {self._auto_contrast_max} -> {new_max} "
                                     f"(bright pixels: {bright_ratio:.1%})")
                    self._auto_contrast_max = new_max
                    self._last_contrast_adjustment = current_time

        return self._auto_contrast_max

    @pyqtSlot(object)
    def _on_camera_state_changed(self, state) -> None:
        """Handle camera state change.

        Updates both the status label and the live view button to reflect
        the actual camera state. This ensures the GUI stays in sync when
        the camera is controlled externally (e.g., by workflows).
        """
        state_names = {0: "Idle", 1: "Starting", 2: "Running", 3: "Stopping"}
        state_name = state_names.get(state.value if hasattr(state, 'value') else state, "Unknown")
        self.live_status_label.setText(f"Status: {state_name}")

        # Also update the live view button to match actual camera state
        self._update_live_view_state()

    @pyqtSlot(float, float, float, float)
    def _on_position_changed(self, x: float, y: float, z: float, r: float) -> None:
        """Handle position change from movement controller."""
        positions = {'x': x, 'y': y, 'z': z, 'r': r}

        for axis_id, value in positions.items():
            if axis_id in self.position_sliders:
                slider = self.position_sliders[axis_id]
                edit = self.position_edits[axis_id]

                # Block signals to prevent feedback loop
                slider.blockSignals(True)
                slider.setValue(int(value * self._slider_scale))
                slider.blockSignals(False)

                # Update value edit field
                decimals = slider.property('decimals')
                edit.blockSignals(True)
                edit.setText(f"{value:.{decimals}f}")
                edit.blockSignals(False)

        # Queue 3D visualization update (throttled to 20 FPS max)
        self._pending_stage_update = {'x': x, 'y': y, 'z': z, 'r': r}
        if not self._stage_update_timer.isActive():
            self._stage_update_timer.start()

        # Update plane view overlays with current position
        self._update_plane_overlays()

        # Mark target markers as stale when stage reaches target position
        self._check_and_mark_targets_stale(x, y, z)

    def _on_position_slider_changed(self, axis: str, value: int) -> None:
        """Handle position slider value change (during drag)."""
        if axis in self.position_sliders:
            slider = self.position_sliders[axis]
            edit = self.position_edits[axis]

            real_value = value / self._slider_scale
            decimals = slider.property('decimals')
            edit.blockSignals(True)
            edit.setText(f"{real_value:.{decimals}f}")
            edit.blockSignals(False)

    def _on_position_slider_released(self, axis: str) -> None:
        """Handle position slider release - send move command."""
        if axis in self.position_sliders:
            slider = self.position_sliders[axis]
            real_value = slider.value() / self._slider_scale
            self._send_position_command(axis, real_value)

    def _send_position_command(self, axis: str, value: float) -> None:
        """Send a movement command to the specified axis.

        Args:
            axis: The axis to move ('x', 'y', 'z', or 'r')
            value: The target position value
        """
        if not self.movement_controller:
            return

        try:
            self.movement_controller.move_absolute(axis, value, verify=False)
            self.logger.info(f"Moving {axis.upper()} to {value:.3f}")
        except Exception as e:
            self.logger.error(f"Error moving {axis}: {e}")

    def _on_colormap_changed(self, colormap: str) -> None:
        """Handle colormap selection change."""
        self._colormap = colormap
        self._update_live_display()

    def _on_auto_scale_changed(self, state: int) -> None:
        """Handle auto-scale checkbox change."""
        self._auto_scale = (state == Qt.Checked)
        enabled = not self._auto_scale
        self.min_intensity_spinbox.setEnabled(enabled)
        self.max_intensity_spinbox.setEnabled(enabled)
        self.range_slider.setEnabled(enabled)
        self._update_live_display()

    def _on_min_spinbox_changed(self, value: int) -> None:
        """Handle min intensity spinbox change - update slider and display."""
        self._intensity_min = value
        # Ensure min doesn't exceed max
        if value > self._intensity_max:
            self.max_intensity_spinbox.setValue(value)
        # Sync range slider with spinboxes
        self.range_slider.blockSignals(True)
        self.range_slider.setValue((self._intensity_min, self._intensity_max))
        self.range_slider.blockSignals(False)
        self._update_live_display()

    def _on_max_spinbox_changed(self, value: int) -> None:
        """Handle max intensity spinbox change - update slider and display."""
        self._intensity_max = value
        # Ensure max doesn't go below min
        if value < self._intensity_min:
            self.min_intensity_spinbox.setValue(value)
        # Sync range slider with spinboxes
        self.range_slider.blockSignals(True)
        self.range_slider.setValue((self._intensity_min, self._intensity_max))
        self.range_slider.blockSignals(False)
        self._update_live_display()

    def _on_range_slider_changed(self, value: tuple) -> None:
        """Handle range slider change - update spinboxes and display."""
        min_val, max_val = value
        self._intensity_min = min_val
        self._intensity_max = max_val
        # Sync spinboxes with slider
        self.min_intensity_spinbox.blockSignals(True)
        self.max_intensity_spinbox.blockSignals(True)
        self.min_intensity_spinbox.setValue(min_val)
        self.max_intensity_spinbox.setValue(max_val)
        self.min_intensity_spinbox.blockSignals(False)
        self.max_intensity_spinbox.blockSignals(False)
        self._update_live_display()

    def _on_channel_visibility_changed(self, channel: int, state: int) -> None:
        """Handle channel visibility checkbox change."""
        visible = (state == Qt.Checked)
        self._channel_states[channel]['visible'] = visible
        self.logger.debug(f"Channel {channel} visibility: {visible}")

        # Toggle visibility on the actual napari layer
        if self.channel_layers:
            layer = self.channel_layers.get(channel)
            if layer is not None:
                layer.visible = visible

    def _on_channel_contrast_changed(self, channel: int, value: tuple) -> None:
        """Handle channel contrast range slider change.

        Args:
            channel: Channel index (0-3)
            value: Tuple of (min, max) contrast values from QRangeSlider
        """
        min_val, max_val = value
        self._channel_states[channel]['contrast_min'] = min_val
        self._channel_states[channel]['contrast_max'] = max_val

        # Update min/max labels
        if channel in self.channel_min_labels:
            self.channel_min_labels[channel].setText(str(min_val))
        if channel in self.channel_max_labels:
            self.channel_max_labels[channel].setText(str(max_val))

        # Update contrast on the actual napari layer
        if self.channel_layers:
            layer = self.channel_layers.get(channel)
            if layer is not None:
                layer.contrast_limits = [min_val, max_val]

        self.logger.debug(f"Channel {channel} contrast range: [{min_val}, {max_val}]")

    def _auto_contrast_channels(self) -> None:
        """Calculate and apply contrast based on actual data statistics."""
        if not self.voxel_storage or not self.channel_layers:
            return

        for ch_id, layer in self.channel_layers.items():
            if not self.voxel_storage.has_data(ch_id):
                continue

            volume = self.voxel_storage.get_display_volume(ch_id)
            if volume is None or volume.size == 0:
                continue

            # Calculate percentile-based contrast (2nd to 98th percentile)
            non_zero = volume[volume > 0]
            if len(non_zero) == 0:
                continue

            min_val = int(np.percentile(non_zero, 2))
            max_val = int(np.percentile(non_zero, 98))

            # Ensure min < max
            if max_val <= min_val:
                max_val = min_val + 10

            # Update layer contrast
            layer.contrast_limits = (min_val, max_val)

            # Update UI slider and labels
            if ch_id in self.channel_contrast_sliders:
                slider = self.channel_contrast_sliders[ch_id]
                # Expand slider range if needed
                current_max = slider.maximum()
                if max_val > current_max:
                    slider.setRange(0, max(max_val + 100, 1000))
                slider.blockSignals(True)
                slider.setValue((min_val, max_val))
                slider.blockSignals(False)

            if ch_id in self.channel_min_labels:
                self.channel_min_labels[ch_id].setText(str(min_val))
            if ch_id in self.channel_max_labels:
                self.channel_max_labels[ch_id].setText(str(max_val))

            # Update channel state
            self._channel_states[ch_id]['contrast_min'] = min_val
            self._channel_states[ch_id]['contrast_max'] = max_val

            self.logger.debug(f"Auto-contrast channel {ch_id}: [{min_val}, {max_val}]")

    def _update_channel_availability(self) -> None:
        """Enable/disable channel controls based on whether data exists."""
        if not self.voxel_storage:
            return

        channels_config = self._config.get('channels', [])

        for ch_id in range(4):
            has_data = self.voxel_storage.has_data(ch_id)
            checkbox = self.channel_checkboxes.get(ch_id)
            slider = self.channel_contrast_sliders.get(ch_id)
            min_lbl = self.channel_min_labels.get(ch_id)
            max_lbl = self.channel_max_labels.get(ch_id)

            if checkbox and has_data and not checkbox.isEnabled():
                # Auto-enable on first data arrival
                checkbox.setEnabled(True)
                checkbox.setChecked(True)
                if slider:
                    slider.setEnabled(True)
                if min_lbl:
                    min_lbl.setEnabled(True)
                if max_lbl:
                    max_lbl.setEnabled(True)

                # Explicitly set layer visibility (don't rely only on signal)
                if ch_id in self.channel_layers:
                    self.channel_layers[ch_id].visible = True
                    self._channel_states[ch_id]['visible'] = True

                name = channels_config[ch_id].get('name', f'Ch {ch_id}') if ch_id < len(channels_config) else f'Ch {ch_id}'
                checkbox.setToolTip(f"{name} — Data available. Click to toggle visibility.")
                self.logger.info(f"Channel {ch_id} auto-enabled (data received)")

    def _get_viewer(self):
        """Get the napari viewer owned by this Sample View."""
        return self.viewer

    # ========== Dialog Launchers ==========

    def _on_saved_positions_clicked(self) -> None:
        """Open saved positions dialog."""
        from py2flamingo.views.position_history_dialog import PositionHistoryDialog

        if not self.movement_controller:
            self.logger.warning("No movement controller - cannot open position history")
            return

        try:
            dialog = PositionHistoryDialog(self.movement_controller, parent=self)
            dialog.exec_()
        except Exception as e:
            self.logger.error(f"Error opening position history: {e}")

    def _on_viewer_controls_clicked(self) -> None:
        """Open viewer controls dialog for napari layer settings."""
        dialog = ViewerControlsDialog(
            viewer_container=self,  # SampleView now owns viewer and channel_layers
            config=self._config,
            parent=self
        )
        # Connect signal to update plane views when channel settings change
        dialog.plane_views_update_requested.connect(self._update_plane_views)
        dialog.exec_()

    def _on_stage_control_clicked(self) -> None:
        """Open the Stage Chamber Visualization window."""
        # Try to find and show the stage chamber visualization window
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            for widget in app.topLevelWidgets():
                if widget.__class__.__name__ == 'StageChamberVisualizationWindow':
                    widget.show()
                    widget.raise_()
                    widget.activateWindow()
                    self.logger.info("Opened Stage Chamber Visualization window")
                    return

        self.logger.info("Stage Chamber Visualization window not available")

    def _on_export_data_clicked(self) -> None:
        """Export accumulated 3D data to file."""
        if not self.voxel_storage:
            self.logger.warning("No voxel storage - cannot export data")
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Not Ready",
                              "Voxel storage not initialized.")
            return

        from PyQt5.QtWidgets import QFileDialog, QMessageBox
        from pathlib import Path

        # Get last-used path from configuration service
        default_path = ""
        if self._configuration_service:
            saved_path = self._configuration_service.get_sample_3d_data_path()
            if saved_path and Path(saved_path).exists():
                default_path = saved_path

        # Basic export dialog
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export 3D Data", default_path,
            "TIFF Stack (*.tif);;NumPy Array (*.npy);;All Files (*)"
        )
        if file_path:
            # Remember the directory for next time
            if self._configuration_service:
                self._configuration_service.set_sample_3d_data_path(str(Path(file_path).parent))

            try:
                if self.voxel_storage:
                    data = self.voxel_storage.get_display_data()
                    if data is not None and data.size > 0:
                        if file_path.endswith('.npy'):
                            np.save(file_path, data)
                        else:
                            import tifffile
                            tifffile.imwrite(file_path, data)
                        self.logger.info(f"Exported data to {file_path}")
                        QMessageBox.information(self, "Export Complete",
                                              f"Data exported to:\n{file_path}")
                    else:
                        QMessageBox.warning(self, "No Data",
                                          "No data to export. Use 'Populate from Live' first.")
            except Exception as e:
                self.logger.error(f"Export failed: {e}")
                QMessageBox.critical(self, "Export Error", f"Failed to export: {e}")

    def _on_load_test_data_clicked(self) -> None:
        """Load test data from file for benchmarking and testing."""
        from PyQt5.QtWidgets import QFileDialog, QMessageBox
        from pathlib import Path

        if not self.voxel_storage:
            QMessageBox.warning(self, "Not Ready",
                              "Voxel storage not initialized. Open 3D visualization first.")
            return

        # Get last-used path from configuration service
        default_path = ""
        if self._configuration_service:
            saved_path = self._configuration_service.get_sample_3d_data_path()
            if saved_path and Path(saved_path).exists():
                default_path = saved_path

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Test Data", default_path,
            "All Supported (*.zarr *.tif *.tiff *.npy);;Zarr Sessions (*.zarr);;TIFF Files (*.tif *.tiff);;NumPy Arrays (*.npy)"
        )

        if file_path:
            # Remember the directory for next time
            if self._configuration_service:
                self._configuration_service.set_sample_3d_data_path(str(Path(file_path).parent))

            try:
                from py2flamingo.visualization.session_manager import load_test_data

                success = load_test_data(Path(file_path), self.voxel_storage)

                if success:
                    self.logger.info(f"Loaded test data from {file_path}")
                    QMessageBox.information(self, "Data Loaded",
                                          f"Test data loaded successfully from:\n{file_path}")
                    # Update visualization
                    self._update_visualization()
                else:
                    QMessageBox.warning(self, "Load Failed",
                                      f"Failed to load data from:\n{file_path}")
            except Exception as e:
                self.logger.exception(f"Load test data failed: {e}")
                QMessageBox.critical(self, "Load Error", f"Error loading data: {e}")

    def _on_save_session_clicked(self) -> None:
        """Save current 3D data to an OME-Zarr session."""
        from PyQt5.QtWidgets import QInputDialog, QMessageBox

        if not self.voxel_storage:
            QMessageBox.warning(self, "Not Ready",
                              "Voxel storage not initialized. Capture some data first.")
            return

        try:
            from py2flamingo.visualization.session_manager import SessionManager

            if not SessionManager.is_available():
                QMessageBox.warning(self, "Zarr Not Available",
                                  "zarr library not installed.\nInstall with: pip install zarr")
                return

            # Prompt for session name
            session_name, ok = QInputDialog.getText(
                self, "Save Session",
                "Enter session name:",
                text=f"session_{time.strftime('%Y%m%d_%H%M')}"
            )

            if ok and session_name:
                manager = SessionManager()
                session_path = manager.save_session(
                    self.voxel_storage,
                    session_name,
                    description="Saved from Sample View"
                )

                self.logger.info(f"Session saved to {session_path}")
                QMessageBox.information(self, "Session Saved",
                                      f"Session saved to:\n{session_path}")
        except Exception as e:
            self.logger.exception(f"Save session failed: {e}")
            QMessageBox.critical(self, "Save Error", f"Error saving session: {e}")

    def _on_load_session_clicked(self) -> None:
        """Load a saved OME-Zarr session."""
        from PyQt5.QtWidgets import QFileDialog, QMessageBox

        if not self.voxel_storage:
            QMessageBox.warning(self, "Not Ready",
                              "Voxel storage not initialized.")
            return

        try:
            from py2flamingo.visualization.session_manager import SessionManager

            if not SessionManager.is_available():
                QMessageBox.warning(self, "Zarr Not Available",
                                  "zarr library not installed.\nInstall with: pip install zarr")
                return

            # Get last-used path from configuration service (independent of other dialogs)
            start_path = str(SessionManager().session_dir)
            if self._configuration_service:
                saved_path = self._configuration_service.get_zarr_session_path()
                if saved_path:
                    start_path = saved_path

            # Open file dialog for .zarr directory
            file_path = QFileDialog.getExistingDirectory(
                self, "Select Session (.zarr folder)",
                start_path
            )

            if file_path and file_path.endswith('.zarr'):
                from pathlib import Path

                # Save the parent directory for next time
                if self._configuration_service:
                    self._configuration_service.set_zarr_session_path(str(Path(file_path).parent))

                manager = SessionManager()
                metadata = manager.restore_to_storage(self.voxel_storage, Path(file_path))

                self.logger.info(f"Session loaded: {metadata.session_name}")
                QMessageBox.information(self, "Session Loaded",
                                      f"Loaded session: {metadata.session_name}\n"
                                      f"Total voxels: {metadata.total_voxels:,}")

                # Update visualization
                self._update_visualization()
            elif file_path:
                QMessageBox.warning(self, "Invalid Selection",
                                  "Please select a .zarr folder")
        except Exception as e:
            self.logger.exception(f"Load session failed: {e}")
            QMessageBox.critical(self, "Load Error", f"Error loading session: {e}")

    def _on_benchmark_clicked(self) -> None:
        """Open the performance benchmark dialog."""
        try:
            from py2flamingo.views.dialogs.performance_benchmark_dialog import PerformanceBenchmarkDialog

            dialog = PerformanceBenchmarkDialog(
                voxel_storage=self.voxel_storage,
                parent=self
            )
            dialog.exec_()
        except Exception as e:
            self.logger.exception(f"Error opening benchmark dialog: {e}")
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error",
                               f"Could not open benchmark dialog: {e}")

    def _on_settings_clicked(self) -> None:
        """Open the application settings dialog."""
        try:
            from py2flamingo.views.dialogs.settings_dialog import SettingsDialog
            from py2flamingo.services.microscope_settings_service import MicroscopeSettingsService

            # Get or create the settings service
            settings_service = getattr(self, '_settings_service', None)
            if settings_service is None:
                # Try to get microscope name from configuration service
                microscope_name = "n7"  # Default
                if self._configuration_service:
                    config = self._configuration_service.get_current_configuration()
                    if config:
                        microscope_name = config.get('name', 'n7')

                # Create settings service (will use the microscope_settings directory)
                from pathlib import Path
                base_path = Path(__file__).parent.parent.parent.parent  # Go up to project root
                settings_service = MicroscopeSettingsService(microscope_name, base_path)
                self._settings_service = settings_service

            dialog = SettingsDialog(
                settings_service=settings_service,
                parent=self
            )

            if dialog.exec_():
                self.logger.info("Settings dialog accepted - settings saved")
                # Notify user if display settings changed (requires restart)
                settings = dialog.get_settings()
                if 'display' in settings:
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.information(
                        self, "Settings Saved",
                        "Display settings have been saved.\n\n"
                        "Note: Changes to storage voxel size or downsample factor "
                        "will take effect after restarting the application."
                    )
        except Exception as e:
            self.logger.exception(f"Error opening settings dialog: {e}")
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error",
                               f"Could not open settings dialog: {e}")

    # ========== Data Collection Controls ==========

    def _on_populate_toggled(self, checked: bool) -> None:
        """Handle populate from live view start/stop."""
        if not self.voxel_storage:
            self.logger.warning("No voxel storage - cannot populate")
            self.populate_btn.setChecked(False)
            return

        self._is_populating = checked
        if checked:
            self.populate_btn.setText("Stop Populating")
            self.logger.info("Started populating from live view")
            # Start populate timer if not running
            if not hasattr(self, '_populate_timer'):
                self._populate_timer = QTimer()
                self._populate_timer.timeout.connect(self._on_populate_tick)
                self._populate_timer.setInterval(100)  # 10 Hz
            self._populate_timer.start()
        else:
            self.populate_btn.setText("Populate from Live")
            self.logger.info("Stopped populating from live view")
            if hasattr(self, '_populate_timer'):
                self._populate_timer.stop()

    def _on_populate_tick(self) -> None:
        """Capture current frame and add to 3D volume."""
        if not getattr(self, '_is_populating', False) or not self.camera_controller:
            return

        try:
            if not self.camera_controller.is_live_view_active():
                return

            if not self.movement_controller:
                return

            position = self.movement_controller.get_position()
            if position is None:
                return

            frame_data = self.camera_controller.get_latest_frame()
            if frame_data is None:
                return

            image, header, frame_num = frame_data

            # Detect active channel
            channel_id = self._detect_active_channel()
            if channel_id is None:
                return

            # Add frame to volume
            stage_pos = {'x': position.x, 'y': position.y, 'z': position.z}
            self.add_frame_to_volume(image, stage_pos, channel_id)

        except Exception as e:
            self.logger.debug(f"Populate tick error: {e}")

    def _detect_active_channel(self) -> Optional[int]:
        """Detect which channel is currently active based on laser state."""
        if not self.laser_led_controller:
            return 0

        try:
            laser_states = self.laser_led_controller.get_laser_states()
            for ch_id, is_on in enumerate(laser_states[:4]):
                if is_on:
                    return ch_id
            return None  # No laser on (probably LED)
        except:
            return 0

    def _on_clear_data_clicked(self) -> None:
        """Clear all accumulated 3D data."""
        if not self.voxel_storage:
            self.logger.warning("No voxel storage - cannot clear data")
            return

        from PyQt5.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Clear Data",
            "Are you sure you want to clear all accumulated data?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.voxel_storage.clear()
            self._update_visualization()
            self.logger.info("Cleared all visualization data")

    def add_frame_to_volume(self, image: np.ndarray, stage_position_mm: dict,
                            channel_id: int, timestamp: float = None,
                            use_stage_y_delta: bool = False) -> None:
        """
        Place a camera frame into the 3D voxel storage.

        Args:
            image: Camera image
            stage_position_mm: {'x': float, 'y': float, 'z': float} in mm
            channel_id: Channel index (0-3)
            timestamp: Optional timestamp in ms
            use_stage_y_delta: If True, use stage Y position delta for 3D placement
                (for tile workflows where tiles are at different Y positions).
                If False, place all data at focal plane Y (for live view where
                data is always collected at the focal plane).
        """
        if not self.voxel_storage:
            return

        try:
            import time as time_module
            if timestamp is None:
                timestamp = time_module.time() * 1000

            # Downsample image to storage resolution
            downsampled = self._downsample_for_storage(image)
            H, W = downsampled.shape

            # Generate pixel coordinate grid
            y_indices, x_indices = np.meshgrid(np.arange(H), np.arange(W), indexing='ij')

            # Calculate FOV from magnification
            FOV_mm = 0.5182  # Field of view in mm
            FOV_um = FOV_mm * 1000
            pixel_size_um = FOV_um / W

            # Convert to camera space (micrometers)
            camera_x = (x_indices - W/2) * pixel_size_um
            camera_y = (y_indices - H/2) * pixel_size_um

            # Stack into (N, 2) array
            camera_coords_2d = np.column_stack([camera_x.ravel(), camera_y.ravel()])

            # Get sample region center from config
            sample_center = self._config.get('sample_chamber', {}).get(
                'sample_region_center_um', [6655, 7000, 19250]
            )

            # Get reference position
            pos_x = stage_position_mm['x']
            pos_y = stage_position_mm['y']
            pos_z = stage_position_mm['z']

            if self.voxel_storage.reference_stage_position is None:
                # Set the reference position on first frame - this persists it
                # so subsequent frames calculate deltas relative to this reference
                self.voxel_storage.set_reference_position(stage_position_mm)
                self.logger.info(f"First frame - set reference position to ({pos_x:.3f}, {pos_y:.3f}, {pos_z:.3f})")

            # Always use the stored reference position
            ref_x = self.voxel_storage.reference_stage_position['x']
            ref_y = self.voxel_storage.reference_stage_position['y']
            ref_z = self.voxel_storage.reference_stage_position['z']

            # Calculate stage delta from reference
            delta_x = pos_x - ref_x
            delta_y = pos_y - ref_y
            delta_z = pos_z - ref_z

            # Log delta values for debugging 3D distribution
            self.logger.debug(f"add_frame_to_volume: pos=({pos_x:.3f}, {pos_y:.3f}, {pos_z:.3f}), "
                             f"ref=({ref_x:.3f}, {ref_y:.3f}, {ref_z:.3f}), "
                             f"delta=({delta_x:.3f}, {delta_y:.3f}, {delta_z:.3f})")

            # Storage position (ZYX order)
            base_z_um = sample_center[2]
            base_y_um = sample_center[1]
            base_x_um = sample_center[0]

            # X-axis storage handling:
            # - When invert_x=True: use -delta_x (storage inverts, display inverts -> correct)
            # - When invert_x=False: use +delta_x (storage normal, display normal -> correct)
            delta_x_storage = -delta_x if self._invert_x else delta_x

            # World coordinates for this frame based on stage position deltas
            # Y behavior depends on workflow:
            # - Tile workflow (use_stage_y_delta=True): Y varies with tile position
            # - Live view (use_stage_y_delta=False): Y fixed at focal plane
            y_offset = delta_y * 1000 if use_stage_y_delta else 0
            world_center_um = np.array([
                base_z_um - delta_z * 1000,       # Z varies with stage movement
                base_y_um + y_offset,             # Y: conditional on workflow
                base_x_um + delta_x_storage * 1000  # X varies with stage movement
            ])

            # Log world center for debugging 3D placement
            self.logger.info(f"Frame placed at world_center_um (Z,Y,X): ({world_center_um[0]:.1f}, {world_center_um[1]:.1f}, {world_center_um[2]:.1f})")

            # Create 3D coords
            slice_thickness_um = 100
            num_pixels = len(camera_coords_2d)
            z_offsets = np.linspace(-slice_thickness_um/2, slice_thickness_um/2, num_pixels)

            camera_offsets_3d = np.column_stack([
                z_offsets,
                camera_coords_2d[:, 1],
                camera_coords_2d[:, 0]
            ])

            world_coords_3d = camera_offsets_3d + world_center_um
            values = downsampled.ravel()

            # Update voxel storage
            self.voxel_storage.update_storage(
                channel_id=channel_id,
                world_coords=world_coords_3d,
                pixel_values=values,
                timestamp=timestamp,
                update_mode='maximum'
            )

            # Trigger channel availability check
            if hasattr(self, '_channel_availability_timer'):
                self._channel_availability_timer.start()

            # Trigger debounced visualization update
            if hasattr(self, '_visualization_update_timer'):
                self._visualization_update_timer.start()

        except Exception as e:
            self.logger.error(f"Error in add_frame_to_volume: {e}", exc_info=True)

    def _downsample_for_storage(self, image: np.ndarray) -> np.ndarray:
        """Downsample camera image to storage resolution."""
        from scipy.ndimage import zoom

        if image.ndim == 3:
            image = image[:, :, 0]

        # Calculate downsample factor (camera ~2000px to storage ~100px)
        target_size = 100
        current_size = max(image.shape)
        factor = target_size / current_size

        if factor < 1:
            return zoom(image, factor, order=1).astype(np.uint16)
        return image.astype(np.uint16)

        # Reset channel controls to disabled
        channels_config = self._config.get('channels', [])
        for ch_id in range(4):
            cb = self.channel_checkboxes.get(ch_id)
            sl = self.channel_contrast_sliders.get(ch_id)
            ml = self.channel_min_labels.get(ch_id)
            xl = self.channel_max_labels.get(ch_id)
            if cb:
                cb.setEnabled(False)
                cb.setChecked(False)
                name = channels_config[ch_id].get('name', f'Ch {ch_id}') if ch_id < len(channels_config) else f'Ch {ch_id}'
                cb.setToolTip(
                    f"{name} channel — No data loaded.\n"
                    "This channel will activate automatically when 3D volume data is received."
                )
            if sl:
                sl.setEnabled(False)
            if ml:
                ml.setEnabled(False)
            if xl:
                xl.setEnabled(False)

    # ========== 3D Viewer Integration ==========

    def _embed_3d_viewer(self) -> None:
        """Create and embed the napari 3D viewer."""
        if not NAPARI_AVAILABLE:
            self.logger.warning("napari not available - 3D viewer not created")
            return

        if not self.voxel_storage:
            self.logger.warning("No voxel_storage available - 3D viewer not created")
            return

        try:
            t_start = time.perf_counter()

            # Create napari viewer with axis display
            self.viewer = napari.Viewer(ndisplay=3, show=False)
            t_viewer = time.perf_counter()
            self.logger.info(f"napari.Viewer() created in {t_viewer - t_start:.2f}s")

            # Enable axis display
            self.viewer.axes.visible = True
            self.viewer.axes.labels = True
            self.viewer.axes.colored = True

            # Set initial camera orientation
            self.viewer.camera.angles = (0, 0, 180)
            self.viewer.camera.zoom = 1.57

            # Get the napari Qt widget
            napari_window = self.viewer.window
            viewer_widget = napari_window._qt_viewer

            # Replace placeholder with actual viewer
            if hasattr(self, 'viewer_placeholder') and self.viewer_placeholder:
                parent_widget = self.viewer_placeholder.parent()
                if parent_widget:
                    layout = parent_widget.layout()
                    if layout:
                        layout.replaceWidget(self.viewer_placeholder, viewer_widget)
                        self.viewer_placeholder.deleteLater()
                        self.viewer_placeholder = None

            t_embed = time.perf_counter()

            # Setup visualization components
            self._setup_chamber_visualization()
            t_chamber = time.perf_counter()
            self.logger.info(f"Chamber visualization setup in {t_chamber - t_embed:.2f}s")

            self._setup_data_layers()
            t_layers = time.perf_counter()
            self.logger.info(f"Data layers setup in {t_layers - t_chamber:.2f}s")

            self.logger.info(f"Created napari 3D viewer successfully (total: {t_layers - t_start:.2f}s)")

            # Reset camera after setup
            QTimer.singleShot(100, self._reset_viewer_camera)

        except Exception as e:
            self.logger.error(f"Failed to create 3D viewer: {e}")
            import traceback
            traceback.print_exc()
            self.viewer = None

    def _setup_chamber_visualization(self) -> None:
        """Setup the fixed chamber wireframe as visual guide."""
        if not self.viewer or not self.voxel_storage:
            return

        try:
            dims = self.voxel_storage.display_dims  # (Z, Y, X) order

            # Define the 8 corners of the box in napari (Z, Y, X) order
            corners = np.array([
                [0, 0, 0],
                [dims[0]-1, 0, 0],
                [dims[0]-1, 0, dims[2]-1],
                [0, 0, dims[2]-1],
                [0, dims[1]-1, 0],
                [dims[0]-1, dims[1]-1, 0],
                [dims[0]-1, dims[1]-1, dims[2]-1],
                [0, dims[1]-1, dims[2]-1]
            ])

            # All 12 chamber edges combined into single layer for performance
            # Z edges (yellow), Y edges (magenta), X edges (cyan)
            all_edges = [
                # Z edges (4)
                [corners[0], corners[1]],
                [corners[3], corners[2]],
                [corners[4], corners[5]],
                [corners[7], corners[6]],
                # Y edges (4)
                [corners[0], corners[4]],
                [corners[1], corners[5]],
                [corners[2], corners[6]],
                [corners[3], corners[7]],
                # X edges (4)
                [corners[0], corners[3]],
                [corners[1], corners[2]],
                [corners[4], corners[7]],
                [corners[5], corners[6]]
            ]

            # Per-edge colors: 4 yellow (Z), 4 magenta (Y), 4 cyan (X)
            edge_colors = (
                ['#8B8B00'] * 4 +  # Z edges
                ['#8B008B'] * 4 +  # Y edges
                ['#008B8B'] * 4    # X edges
            )

            self.viewer.add_shapes(
                data=all_edges, shape_type='line', name='Chamber Wireframe',
                edge_color=edge_colors, edge_width=2, opacity=0.6
            )

            # Add additional visualization elements
            self._add_sample_holder()
            self._add_fine_extension()
            self._add_objective_indicator()
            self._add_rotation_indicator()
            self._add_xy_focus_frame()

        except Exception as e:
            self.logger.warning(f"Failed to setup chamber visualization: {e}")

    def _add_sample_holder(self) -> None:
        """Add sample holder indicator at the top of the chamber.

        The holder is a gray sphere shown at Y=0 (chamber top), representing
        the mounting point where the sample holder enters the chamber.
        """
        if not self.viewer or not self.voxel_storage:
            return

        try:
            # Get holder dimensions from config
            holder_diameter_mm = self._config.get('sample_chamber', {}).get('holder_diameter_mm', 3.0)
            voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])[0]
            holder_radius_voxels = int((holder_diameter_mm * 1000 / 2) / voxel_size_um)
            voxel_size_mm = voxel_size_um / 1000.0

            dims = self.voxel_storage.display_dims  # (Z, Y, X)

            # Get initial position from sliders
            x_mm = 0
            stage_y_mm = 0
            z_mm = 0
            if 'x' in self.position_sliders:
                x_mm = self.position_sliders['x'].value() / self._slider_scale
            if 'y' in self.position_sliders:
                stage_y_mm = self.position_sliders['y'].value() / self._slider_scale
            if 'z' in self.position_sliders:
                z_mm = self.position_sliders['z'].value() / self._slider_scale

            # Convert stage Y to chamber Y (where extension tip is)
            chamber_y_tip_mm = self._stage_y_to_chamber_y(stage_y_mm)

            # Get coordinate ranges from config
            x_range = self._config.get('stage_control', {}).get('x_range_mm', [1.0, 12.31])
            y_range = self._config.get('stage_control', {}).get('y_range_mm', [0.0, 14.0])
            z_range = self._config.get('stage_control', {}).get('z_range_mm', [12.5, 26.0])

            # Convert physical mm to napari voxel coordinates
            # X is inverted in napari if configured
            if self._invert_x:
                napari_x = int((x_range[1] - x_mm) / voxel_size_mm)
            else:
                napari_x = int((x_mm - x_range[0]) / voxel_size_mm)

            # Y is inverted in napari (Y=0 at top, increases downward)
            napari_y_tip = int((y_range[1] - chamber_y_tip_mm) / voxel_size_mm)

            # Z is offset from range minimum
            napari_z = int((z_mm - z_range[0]) / voxel_size_mm)

            # Clamp to valid range
            napari_x = max(0, min(dims[2] - 1, napari_x))
            napari_y_tip = max(0, min(dims[1] - 1, napari_y_tip))
            napari_z = max(0, min(dims[0] - 1, napari_z))

            # Store holder TIP position (what matters for extension and sample data)
            self.holder_position = {'x': napari_x, 'y': napari_y_tip, 'z': napari_z}

            # Create holder indicator at chamber top (Y=0) - the mounting point
            # Note: The holder_position stores the TIP, but we display at Y=0
            holder_point = np.array([[napari_z, 0, napari_x]])

            self.viewer.add_points(
                holder_point,
                name='Sample Holder',
                size=holder_radius_voxels * 2,
                face_color='gray',
                border_color='darkgray',
                border_width=0.05,
                opacity=0.6,
                shading='spherical'
            )

            self.logger.info(f"Added sample holder at napari coords: Z={napari_z}, Y=0, X={napari_x}")
            self.logger.info(f"  Holder TIP position: Y_tip={napari_y_tip} (stage_y={stage_y_mm:.2f}mm, chamber_y={chamber_y_tip_mm:.2f}mm)")

        except Exception as e:
            self.logger.warning(f"Failed to add sample holder: {e}")

    def _add_fine_extension(self) -> None:
        """Add fine extension (thin probe) showing sample position.

        The extension shows where the sample is positioned. The TIP is at the
        sample location (holder_position['y']), and it extends UPWARD by 10mm
        (toward chamber top) for visibility.

        In napari coordinates, upward = decreasing Y values.
        """
        if not self.viewer or not self.voxel_storage:
            return

        try:
            dims = self.voxel_storage.display_dims
            voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])[0]
            voxel_size_mm = voxel_size_um / 1000.0

            # Get extension TIP position (stored in holder_position)
            napari_x = self.holder_position['x']
            napari_y_tip = self.holder_position['y']  # Extension tip (where sample is attached)
            napari_z = self.holder_position['z']

            # Extension extends UPWARD from tip by extension_length_mm (10mm)
            # Napari Y is inverted: upward = DECREASING Y values
            extension_length_voxels = int(self.extension_length_mm / voxel_size_mm)
            napari_y_top = napari_y_tip - extension_length_voxels  # Top is ABOVE tip (smaller Y)

            # Extension from top (smaller Y) to tip (larger Y)
            y_start = max(0, napari_y_top)  # Clamp to chamber top if needed
            y_end = napari_y_tip  # End at tip (sample position)

            # Create vertical line of points for extension
            # Napari coordinates: (Z, Y, X) order
            extension_points = []
            for y in range(y_start, y_end + 1, 2):
                extension_points.append([napari_z, y, napari_x])

            self.logger.info(f"Extension: {len(extension_points)} points from Y={y_start} (top) to Y={y_end} (tip)")

            if extension_points:
                extension_array = np.array(extension_points)
                self.viewer.add_points(
                    extension_array,
                    name='Fine Extension',
                    size=4,
                    face_color='#FFFF00',  # Bright yellow
                    border_color='#FFA500',  # Orange border
                    border_width=0.1,
                    opacity=0.9,
                    shading='spherical'
                )

        except Exception as e:
            self.logger.warning(f"Failed to add fine extension: {e}")

    def _add_objective_indicator(self) -> None:
        """Add objective position indicator circle at Z=0 (back wall)."""
        if not self.viewer or not self.voxel_storage:
            return

        try:
            dims = self.voxel_storage.display_dims  # (Z, Y, X)

            # Objective at Z=0 (back wall)
            z_objective = 0

            # Objective focal plane position
            voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])[0]
            voxel_size_mm = voxel_size_um / 1000.0

            # Y position at objective focal plane (7mm from top in physical coords)
            # In napari, Y is inverted
            y_range = self._config.get('stage_control', {}).get('y_range_mm', [0, 14])
            napari_y_objective = int((y_range[1] - self.OBJECTIVE_CHAMBER_Y_MM) / voxel_size_mm)
            napari_y_objective = min(max(0, napari_y_objective), dims[1] - 1)

            center_y = napari_y_objective
            center_x = dims[2] // 2

            # Circle radius (1/6 of smaller dimension)
            radius = min(dims[1], dims[2]) // 6

            # Create circle as line segments
            num_points = 36
            angles = np.linspace(0, 2 * np.pi, num_points, endpoint=False)

            circle_points = []
            for angle in angles:
                y = center_y + radius * np.cos(angle)
                x = center_x + radius * np.sin(angle)
                circle_points.append([z_objective, y, x])

            # Create circle edges
            circle_edges = [[circle_points[i], circle_points[(i+1) % len(circle_points)]]
                           for i in range(len(circle_points))]

            self.viewer.add_shapes(
                data=circle_edges,
                shape_type='line',
                name='Objective',
                edge_color='#FFCC00',  # Gold/yellow
                edge_width=3,
                opacity=0.3
            )

        except Exception as e:
            self.logger.warning(f"Failed to add objective indicator: {e}")

    def _add_rotation_indicator(self) -> None:
        """Add rotation indicator line at top of chamber."""
        if not self.viewer or not self.voxel_storage:
            return

        try:
            dims = self.voxel_storage.display_dims

            # Indicator length - 1/2 shortest dimension
            indicator_length = min(dims[0], dims[2]) // 2
            self.rotation_indicator_length = indicator_length

            # Position at Y=0 (top of chamber)
            y_position = 0
            holder_z = self.holder_position['z']
            holder_x = self.holder_position['x']

            # At 0 degrees, extends along +X axis
            indicator_start = np.array([holder_z, y_position, holder_x])
            indicator_end = np.array([holder_z, y_position, holder_x + indicator_length])

            # Get color based on rotation angle
            initial_color = self._get_rotation_gradient_color(self.current_rotation.get('ry', 0))

            self.viewer.add_shapes(
                data=[[indicator_start, indicator_end]],
                shape_type='line',
                name='Rotation Indicator',
                edge_color=initial_color,
                edge_width=3,
                opacity=0.8
            )

            # Immediately update indicator to current rotation angle
            # (indicator was created at 0°, but actual rotation may differ)
            self._update_rotation_indicator()
            self.logger.info(f"Rotation indicator initialized at {self.current_rotation.get('ry', 0):.1f}°")

        except Exception as e:
            self.logger.warning(f"Failed to add rotation indicator: {e}")

    def _add_xy_focus_frame(self) -> None:
        """Add XY focus frame showing camera field of view at focal plane."""
        if not self.viewer or not self.voxel_storage:
            return

        try:
            dims = self.voxel_storage.display_dims
            voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])[0]
            voxel_size_mm = voxel_size_um / 1000.0

            # Focus frame configuration
            focus_config = self._config.get('focus_frame', {})
            fov_x_mm = focus_config.get('field_of_view_x_mm', 0.52)
            fov_y_mm = focus_config.get('field_of_view_y_mm', 0.52)
            frame_color = focus_config.get('color', '#FFFF00')
            edge_width = focus_config.get('edge_width', 3)
            opacity = focus_config.get('opacity', 0.9)

            # FOV in voxels
            fov_x_voxels = fov_x_mm / voxel_size_mm
            fov_y_voxels = fov_y_mm / voxel_size_mm

            # Position at objective focal plane
            x_range = self._config.get('stage_control', {}).get('x_range_mm', [1.0, 12.31])
            y_range = self._config.get('stage_control', {}).get('y_range_mm', [0, 14])
            z_range = self._config.get('stage_control', {}).get('z_range_mm', [12.5, 26])

            # Use calibration if available, otherwise center of ranges
            if self.objective_xy_calibration:
                focal_x_mm = self.objective_xy_calibration.get('x', (x_range[0] + x_range[1]) / 2)
                focal_z_mm = self.objective_xy_calibration.get('z', (z_range[0] + z_range[1]) / 2)
            else:
                focal_x_mm = (x_range[0] + x_range[1]) / 2
                focal_z_mm = (z_range[0] + z_range[1]) / 2

            # Convert physical Z to napari Z (offset from range minimum)
            napari_z = int((focal_z_mm - z_range[0]) / voxel_size_mm)
            napari_z = min(max(0, napari_z), dims[0] - 1)

            # Y at objective focal plane (7mm in chamber coordinates)
            # Y is inverted in napari (Y=0 at top)
            napari_y = int((y_range[1] - self.OBJECTIVE_CHAMBER_Y_MM) / voxel_size_mm)
            napari_y = min(max(0, napari_y), dims[1] - 1)

            # X from calibration or center, with proper coordinate conversion
            if self._invert_x:
                napari_x = int((x_range[1] - focal_x_mm) / voxel_size_mm)
            else:
                napari_x = int((focal_x_mm - x_range[0]) / voxel_size_mm)
            napari_x = min(max(0, napari_x), dims[2] - 1)

            # Frame corners
            half_fov_x = fov_x_voxels / 2
            half_fov_y = fov_y_voxels / 2

            corners = [
                [napari_z, napari_y - half_fov_y, napari_x - half_fov_x],
                [napari_z, napari_y - half_fov_y, napari_x + half_fov_x],
                [napari_z, napari_y + half_fov_y, napari_x + half_fov_x],
                [napari_z, napari_y + half_fov_y, napari_x - half_fov_x],
            ]

            frame_edges = [
                [corners[0], corners[1]],
                [corners[1], corners[2]],
                [corners[2], corners[3]],
                [corners[3], corners[0]],
            ]

            self.viewer.add_shapes(
                data=frame_edges,
                shape_type='line',
                name='XY Focus Frame',
                edge_color=frame_color,
                edge_width=edge_width,
                opacity=opacity
            )

        except Exception as e:
            self.logger.warning(f"Failed to add XY focus frame: {e}")

    def _get_rotation_gradient_color(self, angle_degrees: float) -> str:
        """Get color for rotation indicator based on angle."""
        # Normalize angle to 0-360
        angle = angle_degrees % 360
        if angle < 0:
            angle += 360

        # Color gradient: 0°=red, 90°=yellow, 180°=green, 270°=cyan, 360°=red
        if angle < 90:
            r = 255
            g = int(255 * angle / 90)
            b = 0
        elif angle < 180:
            r = int(255 * (180 - angle) / 90)
            g = 255
            b = 0
        elif angle < 270:
            r = 0
            g = 255
            b = int(255 * (angle - 180) / 90)
        else:
            r = 0
            g = int(255 * (360 - angle) / 90)
            b = 255

        return f'#{r:02x}{g:02x}{b:02x}'

    def _stage_y_to_chamber_y(self, stage_y_mm: float) -> float:
        """Convert stage Y position to chamber Y coordinate."""
        # At stage Y = 7.45mm, extension tip is at objective focal plane (Y=7.0mm)
        offset = stage_y_mm - self.STAGE_Y_AT_OBJECTIVE
        return self.OBJECTIVE_CHAMBER_Y_MM + offset

    def _update_sample_holder_position(self, x_mm: float, y_mm: float, z_mm: float):
        """Update sample holder position when stage moves.

        Args:
            x_mm, y_mm, z_mm: Physical stage coordinates in mm (y_mm is stage control value)
        """
        if not self.viewer or 'Sample Holder' not in self.viewer.layers:
            return
        if not self.voxel_storage:
            return

        # Convert stage Y to chamber Y (where extension tip actually is)
        chamber_y_tip_mm = self._stage_y_to_chamber_y(y_mm)

        # Get coordinate conversion parameters from config
        voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])[0]
        voxel_size_mm = voxel_size_um / 1000.0
        x_range = self._config.get('stage_control', {}).get('x_range_mm', [1.0, 12.31])
        y_range = self._config.get('stage_control', {}).get('y_range_mm', [0.0, 14.0])
        z_range = self._config.get('stage_control', {}).get('z_range_mm', [12.5, 26.0])
        dims = self.voxel_storage.display_dims  # (Z, Y, X)

        # Convert physical mm to napari voxel coordinates
        if self._invert_x:
            napari_x = int((x_range[1] - x_mm) / voxel_size_mm)
        else:
            napari_x = int((x_mm - x_range[0]) / voxel_size_mm)

        napari_y_tip = int((y_range[1] - chamber_y_tip_mm) / voxel_size_mm)
        napari_z = int((z_mm - z_range[0]) / voxel_size_mm)

        # Clamp to valid range
        napari_x = max(0, min(dims[2] - 1, napari_x))
        napari_y_tip = max(0, min(dims[1] - 1, napari_y_tip))
        napari_z = max(0, min(dims[0] - 1, napari_z))

        # Update holder TIP position
        self.holder_position = {'x': napari_x, 'y': napari_y_tip, 'z': napari_z}

        # Holder shown as single ball at chamber top (Y=0) - the mounting point
        holder_point = np.array([[napari_z, 0, napari_x]])
        self.viewer.layers['Sample Holder'].data = holder_point

        self.logger.debug(f"Updated holder: stage ({x_mm:.2f}, {y_mm:.2f}, {z_mm:.2f}) -> "
                         f"napari (Z={napari_z}, Y_tip={napari_y_tip}, X={napari_x})")

        # Update dependent elements
        self._update_fine_extension()
        self._update_rotation_indicator()

    def _update_fine_extension(self):
        """Update fine extension position based on current holder position.

        The extension shows where the sample is positioned. The TIP is at the
        sample location (holder_position['y']), and it extends UPWARD by 10mm
        (toward chamber top) for visibility.

        In napari coordinates, upward = decreasing Y values.
        """
        if not self.viewer or 'Fine Extension' not in self.viewer.layers:
            return
        if not self.voxel_storage:
            return

        # Get current TIP position (where sample is attached)
        napari_x = self.holder_position['x']
        napari_y_tip = self.holder_position['y']  # Extension tip (sample position)
        napari_z = self.holder_position['z']

        # Extension extends UPWARD from tip by extension_length_mm (10mm)
        # Napari Y is inverted: upward = DECREASING Y values
        voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])[0]
        voxel_size_mm = voxel_size_um / 1000.0
        extension_length_voxels = int(self.extension_length_mm / voxel_size_mm)
        napari_y_top = napari_y_tip - extension_length_voxels  # Top is ABOVE tip (smaller Y)

        # Extension from top (smaller Y) to tip (larger Y)
        y_start = max(0, napari_y_top)  # Clamp to chamber top if needed
        y_end = napari_y_tip  # End at tip (sample position)

        # Create vertical line of points in (Z, Y, X) order
        extension_points = []
        for y in range(y_start, y_end + 1, 2):
            extension_points.append([napari_z, y, napari_x])

        if extension_points:
            self.viewer.layers['Fine Extension'].data = np.array(extension_points)
        else:
            # If no points, show minimal placeholder
            self.viewer.layers['Fine Extension'].data = np.array([[napari_z, y_start, napari_x]])

    def _update_rotation_indicator(self):
        """Update rotation indicator based on current rotation angle and holder position."""
        if not self.viewer or 'Rotation Indicator' not in self.viewer.layers:
            return

        angle_deg = self.current_rotation.get('ry', 0)
        angle_rad = np.radians(angle_deg)

        indicator_color = self._get_rotation_gradient_color(angle_deg)

        # Indicator at Y=0 (top of chamber), follows holder X/Z position
        y_position = 0
        start = np.array([
            self.holder_position['z'],
            y_position,
            self.holder_position['x']
        ])

        # End point rotated in ZX plane
        dx = self.rotation_indicator_length * np.cos(angle_rad)
        dz = self.rotation_indicator_length * np.sin(angle_rad)

        end = np.array([
            start[0] + dz,
            y_position,
            start[2] + dx
        ])

        self.viewer.layers['Rotation Indicator'].data = [[start, end]]
        self.viewer.layers['Rotation Indicator'].edge_color = [indicator_color]

    def _update_xy_focus_frame(self):
        """Update XY focus frame position based on calibration.

        The focus frame is at a FIXED position (focal plane) and only needs
        to be updated when the calibration changes, not when the stage moves.
        """
        if not self.viewer or 'XY Focus Frame' not in self.viewer.layers:
            return
        if not self.voxel_storage:
            return

        dims = self.voxel_storage.display_dims
        voxel_size_um = self._config.get('display', {}).get('voxel_size_um', [50, 50, 50])[0]
        voxel_size_mm = voxel_size_um / 1000.0

        focus_config = self._config.get('focus_frame', {})
        fov_x_mm = focus_config.get('field_of_view_x_mm', 0.52)
        fov_y_mm = focus_config.get('field_of_view_y_mm', 0.52)

        # Y position at objective focal plane
        y_range = self._config.get('stage_control', {}).get('y_range_mm', [0, 14])
        napari_y = int((y_range[1] - self.OBJECTIVE_CHAMBER_Y_MM) / voxel_size_mm)
        napari_y = min(max(0, napari_y), dims[1] - 1)

        # X and Z from calibration or use defaults
        if self.objective_xy_calibration:
            x_mm = self.objective_xy_calibration['x']
            z_mm = self.objective_xy_calibration['z']
        else:
            x_range = self._config.get('stage_control', {}).get('x_range_mm', [1.0, 12.31])
            z_range = self._config.get('stage_control', {}).get('z_range_mm', [12.5, 26.0])
            x_mm = (x_range[0] + x_range[1]) / 2
            z_mm = (z_range[0] + z_range[1]) / 2

        # Convert to napari coordinates
        x_range = self._config.get('stage_control', {}).get('x_range_mm', [1.0, 12.31])
        z_range = self._config.get('stage_control', {}).get('z_range_mm', [12.5, 26.0])

        if self._invert_x:
            napari_x = int((x_range[1] - x_mm) / voxel_size_mm)
        else:
            napari_x = int((x_mm - x_range[0]) / voxel_size_mm)
        napari_z = int((z_mm - z_range[0]) / voxel_size_mm)

        napari_x = max(0, min(dims[2] - 1, napari_x))
        napari_z = max(0, min(dims[0] - 1, napari_z))

        # FOV in voxels
        half_fov_x = (fov_x_mm / voxel_size_mm) / 2
        half_fov_y = (fov_y_mm / voxel_size_mm) / 2

        corners = [
            [napari_z, napari_y - half_fov_y, napari_x - half_fov_x],
            [napari_z, napari_y - half_fov_y, napari_x + half_fov_x],
            [napari_z, napari_y + half_fov_y, napari_x + half_fov_x],
            [napari_z, napari_y + half_fov_y, napari_x - half_fov_x],
        ]

        frame_edges = [
            [corners[0], corners[1]],
            [corners[1], corners[2]],
            [corners[2], corners[3]],
            [corners[3], corners[0]],
        ]

        self.viewer.layers['XY Focus Frame'].data = frame_edges

        self.logger.info(f"Updated XY focus frame to X={x_mm:.2f}, Z={z_mm:.2f} mm "
                        f"(napari X={napari_x}, Z={napari_z})")

    def _process_pending_stage_update(self):
        """Process pending stage position update for 3D visualization.

        Called by the throttle timer (50ms interval / 20 FPS max) to avoid
        overwhelming the GUI with rapid position updates.
        """
        if self._pending_stage_update is None:
            self._stage_update_timer.stop()
            return

        # Pop pending position
        stage_pos = self._pending_stage_update
        self._pending_stage_update = None

        # Store last stage position
        self.last_stage_position = stage_pos

        # Update rotation tracking
        self.current_rotation['ry'] = stage_pos.get('r', 0)

        # Update reference geometry (holder, extension, rotation indicator)
        self._update_sample_holder_position(
            stage_pos['x'], stage_pos['y'], stage_pos['z']
        )

        # Update data layers with transformed volumes (data moves with stage)
        if not self.voxel_storage:
            return

        # Get holder position for rotation center
        holder_pos_voxels = np.array([
            self.holder_position['x'],
            self.holder_position['y'],
            self.holder_position['z']
        ])

        for ch_id in range(self.voxel_storage.num_channels):
            if not self.voxel_storage.has_data(ch_id):
                continue

            volume = self.voxel_storage.get_display_volume_transformed(
                ch_id, stage_pos, holder_pos_voxels
            )

            if ch_id in self.channel_layers:
                self.channel_layers[ch_id].data = volume

                self.logger.debug(f"Stage update: Channel {ch_id} - "
                                 f"non-zero voxels: {np.count_nonzero(volume)}")

    def _setup_data_layers(self) -> None:
        """Setup napari layers for multi-channel data."""
        if not self.viewer or not self.voxel_storage:
            return

        channels_config = self._config.get('channels', [
            {'id': 0, 'name': '405nm (DAPI)', 'default_colormap': 'cyan', 'default_visible': True},
            {'id': 1, 'name': '488nm (GFP)', 'default_colormap': 'green', 'default_visible': True},
            {'id': 2, 'name': '561nm (RFP)', 'default_colormap': 'red', 'default_visible': True},
            {'id': 3, 'name': '640nm (Far-Red)', 'default_colormap': 'magenta', 'default_visible': False}
        ])

        for ch_config in channels_config:
            ch_id = ch_config['id']
            ch_name = ch_config['name']

            # Create empty volume
            empty_volume = np.zeros(self.voxel_storage.display_dims, dtype=np.uint16)

            # Add layer
            layer = self.viewer.add_image(
                empty_volume,
                name=ch_name,
                colormap=ch_config.get('default_colormap', 'gray'),
                visible=ch_config.get('default_visible', True),
                blending='additive',
                opacity=0.8,
                rendering='mip',
                contrast_limits=(0, 500)  # Match UI slider default
            )

            self.channel_layers[ch_id] = layer

        self.logger.info(f"Setup {len(self.channel_layers)} data layers")

    def _update_visualization(self) -> None:
        """Update the 3D visualization with latest data from voxel storage."""
        if not self.viewer or not self.voxel_storage:
            return

        try:
            for ch_id in range(self.voxel_storage.num_channels):
                if ch_id in self.channel_layers:
                    if not self.voxel_storage.has_data(ch_id):
                        continue

                    # Use transformed volume if stage has moved from origin
                    if self.last_stage_position and any(
                        v != 0 for v in self.last_stage_position.values()
                    ):
                        holder_pos = np.array([
                            self.holder_position['x'],
                            self.holder_position['y'],
                            self.holder_position['z']
                        ])
                        volume = self.voxel_storage.get_display_volume_transformed(
                            ch_id, self.last_stage_position, holder_pos
                        )
                    else:
                        volume = self.voxel_storage.get_display_volume(ch_id)

                    # Diagnostic logging to help debug data display issues
                    self.logger.info(
                        f"Channel {ch_id}: volume shape={volume.shape}, "
                        f"non-zero={np.count_nonzero(volume)}, "
                        f"max={volume.max()}"
                    )

                    self.channel_layers[ch_id].data = volume

                    # Auto-contrast if this is first data for channel
                    layer = self.channel_layers[ch_id]
                    if not getattr(layer, '_auto_contrast_applied', False):
                        self._auto_contrast_channels()
                        layer._auto_contrast_applied = True

        except Exception as e:
            self.logger.error(f"Error updating visualization: {e}", exc_info=True)

    def _reset_viewer_camera(self) -> None:
        """Reset the napari viewer camera zoom (preserves orientation from 3D window)."""
        viewer = self._get_viewer()
        if viewer and hasattr(viewer, 'camera'):
            # Only set zoom - don't override camera.angles as 3D window has correct orientation
            viewer.camera.zoom = 1.57
            self.logger.info("Reset viewer camera zoom to 1.57")

    # ========== Live View Control ==========

    def _on_live_view_toggle(self) -> None:
        """Toggle live view on/off."""
        if not self.camera_controller:
            self.logger.warning("No camera controller available")
            return

        try:
            if self._live_view_active:
                # Stop live view
                self.camera_controller.stop_live_view()
                self._live_view_active = False
                self.live_view_toggle_btn.setChecked(False)
                self.live_view_toggle_btn.setText("Start Live")
                self.live_view_toggle_btn.setStyleSheet(
                    f"QPushButton {{ background-color: {SUCCESS_COLOR}; color: white; "
                    f"font-weight: bold; padding: 6px 12px; font-size: 10pt; }}"
                )
                self.live_status_label.setText("Status: Idle")
                self.logger.info("Live view stopped")
            else:
                # Re-enable the selected light source before starting camera
                # (it was disabled when live view stopped previously)
                if hasattr(self, 'laser_led_panel') and self.laser_led_panel:
                    self.laser_led_panel.restore_checked_illumination()

                # Start live view
                self.camera_controller.start_live_view()
                self._live_view_active = True
                self.live_view_toggle_btn.setChecked(True)
                self.live_view_toggle_btn.setText("Stop Live")
                self.live_view_toggle_btn.setStyleSheet(
                    f"QPushButton {{ background-color: {ERROR_COLOR}; color: white; "
                    f"font-weight: bold; padding: 6px 12px; font-size: 10pt; }}"
                )
                self.live_status_label.setText("Status: Streaming")
                self.logger.info("Live view started")
        except Exception as e:
            self.logger.error(f"Error toggling live view: {e}")

    def _update_live_view_state(self) -> None:
        """Update the live view button state based on camera controller state."""
        if not self.camera_controller:
            return

        try:
            is_live = self.camera_controller.state == CameraState.LIVE_VIEW
            self._live_view_active = is_live

            if is_live:
                self.live_view_toggle_btn.setChecked(True)
                self.live_view_toggle_btn.setText("Stop Live")
                self.live_view_toggle_btn.setStyleSheet(
                    f"QPushButton {{ background-color: {ERROR_COLOR}; color: white; "
                    f"font-weight: bold; padding: 6px 12px; font-size: 10pt; }}"
                )
                self.live_status_label.setText("Status: Streaming")
            else:
                self.live_view_toggle_btn.setChecked(False)
                self.live_view_toggle_btn.setText("Start Live")
                self.live_view_toggle_btn.setStyleSheet(
                    f"QPushButton {{ background-color: {SUCCESS_COLOR}; color: white; "
                    f"font-weight: bold; padding: 6px 12px; font-size: 10pt; }}"
                )
                self.live_status_label.setText("Status: Idle")
        except Exception as e:
            self.logger.error(f"Error updating live view state: {e}")

    def _update_zoom_display(self) -> None:
        """Update the zoom level display from napari viewer."""
        viewer = self._get_viewer()
        if viewer and hasattr(viewer, 'camera'):
            zoom = viewer.camera.zoom
            self.zoom_label.setText(f"Zoom: {zoom:.2f}")
        else:
            self.zoom_label.setText("Zoom: --")

    def _on_reset_zoom_clicked(self) -> None:
        """Reset camera view to defaults (orientation and zoom)."""
        viewer = self._get_viewer()
        if viewer and hasattr(viewer, 'camera'):
            viewer.reset_view()  # Reset orientation to napari defaults
            viewer.camera.zoom = 1.57  # Set zoom after reset
            self._update_zoom_display()
            self.logger.info("Reset camera view to defaults (orientation + zoom=1.57)")

    def _update_info_displays(self) -> None:
        """Periodically update zoom, FPS, and data stats displays."""
        # Update zoom
        self._update_zoom_display()

        # Update data stats (memory/voxels)
        self._update_data_stats()

        # Update FPS from camera controller if live
        if self._live_view_active and self.camera_controller:
            fps = getattr(self.camera_controller, '_current_fps', None)
            if fps is not None:
                self.fps_label.setText(f"FPS: {fps:.1f}")
            else:
                self.fps_label.setText("FPS: --")
        elif not self._live_view_active:
            self.fps_label.setText("FPS: --")

    def _update_data_stats(self) -> None:
        """Update memory and voxel count labels from voxel storage."""
        if not self.voxel_storage:
            return

        try:
            stats = self.voxel_storage.get_memory_usage()
            self.memory_label.setText(f"Memory: {stats['total_mb']:.1f} MB")
            voxels = stats['storage_voxels']
            if voxels >= 1_000_000:
                self.voxel_label.setText(f"Voxels: {voxels/1_000_000:.1f}M")
            elif voxels >= 1_000:
                self.voxel_label.setText(f"Voxels: {voxels/1_000:.1f}K")
            else:
                self.voxel_label.setText(f"Voxels: {voxels:,}")
        except Exception as e:
            self.logger.debug(f"Error updating data stats: {e}")

    def _on_transform_quality_changed(self, fast_mode: bool) -> None:
        """Handle Fast Transform checkbox toggle."""
        try:
            from py2flamingo.visualization.coordinate_transforms import TransformQuality
            quality = TransformQuality.FAST if fast_mode else TransformQuality.QUALITY
            if self.voxel_storage:
                self.voxel_storage.transform_quality = quality
                # Trigger visualization update
                self._update_visualization()
            self.logger.info(f"Transform quality changed to: {quality.name}")
        except Exception as e:
            self.logger.error(f"Error changing transform quality: {e}")

    def _on_live_settings_clicked(self) -> None:
        """Open Live Display (image controls) window for advanced settings."""
        if self.image_controls_window:
            self.image_controls_window.show()
            self.image_controls_window.raise_()
        else:
            self.logger.info("Live View Settings clicked (window not available)")

    def _on_plane_click(self, plane: str, h_coord: float, v_coord: float) -> None:
        """Handle click-to-move from plane viewers."""
        if not self.movement_controller:
            return

        try:
            # Map plane coordinates to axis movements
            if plane == 'xz':
                # XZ plane: h=X, v=Z
                self.movement_controller.move_absolute('x', h_coord, verify=False)
                self.movement_controller.move_absolute('z', v_coord, verify=False)
                self.logger.info(f"Moving to X={h_coord:.3f}, Z={v_coord:.3f}")
            elif plane == 'xy':
                # XY plane: h=X, v=Y
                self.movement_controller.move_absolute('x', h_coord, verify=False)
                self.movement_controller.move_absolute('y', v_coord, verify=False)
                self.logger.info(f"Moving to X={h_coord:.3f}, Y={v_coord:.3f}")
            elif plane == 'yz':
                # YZ plane: h=Z, v=Y
                self.movement_controller.move_absolute('z', h_coord, verify=False)
                self.movement_controller.move_absolute('y', v_coord, verify=False)
                self.logger.info(f"Moving to Z={h_coord:.3f}, Y={v_coord:.3f}")
        except Exception as e:
            self.logger.error(f"Error moving from plane click: {e}")

    def _update_plane_views(self) -> None:
        """Update the MIP (Maximum Intensity Projection) plane views from voxel data.

        Supports multi-channel display with colormaps from Viewer Controls settings.
        """
        if not self.voxel_storage:
            return

        try:
            # Collect MIP data and settings for each channel
            xz_channel_mips: Dict[int, np.ndarray] = {}
            xy_channel_mips: Dict[int, np.ndarray] = {}
            yz_channel_mips: Dict[int, np.ndarray] = {}
            channel_settings: Dict[int, dict] = {}

            # Get channel settings from napari layers (if available)
            viewer = self._get_viewer()

            for ch_id in range(4):
                # Check if channel has data
                if not self.voxel_storage.has_data(ch_id):
                    continue

                # Get channel volume from storage
                volume = self.voxel_storage.get_display_volume(ch_id)
                if volume is None or volume.size == 0:
                    continue

                # Data is in (Z, Y, X) order - generate MIP projections
                # XZ plane (top-down) - project along Y axis (axis 1)
                xz_channel_mips[ch_id] = np.max(volume, axis=1)

                # XY plane (front view) - project along Z axis (axis 0)
                xy_channel_mips[ch_id] = np.max(volume, axis=0)

                # YZ plane (side view) - project along X axis (axis 2)
                yz_channel_mips[ch_id] = np.max(volume, axis=2)

                # Get channel settings from napari layer or use defaults
                settings = {
                    'visible': True,
                    'colormap': 'gray',
                    'contrast_min': 0,
                    'contrast_max': 65535
                }

                # Try to get settings from napari layer
                if ch_id in self.channel_layers:
                    layer = self.channel_layers[ch_id]
                    if hasattr(layer, 'visible'):
                        settings['visible'] = layer.visible
                    if hasattr(layer, 'colormap') and hasattr(layer.colormap, 'name'):
                        settings['colormap'] = layer.colormap.name
                    if hasattr(layer, 'contrast_limits'):
                        limits = layer.contrast_limits
                        settings['contrast_min'] = int(limits[0])
                        settings['contrast_max'] = int(limits[1])

                channel_settings[ch_id] = settings

            # Update plane viewers with multi-channel data
            if xz_channel_mips:
                self.xz_plane_viewer.set_multi_channel_mip(xz_channel_mips, channel_settings)
            if xy_channel_mips:
                self.xy_plane_viewer.set_multi_channel_mip(xy_channel_mips, channel_settings)
            if yz_channel_mips:
                self.yz_plane_viewer.set_multi_channel_mip(yz_channel_mips, channel_settings)

        except Exception as e:
            self.logger.error(f"Error updating plane views: {e}")

    def _update_plane_overlays(self) -> None:
        """Update overlay positions on all plane viewers."""
        if not self.viewer:
            return

        try:
            # Get current stage position from movement controller
            if self.movement_controller:
                pos = self.movement_controller.get_position()
                if pos:
                    x, y, z = pos.x, pos.y, pos.z

                    # Update holder position on each plane
                    self.xz_plane_viewer.set_holder_position(x, z)
                    self.xy_plane_viewer.set_holder_position(x, y)
                    self.yz_plane_viewer.set_holder_position(z, y)

            # Get objective position from config
            stage_config = self._config.get('stage_control', {})
            obj_x = (stage_config.get('x_range_mm', [1, 12.31])[0] +
                     stage_config.get('x_range_mm', [1, 12.31])[1]) / 2
            obj_y = (stage_config.get('y_range_mm', [5, 25])[0] +
                     stage_config.get('y_range_mm', [5, 25])[1]) / 2
            obj_z = stage_config.get('z_range_mm', [12.5, 26])[0]  # Objective at back

            # Update objective position on each plane
            self.xz_plane_viewer.set_objective_position(obj_x, obj_z)
            self.xy_plane_viewer.set_objective_position(obj_x, obj_y)
            self.yz_plane_viewer.set_objective_position(obj_z, obj_y)

        except Exception as e:
            self.logger.error(f"Error updating plane overlays: {e}")

    def _check_and_mark_targets_stale(self, x: float, y: float, z: float) -> None:
        """Check if stage has reached target positions and mark targets as stale.

        Args:
            x: Current X position in mm
            y: Current Y position in mm
            z: Current Z position in mm
        """
        threshold = 0.05  # 50 microns tolerance

        # Check XZ plane target
        target = self.xz_plane_viewer._target_pos
        if target and self.xz_plane_viewer._target_active:
            target_x, target_z = target
            if abs(x - target_x) < threshold and abs(z - target_z) < threshold:
                self.xz_plane_viewer.set_target_stale()

        # Check XY plane target
        target = self.xy_plane_viewer._target_pos
        if target and self.xy_plane_viewer._target_active:
            target_x, target_y = target
            if abs(x - target_x) < threshold and abs(y - target_y) < threshold:
                self.xy_plane_viewer.set_target_stale()

        # Check YZ plane target (note: h=Z, v=Y in YZ plane)
        target = self.yz_plane_viewer._target_pos
        if target and self.yz_plane_viewer._target_active:
            target_z, target_y = target
            if abs(z - target_z) < threshold and abs(y - target_y) < threshold:
                self.yz_plane_viewer.set_target_stale()

    # ========== Public Methods ==========

    def update_workflow_progress(self, status: str, progress: int, time_remaining: str) -> None:
        """
        Update workflow progress display.

        Args:
            status: Status text (e.g., "Running Step 3 of 10")
            progress: Progress percentage (0-100)
            time_remaining: Time remaining string (e.g., "02:30")
        """
        self.workflow_status_label.setText(f"Workflow: {status}")
        self.workflow_progress_bar.setValue(progress)
        self.time_remaining_label.setText(time_remaining)

    # ========== Window Events ==========

    def showEvent(self, event: QShowEvent) -> None:
        """Handle window show event - restore geometry and dialog state on first show."""
        super().showEvent(event)

        # Restore geometry on first show
        if not self._geometry_restored and self._geometry_manager:
            self._geometry_manager.restore_geometry("SampleView", self)
            self._geometry_restored = True
            self.logger.info("Restored SampleView geometry")

        # Restore dialog state on first show
        if not self._dialog_state_restored and self._geometry_manager:
            self._restore_dialog_state()
            self._dialog_state_restored = True

        # Load laser powers from hardware (every time window is shown)
        if self.laser_led_controller:
            self.logger.info("Loading laser powers from hardware...")
            self.laser_led_controller.load_laser_powers_from_hardware()

    def hideEvent(self, event: QHideEvent) -> None:
        """Handle window hide event - save geometry and dialog state when hidden."""
        # Save geometry and dialog state when hiding
        if self._geometry_manager:
            self._geometry_manager.save_geometry("SampleView", self)
            self._save_dialog_state()
            self._geometry_manager.save_all()
            self.logger.debug("Saved SampleView geometry and dialog state on hide")

        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Handle window close event - save geometry and dialog state."""
        # Save geometry and dialog state
        if self._geometry_manager:
            self._geometry_manager.save_geometry("SampleView", self)
            self._save_dialog_state()
            self._geometry_manager.save_all()
            self.logger.info("Saved SampleView geometry and dialog state")

        event.accept()

    # ========== Dialog State Persistence ==========

    def _save_dialog_state(self) -> None:
        """Save dialog state (display settings and illumination selections) for persistence.

        Saves:
        - Display settings: colormap, auto-scale, intensity min/max
        - Illumination selections: laser/LED checkboxes, LED color, LED intensity, light path

        Does NOT save (these are reset or loaded from hardware):
        - Stage positions (loaded from current hardware state)
        - Laser power values (loaded from hardware)
        - "Populate from live" checkbox (always starts unchecked)
        - 3D view camera position (always resets)
        """
        if not self._geometry_manager:
            return

        state = {}

        # Display settings
        state["colormap"] = self.colormap_combo.currentText()
        state["auto_scale"] = self.auto_scale_checkbox.isChecked()
        state["intensity_min"] = self.min_intensity_spinbox.value()
        state["intensity_max"] = self.max_intensity_spinbox.value()

        # Illumination selections from the laser/LED panel
        if hasattr(self, 'laser_led_panel') and self.laser_led_panel:
            state["illumination"] = self.laser_led_panel.get_illumination_selection_state()

        self._geometry_manager.save_dialog_state("SampleView", state)
        self.logger.debug(f"Saved dialog state: colormap={state['colormap']}, "
                         f"auto_scale={state['auto_scale']}, "
                         f"intensity={state['intensity_min']}-{state['intensity_max']}")

    def _restore_dialog_state(self) -> None:
        """Restore dialog state (display settings and illumination selections) from persistence.

        Restores:
        - Display settings: colormap, auto-scale, intensity min/max
        - Illumination selections: laser/LED checkboxes, LED color, LED intensity, light path

        Does NOT restore (intentionally):
        - Stage positions (current hardware state is used)
        - Laser power values (loaded from hardware separately)
        - "Populate from live" checkbox (always starts unchecked)
        - 3D view camera position (always starts in reset position)
        """
        if not self._geometry_manager:
            return

        state = self._geometry_manager.restore_dialog_state("SampleView")
        if not state:
            self.logger.debug("No saved dialog state to restore")
            return

        # Restore display settings (block signals to prevent side effects)
        if "colormap" in state:
            self.colormap_combo.blockSignals(True)
            self.colormap_combo.setCurrentText(state["colormap"])
            self._colormap = state["colormap"]
            self.colormap_combo.blockSignals(False)

        if "auto_scale" in state:
            self.auto_scale_checkbox.blockSignals(True)
            self.auto_scale_checkbox.setChecked(state["auto_scale"])
            self._auto_scale = state["auto_scale"]
            self.auto_scale_checkbox.blockSignals(False)

            # Enable/disable intensity controls based on auto-scale
            manual_enabled = not state["auto_scale"]
            self.min_intensity_spinbox.setEnabled(manual_enabled)
            self.max_intensity_spinbox.setEnabled(manual_enabled)
            self.range_slider.setEnabled(manual_enabled)

        if "intensity_min" in state and "intensity_max" in state:
            self.min_intensity_spinbox.blockSignals(True)
            self.max_intensity_spinbox.blockSignals(True)
            self.range_slider.blockSignals(True)

            self._intensity_min = state["intensity_min"]
            self._intensity_max = state["intensity_max"]
            self.min_intensity_spinbox.setValue(state["intensity_min"])
            self.max_intensity_spinbox.setValue(state["intensity_max"])
            self.range_slider.setValue((state["intensity_min"], state["intensity_max"]))

            self.min_intensity_spinbox.blockSignals(False)
            self.max_intensity_spinbox.blockSignals(False)
            self.range_slider.blockSignals(False)

        # Restore illumination selections
        if "illumination" in state and hasattr(self, 'laser_led_panel') and self.laser_led_panel:
            self.laser_led_panel.restore_illumination_selection_state(state["illumination"])

        self.logger.info(f"Restored dialog state: colormap={state.get('colormap')}, "
                        f"auto_scale={state.get('auto_scale')}, "
                        f"intensity={state.get('intensity_min')}-{state.get('intensity_max')}")

    # ========== Acquisition Lock Controls ==========

    def set_stage_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable stage movement controls.

        Called during acquisition processes (e.g., LED 2D Overview scan) to prevent
        accidental stage movements that could interfere with the acquisition.

        This only affects stage position controls - visualization and display
        controls remain enabled.

        Args:
            enabled: True to enable controls, False to disable
        """
        self.logger.info(f"Stage controls {'enabled' if enabled else 'disabled'} (acquisition lock)")

        # Disable/enable position sliders
        if hasattr(self, 'position_sliders'):
            for slider in self.position_sliders.values():
                slider.setEnabled(enabled)

        # Disable/enable position edit fields
        if hasattr(self, 'position_edits'):
            for edit in self.position_edits.values():
                edit.setEnabled(enabled)

        # Disable/enable illumination controls during acquisition
        # (acquisition controls the LED, user shouldn't change it)
        if hasattr(self, 'laser_led_panel') and self.laser_led_panel:
            self.laser_led_panel.setEnabled(enabled)

    # ========== Tile Workflow Integration ==========

    def prepare_for_tile_workflows(self, tile_info: list):
        """Prepare Sample View to receive tile workflow Z-stacks.

        Args:
            tile_info: List of dicts with tile positions and Z-ranges
                      Each dict has keys: x, y, z_min, z_max, filename
        """
        self._tile_workflow_active = True
        self._expected_tiles = tile_info
        self._accumulated_zstacks = {}
        self._tile_reference_set = False  # Set reference on first tile frame
        self._learned_frames_per_tile = None  # Learn from first tile for channel detection

        # Cache camera FPS for channel detection
        self._tile_camera_fps = 40.0  # Default
        if self.camera_controller and self.camera_controller.camera_service:
            try:
                fps = getattr(self.camera_controller, '_max_display_fps', 40.0)
                if fps and fps > 0:
                    self._tile_camera_fps = fps
                self.logger.info(f"Sample View: Camera FPS for channel detection: {self._tile_camera_fps}")
            except Exception:
                pass

        self.logger.info(f"Sample View: Prepared to receive {len(tile_info)} tile workflows")

    def _on_tile_zstack_frame(self, image: np.ndarray, position: dict,
                              z_index: int, frame_num: int):
        """Handle incoming Z-stack frame from tile workflow.

        Args:
            image: Frame data (H, W) uint16 array
            position: Tile position dict with x, y, z_min, z_max, filename
            z_index: Z-plane index (0-based)
            frame_num: Global frame number
        """
        if not self._tile_workflow_active:
            return

        # Calculate actual Z position from index
        z_min = position['z_min']
        z_max = position['z_max']
        z_range = z_max - z_min

        # Determine laser channel from z_index and channel list
        channels = position.get('channels', [0])
        num_channels = len(channels)

        # Track frames per tile to determine channel boundaries dynamically.
        # The firmware acquires channels sequentially, so we need to detect
        # when we've passed the midpoint of the total frames.
        tile_key = (position['x'], position['y'])
        is_new_tile = tile_key not in self._accumulated_zstacks
        if is_new_tile:
            self._accumulated_zstacks[tile_key] = 0

            # CRITICAL: Force position update at start of each new tile
            # This ensures the display transform is current before new data arrives,
            # so existing data shifts correctly and new data is stored with proper deltas.
            if self.movement_controller:
                try:
                    pos = self.movement_controller.get_position()
                    if pos:
                        self.logger.info(f"New tile starting - forcing position update: "
                                        f"X={pos.x:.3f}, Y={pos.y:.3f}, Z={pos.z:.3f}")
                        self._on_position_changed(pos.x, pos.y, pos.z, pos.r)
                except Exception as e:
                    self.logger.warning(f"Could not force position update for new tile: {e}")

        frame_count = self._accumulated_zstacks[tile_key]

        # Use the learned frame count from the first completed tile, or a default
        # Typical tile has 30-50 frames, so default to 40 per channel as a fallback
        frames_per_tile = getattr(self, '_learned_frames_per_tile', None)
        if frames_per_tile is None:
            # First tile: use a conservative default, will be updated after first tile
            frames_per_channel = 20  # Conservative default
        else:
            frames_per_channel = max(1, frames_per_tile // max(1, num_channels))

        # Which channel does this z_index belong to?
        # Channels are acquired sequentially, so divide frame count by frames_per_channel
        channel_idx = min(z_index // frames_per_channel, num_channels - 1)
        self._current_channel = channels[channel_idx]

        # For tile workflows, ALWAYS calculate Z from z_index
        # Hardware position doesn't update mid-Z-sweep, so querying returns
        # the same Z for all frames. Use the calculated position instead.
        z_within_channel = z_index % max(1, frames_per_channel)
        z_fraction = z_within_channel / max(1, frames_per_channel - 1) if frames_per_channel > 1 else 0.5
        z_position = z_min + z_fraction * z_range

        # Increment frame count (tracking was initialized above for channel detection)
        self._accumulated_zstacks[tile_key] += 1
        frame_count = self._accumulated_zstacks[tile_key]

        # Learn the actual frames per tile from the first tile when it completes
        # This improves channel routing for subsequent tiles
        if len(self._accumulated_zstacks) == 1 and frame_count > 5:
            # Update estimate as we go - will settle on final value
            self._learned_frames_per_tile = frame_count

        # Update workflow progress directly (PyQt signals are starved during frame processing)
        total_expected = num_channels * frames_per_channel
        total_tiles = max(1, len(self._expected_tiles))
        tile_idx = len(self._accumulated_zstacks)  # Current tile number
        if total_expected > 0 and frame_count % 5 == 0:
            tile_pct = min(1.0, frame_count / total_expected)
            overall_pct = min(100, int(((tile_idx - 1 + tile_pct) / total_tiles) * 100))
            ch_name = channels[channel_idx] if channel_idx < len(channels) else '?'
            status = f"Tile {tile_idx}/{total_tiles}: {frame_count} frames (Ch {ch_name})"
            self.update_workflow_progress(status, overall_pct, "--:--")

        # Set reference on first frame of acquisition
        # Query actual stage position synchronously to avoid timing issues
        if not self._tile_reference_set and self.voxel_storage:
            # Query actual position from hardware (synchronous call)
            actual_pos = None
            if self.movement_controller:
                try:
                    actual_pos = self.movement_controller.get_position()
                    if actual_pos is None:
                        self.logger.warning("Sample View: movement_controller.get_position() returned None")
                except Exception as e:
                    self.logger.warning(f"Sample View: Failed to query stage position: {e}")
            else:
                self.logger.warning("Sample View: No movement_controller available for position query")

            # Log position comparison for debugging
            self.logger.info(f"Sample View: Position comparison on first frame:")
            self.logger.info(f"  Workflow target: X={position['x']:.3f}, Y={position['y']:.3f}, Z={z_position:.3f}")
            if actual_pos:
                self.logger.info(f"  Queried actual:  X={actual_pos.x:.3f}, Y={actual_pos.y:.3f}, "
                                f"Z={actual_pos.z:.3f}, R={actual_pos.r:.1f}°")
            else:
                self.logger.info(f"  Cached stage:    X={self.last_stage_position.get('x', 0):.3f}, "
                                f"Y={self.last_stage_position.get('y', 0):.3f}, "
                                f"Z={self.last_stage_position.get('z', 0):.3f}, "
                                f"R={self.last_stage_position.get('r', 0):.1f}°")

            if actual_pos:
                # Use queried actual stage position (most accurate, avoids timing issues)
                ref_x = actual_pos.x
                ref_y = actual_pos.y
                ref_z = actual_pos.z
                ref_r = actual_pos.r
                self.logger.info(f"Sample View: Using QUERIED stage position for reference")
            else:
                # Fall back to cached position or workflow
                last_pos_updated = (
                    self.last_stage_position.get('x', 0) != 0 or
                    self.last_stage_position.get('y', 0) != 0 or
                    self.last_stage_position.get('z', 0) != 0
                )
                if last_pos_updated:
                    ref_x = self.last_stage_position['x']
                    ref_y = self.last_stage_position['y']
                    ref_z = self.last_stage_position['z']
                    ref_r = self.last_stage_position.get('r', 0)
                    self.logger.info(f"Sample View: Using CACHED stage position for reference")
                else:
                    ref_x = position['x']
                    ref_y = position['y']
                    ref_z = z_position
                    # Get rotation from workflow position dict (parsed from filename like R90_X...)
                    ref_r = position.get('r', self.last_stage_position.get('r', 0))
                    self.logger.warning(f"Sample View: Using WORKFLOW position for reference "
                                       f"(stage position not available - potential for jump!)")

            self.voxel_storage.set_reference_position({
                'x': ref_x,
                'y': ref_y,
                'z': ref_z,
                'r': ref_r
            })
            self._tile_reference_set = True
            self.logger.info(f"Sample View: Tile reference set to "
                            f"X={ref_x:.3f}, Y={ref_y:.3f}, Z={ref_z:.3f}, R={ref_r:.1f}°")

        # Add frame to volume
        # use_stage_y_delta=True because tile workflow places tiles at different Y positions
        self.add_frame_to_volume(
            image=image,
            stage_position_mm={'x': position['x'], 'y': position['y'], 'z': z_position},
            channel_id=self._current_channel,
            use_stage_y_delta=True
        )

        # Kick the debounced channel-availability check so checkboxes
        # get enabled once storage reports has_data()==True.
        # The timer is single-shot, so repeated .start() calls just reset it.
        self._channel_availability_timer.start()
        self._visualization_update_timer.start()

        self.logger.debug(f"Sample View: Accumulated Z-plane {z_index} for tile "
                         f"({position['x']:.2f}, {position['y']:.2f})")

