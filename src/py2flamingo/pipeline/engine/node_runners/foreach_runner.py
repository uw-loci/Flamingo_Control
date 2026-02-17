"""
ForEachRunner — iterates over a collection and re-executes body subgraph.

Inputs:
    collection — OBJECT_LIST (List[DetectedObject])

Outputs:
    current_item — OBJECT (single DetectedObject, set each iteration)
    index — SCALAR (0-based iteration index)
    completed — TRIGGER (after all iterations)

The runner uses ScopeResolver to identify body nodes, then calls
PipelineExecutor.execute_subgraph() for each item in the collection.
"""

import logging

from py2flamingo.pipeline.models.port_types import PortType
from py2flamingo.pipeline.models.pipeline import Pipeline, PipelineNode
from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.node_runners.base_runner import AbstractNodeRunner

logger = logging.getLogger(__name__)


class ForEachRunner(AbstractNodeRunner):
    """Iterates a collection, executing body nodes for each item."""

    def __init__(self):
        self._scope_resolver = None
        self._executor = None

    def set_scope_resolver(self, resolver):
        self._scope_resolver = resolver

    def set_executor(self, executor):
        self._executor = executor

    def run(self, node: PipelineNode, pipeline: Pipeline,
            context: ExecutionContext) -> None:
        if not self._scope_resolver or not self._executor:
            raise RuntimeError("ForEachRunner requires scope_resolver and executor")

        # Get collection input
        collection = self._get_input(node, pipeline, context, 'collection')
        if collection is None:
            collection = []

        if not isinstance(collection, (list, tuple)):
            raise RuntimeError(
                f"ForEach input must be a list, got {type(collection).__name__}"
            )

        total = len(collection)
        logger.info(f"ForEach '{node.name}': iterating over {total} items")

        # Get body node IDs from scope resolver
        scope = self._scope_resolver.get_scope(node.id)
        body_sorted = self._scope_resolver.get_body_sorted(node.id)

        if not body_sorted:
            logger.warning(f"ForEach '{node.name}' has no body nodes")
            self._set_output(node, context, 'completed', PortType.TRIGGER, True)
            return

        # Get port IDs for current_item and index outputs
        current_item_port = node.get_output('current_item')
        index_port = node.get_output('index')

        for idx, item in enumerate(collection):
            if context.check_cancelled():
                raise RuntimeError("Pipeline cancelled during ForEach iteration")

            logger.info(f"ForEach '{node.name}': iteration {idx + 1}/{total}")

            # Emit progress signal
            if hasattr(self._executor, 'foreach_iteration'):
                self._executor.foreach_iteration.emit(node.id, idx + 1, total)

            # Create scoped context for this iteration
            iter_context = context.create_scoped_copy()

            # Inject current_item and index into the scoped context
            if current_item_port:
                from py2flamingo.pipeline.models.port_types import PortValue
                iter_context.set_port_value(
                    current_item_port.id,
                    PortValue(port_type=PortType.OBJECT, data=item)
                )
            if index_port:
                from py2flamingo.pipeline.models.port_types import PortValue
                iter_context.set_port_value(
                    index_port.id,
                    PortValue(port_type=PortType.SCALAR, data=idx)
                )

            # Execute body subgraph
            self._executor.execute_subgraph(body_sorted, iter_context)

        # Signal completion
        self._set_output(node, context, 'completed', PortType.TRIGGER, True)
        logger.info(f"ForEach '{node.name}': completed all {total} iterations")
