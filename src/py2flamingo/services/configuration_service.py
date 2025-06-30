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
            str: Data storage path
        """
        return self.config.get('data_storage_location', '/media/deploy/MSN_LS')
    
    def get_stage_limits(self) -> Dict[str, Dict[str, float]]:
        """
        Get stage movement limits from scope settings.
        
        Returns:
            Dict: Stage limits for each axis
        """
        scope_settings = self.config.get('scope_settings', {})
        stage_limits = scope_settings.get('Stage limits', {})
        
        return {
            'x': {
                'min': float(stage_limits.get('Soft limit min x-axis', 0.0)),
                'max': float(stage_limits.get('Soft limit max x-axis', 26.0))
            },
            'y': {
                'min': float(stage_limits.get('Soft limit min y-axis', 0.0)),
                'max': float(stage_limits.get('Soft limit max y-axis', 26.0))
            },
            'z': {
                'min': float(stage_limits.get('Soft limit min z-axis', 0.0)),
                'max': float(stage_limits.get('Soft limit max z-axis', 26.0))
            },
            'r': {
                'min': float(stage_limits.get('Soft limit min r-axis', -720.0)),
                'max': float(stage_limits.get('Soft limit max r-axis', 720.0))
            }
        }
    
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
