"""
PipelineService — facade for pipeline operations.

Coordinates pipeline creation, validation, and execution.
Acts as the service layer between the controller and the engine/models.
"""

import logging
from typing import Optional, Dict, Any, List

from py2flamingo.pipeline.models.pipeline import Pipeline, NodeType, create_node
from py2flamingo.pipeline.services.pipeline_repository import PipelineRepository
from py2flamingo.pipeline.services.threshold_analysis_service import ThresholdAnalysisService

logger = logging.getLogger(__name__)


class PipelineService:
    """High-level facade for pipeline operations."""

    def __init__(self, repository: Optional[PipelineRepository] = None):
        self._repository = repository or PipelineRepository()
        self._threshold_service = ThresholdAnalysisService()

    @property
    def repository(self) -> PipelineRepository:
        return self._repository

    @property
    def threshold_service(self) -> ThresholdAnalysisService:
        return self._threshold_service

    def create_pipeline(self, name: str = "New Pipeline") -> Pipeline:
        """Create a new empty pipeline."""
        return Pipeline(name=name)

    def validate(self, pipeline: Pipeline) -> List[str]:
        """Validate a pipeline and return error messages."""
        return pipeline.validate()

    def save(self, pipeline: Pipeline, filename: Optional[str] = None) -> str:
        """Save a pipeline and return the file path."""
        path = self._repository.save(pipeline, filename)
        return str(path)

    def load(self, filename: str) -> Pipeline:
        """Load a pipeline from the repository."""
        return self._repository.load(filename)

    def load_from_path(self, path: str) -> Pipeline:
        """Load a pipeline from an absolute path."""
        return self._repository.load_from_path(path)

    def list_saved(self) -> List[str]:
        """List saved pipeline files."""
        return self._repository.list_pipelines()

    def delete(self, filename: str) -> bool:
        """Delete a saved pipeline."""
        return self._repository.delete(filename)

    def create_example_pipeline(self) -> Pipeline:
        """Create a sample pipeline: Workflow → Threshold → ForEach → Workflow.

        Useful for testing and as a starting template.
        """
        pipeline = Pipeline(name="Example: Acquire-Analyze-Reacquire")

        # Create nodes
        acquire = create_node(NodeType.WORKFLOW, name="Initial Acquisition",
                              config={'workflow_type': 'zstack'}, x=50, y=100)
        threshold = create_node(NodeType.THRESHOLD, name="Detect Objects",
                                config={
                                    'channel_thresholds': {0: 200},
                                    'gauss_sigma': 1.0,
                                    'min_object_size': 100,
                                }, x=300, y=100)
        for_each = create_node(NodeType.FOR_EACH, name="For Each Object",
                               x=550, y=100)
        reacquire = create_node(NodeType.WORKFLOW, name="Re-acquire at Object",
                                config={
                                    'workflow_type': 'zstack',
                                    'use_input_position': True,
                                }, x=800, y=100)

        # Add nodes
        for node in [acquire, threshold, for_each, reacquire]:
            pipeline.add_node(node)

        # Connect: Acquire.volume → Threshold.volume
        pipeline.add_connection(
            acquire.id, acquire.get_output('volume').id,
            threshold.id, threshold.get_input('volume').id,
        )

        # Connect: Threshold.objects → ForEach.collection
        pipeline.add_connection(
            threshold.id, threshold.get_output('objects').id,
            for_each.id, for_each.get_input('collection').id,
        )

        # Connect: ForEach.current_item → Reacquire.position
        pipeline.add_connection(
            for_each.id, for_each.get_output('current_item').id,
            reacquire.id, reacquire.get_input('position').id,
        )

        return pipeline
