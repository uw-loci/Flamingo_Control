"""
PortItem — QGraphicsEllipseItem representing an input or output port on a node.

Ports are small colored circles positioned on the left (inputs) or right
(outputs) edge of a NodeItem. Dragging from a port initiates wire creation.
"""

import logging
from PyQt5.QtWidgets import QGraphicsEllipseItem, QGraphicsItem
from PyQt5.QtCore import Qt, QRectF, QPointF
from PyQt5.QtGui import QBrush, QPen, QColor

from py2flamingo.pipeline.models.port_types import PortType, PORT_COLORS, can_connect
from py2flamingo.pipeline.models.pipeline import Port, PortDirection

logger = logging.getLogger(__name__)

PORT_RADIUS = 6


class PortItem(QGraphicsEllipseItem):
    """Visual representation of a pipeline port.

    Attributes:
        port: The data model Port instance
        node_item: Parent NodeItem
    """

    def __init__(self, port: Port, node_item, parent=None):
        diameter = PORT_RADIUS * 2
        super().__init__(-PORT_RADIUS, -PORT_RADIUS, diameter, diameter, parent)

        self.port = port
        self.node_item = node_item
        self._connections = []  # ConnectionItems attached to this port

        # Visual styling
        color = QColor(PORT_COLORS.get(port.port_type, '#ffffff'))
        self.setBrush(QBrush(color))
        self.setPen(QPen(color.darker(140), 1.5))

        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.ItemSendsScenePositionChanges)
        self.setCursor(Qt.CrossCursor)

        # Tooltip
        direction = "Input" if port.direction == PortDirection.INPUT else "Output"
        required = " (required)" if port.required else ""
        self.setToolTip(f"{direction}: {port.name}\nType: {port.port_type.name}{required}")

    @property
    def is_input(self) -> bool:
        return self.port.direction == PortDirection.INPUT

    @property
    def is_output(self) -> bool:
        return self.port.direction == PortDirection.OUTPUT

    def center_scene_pos(self) -> QPointF:
        """Get the center of this port in scene coordinates."""
        return self.mapToScene(QPointF(0, 0))

    def add_connection(self, connection_item):
        """Track a connection attached to this port."""
        if connection_item not in self._connections:
            self._connections.append(connection_item)

    def remove_connection(self, connection_item):
        """Remove a tracked connection."""
        if connection_item in self._connections:
            self._connections.remove(connection_item)

    def update_connections(self):
        """Update all connection curves when this port moves."""
        for conn in self._connections:
            conn.update_path()

    def can_accept(self, source_port: 'PortItem') -> bool:
        """Check if this port can accept a connection from source_port."""
        if self.is_output:
            return False  # Can only connect to inputs
        if source_port.is_input:
            return False  # Source must be an output
        if self.node_item is source_port.node_item:
            return False  # No self-connections
        return can_connect(source_port.port.port_type, self.port.port_type)

    def hoverEnterEvent(self, event):
        self.setBrush(QBrush(QColor(PORT_COLORS.get(self.port.port_type, '#ffffff')).lighter(140)))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.setBrush(QBrush(QColor(PORT_COLORS.get(self.port.port_type, '#ffffff'))))
        super().hoverLeaveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemScenePositionHasChanged:
            self.update_connections()
        return super().itemChange(change, value)
