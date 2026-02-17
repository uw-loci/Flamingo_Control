"""
ConnectionItem — QGraphicsPathItem for bezier wire between two ports.

Draws a cubic bezier curve from source port to target port, colored
by the source port's type.
"""

import logging
from PyQt5.QtWidgets import QGraphicsPathItem
from PyQt5.QtCore import QPointF
from PyQt5.QtGui import QPen, QColor, QPainterPath

from py2flamingo.pipeline.models.port_types import PORT_COLORS
from py2flamingo.pipeline.models.pipeline import Connection

logger = logging.getLogger(__name__)

WIRE_WIDTH = 2.5


class ConnectionItem(QGraphicsPathItem):
    """Visual bezier wire between two port items.

    Attributes:
        connection: The data model Connection instance
        source_port_item: PortItem at the start of the wire
        target_port_item: PortItem at the end of the wire
    """

    def __init__(self, connection: Connection, source_port_item, target_port_item,
                 parent=None):
        super().__init__(parent)
        self.connection = connection
        self.source_port_item = source_port_item
        self.target_port_item = target_port_item

        # Color matches source port type
        color = QColor(PORT_COLORS.get(source_port_item.port.port_type, '#ffffff'))
        self.setPen(QPen(color, WIRE_WIDTH))
        self.setZValue(-1)  # Behind nodes

        # Register with both ports
        source_port_item.add_connection(self)
        target_port_item.add_connection(self)

        self.update_path()

    def update_path(self):
        """Recalculate the bezier path from source to target port positions."""
        start = self.source_port_item.center_scene_pos()
        end = self.target_port_item.center_scene_pos()

        path = QPainterPath(start)

        # Horizontal distance for control points
        dx = abs(end.x() - start.x()) * 0.5
        dx = max(dx, 50)  # minimum curvature

        cp1 = QPointF(start.x() + dx, start.y())
        cp2 = QPointF(end.x() - dx, end.y())
        path.cubicTo(cp1, cp2, end)

        self.setPath(path)

    def detach(self):
        """Remove this connection from both port items."""
        self.source_port_item.remove_connection(self)
        self.target_port_item.remove_connection(self)


class DragWireItem(QGraphicsPathItem):
    """Temporary wire drawn while user drags from a port to create a connection."""

    def __init__(self, source_port_item, parent=None):
        super().__init__(parent)
        self.source_port_item = source_port_item
        self._end_pos = source_port_item.center_scene_pos()

        color = QColor(PORT_COLORS.get(source_port_item.port.port_type, '#ffffff'))
        pen = QPen(color, WIRE_WIDTH)
        pen.setStyle(2)  # Dash line
        self.setPen(pen)
        self.setZValue(10)  # Above everything

    def update_end(self, scene_pos: QPointF):
        """Update the endpoint of the drag wire."""
        self._end_pos = scene_pos
        self._rebuild_path()

    def set_valid(self, valid: bool):
        """Change wire color to indicate whether the hover target is valid."""
        base_color = QColor(PORT_COLORS.get(self.source_port_item.port.port_type, '#ffffff'))
        if valid:
            color = QColor('#66bb6a')  # Green
        else:
            color = base_color
        pen = self.pen()
        pen.setColor(color)
        self.setPen(pen)

    def _rebuild_path(self):
        start = self.source_port_item.center_scene_pos()
        end = self._end_pos

        path = QPainterPath(start)
        dx = abs(end.x() - start.x()) * 0.5
        dx = max(dx, 50)

        cp1 = QPointF(start.x() + dx, start.y())
        cp2 = QPointF(end.x() - dx, end.y())
        path.cubicTo(cp1, cp2, end)

        self.setPath(path)
