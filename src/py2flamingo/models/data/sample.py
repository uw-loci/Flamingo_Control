"""Sample models for specimen representation.

This module provides models for representing biological samples,
their spatial boundaries, and regions of interest.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..base import BaseModel, ValidatedModel, ValidationError
from ..hardware.stage import Position


class SampleType(Enum):
    """Types of biological samples."""

    CELL_CULTURE = "cell_culture"
    TISSUE_SECTION = "tissue_section"
    WHOLE_MOUNT = "whole_mount"
    ORGANOID = "organoid"
    EMBRYO = "embryo"
    CLEARED_TISSUE = "cleared_tissue"
    BEAD_SAMPLE = "bead_sample"  # For calibration
    OTHER = "other"


class MountingMedium(Enum):
    """Sample mounting media."""

    WATER = "water"
    PBS = "pbs"
    GLYCEROL = "glycerol"
    MOUNTING_MEDIUM = "mounting_medium"
    AGAROSE = "agarose"
    OIL = "oil"
    AIR = "air"
    CUSTOM = "custom"


class FluorophoreLabel:
    """Fluorescent label information."""

    name: str
    target: str  # What it labels (e.g., "nuclei", "actin")
    excitation_nm: float
    emission_nm: float
    concentration: Optional[str] = None  # e.g., "1:1000"


@dataclass
class SampleBounds(ValidatedModel):
    """3D bounding box of a sample in stage coordinates."""

    min_position: Position  # Minimum corner
    max_position: Position  # Maximum corner
    padding_um: float = 0.0  # Safety padding around sample

    def validate(self) -> None:
        """Validate bounds are properly defined."""
        # Check min is less than max for each axis
        if self.min_position.x > self.max_position.x:
            raise ValidationError("Min X must be less than max X")
        if self.min_position.y > self.max_position.y:
            raise ValidationError("Min Y must be less than max Y")
        if self.min_position.z > self.max_position.z:
            raise ValidationError("Min Z must be less than max Z")

        if self.padding_um < 0:
            raise ValidationError("Padding cannot be negative")

    def get_center(self) -> Position:
        """Calculate center position of bounding box.

        Returns:
            Center position
        """
        return Position(
            x=(self.min_position.x + self.max_position.x) / 2,
            y=(self.min_position.y + self.max_position.y) / 2,
            z=(self.min_position.z + self.max_position.z) / 2,
            r=(self.min_position.r + self.max_position.r) / 2,
        )

    def contains_position(self, position: Position, use_padding: bool = True) -> bool:
        """Check if position is within bounds.

        Args:
            position: Position to check
            use_padding: Whether to include padding

        Returns:
            True if position is within bounds
        """
        padding_mm = self.padding_um / 1000.0 if use_padding else 0

        return (
            self.min_position.x - padding_mm
            <= position.x
            <= self.max_position.x + padding_mm
            and self.min_position.y - padding_mm
            <= position.y
            <= self.max_position.y + padding_mm
            and self.min_position.z - padding_mm
            <= position.z
            <= self.max_position.z + padding_mm
        )


@dataclass
class SampleRegion(BaseModel):
    """Region of interest within a sample."""

    name: str
    bounds: Optional[SampleBounds] = None
    center: Optional[Position] = None
    radius_mm: Optional[float] = None  # For circular regions
    polygon_vertices: Optional[List[Position]] = None  # For polygon regions
    z_range: Optional[Tuple[float, float]] = None  # Z limits
    notes: Optional[str] = None
    color: Optional[str] = None  # For visualization
    priority: int = 0  # Acquisition priority (higher = first)

    def contains_position(self, position: Position) -> bool:
        """Check if position is within region.

        Args:
            position: Position to check

        Returns:
            True if position is in region
        """
        # Bounding box check
        if self.bounds:
            return self.bounds.contains_position(position)

        # Circular region check
        if self.center and self.radius_mm:
            distance = position.distance_to(self.center, include_rotation=False)
            return distance <= self.radius_mm

        # Polygon check (simplified - proper implementation would use point-in-polygon)
        if self.polygon_vertices:
            # Placeholder - would implement proper point-in-polygon test
            return False

        return False


@dataclass
class Sample(BaseModel):
    """Complete sample model with metadata and spatial information."""

    name: str
    sample_type: SampleType
    preparation_date: datetime
    mounting_medium: MountingMedium = MountingMedium.WATER
    coverslip_thickness_mm: float = 0.17  # Standard #1.5

    # Spatial information
    bounds: Optional[SampleBounds] = None
    regions: List[SampleRegion] = field(default_factory=list)
    reference_positions: List[Position] = field(default_factory=list)

    # Biological information
    organism: Optional[str] = None
    tissue: Optional[str] = None
    cell_type: Optional[str] = None
    treatment: Optional[str] = None
    age: Optional[str] = None  # e.g., "3 days", "adult"
    genotype: Optional[str] = None

    # Labeling
    fluorophores: List[FluorophoreLabel] = field(default_factory=list)
    staining_protocol: Optional[str] = None
    fixation_method: Optional[str] = None

    # Experimental conditions
    temperature_c: Optional[float] = None
    co2_percent: Optional[float] = None
    humidity_percent: Optional[float] = None
    culture_medium: Optional[str] = None

    # Metadata
    experimenter: Optional[str] = None
    project: Optional[str] = None
    protocol_id: Optional[str] = None
    notes: Optional[str] = None
    storage_location: Optional[str] = None

    # Quality metrics
    viability_percent: Optional[float] = None
    contamination: bool = False
    quality_score: Optional[int] = None  # 1-10 scale

    @classmethod
    def create_calibration_sample(cls) -> "Sample":
        """Create a standard calibration sample.

        Returns:
            Sample configured for calibration
        """
        return cls(
            name="Calibration Beads",
            sample_type=SampleType.BEAD_SAMPLE,
            preparation_date=datetime.now(),
            mounting_medium=MountingMedium.WATER,
            notes="Fluorescent calibration beads for system alignment",
        )
