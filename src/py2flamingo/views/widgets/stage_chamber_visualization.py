"""
Stage Chamber Visualization Widget - Dual XZ/XY Wire-frame Views.

Provides two 2D views of the stage position within the sample chamber:
- XZ View (Top-Down): Shows X and Z axes, sample holder as circle
- XY View (Side View): Shows X and Y axes, sample holder as pole with nub

The visualization shows:
- Chamber boundaries (stage movement limits)
- Sample holder position (pole/nub in XY, circle in XZ)
- Objective position (3 concentric circles in XY view, inside chamber)
- Real-time position updates

Color scheme is designed for red-green colorblind accessibility.
"""

import logging
from typing import Optional
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt, QPointF, QRectF, pyqtSlot, pyqtSignal
from PyQt5.QtGui import QPainter, QPen, QBrush, QColor, QFont, QMouseEvent


class ChamberViewPanel(QWidget):
    """Base class for chamber view panels (XZ and XY)."""

    # Signal emitted when user clicks in the chamber (world coordinates in mm)
    click_position = pyqtSignal(float, float)  # (x, other_axis_value)

    def __init__(self, title: str, x_min: float, x_max: float,
                 other_min: float, other_max: float, other_label: str, parent=None):
        """
        Initialize chamber view panel.

        Args:
            title: Panel title (e.g., "XZ View (Top-Down)")
            x_min: Minimum X position in mm
            x_max: Maximum X position in mm
            other_min: Minimum position for other axis (Z or Y) in mm
            other_max: Maximum position for other axis (Z or Y) in mm
            other_label: Label for other axis ("Z" or "Y")
        """
        super().__init__(parent)

        self.logger = logging.getLogger(__name__)
        self.title = title
        self.other_label = other_label

        # Chamber limits (mm)
        self.x_min = x_min
        self.x_max = x_max
        self.other_min = other_min
        self.other_max = other_max

        # Current position (mm)
        self.x_pos: Optional[float] = None
        self.other_pos: Optional[float] = None  # Z or Y

        # Target position for click-to-move (mm)
        self.target_x: Optional[float] = None
        self.target_other: Optional[float] = None  # Z or Y

        # Widget appearance
        self.setMinimumSize(350, 350)
        self.setStyleSheet("background-color: white; border: 1px solid #ccc;")

    def set_position(self, x: float, other: float) -> None:
        """
        Update position.

        Args:
            x: X position in mm
            other: Z or Y position in mm
        """
        self.x_pos = x
        self.other_pos = other
        self.update()  # Trigger repaint

    def set_target_position(self, x: float, other: float) -> None:
        """
        Set target position for click-to-move visual feedback.

        Args:
            x: Target X position in mm
            other: Target Z or Y position in mm
        """
        self.target_x = x
        self.target_other = other
        self.update()  # Trigger repaint

    def clear_target_position(self) -> None:
        """Clear target position marker."""
        self.target_x = None
        self.target_other = None
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse click - convert to world coordinates and emit signal."""
        if event.button() == Qt.LeftButton:
            # Convert screen coordinates to world coordinates
            x_world, other_world = self._screen_to_world(event.x(), event.y())

            # Check if click is within chamber bounds
            if (self.x_min <= x_world <= self.x_max and
                self.other_min <= other_world <= self.other_max):

                # Set target position for visual feedback
                self.set_target_position(x_world, other_world)

                # Emit signal with world coordinates
                self.click_position.emit(x_world, other_world)
                self.logger.debug(f"Click at: X={x_world:.3f}, {self.other_label}={other_world:.3f}")

    def _screen_to_world(self, screen_x: int, screen_y: int) -> tuple:
        """
        Convert screen coordinates (pixels) to world coordinates (mm).

        Args:
            screen_x: Screen X coordinate in pixels
            screen_y: Screen Y coordinate in pixels

        Returns:
            Tuple of (x_world, other_world) in mm
        """
        # Map bounds with padding (must match _world_to_screen)
        padding = 50
        title_offset = 30
        map_width = self.width() - 2 * padding
        map_height = self.height() - 2 * padding - title_offset

        # Scale factors
        x_range = self.x_max - self.x_min
        other_range = self.other_max - self.other_min

        # Convert from screen to world coordinates
        # X axis: left to right
        x_world = self.x_min + ((screen_x - padding) / map_width) * x_range

        # Other axis: bottom to top (inverted Y screen coordinates)
        other_world = self.other_max - ((screen_y - padding - title_offset) / map_height) * other_range

        return x_world, other_world

    def _world_to_screen(self, x_world: float, other_world: float) -> tuple:
        """
        Convert world coordinates (mm) to screen coordinates (pixels).

        Args:
            x_world: X position in mm
            other_world: Z or Y position in mm

        Returns:
            Tuple of (screen_x, screen_y) in pixels
        """
        # Map bounds with padding
        padding = 50
        title_offset = 30
        map_width = self.width() - 2 * padding
        map_height = self.height() - 2 * padding - title_offset

        # Scale factors
        x_range = self.x_max - self.x_min
        other_range = self.other_max - self.other_min

        # Convert to screen coordinates
        # X axis: left to right
        screen_x = padding + ((x_world - self.x_min) / x_range) * map_width

        # Other axis: bottom to top (inverted Y screen coordinates)
        screen_y = padding + title_offset + ((self.other_max - other_world) / other_range) * map_height

        return screen_x, screen_y

    def _draw_chamber_bounds(self, painter: QPainter) -> None:
        """Draw chamber boundary rectangle."""
        # Get screen coordinates for bounds
        x1, y1 = self._world_to_screen(self.x_min, self.other_max)
        x2, y2 = self._world_to_screen(self.x_max, self.other_min)

        # Draw boundary rectangle
        pen = QPen(QColor("#888"), 2)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor("#f9f9f9")))
        painter.drawRect(QRectF(x1, y1, x2 - x1, y2 - y1))

    def _draw_grid(self, painter: QPainter) -> None:
        """Draw grid lines every 2mm."""
        pen = QPen(QColor("#ddd"), 1, Qt.DotLine)
        painter.setPen(pen)

        # Vertical grid lines (X axis)
        grid_step = 2.0
        x = self.x_min
        while x <= self.x_max:
            x1, y1 = self._world_to_screen(x, self.other_min)
            x2, y2 = self._world_to_screen(x, self.other_max)
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))
            x += grid_step

        # Horizontal grid lines (other axis)
        other = self.other_min
        while other <= self.other_max:
            x1, y1 = self._world_to_screen(self.x_min, other)
            x2, y2 = self._world_to_screen(self.x_max, other)
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))
            other += grid_step

    def _draw_axis_labels(self, painter: QPainter) -> None:
        """Draw axis labels and ranges."""
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        painter.setPen(QPen(Qt.black))

        # Get bounds
        x1, y1 = self._world_to_screen(self.x_min, self.other_max)
        x2, y2 = self._world_to_screen(self.x_max, self.other_min)

        # X-axis label (bottom)
        x_label = f"X: {self.x_min:.1f} - {self.x_max:.1f} mm"
        painter.drawText(int((x1 + x2) / 2) - 60, int(y2) + 30, x_label)

        # Other-axis label (left, rotated)
        painter.save()
        painter.translate(int(x1) - 35, int((y1 + y2) / 2))
        painter.rotate(-90)
        other_label = f"{self.other_label}: {self.other_min:.1f} - {self.other_max:.1f} mm"
        painter.drawText(-60, 0, other_label)
        painter.restore()

    def _draw_title(self, painter: QPainter) -> None:
        """Draw panel title."""
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(Qt.black))
        painter.drawText(10, 20, self.title)


class XZViewPanel(ChamberViewPanel):
    """XZ View Panel - Top-down view showing X and Z axes."""

    def __init__(self, x_min: float, x_max: float, z_min: float, z_max: float, parent=None):
        """Initialize XZ view panel."""
        super().__init__("XZ View (Top-Down)", x_min, x_max, z_min, z_max, "Z", parent)
        self.z_pos = None

    def set_position(self, x: float, z: float) -> None:
        """Update X and Z position."""
        self.z_pos = z
        super().set_position(x, z)

    def paintEvent(self, event) -> None:
        """Paint the XZ view."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw title
        self._draw_title(painter)

        # Draw chamber and grid
        self._draw_chamber_bounds(painter)
        self._draw_grid(painter)
        self._draw_axis_labels(painter)

        # Draw sample holder circle if position is set
        if self.x_pos is not None and self.z_pos is not None:
            self._draw_sample_circle(painter)

        # Draw target position marker if set (click-to-move)
        if self.target_x is not None and self.target_other is not None:
            self._draw_target_marker(painter)

        # Draw position coordinates
        if self.x_pos is not None and self.z_pos is not None:
            self._draw_position_text(painter)

    def _draw_sample_circle(self, painter: QPainter) -> None:
        """Draw sample holder as 0.25mm diameter circle (vibrant blue)."""
        if self.x_pos is None or self.z_pos is None:
            return

        # Calculate screen position
        screen_x, screen_y = self._world_to_screen(self.x_pos, self.z_pos)

        # Calculate circle radius in pixels (0.25mm diameter = 0.125mm radius)
        x_range = self.x_max - self.x_min
        map_width = self.width() - 100  # Account for padding
        scale_factor = map_width / x_range
        radius_px = 0.125 * scale_factor

        # Use vibrant cyan/blue (colorblind-safe)
        color = QColor(0, 150, 200)  # Cyan-blue
        painter.setPen(QPen(color, 2))
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QPointF(screen_x, screen_y), radius_px, radius_px)

        # Draw center point
        painter.setBrush(QBrush(Qt.white))
        painter.drawEllipse(QPointF(screen_x, screen_y), 2, 2)

    def _draw_target_marker(self, painter: QPainter) -> None:
        """Draw target position marker (crosshair) for click-to-move."""
        if self.target_x is None or self.target_other is None:
            return

        # Get screen coordinates for target
        screen_x, screen_y = self._world_to_screen(self.target_x, self.target_other)

        # Draw crosshair in orange/red (highly visible)
        color = QColor(255, 100, 0)  # Orange-red
        pen = QPen(color, 2)
        painter.setPen(pen)

        # Crosshair size
        size = 15

        # Draw horizontal line
        painter.drawLine(int(screen_x - size), int(screen_y),
                        int(screen_x + size), int(screen_y))

        # Draw vertical line
        painter.drawLine(int(screen_x), int(screen_y - size),
                        int(screen_x), int(screen_y + size))

        # Draw circle around crosshair
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(screen_x, screen_y), size, size)

        # Draw "TARGET" label
        font = QFont()
        font.setPointSize(7)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(int(screen_x) + size + 5, int(screen_y) - 5, "TARGET")

    def _draw_position_text(self, painter: QPainter) -> None:
        """Draw current position coordinates."""
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QPen(Qt.black))

        screen_x, screen_y = self._world_to_screen(self.x_pos, self.z_pos)
        text = f"({self.x_pos:.2f}, {self.z_pos:.2f})"
        painter.drawText(int(screen_x) + 15, int(screen_y) - 10, text)


