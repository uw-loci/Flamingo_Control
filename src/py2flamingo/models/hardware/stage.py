"""Stage models for microscope positioning system.

This module provides models for representing stage positions,
movement limits, and stage configuration.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum
import math
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
    HOME = "home"         # Move to home position


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
    def from_config(cls, config: Dict[str, Any]) -> 'StageLimits':
        """Create StageLimits from configuration dictionary.

        Args:
            config: Configuration dictionary with axis limits

        Returns:
            StageLimits instance
        """
        # Handle both new format and legacy format
        if 'limits' in config:
            limits = config['limits']
        else:
            limits = config

        # Parse X axis
        x_data = limits.get('x_axis', limits.get('x', {}))
        x_axis = AxisLimits(
            min=x_data.get('min_mm', x_data.get('min', 0.0)),
            max=x_data.get('max_mm', x_data.get('max', 26.0)),
            soft_min=x_data.get('soft_min_mm', x_data.get('soft_min')),
            soft_max=x_data.get('soft_max_mm', x_data.get('soft_max')),
            unit="mm"
        )

        # Parse Y axis
        y_data = limits.get('y_axis', limits.get('y', {}))
        y_axis = AxisLimits(
            min=y_data.get('min_mm', y_data.get('min', 0.0)),
            max=y_data.get('max_mm', y_data.get('max', 26.0)),
            soft_min=y_data.get('soft_min_mm', y_data.get('soft_min')),
            soft_max=y_data.get('soft_max_mm', y_data.get('soft_max')),
            unit="mm"
        )

        # Parse Z axis
        z_data = limits.get('z_axis', limits.get('z', {}))
        z_axis = AxisLimits(
            min=z_data.get('min_mm', z_data.get('min', 0.0)),
            max=z_data.get('max_mm', z_data.get('max', 26.0)),
            soft_min=z_data.get('soft_min_mm', z_data.get('soft_min')),
            soft_max=z_data.get('soft_max_mm', z_data.get('soft_max')),
            unit="mm"
        )

        # Parse R (rotation) axis
        r_data = limits.get('r_axis', limits.get('r', {}))
        r_axis = AxisLimits(
            min=r_data.get('min_degrees', r_data.get('min', -720.0)),
            max=r_data.get('max_degrees', r_data.get('max', 720.0)),
            soft_min=r_data.get('soft_min_degrees', r_data.get('soft_min')),
            soft_max=r_data.get('soft_max_degrees', r_data.get('soft_max')),
            unit="degrees"
        )

        return cls(x_axis=x_axis, y_axis=y_axis, z_axis=z_axis, r_axis=r_axis)

    @classmethod
    def from_legacy(cls, x_min: float = 0.0, x_max: float = 26.0,
                   y_min: float = 0.0, y_max: float = 26.0,
                   z_min: float = 0.0, z_max: float = 26.0,
                   r_min: float = -720.0, r_max: float = 720.0) -> 'StageLimits':
        """Create StageLimits from legacy min/max values.

        Provides backward compatibility with old StageLimits class.
        """
        return cls(
            x_axis=AxisLimits(min=x_min, max=x_max, unit="mm"),
            y_axis=AxisLimits(min=y_min, max=y_max, unit="mm"),
            z_axis=AxisLimits(min=z_min, max=z_max, unit="mm"),
            r_axis=AxisLimits(min=r_min, max=r_max, unit="degrees")
        )

    def get_axis_limits(self, axis: StageAxis) -> AxisLimits:
        """Get limits for specific axis.

        Args:
            axis: Axis to get limits for

        Returns:
            AxisLimits for the specified axis
        """
        axis_map = {
            StageAxis.X: self.x_axis,
            StageAxis.Y: self.y_axis,
            StageAxis.Z: self.z_axis,
            StageAxis.R: self.r_axis
        }
        return axis_map[axis]

    def is_position_valid(self, position: 'Position', use_soft: bool = True) -> bool:
        """Check if a position is within all stage limits.

        Args:
            position: Position to validate
            use_soft: Whether to check against soft limits

        Returns:
            True if position is within all limits
        """
        return (self.x_axis.is_within_limits(position.x, use_soft) and
                self.y_axis.is_within_limits(position.y, use_soft) and
                self.z_axis.is_within_limits(position.z, use_soft) and
                self.r_axis.is_within_limits(position.r, use_soft))


@dataclass
class Position(ValidatedModel):
    """Represents a position in the microscope coordinate system.

    Enhanced version of the original Position class that includes
    validation and metadata support.

    Note: Default values are required because this class inherits from
    ValidatedModel/BaseModel which have fields with defaults. Python 3.12+
    enforces that non-default fields cannot follow default fields.
    """
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    r: float = 0.0  # Rotation in degrees
    name: Optional[str] = None  # Optional position name/label
    stage_limits: Optional[StageLimits] = field(default=None, repr=False)

    def validate(self) -> None:
        """Validate position coordinates."""
        # Check for NaN or infinity
        for axis, value in [('x', self.x), ('y', self.y), ('z', self.z), ('r', self.r)]:
            if math.isnan(value) or math.isinf(value):
                raise ValidationError(f"Invalid {axis} coordinate: {value}")

        # Validate against stage limits if provided
        if self.stage_limits and not self.stage_limits.is_position_valid(self):
            raise ValidationError(
                f"Position {self} is outside stage limits"
            )

    def distance_to(self, other: 'Position', include_rotation: bool = False) -> float:
        """Calculate Euclidean distance to another position.

        Args:
            other: Target position
            include_rotation: Whether to include rotation in distance

        Returns:
            Distance in millimeters
        """
        dx = self.x - other.x
        dy = self.y - other.y
        dz = self.z - other.z

        if include_rotation:
            # Normalize rotation difference to [-180, 180]
            dr = (self.r - other.r) % 360
            if dr > 180:
                dr -= 360
            # Convert rotation to approximate linear distance (arbitrary scale)
            dr_mm = dr * 0.1  # 1 degree ~= 0.1mm for distance purposes
            return math.sqrt(dx*dx + dy*dy + dz*dz + dr_mm*dr_mm)
        else:
            return math.sqrt(dx*dx + dy*dy + dz*dz)

    def offset_by(self, dx: float = 0, dy: float = 0, dz: float = 0, dr: float = 0) -> 'Position':
        """Create a new position offset from this one.

        Args:
            dx: X offset in mm
            dy: Y offset in mm
            dz: Z offset in mm
            dr: Rotation offset in degrees

        Returns:
            New Position with offsets applied
        """
        return Position(
            x=self.x + dx,
            y=self.y + dy,
            z=self.z + dz,
            r=self.r + dr,
            name=None,  # Don't copy name for offset position
            stage_limits=self.stage_limits
        )

    def clamp_to_limits(self, limits: StageLimits, use_soft: bool = True) -> 'Position':
        """Create a new position clamped to stage limits.

        Args:
            limits: Stage limits to clamp to
            use_soft: Whether to use soft limits

        Returns:
            New Position within limits
        """
        return Position(
            x=limits.x_axis.clamp(self.x, use_soft),
            y=limits.y_axis.clamp(self.y, use_soft),
            z=limits.z_axis.clamp(self.z, use_soft),
            r=limits.r_axis.clamp(self.r, use_soft),
            name=self.name,
            stage_limits=limits
        )

    def to_list(self) -> List[float]:
        """Convert to list format for backward compatibility.

        Returns:
            [x, y, z, r] coordinates
        """
        return [self.x, self.y, self.z, self.r]

    @classmethod
    def from_list(cls, coords: List[float], name: Optional[str] = None) -> 'Position':
        """Create Position from list of coordinates.

        Args:
            coords: List of [x, y, z, r] coordinates
            name: Optional position name

        Returns:
            New Position instance
        """
        if len(coords) < 3:
            raise ValueError(f"Expected at least 3 coordinates, got {len(coords)}")

        # Handle both 3 and 4 coordinate lists
        x, y, z = coords[:3]
        r = coords[3] if len(coords) > 3 else 0.0

        return cls(x=float(x), y=float(y), z=float(z), r=float(r), name=name)

    def __str__(self) -> str:
        """String representation of position."""
        name_str = f"'{self.name}' " if self.name else ""
        return f"Position {name_str}(x={self.x:.3f}, y={self.y:.3f}, z={self.z:.3f}, r={self.r:.1f}°)"


@dataclass
class StageVelocity:
    """Stage movement velocity settings."""
    x_velocity: float = 1.0  # mm/s
    y_velocity: float = 1.0  # mm/s
    z_velocity: float = 1.0  # mm/s
    r_velocity: float = 10.0  # degrees/s

    def get_velocity(self, axis: StageAxis) -> float:
        """Get velocity for specific axis."""
        velocity_map = {
            StageAxis.X: self.x_velocity,
            StageAxis.Y: self.y_velocity,
            StageAxis.Z: self.z_velocity,
            StageAxis.R: self.r_velocity
        }
        return velocity_map[axis]

    def set_velocity(self, axis: StageAxis, velocity: float) -> None:
        """Set velocity for specific axis."""
        if axis == StageAxis.X:
            self.x_velocity = velocity
        elif axis == StageAxis.Y:
            self.y_velocity = velocity
        elif axis == StageAxis.Z:
            self.z_velocity = velocity
        elif axis == StageAxis.R:
            self.r_velocity = velocity


@dataclass
class Stage(ValidatedModel):
    """Complete stage model with position, limits, and configuration."""
    current_position: Position = field(default_factory=Position)
    home_position: Optional[Position] = None
    limits: Optional[StageLimits] = None
    velocity: StageVelocity = field(default_factory=StageVelocity)
    is_homed: bool = False
    is_moving: bool = False
    backlash_compensation: Dict[str, float] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate stage configuration."""
        # Validate current position
        if self.limits and not self.limits.is_position_valid(self.current_position):
            raise ValidationError(
                f"Current position {self.current_position} is outside stage limits"
            )

        # Validate home position if set
        if self.home_position and self.limits:
            if not self.limits.is_position_valid(self.home_position):
                raise ValidationError(
                    f"Home position {self.home_position} is outside stage limits"
                )

    def move_to(self, target: Position, validate: bool = True) -> None:
        """Update current position to target.

        Args:
            target: Target position
            validate: Whether to validate against limits

        Raises:
            ValidationError: If target is outside limits
        """
        if validate and self.limits:
            if not self.limits.is_position_valid(target):
                raise ValidationError(
                    f"Target position {target} is outside stage limits"
                )

        self.current_position = target
        self.update()  # Update timestamp

    def get_movement_time(self, target: Position) -> float:
        """Estimate time required to move to target position.

        Args:
            target: Target position

        Returns:
            Estimated movement time in seconds
        """
        # Calculate time for each axis and return maximum
        times = [
            abs(target.x - self.current_position.x) / self.velocity.x_velocity,
            abs(target.y - self.current_position.y) / self.velocity.y_velocity,
            abs(target.z - self.current_position.z) / self.velocity.z_velocity,
            abs(target.r - self.current_position.r) / self.velocity.r_velocity
        ]
        return max(times)