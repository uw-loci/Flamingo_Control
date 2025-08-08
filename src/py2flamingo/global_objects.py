# global_objects.py (proxy to legacy adapter)
from py2flamingo.core.legacy_adapter import (
    image_queue,
    command_queue,
    z_plane_queue,
    intensity_queue,
    visualize_queue,
    visualize_event,
    view_snapshot,
    terminate_event,
    processing_event,
    send_event,
    system_idle,
    stage_location_queue,
    other_data_queue,
    clear_all_events_queues,
)

# Re-export names for old imports
__all__ = [
    'image_queue','command_queue','z_plane_queue','intensity_queue','visualize_queue',
    'visualize_event','view_snapshot','terminate_event','processing_event','send_event',
    'system_idle','stage_location_queue','other_data_queue','clear_all_events_queues'
]
