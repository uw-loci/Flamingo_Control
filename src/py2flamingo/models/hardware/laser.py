"""Laser models for illumination control.

This module provides models for laser configuration,
power settings, and safety limits.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime, timedelta
from ..base import ValidatedModel, ValidationError, validate_range


class LaserState(Enum):
    """Laser operational states."""
    OFF = "off"
    STANDBY = "standby"
    WARMING = "warming"
    READY = "ready"
    ACTIVE = "active"
    ERROR = "error"
    INTERLOCK = "interlock"  # Safety interlock engaged


class LaserMode(Enum):
    """Laser operation modes."""
    CW = "continuous_wave"      # Continuous wave
    PULSED = "pulsed"           # Pulsed operation
    MODULATED = "modulated"      # External modulation
    TRIGGERED = "triggered"      # External trigger


class LaserType(Enum):
    """Types of laser sources."""
    DIODE = "diode"
    DPSS = "dpss"              # Diode-pumped solid-state
    GAS = "gas"
    FIBER = "fiber"
    LED = "led"                 # LED sources treated as lasers


@dataclass
class PowerLimits:
    """Laser power limits and safety settings."""
    min_mw: float = 0.0           # Minimum power in milliwatts
    max_mw: float = 100.0         # Maximum power in milliwatts
    safe_max_mw: float = 50.0     # Safe maximum for routine use
    startup_mw: float = 0.0       # Default power at startup
    increment_mw: float = 1.0     # Minimum power increment

    def __post_init__(self):
        """Validate power limits."""
        if self.min_mw < 0:
            raise ValidationError(f"Minimum power cannot be negative: {self.min_mw} mW")

        if self.min_mw >= self.max_mw:
            raise ValidationError(
                f"Min power ({self.min_mw} mW) must be less than max ({self.max_mw} mW)"
            )

        if self.safe_max_mw > self.max_mw:
            raise ValidationError(
                f"Safe max ({self.safe_max_mw} mW) cannot exceed max ({self.max_mw} mW)"
            )

        if self.startup_mw < self.min_mw or self.startup_mw > self.safe_max_mw:
            raise ValidationError(
                f"Startup power ({self.startup_mw} mW) must be between min and safe max"
            )

    def clamp_power(self, power_mw: float, use_safe_limit: bool = True) -> float:
        """Clamp power to valid range.

        Args:
            power_mw: Requested power in milliwatts
            use_safe_limit: Whether to limit to safe maximum

        Returns:
            Clamped power value
        """
        max_limit = self.safe_max_mw if use_safe_limit else self.max_mw
        return max(self.min_mw, min(power_mw, max_limit))

    def is_valid_power(self, power_mw: float, use_safe_limit: bool = True) -> bool:
        """Check if power value is within limits.

        Args:
            power_mw: Power to validate
            use_safe_limit: Whether to check against safe limit

        Returns:
            True if power is valid
        """
        max_limit = self.safe_max_mw if use_safe_limit else self.max_mw
        return self.min_mw <= power_mw <= max_limit

    def quantize_power(self, power_mw: float) -> float:
        """Quantize power to nearest valid increment.

        Args:
            power_mw: Requested power

        Returns:
            Power quantized to nearest increment
        """
        if self.increment_mw <= 0:
            return power_mw

        steps = round(power_mw / self.increment_mw)
        return steps * self.increment_mw


@dataclass
class LaserCalibration:
    """Laser calibration and characterization data."""
    wavelength_nm: float                    # Nominal wavelength
    actual_wavelength_nm: Optional[float] = None  # Measured wavelength
    linewidth_nm: Optional[float] = None    # Spectral linewidth
    beam_diameter_mm: Optional[float] = None # Beam diameter at aperture
    divergence_mrad: Optional[float] = None  # Beam divergence
    m_squared: Optional[float] = None        # M² beam quality factor
    polarization: Optional[str] = None      # Polarization type
    calibration_date: Optional[datetime] = None
    power_calibration: Dict[float, float] = field(default_factory=dict)  # Set vs actual power

    def get_actual_power(self, set_power_mw: float) -> float:
        """Get actual power from calibration curve.

        Args:
            set_power_mw: Set power value

        Returns:
            Actual/measured power, or set power if no calibration
        """
        if not self.power_calibration:
            return set_power_mw

        # Find nearest calibration points for interpolation
        set_points = sorted(self.power_calibration.keys())

        if set_power_mw <= set_points[0]:
            return self.power_calibration[set_points[0]]
        if set_power_mw >= set_points[-1]:
            return self.power_calibration[set_points[-1]]

        # Linear interpolation between points
        for i in range(len(set_points) - 1):
            if set_points[i] <= set_power_mw <= set_points[i + 1]:
                x1, x2 = set_points[i], set_points[i + 1]
                y1 = self.power_calibration[x1]
                y2 = self.power_calibration[x2]

                # Linear interpolation
                ratio = (set_power_mw - x1) / (x2 - x1)
                return y1 + ratio * (y2 - y1)

        return set_power_mw


@dataclass
class PulseSettings:
    """Settings for pulsed laser operation."""
    frequency_hz: float
    duty_cycle_percent: float
    pulse_width_ns: Optional[float] = None
    delay_ns: float = 0.0

    def __post_init__(self):
        """Validate pulse settings."""
        validate_range(self.frequency_hz, min_val=0.1, max_val=1e9, field_name="frequency_hz")
        validate_range(self.duty_cycle_percent, min_val=0.1, max_val=100.0, field_name="duty_cycle_percent")

        if self.pulse_width_ns is not None:
            validate_range(self.pulse_width_ns, min_val=1.0, max_val=1e9, field_name="pulse_width_ns")

    def get_average_power(self, peak_power_mw: float) -> float:
        """Calculate average power for pulsed operation.

        Args:
            peak_power_mw: Peak power during pulse

        Returns:
            Average power in milliwatts
        """
        return peak_power_mw * (self.duty_cycle_percent / 100.0)


@dataclass
class LaserSettings(ValidatedModel):
    """Current laser settings."""
    power_setpoint_mw: float = 0.0
    mode: LaserMode = LaserMode.CW
    enabled: bool = False
    pulse_settings: Optional[PulseSettings] = None
    modulation_frequency_hz: Optional[float] = None
    modulation_depth_percent: Optional[float] = None
    shutter_open: bool = False

    def validate(self) -> None:
        """Validate laser settings."""
        # Power must be non-negative
        if self.power_setpoint_mw < 0:
            raise ValidationError(f"Power cannot be negative: {self.power_setpoint_mw} mW")

        # Validate modulation settings if in modulated mode
        if self.mode == LaserMode.MODULATED:
            if self.modulation_frequency_hz is None:
                raise ValidationError("Modulation frequency required for modulated mode")

            validate_range(
                self.modulation_frequency_hz,
                min_val=0.1, max_val=1e6,
                field_name="modulation_frequency_hz"
            )

            if self.modulation_depth_percent is not None:
                validate_range(
                    self.modulation_depth_percent,
                    min_val=0.0, max_val=100.0,
                    field_name="modulation_depth_percent"
                )

        # Validate pulse settings if in pulsed mode
        if self.mode == LaserMode.PULSED and self.pulse_settings is None:
            raise ValidationError("Pulse settings required for pulsed mode")

    def get_effective_power(self) -> float:
        """Get effective output power based on mode.

        Returns:
            Effective power in milliwatts
        """
        if not self.enabled or not self.shutter_open:
            return 0.0

        if self.mode == LaserMode.PULSED and self.pulse_settings:
            return self.pulse_settings.get_average_power(self.power_setpoint_mw)

        if self.mode == LaserMode.MODULATED and self.modulation_depth_percent:
            # Average power for sinusoidal modulation
            return self.power_setpoint_mw * (1.0 - self.modulation_depth_percent / 200.0)

        return self.power_setpoint_mw


@dataclass
class LaserSafetyStatus:
    """Laser safety and interlock status."""
    interlock_closed: bool = True     # Safety interlock circuit
    key_switch_on: bool = False       # Key switch position
    emission_indicator: bool = False   # Emission indicator status
    temperature_ok: bool = True        # Temperature within limits
    coolant_flow_ok: bool = True      # Coolant flow (if applicable)
    hours_since_service: float = 0.0  # Operating hours since last service
    total_hours: float = 0.0          # Total operating hours

    def is_safe_to_operate(self) -> bool:
        """Check if all safety conditions are met.

        Returns:
            True if safe to operate
        """
        return (self.interlock_closed and
                self.key_switch_on and
                self.temperature_ok and
                self.coolant_flow_ok)


@dataclass
class Laser(ValidatedModel):
    """Complete laser model with settings, limits, and state."""
    name: str = ""
    channel_id: int = 0
    laser_type: LaserType = LaserType.DIODE
    power_limits: PowerLimits = field(default_factory=PowerLimits)
    settings: LaserSettings = field(default_factory=LaserSettings)
    calibration: LaserCalibration = field(default_factory=LaserCalibration)
    state: LaserState = LaserState.OFF
    safety_status: LaserSafetyStatus = field(default_factory=LaserSafetyStatus)
    warmup_time_seconds: float = 0.0
    cooldown_time_seconds: float = 0.0
    last_enabled_time: Optional[datetime] = None
    error_message: Optional[str] = None
    usage_hours: float = 0.0
    serial_number: Optional[str] = None

    def validate(self) -> None:
        """Validate laser configuration."""
        # Validate power is within limits
        if not self.power_limits.is_valid_power(
            self.settings.power_setpoint_mw,
            use_safe_limit=False
        ):
            raise ValidationError(
                f"Power {self.settings.power_setpoint_mw} mW outside limits "
                f"({self.power_limits.min_mw}-{self.power_limits.max_mw} mW)"
            )

        # Validate channel ID
        if self.channel_id < 0:
            raise ValidationError(f"Invalid channel ID: {self.channel_id}")

        # Validate wavelength
        validate_range(
            self.calibration.wavelength_nm,
            min_val=200.0, max_val=2000.0,
            field_name="wavelength_nm"
        )

    def set_power(self, power_mw: float, use_safe_limit: bool = True) -> None:
        """Set laser power with validation.

        Args:
            power_mw: Desired power in milliwatts
            use_safe_limit: Whether to enforce safe power limit
        """
        # Clamp to valid range
        clamped_power = self.power_limits.clamp_power(power_mw, use_safe_limit)

        # Quantize to valid increment
        quantized_power = self.power_limits.quantize_power(clamped_power)

        self.settings.power_setpoint_mw = quantized_power
        self.settings.validate()
        self.update()

    def enable(self) -> bool:
        """Enable laser if safety conditions are met.

        Returns:
            True if laser was enabled
        """
        if not self.safety_status.is_safe_to_operate():
            self.state = LaserState.INTERLOCK
            self.error_message = "Safety interlock prevents operation"
            return False

        self.settings.enabled = True
        self.last_enabled_time = datetime.now()

        # Set state based on warmup requirements
        if self.warmup_time_seconds > 0:
            self.state = LaserState.WARMING
        else:
            self.state = LaserState.READY

        self.update()
        return True

    def disable(self) -> None:
        """Disable laser."""
        self.settings.enabled = False
        self.settings.shutter_open = False
        self.state = LaserState.OFF
        self.update()

    def is_warmed_up(self) -> bool:
        """Check if laser has completed warmup.

        Returns:
            True if warmed up or no warmup required
        """
        if self.warmup_time_seconds <= 0:
            return True

        if not self.last_enabled_time:
            return False

        elapsed = (datetime.now() - self.last_enabled_time).total_seconds()
        return elapsed >= self.warmup_time_seconds

    def get_actual_output_power(self) -> float:
        """Get actual output power based on calibration.

        Returns:
            Actual output power in milliwatts
        """
        effective_power = self.settings.get_effective_power()

        if effective_power == 0:
            return 0.0

        return self.calibration.get_actual_power(effective_power)

    @classmethod
    def create_default(cls, name: str, wavelength_nm: float, channel_id: int = 0) -> 'Laser':
        """Create a laser with default settings.

        Args:
            name: Laser name/label
            wavelength_nm: Laser wavelength
            channel_id: Channel identifier

        Returns:
            Laser with default configuration
        """
        power_limits = PowerLimits(min_mw=0, max_mw=100, safe_max_mw=50)
        settings = LaserSettings(power_setpoint_mw=0)
        calibration = LaserCalibration(wavelength_nm=wavelength_nm)

        return cls(
            name=name,
            channel_id=channel_id,
            laser_type=LaserType.DIODE,
            power_limits=power_limits,
            settings=settings,
            calibration=calibration
        )