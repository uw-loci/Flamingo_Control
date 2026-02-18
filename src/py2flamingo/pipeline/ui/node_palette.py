"""
NodePalette — sidebar with draggable node types for the pipeline editor.

Lists all available node types. Users drag items from the palette onto
the graph canvas to create new nodes.
"""

import logging
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QListWidget, QListWidgetItem, QLabel
)
from PyQt5.QtCore import Qt, QMimeData
from PyQt5.QtGui import QDrag, QColor, QPixmap, QPainter, QFont

from py2flamingo.pipeline.models.pipeline import NodeType, NODE_COLORS

logger = logging.getLogger(__name__)

# Node type descriptions
_NODE_DESCRIPTIONS = {
    NodeType.WORKFLOW: "Execute an acquisition workflow (z-stack, tile scan, etc.)",
    NodeType.THRESHOLD: "Threshold volumes and detect objects",
    NodeType.FOR_EACH: "Iterate over a list of detected objects",
    NodeType.CONDITIONAL: "Branch based on a condition (>, <, ==, etc.)",
    NodeType.EXTERNAL_COMMAND: "Run an external script or command",
    NodeType.SAMPLE_VIEW_DATA: "Read current 3D viewer data (volumes, position)",
}

MIME_TYPE = 'application/x-pipeline-node-type'


class NodePalette(QWidget):
    """Sidebar listing available node types for drag-and-drop."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        header = QLabel("Node Types")
        header.setStyleSheet("font-weight: bold; padding: 4px;")
        layout.addWidget(header)

        self._list = QListWidget()
        self._list.setDragEnabled(True)
        self._list.setDefaultDropAction(Qt.CopyAction)
        self._list.setCursor(Qt.OpenHandCursor)
        self._list.setStyleSheet("""
            QListWidget {
                background: #2d2d2d;
                border: 1px solid #555;
                border-radius: 4px;
            }
            QListWidget::item {
                padding: 8px 6px;
                border-bottom: 1px solid #3d3d3d;
            }
            QListWidget::item:hover {
                background: #3d3d3d;
            }
        """)

        for node_type in NodeType:
            item = QListWidgetItem()
            display_name = node_type.name.replace('_', ' ').title()
            description = _NODE_DESCRIPTIONS.get(node_type, '')
            item.setText(display_name)
            item.setToolTip(f"{description}\n(Drag onto canvas to add)")
            item.setData(Qt.UserRole, node_type.name)

            # Color indicator
            color = QColor(NODE_COLORS.get(node_type, '#607d8b'))
            item.setForeground(color)

            self._list.addItem(item)

        self._list.startDrag = self._start_drag
        layout.addWidget(self._list)

        hint = QLabel("Drag items into the canvas\nto add pipeline nodes")
        hint.setStyleSheet(
            "color: #777; font-size: 10px; padding: 6px 4px;"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch()

    def _start_drag(self, supported_actions):
        """Override to create custom drag with node type data."""
        item = self._list.currentItem()
        if not item:
            return

        node_type_name = item.data(Qt.UserRole)

        drag = QDrag(self._list)
        mime = QMimeData()
        mime.setData(MIME_TYPE, node_type_name.encode('utf-8'))
        drag.setMimeData(mime)

        # Create a small drag pixmap
        pixmap = QPixmap(120, 30)
        pixmap.fill(QColor('#2d2d2d'))
        painter = QPainter(pixmap)
        painter.setPen(QColor('#ffffff'))
        painter.setFont(QFont('Sans', 9))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, item.text())
        painter.end()
        drag.setPixmap(pixmap)

        drag.exec_(Qt.CopyAction)
