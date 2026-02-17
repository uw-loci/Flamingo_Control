"""
ThresholdAnalysisService — programmatic threshold + connected-component analysis.

Extracted from UnionThresholderDialog._recompute_mask() so that the same
pipeline (smooth → threshold → union → opening → size filter → object extraction)
can be used both by the interactive dialog and by pipeline ThresholdRunner nodes.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Callable

import numpy as np
from scipy import ndimage

from py2flamingo.pipeline.models.detected_object import DetectedObject

logger = logging.getLogger(__name__)


@dataclass
class ThresholdSettings:
    """Settings for the threshold analysis pipeline.

    Attributes:
        channel_thresholds: Map of channel_id -> threshold value (skip if <= 0)
        gauss_sigma: Gaussian smoothing sigma (0 = no smoothing)
        opening_enabled: Whether to apply morphological opening
        opening_radius: Radius for opening structuring element
        min_object_size: Minimum connected-component size in voxels (0 = no filter)
    """
    channel_thresholds: Dict[int, float] = field(default_factory=dict)
    gauss_sigma: float = 0.0
    opening_enabled: bool = False
    opening_radius: int = 1
    min_object_size: int = 0


@dataclass
class ThresholdResult:
    """Output of threshold analysis.

    Attributes:
        combined_mask: Boolean 3D array (union of all channels)
        labels: Integer 3D array with per-channel labels
        objects: List of detected connected components
        object_count: Number of detected objects
    """
    combined_mask: Optional[np.ndarray] = None
    labels: Optional[np.ndarray] = None
    objects: List[DetectedObject] = field(default_factory=list)
    object_count: int = 0


class ThresholdAnalysisService:
    """Runs the threshold + analysis pipeline on 3D volumes.

    The pipeline per channel:
      1. Gaussian smooth (if sigma > 0)
      2. Threshold → boolean mask
      3. Assign per-channel label (ch_id + 1)

    Post-union:
      4. Morphological opening (if enabled)
      5. Remove small objects (if min_size > 0)
      6. Connected component extraction → DetectedObject instances
    """

    def analyze(
        self,
        volumes: Dict[int, np.ndarray],
        settings: ThresholdSettings,
        voxel_size_um: Tuple[float, float, float] = (50.0, 50.0, 50.0),
        voxel_to_stage_fn: Optional[Callable] = None,
    ) -> ThresholdResult:
        """Run threshold analysis on one or more channel volumes.

        Args:
            volumes: Map of channel_id -> 3D numpy array
            settings: Threshold and filtering parameters
            voxel_size_um: Voxel dimensions in micrometers (z, y, x)
            voxel_to_stage_fn: Optional function(z, y, x) -> (stage_x, stage_y, stage_z)
                               for converting voxel centroids to stage coordinates.
                               If None, centroid_stage is set to (0, 0, 0).

        Returns:
            ThresholdResult with mask, labels, and detected objects
        """
        combined: Optional[np.ndarray] = None
        labels: Optional[np.ndarray] = None

        # --- Per-channel threshold ---
        for ch_id, threshold in settings.channel_thresholds.items():
            if threshold <= 0:
                continue
            vol = volumes.get(ch_id)
            if vol is None:
                logger.warning(f"No volume for channel {ch_id}, skipping")
                continue

            # Gaussian smoothing
            if settings.gauss_sigma > 0:
                vol = ndimage.gaussian_filter(
                    vol.astype(np.float32), sigma=settings.gauss_sigma, truncate=3.0
                )

            ch_mask = vol >= threshold

            if combined is None:
                combined = ch_mask
                labels = np.zeros(ch_mask.shape, dtype=np.int32)
            else:
                if combined.shape != ch_mask.shape:
                    logger.warning(
                        f"Shape mismatch ch {ch_id}: {ch_mask.shape} vs {combined.shape}"
                    )
                    continue
                combined = combined | ch_mask

            # Per-channel label (ch_id + 1); later channels overwrite in overlap
            labels[ch_mask] = ch_id + 1

        # --- Post-union processing ---
        if combined is None or not combined.any():
            return ThresholdResult()

        # Morphological opening
        if settings.opening_enabled:
            struct = ndimage.generate_binary_structure(3, 1)
            struct = ndimage.iterate_structure(struct, settings.opening_radius)
            combined = ndimage.binary_opening(combined, structure=struct)

        # Remove small objects
        if settings.min_object_size > 0:
            labeled_arr, num_features = ndimage.label(combined)
            if num_features > 0:
                comp_sizes = np.bincount(labeled_arr.ravel())
                small_labels = np.where(comp_sizes < settings.min_object_size)[0]
                small_labels = small_labels[small_labels > 0]
                if small_labels.size > 0:
                    remove_mask = np.isin(labeled_arr, small_labels)
                    combined[remove_mask] = False

        # Zero out labels wherever combined mask became False
        if labels is not None:
            labels[~combined] = 0

        # --- Connected component extraction ---
        objects = self._extract_objects(
            combined, labels, voxel_size_um, voxel_to_stage_fn
        )

        return ThresholdResult(
            combined_mask=combined,
            labels=labels,
            objects=objects,
            object_count=len(objects),
        )

    def _extract_objects(
        self,
        mask: np.ndarray,
        labels: np.ndarray,
        voxel_size_um: Tuple[float, float, float],
        voxel_to_stage_fn: Optional[Callable],
    ) -> List[DetectedObject]:
        """Extract per-component DetectedObject instances from the mask.

        Uses ndimage.label() to identify connected components, then
        ndimage.find_objects() to get bounding boxes and ndimage.center_of_mass()
        for centroids.
        """
        labeled_arr, num_features = ndimage.label(mask)
        if num_features == 0:
            return []

        slices = ndimage.find_objects(labeled_arr)
        centroids = ndimage.center_of_mass(mask, labeled_arr, range(1, num_features + 1))

        # Voxel volume in mm³
        vz, vy, vx = voxel_size_um
        voxel_vol_mm3 = (vz / 1000.0) * (vy / 1000.0) * (vx / 1000.0)

        objects: List[DetectedObject] = []
        for i in range(num_features):
            label_id = i + 1
            bb = slices[i]
            if bb is None:
                continue

            centroid_voxel = centroids[i]  # (z, y, x)
            volume_voxels = int(np.sum(labeled_arr[bb] == label_id))

            # Convert centroid to stage coordinates
            if voxel_to_stage_fn:
                centroid_stage = voxel_to_stage_fn(*centroid_voxel)
            else:
                centroid_stage = (0.0, 0.0, 0.0)

            # Determine which channel contributed most voxels
            source_channel = None
            if labels is not None:
                region_labels = labels[bb][labeled_arr[bb] == label_id]
                if region_labels.size > 0:
                    counts = np.bincount(region_labels[region_labels > 0])
                    if counts.size > 0:
                        source_channel = int(np.argmax(counts))

            obj = DetectedObject(
                label_id=label_id,
                centroid_voxel=tuple(float(c) for c in centroid_voxel),
                centroid_stage=tuple(float(c) for c in centroid_stage),
                bounding_box=bb,
                volume_voxels=volume_voxels,
                volume_mm3=volume_voxels * voxel_vol_mm3,
                source_channel=source_channel,
            )
            objects.append(obj)

        logger.info(f"Extracted {len(objects)} objects from threshold mask")
        return objects
