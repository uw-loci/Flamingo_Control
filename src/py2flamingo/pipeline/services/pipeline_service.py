"""
PipelineService — facade for pipeline operations.

Coordinates pipeline creation, validation, and execution.
Acts as the service layer between the controller and the engine/models.
"""

import logging
from typing import Any, Dict, List, Optional

from py2flamingo.pipeline.models.pipeline import NodeType, Pipeline, create_node
from py2flamingo.pipeline.services.pipeline_repository import PipelineRepository
from py2flamingo.pipeline.services.threshold_analysis_service import (
    ThresholdAnalysisService,
)

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

    def delete(self, filename: str) -> bool:
        """Delete a saved pipeline."""
        return self._repository.delete(filename)
