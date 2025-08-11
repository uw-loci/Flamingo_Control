# src/py2flamingo/controllers/settings_controller.py
"""
Controller for microscope settings management.

This controller handles settings-related operations including
setting home position and managing configuration.
"""
import os
import time
import logging
from typing import Optional
from pathlib import Path

from py2flamingo.models.microscope import Position
from py2flamingo.models.settings import HomePosition, MicroscopeSettings
from py2flamingo.services.connection_service import ConnectionService
from py2flamingo.core.queue_manager import QueueManager
from py2flamingo.core.events import EventManager
from py2flamingo.utils.file_handlers import text_to_dict, dict_to_text

class SettingsController:
    """
    Controller for managing microscope settings.
    
    This controller replaces the functionality from set_home.py
    with improved structure and error handling.
    
    Attributes:
        connection_service: Service for microscope communication
        queue_manager: Manager for command queues
        event_manager: Manager for synchronization events
        logger: Logger instance
    """
    
    # Command codes from command_list.txt
    COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD = 4105
    COMMAND_CODES_COMMON_SCOPE_SETTINGS_SAVE = 4107
    
    def __init__(self,
                 connection_service: ConnectionService,
                 queue_manager: QueueManager,
                 event_manager: EventManager):
        """
        Initialize the settings controller.
        
        Args:
            connection_service: Service for microscope communication
            queue_manager: Queue manager for commands
            event_manager: Event manager for synchronization
        """
        self.connection = connection_service
        self.queue_manager = queue_manager
        self.event_manager = event_manager
        self.logger = logging.getLogger(__name__)
    
    def set_home_position(self, position: Position) -> None:
        """
        Set the home coordinates for the microscope's stage.
        
        This method replaces the original set_home function with improved
        structure and error handling. It sends commands to the microscope 
        to load its current settings, modifies these settings to update 
        the home coordinates, and saves these new settings back to the microscope.
        
        Args:
            position: Position to set as home
            
        Raises:
            RuntimeError: If not connected or operation fails
        """
        # Original comment from set_home.py:
        # Unpack the connection_data list
        if not self.connection.is_connected():
            raise RuntimeError("Not connected to microscope")
        
        try:
            self.logger.info(f"Setting home position to: {position}")
            
            # Original comment from set_home.py:
            # Load command list from text file and convert it to a dictionary
            # Get microscope settings file to temp location
            self._load_current_settings()
            
            # Original comment from set_home.py:
            # Microscope settings should now be in a text file called ScopeSettings.txt 
            # in the 'microscope_settings' directory
            # Convert them into a dictionary to extract useful information
            settings_path = Path('microscope_settings') / 'ScopeSettings.txt'
            settings_dict = text_to_dict(str(settings_path))
            
            # Update the home coordinates in the settings dictionary
            settings_dict['Stage limits']['Home x-axis'] = position.x
            settings_dict['Stage limits']['Home y-axis'] = position.y
            settings_dict['Stage limits']['Home z-axis'] = position.z
            settings_dict['Stage limits']['Home r-axis'] = position.r
            
            # Convert the updated settings dictionary back into a text file
            send_settings_path = Path('microscope_settings') / 'send_settings.txt'
            dict_to_text(str(send_settings_path), settings_dict)
            
            self.logger.info("Saving settings to microscope")
            
            # Send command to microscope to save the updated settings
            self._save_settings_to_microscope()
            
            # Allow time for command to be processed
            time.sleep(0.2)
            
            # Clean up temporary file
            if send_settings_path.exists():
                os.remove(send_settings_path)
            else:
                self.logger.warning(f"{send_settings_path} not found.")
                
            self.logger.info("Home position set successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to set home position: {e}")
            raise RuntimeError(f"Failed to set home position: {e}")
    
    def set_home_from_xyzr(self, xyzr: list) -> None:
        """
        Set home position from XYZR list (backward compatibility).
        
        Args:
            xyzr: List of [x, y, z, r] coordinates
        """
        position = Position(
            x=float(xyzr[0]),
            y=float(xyzr[1]),
            z=float(xyzr[2]),
            r=float(xyzr[3])
        )
        self.set_home_position(position)
    
    def _load_current_settings(self) -> None:
        """Load current settings from microscope."""
        self.logger.debug("Loading settings from microscope")
        
        # Movement command (as in original)
        self.queue_manager.put_nowait('command', self.COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD)
        self.event_manager.set_event('send')
        
        # Wait for queue to be processed
        while not self.queue_manager.get_queue('command').empty():
            time.sleep(0.3)
    
    def _save_settings_to_microscope(self) -> None:
        """Save updated settings to microscope."""
        self.logger.debug("Saving settings to microscope")
        
        self.queue_manager.put_nowait('command', self.COMMAND_CODES_COMMON_SCOPE_SETTINGS_SAVE)
        self.event_manager.set_event('send')
        
        # Wait for command to be processed
        time.sleep(0.2)
    
    def get_home_position(self) -> Optional[Position]:
        """
        Get current home position from settings.
        
        Returns:
            Optional[Position]: Home position or None if not set
        """
        try:
            settings_path = Path('microscope_settings') / 'ScopeSettings.txt'
            if not settings_path.exists():
                self.logger.warning("Settings file not found")
                return None
            
            settings_dict = text_to_dict(str(settings_path))
            stage_limits = settings_dict.get('Stage limits', {})
            
            return Position(
                x=float(stage_limits.get('Home x-axis', 0.0)),
                y=float(stage_limits.get('Home y-axis', 0.0)),
                z=float(stage_limits.get('Home z-axis', 0.0)),
                r=float(stage_limits.get('Home r-axis', 0.0))
            )
            
        except Exception as e:
            self.logger.error(f"Failed to get home position: {e}")
            return None
