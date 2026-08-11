# src/py2flamingo/services/configuration_service.py
"""
Configuration service for managing application settings and file validation.

This service handles loading configuration files, validating required files,
and providing configuration data to the application.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from py2flamingo.utils.file_handlers import text_to_dict, workflow_to_dict


class ConfigurationService:
    """
    Service for managing application configuration.

    This service replaces the file checking logic in FlamingoConnect
    and provides centralized configuration management.

    Attributes:
        logger: Logger instance
        base_path: Base path for configuration files
        config: Loaded configuration dictionary
    """

    def __init__(self, base_path: Optional[Path] = None):
        """
        Initialize configuration service.

        Args:
            base_path: Base path for configuration files (defaults to project root)
        """
        self.logger = logging.getLogger(__name__)
        if base_path:
            self.base_path = base_path
            print(f"[ConfigurationService] Using provided base_path: {self.base_path}")
            self.logger.info(
                f"[ConfigurationService] Using provided base_path: {self.base_path}"
            )
        else:
            # Find project root by looking for microscope_settings directory
            # Start from current working directory and walk up until we find it
            current = Path.cwd()
            print(
                f"[ConfigurationService] Searching for project root, starting from: {current}"
            )
            self.logger.info(
                f"[ConfigurationService] Searching for project root, starting from: {current}"
            )

            search_count = 0
            while current != current.parent:  # Stop at filesystem root
                search_count += 1
                check_path = current / "microscope_settings"
                print(f"[ConfigurationService]   Check #{search_count}: {check_path}")
                if check_path.exists():
                    self.base_path = current
                    print(
                        f"[ConfigurationService] ✓ FOUND project root: {self.base_path}"
                    )
                    self.logger.info(
                        f"[ConfigurationService] Found project root: {self.base_path}"
                    )
                    break
                current = current.parent
            else:
                # Fallback to cwd if microscope_settings not found
                self.base_path = Path.cwd()
                print(
                    f"[ConfigurationService] ✗ Could not find microscope_settings, using cwd: {self.base_path}"
                )
                self.logger.warning(
                    f"[ConfigurationService] Could not find microscope_settings directory, "
                    f"using current directory: {self.base_path}"
                )

        # Load configuration
        self.config = {}
        scope_settings = self._load_scope_settings()
        if scope_settings:
            self.config["scope_settings"] = scope_settings

        # Load persisted drive mappings
        self._load_drive_mappings()

        # Load persisted session paths (for file dialogs)
        self._load_session_paths()

        # Load microscope-specific settings
        microscope_name = self.get_microscope_name()
        print(
            f"[ConfigurationService] Detected microscope name: '{microscope_name}' from ScopeSettings.txt"
        )
        self.logger.info(
            f"[ConfigurationService] Detected microscope name: '{microscope_name}' from ScopeSettings.txt"
        )

        from py2flamingo.services.microscope_settings_service import (
            MicroscopeSettingsService,
        )

        self.microscope_settings = MicroscopeSettingsService(
            microscope_name, self.base_path
        )

        # Log the actual stage limits being loaded
        limits = self.microscope_settings.get_stage_limits()
        print(f"[ConfigurationService] Final stage limits loaded:")
        print(f"  X: {limits['x']['min']:.2f} to {limits['x']['max']:.2f} mm")
        print(f"  Y: {limits['y']['min']:.2f} to {limits['y']['max']:.2f} mm")
        print(f"  Z: {limits['z']['min']:.2f} to {limits['z']['max']:.2f} mm")
        print(f"  R: {limits['r']['min']:.1f} to {limits['r']['max']:.1f} degrees")

        self.logger.info(
            f"[ConfigurationService] Loaded stage limits: X={limits['x']['min']:.2f}-{limits['x']['max']:.2f}, "
            f"Y={limits['y']['min']:.2f}-{limits['y']['max']:.2f}, "
            f"Z={limits['z']['min']:.2f}-{limits['z']['max']:.2f}, "
            f"R={limits['r']['min']:.1f}-{limits['r']['max']:.1f}"
        )
        self.logger.info(f"Loaded microscope-specific settings for '{microscope_name}'")

    def _load_scope_settings(self) -> Optional[Dict[str, Any]]:
        """
        Load scope settings if available.

        Returns:
            Optional[Dict]: Scope settings or None
        """
        settings_path = self.base_path / "microscope_settings" / "ScopeSettings.txt"

        if settings_path.exists():
            try:
                return text_to_dict(str(settings_path))
            except Exception as e:
                self.logger.warning(f"Failed to load scope settings: {e}")
                return None

        return None

    def get_data_storage_location(self) -> str:
        """
        Get default data storage location.

        Returns:
            str: Data storage path, or empty string if not configured.
                 User must select via Refresh button in Advanced Save Settings.
        """
        return self.config.get("data_storage_location", "")

    def get_microscope_name(self) -> str:
        """
        Get microscope name from scope settings.

        Returns:
            str: Microscope name (e.g., "zion")
        """
        scope_settings = self.config.get("scope_settings", {})
        type_settings = scope_settings.get("Type", {})
        microscope_name = type_settings.get("Microscope name", "default")
        return microscope_name.strip()

    def get_stage_limits(self) -> Dict[str, Dict[str, float]]:
        """
        Get stage movement limits from microscope-specific settings.

        These limits are loaded from {microscope_name}_settings.json
        which allows per-microscope configuration without code changes.

        Returns:
            Dict: Stage limits for each axis with min/max values
        """
        # Use microscope-specific settings (loads from JSON file)
        return self.microscope_settings.get_stage_limits()

    def get_position_history_max_size(self) -> int:
        """
        Get maximum size for position history from microscope settings.

        Returns:
            int: Maximum number of positions to store
        """
        return self.microscope_settings.get_position_history_max_size()

    def get_position_history_display_count(self) -> int:
        """
        Get number of positions to display in history dialog.

        Returns:
            int: Number of visible positions in list
        """
        return self.microscope_settings.get_position_history_display_count()

    # Drive path mapping methods for post-collection folder reorganization
    DRIVE_MAPPINGS_KEY = "drive_path_mappings"

    def get_drive_mappings(self) -> Dict[str, str]:
        """Get server-to-local drive mappings.

        These mappings allow the application to find locally-mounted paths
        for server storage drives, enabling post-collection file reorganization.

        Returns:
            Dictionary mapping server paths to local paths.
            Example: {"/media/deploy/ctlsm1": "G:/CTLSM1"}
        """
        return self.config.get(self.DRIVE_MAPPINGS_KEY, {})

    def set_drive_mapping(self, server_path: str, local_path: str) -> None:
        """Set local path mapping for a server drive.

        Persists immediately to disk so the mapping survives restarts.

        Args:
            server_path: Server storage path (e.g., "/media/deploy/ctlsm1")
            local_path: Local mount path (e.g., "G:/CTLSM1")
        """
        mappings = self.config.get(self.DRIVE_MAPPINGS_KEY, {})
        mappings[server_path] = local_path
        self.config[self.DRIVE_MAPPINGS_KEY] = mappings
        self._save_drive_mappings()
        self.logger.info(f"Set drive mapping: {server_path} -> {local_path}")

    def get_local_path_for_drive(self, server_path: str) -> Optional[str]:
        """Get local path for a server drive, or None if not mapped.

        Args:
            server_path: Server storage path to look up

        Returns:
            Local path if mapped, None otherwise
        """
        return self.get_drive_mappings().get(server_path)

    def remove_drive_mapping(self, server_path: str) -> bool:
        """Remove a drive mapping.

        Args:
            server_path: Server path to remove mapping for

        Returns:
            True if mapping was removed, False if it didn't exist
        """
        mappings = self.config.get(self.DRIVE_MAPPINGS_KEY, {})
        if server_path in mappings:
            del mappings[server_path]
            self.config[self.DRIVE_MAPPINGS_KEY] = mappings
            self._save_drive_mappings()
            self.logger.info(f"Removed drive mapping for: {server_path}")
            return True
        return False

    _DRIVE_MAPPINGS_FILE = "drive_mappings.json"

    def _drive_mappings_path(self) -> Path:
        """Path to the drive mappings JSON file."""
        return self.base_path / self._DRIVE_MAPPINGS_FILE

    def _load_drive_mappings(self) -> None:
        """Load drive mappings from JSON file on disk."""
        path = self._drive_mappings_path()
        # Drive mappings are configuration, not per-run state: they change only
        # when someone edits them, and without them server paths cannot be
        # resolved to local drives. Seed from the tracked example so a fresh
        # clone — or a machine that lost the file — starts able to find data.
        from py2flamingo.utils.seed_config import seed_from_example

        seed_from_example(path, self.logger)
        if not path.exists():
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            mappings = data.get("mappings", {})
            if mappings:
                self.config[self.DRIVE_MAPPINGS_KEY] = mappings
                self.logger.info(f"Loaded {len(mappings)} drive mapping(s) from {path}")
        except Exception as e:
            self.logger.warning(f"Failed to load drive mappings: {e}")

    def _save_drive_mappings(self) -> None:
        """Save current drive mappings to JSON file on disk."""
        path = self._drive_mappings_path()
        mappings = self.config.get(self.DRIVE_MAPPINGS_KEY, {})
        try:
            with open(path, "w") as f:
                json.dump({"mappings": mappings}, f, indent=2)
            self.logger.debug(f"Saved {len(mappings)} drive mapping(s) to {path}")
        except Exception as e:
            self.logger.warning(f"Failed to save drive mappings: {e}")

    # Session save path methods
    LED_2D_SESSION_PATH_KEY = "led_2d_overview_session_path"
    MIP_SESSION_PATH_KEY = "mip_overview_session_path"
    ZARR_SESSION_PATH_KEY = "zarr_3d_session_path"
    THRESHOLDER_PRESET_PATH_KEY = "thresholder_preset_path"
    STITCHED_DATA_PATH_KEY = "stitched_data_path"
    WEBCAM_SESSION_PATH_KEY = "webcam_overview_session_path"
    # Dict of Workflow-tab widget states the user expects to survive a restart
    # (save format, camera AOI, ...). One persisted key holding many sub-keys,
    # so adding a remembered control does not mean touching this service.
    WORKFLOW_PREFS_KEY = "workflow_panel_prefs"
    _SESSION_PATHS_FILE = "session_paths.json"

    def _persisted_keys(self) -> tuple:
        """The ONLY config keys written to / read from disk.

        A key absent from here stays in the in-memory ``config`` and is silently
        lost on restart — which is how "last used save drive" appeared to stick
        for a session and never came back. Resolved at call time because some of
        these constants are defined further down the class body.
        """
        return (
            self.LED_2D_SESSION_PATH_KEY,
            self.MIP_SESSION_PATH_KEY,
            self.ZARR_SESSION_PATH_KEY,
            self.THRESHOLDER_PRESET_PATH_KEY,
            self.MIP_BROWSE_PATH_KEY,
            self.SAMPLE_3D_DATA_PATH_KEY,
            self.STITCHED_DATA_PATH_KEY,
            self.WEBCAM_SESSION_PATH_KEY,
            self.WORKFLOW_PREFS_KEY,
        )

    def _session_paths_file(self) -> Path:
        """Path to the session paths JSON file."""
        return self.base_path / self._SESSION_PATHS_FILE

    def _load_session_paths(self) -> None:
        """Load session paths from JSON file on disk."""
        path = self._session_paths_file()
        if not path.exists():
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # Load each session path key
            for key in self._persisted_keys():
                if key in data:
                    self.config[key] = data[key]
            self.logger.info(f"Loaded session paths from {path}")
        except Exception as e:
            self.logger.warning(f"Failed to load session paths: {e}")

    def _save_session_paths(self) -> None:
        """Save session paths to JSON file on disk."""
        path = self._session_paths_file()
        data = {}
        for key in self._persisted_keys():
            if key in self.config:
                data[key] = self.config[key]
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            self.logger.debug(f"Saved session paths to {path}")
        except Exception as e:
            self.logger.warning(f"Failed to save session paths: {e}")

    def get_led_2d_session_path(self) -> Optional[str]:
        """Get the last-used LED 2D Overview session save path.

        Returns:
            Path string if set, None otherwise
        """
        return self.config.get(self.LED_2D_SESSION_PATH_KEY)

    def set_led_2d_session_path(self, path: str) -> None:
        """Set the LED 2D Overview session save path.

        Args:
            path: Directory path to save sessions to
        """
        self.config[self.LED_2D_SESSION_PATH_KEY] = path
        self._save_session_paths()
        self.logger.info(f"Set LED 2D session path: {path}")

    def get_zarr_session_path(self) -> Optional[str]:
        """Get the last-used 3D Zarr session path (Sample View Load Session).

        Returns:
            Path string if set, None otherwise
        """
        return self.config.get(self.ZARR_SESSION_PATH_KEY)

    def set_zarr_session_path(self, path: str) -> None:
        """Set the 3D Zarr session path (Sample View Load Session).

        Args:
            path: Directory path for Zarr sessions
        """
        self.config[self.ZARR_SESSION_PATH_KEY] = path
        self._save_session_paths()
        self.logger.info(f"Set Zarr session path: {path}")

    def get_mip_session_path(self) -> Optional[str]:
        """Get the last-used MIP Overview session save path.

        Returns:
            Path string if set, None otherwise
        """
        return self.config.get(self.MIP_SESSION_PATH_KEY)

    def set_mip_session_path(self, path: str) -> None:
        """Set the MIP Overview session save path.

        Args:
            path: Directory path to save sessions to
        """
        self.config[self.MIP_SESSION_PATH_KEY] = path
        self._save_session_paths()
        self.logger.info(f"Set MIP session path: {path}")

    # MIP browse path (for Load MIP Files)
    MIP_BROWSE_PATH_KEY = "mip_overview_browse_path"

    def get_mip_browse_path(self) -> Optional[str]:
        """Get the last-used MIP Overview browse path for Load MIP Files.

        Returns:
            Path string if set, None otherwise
        """
        return self.config.get(self.MIP_BROWSE_PATH_KEY)

    def set_mip_browse_path(self, path: str) -> None:
        """Set the MIP Overview browse path for Load MIP Files.

        Args:
            path: Directory path last browsed to
        """
        self.config[self.MIP_BROWSE_PATH_KEY] = path
        self._save_session_paths()
        self.logger.info(f"Set MIP browse path: {path}")

    # Sample View 3D data path (for Save/Load Data in Sample View)
    SAMPLE_3D_DATA_PATH_KEY = "sample_view_3d_data_path"

    def get_sample_3d_data_path(self) -> Optional[str]:
        """Get the last-used Sample View 3D data path.

        Returns:
            Path string if set, None otherwise
        """
        return self.config.get(self.SAMPLE_3D_DATA_PATH_KEY)

    def set_sample_3d_data_path(self, path: str) -> None:
        """Set the Sample View 3D data path.

        Args:
            path: Directory path last used for save/load
        """
        self.config[self.SAMPLE_3D_DATA_PATH_KEY] = path
        self._save_session_paths()
        self.logger.info(f"Set Sample 3D data path: {path}")

    def get_stitched_data_path(self) -> Optional[str]:
        """Get the last-used stitched data directory path.

        Returns:
            Path string if set, None otherwise
        """
        return self.config.get(self.STITCHED_DATA_PATH_KEY)

    def set_stitched_data_path(self, path: str) -> None:
        """Set the last-used stitched data directory path.

        Args:
            path: Directory path last browsed to
        """
        self.config[self.STITCHED_DATA_PATH_KEY] = path
        self._save_session_paths()
        self.logger.info(f"Set stitched data path: {path}")

    # Webcam overview session path
    def get_webcam_session_path(self) -> Optional[str]:
        """Get the last-used Webcam Overview session browse path.

        Returns:
            Path string if set, None otherwise
        """
        return self.config.get(self.WEBCAM_SESSION_PATH_KEY)

    def set_webcam_session_path(self, path: str) -> None:
        """Set the Webcam Overview session browse path.

        Args:
            path: Directory path last browsed to
        """
        self.config[self.WEBCAM_SESSION_PATH_KEY] = path
        self._save_session_paths()
        self.logger.info(f"Set webcam session path: {path}")

    # Workflow-tab panel preferences (save format, camera AOI, save drive, ...)
    def get_workflow_prefs(self) -> Dict[str, Any]:
        """Remembered Workflow-tab control states.

        Returns:
            The stored preferences dict (empty if nothing has been saved yet).
        """
        prefs = self.config.get(self.WORKFLOW_PREFS_KEY)
        return dict(prefs) if isinstance(prefs, dict) else {}

    def set_workflow_pref(self, key: str, value: Any) -> None:
        """Remember one Workflow-tab control state and write it to disk.

        Args:
            key: Preference name, e.g. ``"save_format"`` or ``"aoi_width"``
            value: JSON-serialisable value to store
        """
        prefs = self.get_workflow_prefs()
        if prefs.get(key) == value:
            return  # No change: skip the disk write.
        prefs[key] = value
        self.config[self.WORKFLOW_PREFS_KEY] = prefs
        self._save_session_paths()
        self.logger.debug(f"Saved workflow preference {key}={value!r}")

    # Thresholder preset path (for Save/Load Preset in Union of Thresholders)
    def get_thresholder_preset_path(self) -> Optional[str]:
        """Get the last-used thresholder preset file path.

        Returns:
            Path string if set, None otherwise
        """
        return self.config.get(self.THRESHOLDER_PRESET_PATH_KEY)

    def set_thresholder_preset_path(self, path: str) -> None:
        """Set the thresholder preset file path.

        Args:
            path: Directory path last used for preset save/load
        """
        self.config[self.THRESHOLDER_PRESET_PATH_KEY] = path
        self._save_session_paths()
        self.logger.info(f"Set thresholder preset path: {path}")
