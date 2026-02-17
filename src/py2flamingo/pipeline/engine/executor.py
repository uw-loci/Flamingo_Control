"""
PipelineExecutor — QThread-based DAG walker that runs a pipeline.

Validates the pipeline, performs topological sort, resolves scopes,
and executes each top-level node in order. ForEach/Conditional runners
handle their own scoped subgraphs internally.
"""

import logging
from typing import Dict, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from py2flamingo.pipeline.models.pipeline import Pipeline, NodeType
from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.scope_resolver import ScopeResolver
from py2flamingo.pipeline.engine.node_runners.base_runner import AbstractNodeRunner

logger = logging.getLogger(__name__)


class PipelineExecutor(QThread):
    """Executes a pipeline on a background thread.

    Signals:
        node_started: Emitted when a node begins execution (node_id)
        node_completed: Emitted when a node finishes (node_id)
        node_error: Emitted when a node fails (node_id, error_message)
        pipeline_progress: Emitted after each node (current_index, total_count)
        pipeline_completed: Emitted on successful completion
        pipeline_error: Emitted on fatal error (error_message)
        foreach_iteration: Emitted during ForEach loops (node_id, current, total)
        log_message: General log output (message)
    """

    node_started = pyqtSignal(str)
    node_completed = pyqtSignal(str)
    node_error = pyqtSignal(str, str)
    pipeline_progress = pyqtSignal(int, int)
    pipeline_completed = pyqtSignal()
    pipeline_error = pyqtSignal(str)
    foreach_iteration = pyqtSignal(str, int, int)
    log_message = pyqtSignal(str)

    def __init__(self, pipeline: Pipeline, context: ExecutionContext,
                 runners: Optional[Dict[NodeType, AbstractNodeRunner]] = None,
                 parent=None):
        super().__init__(parent)
        self._pipeline = pipeline
        self._context = context
        self._runners = runners or {}

    def register_runner(self, node_type: NodeType, runner: AbstractNodeRunner) -> None:
        """Register a runner for a node type."""
        self._runners[node_type] = runner

    @property
    def context(self) -> ExecutionContext:
        return self._context

    def run(self):
        """Thread entry point — executes the pipeline."""
        try:
            self._execute()
        except Exception as e:
            logger.exception(f"Pipeline execution failed: {e}")
            self.pipeline_error.emit(str(e))

    def _execute(self):
        """Main execution logic."""
        # Validate
        errors = self._pipeline.validate()
        if errors:
            msg = "Pipeline validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            self.log_message.emit(msg)
            self.pipeline_error.emit(msg)
            return

        # Resolve scopes
        resolver = ScopeResolver(self._pipeline)
        resolver.resolve()
        top_level_ids = resolver.get_top_level_node_ids()

        total = len(top_level_ids)
        self.log_message.emit(f"Executing pipeline: {total} top-level nodes")

        for idx, node_id in enumerate(top_level_ids):
            # Check cancellation
            if self.isInterruptionRequested() or self._context.check_cancelled():
                self.log_message.emit("Pipeline cancelled")
                self.pipeline_error.emit("Pipeline cancelled by user")
                return

            node = self._pipeline.get_node(node_id)
            if not node:
                continue

            self.node_started.emit(node_id)
            self.log_message.emit(f"Running node: {node.name} ({node.node_type.name})")

            try:
                runner = self._runners.get(node.node_type)
                if not runner:
                    raise RuntimeError(
                        f"No runner registered for node type {node.node_type.name}"
                    )

                # Pass scope resolver to runners that need it
                if hasattr(runner, 'set_scope_resolver'):
                    runner.set_scope_resolver(resolver)
                if hasattr(runner, 'set_executor'):
                    runner.set_executor(self)

                runner.run(node, self._pipeline, self._context)

                self.node_completed.emit(node_id)
                self.pipeline_progress.emit(idx + 1, total)

            except Exception as e:
                error_msg = f"Node '{node.name}' failed: {e}"
                logger.exception(error_msg)
                self.node_error.emit(node_id, str(e))
                self.log_message.emit(error_msg)
                self.pipeline_error.emit(error_msg)
                return

        self.log_message.emit("Pipeline completed successfully")
        self.pipeline_completed.emit()

    def execute_subgraph(self, node_ids: list, context: ExecutionContext) -> None:
        """Execute a subset of nodes in order (used by ForEach/Conditional runners).

        This runs on the same thread as the caller (the ForEach/Conditional runner).

        Args:
            node_ids: Node IDs in topological order to execute
            context: Execution context (may be a scoped copy)
        """
        for node_id in node_ids:
            if self.isInterruptionRequested() or context.check_cancelled():
                raise RuntimeError("Pipeline cancelled")

            node = self._pipeline.get_node(node_id)
            if not node:
                continue

            self.node_started.emit(node_id)

            runner = self._runners.get(node.node_type)
            if not runner:
                raise RuntimeError(
                    f"No runner registered for node type {node.node_type.name}"
                )

            if hasattr(runner, 'set_executor'):
                runner.set_executor(self)

            runner.run(node, self._pipeline, context)
            self.node_completed.emit(node_id)
