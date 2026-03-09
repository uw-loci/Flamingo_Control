"""
OverviewAnalysisRunner — runs 2D overview tile analysis via OverviewTileAnalysisService.

Config:
    method: str — detection method key
    tiles_x: int — number of tiles in X
    tiles_y: int — number of tiles in Y
    (plus method-specific threshold/sigma parameters)

Inputs:
    image — 2D numpy array (VOLUME port)
    image_path — file path to load image from (FILE_PATH port)
    trigger — trigger input

Outputs:
    selected_tiles — List[DetectedObject] with tile centroids
    count — number of selected tiles
    mask — 2D boolean mask [tiles_y, tiles_x]
"""

import logging

import numpy as np

from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.node_runners.base_runner import AbstractNodeRunner
from py2flamingo.pipeline.models.detected_object import DetectedObject
from py2flamingo.pipeline.models.pipeline import Pipeline, PipelineNode
from py2flamingo.pipeline.models.port_types import PortType
from py2flamingo.pipeline.services.overview_tile_analysis_service import (
    OverviewTileAnalysisService,
    TileAnalysisSettings,
)

logger = logging.getLogger(__name__)


class OverviewAnalysisRunner(AbstractNodeRunner):
    """Runs 2D overview tile analysis using OverviewTileAnalysisService."""

    def __init__(self):
        self._service = OverviewTileAnalysisService()

    def run(
        self, node: PipelineNode, pipeline: Pipeline, context: ExecutionContext
    ) -> None:
        config = node.config

        # Get image: from input port or from file path
        image = self._get_input(node, pipeline, context, "image")

        if image is None:
            # Try file path
            image_path = self._get_input(node, pipeline, context, "image_path")
            if image_path is None:
                image_path = config.get("image_path", "")

            if image_path:
                image = self._load_image(image_path)

        if image is None:
            raise RuntimeError(
                "No input image available. Connect an image input or specify image_path."
            )

        # Ensure 2D
        if image.ndim == 3 and image.shape[2] in (3, 4):
            pass  # RGB/RGBA — service handles conversion
        elif image.ndim > 2:
            # Take first slice if 3D volume
            image = image[0] if image.ndim == 3 else image[0, 0]

        # Build settings from config
        settings = TileAnalysisSettings(
            method=config.get("method", "entropy"),
            tiles_x=config.get("tiles_x", 8),
            tiles_y=config.get("tiles_y", 8),
            entropy_threshold=config.get("entropy_threshold", 3.0),
            smoothing=config.get("smoothing", True),
            bp_var_min=config.get("bp_var_min", 0.0),
            bp_var_max=config.get("bp_var_max", 1000.0),
            bp_entropy_min=config.get("bp_entropy_min", 2.0),
            gradient_threshold=config.get("gradient_threshold", 0.5),
            dog_threshold=config.get("dog_threshold", 0.0),
            dog_sigma1=config.get("dog_sigma1", 1.0),
            dog_sigma2=config.get("dog_sigma2", 4.0),
            tube_interior_method=config.get("tube_interior_method", "entropy"),
            tube_interior_threshold=config.get("tube_interior_threshold", 3.0),
            tube_edge_sensitivity=config.get("tube_edge_sensitivity", 0.5),
            variance_threshold=config.get("variance_threshold", 100.0),
            edge_threshold=config.get("edge_threshold", 500.0),
            intensity_min=config.get("intensity_min", 20.0),
            intensity_max=config.get("intensity_max", 255.0),
            morphological_cleanup=config.get("morphological_cleanup", False),
            morphological_radius=config.get("morphological_radius", 1),
            invert=config.get("invert", False),
        )

        # Run analysis
        result = self._service.analyze(image, settings)

        # Convert selected tiles to DetectedObject list
        h, w = image.shape[:2]
        tile_w = w / settings.tiles_x
        tile_h = h / settings.tiles_y
        objects = []

        for i, (tx, ty) in enumerate(sorted(result.selected_tiles)):
            # Centroid in image pixel coordinates
            cx = (tx + 0.5) * tile_w
            cy = (ty + 0.5) * tile_h
            obj = DetectedObject(
                label_id=i + 1,
                centroid_voxel=(0.0, cy, cx),  # z=0 for 2D
                centroid_stage=(cx, cy, 0.0),  # image coords as stage placeholder
                bounding_box=(
                    slice(0, 1),
                    slice(int(ty * tile_h), int((ty + 1) * tile_h)),
                    slice(int(tx * tile_w), int((tx + 1) * tile_w)),
                ),
                volume_voxels=int(tile_w * tile_h),
                volume_mm3=0.0,
            )
            objects.append(obj)

        # Build mask output
        mask = np.zeros((settings.tiles_y, settings.tiles_x), dtype=bool)
        for tx, ty in result.selected_tiles:
            mask[ty, tx] = True

        # Set outputs
        self._set_output(node, context, "selected_tiles", PortType.OBJECT_LIST, objects)
        self._set_output(node, context, "count", PortType.SCALAR, result.tile_count)
        self._set_output(node, context, "mask", PortType.VOLUME, mask)

        logger.info(
            f"Overview analysis complete: {result.tile_count}/{result.total_tiles} "
            f"tiles selected using '{settings.method}' method"
        )

    @staticmethod
    def _load_image(path: str) -> np.ndarray:
        """Load an image from file path."""
        from pathlib import Path

        p = Path(path)
        if not p.exists():
            raise RuntimeError(f"Image file not found: {path}")

        suffix = p.suffix.lower()
        if suffix in (".npy",):
            return np.load(str(p))
        elif suffix in (".tif", ".tiff"):
            try:
                from tifffile import imread

                return imread(str(p))
            except ImportError:
                from PIL import Image

                return np.array(Image.open(str(p)))
        else:
            from PIL import Image

            return np.array(Image.open(str(p)))
