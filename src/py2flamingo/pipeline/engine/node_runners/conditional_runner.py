"""
ConditionalRunner — evaluates a condition and executes one branch.

Config:
    comparison_op: str — one of '>', '<', '==', '!=', '>=', '<='
    threshold_value: float — comparison value (used if threshold port unconnected)

Inputs:
    value — ANY (left side of comparison)
    threshold — SCALAR (optional — overrides config threshold_value)

Outputs:
    true_branch — TRIGGER (fires if condition is true)
    false_branch — TRIGGER (fires if condition is false)
    pass_through — ANY (passes the input value regardless of branch)
"""

import logging
import operator

from py2flamingo.pipeline.models.port_types import PortType
from py2flamingo.pipeline.models.pipeline import Pipeline, PipelineNode
from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.node_runners.base_runner import AbstractNodeRunner

logger = logging.getLogger(__name__)

_OPERATORS = {
    '>': operator.gt,
    '<': operator.lt,
    '==': operator.eq,
    '!=': operator.ne,
    '>=': operator.ge,
    '<=': operator.le,
}


class ConditionalRunner(AbstractNodeRunner):
    """Evaluates a condition and executes the matching branch subgraph."""

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
            raise RuntimeError("ConditionalRunner requires scope_resolver and executor")

        config = node.config

        # Get value input
        value = self._get_input(node, pipeline, context, 'value')
        if value is None:
            raise RuntimeError(f"Conditional '{node.name}': no input value")

        # Get threshold: from port if connected, otherwise from config
        threshold = self._get_input(node, pipeline, context, 'threshold')
        if threshold is None:
            threshold = config.get('threshold_value', 0)

        # Coerce to numeric for comparison
        try:
            value_num = float(value) if not isinstance(value, (int, float)) else value
            threshold_num = float(threshold)
        except (TypeError, ValueError):
            # Fall back to equality comparison for non-numeric
            value_num = value
            threshold_num = threshold

        # Evaluate condition
        op_str = config.get('comparison_op', '>')
        op_fn = _OPERATORS.get(op_str)
        if not op_fn:
            raise RuntimeError(f"Unknown comparison operator: {op_str}")

        result = op_fn(value_num, threshold_num)
        branch = 'true' if result else 'false'

        logger.info(
            f"Conditional '{node.name}': {value_num} {op_str} {threshold_num} = {result}"
        )

        # Pass through the input value
        self._set_output(node, context, 'pass_through', PortType.ANY, value)

        # Fire the matching branch trigger
        self._set_output(
            node, context,
            'true_branch' if result else 'false_branch',
            PortType.TRIGGER, True
        )

        # Execute the matching branch subgraph
        branch_sorted = self._scope_resolver.get_branch_sorted(node.id, branch)
        if branch_sorted:
            logger.info(
                f"Conditional '{node.name}': executing {branch} branch "
                f"({len(branch_sorted)} nodes)"
            )
            self._executor.execute_subgraph(branch_sorted, context)
        else:
            logger.info(f"Conditional '{node.name}': {branch} branch has no nodes")
