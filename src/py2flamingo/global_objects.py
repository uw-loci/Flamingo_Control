# global_objects.py
import platform
import time
from queue import Empty, Queue
from threading import Event

# Determine the operating system
OS = platform.system()

# Create queues for handling data between threads
(
    image_queue,
    command_queue,
    z_plane_queue,
    intensity_queue,
    visualize_queue,
    command_data_queue,
    stage_location_queue,
    other_data_queue,
) = (
    Queue(),
    Queue(),
    Queue(),
    Queue(),
    Queue(),
    Queue(),
    Queue(),
    Queue(),
)

# Create events for signaling between threads
(
    view_snapshot,
    system_idle,
    processing_event,
    send_event,
    terminate_event,
    visualize_event,
) = (Event(), Event(), Event(), Event(), Event(), Event())


def clear_all_events_queues():
    """
    Clears all events and queues. This is useful to ensure a clean state before starting a new operation.
    """
    # List of all events
    events = [
        view_snapshot,
        system_idle,
        processing_event,
        send_event,
        terminate_event,
        visualize_event,
    ]
    # List of all queues
    queues = [
        image_queue,
        command_queue,
        z_plane_queue,
        intensity_queue,
        visualize_queue,
        command_data_queue,
        stage_location_queue,
        other_data_queue,
    ]

    # Clear all events
    for event in events:
        event.clear()

    # Empty all queues
    for queue in queues:
        while not queue.empty():
            try:
                queue.get(False)
            except Empty:
                continue
