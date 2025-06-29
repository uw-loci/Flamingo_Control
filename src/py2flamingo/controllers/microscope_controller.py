# ============================================================================
# src/py2flamingo/controllers/microscope_controller.py
"""
Controller for managing microscope state and operations.
"""

from typing import List, Callable, Optional
import logging
from threading import Lock

from models.microscope import MicroscopeModel, Position, MicroscopeState
from services.communication.connection_manager import ConnectionManager


class MicroscopeController:
    """
    Controller for microscope operations.
    
    Manages microscope state, position updates, and coordinates
    communication with the hardware.
    """
    
    def __init__(self, model: MicroscopeModel, connection_manager: ConnectionManager):
        """
        Initialize microscope controller.
        
        Args:
            model: Microscope data model
            connection_manager: Connection manager for hardware communication
        """
        self.model = model
        self.connection = connection_manager
        self._observers: List[Callable] = []
        self._lock = Lock()
        self.logger = logging.getLogger(__name__)
        
        # Subscribe to position updates from connection
        self.connection.subscribe_position_updates(self._handle_position_update)
        
    def subscribe(self, callback: Callable[[MicroscopeModel], None]):
        """
        Subscribe to model updates.
        
        Args:
            callback: Function to call when model updates
        """
        with self._lock:
            self._observers.append(callback)
    
    def unsubscribe(self, callback: Callable[[MicroscopeModel], None]):
        """
        Unsubscribe from model updates.
        
        Args:
            callback: Function to remove from observers
        """
        with self._lock:
            if callback in self._observers:
                self._observers.remove(callback)
    
    def _notify_observers(self):
        """Notify all observers of model changes."""
        with self._lock:
            for callback in self._observers:
                try:
                    callback(self.model)
                except Exception as e:
                    self.logger.error(f"Error notifying observer: {e}")
    
    def _handle_position_update(self, position_data: List[float]):
        """
        Handle position update from hardware.
        
        Args:
            position_data: List of [x, y, z, r] values
        """
        if len(position_data) >= 4:
            self.model.current_position = Position(
                x=position_data[0],
                y=position_data[1],
                z=position_data[2],
                r=position_data[3]
            )
            self._notify_observers()
    
    def move_to_position(self, position: Position):
        """
        Move microscope to specified position.
        
        Args:
            position: Target position
        """
        self.model.state = MicroscopeState.MOVING
        self._notify_observers()
        
        try:
            # Send move command
            self.connection.send_move_command(position)
            
            # Update model with target position
            self.model.current_position = position
            self.model.state = MicroscopeState.IDLE
            
        except Exception as e:
            self.logger.error(f"Failed to move to position: {e}")
            self.model.state = MicroscopeState.ERROR
            raise
        finally:
            self._notify_observers()
    
    def get_current_position(self) -> Position:
        """
        Get current microscope position.
        
        Returns:
            Current position
        """
        return self.model.current_position
    
    def update_laser_settings(self, laser_channel: str, power: float):
        """
        Update laser settings.
        
        Args:
            laser_channel: Laser channel name
            power: Power percentage (0-100)
        """
        if laser_channel in self.model.lasers:
            self.model.selected_laser = laser_channel
            self.model.laser_power = power
            self._notify_observers()
        else:
            raise ValueError(f"Unknown laser channel: {laser_channel}")
    
    def get_available_lasers(self) -> List[str]:
        """
        Get list of available laser channels.
        
        Returns:
            List of laser channel names
        """
        return self.model.lasers.copy()
    
    def get_state(self) -> MicroscopeState:
        """
        Get current microscope state.
        
        Returns:
            Current state
        """
        return self.model.state
    
    def emergency_stop(self):
        """Execute emergency stop."""
        try:
            self.connection.send_emergency_stop()
            self.model.state = MicroscopeState.IDLE
        except Exception as e:
            self.logger.error(f"Emergency stop failed: {e}")
            self.model.state = MicroscopeState.ERROR
        finally:
            self._notify_observers()