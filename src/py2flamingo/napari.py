import napari
from GUI import Py2FlamingoGUI
import numpy as np


class NapariFlamingoGui(Py2FlamingoGUI):
    def __init__(self, queues_and_events, viewer):
        super().__init__(queues_and_events)
        self.viewer = viewer
        self.preview = viewer.add_image(np.zeros((10, 10)), name="Preview")

    def display_image(self, image):
        self.preview.data = image


if __name__ == "__main__":
    from global_objects import queues_and_events

    viewer = napari.Viewer()
    controller = Py2FlamingoGUI(queues_and_events, viewer)
    viewer.window.add_dock_widget(controller, area="right")

    napari.run()