class XYViewPanel(ChamberViewPanel):
    """XY View Panel - Side view showing X and Y axes with pole/nub sample holder."""

    def __init__(self, x_min: float, x_max: float, y_min: float, y_max: float, parent=None):
        """Initialize XY view panel."""
        super().__init__("XY View (Side View)", x_min, x_max, y_min, y_max, "Y", parent)
        self.y_pos = None

        # Objective position (rough estimate - inside chamber)
        self.objective_x = (x_min + x_max) / 2  # Center X
        self.objective_y = y_min + 2.0  # 2mm above bottom of chamber

    def set_position(self, x: float, y: float) -> None:
        """Update X and Y position."""
        self.y_pos = y
        super().set_position(x, y)

    def paintEvent(self, event) -> None:
        """Paint the XY view."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw title
        self._draw_title(painter)

        # Draw chamber and grid
        self._draw_chamber_bounds(painter)
        self._draw_grid(painter)

        # Draw objective circles (FIRST - background layer, inside chamber)
        self._draw_objective_circles(painter)

        # Draw axis labels
        self._draw_axis_labels(painter)

        # Draw sample holder pole and nub if position is set
        if self.x_pos is not None and self.other_pos is not None:
            self._draw_sample_holder(painter)

        # Draw target position marker if set (click-to-move)
        if self.target_x is not None and self.target_other is not None:
            self._draw_target_marker(painter)

        # Draw position coordinates
        if self.x_pos is not None and self.other_pos is not None:
            self._draw_position_text(painter)

        # TODO: Placeholder for future sample object below nub
        # self._draw_sample_object(painter)

    def _draw_objective_circles(self, painter: QPainter) -> None:
        """Draw 3 concentric circles representing objective (faded, inside chamber)."""
        # Semi-transparent light gray
        color = QColor(120, 120, 120, 80)  # RGBA - alpha=80 for fade
        painter.setPen(QPen(color, 1, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)

        # Get screen position for objective
        screen_x, screen_y = self._world_to_screen(self.objective_x, self.objective_y)

        # Calculate scale factor for radius
        x_range = self.x_max - self.x_min
        map_width = self.width() - 100
        scale_factor = map_width / x_range

        # Draw 3 concentric circles
        for radius_mm in [0.5, 1.0, 1.5]:
            radius_px = radius_mm * scale_factor
            painter.drawEllipse(QPointF(screen_x, screen_y), radius_px, radius_px)

    def _draw_sample_holder(self, painter: QPainter) -> None:
        """
        Draw sample holder as pole with nub, dipping down into chamber.

        Only shows portion inside chamber boundaries.
        Position is at tip of nub (bottom of 0.25mm extension).
        """
        if self.x_pos is None or self.other_pos is None:
            return

        # Y position is stored in other_pos (base class variable)
        y_pos = self.other_pos
        y_min = self.other_min
        y_max = self.other_max

        # Calculate scale factor
        x_range = self.x_max - self.x_min
        map_width = self.width() - 100
        scale_factor = map_width / x_range

        # Sample holder dimensions
        column_width_mm = 1.0  # 1mm thick column
        nub_width_mm = 0.25    # 0.25mm thick nub
        nub_length_mm = 1.0    # 1mm extension down

        # Convert to pixels
        column_width_px = column_width_mm * scale_factor
        nub_width_px = nub_width_mm * scale_factor
        nub_length_px = nub_length_mm * scale_factor

        # Position is at TIP of nub (y_pos)
        # Nub extends from y_pos upward to y_pos + nub_length_mm
        # Column extends from top of nub upward to top of chamber

        nub_top_y = y_pos + nub_length_mm
        column_top_y = y_max  # Extends to top of chamber

        # Get screen coordinates
        screen_x_pos, screen_y_tip = self._world_to_screen(self.x_pos, y_pos)
        screen_x_pos, screen_y_nub_top = self._world_to_screen(self.x_pos, nub_top_y)
        screen_x_pos, screen_y_column_top = self._world_to_screen(self.x_pos, column_top_y)

        # Use vibrant cyan-blue (colorblind-safe)
        sample_color = QColor(0, 150, 200)

        # Draw column (1mm wide, from top of chamber to top of nub)
        # Only draw portion inside chamber
        if nub_top_y <= y_max:  # Top of nub is inside chamber
            column_rect = QRectF(
                screen_x_pos - column_width_px / 2,
                screen_y_column_top,
                column_width_px,
                screen_y_nub_top - screen_y_column_top
            )
            painter.setPen(QPen(sample_color, 1))
            painter.setBrush(QBrush(sample_color))
            painter.drawRect(column_rect)

        # Draw nub (0.25mm wide, extends 1mm down from top)
        # Only draw portion inside chamber
        if y_pos >= y_min:  # Nub tip is inside chamber
            nub_rect = QRectF(
                screen_x_pos - nub_width_px / 2,
                screen_y_nub_top,
                nub_width_px,
                screen_y_tip - screen_y_nub_top
            )
            painter.setPen(QPen(sample_color, 1))
            painter.setBrush(QBrush(sample_color))
            painter.drawRect(nub_rect)

        # Draw position marker at nub tip (bright orange for visibility)
        marker_color = QColor(255, 140, 0)  # Orange
        painter.setPen(QPen(marker_color, 2))
        painter.setBrush(QBrush(marker_color))
        painter.drawEllipse(QPointF(screen_x_pos, screen_y_tip), 4, 4)

    def _draw_target_marker(self, painter: QPainter) -> None:
        """Draw target position marker (crosshair) for click-to-move."""
        if self.target_x is None or self.target_other is None:
            return

        # Get screen coordinates for target
        screen_x, screen_y = self._world_to_screen(self.target_x, self.target_other)

        # Draw crosshair in orange/red (highly visible)
        color = QColor(255, 100, 0)  # Orange-red
        pen = QPen(color, 2)
        painter.setPen(pen)

        # Crosshair size
        size = 15

        # Draw horizontal line
        painter.drawLine(int(screen_x - size), int(screen_y),
                        int(screen_x + size), int(screen_y))

        # Draw vertical line
        painter.drawLine(int(screen_x), int(screen_y - size),
                        int(screen_x), int(screen_y + size))

        # Draw circle around crosshair
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(screen_x, screen_y), size, size)

        # Draw "TARGET" label
        font = QFont()
        font.setPointSize(7)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(int(screen_x) + size + 5, int(screen_y) - 5, "TARGET")

    def _draw_position_text(self, painter: QPainter) -> None:
        """Draw current position coordinates."""
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QPen(Qt.black))

        # Y position is stored in other_pos
        y_pos = self.other_pos
        screen_x, screen_y = self._world_to_screen(self.x_pos, y_pos)
        text = f"({self.x_pos:.2f}, {y_pos:.2f})"
        painter.drawText(int(screen_x) + 15, int(screen_y) + 15, text)

    # TODO: Future implementation - sample object below nub
    # def _draw_sample_object(self, painter: QPainter) -> None:
    #     """
    #     Draw sample object below nub (to be implemented).
    #
    #     The sample object will dangle below the nub and is what
    #     actually fluoresces and gets imaged.
    #     """
    #     pass


