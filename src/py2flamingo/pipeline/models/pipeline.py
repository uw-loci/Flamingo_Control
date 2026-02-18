"""
Pipeline graph model — nodes, ports, connections, and graph operations.

The Pipeline is a directed acyclic graph (DAG) where nodes represent
processing steps and connections carry typed data between ports.
"""

import uuid
import logging
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Set, Tuple
from collections import deque

from py2flamingo.pipeline.models.port_types import PortType, can_connect

logger = logging.getLogger(__name__)


class NodeType(Enum):
    """Built-in pipeline node types."""
    WORKFLOW = auto()
    THRESHOLD = auto()
    FOR_EACH = auto()
    CONDITIONAL = auto()
    EXTERNAL_COMMAND = auto()
    SAMPLE_VIEW_DATA = auto()


class PortDirection(Enum):
    INPUT = auto()
    OUTPUT = auto()


# Node type display colors (hex strings for UI header)
NODE_COLORS: Dict[NodeType, str] = {
    NodeType.WORKFLOW: '#42a5f5',          # Blue
    NodeType.THRESHOLD: '#ff7043',         # Orange
    NodeType.FOR_EACH: '#ab47bc',          # Purple
    NodeType.CONDITIONAL: '#ffee58',       # Yellow
    NodeType.EXTERNAL_COMMAND: '#66bb6a',  # Green
    NodeType.SAMPLE_VIEW_DATA: '#26c6da',  # Teal
}


@dataclass
class Port:
    """A typed input or output port on a pipeline node.

    Attributes:
        id: Unique identifier
        name: Display name (e.g. "volume", "trigger")
        port_type: Data type flowing through this port
        direction: INPUT or OUTPUT
        required: Whether this input must be connected for execution
    """
    id: str
    name: str
    port_type: PortType
    direction: PortDirection
    required: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'port_type': self.port_type.name,
            'direction': self.direction.name,
            'required': self.required,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'Port':
        return cls(
            id=d['id'],
            name=d['name'],
            port_type=PortType[d['port_type']],
            direction=PortDirection[d['direction']],
            required=d.get('required', False),
        )


@dataclass
class Connection:
    """A directed edge between an output port and an input port.

    Attributes:
        id: Unique identifier
        source_node_id: Node owning the output port
        source_port_id: Output port ID
        target_node_id: Node owning the input port
        target_port_id: Input port ID
    """
    id: str
    source_node_id: str
    source_port_id: str
    target_node_id: str
    target_port_id: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'source_node_id': self.source_node_id,
            'source_port_id': self.source_port_id,
            'target_node_id': self.target_node_id,
            'target_port_id': self.target_port_id,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'Connection':
        return cls(
            id=d['id'],
            source_node_id=d['source_node_id'],
            source_port_id=d['source_port_id'],
            target_node_id=d['target_node_id'],
            target_port_id=d['target_port_id'],
        )


def _make_port(name: str, port_type: PortType, direction: PortDirection,
               required: bool = False) -> Port:
    """Helper to create a port with a generated UUID."""
    return Port(
        id=str(uuid.uuid4()),
        name=name,
        port_type=port_type,
        direction=direction,
        required=required,
    )


