"""
ExecutionContext — per-run state and service references for pipeline execution.

Holds the port values produced by nodes, cancellation state, and references
to application services needed by node runners.
"""

import logging
from typing import Dict, Optional, Any

from py2flamingo.pipeline.models.port_types import PortValue

logger = logging.getLogger(__name__)


class ExecutionContext:
    """Per-run execution state shared across all node runners.

    Attributes:
        port_values: Map of port_id -> PortValue produced during execution
        services: Map of service_name -> service instance (injected at creation)
        variables: General-purpose key-value store for inter-node communication
    """

    def __init__(self, services: Optional[Dict[str, Any]] = None):
        self.port_values: Dict[str, PortValue] = {}
        self.services: Dict[str, Any] = services or {}
        self.variables: Dict[str, Any] = {}
        self._cancelled = False

    # ---- Port value management ----

    def set_port_value(self, port_id: str, value: PortValue) -> None:
        """Store the output value of a port."""
        self.port_values[port_id] = value

    def get_port_value(self, port_id: str) -> Optional[PortValue]:
        """Retrieve the value that was written to a port."""
        return self.port_values.get(port_id)

    def get_input_value(self, pipeline, node_id: str, port_name: str) -> Optional[PortValue]:
        """Resolve the value feeding into a named input port of a node.

        Follows the connection from the source output port to find the value.

        Args:
            pipeline: Pipeline instance to look up connections
            node_id: Target node ID
            port_name: Name of the input port on that node
        """
        node = pipeline.get_node(node_id)
        if not node:
            return None
        port = node.get_input(port_name)
        if not port:
            return None

        # Find connection feeding this input port
        for conn in pipeline.get_incoming_connections(node_id):
            if conn.target_port_id == port.id:
                return self.port_values.get(conn.source_port_id)
        return None

    # ---- Service access ----

    def get_service(self, name: str) -> Optional[Any]:
        """Get an application service by name."""
        return self.services.get(name)

    # ---- Cancellation ----

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        """Request cancellation of the pipeline run."""
        logger.info("Pipeline execution cancellation requested")
        self._cancelled = True

    # ---- Scoped copy for ForEach iterations ----

    def create_scoped_copy(self) -> 'ExecutionContext':
        """Create a child context that inherits port values and services.

        The child shares the services dict and cancellation state reference,
        but gets its own copy of port_values so that loop iterations don't
        clobber each other's intermediate results.
        """
        child = ExecutionContext(services=self.services)
        child.port_values = dict(self.port_values)  # shallow copy
        child.variables = dict(self.variables)
        child._cancelled = self._cancelled
        # Share cancellation flag by reference via parent
        child._parent = self
        return child

    def check_cancelled(self) -> bool:
        """Check cancellation including parent context."""
        if self._cancelled:
            return True
        parent = getattr(self, '_parent', None)
        if parent and parent.is_cancelled:
            self._cancelled = True
            return True
        return False
