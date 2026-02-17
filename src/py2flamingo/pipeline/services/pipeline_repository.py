"""
PipelineRepository — save/load pipeline JSON files.

Manages a directory of pipeline files and provides CRUD operations.
"""

import json
import logging
from pathlib import Path
from typing import List, Optional

from py2flamingo.pipeline.models.pipeline import Pipeline

logger = logging.getLogger(__name__)

DEFAULT_PIPELINE_DIR = "pipelines"


class PipelineRepository:
    """Persistence layer for Pipeline objects (JSON files)."""

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            self._dir = Path(base_dir)
        else:
            self._dir = Path.home() / '.flamingo' / DEFAULT_PIPELINE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        return self._dir

    def save(self, pipeline: Pipeline, filename: Optional[str] = None) -> Path:
        """Save a pipeline to JSON.

        Args:
            pipeline: Pipeline to save
            filename: Optional filename (defaults to pipeline.name + .json)

        Returns:
            Path to the saved file
        """
        if not filename:
            safe_name = pipeline.name.replace(' ', '_').lower()
            filename = f"{safe_name}.json"

        path = self._dir / filename
        data = pipeline.to_dict()

        with open(path, 'w') as f:
            json.dump(data, f, indent=2)

        logger.info(f"Pipeline saved: {path}")
        return path

    def load(self, filename: str) -> Pipeline:
        """Load a pipeline from JSON.

        Args:
            filename: Name of the file in the pipeline directory

        Returns:
            Pipeline instance

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        path = self._dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Pipeline file not found: {path}")

        with open(path) as f:
            data = json.load(f)

        pipeline = Pipeline.from_dict(data)
        logger.info(f"Pipeline loaded: {path}")
        return pipeline

    def load_from_path(self, path: str) -> Pipeline:
        """Load a pipeline from an absolute path."""
        with open(path) as f:
            data = json.load(f)
        return Pipeline.from_dict(data)

    def list_pipelines(self) -> List[str]:
        """List all pipeline files in the directory."""
        return sorted(
            f.name for f in self._dir.glob('*.json')
        )

    def delete(self, filename: str) -> bool:
        """Delete a pipeline file."""
        path = self._dir / filename
        if path.exists():
            path.unlink()
            logger.info(f"Pipeline deleted: {path}")
            return True
        return False
