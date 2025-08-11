# src/py2flamingo/core/legacy_adapter.py
"""
Adapter for maintaining backward compatibility with global objects.

This module provides the legacy global objects interface while using
the new managed approach internally.
"""
import platform

# Import the actual implementations
from .events import EventManager
from .queue_manager import QueueManager

# Create singleton instances
_event_manager = EventManager()
_queue_manager = QueueManager()

# Legacy queue exports - these are the actual Queue objects
image_queue = _queue_manager.get_queue('image')
command_queue = _queue_manager.get_queue('command')
z_plane_queue = _queue_manager.get_queue('z_plane')
intensity_queue = _queue_manager.get_queue('intensity')
visualize_queue = _queue_manager.get_queue('visualize')
command_data_queue = _queue_manager.get_queue('command_data')
stage_location_queue = _queue_manager.get_queue('stage_location')
other_data_queue = _queue_manager.get_queue('other_data')

# Legacy event exports - these are the actual Event objects
view_snapshot = _event_manager.get_event('view_snapshot')
system_idle = _event_manager.get_event('system_idle')
processing_event = _event_manager.get_event('processing')
send_event = _event_manager.get_event('send')
terminate_event = _event_manager.get_event('terminate')
visualize_event = _event_manager.get_event('visualize')

# OS export
OS = platform.system()

def clear_all_events_queues():
    """
    Clears all events and queues. 
    This is useful to ensure a clean state before starting a new operation.
    
    This function was in the original global_objects.py
    """
    _event_manager.clear_all()
    _queue_manager.clear_all()

# For code that expects to import the managers directly
def get_queue_manager():
    """Get the global queue manager instance."""
    return _queue_manager

def get_event_manager():
    """Get the global event manager instance."""
    return _event_manager
