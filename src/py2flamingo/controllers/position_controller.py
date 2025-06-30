# src/py2flamingo/controllers/position_controller.py
"""
Controller for microscope position management.

This controller handles all position-related operations including
movement, validation, and position tracking.
"""
import logging
from typing import List, Optional, Callable
from dataclasses import dataclass

from py2flamingo.models.microscope import Position, MicroscopeState
from py2flamingo.services.connection_service import ConnectionService
from py2flamingo.core.queue_manager import QueueManager
from py2flamingo.core.events import EventManager

@dataclass
class AxisCode:
    """Axis codes for stage movement commands."""
    X = 1
    Y = 2
    Z = 3
    R = 4

class PositionController:
    """
    Controller for managing microscope stage positions.
    
    This controller replaces the functionality from go_to_position.py
    with a cleaner, more maintainable interface.
    
    Attributes:
        connection_service: Service for microscope communication
        queue_manager: Manager for command queues
        event_manager: Manager for synchronization events
        logger: Logger instance
    """
    
    # Command codes from command_list.txt
    COMMAND_CODES_STAGE_POSITION_SET = 24580
    COMMAND_CODES_STAGE_POSITION_GET = 24584
    
    def __init__(self, 
                 connection_service: ConnectionService,
                 queue_manager: QueueManager,
                 event_manager: EventManager):
        """
        Initialize the position controller.
        
        Args:
            connection_service: Service for microscope communication
            queue_manager: Queue manager for commands
            event_manager: Event manager for synchronization
        """
        self.connection = connection_service
        self.queue_manager = queue_manager
        self.event_manager = event_manager
        self.logger = logging.getLogger(__name__)
        self.axis = AxisCode()
    
    def go_to_position(self, position: Position, 
                      validate: bool = True,
                      callback: Optional[Callable[[str], None]] = None) -> None:
        """
        Move microscope to specified position.
        
        This method replaces the original go_to_position function with
        improved error handling and progress reporting.
        
        Args:
            position: Target position
            validate: Whether to validate position before movement
            callback: Optional callback for progress updates
            
        Raises:
            ValueError: If position is invalid
            RuntimeError: If not connected to microscope
        """
        # Check connection
        if not self.connection.is_connected():
            raise RuntimeError("Not connected to microscope")
        
        # Validate position if requested
        if validate:
            self._validate_position(position)
        
        self.logger.info(f"Moving to position: {position}")
        
        # Original comment from go_to_position:
        # Look in the functions/command_list.txt file for other command codes, or add more
        
        # Send movement commands for each axis
        self._move_axis(self.axis.X, position.x, "X-axis")
        self._move_axis(self.axis.Z, position.z, "Z-axis")  
        self._move_axis(self.axis.R, position.r, "Rotation")
        self._move_axis(self.axis.Y, position.y, "Y-axis")  # Y-axis last as in original
        
        if callback:
            callback("Movement complete")
    
    def go_to_xyzr(self, xyzr: List[float], **kwargs) -> None:
        """
        Move to position specified as list (backward compatibility).
        
        This method provides backward compatibility with the original
        go_to_XYZR function from microscope_connect.py.
        
        Args:
            xyzr: List of [x, y, z, r] coordinates
            **kwargs: Additional arguments passed to go_to_position
        """
        # Original comment from microscope_connect.py:
        # Unpack the provided XYZR coordinates, r is in degrees, other values are in mm
        x, y, z, r = xyzr
        
        position = Position(x=float(x), y=float(y), z=float(z), r=float(r))
        self.go_to_position(position, **kwargs)
    
    def _move_axis(self, axis_code: int, value: float, axis_name: str) -> None:
        """
        Move a specific axis to the specified value.
        
        This is the refactored version of move_axis from microscope_connect.py.
        
        Args:
            axis_code: The code of the axis to move
            value: The value to move the axis to
            axis_name: Human-readable axis name for logging
        """

        self.logger.debug(f"Moving {axis_name} to {value}")
        
        try:
            # Put command data in queue
            command_data = [axis_code, 0, 0, value]
            self.queue_manager.put_nowait('command_data', command_data)
            
            # Put command in queue
            self.queue_manager.put_nowait('command', self.COMMAND_CODES_STAGE_POSITION_SET)
            
            # Trigger send event
            self.event_manager.set_event('send')
            
            # Wait for command to be processed with timeout
            import time
            timeout = 5.0  # 5 second timeout
            start_time = time.time()
            
            while not self.queue_manager.get_queue('command').empty():
                if time.time() - start_time > timeout:
                    raise TimeoutError(f"Timeout waiting for {axis_name} movement")
                time.sleep(0.1)
                
        except Exception as e:
            self.logger.error(f"Failed to move {axis_name}: {e}")
            raise

    
    def _validate_position(self, position: Position) -> None:
        """
        Validate that position is within stage limits.
        
        Args:
            position: Position to validate
            
        Raises:
            ValueError: If position is outside limits
        """
        # Get stage limits from configuration
        from py2flamingo.services.configuration_service import ConfigurationService
        config_service = ConfigurationService()
        limits = config_service.get_stage_limits()
        
        # Check each axis
        axes = [
            ('x', position.x, limits['x']),
            ('y', position.y, limits['y']),
            ('z', position.z, limits['z']),
            ('r', position.r, limits['r'])
        ]
        
        for axis_name, value, axis_limits in axes:
            if not (axis_limits['min'] <= value <= axis_limits['max']):
                raise ValueError(
                    f"{axis_name.upper()}-axis position {value} is outside limits "
                    f"[{axis_limits['min']}, {axis_limits['max']}]"
                )
    
    def get_current_position(self) -> Optional[Position]:
        """
        Get current position from microscope.
        
        Returns:
            Optional[Position]: Current position or None if error
        """
        try:
            # Send get position command
            self.queue_manager.put_nowait('command', self.COMMAND_CODES_STAGE_POSITION_GET)
            self.event_manager.set_event('send')
            
            # Wait for response (this would need proper implementation)
            # For now, return None
            self.logger.warning("Get position not fully implemented")
            return None
            
        except Exception as e:
            self.logger.error(f"Failed to get position: {e}")
            return None