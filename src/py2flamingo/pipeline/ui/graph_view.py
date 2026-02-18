"""
PipelineGraphView — QGraphicsView with pan, zoom, and port dragging.

Handles:
- Middle-mouse or right-click drag to pan the canvas
- Scroll wheel to zoom (anchored under mouse)
- Left-click drag on output ports to create wires
- Right-click (without drag) for context menu
- Delete key to remove selected items
"""

import logging
from PyQt5.QtWidgets import QGraphicsView, QGraphicsItem, QMenu, QAction
from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QPainter

from py2flamingo.pipeline.models.pipeline import NodeType
from py2flamingo.pipeline.ui.graph_scene import PipelineGraphScene
from py2flamingo.pipeline.ui.port_item import PortItem
from py2flamingo.pipeline.ui.connection_item import ConnectionItem

logger = logging.getLogger(__name__)

ZOOM_FACTOR = 1.15
MIN_ZOOM = 0.2
MAX_ZOOM = 3.0


class PipelineGraphView(QGraphicsView):
    """QGraphicsView for the pipeline graph editor with pan/zoom/port dragging."""

    def __init__(self, scene: PipelineGraphScene, parent=None):
        super().__init__(scene, parent)
        self._scene = scene
        self._panning = False
        self._pan_start = QPointF()
        self._pan_moved = False
        self._dragging_port = False

        # Rendering
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)

        # Appearance
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("border: none;")

    def mousePressEvent(self, event):
        # Middle or Right button -> pan
        if event.button() in (Qt.MiddleButton, Qt.RightButton):
            self._panning = True
            self._pan_start = event.pos()
            self._pan_moved = False
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        # Left button -> check for port drag
        if event.button() == Qt.LeftButton:
            item = self.itemAt(event.pos())
            if isinstance(item, PortItem) and item.is_output:
                self._dragging_port = True
                self._scene.start_port_drag(item)
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            if delta.manhattanLength() > 2:
                self._pan_moved = True
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            event.accept()
            return

        if self._dragging_port:
            scene_pos = self.mapToScene(event.pos())
            self._scene.update_port_drag(scene_pos)
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() in (Qt.MiddleButton, Qt.RightButton) and self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            # Right-click without movement → show context menu
            if event.button() == Qt.RightButton and not self._pan_moved:
                self._show_context_menu(event.pos())
            event.accept()
            return

        if event.button() == Qt.LeftButton and self._dragging_port:
            self._dragging_port = False
            scene_pos = self.mapToScene(event.pos())
            self._scene.finish_port_drag(scene_pos)
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        # Scroll to zoom (no Ctrl required)
        angle = event.angleDelta().y()
        if angle != 0:
            if angle > 0:
                factor = ZOOM_FACTOR
            else:
                factor = 1.0 / ZOOM_FACTOR

            # Clamp zoom
            current_scale = self.transform().m11()
            new_scale = current_scale * factor
            if MIN_ZOOM <= new_scale <= MAX_ZOOM:
                self.scale(factor, factor)

            event.accept()
            return

        super().wheelEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            # Try removing selected connection first, then node
            if not self._scene.remove_selected_connection():
                selected = self._scene.selectedItems()
                from py2flamingo.pipeline.ui.node_item import NodeItem
                for item in selected:
                    if isinstance(item, NodeItem):
                        self._scene.remove_node(item.pipeline_node.id)
                        break
            event.accept()
            return

        if event.key() == Qt.Key_Escape:
            self._scene.cancel_port_drag()
            if self._dragging_port:
                self._dragging_port = False
            event.accept()
            return

        super().keyPressEvent(event)

    def _show_context_menu(self, view_pos):
        """Show a right-click context menu appropriate to what was clicked."""
        scene_pos = self.mapToScene(view_pos)
        item = self.itemAt(view_pos)

        menu = QMenu(self)

        # Check what was clicked
        from py2flamingo.pipeline.ui.node_item import NodeItem

        if isinstance(item, NodeItem):
            # Right-click on a node
            delete_action = menu.addAction("Delete Node")
            action = menu.exec_(self.mapToGlobal(view_pos))
            if action == delete_action:
                self._scene.remove_node(item.pipeline_node.id)
            return

        if isinstance(item, ConnectionItem):
            # Right-click on a connection
            delete_action = menu.addAction("Delete Connection")
            action = menu.exec_(self.mapToGlobal(view_pos))
            if action == delete_action:
                self._scene._remove_connection_item(item.connection.id)
            return

        # Check if clicked item's parent is a NodeItem (e.g. port or label)
        if item is not None:
            parent = item.parentItem()
            if isinstance(parent, NodeItem):
                delete_action = menu.addAction("Delete Node")
                action = menu.exec_(self.mapToGlobal(view_pos))
                if action == delete_action:
                    self._scene.remove_node(parent.pipeline_node.id)
                return

        # Right-click on empty canvas
        add_menu = menu.addMenu("Add Node")
        node_type_actions = {}
        for node_type in NodeType:
            display_name = node_type.name.replace('_', ' ').title()
            action = add_menu.addAction(display_name)
            node_type_actions[action] = node_type

        menu.addSeparator()
        fit_action = menu.addAction("Fit to Content")
        reset_action = menu.addAction("Reset Zoom")

        action = menu.exec_(self.mapToGlobal(view_pos))
        if action is None:
            return

        if action in node_type_actions:
            self._scene.add_node(node_type_actions[action], scene_pos.x(), scene_pos.y())
        elif action == fit_action:
            self.fit_to_content()
        elif action == reset_action:
            self.resetTransform()

    def fit_to_content(self):
        """Zoom to fit all items in view."""
        rect = self._scene.itemsBoundingRect()
        if not rect.isEmpty():
            rect.adjust(-50, -50, 50, 50)
            self.fitInView(rect, Qt.KeepAspectRatio)
