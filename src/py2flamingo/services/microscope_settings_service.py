"""
Microscope Settings Service - Per-Microscope Configuration

This service manages microscope-specific settings stored in JSON format.
Each microscope has its own settings file (e.g., zion_settings.json)
containing:
- Position history configuration
- Stage axis limits
- Other expandable settings

Settings are loaded based on the microscope name from ScopeSettings.txt
and can be easily updated without modifying code.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional


class MicroscopeSettingsService:
    """Service for managing per-microscope settings from JSON files.

    Features:
    - Loads settings from {microscope_name}_settings.json
    - Provides stage limits with proper min/max values
    - Stores position history configuration
    - Expandable for future settings
    - Falls back to safe defaults if file missing
    """

    def __init__(self, microscope_name: str, base_path: Optional[Path] = None):
        """Initialize microscope settings service.

        Args:
            microscope_name: Name of the microscope (e.g., "zion")
            base_path: Base path for project (defaults to current directory)
        """
        self.logger = logging.getLogger(__name__)
        self.microscope_name = microscope_name
        self.base_path = base_path or Path.cwd()
        self.settings_file = (
            self.base_path / "microscope_settings" / f"{microscope_name}_settings.json"
        )

        print(f"[MicroscopeSettingsService] Initializing for microscope: '{microscope_name}'")
        print(f"[MicroscopeSettingsService] Base path: {self.base_path}")
        print(f"[MicroscopeSettingsService] Looking for settings file: {self.settings_file}")
        print(f"[MicroscopeSettingsService] Settings file exists: {self.settings_file.exists()}")

        self.settings = self._load_settings()

    def _load_settings(self) -> Dict[str, Any]:
        """Load microscope-specific settings from JSON file.

        Returns:
            Dict containing all settings

        Raises:
            FileNotFoundError: If settings file doesn't exist
        """
        if not self.settings_file.exists():
            print(f"[MicroscopeSettingsService] ✗ Settings file NOT FOUND!")
            print(f"[MicroscopeSettingsService]   Expected: {self.settings_file}")
            print(f"[MicroscopeSettingsService]   Base path: {self.base_path}")
            print(f"[MicroscopeSettingsService]   Microscope name: '{self.microscope_name}'")
            print(f"[MicroscopeSettingsService] ⚠ Falling back to DEFAULT settings (stage limits will be 0-26)")

            self.logger.warning(
                f"[MicroscopeSettingsService] Settings file NOT FOUND: {self.settings_file}"
            )
            self.logger.warning(
                f"[MicroscopeSettingsService] Base path: {self.base_path}, Microscope name: '{self.microscope_name}'"
            )
            self.logger.warning(
                f"[MicroscopeSettingsService] Falling back to DEFAULT settings (stage limits will be 0-26)"
            )
            return self._get_default_settings()

        try:
            with open(self.settings_file, 'r') as f:
                settings = json.load(f)
                print(f"[MicroscopeSettingsService] ✓ Successfully loaded settings from: {self.settings_file}")
                self.logger.info(
                    f"[MicroscopeSettingsService] Successfully loaded settings for microscope '{self.microscope_name}' "
                    f"from {self.settings_file}"
                )
                # Log stage limits to verify correct file was loaded
                if 'stage_limits' in settings:
                    limits = settings['stage_limits']
                    print(f"[MicroscopeSettingsService] Settings file contains stage limits:")
                    print(f"  X: {limits['x']['min']} to {limits['x']['max']} mm")
                    print(f"  Y: {limits['y']['min']} to {limits['y']['max']} mm")
                    print(f"  Z: {limits['z']['min']} to {limits['z']['max']} mm")
                    self.logger.info(
                        f"[MicroscopeSettingsService] File contains stage limits: "
                        f"X={limits['x']['min']}-{limits['x']['max']}, "
                        f"Y={limits['y']['min']}-{limits['y']['max']}, "
                        f"Z={limits['z']['min']}-{limits['z']['max']}"
                    )
                return settings
        except Exception as e:
            print(f"[MicroscopeSettingsService] ✗ Error loading settings file: {e}")
            self.logger.error(f"[MicroscopeSettingsService] Error loading settings file: {e}")
            return self._get_default_settings()

    def _get_default_settings(self) -> Dict[str, Any]:
        """Get default settings if file doesn't exist.

        Returns:
            Dict with safe default values
        """
        self.logger.warning(
            f"[MicroscopeSettingsService] Using DEFAULT settings for '{self.microscope_name}' "
            f"(stage limits will be 0-26 for all axes). Expected file: {self.settings_file}"
        )
        return {
            "microscope_name": self.microscope_name,
            "position_history": {
                "max_size": 100,
                "display_count": 20
            },
            "stage_limits": {
                "x": {"min": 0.0, "max": 26.0, "unit": "mm"},
                "y": {"min": 0.0, "max": 26.0, "unit": "mm"},
                "z": {"min": 0.0, "max": 26.0, "unit": "mm"},
                "r": {"min": -720.0, "max": 720.0, "unit": "degrees"}
            },
            "version": "1.0"
        }

    def get_stage_limits(self) -> Dict[str, Dict[str, float]]:
        """Get stage movement limits for all axes.

        Returns:
            Dict with min/max for each axis (x, y, z, r)

        Example:
            >>> limits = settings.get_stage_limits()
            >>> limits['x']
            {'min': 1.0, 'max': 12.31}
        """
        stage_limits = self.settings.get('stage_limits', {})

        return {
            'x': {
                'min': float(stage_limits.get('x', {}).get('min', 0.0)),
                'max': float(stage_limits.get('x', {}).get('max', 26.0))
            },
            'y': {
                'min': float(stage_limits.get('y', {}).get('min', 0.0)),
                'max': float(stage_limits.get('y', {}).get('max', 26.0))
            },
            'z': {
                'min': float(stage_limits.get('z', {}).get('min', 0.0)),
                'max': float(stage_limits.get('z', {}).get('max', 26.0))
            },
            'r': {
                'min': float(stage_limits.get('r', {}).get('min', -720.0)),
                'max': float(stage_limits.get('r', {}).get('max', 720.0))
            }
        }

    def get_position_history_max_size(self) -> int:
        """Get maximum size for position history storage.

        Returns:
            Maximum number of positions to store
        """
        return self.settings.get('position_history', {}).get('max_size', 100)

    def get_position_history_display_count(self) -> int:
        """Get number of positions to display in history dialog.

        Returns:
            Number of visible positions in list
        """
        return self.settings.get('position_history', {}).get('display_count', 20)

    def save_settings(self) -> None:
        """Save current settings back to JSON file.

        This allows programmatic updates to settings.
        """
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, indent=2, fp=f)

            self.logger.info(f"Saved settings to {self.settings_file}")

        except Exception as e:
            self.logger.error(f"Error saving settings: {e}")

    def update_setting(self, key_path: str, value: Any) -> None:
        """Update a specific setting value.

        Args:
            key_path: Dot-separated path to setting (e.g., "stage_limits.x.max")
            value: New value for the setting

        Example:
            >>> settings.update_setting("stage_limits.x.max", 15.0)
            >>> settings.save_settings()
        """
        keys = key_path.split('.')
        current = self.settings

        # Navigate to the parent of the target key
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        # Set the value
        current[keys[-1]] = value
        self.logger.info(f"Updated setting: {key_path} = {value}")

    def get_setting(self, key_path: str, default: Any = None) -> Any:
        """Get a specific setting value.

        Args:
            key_path: Dot-separated path to setting
            default: Default value if setting not found

        Returns:
            Setting value or default

        Example:
            >>> max_history = settings.get_setting("position_history.max_size", 100)
        """
        keys = key_path.split('.')
        current = self.settings

        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default

        return current
