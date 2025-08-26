# src/py2flamingo/global_objects.py - Updated current version
"""
Module defining global queues and events for inter-thread communication.
"""

from queue import Queue
from threading import Event
import platform

# Queues for data and commands
image_queue = Queue()
command_queue = Queue()
z_plane_queue = Queue()
intensity_queue = Queue()
visualize_queue = Queue()
other_data_queue = Queue()
command_data_queue = Queue()
stage_location_queue = Queue()

# Events for synchronization
view_snapshot = Event()
system_idle = Event()
processing_event = Event()
send_event = Event()
terminate_event = Event()
visualize_event = Event()  # <- This was missing!

# OS info (tests expect this)
OS = platform.system()

def clear_all_events_queues():
    """
    Clear all event flags and queues.
    Useful for resetting system state.
    """
    # Clear events
    for ev in [
        view_snapshot,
        system_idle,
        processing_event,
        send_event,
        terminate_event,
        visualize_event,
    ]:
        ev.clear()
    
    # Set system_idle after clearing (expected initial state)
    system_idle.set()
    
    # Empty queues
    for q in [
        image_queue,
        command_queue,
        z_plane_queue,
        intensity_queue,
        visualize_queue,
        other_data_queue,
        command_data_queue,
        stage_location_queue,
    ]:
        try:
            while True:
                q.get_nowait()
        except Exception:
            continue