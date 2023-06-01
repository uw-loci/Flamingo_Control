# TO DO? Create initial dialog to ask about which microscope to connect to. Create named files based on the microscope (settings, workflows)
# TODO move the microscope connection functions out of the GUI. Or move the GUI out of init?
# TODO cancel button does cancel, but then most functions do not work after.
#Run black . and isort --profile black

######################################

from PyQt5.QtWidgets import QApplication
#from global_objects import 
from GUI import Py2FlamingoGUI
import sys
from global_objects import view_snapshot, system_idle, processing_event, send_event, terminate_event, visualize_event
from global_objects import image_queue, command_queue, z_plane_queue, intensity_queue, visualize_queue, command_data_queue, stage_location_queue, other_data_queue
from global_objects import OS

queues_and_events = {
    'queues': [image_queue, command_queue, z_plane_queue, intensity_queue, visualize_queue],
    'events': [view_snapshot, system_idle, processing_event, send_event, terminate_event]
}

if __name__ == "__main__":
    app = QApplication(sys.argv)

    controller = Py2FlamingoGUI(queues_and_events)

    controller.show()

    sys.exit(app.exec_())
