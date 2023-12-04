import napari
from py2flamingo.GUI import Py2FlamingoGUI
import numpy as np


class NapariFlamingoGui(Py2FlamingoGUI):
    def __init__(self, queues_and_events, viewer):
        super().__init__(queues_and_events)
        self.viewer = viewer
        self.preview = None #viewer.add_image(np.zeros((10, 10)), name="Preview")
        self.image_label.hide()

    def display_image(self, image):
        # Ensure the image is a 2D array
        if image.ndim != 2:
            raise ValueError("Provided image is not a 2D array")

        # Convert the image to a 3D array (shape: (1, height, width))
        image = np.expand_dims(image, axis=0)

        # If the preview layer is not initialized, create it
        if self.preview is None:
            self.preview = self.viewer.add_image(image, name="Preview")
            self.preview._keep_auto_contrast = True
        elif self.preview.data.ndim == 3:
            # Concatenate the new image
            self.preview.data = np.concatenate((self.preview.data, image), axis=0)

        else:
            raise ValueError("Existing data is not a 3D array")

        # Set the current step to the last image
        self.viewer.dims.set_current_step(0, len(self.preview.data) - 1)
        
if __name__ == "__main__":
    from py2flamingo import queues_and_events

    viewer = napari.Viewer()
    controller = NapariFlamingoGui(queues_and_events, viewer)
    viewer.window.add_dock_widget(controller, area="right")

    napari.run()
