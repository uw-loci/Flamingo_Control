# global_objects.py
from queue import Queue, Empty
from threading import Event
import time
image_queue, command_queue, z_plane_queue, intensity_queue, visualize_queue, command_data_queue, stage_location_queue, other_data_queue = (
    Queue(),
    Queue(),
    Queue(),
    Queue(),
    Queue(),
    Queue(),
    Queue(),
    Queue(),   
)
view_snapshot, system_idle, processing_event, send_event, terminate_event, visualize_event = (
    Event(),
    Event(),
    Event(),
    Event(),
    Event(),
    Event()
)

def clear_all_events_queues():
    events = [view_snapshot, system_idle, processing_event, send_event, terminate_event, visualize_event]
    queues = [image_queue, command_queue, z_plane_queue, intensity_queue, visualize_queue, command_data_queue, stage_location_queue, other_data_queue ]
    for event in events:
        event.clear()
    for queue in queues:
        while not queue.empty():
            try:
                queue.get(False)
            except Empty:
                continue
