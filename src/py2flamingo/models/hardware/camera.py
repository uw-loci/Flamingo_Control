"""Camera models for image acquisition hardware.

This module provides models for camera configuration,
region of interest (ROI) settings, and acquisition parameters.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum
from ..base import ValidatedModel, ValidationError, validate_range


class TriggerMode(Enum):
    """Camera triggering modes."""
    INTERNAL = "internal"      # Camera runs at its own frame rate
    EXTERNAL = "external"      # External trigger signal
    SOFTWARE = "software"      # Software triggered
    CONTINUOUS = "continuous"  # Free-running mode


class BinningMode(Enum):
    """Camera pixel binning modes."""
    BIN_1X1 = (1, 1)
    BIN_2X2 = (2, 2)
    BIN_4X4 = (4, 4)
    BIN_8X8 = (8, 8)

    @property
    def x(self) -> int:
        """X-axis binning factor."""
        return self.value[0]

    @property
    def y(self) -> int:
        """Y-axis binning factor."""
        return self.value[1]


class PixelFormat(Enum):
    """Camera pixel data formats."""
    MONO8 = "Mono8"
    MONO12 = "Mono12"
    MONO16 = "Mono16"
    RGB8 = "RGB8"
    BGR8 = "BGR8"


@dataclass
class ROI(ValidatedModel):
    """Region of Interest for camera acquisition.

    Defines a rectangular region within the camera sensor
    to acquire images from.
    """
    x: int              # X offset in pixels
    y: int              # Y offset in pixels
    width: int          # Width in pixels
    height: int         # Height in pixels
    sensor_width: int   # Total sensor width
    sensor_height: int  # Total sensor height

    def validate(self) -> None:
        """Validate ROI parameters."""
        # Validate non-negative values
        if self.x < 0 or self.y < 0:
            raise ValidationError(f"ROI offset cannot be negative: x={self.x}, y={self.y}")

        if self.width <= 0 or self.height <= 0:
            raise ValidationError(f"ROI dimensions must be positive: {self.width}x{self.height}")

        # Validate ROI fits within sensor
        if self.x + self.width > self.sensor_width:
            raise ValidationError(
                f"ROI extends beyond sensor width: {self.x + self.width} > {self.sensor_width}"
            )

        if self.y + self.height > self.sensor_height:
            raise ValidationError(
                f"ROI extends beyond sensor height: {self.y + self.height} > {self.sensor_height}"
            )

    @classmethod
    def full_frame(cls, sensor_width: int, sensor_height: int) -> 'ROI':
        """Create a full-frame ROI.

        Args:
            sensor_width: Width of camera sensor in pixels
            sensor_height: Height of camera sensor in pixels

        Returns:
            ROI covering entire sensor
        """
        return cls(
            x=0, y=0,
            width=sensor_width,
            height=sensor_height,
            sensor_width=sensor_width,
            sensor_height=sensor_height
        )

    @classmethod
    def centered(cls, width: int, height: int,
                sensor_width: int, sensor_height: int) -> 'ROI':
        """Create a centered ROI.

        Args:
            width: ROI width in pixels
            height: ROI height in pixels
            sensor_width: Total sensor width
            sensor_height: Total sensor height

        Returns:
            Centered ROI
        """
        x = (sensor_width - width) // 2
        y = (sensor_height - height) // 2
        return cls(
            x=x, y=y,
            width=width, height=height,
            sensor_width=sensor_width,
            sensor_height=sensor_height
        )

    def get_center(self) -> Tuple[int, int]:
        """Get center coordinates of ROI.

        Returns:
            Tuple of (center_x, center_y)
        """
        return (self.x + self.width // 2, self.y + self.height // 2)

    def scale(self, factor: float) -> 'ROI':
        """Create a scaled ROI maintaining center position.

        Args:
            factor: Scale factor (>1 to enlarge, <1 to shrink)

        Returns:
            New scaled ROI
        """
        new_width = int(self.width * factor)
        new_height = int(self.height * factor)
        center_x, center_y = self.get_center()

        new_x = center_x - new_width // 2
        new_y = center_y - new_height // 2

        # Clamp to sensor boundaries
        new_x = max(0, min(new_x, self.sensor_width - new_width))
        new_y = max(0, min(new_y, self.sensor_height - new_height))

        return ROI(
            x=new_x, y=new_y,
            width=new_width, height=new_height,
            sensor_width=self.sensor_width,
            sensor_height=self.sensor_height
        )


@dataclass
class ExposureSettings:
    """Camera exposure settings."""
    exposure_time_ms: float
    auto_exposure: bool = False
    min_exposure_ms: float = 0.01
    max_exposure_ms: float = 1000.0
    target_brightness: Optional[float] = None  # For auto-exposure

    def __post_init__(self):
        """Validate exposure settings."""
        if not self.auto_exposure:
            validate_range(
                self.exposure_time_ms,
                min_val=self.min_exposure_ms,
                max_val=self.max_exposure_ms,
                field_name="exposure_time_ms"
            )

        if self.target_brightness is not None:
            validate_range(
                self.target_brightness,
                min_val=0.0, max_val=1.0,
                field_name="target_brightness"
            )


@dataclass
class GainSettings:
    """Camera gain settings."""
    gain_db: float
    auto_gain: bool = False
    min_gain_db: float = 0.0
    max_gain_db: float = 48.0

    def __post_init__(self):
        """Validate gain settings."""
        if not self.auto_gain:
            validate_range(
                self.gain_db,
                min_val=self.min_gain_db,
                max_val=self.max_gain_db,
                field_name="gain_db"
            )


@dataclass
class AcquisitionSettings(ValidatedModel):
    """Complete camera acquisition settings."""
    roi: ROI
    exposure: ExposureSettings
    gain: GainSettings
    binning: BinningMode = BinningMode.BIN_1X1
    pixel_format: PixelFormat = PixelFormat.MONO16
    trigger_mode: TriggerMode = TriggerMode.INTERNAL
    frame_rate_hz: Optional[float] = None
    bit_depth: int = 16
    gamma: float = 1.0
    black_level: float = 0.0
    white_balance: Optional[Dict[str, float]] = None  # For color cameras

    def validate(self) -> None:
        """Validate acquisition settings."""
        # Validate frame rate if specified
        if self.frame_rate_hz is not None:
            validate_range(
                self.frame_rate_hz,
                min_val=0.1, max_val=1000.0,
                field_name="frame_rate_hz"
            )

        # Validate bit depth matches pixel format
        format_bit_depths = {
            PixelFormat.MONO8: 8,
            PixelFormat.MONO12: 12,
            PixelFormat.MONO16: 16,
            PixelFormat.RGB8: 8,
            PixelFormat.BGR8: 8
        }

        expected_depth = format_bit_depths.get(self.pixel_format)
        if expected_depth and self.bit_depth != expected_depth:
            raise ValidationError(
                f"Bit depth {self.bit_depth} doesn't match format {self.pixel_format.value}"
            )

        # Validate gamma
        validate_range(self.gamma, min_val=0.1, max_val=10.0, field_name="gamma")

        # Validate black level
        validate_range(self.black_level, min_val=0.0, max_val=100.0, field_name="black_level")

    def get_effective_resolution(self) -> Tuple[int, int]:
        """Get effective image resolution after binning.

        Returns:
            Tuple of (width, height) in pixels
        """
        effective_width = self.roi.width // self.binning.x
        effective_height = self.roi.height // self.binning.y
        return (effective_width, effective_height)

    def get_pixel_size_um(self, sensor_pixel_size_um: float) -> float:
        """Calculate effective pixel size after binning.

        Args:
            sensor_pixel_size_um: Physical pixel size on sensor

        Returns:
            Effective pixel size in micrometers
        """
        return sensor_pixel_size_um * self.binning.x

    def estimate_data_rate_mbps(self) -> float:
        """Estimate data rate for current settings.

        Returns:
            Estimated data rate in megabits per second
        """
        width, height = self.get_effective_resolution()
        bytes_per_pixel = self.bit_depth / 8
        bytes_per_frame = width * height * bytes_per_pixel

        # Use frame rate or estimate from exposure
        if self.frame_rate_hz:
            fps = self.frame_rate_hz
        else:
            # Estimate from exposure time
            fps = min(1000.0 / self.exposure.exposure_time_ms, 100.0)

        bits_per_second = bytes_per_frame * fps * 8
        return bits_per_second / 1_000_000  # Convert to Mbps


@dataclass
class CameraCalibration:
    """Camera calibration data."""
    pixel_size_um: float          # Physical pixel size in micrometers
    quantum_efficiency: float     # Quantum efficiency (0-1)
    read_noise_electrons: float   # Read noise in electrons
    dark_current_eps: float       # Dark current in electrons/pixel/second
    full_well_capacity: int       # Maximum electrons per pixel
    calibration_date: Optional[str] = None
    temperature_c: Optional[float] = None  # Sensor temperature during calibration


@dataclass
class Camera(ValidatedModel):
    """Complete camera model with all settings and state."""
    model_name: str
    serial_number: str
    sensor_width_pixels: int
    sensor_height_pixels: int
    acquisition_settings: AcquisitionSettings
    calibration: Optional[CameraCalibration] = None
    is_acquiring: bool = False
    temperature_c: Optional[float] = None  # Current sensor temperature
    cooler_enabled: bool = False
    target_temperature_c: Optional[float] = None
    supported_formats: List[PixelFormat] = field(default_factory=list)
    supported_binning: List[BinningMode] = field(default_factory=list)

    def validate(self) -> None:
        """Validate camera configuration."""
        # Validate sensor dimensions
        if self.sensor_width_pixels <= 0 or self.sensor_height_pixels <= 0:
            raise ValidationError(
                f"Invalid sensor dimensions: {self.sensor_width_pixels}x{self.sensor_height_pixels}"
            )

        # Validate temperature settings
        if self.cooler_enabled and self.target_temperature_c is not None:
            validate_range(
                self.target_temperature_c,
                min_val=-100.0, max_val=50.0,
                field_name="target_temperature_c"
            )

    def start_acquisition(self) -> None:
        """Mark camera as acquiring."""
        self.is_acquiring = True
        self.update()

    def stop_acquisition(self) -> None:
        """Mark camera as not acquiring."""
        self.is_acquiring = False
        self.update()

    def get_field_of_view_mm(self, magnification: float) -> Tuple[float, float]:
        """Calculate field of view in millimeters.

        Args:
            magnification: Objective magnification

        Returns:
            Tuple of (width_mm, height_mm)
        """
        if not self.calibration:
            raise ValueError("Camera calibration required for FOV calculation")

        pixel_size_um = self.acquisition_settings.get_pixel_size_um(
            self.calibration.pixel_size_um
        )
        width_px, height_px = self.acquisition_settings.get_effective_resolution()

        width_mm = (width_px * pixel_size_um) / (magnification * 1000)
        height_mm = (height_px * pixel_size_um) / (magnification * 1000)

        return (width_mm, height_mm)

    @classmethod
    def create_default(cls) -> 'Camera':
        """Create a camera with default settings."""
        roi = ROI.full_frame(2048, 2048)
        exposure = ExposureSettings(exposure_time_ms=10.0)
        gain = GainSettings(gain_db=0.0)
        acquisition = AcquisitionSettings(roi=roi, exposure=exposure, gain=gain)

        return cls(
            model_name="Default Camera",
            serial_number="000000",
            sensor_width_pixels=2048,
            sensor_height_pixels=2048,
            acquisition_settings=acquisition,
            supported_formats=[PixelFormat.MONO8, PixelFormat.MONO16],
            supported_binning=[BinningMode.BIN_1X1, BinningMode.BIN_2X2]
        )