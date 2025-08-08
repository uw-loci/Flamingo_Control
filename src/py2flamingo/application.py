# src/py2flamingo/application.py
"""
Application coordinator for Py2Flamingo.

This module manages the application lifecycle, configuration,
and coordination between different components.
"""
import logging
import os
from typing import Dict, Optional, Any
from pathlib import Path

from .core.events import EventManager
from .core.queue_manager import QueueManager
from .services.configuration_service import ConfigurationService
from .services.connection_service import ConnectionService
from .models.microscope import MicroscopeModel, Position, MicroscopeState

class Application:
    """
    Main application coordinator.
    
    This class manages application initialization, configuration,
    and provides access to core services and managers. It serves
    as the central point for coordinating between the GUI, services,
    and hardware communication.
    
    Attributes:
        config_service: Service for managing configuration
        connection_service: Service for microscope connections
        event_manager: Manager for application events
        queue_manager: Manager for inter-thread queues
        microscope_model: Current microscope state model
        logger: Application logger
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the application coordinator.
        
        Args:
            config_path: Optional path to configuration directory
        """
        self.logger = logging.getLogger(__name__)
        
        # Check for environment variable config path
        if config_path is None:
            config_path = os.environ.get('PY2FLAMINGO_CONFIG_PATH')
        
        # Initialize services
        self.config_service = ConfigurationService(config_path)
        self.connection_service = None
        self.event_manager = EventManager()
        self.queue_manager = QueueManager()
        
        # Initialize model
        self.microscope_model = None
        self._initialized = False
        
        # Store configuration
        self.config = {}
        
    def initialize(self) -> bool:
        """
        Initialize the application.
        
        Performs all necessary setup including configuration loading,
        connection establishment, and service initialization.
        
        Returns:
            bool: True if initialization successful, False otherwise
        """
        try:
            self.logger.info("Initializing Py2Flamingo application...")
            
            # Load configuration
            self.logger.info("Loading configuration...")
            self.config = self.config_service.load_configuration()
            if not self.config:
                self.logger.error("Failed to load configuration")
                return False
            
            # Create microscope model
            self.microscope_model = MicroscopeModel(
                name=self.config['microscope_name'],
                ip_address=self.config['microscope_ip'],
                port=self.config['microscope_port'],
                current_position=Position(
                    x=self.config['start_position']['x'],
                    y=self.config['start_position']['y'],
                    z=self.config['start_position']['z'],
                    r=self.config['start_position']['r']
                ),
                lasers=self.config['workflow_template'].get('lasers', []),
                selected_laser=self.config.get('default_laser'),
                laser_power=self.config.get('default_laser_power', 5.0)
            )
            
            # Update state
            self.microscope_model.state = MicroscopeState.CONNECTING
            
            # Initialize connection service
            self.logger.info("Initializing connection service...")
            self.connection_service = ConnectionService(
                ip=self.config['microscope_ip'],
                port=self.config['microscope_port'],
                event_manager=self.event_manager,
                queue_manager=self.queue_manager
            )
            
            # Establish connection
            self.logger.info(f"Connecting to microscope at {self.config['microscope_ip']}:{self.config['microscope_port']}...")
            if not self.connection_service.connect():
                self.logger.error("Failed to connect to microscope")
                self.microscope_model.state = MicroscopeState.ERROR
                return False
            
            # Update model state
            self.microscope_model.state = MicroscopeState.IDLE
            
            # Get microscope settings and pixel size
            try:
                pixel_size, scope_settings = self.connection_service.get_microscope_settings()
                self.microscope_model.pixel_size_mm = pixel_size
                self.microscope_model.metadata['scope_settings'] = scope_settings
                
                # Update stage limits from settings
                from .models.microscope import StageLimits
                self.microscope_model.stage_limits = StageLimits.from_dict(
                    self.config_service.get_stage_limits()
                )
                
            except Exception as e:
                self.logger.warning(f"Failed to get microscope settings: {e}")
                # Continue with defaults
            
            self._initialized = True
            self.logger.info("Application initialized successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Initialization failed: {e}")
            self.microscope_model.state = MicroscopeState.ERROR
            return False
    
    def get_legacy_queues_and_events(self) -> Dict[str, Any]:
        """
        Get legacy queues and events for backward compatibility.
        
        This method provides the old-style dictionary of queues and events
        to support the existing GUI code during migration.
        
        Returns:
            Dict: Dictionary with 'queues' and 'events' keys
        """
        return {
            "queues": [
                self.queue_manager.get_queue('image'),
                self.queue_manager.get_queue('command'),
                self.queue_manager.get_queue('z_plane'),
                self.queue_manager.get_queue('intensity'),
                self.queue_manager.get_queue('visualize'),
            ],
            "events": [
                self.event_manager.get_event('view_snapshot'),
                self.event_manager.get_event('system_idle'),
                self.event_manager.get_event('processing'),
                self.event_manager.get_event('send'),
                self.event_manager.get_event('terminate'),
            ],
        }
    
    def get_connection_data(self) -> Optional[list]:
        """
        Get connection data in legacy format.
        
        Returns:
            Optional[list]: Connection data or None if not connected
        """
        if self.connection_service and self.connection_service.is_connected():
            return self.connection_service.get_connection_data()
        return None
    
    def shutdown(self):
        """Shutdown the application and cleanup resources."""
        self.logger.info("Shutting down application...")
        
        # Update model state
        if self.microscope_model:
            self.microscope_model.state = MicroscopeState.DISCONNECTED
        
        # Disconnect from microscope
        if self.connection_service:
            self.connection_service.disconnect()
        
        # Clear events and queues
        self.event_manager.clear_all()
        self.queue_manager.clear_all()
        
        self._initialized = False
        self.logger.info("Application shutdown complete")
    
    def restart_connection(self) -> bool:
        """
        Restart the connection to the microscope.
        
        Returns:
            bool: True if reconnection successful
        """
        self.logger.info("Restarting microscope connection...")
        
        # Disconnect if connected
        if self.connection_service and self.connection_service.is_connected():
            self.connection_service.disconnect()
        
        # Update state
        self.microscope_model.state = MicroscopeState.CONNECTING
        
        # Try to reconnect
        if self.connection_service.connect():
            self.microscope_model.state = MicroscopeState.IDLE
            self.logger.info("Reconnection successful")
            return True
        else:
            self.microscope_model.state = MicroscopeState.ERROR
            self.logger.error("Reconnection failed")
            return False
    
    def get_microscope_info(self) -> Dict[str, Any]:
        """
        Get current microscope information.
        
        Returns:
            Dict: Microscope information including name, state, position
        """
        if not self.microscope_model:
            return {
                'name': 'Unknown',
                'state': 'Not initialized',
                'connected': False
            }
        
        return {
            'name': self.microscope_model.name,
            'state': self.microscope_model.state.value,
            'connected': self.microscope_model.is_connected(),
            'position': self.microscope_model.current_position.to_dict(),
            'lasers': self.microscope_model.lasers,
            'selected_laser': self.microscope_model.selected_laser,
            'laser_power': self.microscope_model.laser_power
        }
    
    def is_initialized(self) -> bool:
        """
        Check if application is initialized.
        
        Returns:
            bool: True if initialized
        """
        return self._initialized