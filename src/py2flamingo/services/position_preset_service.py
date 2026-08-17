# src/py2flamingo/services/position_preset_service.py

"""
Service for managing saved stage position presets.

This service handles saving, loading, and deleting named position presets
that allow users to quickly return to frequently-used stage locations.
"""

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import QObject, pyqtSignal

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
    def from_position(
        cls, name: str, position: Position, description: str = ""
    ) -> "PositionPreset":
        """Create preset from Position object."""
        return cls(
            name=name,
            x=position.x,
            y=position.y,
            z=position.z,
            r=position.r,
            description=description,
        )


class PositionPresetService(QObject):
    """
    Service for managing position presets.

    Presets are stored in a JSON file in the microscope_settings directory.

    Emits ``presets_changed`` whenever the set of presets is mutated (save,
    delete, clear) so any view listing presets — e.g. the Stage tab and the
    Workflow tab's Position A/B dropdowns — can refresh live instead of only at
    startup.
    """

    #: Emitted after the preset set changes (save/delete/clear).
    presets_changed = pyqtSignal()

    def __init__(self, presets_file: Optional[str] = None):
        """
        Initialize position preset service.

        Args:
            presets_file: Path to presets JSON file. If None, uses default location.
        """
        super().__init__()
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
        """Load presets from JSON file.

        Loads each preset independently and ignores unknown fields. It used to
        build them in one dict comprehension with ``PositionPreset(**data)``, so
        a single unrecognised key — anything a newer version had added — raised
        TypeError, hit the blanket except, and left ``self._presets`` empty. The
        next save then wrote that empty set back over the file: **every named
        position gone, permanently, with only a log line.** These are stage
        coordinates somebody found by hand.

        When the file exists but cannot be read at all, saving is disabled
        (``self._load_failed``) so a bad parse can never overwrite the original.
        """
        self._load_failed = False
        self._presets = {}
        if not self.presets_file.exists():
            self.logger.info(
                f"No preset file found at {self.presets_file}, starting with empty presets"
            )
            return

        try:
            with open(self.presets_file, "r") as f:
                data = json.load(f)
        except Exception as e:
            # The file is there but unreadable. Refuse to save over it.
            self._load_failed = True
            self.logger.error(
                f"Could not read {self.presets_file}: {e}. Saving is disabled for "
                f"this session so the existing presets are not overwritten; fix "
                f"or move the file and restart.",
                exc_info=True,
            )
            return

        known = {f.name for f in fields(PositionPreset)}
        skipped = []
        for name, preset_data in (data or {}).items():
            try:
                unknown = set(preset_data) - known
                if unknown:
                    self.logger.warning(
                        f"Preset '{name}' has field(s) this version does not "
                        f"know ({sorted(unknown)}); ignoring them. It was "
                        f"probably written by a newer Py2Flamingo."
                    )
                self._presets[name] = PositionPreset(
                    **{k: v for k, v in preset_data.items() if k in known}
                )
            except Exception as e:
                # One malformed preset must not cost the others.
                skipped.append(name)
                self.logger.error(f"Skipping unreadable preset '{name}': {e}")

        if skipped:
            self._load_failed = True
            self.logger.error(
                f"{len(skipped)} preset(s) could not be read ({skipped}); saving "
                f"is disabled so they are not dropped from the file."
            )
        self.logger.info(
            f"Loaded {len(self._presets)} position presets from {self.presets_file}"
        )

    def _save_presets(self) -> None:
        """Save presets to JSON file.

        Refuses when the load could not read the existing file. Writing the
        in-memory set over a file we failed to parse is how every preset would
        be destroyed by one unreadable entry.
        """
        if getattr(self, "_load_failed", False):
            self.logger.error(
                f"Refusing to save presets: {self.presets_file} could not be "
                f"fully read at startup, so writing now would discard whatever "
                f"is in it. Fix or move the file and restart."
            )
            raise RuntimeError(
                "Position presets were not fully loaded; saving is disabled to "
                "avoid overwriting the existing file."
            )
        try:
            data = {name: asdict(preset) for name, preset in self._presets.items()}
            with open(self.presets_file, "w") as f:
                json.dump(data, f, indent=2)
            self.logger.info(
                f"Saved {len(self._presets)} presets to {self.presets_file}"
            )
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

        self.logger.info(
            f"Saved preset '{name}': X={position.x:.3f}, Y={position.y:.3f}, Z={position.z:.3f}, R={position.r:.2f}"
        )
        self.presets_changed.emit()

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
            self.presets_changed.emit()
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
        self.presets_changed.emit()
