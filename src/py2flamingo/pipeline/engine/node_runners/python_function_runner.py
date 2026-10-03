"""PythonFunctionRunner — executes an operator-written body as a pipeline step.

Config:
    code: str — the function body, as typed in the node's editor

Inputs:
    volume  — VOLUME      (body gets ``volume`` AND ``volumes``, see below)
    value   — ANY         (available as ``value``)
    objects — OBJECT_LIST (available as ``objects``)
    trigger — TRIGGER     (execution order only)

A VOLUME port may carry a single array or a ``{channel_id: array}`` dict --
``SAMPLE_VIEW_DATA`` emits the dict, ``--input`` with one channel emits an
array, and ``ThresholdRunner`` handles both at ``threshold_runner.py:85``.
Passing that straight through would make every body guess, so this runner
normalizes: ``volume`` is always one 3-D array (the lowest channel id when a
dict arrives) and ``volumes`` is always the full mapping.

Outputs:
    value / boolean / mask / objects / result — whatever the body returned
    true_branch / false_branch — fired from ``boolean``, so the node can act as
        a conditional in its own right rather than needing one wired after it
    completed — TRIGGER, always fired on success

The rules about what a body may contain, and the honest limits of them, live in
:mod:`py2flamingo.pipeline.models.python_function`. This file is dispatch only.
"""

import logging

from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.node_runners.base_runner import AbstractNodeRunner
from py2flamingo.pipeline.models.pipeline import Pipeline, PipelineNode
from py2flamingo.pipeline.models.port_types import PortType
from py2flamingo.pipeline.models.python_function import NodeCodeError, run_code

logger = logging.getLogger(__name__)


def _normalize_volume_input(raw):
    """Split a VOLUME port value into (one array, {channel: array}).

    Returns ``(None, {})`` for an unconnected port so a body can test
    ``volume is None`` instead of catching a TypeError. The single array is the
    LOWEST channel id rather than whatever the dict happens to iterate first,
    so the same pipeline picks the same channel on every run.
    """
    if raw is None:
        return None, {}
    if isinstance(raw, dict):
        if not raw:
            return None, {}
        try:
            first = min(raw)
        except TypeError:
            # Mixed or unorderable keys: fall back to insertion order rather
            # than failing, and let the body use `volumes` if it cares.
            first = next(iter(raw))
        return raw[first], dict(raw)
    return raw, {0: raw}


class PythonFunctionRunner(AbstractNodeRunner):
    """Runs the node's code and maps what it returned onto the output ports."""

    def __init__(self):
        self._scope_resolver = None
        self._executor = None

    def set_scope_resolver(self, resolver):
        self._scope_resolver = resolver

    def set_executor(self, executor):
        self._executor = executor

    def run(
        self, node: PipelineNode, pipeline: Pipeline, context: ExecutionContext
    ) -> None:
        code = (node.config or {}).get("code", "")

        volume, volumes = _normalize_volume_input(
            self._get_input(node, pipeline, context, "volume")
        )

        try:
            result = run_code(
                code,
                volume=volume,
                volumes=volumes,
                value=self._get_input(node, pipeline, context, "value"),
                objects=self._get_input(node, pipeline, context, "objects"),
                params=dict(node.config or {}).get("params") or {},
            )
        except NodeCodeError as e:
            # Name the node: a pipeline can hold several of these, and "line 3"
            # on its own does not say which editor to open.
            raise RuntimeError(f"Python function '{node.name}': {e}") from e

        for line in result.log_lines:
            logger.info(f"Python function '{node.name}': {line}")

        self._set_output(node, context, "value", PortType.SCALAR, result.value)
        self._set_output(node, context, "boolean", PortType.BOOLEAN, result.boolean)
        self._set_output(node, context, "mask", PortType.VOLUME, result.mask)
        self._set_output(node, context, "objects", PortType.OBJECT_LIST, result.objects)
        self._set_output(node, context, "result", PortType.ANY, result.result)
        self._set_output(node, context, "completed", PortType.TRIGGER, True)

        if result.boolean is not None:
            self._run_branch(node, context, result.boolean)

    def _run_branch(
        self, node: PipelineNode, context: ExecutionContext, condition: bool
    ) -> None:
        """Fire and execute the branch the body's boolean selected.

        Branching is optional here, unlike in ConditionalRunner: a body that
        returns no boolean is an analysis step, not a decision, and must not be
        made to look like a decision that chose 'false'.
        """
        self._set_output(
            node,
            context,
            "true_branch" if condition else "false_branch",
            PortType.TRIGGER,
            True,
        )

        if not (self._scope_resolver and self._executor):
            # Branch ports still carry their value for anything reading them
            # directly; only subgraph execution needs the wiring.
            logger.debug(
                f"Python function '{node.name}': no executor, branch not expanded"
            )
            return

        branch = "true" if condition else "false"
        branch_sorted = self._scope_resolver.get_branch_sorted(node.id, branch)
        if branch_sorted:
            logger.info(
                f"Python function '{node.name}': executing {branch} branch "
                f"({len(branch_sorted)} nodes)"
            )
            self._executor.execute_subgraph(branch_sorted, context)