def create_default_ports(node_type: NodeType) -> Tuple[List[Port], List[Port]]:
    """Create the default input and output ports for a node type.

    Returns:
        (inputs, outputs) lists of Port instances
    """
    inp = PortDirection.INPUT
    out = PortDirection.OUTPUT

    if node_type == NodeType.WORKFLOW:
        inputs = [
            _make_port('trigger', PortType.TRIGGER, inp),
            _make_port('position', PortType.POSITION, inp),
            _make_port('z_range', PortType.OBJECT, inp),
        ]
        outputs = [
            _make_port('volume', PortType.VOLUME, out),
            _make_port('file_path', PortType.FILE_PATH, out),
            _make_port('completed', PortType.TRIGGER, out),
        ]

    elif node_type == NodeType.THRESHOLD:
        inputs = [
            _make_port('volume', PortType.VOLUME, inp),
        ]
        outputs = [
            _make_port('objects', PortType.OBJECT_LIST, out),
            _make_port('mask', PortType.VOLUME, out),
            _make_port('count', PortType.SCALAR, out),
        ]

    elif node_type == NodeType.FOR_EACH:
        inputs = [
            _make_port('collection', PortType.OBJECT_LIST, inp, required=True),
        ]
        outputs = [
            _make_port('current_item', PortType.OBJECT, out),
            _make_port('index', PortType.SCALAR, out),
            _make_port('completed', PortType.TRIGGER, out),
        ]

    elif node_type == NodeType.CONDITIONAL:
        inputs = [
            _make_port('value', PortType.ANY, inp, required=True),
            _make_port('threshold', PortType.SCALAR, inp),
        ]
        outputs = [
            _make_port('true_branch', PortType.TRIGGER, out),
            _make_port('false_branch', PortType.TRIGGER, out),
            _make_port('pass_through', PortType.ANY, out),
        ]

    elif node_type == NodeType.EXTERNAL_COMMAND:
        inputs = [
            _make_port('input_data', PortType.ANY, inp),
            _make_port('trigger', PortType.TRIGGER, inp),
        ]
        outputs = [
            _make_port('output_data', PortType.ANY, out),
            _make_port('file_path', PortType.FILE_PATH, out),
            _make_port('completed', PortType.TRIGGER, out),
        ]

    elif node_type == NodeType.SAMPLE_VIEW_DATA:
        inputs = []
        outputs = [
            _make_port('volume', PortType.VOLUME, out),
            _make_port('position', PortType.POSITION, out),
            _make_port('config', PortType.ANY, out),
        ]

    else:
        inputs, outputs = [], []

    return inputs, outputs


@dataclass
class PipelineNode:
    """A single node in the pipeline graph.

    Attributes:
        id: Unique identifier
        node_type: The kind of processing this node performs
        name: User-visible display name
        inputs: List of input ports
        outputs: List of output ports
        config: Type-specific configuration dict
        x: X position in the editor canvas
        y: Y position in the editor canvas
    """
    id: str
    node_type: NodeType
    name: str
    inputs: List[Port] = field(default_factory=list)
    outputs: List[Port] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0

    def get_port(self, port_id: str) -> Optional[Port]:
        """Find a port by ID across inputs and outputs."""
        for p in self.inputs + self.outputs:
            if p.id == port_id:
                return p
        return None

    def get_input(self, name: str) -> Optional[Port]:
        """Find an input port by name."""
        for p in self.inputs:
            if p.name == name:
                return p
        return None

    def get_output(self, name: str) -> Optional[Port]:
        """Find an output port by name."""
        for p in self.outputs:
            if p.name == name:
                return p
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'node_type': self.node_type.name,
            'name': self.name,
            'inputs': [p.to_dict() for p in self.inputs],
            'outputs': [p.to_dict() for p in self.outputs],
            'config': self.config,
            'x': self.x,
            'y': self.y,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'PipelineNode':
        return cls(
            id=d['id'],
            node_type=NodeType[d['node_type']],
            name=d['name'],
            inputs=[Port.from_dict(p) for p in d.get('inputs', [])],
            outputs=[Port.from_dict(p) for p in d.get('outputs', [])],
            config=d.get('config', {}),
            x=d.get('x', 0.0),
            y=d.get('y', 0.0),
        )


def create_node(node_type: NodeType, name: Optional[str] = None,
                config: Optional[Dict[str, Any]] = None,
                x: float = 0.0, y: float = 0.0) -> PipelineNode:
    """Factory to create a PipelineNode with default ports.

    Args:
        node_type: Kind of node to create
        name: Display name (defaults to node_type.name.title())
        config: Initial configuration dict
        x: Editor X position
        y: Editor Y position
    """
    if name is None:
        name = node_type.name.replace('_', ' ').title()
    inputs, outputs = create_default_ports(node_type)
    return PipelineNode(
        id=str(uuid.uuid4()),
        node_type=node_type,
        name=name,
        inputs=inputs,
        outputs=outputs,
        config=config or {},
        x=x,
        y=y,
    )


