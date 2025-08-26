# ============================================================================
# src/py2flamingo/controllers/microscope_controller.py
"""
Controller for managing microscope state and operations.
"""

from typing import List, Callable, Optional
import logging
from threading import Lock

try:
    from ..models.microscope import MicroscopeModel, Position, MicroscopeState
    from ..services.communication.connection_manager import ConnectionManager
    MODELS_AVAILABLE = True
except ImportError:
    # Fallback for when models aren't available yet
    MODELS_AVAILABLE = False
    
    class Position:
        def __init__(self, x=0, y=0, z=0, r=0):
            self.x, self.y, self.z, self.r = x, y, z, r
    
    class MicroscopeState:
        IDLE = "idle"
        MOVING = "moving"
        ERROR = "error"
    
    class MicroscopeModel:
        def __init__(self):
            self.current_position = Position()
            self.state = MicroscopeState.IDLE


class MicroscopeController:
    """
    Controller for microscope operations.
    
    Manages microscope state, position updates, and coordinates
    communication with the hardware.
    """
    
    def __init__(self, model=None, connection_manager=None):
        """
        Initialize microscope controller.
        
        Args:
            model: Microscope data model (optional, will create default if None)
            connection_manager: Connection manager for hardware communication (optional)
        """
        # Create default model if none provided
        if model is None:
            if MODELS_AVAILABLE:
                self.model = MicroscopeModel()
            else:
                self.model = MicroscopeModel()  # Fallback version
        else:
            self.model = model
            
        self.connection = connection_manager
        self._observers: List[Callable] = []
        self._lock = Lock()
        self.logger = logging.getLogger(__name__)
        
        # Subscribe to position updates from connection if available
        if self.connection and hasattr(self.connection, 'subscribe_position_updates'):
            self.connection.subscribe_position_updates(self._handle_position_update)
        
    def subscribe(self, callback: Callable):
        """
        Subscribe to model updates.
        
        Args:
            callback: Function to call when model updates
        """
        with self._lock:
            self._observers.append(callback)
    
    def unsubscribe(self, callback: Callable):
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
        if MODELS_AVAILABLE:
            self.model.state = MicroscopeState.MOVING
        self._notify_observers()
        
        try:
            # Send move command if connection available
            if self.connection and hasattr(self.connection, 'send_move_command'):
                self.connection.send_move_command(position)
            
            # Update model with target position
            self.model.current_position = position
            if MODELS_AVAILABLE:
                self.model.state = MicroscopeState.IDLE
            
        except Exception as e:
            self.logger.error(f"Failed to move to position: {e}")
            if MODELS_AVAILABLE:
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
    
    def disconnect(self):
        """Disconnect from microscope."""
        if self.connection and hasattr(self.connection, 'disconnect'):
            self.connection.disconnect()
        self.logger.info("Microscope controller disconnected")
    
    def emergency_stop(self):
        """Execute emergency stop."""
        try:
            if self.connection and hasattr(self.connection, 'send_emergency_stop'):
                self.connection.send_emergency_stop()
            if MODELS_AVAILABLE:
                self.model.state = MicroscopeState.IDLE
        except Exception as e:
            self.logger.error(f"Emergency stop failed: {e}")
            if MODELS_AVAILABLE:
                self.model.state = MicroscopeState.ERROR
        finally:
            self._notify_observers()