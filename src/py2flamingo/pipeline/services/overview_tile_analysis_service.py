"""
OverviewTileAnalysisService — programmatic 2D overview tile analysis.

Extracted as a reusable service so that the same analysis logic can be
used by both the interactive OverviewThresholderDialog and the pipeline
OVERVIEW_ANALYSIS node runner.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TileAnalysisSettings:
    """Settings for tile analysis.

    Attributes:
        method: Detection method key ("entropy", "bandpass", "gradient",
                "dog", "tube_detect", "variance", "edge", "intensity", "combined")
        tiles_x: Number of tiles in X
        tiles_y: Number of tiles in Y
    """

    method: str = "entropy"
    tiles_x: int = 1
    tiles_y: int = 1
    # Entropy
    entropy_threshold: float = 3.0
    smoothing: bool = True
    # Band-pass
    bp_var_min: float = 0.0
    bp_var_max: float = 1000.0
    bp_entropy_min: float = 2.0
    # Gradient orientation
    gradient_threshold: float = 0.5
    # DoG
    dog_threshold: float = 0.0
    dog_sigma1: float = 1.0
    dog_sigma2: float = 4.0
    # Tube detection
    tube_interior_method: str = "entropy"
    tube_interior_threshold: float = 3.0
    tube_edge_sensitivity: float = 0.5
    # Variance / Edge / Intensity / Combined
    variance_threshold: float = 100.0
    edge_threshold: float = 500.0
    intensity_min: float = 20.0
    intensity_max: float = 255.0
    # Post-processing
    morphological_cleanup: bool = False
    morphological_radius: int = 1
    invert: bool = False


@dataclass
class TileAnalysisResult:
    """Output of tile analysis.

    Attributes:
        selected_tiles: Set of (tx, ty) tuples of selected tiles
        tile_count: Number of selected tiles
        total_tiles: Total number of tiles
        metrics: Dict mapping metric name to [tiles_y, tiles_x] array
    """

    selected_tiles: Set[Tuple[int, int]] = field(default_factory=set)
    tile_count: int = 0
    total_tiles: int = 0
    metrics: Dict[str, np.ndarray] = field(default_factory=dict)


class OverviewTileAnalysisService:
    """Reusable service for 2D overview tile analysis.

    Computes per-tile metrics and applies detection methods to classify
    tiles as sample vs background.
    """

    def analyze(
        self, image: np.ndarray, settings: TileAnalysisSettings
    ) -> TileAnalysisResult:
        """Run tile analysis with the given method and settings.

        Args:
            image: 2D image as numpy array (grayscale or RGB)
            settings: Analysis settings

        Returns:
            TileAnalysisResult with selected tiles and metrics
        """
        from py2flamingo.views.dialogs.overview_thresholder_dialog import (
            calculate_tile_dog_variance,
            calculate_tile_edges,
            calculate_tile_entropy,
            calculate_tile_gradient_anisotropy,
            calculate_tile_intensity,
            calculate_tile_variance,
        )

        tiles_x = settings.tiles_x
        tiles_y = settings.tiles_y
        method = settings.method

        metrics: Dict[str, np.ndarray] = {}
        selected: Set[Tuple[int, int]] = set()

        # Compute required metrics based on method
        if method in ("entropy", "bandpass", "tube_detect"):
            metrics["entropy"] = calculate_tile_entropy(image, tiles_x, tiles_y)
            if settings.smoothing:
                from scipy.ndimage import gaussian_filter

                metrics["entropy_smoothed"] = gaussian_filter(
                    metrics["entropy"], sigma=1.5
                )

        if method in ("variance", "bandpass", "combined", "tube_detect"):
            metrics["variance"] = calculate_tile_variance(image, tiles_x, tiles_y)

        if method in ("edge", "combined"):
            metrics["edge"] = calculate_tile_edges(image, tiles_x, tiles_y)

        if method == "intensity":
            metrics["intensity"] = calculate_tile_intensity(image, tiles_x, tiles_y)

        if method == "gradient":
            metrics["gradient"] = calculate_tile_gradient_anisotropy(
                image, tiles_x, tiles_y
            )

        if method == "dog":
            metrics["dog"] = calculate_tile_dog_variance(
                image, tiles_x, tiles_y, settings.dog_sigma1, settings.dog_sigma2
            )

        # Apply method
        if method == "entropy":
            scores = (
                metrics.get("entropy_smoothed", metrics.get("entropy"))
                if settings.smoothing
                else metrics.get("entropy")
            )
            if scores is not None:
                for ty in range(tiles_y):
                    for tx in range(tiles_x):
                        if scores[ty, tx] >= settings.entropy_threshold:
                            selected.add((tx, ty))

        elif method == "bandpass":
            variances = metrics.get("variance")
            entropies = metrics.get("entropy")
            if variances is not None and entropies is not None:
                for ty in range(tiles_y):
                    for tx in range(tiles_x):
                        v = variances[ty, tx]
                        e = entropies[ty, tx]
                        if (
                            settings.bp_var_min <= v <= settings.bp_var_max
                            and e >= settings.bp_entropy_min
                        ):
                            selected.add((tx, ty))

        elif method == "gradient":
            aniso = metrics.get("gradient")
            if aniso is not None:
                for ty in range(tiles_y):
                    for tx in range(tiles_x):
                        if aniso[ty, tx] <= settings.gradient_threshold:
                            selected.add((tx, ty))

        elif method == "dog":
            dog_var = metrics.get("dog")
            if dog_var is not None:
                for ty in range(tiles_y):
                    for tx in range(tiles_x):
                        if dog_var[ty, tx] >= settings.dog_threshold:
                            selected.add((tx, ty))

        elif method == "tube_detect":
            selected = self._detect_tube(image, settings, metrics)

        elif method == "variance":
            variances = metrics.get("variance")
            if variances is not None:
                for ty in range(tiles_y):
                    for tx in range(tiles_x):
                        if variances[ty, tx] >= settings.variance_threshold:
                            selected.add((tx, ty))

        elif method == "edge":
            edges = metrics.get("edge")
            if edges is not None:
                for ty in range(tiles_y):
                    for tx in range(tiles_x):
                        if edges[ty, tx] >= settings.edge_threshold:
                            selected.add((tx, ty))

        elif method == "intensity":
            intens = metrics.get("intensity")
            if intens is not None:
                for ty in range(tiles_y):
                    for tx in range(tiles_x):
                        if (
                            settings.intensity_min
                            <= intens[ty, tx]
                            <= settings.intensity_max
                        ):
                            selected.add((tx, ty))

        elif method == "combined":
            variances = metrics.get("variance")
            edges = metrics.get("edge")
            if variances is not None and edges is not None:
                for ty in range(tiles_y):
                    for tx in range(tiles_x):
                        if (
                            variances[ty, tx] >= settings.variance_threshold
                            or edges[ty, tx] >= settings.edge_threshold
                        ):
                            selected.add((tx, ty))

        # Morphological cleanup
        if settings.morphological_cleanup and selected:
            selected = self._morphological_cleanup(
                selected, tiles_x, tiles_y, settings.morphological_radius
            )

        # Inversion
        if settings.invert:
            all_tiles = {(tx, ty) for tx in range(tiles_x) for ty in range(tiles_y)}
            selected = all_tiles - selected

        return TileAnalysisResult(
            selected_tiles=selected,
            tile_count=len(selected),
            total_tiles=tiles_x * tiles_y,
            metrics=metrics,
        )

    def compute_all_metrics(
        self, image: np.ndarray, tiles_x: int, tiles_y: int
    ) -> Dict[str, np.ndarray]:
        """Compute all available per-tile metrics.

        Args:
            image: 2D image
            tiles_x: Number of tiles in X
            tiles_y: Number of tiles in Y

        Returns:
            Dict mapping metric name to [tiles_y, tiles_x] array
        """
        from py2flamingo.views.dialogs.overview_thresholder_dialog import (
            calculate_tile_dog_variance,
            calculate_tile_edges,
            calculate_tile_entropy,
            calculate_tile_gradient_anisotropy,
            calculate_tile_intensity,
            calculate_tile_variance,
        )

        metrics = {}
        metrics["variance"] = calculate_tile_variance(image, tiles_x, tiles_y)
        metrics["edge"] = calculate_tile_edges(image, tiles_x, tiles_y)
        metrics["intensity"] = calculate_tile_intensity(image, tiles_x, tiles_y)
        metrics["entropy"] = calculate_tile_entropy(image, tiles_x, tiles_y)
        metrics["gradient"] = calculate_tile_gradient_anisotropy(
            image, tiles_x, tiles_y
        )
        metrics["dog"] = calculate_tile_dog_variance(image, tiles_x, tiles_y)
        return metrics

    def _detect_tube(
        self,
        image: np.ndarray,
        settings: TileAnalysisSettings,
        metrics: Dict[str, np.ndarray],
    ) -> Set[Tuple[int, int]]:
        """Two-stage tube detection."""
        from scipy.ndimage import gaussian_filter

        if len(image.shape) == 3:
            gray = np.mean(image, axis=2).astype(np.float64)
        else:
            gray = image.astype(np.float64)

        h, w = gray.shape
        tiles_x = settings.tiles_x
        tiles_y = settings.tiles_y
        tile_w = w // tiles_x

        # Column-wise mean intensity profile
        col_profile = np.mean(gray, axis=0)
        smoothed = gaussian_filter(col_profile, sigma=max(tile_w * 0.5, 5))
        gradient = np.gradient(smoothed)
        grad_abs = np.abs(gradient)

        sensitivity = settings.tube_edge_sensitivity
        grad_threshold = np.percentile(grad_abs, 95) * (1.0 - sensitivity * 0.8)
        grad_threshold = max(grad_threshold, np.std(grad_abs) * 0.5)
        edge_positions = np.where(grad_abs > grad_threshold)[0]

        if len(edge_positions) < 2:
            return {(tx, ty) for tx in range(tiles_x) for ty in range(tiles_y)}

        left_tile_col = edge_positions[0] // tile_w
        right_tile_col = min(edge_positions[-1] // tile_w, tiles_x - 1)

        selected = set()
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                if tx < left_tile_col or tx > right_tile_col:
                    continue

                if settings.tube_interior_method == "entropy":
                    scores = metrics.get("entropy")
                    if (
                        scores is not None
                        and scores[ty, tx] >= settings.tube_interior_threshold
                    ):
                        selected.add((tx, ty))
                elif settings.tube_interior_method == "variance":
                    scores = metrics.get("variance")
                    if (
                        scores is not None
                        and scores[ty, tx] >= settings.tube_interior_threshold
                    ):
                        selected.add((tx, ty))

        return selected

    @staticmethod
    def _morphological_cleanup(
        selected: Set[Tuple[int, int]],
        tiles_x: int,
        tiles_y: int,
        radius: int,
    ) -> Set[Tuple[int, int]]:
        """Apply morphological closing then opening."""
        from scipy.ndimage import binary_closing, binary_opening

        mask = np.zeros((tiles_y, tiles_x), dtype=bool)
        for tx, ty in selected:
            if 0 <= ty < tiles_y and 0 <= tx < tiles_x:
                mask[ty, tx] = True

        mask = binary_closing(mask, iterations=radius)
        mask = binary_opening(mask, iterations=radius)

        result = set()
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                if mask[ty, tx]:
                    result.add((tx, ty))
        return result
