"""
PipelineController — mediates between the pipeline editor UI and the execution engine.

Creates the executor with appropriate runners and service references,
wires signals between executor and editor dialog, and manages lifecycle.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtCore import QObject

from py2flamingo.pipeline.models.pipeline import Pipeline, NodeType
from py2flamingo.pipeline.engine.executor import PipelineExecutor
from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.node_runners.workflow_runner import WorkflowRunner
from py2flamingo.pipeline.engine.node_runners.threshold_runner import ThresholdRunner
from py2flamingo.pipeline.engine.node_runners.foreach_runner import ForEachRunner
from py2flamingo.pipeline.engine.node_runners.conditional_runner import ConditionalRunner
from py2flamingo.pipeline.engine.node_runners.external_command_runner import ExternalCommandRunner
from py2flamingo.pipeline.services.pipeline_service import PipelineService
from py2flamingo.pipeline.ui.pipeline_editor_dialog import PipelineEditorDialog

logger = logging.getLogger(__name__)


class PipelineController(QObject):
    """Coordinates pipeline UI, services, and execution.

    Attributes:
        service: PipelineService instance
        editor: PipelineEditorDialog (created lazily)
    """

    def __init__(self, service: PipelineService, app=None, parent=None):
        super().__init__(parent)
        self._service = service
        self._app = app
        self._editor: Optional[PipelineEditorDialog] = None
        self._executor: Optional[PipelineExecutor] = None

    @property
    def service(self) -> PipelineService:
        return self._service

    def open_editor(self):
        """Open (or raise) the pipeline editor dialog."""
        if self._editor is None:
            self._editor = PipelineEditorDialog(app=self._app)
            self._editor.run_requested.connect(self._on_run_requested)
            self._editor.stop_requested.connect(self._on_stop_requested)

        self._editor.show()
        self._editor.raise_()
        self._editor.activateWindow()

    def _on_run_requested(self, pipeline_dict: dict):
        """Handle run request from the editor."""
        try:
            pipeline = Pipeline.from_dict(pipeline_dict)
            self._execute_pipeline(pipeline)
        except Exception as e:
            logger.exception(f"Failed to start pipeline: {e}")
            if self._editor:
                self._editor.on_pipeline_error(str(e))

    def _on_stop_requested(self):
        """Handle stop request from the editor."""
        if self._executor and self._executor.isRunning():
            self._executor.requestInterruption()
            if self._executor.context:
                self._executor.context.cancel()
            logger.info("Pipeline stop requested")

    def _execute_pipeline(self, pipeline: Pipeline):
        """Set up and run the pipeline executor."""
        # Build execution context with service references
        services: Dict[str, Any] = {}

        if self._app:
            # Inject application services
            if hasattr(self._app, 'workflow_controller') and self._app.workflow_controller:
                facade = getattr(self._app.workflow_controller, '_workflow_service', None)
                if facade:
                    services['workflow_facade'] = facade

            if hasattr(self._app, 'workflow_queue_service'):
                services['workflow_queue_service'] = self._app.workflow_queue_service

            if hasattr(self._app, 'voxel_storage'):
                services['voxel_storage'] = self._app.voxel_storage

        context = ExecutionContext(services=services)

        # Create runners
        runners = {
            NodeType.WORKFLOW: WorkflowRunner(),
            NodeType.THRESHOLD: ThresholdRunner(),
            NodeType.FOR_EACH: ForEachRunner(),
            NodeType.CONDITIONAL: ConditionalRunner(),
            NodeType.EXTERNAL_COMMAND: ExternalCommandRunner(),
        }

        # Create executor
        self._executor = PipelineExecutor(pipeline, context, runners)

        # Wire signals to editor
        if self._editor:
            self._executor.node_started.connect(self._editor.on_node_started)
            self._executor.node_completed.connect(self._editor.on_node_completed)
            self._executor.node_error.connect(self._editor.on_node_error)
            self._executor.pipeline_completed.connect(self._editor.on_pipeline_completed)
            self._executor.pipeline_error.connect(self._editor.on_pipeline_error)
            self._executor.foreach_iteration.connect(self._editor.on_foreach_iteration)
            self._executor.log_message.connect(self._editor.on_log_message)

        # Clean up when done
        self._executor.finished.connect(self._on_executor_finished)

        # Start
        logger.info(f"Starting pipeline execution: {pipeline.name}")
        self._executor.start()

    def _on_executor_finished(self):
        """Clean up after executor thread finishes."""
        logger.info("Pipeline executor thread finished")
        self._executor = None
