"""
NodeItem — QGraphicsItem for a pipeline node in the graph editor.

Renders a rounded rectangle with a colored header (by node type),
node name, input ports on the left, output ports on the right,
and a status indicator dot.
"""

import logging
from PyQt5.QtWidgets import (
    QGraphicsItem, QGraphicsRectItem, QGraphicsTextItem,
    QGraphicsEllipseItem, QStyleOptionGraphicsItem, QWidget
)
from PyQt5.QtCore import Qt, QRectF, QPointF
from PyQt5.QtGui import (
    QBrush, QPen, QColor, QPainter, QFont, QFontMetrics,
    QPainterPath
)

from py2flamingo.pipeline.models.pipeline import PipelineNode, NodeType, NODE_COLORS, PortDirection
from py2flamingo.pipeline.ui.port_item import PortItem, PORT_RADIUS

logger = logging.getLogger(__name__)

NODE_WIDTH = 180
HEADER_HEIGHT = 28
PORT_SPACING = 24
PORT_MARGIN = 12
BODY_PADDING = 8
CORNER_RADIUS = 8


class NodeItem(QGraphicsItem):
    """Visual representation of a pipeline node.

    Attributes:
        pipeline_node: The data model PipelineNode
        input_port_items: List of PortItem for inputs
        output_port_items: List of PortItem for outputs
    """

    def __init__(self, pipeline_node: PipelineNode, parent=None):
        super().__init__(parent)
        self.pipeline_node = pipeline_node
        self.input_port_items: list[PortItem] = []
        self.output_port_items: list[PortItem] = []

        # State
        self._selected = False
        self._status = 'idle'  # idle, running, completed, error

        # Make movable and selectable
        self.setFlag(QGraphicsItem.ItemIsMovable)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)

        # Position from model
        self.setPos(pipeline_node.x, pipeline_node.y)

        # Calculate height
        max_ports = max(len(pipeline_node.inputs), len(pipeline_node.outputs), 1)
        self._body_height = max_ports * PORT_SPACING + BODY_PADDING * 2
        self._total_height = HEADER_HEIGHT + self._body_height

        # Colors
        self._header_color = QColor(NODE_COLORS.get(pipeline_node.node_type, '#607d8b'))
        self._body_color = QColor('#2d2d2d')
        self._border_color = QColor('#555555')

        # Create port items
        self._create_ports()

    def _create_ports(self):
        """Create PortItem children for all input and output ports."""
        # Inputs on left edge
        for i, port in enumerate(self.pipeline_node.inputs):
            port_item = PortItem(port, self, parent=self)
            y = HEADER_HEIGHT + BODY_PADDING + PORT_RADIUS + i * PORT_SPACING
            port_item.setPos(0, y)
            self.input_port_items.append(port_item)

        # Outputs on right edge
        for i, port in enumerate(self.pipeline_node.outputs):
            port_item = PortItem(port, self, parent=self)
            y = HEADER_HEIGHT + BODY_PADDING + PORT_RADIUS + i * PORT_SPACING
            port_item.setPos(NODE_WIDTH, y)
            self.output_port_items.append(port_item)

    def get_port_item(self, port_id: str) -> PortItem:
        """Find a PortItem by port ID."""
        for pi in self.input_port_items + self.output_port_items:
            if pi.port.id == port_id:
                return pi
        return None

    def set_status(self, status: str):
        """Set execution status: 'idle', 'running', 'completed', 'error'."""
        self._status = status
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(-2, -2, NODE_WIDTH + 4, self._total_height + 4)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem,
              widget: QWidget = None):
        # Body
        body_rect = QRectF(0, 0, NODE_WIDTH, self._total_height)
        path = QPainterPath()
        path.addRoundedRect(body_rect, CORNER_RADIUS, CORNER_RADIUS)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(self._body_color))
        painter.drawPath(path)

        # Header
        header_path = QPainterPath()
        header_rect = QRectF(0, 0, NODE_WIDTH, HEADER_HEIGHT)
        header_path.addRoundedRect(header_rect, CORNER_RADIUS, CORNER_RADIUS)
        # Clip bottom corners of header
        header_path.addRect(QRectF(0, HEADER_HEIGHT - CORNER_RADIUS,
                                   NODE_WIDTH, CORNER_RADIUS))

        painter.setBrush(QBrush(self._header_color))
        painter.drawPath(header_path.simplified())

        # Border (with selection highlight)
        border_pen = QPen(
            QColor('#4fc3f7') if self.isSelected() else self._border_color,
            2.0 if self.isSelected() else 1.0
        )
        painter.setPen(border_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(body_rect, CORNER_RADIUS, CORNER_RADIUS)

        # Node name
        painter.setPen(QPen(QColor('#ffffff')))
        font = QFont('Sans', 9, QFont.Bold)
        painter.setFont(font)
        name_rect = QRectF(8, 2, NODE_WIDTH - 30, HEADER_HEIGHT - 4)
        painter.drawText(name_rect, Qt.AlignVCenter | Qt.AlignLeft,
                         self.pipeline_node.name)

        # Status dot
        status_colors = {
            'idle': '#888888',
            'running': '#42a5f5',
            'completed': '#66bb6a',
            'error': '#ef5350',
        }
        dot_color = QColor(status_colors.get(self._status, '#888888'))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(dot_color))
        painter.drawEllipse(QPointF(NODE_WIDTH - 14, HEADER_HEIGHT / 2), 5, 5)

        # Port labels
        label_font = QFont('Sans', 8)
        painter.setFont(label_font)
        painter.setPen(QPen(QColor('#cccccc')))

        for i, port in enumerate(self.pipeline_node.inputs):
            y = HEADER_HEIGHT + BODY_PADDING + PORT_RADIUS + i * PORT_SPACING
            painter.drawText(
                QRectF(PORT_RADIUS + 4, y - 8, NODE_WIDTH / 2 - PORT_RADIUS, 16),
                Qt.AlignVCenter | Qt.AlignLeft,
                port.name
            )

        for i, port in enumerate(self.pipeline_node.outputs):
            y = HEADER_HEIGHT + BODY_PADDING + PORT_RADIUS + i * PORT_SPACING
            painter.drawText(
                QRectF(NODE_WIDTH / 2, y - 8, NODE_WIDTH / 2 - PORT_RADIUS - 4, 16),
                Qt.AlignVCenter | Qt.AlignRight,
                port.name
            )

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            # Update model position
            pos = value
            self.pipeline_node.x = pos.x()
            self.pipeline_node.y = pos.y()
            # Update all port connections
            for pi in self.input_port_items + self.output_port_items:
                pi.update_connections()
        return super().itemChange(change, value)