class StageChamberVisualizationWidget(QWidget):
    """
    Dual-panel visualization of stage chamber with sample holder.

    Displays two side-by-side views:
    - XZ View (left): Top-down view showing X and Z axes
    - XY View (right): Side view showing X and Y axes with objective

    The visualization updates in real-time as the stage moves.
    """

    def __init__(self, parent=None):
        """Initialize stage chamber visualization widget."""
        super().__init__(parent)

        self.logger = logging.getLogger(__name__)

        # Stage limits (corrected values)
        self.x_min = 1.0
        self.x_max = 12.31
        self.z_min = 12.5
        self.z_max = 26.0
        self.y_min = 5.0   # Lowest safe position
        self.y_max = 20.0  # Estimated maximum

        self._setup_ui()

        self.logger.info("StageChamberVisualizationWidget initialized")

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        # Main layout - horizontal dual panel
        layout = QHBoxLayout()
        layout.setSpacing(10)

        # Create XZ view panel (left)
        self.xz_panel = XZViewPanel(
            self.x_min, self.x_max,
            self.z_min, self.z_max
        )
        layout.addWidget(self.xz_panel)

        # Create XY view panel (right)
        self.xy_panel = XYViewPanel(
            self.x_min, self.x_max,
            self.y_min, self.y_max
        )
        layout.addWidget(self.xy_panel)

        self.setLayout(layout)

    @pyqtSlot(float, float, float, float)
    def update_position(self, x: float, y: float, z: float, r: float) -> None:
        """
        Update stage position in both views.

        Args:
            x: X position in mm
            y: Y position in mm
            z: Z position in mm
            r: Rotation in degrees (not used in visualization)
        """
        # Update XZ view (uses X and Z)
        self.xz_panel.set_position(x, z)

        # Update XY view (uses X and Y)
        self.xy_panel.set_position(x, y)

        self.logger.debug(f"Chamber visualization updated: X={x:.2f}, Y={y:.2f}, Z={z:.2f}")
