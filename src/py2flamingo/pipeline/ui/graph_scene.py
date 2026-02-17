"""
PipelineGraphScene — QGraphicsScene managing node and connection items.

Handles creating/removing visual items when the Pipeline model changes,
and manages the drag-to-connect interaction for creating new wires.
"""

import logging
from typing import Dict, Optional

from PyQt5.QtWidgets import QGraphicsScene
from PyQt5.QtCore import Qt, QPointF, pyqtSignal
from PyQt5.QtGui import QColor

from py2flamingo.pipeline.models.pipeline import Pipeline, PipelineNode, Connection, create_node, NodeType
from py2flamingo.pipeline.ui.node_item import NodeItem
from py2flamingo.pipeline.ui.port_item import PortItem
from py2flamingo.pipeline.ui.connection_item import ConnectionItem, DragWireItem

logger = logging.getLogger(__name__)


class PipelineGraphScene(QGraphicsScene):
    """QGraphicsScene that manages pipeline node and connection visuals.

    Signals:
        node_selected: Emitted when a node is selected (node_id or None)
        connection_created: Emitted when a new connection is made (connection_id)
        connection_removed: Emitted when a connection is deleted (connection_id)
        node_added: Emitted when a node is added to the scene (node_id)
        node_removed: Emitted when a node is removed (node_id)
    """

    node_selected = pyqtSignal(object)  # str or None
    connection_created = pyqtSignal(str)
    connection_removed = pyqtSignal(str)
    node_added = pyqtSignal(str)
    node_removed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackgroundBrush(QColor('#1e1e1e'))

        self._pipeline: Optional[Pipeline] = None
        self._node_items: Dict[str, NodeItem] = {}
        self._connection_items: Dict[str, ConnectionItem] = {}

        # Drag wire state
        self._drag_wire: Optional[DragWireItem] = None
        self._drag_source_port: Optional[PortItem] = None

        self.selectionChanged.connect(self._on_selection_changed)

    @property
    def pipeline(self) -> Optional[Pipeline]:
        return self._pipeline

    def set_pipeline(self, pipeline: Pipeline):
        """Load a pipeline into the scene, creating all visual items."""
        self.clear_all()
        self._pipeline = pipeline

        # Create node items
        for node in pipeline.nodes.values():
            self._add_node_item(node)

        # Create connection items
        for conn in pipeline.connections.values():
            self._add_connection_item(conn)

    def clear_all(self):
        """Remove all items and reset state."""
        self._node_items.clear()
        self._connection_items.clear()
        self._drag_wire = None
        self._drag_source_port = None
        self.clear()

    # ---- Node management ----

    def add_node(self, node_type: NodeType, x: float = 0, y: float = 0,
                 name: str = None) -> str:
        """Create a new node and add it to both model and scene.

        Returns:
            The new node's ID
        """
        if not self._pipeline:
            return None

        node = create_node(node_type, name=name, x=x, y=y)
        self._pipeline.add_node(node)
        self._add_node_item(node)
        self.node_added.emit(node.id)
        return node.id

    def remove_node(self, node_id: str):
        """Remove a node and its connections from model and scene."""
        if not self._pipeline:
            return

        # Remove connection items first
        conns_to_remove = [
            c for c in self._connection_items.values()
            if c.connection.source_node_id == node_id
            or c.connection.target_node_id == node_id
        ]
        for conn_item in conns_to_remove:
            self._remove_connection_item(conn_item.connection.id)

        # Remove node item
        node_item = self._node_items.pop(node_id, None)
        if node_item:
            self.removeItem(node_item)

        self._pipeline.remove_node(node_id)
        self.node_removed.emit(node_id)

    def _add_node_item(self, node: PipelineNode):
        """Create a NodeItem and add it to the scene."""
        item = NodeItem(node)
        self.addItem(item)
        self._node_items[node.id] = item

    # ---- Connection management ----

    def _add_connection_item(self, conn: Connection):
        """Create a ConnectionItem for an existing Connection."""
        src_node_item = self._node_items.get(conn.source_node_id)
        tgt_node_item = self._node_items.get(conn.target_node_id)
        if not src_node_item or not tgt_node_item:
            logger.warning(f"Cannot create wire: missing node item for connection {conn.id}")
            return

        src_port = src_node_item.get_port_item(conn.source_port_id)
        tgt_port = tgt_node_item.get_port_item(conn.target_port_id)
        if not src_port or not tgt_port:
            logger.warning(f"Cannot create wire: missing port item for connection {conn.id}")
            return

        wire = ConnectionItem(conn, src_port, tgt_port)
        self.addItem(wire)
        self._connection_items[conn.id] = wire

    def _remove_connection_item(self, connection_id: str):
        """Remove a connection from both model and scene."""
        wire = self._connection_items.pop(connection_id, None)
        if wire:
            wire.detach()
            self.removeItem(wire)
        if self._pipeline:
            self._pipeline.remove_connection(connection_id)
        self.connection_removed.emit(connection_id)

    def remove_selected_connection(self):
        """Remove the currently selected connection (if any)."""
        for item in self.selectedItems():
            if isinstance(item, ConnectionItem):
                self._remove_connection_item(item.connection.id)
                return True
        return False

    # ---- Drag-to-connect ----

    def start_port_drag(self, port_item: PortItem):
        """Begin dragging a wire from a port."""
        if not port_item.is_output:
            return  # Can only drag from outputs

        self._drag_source_port = port_item
        self._drag_wire = DragWireItem(port_item)
        self.addItem(self._drag_wire)

    def update_port_drag(self, scene_pos: QPointF):
        """Update the drag wire endpoint."""
        if self._drag_wire:
            self._drag_wire.update_end(scene_pos)

            # Check if hovering over a compatible port
            target = self._find_port_at(scene_pos)
            if target and target.can_accept(self._drag_source_port):
                self._drag_wire.set_valid(True)
            else:
                self._drag_wire.set_valid(False)

    def finish_port_drag(self, scene_pos: QPointF):
        """Complete or cancel the drag-to-connect."""
        if not self._drag_wire or not self._drag_source_port:
            return

        # Remove drag wire
        self.removeItem(self._drag_wire)
        self._drag_wire = None

        # Check target
        target = self._find_port_at(scene_pos)
        if target and target.can_accept(self._drag_source_port):
            self._create_connection(self._drag_source_port, target)

        self._drag_source_port = None

    def cancel_port_drag(self):
        """Cancel an in-progress drag."""
        if self._drag_wire:
            self.removeItem(self._drag_wire)
            self._drag_wire = None
        self._drag_source_port = None

    def _find_port_at(self, scene_pos: QPointF) -> Optional[PortItem]:
        """Find a PortItem near the given scene position."""
        items = self.items(scene_pos)
        for item in items:
            if isinstance(item, PortItem):
                return item
        return None

    def _create_connection(self, source_port: PortItem, target_port: PortItem):
        """Create a connection between two ports."""
        if not self._pipeline:
            return

        try:
            conn = self._pipeline.add_connection(
                source_node_id=source_port.node_item.pipeline_node.id,
                source_port_id=source_port.port.id,
                target_node_id=target_port.node_item.pipeline_node.id,
                target_port_id=target_port.port.id,
            )
            self._add_connection_item(conn)
            self.connection_created.emit(conn.id)
        except ValueError as e:
            logger.info(f"Connection rejected: {e}")

    # ---- Selection ----

    def _on_selection_changed(self):
        """Emit node_selected when selection changes."""
        selected = self.selectedItems()
        node_item = None
        for item in selected:
            if isinstance(item, NodeItem):
                node_item = item
                break
        if node_item:
            self.node_selected.emit(node_item.pipeline_node.id)
        else:
            self.node_selected.emit(None)

    # ---- Status updates ----

    def set_node_status(self, node_id: str, status: str):
        """Update the execution status of a node."""
        item = self._node_items.get(node_id)
        if item:
            item.set_status(status)

    def reset_all_status(self):
        """Reset all nodes to idle status."""
        for item in self._node_items.values():
            item.set_status('idle')

    def get_node_item(self, node_id: str) -> Optional[NodeItem]:
        return self._node_items.get(node_id)
