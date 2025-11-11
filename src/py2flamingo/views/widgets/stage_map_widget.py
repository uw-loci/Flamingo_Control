"""
Stage Position Map Visualization Widget.

Displays a 2D map showing:
- Stage movement boundaries
- Current position
- Target position
- Movement path/vector
- Real-time updates during motion
"""

import logging
from typing import Optional, Tuple
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt, QPointF, QRectF, pyqtSlot
from PyQt5.QtGui import QPainter, QPen, QBrush, QColor, QFont, QPainterPath

from py2flamingo.models.microscope import Position


class StageMapWidget(QWidget):
    """
    2D visualization of stage position and movement.

    Shows X-Y plane with current position, target position, and movement path.
    """

    def __init__(self, stage_limits: dict, parent=None):
        """
        Initialize stage map widget.

        Args:
            stage_limits: Dict with x/y limits {'x': {'min': ..., 'max': ...}, ...}
            parent: Parent widget
        """
        super().__init__(parent)

        self.logger = logging.getLogger(__name__)

        # Stage limits (in mm)
        self.x_min = stage_limits['x']['min']
        self.x_max = stage_limits['x']['max']
        self.y_min = stage_limits['y']['min']
        self.y_max = stage_limits['y']['max']

        # Current and target positions
        self.current_pos: Optional[Tuple[float, float]] = None  # (x, y)
        self.target_pos: Optional[Tuple[float, float]] = None  # (x, y)
        self.is_moving = False

        # Widget appearance
        self.setMinimumSize(400, 400)
        self.setStyleSheet("background-color: white; border: 2px solid #ddd;")

        # Add title
        layout = QVBoxLayout()
        title = QLabel("Stage Position Map (X-Y Plane)")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-weight: bold; font-size: 10pt;")
        layout.addWidget(title)
        layout.addStretch()
        self.setLayout(layout)

    def set_current_position(self, x: float, y: float) -> None:
        """
        Update current position on map.

        Args:
            x: X position in mm
            y: Y position in mm
        """
        self.current_pos = (x, y)
        self.update()  # Trigger repaint

    def set_target_position(self, x: float, y: float) -> None:
        """
        Set target position for movement visualization.

        Args:
            x: Target X position in mm
            y: Target Y position in mm
        """
        self.target_pos = (x, y)
        self.update()

    def clear_target(self) -> None:
        """Clear target position."""
        self.target_pos = None
        self.update()

    def set_moving(self, moving: bool) -> None:
        """
        Set motion state.

        Args:
            moving: True if stage is currently moving
        """
        self.is_moving = moving
        self.update()

    def _world_to_screen(self, x: float, y: float) -> Tuple[float, float]:
        """
        Convert world coordinates (mm) to screen coordinates (pixels).

        Args:
            x: X position in mm
            y: Y position in mm

        Returns:
            Tuple of (screen_x, screen_y) in pixels
        """
        # Map bounds (with padding)
        padding = 40
        map_width = self.width() - 2 * padding
        map_height = self.height() - 2 * padding - 30  # Account for title

        # Scale factors
        x_range = self.x_max - self.x_min
        y_range = self.y_max - self.y_min

        # Convert to screen coordinates
        # Flip Y axis (screen Y increases downward, stage Y increases upward)
        screen_x = padding + ((x - self.x_min) / x_range) * map_width
        screen_y = padding + 30 + ((self.y_max - y) / y_range) * map_height

        return screen_x, screen_y

    def paintEvent(self, event) -> None:
        """Paint the stage map."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw map bounds
        self._draw_bounds(painter)

        # Draw grid
        self._draw_grid(painter)

        # Draw movement path if target is set
        if self.current_pos and self.target_pos:
            self._draw_movement_path(painter)

        # Draw target position
        if self.target_pos:
            self._draw_target_position(painter)

        # Draw current position
        if self.current_pos:
            self._draw_current_position(painter)

        # Draw legend
        self._draw_legend(painter)

    def _draw_bounds(self, painter: QPainter) -> None:
        """Draw stage movement boundaries."""
        # Get screen coordinates for bounds
        x1, y1 = self._world_to_screen(self.x_min, self.y_max)
        x2, y2 = self._world_to_screen(self.x_max, self.y_min)

        # Draw boundary rectangle
        pen = QPen(QColor("#666"), 2)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor("#f5f5f5")))
        painter.drawRect(QRectF(x1, y1, x2 - x1, y2 - y1))

        # Draw axis labels
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QPen(Qt.black))

        # X-axis label
        painter.drawText(
            int((x1 + x2) / 2) - 40,
            int(y2) + 20,
            f"X: {self.x_min:.1f} - {self.x_max:.1f} mm"
        )

        # Y-axis label
        painter.save()
        painter.translate(int(x1) - 25, int((y1 + y2) / 2))
        painter.rotate(-90)
        painter.drawText(-60, 0, f"Y: {self.y_min:.1f} - {self.y_max:.1f} mm")
        painter.restore()

    def _draw_grid(self, painter: QPainter) -> None:
        """Draw grid lines."""
        pen = QPen(QColor("#ddd"), 1, Qt.DashLine)
        painter.setPen(pen)

        # Draw vertical grid lines (every 5mm)
        grid_step = 5.0
        x = self.x_min
        while x <= self.x_max:
            x1, y1 = self._world_to_screen(x, self.y_min)
            x2, y2 = self._world_to_screen(x, self.y_max)
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))
            x += grid_step

        # Draw horizontal grid lines
        y = self.y_min
        while y <= self.y_max:
            x1, y1 = self._world_to_screen(self.x_min, y)
            x2, y2 = self._world_to_screen(self.x_max, y)
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))
            y += grid_step

    def _draw_current_position(self, painter: QPainter) -> None:
        """Draw current position marker."""
        x, y = self.current_pos
        sx, sy = self._world_to_screen(x, y)

        # Draw outer circle (pulsing effect if moving)
        if self.is_moving:
            painter.setPen(QPen(QColor("#ff9800"), 2))
            painter.setBrush(QBrush(QColor("#ff9800")))
        else:
            painter.setPen(QPen(QColor("#4caf50"), 2))
            painter.setBrush(QBrush(QColor("#4caf50")))

        painter.drawEllipse(QPointF(sx, sy), 8, 8)

        # Draw center dot
        painter.setBrush(QBrush(Qt.white))
        painter.drawEllipse(QPointF(sx, sy), 3, 3)

        # Draw position label
        font = QFont()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(Qt.black))
        painter.drawText(int(sx) + 12, int(sy) - 8, f"({x:.2f}, {y:.2f})")

    def _draw_target_position(self, painter: QPainter) -> None:
        """Draw target position marker."""
        x, y = self.target_pos
        sx, sy = self._world_to_screen(x, y)

        # Draw crosshair
        pen = QPen(QColor("#2196f3"), 2)
        painter.setPen(pen)

        # Horizontal line
        painter.drawLine(int(sx) - 10, int(sy), int(sx) + 10, int(sy))
        # Vertical line
        painter.drawLine(int(sx), int(sy) - 10, int(sx), int(sy) + 10)

        # Draw circle
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(sx, sy), 12, 12)

        # Draw position label
        font = QFont()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#2196f3")))
        painter.drawText(int(sx) + 12, int(sy) + 20, f"Target: ({x:.2f}, {y:.2f})")

    def _draw_movement_path(self, painter: QPainter) -> None:
        """Draw path from current to target position."""
        if not self.current_pos or not self.target_pos:
            return

        cx, cy = self.current_pos
        tx, ty = self.target_pos

        scx, scy = self._world_to_screen(cx, cy)
        stx, sty = self._world_to_screen(tx, ty)

        # Draw dashed line
        pen = QPen(QColor("#2196f3"), 2, Qt.DashLine)
        painter.setPen(pen)
        painter.drawLine(int(scx), int(scy), int(stx), int(sty))

        # Draw arrow head at target
        self._draw_arrow_head(painter, scx, scy, stx, sty)

    def _draw_arrow_head(self, painter: QPainter, x1: float, y1: float, x2: float, y2: float) -> None:
        """Draw arrow head pointing from (x1, y1) to (x2, y2)."""
        import math

        # Calculate angle
        dx = x2 - x1
        dy = y2 - y1
        angle = math.atan2(dy, dx)

        # Arrow head size
        arrow_length = 12
        arrow_width = 6

        # Arrow head points
        point1 = QPointF(
            x2 - arrow_length * math.cos(angle - math.pi / 6),
            y2 - arrow_length * math.sin(angle - math.pi / 6)
        )
        point2 = QPointF(x2, y2)
        point3 = QPointF(
            x2 - arrow_length * math.cos(angle + math.pi / 6),
            y2 - arrow_length * math.sin(angle + math.pi / 6)
        )

        # Draw filled arrow head
        path = QPainterPath()
        path.moveTo(point1)
        path.lineTo(point2)
        path.lineTo(point3)

        painter.setBrush(QBrush(QColor("#2196f3")))
        painter.setPen(QPen(QColor("#2196f3"), 2))
        painter.drawPath(path)

    def _draw_legend(self, painter: QPainter) -> None:
        """Draw legend showing marker meanings."""
        legend_x = 10
        legend_y = self.height() - 60

        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)

        # Current position
        painter.setBrush(QBrush(QColor("#4caf50")))
        painter.setPen(QPen(QColor("#4caf50"), 2))
        painter.drawEllipse(QPointF(legend_x + 6, legend_y), 6, 6)
        painter.setPen(QPen(Qt.black))
        painter.drawText(legend_x + 20, legend_y + 5, "Current Position")

        # Target position
        legend_y += 20
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#2196f3"), 2))
        painter.drawEllipse(QPointF(legend_x + 6, legend_y), 6, 6)
        painter.drawLine(legend_x, legend_y, legend_x + 12, legend_y)
        painter.setPen(QPen(Qt.black))
        painter.drawText(legend_x + 20, legend_y + 5, "Target Position")

    @pyqtSlot(float, float, float, float)
    def update_position(self, x: float, y: float, z: float, r: float) -> None:
        """
        Update current position from signal.

        Args:
            x, y, z, r: Position coordinates
        """
        self.set_current_position(x, y)
