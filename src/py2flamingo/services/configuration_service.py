# src/py2flamingo/services/configuration_service.py
"""
Configuration service for managing application settings and file validation.

This service handles loading configuration files, validating required files,
and providing configuration data to the application.
"""
import os
import logging
from typing import Dict, Optional, Any
from pathlib import Path

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
            self.logger.info(f"[ConfigurationService] Using provided base_path: {self.base_path}")
        else:
            # Find project root by looking for microscope_settings directory
            # Start from current working directory and walk up until we find it
            current = Path.cwd()
            print(f"[ConfigurationService] Searching for project root, starting from: {current}")
            self.logger.info(f"[ConfigurationService] Searching for project root, starting from: {current}")

            search_count = 0
            while current != current.parent:  # Stop at filesystem root
                search_count += 1
                check_path = current / "microscope_settings"
                print(f"[ConfigurationService]   Check #{search_count}: {check_path}")
                if check_path.exists():
                    self.base_path = current
                    print(f"[ConfigurationService] ✓ FOUND project root: {self.base_path}")
                    self.logger.info(f"[ConfigurationService] Found project root: {self.base_path}")
                    break
                current = current.parent
            else:
                # Fallback to cwd if microscope_settings not found
                self.base_path = Path.cwd()
                print(f"[ConfigurationService] ✗ Could not find microscope_settings, using cwd: {self.base_path}")
                self.logger.warning(
                    f"[ConfigurationService] Could not find microscope_settings directory, "
                    f"using current directory: {self.base_path}"
                )

        # Load configuration
        self.config = {}
        scope_settings = self._load_scope_settings()
        if scope_settings:
            self.config['scope_settings'] = scope_settings

        # Load microscope-specific settings
        microscope_name = self.get_microscope_name()
        print(f"[ConfigurationService] Detected microscope name: '{microscope_name}' from ScopeSettings.txt")
        self.logger.info(f"[ConfigurationService] Detected microscope name: '{microscope_name}' from ScopeSettings.txt")

        from py2flamingo.services.microscope_settings_service import MicroscopeSettingsService
        self.microscope_settings = MicroscopeSettingsService(microscope_name, self.base_path)

        # Log the actual stage limits being loaded
        limits = self.microscope_settings.get_stage_limits()
        print(f"[ConfigurationService] Final stage limits loaded:")
        print(f"  X: {limits['x']['min']:.2f} to {limits['x']['max']:.2f} mm")
        print(f"  Y: {limits['y']['min']:.2f} to {limits['y']['max']:.2f} mm")
        print(f"  Z: {limits['z']['min']:.2f} to {limits['z']['max']:.2f} mm")
        print(f"  R: {limits['r']['min']:.1f} to {limits['r']['max']:.1f} degrees")

        self.logger.info(f"[ConfigurationService] Loaded stage limits: X={limits['x']['min']:.2f}-{limits['x']['max']:.2f}, "
                        f"Y={limits['y']['min']:.2f}-{limits['y']['max']:.2f}, "
                        f"Z={limits['z']['min']:.2f}-{limits['z']['max']:.2f}, "
                        f"R={limits['r']['min']:.1f}-{limits['r']['max']:.1f}")
        self.logger.info(f"Loaded microscope-specific settings for '{microscope_name}'")

    def _load_start_position(self, microscope_name: str) -> Dict[str, float]:
        """
        Load start position for the microscope if available.
        
        Args:
            microscope_name: Name of the microscope
            
        Returns:
            Dict: Start position with x, y, z, r values
        """
        position_path = self.base_path / 'microscope_settings' / f'{microscope_name}_start_position.txt'
        
        if position_path.exists():
            try:
                position_dict = text_to_dict(str(position_path))
                pos = position_dict.get(microscope_name, {})
                return {
                    'x': float(pos.get('x(mm)', 0.0)),
                    'y': float(pos.get('y(mm)', 0.0)),
                    'z': float(pos.get('z(mm)', 0.0)),
                    'r': float(pos.get('r(°)', 0.0))
                }
            except Exception as e:
                self.logger.warning(f"Failed to load start position: {e}")
        
        # Return default position if file doesn't exist
        self.logger.info("No start position file found, using defaults")
        return {'x': 0.0, 'y': 0.0, 'z': 0.0, 'r': 0.0}
    
    def _load_scope_settings(self) -> Optional[Dict[str, Any]]:
        """
        Load scope settings if available.
        
        Returns:
            Optional[Dict]: Scope settings or None
        """
        settings_path = self.base_path / 'microscope_settings' / 'ScopeSettings.txt'
        
        if settings_path.exists():
            try:
                return text_to_dict(str(settings_path))
            except Exception as e:
                self.logger.warning(f"Failed to load scope settings: {e}")
                return None
        
        return None

    def _prompt_for_file(self, filename: str, title: str, message: str) -> Optional[Path]:
        """
        Prompt user to select a file using Qt dialog.

        Fixed to avoid QApplication conflicts.
        """
        try:
            from PyQt5.QtWidgets import QFileDialog, QMessageBox, QApplication

            # Check if we're in a Qt environment
            app = QApplication.instance()
            if not app:
                # Log error instead of creating app
                self.logger.error("No QApplication instance available for file dialog")
                return None

            # Show information message
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Information)
            msg.setWindowTitle("File Required")
            msg.setText(f"The file {filename} was not found.")
            msg.setInformativeText(message)
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec_()

            # Open file dialog
            file_path, _ = QFileDialog.getOpenFileName(
                None,
                title,
                str(self.base_path),
                "Text files (*.txt);;All files (*.*)"
            )

            return Path(file_path) if file_path else None

        except ImportError:
            self.logger.error("Qt not available for file dialog")
            return None
        except Exception as e:
            self.logger.error(f"Error showing file dialog: {e}")
            return None
    
    def _copy_file_to_settings(self, source_path: Path, target_name: str) -> None:
        """
        Copy a file to the microscope_settings directory.
        
        Args:
            source_path: Source file path
            target_name: Target filename
        """
        import shutil
        target_path = self.base_path / 'microscope_settings' / target_name
        shutil.copy(source_path, target_path)
        self.logger.info(f"Copied {source_path} to {target_path}")
    
    def get_lasers(self) -> list:
        """
        Get list of available lasers.
        
        Returns:
            list: List of laser names
        """
        return self.config.get('workflow_template', {}).get('lasers', [])
    
    def get_default_laser(self) -> str:
        """
        Get default laser channel.
        
        Returns:
            str: Default laser channel name
        """
        return self.config.get('default_laser', 'Laser 3 488 nm')
    
    def get_default_laser_power(self) -> float:
        """
        Get default laser power.
        
        Returns:
            float: Default laser power percentage
        """
        return self.config.get('default_laser_power', 5.0)
    
    def get_data_storage_location(self) -> str:
        """
        Get default data storage location.

        Returns:
            str: Data storage path, or empty string if not configured.
                 User must select via Refresh button in Advanced Save Settings.
        """
        return self.config.get('data_storage_location', '')
    
    def get_microscope_name(self) -> str:
        """
        Get microscope name from scope settings.

        Returns:
            str: Microscope name (e.g., "zion")
        """
        scope_settings = self.config.get('scope_settings', {})
        type_settings = scope_settings.get('Type', {})
        microscope_name = type_settings.get('Microscope name', 'default')
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

    def save_start_position(self, microscope_name: str, position: Dict[str, float]) -> None:
        """
        Save start position to file.

        Args:
            microscope_name: Name of microscope
            position: Dictionary with x, y, z, r values
        """
        from py2flamingo.utils.file_handlers import dict_to_text

        position_dict = {
            microscope_name: {
                'x(mm)': position['x'],
                'y(mm)': position['y'],
                'z(mm)': position['z'],
                'r(°)': position['r']
            }
        }

        file_path = self.base_path / 'microscope_settings' / f'{microscope_name}_start_position.txt'
        dict_to_text(str(file_path), position_dict)
        self.logger.info(f"Saved start position to {file_path}")

    # Drive path mapping methods for post-collection folder reorganization
    DRIVE_MAPPINGS_KEY = 'drive_path_mappings'

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

        Args:
            server_path: Server storage path (e.g., "/media/deploy/ctlsm1")
            local_path: Local mount path (e.g., "G:/CTLSM1")
        """
        mappings = self.config.get(self.DRIVE_MAPPINGS_KEY, {})
        mappings[server_path] = local_path
        self.config[self.DRIVE_MAPPINGS_KEY] = mappings
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
            self.logger.info(f"Removed drive mapping for: {server_path}")
            return True
        return False
