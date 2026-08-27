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
from typing import Any, Dict, Optional


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
        if not self.settings_file.exists():
            found = self._find_case_insensitive(microscope_name)
            if found is not None:
                self.logger.warning(
                    "[MicroscopeSettingsService] Settings file matched only by "
                    "case-folding: '%s' -> '%s'. The scope reports its name as "
                    "'%s'; rename the file to match exactly.",
                    self.settings_file.name,
                    found.name,
                    microscope_name,
                )
                # Adopt the found path so save_settings() writes back to the
                # same file rather than creating a second, differently-cased one.
                self.settings_file = found

        print(
            f"[MicroscopeSettingsService] Initializing for microscope: '{microscope_name}'"
        )
        print(f"[MicroscopeSettingsService] Base path: {self.base_path}")
        print(
            f"[MicroscopeSettingsService] Looking for settings file: {self.settings_file}"
        )
        print(
            f"[MicroscopeSettingsService] Settings file exists: {self.settings_file.exists()}"
        )

        # False when the settings file was absent and placeholders are standing
        # in. Anything that drives hardware, and the setup dialog, both key off
        # this rather than re-testing the file path.
        self.is_configured: bool = self.settings_file.exists()
        self.settings = self._load_settings()

    def _find_case_insensitive(self, microscope_name: str) -> Optional[Path]:
        """A ``{name}_settings.json`` differing from ``microscope_name`` only in case.

        The filename is built verbatim from the scope-reported name, but the
        per-microscope *visualization* overlay matches case-insensitively. On a
        case-sensitive filesystem that mismatch is dangerous rather than merely
        untidy: a scope reporting "Liara" against a ``liara_settings.json``
        finds no file, falls through to ``_get_default_settings()``, and gates
        the stage with the 0-26 mm placeholders -- which are WIDER than any real
        instrument. Matching here turns a silent widening into a warning.

        Returns None when there is no match or the directory is unreadable.
        """
        target = f"{microscope_name}_settings.json".lower()
        try:
            settings_dir = self.base_path / "microscope_settings"
            matches = [
                c
                for c in sorted(settings_dir.glob("*_settings.json"))
                if c.name.lower() == target
            ]
        except OSError:
            self.logger.debug("Could not scan for settings files", exc_info=True)
            return None
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Two files differing only in case. Picking one would be a coin
            # toss decided by ASCII order, and self.settings_file is also where
            # save_settings() writes — so Edit > Microscope Setup would tighten
            # one file while the other stayed in force. Refuse, and let the
            # not-configured path put the placeholders on the record instead.
            self.logger.critical(
                "[MicroscopeSettingsService] %d settings files match '%s' "
                "differing only in case (%s). Refusing to guess. Delete or "
                "merge all but one, named exactly as the scope reports itself.",
                len(matches),
                microscope_name,
                ", ".join(c.name for c in matches),
            )
        return None

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
            print(
                f"[MicroscopeSettingsService]   Microscope name: '{self.microscope_name}'"
            )
            print(
                f"[MicroscopeSettingsService] ⚠ Falling back to DEFAULT settings (stage limits will be 0-26)"
            )

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
            with open(self.settings_file, "r") as f:
                settings = json.load(f)
                print(
                    f"[MicroscopeSettingsService] ✓ Successfully loaded settings from: {self.settings_file}"
                )
                self.logger.info(
                    f"[MicroscopeSettingsService] Successfully loaded settings for microscope '{self.microscope_name}' "
                    f"from {self.settings_file}"
                )
                # The file's own idea of which scope it describes. A mismatch is
                # worth saying out loud: n7_settings.json's note records that its
                # limits were "copied from zion settings as a starting point",
                # which is exactly how one instrument's envelope ends up gating
                # another.
                claimed = str(settings.get("microscope_name", "") or "").strip()
                if claimed and claimed.lower() != str(self.microscope_name).lower():
                    self.logger.warning(
                        "[MicroscopeSettingsService] %s says it is for '%s', but "
                        "it was loaded for '%s'. Check that these stage limits "
                        "belong to the connected instrument.",
                        self.settings_file.name,
                        claimed,
                        self.microscope_name,
                    )

                # A file with NO stage_limits block used to take this branch
                # silently: is_configured stayed True while get_stage_limits()
                # handed back the 0-26 mm placeholders. That is the permissive
                # direction, and it defeats every guard that keys off
                # is_configured. A partial block already lands in the except
                # below (the log lines index it); a missing one must not be
                # treated as better-configured than a broken one.
                if not isinstance(settings.get("stage_limits"), dict):
                    self.is_configured = False
                    self.logger.error(
                        "[MicroscopeSettingsService] %s has no 'stage_limits' "
                        "block, so the PLACEHOLDER 0-26 mm limits apply — wider "
                        "than any real instrument. Marking '%s' NOT CONFIGURED; "
                        "run Edit > Microscope Setup before moving the stage.",
                        self.settings_file.name,
                        self.microscope_name,
                    )
                    return settings

                # Log stage limits to verify correct file was loaded
                if "stage_limits" in settings:
                    limits = settings["stage_limits"]
                    print(
                        f"[MicroscopeSettingsService] Settings file contains stage limits:"
                    )
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
            # The file exists but could not be read, so is_configured was
            # already set True from its mere existence in __init__. Clear it:
            # the placeholder limits substituted below are WIDER than the real
            # instrument (0-26 mm on X against N7's 1.0-12.31), and every guard
            # that protects the stage keys off is_configured. Leaving it True
            # here silently authorises moves the stage cannot make, which is
            # the exact hazard _get_default_settings' own docstring warns about.
            self.is_configured = False
            print(f"[MicroscopeSettingsService] ✗ Error loading settings file: {e}")
            self.logger.error(
                f"[MicroscopeSettingsService] Error loading settings file: {e}. "
                f"Falling back to PLACEHOLDER limits and marking this microscope "
                f"NOT CONFIGURED — run Edit > Microscope Setup, or fix "
                f"{self.settings_file}, before moving the stage."
            )
            return self._get_default_settings()

    def _get_default_settings(self) -> Dict[str, Any]:
        """Placeholder settings for a microscope that has never been set up.

        These limits are NOT safe, despite the old docstring saying so. They are
        a guess made in code, and on N7 the guess is WIDER than the instrument:
        0-26 mm on X against a real envelope of 1.0-12.31. A permissive
        fabricated limit is worse than none, because it silently authorises
        moves the stage cannot make.

        Kept only so the app can start and reach the setup dialog. ``is_configured``
        is False here, and callers that drive hardware should refuse or warn
        rather than trust these numbers.
        """
        self.logger.error(
            f"[MicroscopeSettingsService] No settings file for microscope "
            f"'{self.microscope_name}' ({self.settings_file}). Falling back to "
            f"PLACEHOLDER stage limits (0-26 mm), which are a guess and may be "
            f"WIDER than the instrument allows. Run microscope setup before "
            f"moving the stage."
        )
        return {
            "microscope_name": self.microscope_name,
            "position_history": {"max_size": 100, "display_count": 20},
            "stage_limits": {
                "x": {"min": 0.0, "max": 26.0, "unit": "mm"},
                "y": {"min": 0.0, "max": 26.0, "unit": "mm"},
                "z": {"min": 0.0, "max": 26.0, "unit": "mm"},
                "r": {"min": -720.0, "max": 720.0, "unit": "degrees"},
            },
            "version": "1.0",
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
        stage_limits = self.settings.get("stage_limits", {})

        return {
            "x": {
                "min": float(stage_limits.get("x", {}).get("min", 0.0)),
                "max": float(stage_limits.get("x", {}).get("max", 26.0)),
            },
            "y": {
                "min": float(stage_limits.get("y", {}).get("min", 0.0)),
                "max": float(stage_limits.get("y", {}).get("max", 26.0)),
            },
            "z": {
                "min": float(stage_limits.get("z", {}).get("min", 0.0)),
                "max": float(stage_limits.get("z", {}).get("max", 26.0)),
            },
            "r": {
                "min": float(stage_limits.get("r", {}).get("min", -720.0)),
                "max": float(stage_limits.get("r", {}).get("max", 720.0)),
            },
        }

    def get_position_history_max_size(self) -> int:
        """Get maximum size for position history storage.

        Returns:
            Maximum number of positions to store
        """
        return self.settings.get("position_history", {}).get("max_size", 100)

    def get_position_history_display_count(self) -> int:
        """Get number of positions to display in history dialog.

        Returns:
            Number of visible positions in list
        """
        return self.settings.get("position_history", {}).get("display_count", 20)

    # ------------------------------------------------------------------
    # Reference position — the recovery anchor
    # ------------------------------------------------------------------

    REFERENCE_POSITION_KEY = "reference_position"

    def get_reference_position(self) -> Optional[Dict[str, float]]:
        """The safe recovery position for THIS microscope, or None if unset.

        Where the stage is sent when something goes wrong: high and central,
        clear of the sample holder tip. Set during microscope setup.

        Returns None — never a fabricated position — when nothing has been
        configured. There is no safe default: (0, 0, 0) is a real coordinate
        that on N7 sits outside the stage's own soft limits (x starts at 1.0),
        and inventing a "safe" place to drive the stage is how you drive it
        into the sample. Callers must handle None by asking the user to run
        setup, not by guessing.

        Deliberately not position_presets.json (a user-editable list that can be
        emptied) and not the scope's Home (writable by the vendor control
        software without our knowledge). This lives in the per-microscope
        settings file, beside the stage limits that bound it.
        """
        raw = self.get_setting(self.REFERENCE_POSITION_KEY)
        if not isinstance(raw, dict):
            return None
        try:
            return {
                "x": float(raw["x_mm"]),
                "y": float(raw["y_mm"]),
                "z": float(raw["z_mm"]),
                "r": float(raw.get("r_degrees", 0.0)),
            }
        except (KeyError, TypeError, ValueError) as exc:
            self.logger.warning(
                f"{self.settings_file.name} has a malformed "
                f"{self.REFERENCE_POSITION_KEY} ({exc}); treating it as unset "
                f"rather than guessing. Re-run microscope setup."
            )
            return None

    def set_reference_position(
        self, x: float, y: float, z: float, r: float = 0.0, note: str = ""
    ) -> None:
        """Record the recovery position. Caller must still ``save_settings()``."""
        self.update_setting(
            self.REFERENCE_POSITION_KEY,
            {
                "x_mm": float(x),
                "y_mm": float(y),
                "z_mm": float(z),
                "r_degrees": float(r),
                "note": note
                or "Safe recovery position: high and central, clear of the "
                "sample holder tip.",
            },
        )

    def save_settings(self) -> None:
        """Save current settings back to JSON file.

        This allows programmatic updates to settings.
        """
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.settings_file, "w") as f:
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
        keys = key_path.split(".")
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
        keys = key_path.split(".")
        current = self.settings

        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default

        return current
