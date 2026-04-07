"""
DetectedObject — per-connected-component properties from threshold analysis.

Produced by ThresholdAnalysisService when analyzing thresholded volumes.
Each instance represents one spatially connected region in the mask.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass
class DetectedObject:
    """A single detected object (connected component) from threshold analysis.

    Attributes:
        label_id: Unique integer label from ndimage.label()
        centroid_voxel: Center of mass in voxel coords (z, y, x)
        centroid_stage: Center of mass in stage coords (x, y, z) in mm
        bounding_box: Axis-aligned bounding box as (z_slice, y_slice, x_slice)
        volume_voxels: Number of voxels in this object
        volume_mm3: Physical volume in cubic millimeters
        source_channel: Channel ID that contributed most voxels (optional)
        mean_intensity: Mean intensity within the object mask
        max_intensity: Maximum intensity within the object mask
        min_intensity: Minimum intensity within the object mask
        std_intensity: Standard deviation of intensity within the object mask
        surface_area_voxels: Number of boundary voxels (surface estimate)
        sphericity: 0-1 measure of how spherical the object is
        elongation: Ratio of longest to shortest principal axis (>=1)
        principal_axis_lengths: (major, mid, minor) axis lengths in voxels
    """

    label_id: int
    centroid_voxel: Tuple[float, float, float]  # (z, y, x)
    centroid_stage: Tuple[float, float, float]  # (x, y, z) in mm
    bounding_box: Tuple[slice, slice, slice]  # (z, y, x) slices
    volume_voxels: int
    volume_mm3: float
    source_channel: Optional[int] = None

    # Intensity features (from original pre-threshold volume)
    mean_intensity: Optional[float] = None
    max_intensity: Optional[float] = None
    min_intensity: Optional[float] = None
    std_intensity: Optional[float] = None

    # Morphology features
    surface_area_voxels: Optional[int] = None
    sphericity: Optional[float] = None
    elongation: Optional[float] = None
    principal_axis_lengths: Optional[Tuple[float, float, float]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        d: Dict[str, Any] = {
            "label_id": self.label_id,
            "centroid_voxel": list(self.centroid_voxel),
            "centroid_stage": list(self.centroid_stage),
            "bounding_box": [[s.start, s.stop] for s in self.bounding_box],
            "volume_voxels": self.volume_voxels,
            "volume_mm3": self.volume_mm3,
            "source_channel": self.source_channel,
        }
        # Optional intensity features
        if self.mean_intensity is not None:
            d["mean_intensity"] = self.mean_intensity
        if self.max_intensity is not None:
            d["max_intensity"] = self.max_intensity
        if self.min_intensity is not None:
            d["min_intensity"] = self.min_intensity
        if self.std_intensity is not None:
            d["std_intensity"] = self.std_intensity
        # Optional morphology features
        if self.surface_area_voxels is not None:
            d["surface_area_voxels"] = self.surface_area_voxels
        if self.sphericity is not None:
            d["sphericity"] = self.sphericity
        if self.elongation is not None:
            d["elongation"] = self.elongation
        if self.principal_axis_lengths is not None:
            d["principal_axis_lengths"] = list(self.principal_axis_lengths)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DetectedObject":
        """Deserialize from dict."""
        bb = d["bounding_box"]
        pal = d.get("principal_axis_lengths")
        return cls(
            label_id=d["label_id"],
            centroid_voxel=tuple(d["centroid_voxel"]),
            centroid_stage=tuple(d["centroid_stage"]),
            bounding_box=tuple(slice(s[0], s[1]) for s in bb),
            volume_voxels=d["volume_voxels"],
            volume_mm3=d["volume_mm3"],
            source_channel=d.get("source_channel"),
            mean_intensity=d.get("mean_intensity"),
            max_intensity=d.get("max_intensity"),
            min_intensity=d.get("min_intensity"),
            std_intensity=d.get("std_intensity"),
            surface_area_voxels=d.get("surface_area_voxels"),
            sphericity=d.get("sphericity"),
            elongation=d.get("elongation"),
            principal_axis_lengths=tuple(pal) if pal is not None else None,
        )

    def bounding_box_mm(
        self,
        voxel_size_um: Tuple[float, float, float],
        z_range_mm: Tuple[float, float],
        y_range_mm: Tuple[float, float],
        x_range_mm: Tuple[float, float],
        invert_x: bool = False,
    ) -> Dict[str, float]:
        """Convert bounding box slices to stage coordinate ranges.

        Uses the project's coordinate conventions:
        - Y inverted (napari 0=top → stage y_range[1])
        - X optionally inverted

        Args:
            voxel_size_um: (z, y, x) voxel dimensions in micrometers
            z_range_mm: (z_min, z_max) stage Z range
            y_range_mm: (y_min, y_max) stage Y range
            x_range_mm: (x_min, x_max) stage X range
            invert_x: Whether X axis is inverted

        Returns:
            dict with x_min, x_max, y_min, y_max, z_min, z_max (mm)
        """
        z_sl, y_sl, x_sl = self.bounding_box
        vz, vy, vx = voxel_size_um

        z_min = z_range_mm[0] + z_sl.start * vz / 1000.0
        z_max = z_range_mm[0] + z_sl.stop * vz / 1000.0

        # Y inverted: high voxel index → low stage Y
        y_min = y_range_mm[1] - y_sl.stop * vy / 1000.0
        y_max = y_range_mm[1] - y_sl.start * vy / 1000.0

        if invert_x:
            x_min = x_range_mm[1] - x_sl.stop * vx / 1000.0
            x_max = x_range_mm[1] - x_sl.start * vx / 1000.0
        else:
            x_min = x_range_mm[0] + x_sl.start * vx / 1000.0
            x_max = x_range_mm[0] + x_sl.stop * vx / 1000.0

        return {
            "x_min": x_min,
            "x_max": x_max,
            "y_min": y_min,
            "y_max": y_max,
            "z_min": z_min,
            "z_max": z_max,
        }

    def extent_mm(
        self, voxel_size_um: Tuple[float, float, float]
    ) -> Tuple[float, float, float]:
        """Size of bounding box in (x, y, z) mm.

        Args:
            voxel_size_um: (z, y, x) voxel dimensions in micrometers

        Returns:
            (x_extent, y_extent, z_extent) in mm
        """
        z_sl, y_sl, x_sl = self.bounding_box
        vz, vy, vx = voxel_size_um
        return (
            (x_sl.stop - x_sl.start) * vx / 1000.0,
            (y_sl.stop - y_sl.start) * vy / 1000.0,
            (z_sl.stop - z_sl.start) * vz / 1000.0,
        )