class Pipeline:
    """A directed acyclic graph of PipelineNodes connected by typed edges.

    Provides graph operations: topological sort, cycle detection,
    validation, and JSON serialization.
    """

    def __init__(self, name: str = "Untitled Pipeline"):
        self.name: str = name
        self.nodes: Dict[str, PipelineNode] = {}
        self.connections: Dict[str, Connection] = {}

    # ---- Node management ----

    def add_node(self, node: PipelineNode) -> None:
        """Add a node to the pipeline."""
        if node.id in self.nodes:
            raise ValueError(f"Node {node.id} already exists")
        self.nodes[node.id] = node

    def remove_node(self, node_id: str) -> None:
        """Remove a node and all its connections."""
        if node_id not in self.nodes:
            return
        # Remove connections touching this node
        to_remove = [
            c.id for c in self.connections.values()
            if c.source_node_id == node_id or c.target_node_id == node_id
        ]
        for cid in to_remove:
            del self.connections[cid]
        del self.nodes[node_id]

    def get_node(self, node_id: str) -> Optional[PipelineNode]:
        return self.nodes.get(node_id)

    # ---- Connection management ----

    def add_connection(self, source_node_id: str, source_port_id: str,
                       target_node_id: str, target_port_id: str) -> Connection:
        """Create a connection between two ports after validation.

        Raises:
            ValueError: If nodes/ports don't exist, types are incompatible,
                        target port already has a connection, or connection
                        would create a cycle.
        """
        src_node = self.nodes.get(source_node_id)
        tgt_node = self.nodes.get(target_node_id)
        if not src_node:
            raise ValueError(f"Source node {source_node_id} not found")
        if not tgt_node:
            raise ValueError(f"Target node {target_node_id} not found")

        src_port = src_node.get_port(source_port_id)
        tgt_port = tgt_node.get_port(target_port_id)
        if not src_port or src_port.direction != PortDirection.OUTPUT:
            raise ValueError(f"Source port {source_port_id} is not a valid output")
        if not tgt_port or tgt_port.direction != PortDirection.INPUT:
            raise ValueError(f"Target port {target_port_id} is not a valid input")

        # Type compatibility
        if not can_connect(src_port.port_type, tgt_port.port_type):
            raise ValueError(
                f"Type mismatch: {src_port.port_type.name} -> {tgt_port.port_type.name}"
            )

        # Input port can only have one incoming connection
        for c in self.connections.values():
            if c.target_node_id == target_node_id and c.target_port_id == target_port_id:
                raise ValueError(
                    f"Input port {tgt_port.name} on {tgt_node.name} already connected"
                )

        # Self-connection check
        if source_node_id == target_node_id:
            raise ValueError("Cannot connect a node to itself")

        conn = Connection(
            id=str(uuid.uuid4()),
            source_node_id=source_node_id,
            source_port_id=source_port_id,
            target_node_id=target_node_id,
            target_port_id=target_port_id,
        )

        # Cycle detection: temporarily add and check
        self.connections[conn.id] = conn
        if self._has_cycle():
            del self.connections[conn.id]
            raise ValueError("Connection would create a cycle")

        return conn

    def remove_connection(self, connection_id: str) -> None:
        """Remove a connection by ID."""
        self.connections.pop(connection_id, None)

    def get_connections_for_node(self, node_id: str) -> List[Connection]:
        """Get all connections involving a node."""
        return [
            c for c in self.connections.values()
            if c.source_node_id == node_id or c.target_node_id == node_id
        ]

    def get_incoming_connections(self, node_id: str) -> List[Connection]:
        """Get connections where this node is the target."""
        return [
            c for c in self.connections.values()
            if c.target_node_id == node_id
        ]

    def get_outgoing_connections(self, node_id: str) -> List[Connection]:
        """Get connections where this node is the source."""
        return [
            c for c in self.connections.values()
            if c.source_node_id == node_id
        ]

    # ---- Graph algorithms ----

    def _build_adjacency(self) -> Dict[str, Set[str]]:
        """Build node-level adjacency list from connections."""
        adj: Dict[str, Set[str]] = {nid: set() for nid in self.nodes}
        for c in self.connections.values():
            adj[c.source_node_id].add(c.target_node_id)
        return adj

    def _build_in_degree(self) -> Dict[str, int]:
        """Compute in-degree for each node."""
        deg = {nid: 0 for nid in self.nodes}
        for c in self.connections.values():
            deg[c.target_node_id] += 1
        return deg

    def _has_cycle(self) -> bool:
        """Detect cycles using Kahn's algorithm.

        Returns True if the graph contains a cycle.
        """
        adj = self._build_adjacency()
        in_deg = self._build_in_degree()
        queue = deque(nid for nid, d in in_deg.items() if d == 0)
        visited = 0
        while queue:
            nid = queue.popleft()
            visited += 1
            for neighbor in adj.get(nid, set()):
                in_deg[neighbor] -= 1
                if in_deg[neighbor] == 0:
                    queue.append(neighbor)
        return visited != len(self.nodes)

    def topological_sort(self) -> List[str]:
        """Return node IDs in topological order (Kahn's algorithm).

        Raises:
            ValueError: If the graph has a cycle
        """
        adj = self._build_adjacency()
        in_deg = self._build_in_degree()
        queue = deque(
            sorted(nid for nid, d in in_deg.items() if d == 0)
        )
        result: List[str] = []
        while queue:
            nid = queue.popleft()
            result.append(nid)
            for neighbor in sorted(adj.get(nid, set())):
                in_deg[neighbor] -= 1
                if in_deg[neighbor] == 0:
                    queue.append(neighbor)
        if len(result) != len(self.nodes):
            raise ValueError("Pipeline contains a cycle")
        return result

    def get_downstream_nodes(self, start_node_id: str) -> Set[str]:
        """Get all nodes reachable from start_node_id (not including it)."""
        adj = self._build_adjacency()
        visited: Set[str] = set()
        queue = deque(adj.get(start_node_id, set()))
        while queue:
            nid = queue.popleft()
            if nid in visited:
                continue
            visited.add(nid)
            queue.extend(adj.get(nid, set()))
        return visited

    def get_downstream_from_port(self, node_id: str, port_id: str) -> Set[str]:
        """Get all nodes reachable from a specific output port.

        Follows only connections originating from the given port,
        then recursively all connections from those downstream nodes.
        """
        # First hop: only connections from this specific port
        first_hop = set()
        for c in self.connections.values():
            if c.source_node_id == node_id and c.source_port_id == port_id:
                first_hop.add(c.target_node_id)

        # Then BFS from those nodes through all connections
        adj = self._build_adjacency()
        visited: Set[str] = set()
        queue = deque(first_hop)
        while queue:
            nid = queue.popleft()
            if nid in visited:
                continue
            visited.add(nid)
            queue.extend(adj.get(nid, set()))
        return visited

    # ---- Validation ----

    def validate(self) -> List[str]:
        """Validate the pipeline graph.

        Returns:
            List of error message strings (empty if valid)
        """
        errors: List[str] = []

        if not self.nodes:
            errors.append("Pipeline has no nodes")
            return errors

        # Cycle check
        try:
            self.topological_sort()
        except ValueError:
            errors.append("Pipeline contains a cycle")

        # Type compatibility of all connections
        for c in self.connections.values():
            src_node = self.nodes.get(c.source_node_id)
            tgt_node = self.nodes.get(c.target_node_id)
            if not src_node or not tgt_node:
                errors.append(f"Connection {c.id} references missing node")
                continue
            src_port = src_node.get_port(c.source_port_id)
            tgt_port = tgt_node.get_port(c.target_port_id)
            if not src_port or not tgt_port:
                errors.append(f"Connection {c.id} references missing port")
                continue
            if not can_connect(src_port.port_type, tgt_port.port_type):
                errors.append(
                    f"Type mismatch on connection {c.id}: "
                    f"{src_port.port_type.name} -> {tgt_port.port_type.name}"
                )

        # Required ports must be connected
        for node in self.nodes.values():
            for port in node.inputs:
                if port.required:
                    has_conn = any(
                        c.target_node_id == node.id and c.target_port_id == port.id
                        for c in self.connections.values()
                    )
                    if not has_conn:
                        errors.append(
                            f"Required input '{port.name}' on '{node.name}' is not connected"
                        )

        return errors

    # ---- Serialization ----

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the entire pipeline to a JSON-compatible dict."""
        return {
            'name': self.name,
            'nodes': [n.to_dict() for n in self.nodes.values()],
            'connections': [c.to_dict() for c in self.connections.values()],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'Pipeline':
        """Deserialize a pipeline from a dict."""
        pipeline = cls(name=d.get('name', 'Untitled Pipeline'))
        for nd in d.get('nodes', []):
            pipeline.nodes[nd['id']] = PipelineNode.from_dict(nd)
        for cd in d.get('connections', []):
            pipeline.connections[cd['id']] = Connection.from_dict(cd)
        return pipeline
