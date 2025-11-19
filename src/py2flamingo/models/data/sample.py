"""Sample models for specimen representation.

This module provides models for representing biological samples,
their spatial boundaries, and regions of interest.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime
from enum import Enum
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

    def is_compatible_with_laser(self, laser_wavelength_nm: float,
                                tolerance_nm: float = 20.0) -> bool:
        """Check if fluorophore is excitable by laser.

        Args:
            laser_wavelength_nm: Laser wavelength
            tolerance_nm: Tolerance range

        Returns:
            True if laser can excite fluorophore
        """
        return abs(laser_wavelength_nm - self.excitation_nm) <= tolerance_nm


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
            r=(self.min_position.r + self.max_position.r) / 2
        )

    def get_dimensions(self) -> Tuple[float, float, float]:
        """Calculate dimensions of bounding box.

        Returns:
            Tuple of (width, height, depth) in mm
        """
        return (
            self.max_position.x - self.min_position.x,
            self.max_position.y - self.min_position.y,
            self.max_position.z - self.min_position.z
        )

    def get_volume(self) -> float:
        """Calculate volume of bounding box.

        Returns:
            Volume in cubic millimeters
        """
        width, height, depth = self.get_dimensions()
        return width * height * depth

    def contains_position(self, position: Position,
                         use_padding: bool = True) -> bool:
        """Check if position is within bounds.

        Args:
            position: Position to check
            use_padding: Whether to include padding

        Returns:
            True if position is within bounds
        """
        padding_mm = self.padding_um / 1000.0 if use_padding else 0

        return (
            self.min_position.x - padding_mm <= position.x <= self.max_position.x + padding_mm and
            self.min_position.y - padding_mm <= position.y <= self.max_position.y + padding_mm and
            self.min_position.z - padding_mm <= position.z <= self.max_position.z + padding_mm
        )

    def expand_to_include(self, position: Position) -> 'SampleBounds':
        """Create expanded bounds to include a position.

        Args:
            position: Position to include

        Returns:
            New SampleBounds including the position
        """
        return SampleBounds(
            min_position=Position(
                x=min(self.min_position.x, position.x),
                y=min(self.min_position.y, position.y),
                z=min(self.min_position.z, position.z),
                r=self.min_position.r
            ),
            max_position=Position(
                x=max(self.max_position.x, position.x),
                y=max(self.max_position.y, position.y),
                z=max(self.max_position.z, position.z),
                r=self.max_position.r
            ),
            padding_um=self.padding_um
        )

    def get_grid_positions(self, spacing_mm: float,
                          z_plane: Optional[float] = None) -> List[Position]:
        """Generate grid of positions within bounds.

        Args:
            spacing_mm: Grid spacing in millimeters
            z_plane: Specific Z plane, or None for center

        Returns:
            List of grid positions
        """
        if z_plane is None:
            z_plane = self.get_center().z

        positions = []
        width, height, _ = self.get_dimensions()
        num_x = int(width / spacing_mm) + 1
        num_y = int(height / spacing_mm) + 1

        for i in range(num_x):
            for j in range(num_y):
                pos = Position(
                    x=self.min_position.x + i * spacing_mm,
                    y=self.min_position.y + j * spacing_mm,
                    z=z_plane,
                    r=self.min_position.r
                )
                if self.contains_position(pos, use_padding=False):
                    positions.append(pos)

        return positions


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

    def get_scan_positions(self, spacing_mm: float) -> List[Position]:
        """Generate scan positions within region.

        Args:
            spacing_mm: Spacing between positions

        Returns:
            List of scan positions
        """
        if self.bounds:
            return self.bounds.get_grid_positions(spacing_mm)

        if self.center and self.radius_mm:
            # Generate positions in circular pattern
            positions = []
            num_rings = int(self.radius_mm / spacing_mm) + 1

            for ring in range(num_rings):
                if ring == 0:
                    positions.append(self.center)
                else:
                    radius = ring * spacing_mm
                    circumference = 2 * np.pi * radius
                    num_points = max(6, int(circumference / spacing_mm))

                    for i in range(num_points):
                        angle = 2 * np.pi * i / num_points
                        x = self.center.x + radius * np.cos(angle)
                        y = self.center.y + radius * np.sin(angle)
                        pos = Position(x=x, y=y, z=self.center.z, r=self.center.r)
                        positions.append(pos)

            return positions

        return []


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

    def add_region(self, region: SampleRegion) -> None:
        """Add a region of interest.

        Args:
            region: Region to add
        """
        self.regions.append(region)
        self.update()

        # Update sample bounds to include region
        if region.bounds and self.bounds:
            # Expand bounds to include region
            self.bounds = SampleBounds(
                min_position=Position(
                    x=min(self.bounds.min_position.x, region.bounds.min_position.x),
                    y=min(self.bounds.min_position.y, region.bounds.min_position.y),
                    z=min(self.bounds.min_position.z, region.bounds.min_position.z),
                    r=self.bounds.min_position.r
                ),
                max_position=Position(
                    x=max(self.bounds.max_position.x, region.bounds.max_position.x),
                    y=max(self.bounds.max_position.y, region.bounds.max_position.y),
                    z=max(self.bounds.max_position.z, region.bounds.max_position.z),
                    r=self.bounds.max_position.r
                )
            )
        elif region.bounds:
            self.bounds = region.bounds

    def add_reference_position(self, position: Position, name: Optional[str] = None) -> None:
        """Add a reference position.

        Args:
            position: Position to add
            name: Optional name for position
        """
        if name:
            position.name = name
        self.reference_positions.append(position)
        self.update()

    def get_regions_by_priority(self) -> List[SampleRegion]:
        """Get regions sorted by priority.

        Returns:
            Sorted list of regions (highest priority first)
        """
        return sorted(self.regions, key=lambda r: r.priority, reverse=True)

    def find_compatible_laser(self, available_lasers: List[float]) -> Dict[str, List[float]]:
        """Find compatible lasers for fluorophores.

        Args:
            available_lasers: List of available laser wavelengths

        Returns:
            Dictionary mapping fluorophore names to compatible lasers
        """
        compatible = {}
        for fluorophore in self.fluorophores:
            compatible[fluorophore.name] = [
                laser for laser in available_lasers
                if fluorophore.is_compatible_with_laser(laser)
            ]
        return compatible

    def estimate_imaging_time(self, positions_per_region: int,
                            time_per_position: float) -> float:
        """Estimate total imaging time.

        Args:
            positions_per_region: Number of positions per region
            time_per_position: Time per position in seconds

        Returns:
            Estimated total time in seconds
        """
        total_positions = len(self.regions) * positions_per_region
        total_positions += len(self.reference_positions)
        return total_positions * time_per_position

    def get_storage_requirements(self, images_per_position: int,
                                bytes_per_image: int) -> float:
        """Estimate data storage requirements.

        Args:
            images_per_position: Number of images per position
            bytes_per_image: Size of each image in bytes

        Returns:
            Estimated storage in GB
        """
        # This is a simple estimate - actual implementation would be more sophisticated
        total_positions = len(self.reference_positions)
        for region in self.regions:
            if region.bounds:
                # Estimate based on region size
                width, height, depth = region.bounds.get_dimensions()
                # Rough estimate of positions needed
                positions = (width * height * depth) / 0.1  # 0.1mm³ per position
                total_positions += int(positions)

        total_bytes = total_positions * images_per_position * bytes_per_image
        return total_bytes / (1024 ** 3)  # Convert to GB

    @classmethod
    def create_calibration_sample(cls) -> 'Sample':
        """Create a standard calibration sample.

        Returns:
            Sample configured for calibration
        """
        return cls(
            name="Calibration Beads",
            sample_type=SampleType.BEAD_SAMPLE,
            preparation_date=datetime.now(),
            mounting_medium=MountingMedium.WATER,
            notes="Fluorescent calibration beads for system alignment"
        )