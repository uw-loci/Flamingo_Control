import sys

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
)
from .GUI import Py2FlamingoGUI
from PyQt5.QtWidgets import QApplication

queues_and_events = {
    "queues": [
        image_queue,
        command_queue,
        z_plane_queue,
        intensity_queue,
        visualize_queue,
    ],
    "events": [
        view_snapshot,
        system_idle,
        processing_event,
        send_event,
        terminate_event,
    ],
}

app = QApplication(sys.argv)

controller = Py2FlamingoGUI(queues_and_events)

controller.show()

sys.exit(app.exec_())
