"""Stage limit models (per-axis hard and soft limits).

The stage position is ``py2flamingo.models.microscope.Position`` (mm, r in
degrees); there is no second Position class.
"""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from py2flamingo.models.microscope import Position

from ..base import ValidatedModel, ValidationError, validate_range


class StageAxis(Enum):
    """Enumeration of stage axes."""

    X = "x"
    Y = "y"
    Z = "z"
    R = "r"  # Rotation


class MovementMode(Enum):
    """Stage movement modes."""

    ABSOLUTE = "absolute"  # Move to absolute position
    RELATIVE = "relative"  # Move relative to current position
    HOME = "home"  # Move to home position


@dataclass
class AxisLimits:
    """Limits for a single stage axis.

    Includes both hard limits (physical boundaries) and
    soft limits (user-defined safety margins).
    """

    min: float
    max: float
    soft_min: Optional[float] = None
    soft_max: Optional[float] = None
    unit: str = "mm"  # Default to millimeters, "degrees" for rotation

    def __post_init__(self):
        """Validate and set soft limits if not provided."""
        if self.min >= self.max:
            raise ValidationError(
                f"Min limit ({self.min}) must be less than max ({self.max})"
            )

        # Set soft limits to hard limits if not specified
        if self.soft_min is None:
            self.soft_min = self.min
        if self.soft_max is None:
            self.soft_max = self.max

        # Validate soft limits are within hard limits
        if self.soft_min < self.min:
            raise ValidationError(
                f"Soft min ({self.soft_min}) cannot be less than hard min ({self.min})"
            )
        if self.soft_max > self.max:
            raise ValidationError(
                f"Soft max ({self.soft_max}) cannot be greater than hard max ({self.max})"
            )

    def is_within_limits(self, value: float, use_soft: bool = True) -> bool:
        """Check if value is within limits.

        Args:
            value: Value to check
            use_soft: Whether to use soft limits (default) or hard limits

        Returns:
            True if value is within specified limits
        """
        if use_soft:
            return self.soft_min <= value <= self.soft_max
        else:
            return self.min <= value <= self.max

    def clamp(self, value: float, use_soft: bool = True) -> float:
        """Clamp value to be within limits.

        Args:
            value: Value to clamp
            use_soft: Whether to use soft limits

        Returns:
            Clamped value within limits
        """
        if use_soft:
            return max(self.soft_min, min(value, self.soft_max))
        else:
            return max(self.min, min(value, self.max))


@dataclass
class StageLimits:
    """Complete stage movement limits for all axes."""

    x_axis: AxisLimits
    y_axis: AxisLimits
    z_axis: AxisLimits
    r_axis: AxisLimits  # Rotation axis

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "StageLimits":
        """Create StageLimits from configuration dictionary.

        Args:
            config: Configuration dictionary with axis limits

        Returns:
            StageLimits instance
        """
        # Handle both new format and legacy format
        if "limits" in config:
            limits = config["limits"]
        else:
            limits = config

        # Parse X axis
        x_data = limits.get("x_axis", limits.get("x", {}))
        x_axis = AxisLimits(
            min=x_data.get("min_mm", x_data.get("min", 0.0)),
            max=x_data.get("max_mm", x_data.get("max", 26.0)),
            soft_min=x_data.get("soft_min_mm", x_data.get("soft_min")),
            soft_max=x_data.get("soft_max_mm", x_data.get("soft_max")),
            unit="mm",
        )

        # Parse Y axis
        y_data = limits.get("y_axis", limits.get("y", {}))
        y_axis = AxisLimits(
            min=y_data.get("min_mm", y_data.get("min", 0.0)),
            max=y_data.get("max_mm", y_data.get("max", 26.0)),
            soft_min=y_data.get("soft_min_mm", y_data.get("soft_min")),
            soft_max=y_data.get("soft_max_mm", y_data.get("soft_max")),
            unit="mm",
        )

        # Parse Z axis
        z_data = limits.get("z_axis", limits.get("z", {}))
        z_axis = AxisLimits(
            min=z_data.get("min_mm", z_data.get("min", 0.0)),
            max=z_data.get("max_mm", z_data.get("max", 26.0)),
            soft_min=z_data.get("soft_min_mm", z_data.get("soft_min")),
            soft_max=z_data.get("soft_max_mm", z_data.get("soft_max")),
            unit="mm",
        )

        # Parse R (rotation) axis
        r_data = limits.get("r_axis", limits.get("r", {}))
        r_axis = AxisLimits(
            min=r_data.get("min_degrees", r_data.get("min", -720.0)),
            max=r_data.get("max_degrees", r_data.get("max", 720.0)),
            soft_min=r_data.get("soft_min_degrees", r_data.get("soft_min")),
            soft_max=r_data.get("soft_max_degrees", r_data.get("soft_max")),
            unit="degrees",
        )

        return cls(x_axis=x_axis, y_axis=y_axis, z_axis=z_axis, r_axis=r_axis)

    @classmethod
    def from_legacy(
        cls,
        x_min: float = 0.0,
        x_max: float = 26.0,
        y_min: float = 0.0,
        y_max: float = 26.0,
        z_min: float = 0.0,
        z_max: float = 26.0,
        r_min: float = -720.0,
        r_max: float = 720.0,
    ) -> "StageLimits":
        """Create StageLimits from legacy min/max values.

        Provides backward compatibility with old StageLimits class.
        """
        return cls(
            x_axis=AxisLimits(min=x_min, max=x_max, unit="mm"),
            y_axis=AxisLimits(min=y_min, max=y_max, unit="mm"),
            z_axis=AxisLimits(min=z_min, max=z_max, unit="mm"),
            r_axis=AxisLimits(min=r_min, max=r_max, unit="degrees"),
        )

    def is_position_valid(self, position: "Position", use_soft: bool = True) -> bool:
        """Check if a position is within all stage limits.

        Args:
            position: Position to validate
            use_soft: Whether to check against soft limits

        Returns:
            True if position is within all limits
        """
        return (
            self.x_axis.is_within_limits(position.x, use_soft)
            and self.y_axis.is_within_limits(position.y, use_soft)
            and self.z_axis.is_within_limits(position.z, use_soft)
            and self.r_axis.is_within_limits(position.r, use_soft)
        )
