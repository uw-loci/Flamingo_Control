# src/py2flamingo/core/events.py
"""
Event manager for application-wide events.

This module replaces the global event objects with a managed approach.
"""
from threading import Event
from typing import Dict, Optional
import logging

class EventManager:
    """
    Manages threading events for synchronization.
    
    This class centralizes event management, replacing global event objects
    with a more maintainable approach.
    
    Attributes:
        _events: Dictionary of managed events
        logger: Logger instance
    """
    
    def __init__(self):
        """Initialize the event manager with standard events."""
        self.logger = logging.getLogger(__name__)
        
        # Create all events that were in global_objects.py
        self._events: Dict[str, Event] = {
            'view_snapshot': Event(),   # Signal to view a snapshot
            'system_idle': Event(),     # System is idle
            'processing': Event(),      # Processing is occurring
            'send': Event(),           # Send command to microscope
            'terminate': Event(),      # Terminate threads
            'visualize': Event(),      # Visualize image data
        }
        
        # Set initial states
        self._events['system_idle'].set()  # System starts idle
        
        self.logger.debug(f"Initialized {len(self._events)} events")
    
    def get_event(self, name: str) -> Event:
        """
        Get an event by name.
        
        Args:
            name: Name of the event
            
        Returns:
            Event: The requested event
            
        Raises:
            KeyError: If event name doesn't exist
        """
        if name not in self._events:
            raise KeyError(f"Event '{name}' not found. Available events: {list(self._events.keys())}")
        return self._events[name]
    
    def set_event(self, name: str) -> None:
        """
        Set an event.
        
        Args:
            name: Name of the event to set
            
        Raises:
            KeyError: If event doesn't exist
        """
        event = self.get_event(name)
        event.set()
        self.logger.debug(f"Event '{name}' set")
    
    def clear_event(self, name: str) -> None:
        """
        Clear an event.
        
        Args:
            name: Name of the event to clear
            
        Raises:
            KeyError: If event doesn't exist
        """
        event = self.get_event(name)
        event.clear()
        self.logger.debug(f"Event '{name}' cleared")
    
    def is_set(self, name: str) -> bool:
        """
        Check if an event is set.
        
        Args:
            name: Name of the event
            
        Returns:
            bool: True if event is set, False otherwise
        """
        try:
            return self.get_event(name).is_set()
        except KeyError:
            self.logger.error(f"Event '{name}' not found")
            return False
    
    def wait_for_event(self, name: str, timeout: Optional[float] = None) -> bool:
        """
        Wait for an event to be set.
        
        Args:
            name: Name of the event to wait for
            timeout: Maximum time to wait in seconds (None = wait forever)
            
        Returns:
            bool: True if event was set, False if timeout
        """
        try:
            event = self.get_event(name)
            result = event.wait(timeout)
            
            if not result and timeout is not None:
                self.logger.debug(f"Timeout waiting for event '{name}' after {timeout}s")
            
            return result
            
        except KeyError as e:
            self.logger.error(f"Cannot wait for event: {e}")
            return False
    
    def clear_all(self) -> None:
        """Clear all events."""
        for name, event in self._events.items():
            event.clear()
            self.logger.debug(f"Cleared event '{name}'")
        
        # Set system_idle back to True
        self._events['system_idle'].set()
        
        self.logger.info("All events cleared (system_idle set)")
    
    def get_event_states(self) -> Dict[str, bool]:
        """
        Get current state of all events.
        
        Returns:
            Dict[str, bool]: Event names to their set state
        """
        return {name: event.is_set() for name, event in self._events.items()}