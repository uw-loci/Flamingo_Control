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

    #: The shared file presets lived in before they were split per microscope.
    LEGACY_FILENAME = "position_presets.json"

    def __init__(
        self,
        presets_file: Optional[str] = None,
        microscope_name: Optional[str] = None,
    ):
        """
        Initialize position preset service.

        Args:
            presets_file: Explicit path to a presets JSON file. Overrides the
                per-microscope resolution below; mainly for tests.
            microscope_name: Scope to load presets for. If None, read from
                ScopeSettings.txt (the same source every other per-microscope
                lookup uses).

        Presets are stage coordinates, so they are per-instrument: n7's
        ``CalibrationInsert`` sits at x 6.78, z 18.51, both outside Liara's
        0-5 / 0-15 envelope. ``move_to_position(validate=True)`` CLAMPS rather
        than refuses, so a shared file meant clicking a named preset moved the
        stage to a silently different place while the UI reported the name.
        """
        super().__init__()
        self.logger = logging.getLogger(__name__)

        if presets_file is not None:
            self.presets_file = Path(presets_file)
        else:
            settings_dir = Path("microscope_settings")
            settings_dir.mkdir(exist_ok=True)
            name = microscope_name or self._resolve_microscope_name()
            if name:
                self.presets_file = settings_dir / f"{name}_position_presets.json"
                self._migrate_legacy_presets(settings_dir, name)
            else:
                # No scope name to attribute them to (offline / headless): keep
                # using the shared file rather than inventing an owner.
                self.presets_file = settings_dir / self.LEGACY_FILENAME

        self._presets: Dict[str, PositionPreset] = {}
        self._load_presets()

    @staticmethod
    def _resolve_microscope_name() -> Optional[str]:
        """Scope name from ScopeSettings.txt, or None."""
        try:
            from py2flamingo.configs.config_loader import _read_scope_microscope_name

            return _read_scope_microscope_name()
        except Exception:  # noqa: BLE001 - fall back to the shared file
            return None

    def _migrate_legacy_presets(self, settings_dir: Path, name: str) -> None:
        """Adopt the pre-split ``position_presets.json`` for this scope, if it fits.

        All-or-nothing, and only when every preset is reachable on THIS scope.
        The legacy file carries no record of which instrument it came from, so
        reachability is the only evidence available — and it is good evidence:
        all 8 of the presets that existed at the split are inside n7's envelope
        and every one of them is outside Liara's.

        Adopting a partial subset was rejected: half of another instrument's
        named positions is more confusing than none, and the ones that survive
        the filter are not thereby correct.

        The legacy file is COPIED, never moved or deleted, so the scope it
        really belongs to can still adopt it. A target file is written either
        way so the decision is made once rather than re-litigated on every
        construction (this service is built in ~10 places).
        """
        target = settings_dir / f"{name}_position_presets.json"
        legacy = settings_dir / self.LEGACY_FILENAME
        if target.exists() or not legacy.is_file():
            return

        try:
            data = json.loads(legacy.read_text()) or {}
        except Exception as e:  # noqa: BLE001 - never break startup over this
            self.logger.warning(
                f"Could not read {legacy} to migrate presets for '{name}': {e}. "
                f"Starting with no presets; the file is untouched."
            )
            return

        limits = self._stage_limits(name)
        if limits is None:
            # Deliberately does NOT write the target file: the answer here is
            # "not yet knowable", not "no presets". Writing one would lock in
            # an empty set before the scope is configured, and the migration
            # would never be retried once someone runs Microscope Setup.
            self.logger.warning(
                f"'{name}' has no stage-limit configuration, so there is no way "
                f"to tell whether the presets in {legacy.name} belong to it. "
                f"Not adopting them for now — run Edit > Microscope Setup and "
                f"this is retried automatically."
            )
            return

        unreachable = [
            preset_name
            for preset_name, p in data.items()
            if not self._within(p, limits)
        ]
        if unreachable:
            self.logger.warning(
                f"Not adopting {legacy.name} for '{name}': {len(unreachable)} of "
                f"{len(data)} preset(s) are outside this scope's stage limits "
                f"({', '.join(sorted(unreachable)[:5])}"
                f"{'...' if len(unreachable) > 5 else ''}), so the file belongs "
                f"to a different instrument. Starting with no presets."
            )
            data = {}
        else:
            self.logger.info(
                f"Adopted {len(data)} preset(s) from {legacy.name} for '{name}' "
                f"— every one is within this scope's stage limits. The original "
                f"is left in place and can be deleted once every microscope has "
                f"its own file."
            )

        try:
            target.write_text(json.dumps(data, indent=2))
        except Exception as e:  # noqa: BLE001
            self.logger.warning(f"Could not write {target}: {e}")

    @staticmethod
    def _stage_limits(name: str) -> Optional[Dict[str, Dict[str, float]]]:
        """This scope's stage limits, or None when it has no settings file.

        None matters: an unconfigured scope falls back to placeholder limits of
        0-26 mm on every axis, which are WIDER than any real instrument. Judging
        "reachable" against those would wave another scope's presets straight
        through, which is the failure this split exists to prevent.
        """
        try:
            from py2flamingo.services.microscope_settings_service import (
                MicroscopeSettingsService,
            )

            svc = MicroscopeSettingsService(name)
            if not svc.is_configured:
                return None
            return svc.get_stage_limits()
        except Exception:  # noqa: BLE001
            return None

    #: Tolerance (mm) when testing a preset against a stage limit. A position
    #: saved AT the limit comes back from the encoder a hair outside it —
    #: `beadsA` sits 1.4 NANOMETRES past n7's own y max of 25.0, which was
    #: enough to make n7 disown its own preset file. 1 um is far below stage
    #: repeatability and nowhere near the millimetres that separate one
    #: instrument's envelope from another's, so it cannot mask a real mismatch.
    LIMIT_TOLERANCE_MM = 1e-3

    @classmethod
    def _within(cls, preset: dict, limits: Dict[str, Dict[str, float]]) -> bool:
        """Is every linear axis of ``preset`` inside ``limits`` (within tolerance)?

        Rotation is excluded: r is periodic and its limits are +/-720 on every
        instrument, so it carries no information about which scope a preset
        came from.
        """
        tol = cls.LIMIT_TOLERANCE_MM
        for axis in ("x", "y", "z"):
            try:
                value = float(preset[axis])
            except (KeyError, TypeError, ValueError):
                return False
            bound = limits.get(axis) or {}
            lo = float(bound.get("min", 0.0)) - tol
            hi = float(bound.get("max", 0.0)) + tol
            if not (lo <= value <= hi):
                return False
        return True

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
