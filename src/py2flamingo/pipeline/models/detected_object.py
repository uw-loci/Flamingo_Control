"""
DetectedObject — per-connected-component properties from threshold analysis.

Produced by ThresholdAnalysisService when analyzing thresholded volumes.
Each instance represents one spatially connected region in the mask.
"""

from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any


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
    """
    label_id: int
    centroid_voxel: Tuple[float, float, float]       # (z, y, x)
    centroid_stage: Tuple[float, float, float]        # (x, y, z) in mm
    bounding_box: Tuple[slice, slice, slice]           # (z, y, x) slices
    volume_voxels: int
    volume_mm3: float
    source_channel: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            'label_id': self.label_id,
            'centroid_voxel': list(self.centroid_voxel),
            'centroid_stage': list(self.centroid_stage),
            'bounding_box': [
                [s.start, s.stop] for s in self.bounding_box
            ],
            'volume_voxels': self.volume_voxels,
            'volume_mm3': self.volume_mm3,
            'source_channel': self.source_channel,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'DetectedObject':
        """Deserialize from dict."""
        bb = d['bounding_box']
        return cls(
            label_id=d['label_id'],
            centroid_voxel=tuple(d['centroid_voxel']),
            centroid_stage=tuple(d['centroid_stage']),
            bounding_box=tuple(slice(s[0], s[1]) for s in bb),
            volume_voxels=d['volume_voxels'],
            volume_mm3=d['volume_mm3'],
            source_channel=d.get('source_channel'),
        )
