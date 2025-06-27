# TO DO? Create initial dialog to ask about which microscope to connect to. Create named files based on the microscope (settings, workflows)
# TODO Running from command line currently not supported but should be the goal
# TODO Feedback window indicating status
# Run 
# black .
# isort . --profile black
# TODO create chatGPT prompt that allows the creation of new functions and buttons.
# TODO TODO TODO Cancel button stopped working at some point during the restructuring. It does cancel but does not leave the program in a workable state.
######################################

# src/py2flamingo/__init__.py
"""
Py2Flamingo - Control software for Flamingo light sheet microscopes.

This package provides a GUI and control system for Flamingo microscopes,
handling image acquisition, sample location, and multi-angle collection.
The primary interface is through Napari, providing advanced visualization
capabilities alongside microscope control.
"""

__version__ = "0.5.0"
__author__ = "Your Name"
__email__ = "your.email@example.com"

# Import main components for external use
from .application import Application

# For Napari plugin registration
from .napari import NapariFlamingoGui

# Keep backward compatibility imports during migration
# These will eventually be removed in favor of the Application class
try:
    from .core.legacy_adapter import (
        # Operating system
        OS,
        # Queues
        command_data_queue,
        command_queue,
        image_queue,
        intensity_queue,
        other_data_queue,
        stage_location_queue,
        visualize_queue,
        z_plane_queue,
        # Events
        processing_event,
        send_event,
        system_idle,
        terminate_event,
        view_snapshot,
        visualize_event,
        # Utility function
        clear_all_events_queues
    )
    
    # Legacy GUI support
    from .GUI import Py2FlamingoGUI
    
    # Mark legacy imports as available
    _LEGACY_SUPPORT = True
    
except ImportError:
    # If core modules aren't available yet, use global_objects directly
    # This allows gradual migration
    try:
        from .global_objects import (
            OS,
            command_data_queue,
            command_queue,
            image_queue,
            intensity_queue,
            other_data_queue,
            processing_event,
            send_event,
            stage_location_queue,
            system_idle,
            terminate_event,
            view_snapshot,
            visualize_event,
            visualize_queue,
            z_plane_queue,
            clear_all_events_queues
        )
        from .GUI import Py2FlamingoGUI
        _LEGACY_SUPPORT = True
    except ImportError:
        _LEGACY_SUPPORT = False

# Napari plugin registration
def napari_experimental_provide_dock_widget():
    """
    Napari plugin hook implementation.
    
    This allows Py2Flamingo to be loaded as a Napari plugin.
    Users can access it through Plugins -> Py2Flamingo in Napari.
    """
    from .napari_plugin import create_flamingo_widget
    return [(create_flamingo_widget, {"name": "Flamingo Control"})]

# Public API
__all__ = [
    # Main application class
    'Application',
    
    # Napari integration
    'NapariFlamingoGui',
    
    # Version info
    '__version__',
    '__author__',
    '__email__',
]

# Add legacy exports if available
if _LEGACY_SUPPORT:
    __all__.extend([
        # Legacy GUI
        'Py2FlamingoGUI',
        
        # Queues (for backward compatibility)
        'command_queue',
        'image_queue',
        'visualize_queue',
        'command_data_queue',
        'intensity_queue',
        'other_data_queue',
        'stage_location_queue',
        'z_plane_queue',
        
        # Events (for backward compatibility)
        'processing_event',
        'send_event',
        'system_idle',
        'terminate_event',
        'view_snapshot',
        'visualize_event',
        
        # Utilities
        'clear_all_events_queues',
        'OS',
    ])

# Module-level initialization
def get_application():
    """
    Get or create the application instance.
    
    This provides a singleton-like interface for accessing
    the application coordinator.
    
    Returns:
        Application: The application instance
    """
    if not hasattr(get_application, '_instance'):
        get_application._instance = Application()
    return get_application._instance