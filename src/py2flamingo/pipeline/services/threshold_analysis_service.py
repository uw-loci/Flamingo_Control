"""
ThresholdAnalysisService — programmatic threshold + connected-component analysis.

Extracted from UnionThresholderDialog._recompute_mask() so that the same
pipeline (smooth → threshold → union → opening → size filter → object extraction)
can be used both by the interactive dialog and by pipeline ThresholdRunner nodes.

GPU-accelerated when CuPy is available (gaussian filter, morphological opening,
connected component labeling).  Falls back transparently to CPU (scipy.ndimage).
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from scipy import ndimage

from py2flamingo.pipeline.models.detected_object import DetectedObject
from py2flamingo.visualization.gpu_transforms import (
    binary_opening_auto,
    gaussian_filter_auto,
    generate_binary_structure_auto,
    label_auto,
)

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
      1. Gaussian smooth (if sigma > 0)  — GPU-accelerated
      2. Threshold → boolean mask
      3. Assign per-channel label (ch_id + 1)

    Post-union:
      4. Morphological opening (if enabled)  — GPU-accelerated
      5. Remove small objects (if min_size > 0)  — GPU-accelerated labeling
      6. Connected component extraction → DetectedObject instances
         with intensity stats, surface area, sphericity, elongation
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

        # Keep original (pre-smoothing) volumes for intensity feature extraction
        original_volumes = volumes

        # --- Per-channel threshold ---
        for ch_id, threshold in settings.channel_thresholds.items():
            if threshold <= 0:
                continue
            vol = volumes.get(ch_id)
            if vol is None:
                logger.warning(f"No volume for channel {ch_id}, skipping")
                continue

            # Gaussian smoothing (GPU-accelerated)
            if settings.gauss_sigma > 0:
                vol = gaussian_filter_auto(
                    vol.astype(np.float32), sigma=settings.gauss_sigma
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

        # Morphological opening (GPU-accelerated)
        if settings.opening_enabled:
            struct = generate_binary_structure_auto(3, 1)
            struct = ndimage.iterate_structure(struct, settings.opening_radius)
            combined = binary_opening_auto(combined, structure=struct)

        # Remove small objects (GPU-accelerated labeling)
        if settings.min_object_size > 0:
            labeled_arr, num_features = label_auto(combined)
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

        # --- Connected component extraction with feature extraction ---
        objects = self._extract_objects(
            combined, labels, voxel_size_um, voxel_to_stage_fn, original_volumes
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
        volumes: Optional[Dict[int, np.ndarray]] = None,
    ) -> List[DetectedObject]:
        """Extract per-component DetectedObject instances from the mask.

        Uses GPU-accelerated labeling where beneficial, then extracts per-object
        features including intensity statistics, surface area, sphericity, and
        elongation via principal axis analysis.
        """
        # GPU-accelerated connected component labeling
        labeled_arr, num_features = label_auto(mask)
        if num_features == 0:
            return []

        slices = ndimage.find_objects(labeled_arr)
        centroids = ndimage.center_of_mass(
            mask, labeled_arr, range(1, num_features + 1)
        )

        # Voxel volume in mm³
        vz, vy, vx = voxel_size_um
        voxel_vol_mm3 = (vz / 1000.0) * (vy / 1000.0) * (vx / 1000.0)

        # Pick a reference volume for intensity features
        ref_volume = None
        if volumes:
            # Use the first available channel volume
            for vol in volumes.values():
                if vol is not None:
                    ref_volume = vol
                    break

        objects: List[DetectedObject] = []
        for i in range(num_features):
            label_id = i + 1
            bb = slices[i]
            if bb is None:
                continue

            centroid_voxel = centroids[i]  # (z, y, x)
            region_mask = labeled_arr[bb] == label_id
            volume_voxels = int(np.sum(region_mask))

            # Convert centroid to stage coordinates
            if voxel_to_stage_fn:
                centroid_stage = voxel_to_stage_fn(*centroid_voxel)
            else:
                centroid_stage = (0.0, 0.0, 0.0)

            # Determine which channel contributed most voxels
            source_channel = None
            if labels is not None:
                region_labels = labels[bb][region_mask]
                if region_labels.size > 0:
                    counts = np.bincount(region_labels[region_labels > 0])
                    if counts.size > 0:
                        source_channel = int(np.argmax(counts))

            # --- Intensity features ---
            mean_intensity = None
            max_intensity = None
            min_intensity = None
            std_intensity = None

            if ref_volume is not None and ref_volume.shape == mask.shape:
                region_intensities = ref_volume[bb][region_mask]
                if region_intensities.size > 0:
                    mean_intensity = float(np.mean(region_intensities))
                    max_intensity = float(np.max(region_intensities))
                    min_intensity = float(np.min(region_intensities))
                    std_intensity = float(np.std(region_intensities))

            # --- Morphology features ---
            surface_area_voxels = None
            sphericity = None
            elongation = None
            principal_axis_lengths = None

            if volume_voxels >= 8:  # Need minimum size for meaningful features
                surface_area_voxels, sphericity = _compute_surface_sphericity(
                    region_mask, volume_voxels
                )
                principal_axis_lengths, elongation = _compute_principal_axes(
                    region_mask, voxel_size_um
                )

            obj = DetectedObject(
                label_id=label_id,
                centroid_voxel=tuple(float(c) for c in centroid_voxel),
                centroid_stage=tuple(float(c) for c in centroid_stage),
                bounding_box=bb,
                volume_voxels=volume_voxels,
                volume_mm3=volume_voxels * voxel_vol_mm3,
                source_channel=source_channel,
                mean_intensity=mean_intensity,
                max_intensity=max_intensity,
                min_intensity=min_intensity,
                std_intensity=std_intensity,
                surface_area_voxels=surface_area_voxels,
                sphericity=sphericity,
                elongation=elongation,
                principal_axis_lengths=principal_axis_lengths,
            )
            objects.append(obj)

        logger.info(f"Extracted {len(objects)} objects from threshold mask")
        return objects


def _compute_surface_sphericity(
    region_mask: np.ndarray, volume_voxels: int
) -> Tuple[int, float]:
    """Compute surface area (boundary voxels) and sphericity.

    Surface voxels = mask voxels that have at least one non-mask neighbor
    (6-connectivity).  Sphericity = ratio of equivalent-sphere surface area
    to actual surface area.

    Returns:
        (surface_area_voxels, sphericity)
    """
    # Erode by 1 voxel (6-connectivity), boundary = mask minus interior
    eroded = ndimage.binary_erosion(region_mask)
    boundary = region_mask & ~eroded
    surface_voxels = int(np.sum(boundary))

    if surface_voxels == 0:
        return volume_voxels, 0.0

    # Sphericity: SA_sphere / SA_actual where SA_sphere corresponds to same volume
    # SA_sphere = (pi^(1/3)) * (6V)^(2/3)
    # For voxelized: use face-count approximation = 6V - 2*adjacencies, but
    # boundary voxel count is a reasonable proxy for our resolution.
    v = float(volume_voxels)
    sa_equiv_sphere = (np.pi ** (1.0 / 3.0)) * ((6.0 * v) ** (2.0 / 3.0))
    sphericity = min(sa_equiv_sphere / float(surface_voxels), 1.0)

    return surface_voxels, float(sphericity)


def _compute_principal_axes(
    region_mask: np.ndarray,
    voxel_size_um: Tuple[float, float, float],
) -> Tuple[Optional[Tuple[float, float, float]], Optional[float]]:
    """Compute principal axis lengths and elongation from inertia tensor.

    Uses eigenvalues of the covariance matrix of voxel positions (scaled by
    voxel size) to determine 3D shape orientation and elongation.

    Returns:
        (principal_axis_lengths, elongation)  or  (None, None) if degenerate.
    """
    coords = np.argwhere(region_mask)  # (N, 3) in (z, y, x) voxel indices
    if coords.shape[0] < 4:
        return None, None

    # Scale to physical units (micrometers)
    vz, vy, vx = voxel_size_um
    scaled = coords.astype(np.float64)
    scaled[:, 0] *= vz
    scaled[:, 1] *= vy
    scaled[:, 2] *= vx

    # Covariance matrix → eigenvalues = variance along principal axes
    cov = np.cov(scaled, rowvar=False)
    try:
        eigenvalues = np.linalg.eigvalsh(cov)
    except np.linalg.LinAlgError:
        return None, None

    # Eigenvalues are in ascending order; convert variance → "length" (2*sqrt)
    eigenvalues = np.maximum(eigenvalues, 0.0)
    axis_lengths = 2.0 * np.sqrt(eigenvalues)  # ascending: minor, mid, major

    # Return in descending order: (major, mid, minor)
    major, mid, minor = axis_lengths[2], axis_lengths[1], axis_lengths[0]

    elongation = float(major / minor) if minor > 1e-6 else float("inf")

    return (float(major), float(mid), float(minor)), elongation
