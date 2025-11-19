"""Filter wheel models for optical path control.

This module provides models for filter wheels, filters,
and filter positions in the microscope light path.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
from ..base import ValidatedModel, ValidationError, validate_range


class FilterType(Enum):
    """Types of optical filters."""
    EXCITATION = "excitation"      # Excitation filter
    EMISSION = "emission"          # Emission filter
    DICHROIC = "dichroic"         # Dichroic mirror/beamsplitter
    NEUTRAL_DENSITY = "nd"         # Neutral density
    BANDPASS = "bandpass"         # Bandpass filter
    LONGPASS = "longpass"         # Long-pass filter
    SHORTPASS = "shortpass"       # Short-pass filter
    NOTCH = "notch"              # Notch filter
    POLARIZER = "polarizer"       # Polarizing filter


class FilterWheelState(Enum):
    """Filter wheel operational states."""
    IDLE = "idle"
    MOVING = "moving"
    ERROR = "error"
    INITIALIZING = "initializing"
    NOT_INITIALIZED = "not_initialized"


@dataclass
class FilterSpectrum:
    """Spectral characteristics of a filter."""
    center_wavelength_nm: Optional[float] = None
    bandwidth_nm: Optional[float] = None  # FWHM for bandpass
    cutoff_wavelength_nm: Optional[float] = None  # For long/short pass
    transmission_percent: float = 90.0
    optical_density: Optional[float] = None  # For ND filters
    blocking_od: float = 6.0  # Out-of-band blocking

    def __post_init__(self):
        """Validate spectral parameters."""
        if self.center_wavelength_nm is not None:
            validate_range(
                self.center_wavelength_nm,
                min_val=200.0, max_val=2000.0,
                field_name="center_wavelength_nm"
            )

        if self.bandwidth_nm is not None:
            validate_range(
                self.bandwidth_nm,
                min_val=1.0, max_val=500.0,
                field_name="bandwidth_nm"
            )

        validate_range(
            self.transmission_percent,
            min_val=0.0, max_val=100.0,
            field_name="transmission_percent"
        )

        if self.optical_density is not None:
            validate_range(
                self.optical_density,
                min_val=0.0, max_val=10.0,
                field_name="optical_density"
            )

    def get_transmission_factor(self) -> float:
        """Get transmission as a factor (0-1).

        Returns:
            Transmission factor
        """
        if self.optical_density is not None:
            # For ND filters, use OD
            return 10 ** (-self.optical_density)
        else:
            # For other filters, use transmission percentage
            return self.transmission_percent / 100.0

    def is_wavelength_transmitted(self, wavelength_nm: float) -> bool:
        """Check if wavelength is transmitted by filter.

        Args:
            wavelength_nm: Wavelength to check

        Returns:
            True if wavelength is transmitted
        """
        if self.center_wavelength_nm and self.bandwidth_nm:
            # Bandpass filter
            lower = self.center_wavelength_nm - self.bandwidth_nm / 2
            upper = self.center_wavelength_nm + self.bandwidth_nm / 2
            return lower <= wavelength_nm <= upper

        if self.cutoff_wavelength_nm:
            # Long/short pass filter (assuming context determines type)
            # This is simplified - actual implementation would need filter type
            return True  # Placeholder

        # Default to transmitting if no spectral data
        return True


@dataclass
class Filter(ValidatedModel):
    """Optical filter specification."""
    name: str
    filter_type: FilterType
    spectrum: FilterSpectrum
    part_number: Optional[str] = None
    manufacturer: Optional[str] = None
    diameter_mm: float = 25.0
    thickness_mm: float = 3.0
    coating: Optional[str] = None
    substrate: Optional[str] = None
    orientation_sensitive: bool = False
    notes: Optional[str] = None

    def validate(self) -> None:
        """Validate filter specifications."""
        # Validate physical dimensions
        validate_range(self.diameter_mm, min_val=1.0, max_val=100.0, field_name="diameter_mm")
        validate_range(self.thickness_mm, min_val=0.1, max_val=20.0, field_name="thickness_mm")

        # Validate name is not empty
        if not self.name or not self.name.strip():
            raise ValidationError("Filter name cannot be empty")

    def get_label(self) -> str:
        """Get human-readable filter label.

        Returns:
            Filter label string
        """
        if self.filter_type == FilterType.NEUTRAL_DENSITY and self.spectrum.optical_density:
            return f"{self.name} (ND {self.spectrum.optical_density:.1f})"

        if self.spectrum.center_wavelength_nm and self.spectrum.bandwidth_nm:
            return (f"{self.name} "
                   f"({self.spectrum.center_wavelength_nm:.0f}/"
                   f"{self.spectrum.bandwidth_nm:.0f}nm)")

        if self.spectrum.cutoff_wavelength_nm:
            return f"{self.name} ({self.spectrum.cutoff_wavelength_nm:.0f}nm)"

        return self.name

    @classmethod
    def create_empty(cls) -> 'Filter':
        """Create an empty filter position.

        Returns:
            Filter representing empty position
        """
        return cls(
            name="Empty",
            filter_type=FilterType.NEUTRAL_DENSITY,
            spectrum=FilterSpectrum(
                optical_density=0.0,
                transmission_percent=100.0
            )
        )


@dataclass
class FilterPosition:
    """Position in a filter wheel."""
    position_index: int
    filter: Optional[Filter] = None
    is_home: bool = False
    encoder_value: Optional[int] = None

    def is_empty(self) -> bool:
        """Check if position is empty.

        Returns:
            True if no filter or empty filter
        """
        return self.filter is None or self.filter.name.lower() == "empty"

    def __str__(self) -> str:
        """String representation of position."""
        if self.filter:
            return f"Position {self.position_index}: {self.filter.get_label()}"
        else:
            return f"Position {self.position_index}: Empty"


@dataclass
class FilterWheel(ValidatedModel):
    """Complete filter wheel model."""
    name: str
    num_positions: int
    positions: List[FilterPosition]
    current_position: int = 0
    state: FilterWheelState = FilterWheelState.NOT_INITIALIZED
    is_motorized: bool = True
    speed_rpm: float = 60.0
    backlash_steps: int = 0
    serial_number: Optional[str] = None

    def validate(self) -> None:
        """Validate filter wheel configuration."""
        # Validate number of positions
        validate_range(
            self.num_positions,
            min_val=1, max_val=20,
            field_name="num_positions"
        )

        # Validate positions list matches num_positions
        if len(self.positions) != self.num_positions:
            raise ValidationError(
                f"Position count ({len(self.positions)}) doesn't match "
                f"num_positions ({self.num_positions})"
            )

        # Validate current position
        if not (0 <= self.current_position < self.num_positions):
            raise ValidationError(
                f"Current position {self.current_position} out of range "
                f"(0-{self.num_positions - 1})"
            )

        # Validate position indices
        for i, pos in enumerate(self.positions):
            if pos.position_index != i:
                raise ValidationError(
                    f"Position index mismatch at position {i}: "
                    f"expected {i}, got {pos.position_index}"
                )

    def get_current_filter(self) -> Optional[Filter]:
        """Get currently selected filter.

        Returns:
            Current filter or None if empty
        """
        if 0 <= self.current_position < len(self.positions):
            return self.positions[self.current_position].filter
        return None

    def get_filter_at_position(self, position: int) -> Optional[Filter]:
        """Get filter at specific position.

        Args:
            position: Position index

        Returns:
            Filter at position or None
        """
        if 0 <= position < len(self.positions):
            return self.positions[position].filter
        return None

    def find_filter_position(self, filter_name: str) -> Optional[int]:
        """Find position of filter by name.

        Args:
            filter_name: Name of filter to find

        Returns:
            Position index or None if not found
        """
        for pos in self.positions:
            if pos.filter and pos.filter.name.lower() == filter_name.lower():
                return pos.position_index
        return None

    def move_to_position(self, position: int) -> bool:
        """Move to specified position.

        Args:
            position: Target position index

        Returns:
            True if move initiated successfully
        """
        if not (0 <= position < self.num_positions):
            return False

        if not self.is_motorized:
            # Manual filter wheel - just update position
            self.current_position = position
            self.state = FilterWheelState.IDLE
        else:
            # Motorized - would trigger actual movement
            self.state = FilterWheelState.MOVING
            self.current_position = position
            # Actual hardware control would happen here

        self.update()
        return True

    def get_movement_time(self, target_position: int) -> float:
        """Estimate time to move to target position.

        Args:
            target_position: Target position index

        Returns:
            Estimated movement time in seconds
        """
        if not self.is_motorized or target_position == self.current_position:
            return 0.0

        # Calculate shortest path (forward or backward)
        forward_steps = (target_position - self.current_position) % self.num_positions
        backward_steps = (self.current_position - target_position) % self.num_positions
        steps = min(forward_steps, backward_steps)

        # Estimate based on rotation speed
        # Assume equal angular spacing
        angle_per_position = 360.0 / self.num_positions
        total_angle = steps * angle_per_position

        # Add time for acceleration/deceleration
        rotation_time = (total_angle / 360.0) * (60.0 / self.speed_rpm)
        overhead = 0.5  # seconds for accel/decel

        return rotation_time + overhead

    @classmethod
    def create_default(cls, num_positions: int = 6) -> 'FilterWheel':
        """Create filter wheel with default configuration.

        Args:
            num_positions: Number of filter positions

        Returns:
            FilterWheel with empty positions
        """
        positions = [
            FilterPosition(position_index=i, filter=Filter.create_empty())
            for i in range(num_positions)
        ]

        return cls(
            name="Filter Wheel",
            num_positions=num_positions,
            positions=positions,
            state=FilterWheelState.IDLE
        )