# ============================================================================
# src/py2flamingo/models/settings.py
"""
Data models for microscope settings.

This module defines data structures for managing microscope configuration
and settings.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum
import json
from pathlib import Path

from .microscope import Position


class FilterType(Enum):
    """Available filter types."""
    EMPTY = "Empty"
    GFP = "GFP"
    RFP = "RFP"
    DAPI = "DAPI"
    CFP = "CFP"
    YFP = "YFP"
    CUSTOM = "Custom"


class IlluminationPath(Enum):
    """Illumination path options."""
    LEFT = "left"
    RIGHT = "right"
    BOTH = "both"


@dataclass
class HomePosition:
    """
    Home position configuration for the microscope.
    
    Attributes:
        position: The home position coordinates
        is_set: Whether home position has been set
        microscope_name: Name of microscope this home belongs to
    """
    position: Position
    is_set: bool = True
    microscope_name: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary for saving.
        
        Returns:
            Dict[str, Any]: Dictionary representation
        """
        return {
            'x': self.position.x,
            'y': self.position.y,
            'z': self.position.z,
            'r': self.position.r,
            'is_set': self.is_set,
            'microscope_name': self.microscope_name
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'HomePosition':
        """
        Create HomePosition from dictionary.
        
        Args:
            data: Dictionary containing home position data
            
        Returns:
            HomePosition: New instance
        """
        position = Position(
            x=data.get('x', 0.0),
            y=data.get('y', 0.0),
            z=data.get('z', 0.0),
            r=data.get('r', 0.0)
        )
        return cls(
            position=position,
            is_set=data.get('is_set', True),
            microscope_name=data.get('microscope_name')
        )


@dataclass
class StageLimit:
    """
    Stage movement limits for a single axis.
    
    Attributes:
        min_value: Minimum allowed position
        max_value: Maximum allowed position
        soft_min: Soft limit minimum (warning)
        soft_max: Soft limit maximum (warning)
    """
    min_value: float
    max_value: float
    soft_min: Optional[float] = None
    soft_max: Optional[float] = None
    
    def __post_init__(self):
        """Validate limits after initialization."""
        if self.soft_min is None:
            self.soft_min = self.min_value
        if self.soft_max is None:
            self.soft_max = self.max_value
            
        if self.min_value > self.max_value:
            raise ValueError("min_value must be less than max_value")
        if self.soft_min < self.min_value or self.soft_max > self.max_value:
            raise ValueError("Soft limits must be within hard limits")


@dataclass
class CameraSettings:
    """
    Camera capture and overlap settings.
    
    Attributes:
        overlap_percent: Percentage overlap between tiles
        exposure_time_ms: Exposure time in milliseconds
        binning: Camera binning factor
        roi: Region of interest (x, y, width, height)
        bit_depth: Camera bit depth
    """
    overlap_percent: float = 10.0
    exposure_time_ms: float = 100.0
    binning: int = 1
    roi: Optional[tuple[int, int, int, int]] = None
    bit_depth: int = 16
    
    def validate(self):
        """Validate camera settings."""
        if not 0 <= self.overlap_percent <= 50:
            raise ValueError("Overlap must be between 0 and 50 percent")
        if self.exposure_time_ms <= 0:
            raise ValueError("Exposure time must be positive")
        if self.binning not in [1, 2, 4, 8]:
            raise ValueError("Binning must be 1, 2, 4, or 8")


@dataclass
class LEDSettings:
    """
    LED illumination settings.
    
    Attributes:
        intensity: LED intensity (0-100)
        wavelength: LED wavelength in nm
        enabled: Whether LED is enabled
        pulse_mode: Whether to use pulse mode
        pulse_duration_ms: Pulse duration if pulse mode enabled
    """
    intensity: float = 0.0
    wavelength: Optional[int] = None
    enabled: bool = False
    pulse_mode: bool = False
    pulse_duration_ms: float = 10.0
    
    def validate(self):
        """Validate LED settings."""
        if not 0 <= self.intensity <= 100:
            raise ValueError("LED intensity must be between 0 and 100")
        if self.pulse_mode and self.pulse_duration_ms <= 0:
            raise ValueError("Pulse duration must be positive")


@dataclass
class MicroscopeSettings:
    """
    Complete microscope settings.
    
    Attributes:
        filter_wheel_positions: Filter wheel encoder positions
        illumination_path: Current illumination path
        stage_limits: Stage movement limits for each axis
        camera_settings: Camera overlap and capture settings
        led_settings: LED control settings by channel
        microscope_type: Type of microscope
        home_position: Home position for stage
        autofocus_enabled: Whether autofocus is enabled
        temperature_control: Temperature control settings
    """
    filter_wheel_positions: Dict[int, str] = field(default_factory=dict)
    illumination_path: IlluminationPath = IlluminationPath.BOTH
    stage_limits: Dict[str, StageLimit] = field(default_factory=dict)
    camera_settings: CameraSettings = field(default_factory=CameraSettings)
    led_settings: Dict[str, LEDSettings] = field(default_factory=dict)
    microscope_type: str = "FlamingoBeta"
    home_position: Optional[HomePosition] = None
    autofocus_enabled: bool = False
    temperature_control: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MicroscopeSettings':
        """
        Create MicroscopeSettings from dictionary.
        
        Args:
            data: Dictionary containing settings data
            
        Returns:
            MicroscopeSettings: New instance
        """
        # Convert stage limits
        stage_limits = {}
        for axis, limits in data.get('stage_limits', {}).items():
            if isinstance(limits, dict):
                stage_limits[axis] = StageLimit(**limits)
            else:
                # Handle legacy format
                stage_limits[axis] = StageLimit(
                    min_value=limits[0],
                    max_value=limits[1]
                )
        
        # Convert camera settings
        camera_data = data.get('camera_settings', {})
        if isinstance(camera_data, dict):
            camera_settings = CameraSettings(**camera_data)
        else:
            camera_settings = CameraSettings()
        
        # Convert LED settings
        led_settings = {}
        for channel, settings in data.get('led_settings', {}).items():
            if isinstance(settings, dict):
                led_settings[channel] = LEDSettings(**settings)
        
        # Convert home position
        home_data = data.get('home_position')
        home_position = None
        if home_data:
            home_position = HomePosition.from_dict(home_data)
        
        # Convert illumination path
        illum_path = data.get('illumination_path', 'both')
        if isinstance(illum_path, str):
            illumination_path = IlluminationPath(illum_path)
        else:
            illumination_path = IlluminationPath.BOTH
        
        return cls(
            filter_wheel_positions=data.get('filter_wheel_positions', {}),
            illumination_path=illumination_path,
            stage_limits=stage_limits,
            camera_settings=camera_settings,
            led_settings=led_settings,
            microscope_type=data.get('microscope_type', 'FlamingoBeta'),
            home_position=home_position,
            autofocus_enabled=data.get('autofocus_enabled', False),
            temperature_control=data.get('temperature_control', {})
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary for saving.
        
        Returns:
            Dict[str, Any]: Dictionary representation
        """
        return {
            'filter_wheel_positions': self.filter_wheel_positions,
            'illumination_path': self.illumination_path.value,
            'stage_limits': {
                axis: {
                    'min_value': limit.min_value,
                    'max_value': limit.max_value,
                    'soft_min': limit.soft_min,
                    'soft_max': limit.soft_max
                }
                for axis, limit in self.stage_limits.items()
            },
            'camera_settings': {
                'overlap_percent': self.camera_settings.overlap_percent,
                'exposure_time_ms': self.camera_settings.exposure_time_ms,
                'binning': self.camera_settings.binning,
                'roi': self.camera_settings.roi,
                'bit_depth': self.camera_settings.bit_depth
            },
            'led_settings': {
                channel: {
                    'intensity': settings.intensity,
                    'wavelength': settings.wavelength,
                    'enabled': settings.enabled,
                    'pulse_mode': settings.pulse_mode,
                    'pulse_duration_ms': settings.pulse_duration_ms
                }
                for channel, settings in self.led_settings.items()
            },
            'microscope_type': self.microscope_type,
            'home_position': self.home_position.to_dict() if self.home_position else None,
            'autofocus_enabled': self.autofocus_enabled,
            'temperature_control': self.temperature_control
        }
    
    def save_to_file(self, filepath: Path):
        """
        Save settings to JSON file.
        
        Args:
            filepath: Path to save settings file
        """
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def load_from_file(cls, filepath: Path) -> 'MicroscopeSettings':
        """
        Load settings from JSON file.
        
        Args:
            filepath: Path to settings file
            
        Returns:
            MicroscopeSettings: Loaded settings
        """
        with open(filepath, 'r') as f:
            data = json.load(f)
        return cls.from_dict(data)
    
    def validate(self):
        """Validate all settings."""
        # Validate camera settings
        self.camera_settings.validate()
        
        # Validate LED settings
        for settings in self.led_settings.values():
            settings.validate()
        
        # Validate stage limits
        for limit in self.stage_limits.values():
            # Validation happens in StageLimit.__post_init__
            pass
    
    def get_stage_limit(self, axis: str) -> Optional[StageLimit]:
        """
        Get stage limit for specific axis.
        
        Args:
            axis: Axis name ('x', 'y', 'z', or 'r')
            
        Returns:
            Optional[StageLimit]: Stage limit if defined
        """
        return self.stage_limits.get(axis)
    
    def set_filter_position(self, position: int, filter_type: str):
        """
        Set filter type for a wheel position.
        
        Args:
            position: Filter wheel position
            filter_type: Type of filter
        """
        self.filter_wheel_positions[position] = filter_type
    
    def get_filter_at_position(self, position: int) -> Optional[str]:
        """
        Get filter type at specific position.
        
        Args:
            position: Filter wheel position
            
        Returns:
            Optional[str]: Filter type if defined
        """
        return self.filter_wheel_positions.get(position)


@dataclass
class SettingsManager:
    """
    Manager for handling multiple microscope settings.
    
    Attributes:
        settings_dir: Directory to store settings files
        current_settings: Currently active settings
        available_profiles: List of available setting profiles
    """
    settings_dir: Path
    current_settings: Optional[MicroscopeSettings] = None
    available_profiles: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        """Initialize settings directory and load profiles."""
        self.settings_dir.mkdir(parents=True, exist_ok=True)
        self.refresh_profiles()
    
    def refresh_profiles(self):
        """Refresh list of available setting profiles."""
        self.available_profiles = [
            f.stem for f in self.settings_dir.glob("*.json")
        ]
    
    def load_profile(self, profile_name: str):
        """
        Load a settings profile.
        
        Args:
            profile_name: Name of profile to load
        """
        filepath = self.settings_dir / f"{profile_name}.json"
        if not filepath.exists():
            raise FileNotFoundError(f"Profile {profile_name} not found")
        
        self.current_settings = MicroscopeSettings.load_from_file(filepath)
    
    def save_profile(self, profile_name: str, settings: Optional[MicroscopeSettings] = None):
        """
        Save settings to a profile.
        
        Args:
            profile_name: Name for the profile
            settings: Settings to save (uses current if None)
        """
        if settings is None:
            settings = self.current_settings
        
        if settings is None:
            raise ValueError("No settings to save")
        
        filepath = self.settings_dir / f"{profile_name}.json"
        settings.save_to_file(filepath)
        self.refresh_profiles()
    
    def delete_profile(self, profile_name: str):
        """
        Delete a settings profile.
        
        Args:
            profile_name: Name of profile to delete
        """
        filepath = self.settings_dir / f"{profile_name}.json"
        if filepath.exists():
            filepath.unlink()
            self.refresh_profiles()
    
    def get_default_settings(self) -> MicroscopeSettings:
        """
        Get default microscope settings.
        
        Returns:
            MicroscopeSettings: Default settings
        """
        return MicroscopeSettings(
            filter_wheel_positions={
                1: FilterType.EMPTY.value,
                2: FilterType.GFP.value,
                3: FilterType.RFP.value,
                4: FilterType.DAPI.value
            },
            stage_limits={
                'x': StageLimit(min_value=0.0, max_value=100.0),
                'y': StageLimit(min_value=0.0, max_value=100.0),
                'z': StageLimit(min_value=0.0, max_value=50.0),
                'r': StageLimit(min_value=-180.0, max_value=180.0)
            },
            camera_settings=CameraSettings(
                overlap_percent=10.0,
                exposure_time_ms=100.0,
                binning=1,
                bit_depth=16
            ),
            led_settings={
                '488nm': LEDSettings(wavelength=488),
                '561nm': LEDSettings(wavelength=561),
                '405nm': LEDSettings(wavelength=405)
            }
        )
