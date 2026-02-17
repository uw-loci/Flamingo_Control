"""
ScopeResolver — identifies ForEach body nodes and Conditional branch subgraphs.

ForEach and Conditional nodes own subgraphs that should not run as part of
the top-level DAG walk. The ScopeResolver identifies which nodes belong to
each scope so the executor can skip them at the top level and hand them to
the appropriate runner for scoped execution.
"""

import logging
from typing import Dict, Set, List

from py2flamingo.pipeline.models.pipeline import Pipeline, NodeType

logger = logging.getLogger(__name__)


class ScopeInfo:
    """Describes the scope owned by a ForEach or Conditional node."""

    def __init__(self, owner_node_id: str, owner_type: NodeType):
        self.owner_node_id = owner_node_id
        self.owner_type = owner_type
        # For ForEach: body_nodes = set of node IDs executed each iteration
        self.body_nodes: Set[str] = set()
        # For Conditional: branch -> set of node IDs
        self.branches: Dict[str, Set[str]] = {}


class ScopeResolver:
    """Resolves which nodes belong to ForEach/Conditional scopes.

    Usage:
        resolver = ScopeResolver(pipeline)
        scopes = resolver.resolve()
        top_level_ids = resolver.get_top_level_node_ids()
    """

    def __init__(self, pipeline: Pipeline):
        self._pipeline = pipeline
        self._scopes: Dict[str, ScopeInfo] = {}
        self._scoped_node_ids: Set[str] = set()

    def resolve(self) -> Dict[str, ScopeInfo]:
        """Analyze the pipeline and identify all scoped subgraphs.

        Returns:
            Dict mapping scope-owner node_id -> ScopeInfo
        """
        self._scopes.clear()
        self._scoped_node_ids.clear()

        for node in self._pipeline.nodes.values():
            if node.node_type == NodeType.FOR_EACH:
                self._resolve_foreach(node.id)
            elif node.node_type == NodeType.CONDITIONAL:
                self._resolve_conditional(node.id)

        return self._scopes

    def _resolve_foreach(self, node_id: str) -> None:
        """Identify body nodes of a ForEach node.

        Body = all nodes reachable from the "current_item" and "index" output ports.
        """
        node = self._pipeline.get_node(node_id)
        if not node:
            return

        scope = ScopeInfo(node_id, NodeType.FOR_EACH)

        # Collect downstream from current_item and index outputs
        for port_name in ('current_item', 'index'):
            port = node.get_output(port_name)
            if port:
                downstream = self._pipeline.get_downstream_from_port(node_id, port.id)
                scope.body_nodes |= downstream

        self._scoped_node_ids |= scope.body_nodes
        self._scopes[node_id] = scope

        logger.debug(
            f"ForEach {node.name} ({node_id}): body contains "
            f"{len(scope.body_nodes)} nodes"
        )

    def _resolve_conditional(self, node_id: str) -> None:
        """Identify branch subgraphs of a Conditional node.

        true_branch output -> "true" branch nodes
        false_branch output -> "false" branch nodes
        """
        node = self._pipeline.get_node(node_id)
        if not node:
            return

        scope = ScopeInfo(node_id, NodeType.CONDITIONAL)

        for branch_name, port_name in [('true', 'true_branch'), ('false', 'false_branch')]:
            port = node.get_output(port_name)
            if port:
                downstream = self._pipeline.get_downstream_from_port(node_id, port.id)
                scope.branches[branch_name] = downstream
                self._scoped_node_ids |= downstream

        self._scopes[node_id] = scope

        logger.debug(
            f"Conditional {node.name} ({node_id}): "
            f"true={len(scope.branches.get('true', set()))} nodes, "
            f"false={len(scope.branches.get('false', set()))} nodes"
        )

    def get_top_level_node_ids(self) -> List[str]:
        """Get node IDs that should be executed at the top level.

        These are nodes NOT owned by any ForEach/Conditional scope.
        Returned in topological order.
        """
        all_sorted = self._pipeline.topological_sort()
        return [nid for nid in all_sorted if nid not in self._scoped_node_ids]

    def get_scope(self, node_id: str) -> ScopeInfo:
        """Get scope info for a ForEach or Conditional node.

        Raises:
            KeyError: If node_id is not a scope owner
        """
        return self._scopes[node_id]

    def is_scoped(self, node_id: str) -> bool:
        """Check if a node belongs to a ForEach/Conditional scope."""
        return node_id in self._scoped_node_ids

    def get_body_sorted(self, scope_owner_id: str) -> List[str]:
        """Get body/branch nodes in topological order.

        For ForEach: returns body_nodes sorted.
        For Conditional: returns all branch nodes sorted (caller picks branch).
        """
        scope = self._scopes.get(scope_owner_id)
        if not scope:
            return []

        if scope.owner_type == NodeType.FOR_EACH:
            target_ids = scope.body_nodes
        else:
            target_ids = set()
            for branch_nodes in scope.branches.values():
                target_ids |= branch_nodes

        # Filter topological sort to just these nodes
        all_sorted = self._pipeline.topological_sort()
        return [nid for nid in all_sorted if nid in target_ids]

    def get_branch_sorted(self, scope_owner_id: str, branch: str) -> List[str]:
        """Get nodes for a specific Conditional branch in topological order."""
        scope = self._scopes.get(scope_owner_id)
        if not scope or scope.owner_type != NodeType.CONDITIONAL:
            return []
        branch_nodes = scope.branches.get(branch, set())
        all_sorted = self._pipeline.topological_sort()
        return [nid for nid in all_sorted if nid in branch_nodes]
