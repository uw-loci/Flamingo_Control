"""Objective lens models for microscope optics.

This module provides models for objective lenses and
their optical properties.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
import math
from ..base import ValidatedModel, ValidationError, validate_range


class ImmersionMedium(Enum):
    """Objective immersion media."""
    AIR = ("air", 1.0)
    WATER = ("water", 1.33)
    GLYCEROL = ("glycerol", 1.47)
    OIL = ("oil", 1.515)
    SILICONE = ("silicone", 1.40)

    def __init__(self, medium_name: str, refractive_index: float):
        self.medium_name = medium_name
        self.refractive_index = refractive_index


class ObjectiveType(Enum):
    """Types of objective lenses."""
    DRY = "dry"                    # Air objective
    WATER_IMMERSION = "water"      # Water immersion
    OIL_IMMERSION = "oil"          # Oil immersion
    WATER_DIPPING = "dipping"      # Water dipping objective
    MULTI_IMMERSION = "multi"      # Multiple immersion media


class CorrectionType(Enum):
    """Objective optical corrections."""
    ACHROMAT = "achromat"          # Basic chromatic correction
    FLUORITE = "fluorite"          # Semi-apochromatic
    APOCHROMAT = "apochromat"      # Full apochromatic correction
    PLAN = "plan"                  # Field-flattened
    PLAN_ACHROMAT = "plan_achromat"
    PLAN_FLUORITE = "plan_fluorite"
    PLAN_APOCHROMAT = "plan_apochromat"


@dataclass
class ObjectiveProperties:
    """Optical properties of an objective lens."""
    magnification: float
    numerical_aperture: float
    working_distance_mm: float
    immersion_medium: ImmersionMedium = ImmersionMedium.AIR
    focal_length_mm: Optional[float] = None
    field_number_mm: float = 25.0  # Field number
    parfocal_distance_mm: float = 45.0  # Standard for many objectives
    correction_collar: bool = False
    correction_collar_range: Optional[tuple] = None  # (min, max) if applicable
    chromatic_correction: Optional[List[float]] = None  # Corrected wavelengths
    transmission_range_nm: tuple = (400, 700)  # (min, max) wavelength

    def __post_init__(self):
        """Calculate derived properties and validate."""
        # Calculate focal length if not provided
        # Standard tube lens focal length is typically 180mm or 200mm
        if self.focal_length_mm is None:
            tube_lens_fl = 180.0  # Assume standard tube lens
            self.focal_length_mm = tube_lens_fl / self.magnification

        # Validate optical parameters
        self.validate_optical_parameters()

    def validate_optical_parameters(self):
        """Validate optical parameter relationships."""
        # NA cannot exceed refractive index of medium
        max_na = self.immersion_medium.refractive_index
        if self.numerical_aperture > max_na:
            raise ValidationError(
                f"NA ({self.numerical_aperture}) exceeds medium index ({max_na})"
            )

        # Check reasonable magnification range
        validate_range(
            self.magnification,
            min_val=0.5, max_val=200.0,
            field_name="magnification"
        )

        # Check reasonable NA range
        validate_range(
            self.numerical_aperture,
            min_val=0.01, max_val=1.65,
            field_name="numerical_aperture"
        )

        # Working distance typically decreases with magnification
        # This is a soft check - some specialized objectives violate this
        expected_max_wd = 50.0 / self.magnification
        if self.working_distance_mm > expected_max_wd * 2:
            # Just a warning, not an error
            pass

    def get_resolution_um(self, wavelength_nm: float = 520) -> float:
        """Calculate theoretical resolution (Rayleigh criterion).

        Args:
            wavelength_nm: Wavelength of light

        Returns:
            Resolution in micrometers
        """
        # Rayleigh criterion: r = 0.61 * λ / NA
        return 0.61 * wavelength_nm / (self.numerical_aperture * 1000)

    def get_depth_of_field_um(self, wavelength_nm: float = 520,
                              detector_pixel_um: float = 6.5) -> float:
        """Calculate depth of field.

        Args:
            wavelength_nm: Wavelength of light
            detector_pixel_um: Detector pixel size

        Returns:
            Depth of field in micrometers
        """
        # Wave optical depth of field
        wave_dof = wavelength_nm / (self.numerical_aperture ** 2) / 1000

        # Geometrical depth of field
        n = self.immersion_medium.refractive_index
        e = detector_pixel_um / self.magnification  # Detector resolution at specimen
        geom_dof = (n * e) / self.numerical_aperture

        # Total depth of field
        return wave_dof + geom_dof

    def get_field_of_view_mm(self, sensor_diagonal_mm: float) -> float:
        """Calculate field of view diameter.

        Args:
            sensor_diagonal_mm: Sensor diagonal in mm

        Returns:
            Field of view diameter in mm
        """
        return sensor_diagonal_mm / self.magnification

    def get_light_gathering_power(self) -> float:
        """Calculate relative light-gathering power.

        Returns:
            Relative brightness (proportional to NA²/Mag²)
        """
        return (self.numerical_aperture ** 2) / (self.magnification ** 2)


@dataclass
class Objective(ValidatedModel):
    """Complete objective lens model."""
    name: str
    manufacturer: str
    part_number: Optional[str]
    properties: ObjectiveProperties
    objective_type: ObjectiveType
    correction_type: CorrectionType
    position_index: Optional[int] = None  # Position in turret
    is_selected: bool = False
    requires_coverslip: bool = True
    coverslip_thickness_mm: float = 0.17  # Standard #1.5
    thread_type: str = "RMS"  # RMS, M25, M32, etc.
    temperature_range_c: tuple = (15, 35)  # Operating temperature range
    serial_number: Optional[str] = None
    notes: Optional[str] = None

    def validate(self) -> None:
        """Validate objective configuration."""
        # Validate name not empty
        if not self.name or not self.name.strip():
            raise ValidationError("Objective name cannot be empty")

        # Validate immersion type matches objective type
        if self.objective_type == ObjectiveType.DRY:
            if self.properties.immersion_medium != ImmersionMedium.AIR:
                raise ValidationError(
                    "Dry objective must use air as immersion medium"
                )
        elif self.objective_type == ObjectiveType.WATER_IMMERSION:
            if self.properties.immersion_medium != ImmersionMedium.WATER:
                raise ValidationError(
                    "Water immersion objective must use water medium"
                )
        elif self.objective_type == ObjectiveType.OIL_IMMERSION:
            if self.properties.immersion_medium not in [ImmersionMedium.OIL, ImmersionMedium.SILICONE]:
                raise ValidationError(
                    "Oil immersion objective must use oil or silicone medium"
                )

        # Validate coverslip thickness
        if self.requires_coverslip:
            validate_range(
                self.coverslip_thickness_mm,
                min_val=0.0, max_val=0.5,
                field_name="coverslip_thickness_mm"
            )

    def get_display_name(self) -> str:
        """Get formatted display name for objective.

        Returns:
            Human-readable objective description
        """
        immersion = ""
        if self.objective_type != ObjectiveType.DRY:
            immersion = f" {self.properties.immersion_medium.medium_name.capitalize()}"

        correction = ""
        if self.correction_type in [CorrectionType.PLAN_APOCHROMAT,
                                   CorrectionType.APOCHROMAT]:
            correction = " Apo"
        elif self.correction_type in [CorrectionType.PLAN_FLUORITE,
                                     CorrectionType.FLUORITE]:
            correction = " Fluor"

        return (f"{self.properties.magnification:.0f}x/"
                f"{self.properties.numerical_aperture:.2f}"
                f"{immersion}{correction}")

    def is_compatible_with_wavelength(self, wavelength_nm: float) -> bool:
        """Check if objective is suitable for given wavelength.

        Args:
            wavelength_nm: Wavelength to check

        Returns:
            True if wavelength is within transmission range
        """
        min_wl, max_wl = self.properties.transmission_range_nm
        return min_wl <= wavelength_nm <= max_wl

    def calculate_pixel_size_um(self, sensor_pixel_um: float) -> float:
        """Calculate effective pixel size at sample.

        Args:
            sensor_pixel_um: Physical sensor pixel size

        Returns:
            Effective pixel size at sample in micrometers
        """
        return sensor_pixel_um / self.properties.magnification

    @classmethod
    def create_default(cls, magnification: float = 20.0, na: float = 0.75) -> 'Objective':
        """Create objective with default settings.

        Args:
            magnification: Magnification
            na: Numerical aperture

        Returns:
            Objective with default configuration
        """
        properties = ObjectiveProperties(
            magnification=magnification,
            numerical_aperture=na,
            working_distance_mm=1.0  # Typical for 20x
        )

        return cls(
            name=f"{magnification}x/{na} Plan",
            manufacturer="Generic",
            part_number=None,
            properties=properties,
            objective_type=ObjectiveType.DRY,
            correction_type=CorrectionType.PLAN_ACHROMAT
        )


@dataclass
class ObjectiveTurret(ValidatedModel):
    """Objective turret/nosepiece model."""
    num_positions: int
    objectives: List[Optional[Objective]]
    current_position: int = 0
    is_motorized: bool = True
    parfocal_adjustment: Dict[int, float] = field(default_factory=dict)  # Z adjustments

    def validate(self) -> None:
        """Validate turret configuration."""
        # Validate number of positions
        validate_range(
            self.num_positions,
            min_val=1, max_val=10,
            field_name="num_positions"
        )

        # Validate objectives list length
        if len(self.objectives) != self.num_positions:
            raise ValidationError(
                f"Objectives list length ({len(self.objectives)}) "
                f"doesn't match num_positions ({self.num_positions})"
            )

        # Validate current position
        if not (0 <= self.current_position < self.num_positions):
            raise ValidationError(
                f"Current position {self.current_position} out of range"
            )

        # Update position indices for objectives
        for i, obj in enumerate(self.objectives):
            if obj is not None:
                obj.position_index = i
                obj.is_selected = (i == self.current_position)

    def get_current_objective(self) -> Optional[Objective]:
        """Get currently selected objective.

        Returns:
            Current objective or None if position is empty
        """
        return self.objectives[self.current_position]

    def find_objective_position(self, magnification: float) -> Optional[int]:
        """Find position of objective with given magnification.

        Args:
            magnification: Target magnification

        Returns:
            Position index or None if not found
        """
        for i, obj in enumerate(self.objectives):
            if obj and abs(obj.properties.magnification - magnification) < 0.1:
                return i
        return None

    def select_position(self, position: int) -> bool:
        """Select objective at given position.

        Args:
            position: Target position index

        Returns:
            True if selection successful
        """
        if not (0 <= position < self.num_positions):
            return False

        # Update selection state
        for i, obj in enumerate(self.objectives):
            if obj:
                obj.is_selected = (i == position)

        self.current_position = position
        self.update()
        return True

    def get_parfocal_offset(self, position: int) -> float:
        """Get Z-axis parfocal adjustment for position.

        Args:
            position: Position index

        Returns:
            Z-axis offset in mm
        """
        return self.parfocal_adjustment.get(position, 0.0)