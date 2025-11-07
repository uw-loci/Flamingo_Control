# src/py2flamingo/services/position_preset_service.py

"""
Service for managing saved stage position presets.

This service handles saving, loading, and deleting named position presets
that allow users to quickly return to frequently-used stage locations.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

from py2flamingo.models.microscope import Position


@dataclass
class PositionPreset:
    """Named position preset."""
    name: str
    x: float
    y: float
    z: float
    r: float
    description: str = ""

    def to_position(self) -> Position:
        """Convert preset to Position object."""
        return Position(x=self.x, y=self.y, z=self.z, r=self.r)

    @classmethod
    def from_position(cls, name: str, position: Position, description: str = "") -> 'PositionPreset':
        """Create preset from Position object."""
        return cls(
            name=name,
            x=position.x,
            y=position.y,
            z=position.z,
            r=position.r,
            description=description
        )


class PositionPresetService:
    """
    Service for managing position presets.

    Presets are stored in a JSON file in the microscope_settings directory.
    """

    def __init__(self, presets_file: Optional[str] = None):
        """
        Initialize position preset service.

        Args:
            presets_file: Path to presets JSON file. If None, uses default location.
        """
        self.logger = logging.getLogger(__name__)

        if presets_file is None:
            settings_dir = Path("microscope_settings")
            settings_dir.mkdir(exist_ok=True)
            self.presets_file = settings_dir / "position_presets.json"
        else:
            self.presets_file = Path(presets_file)

        self._presets: Dict[str, PositionPreset] = {}
        self._load_presets()

    def _load_presets(self) -> None:
        """Load presets from JSON file."""
        try:
            if self.presets_file.exists():
                with open(self.presets_file, 'r') as f:
                    data = json.load(f)
                    self._presets = {
                        name: PositionPreset(**preset_data)
                        for name, preset_data in data.items()
                    }
                self.logger.info(f"Loaded {len(self._presets)} position presets from {self.presets_file}")
            else:
                self.logger.info(f"No preset file found at {self.presets_file}, starting with empty presets")
                self._presets = {}
        except Exception as e:
            self.logger.error(f"Error loading presets: {e}", exc_info=True)
            self._presets = {}

    def _save_presets(self) -> None:
        """Save presets to JSON file."""
        try:
            data = {
                name: asdict(preset)
                for name, preset in self._presets.items()
            }
            with open(self.presets_file, 'w') as f:
                json.dump(data, f, indent=2)
            self.logger.info(f"Saved {len(self._presets)} presets to {self.presets_file}")
        except Exception as e:
            self.logger.error(f"Error saving presets: {e}", exc_info=True)
            raise

    def save_preset(self, name: str, position: Position, description: str = "") -> None:
        """
        Save a position preset.

        Args:
            name: Name for the preset
            position: Position to save
            description: Optional description

        Raises:
            ValueError: If name is empty or invalid
        """
        if not name or not name.strip():
            raise ValueError("Preset name cannot be empty")

        name = name.strip()

        preset = PositionPreset.from_position(name, position, description)
        self._presets[name] = preset
        self._save_presets()

        self.logger.info(f"Saved preset '{name}': X={position.x:.3f}, Y={position.y:.3f}, Z={position.z:.3f}, R={position.r:.2f}")

    def get_preset(self, name: str) -> Optional[PositionPreset]:
        """
        Get a preset by name.

        Args:
            name: Preset name

        Returns:
            PositionPreset if found, None otherwise
        """
        return self._presets.get(name)

    def delete_preset(self, name: str) -> bool:
        """
        Delete a preset.

        Args:
            name: Preset name

        Returns:
            True if preset was deleted, False if not found
        """
        if name in self._presets:
            del self._presets[name]
            self._save_presets()
            self.logger.info(f"Deleted preset '{name}'")
            return True
        return False

    def list_presets(self) -> List[PositionPreset]:
        """
        Get list of all presets.

        Returns:
            List of presets sorted by name
        """
        return sorted(self._presets.values(), key=lambda p: p.name)

    def get_preset_names(self) -> List[str]:
        """
        Get list of preset names.

        Returns:
            List of preset names sorted alphabetically
        """
        return sorted(self._presets.keys())

    def preset_exists(self, name: str) -> bool:
        """
        Check if preset exists.

        Args:
            name: Preset name

        Returns:
            True if preset exists
        """
        return name in self._presets

    def clear_all_presets(self) -> None:
        """Delete all presets (for testing/reset)."""
        self._presets.clear()
        self._save_presets()
        self.logger.warning("Cleared all position presets")
