# src/py2flamingo/napari.py
from PyQt5.QtWidgets import QWidget, QVBoxLayout
from typing import Dict, Any

from .GUI import GUI as LegacyGUI
from .views.widgets.viewer_widget import ViewerWidget
from .views.napari_view import NapariViewer
from .core.legacy_adapter import image_queue, visualize_queue  # or from global_objects if that's what you're using

class NapariFlamingoGui(QWidget):
    """Dockable widget for using Flamingo controls inside Napari.
    
    This wraps the existing GUI's central widget (controls) and replaces
    its image area with a lightweight viewer widget that forwards images
    into Napari via the ViewerInterface implementation.
    """
    def __init__(self, viewer, legacy_objects: Dict[str, Any]):
        super().__init__()

        # Embed the existing control panel without its own image view
        self._gui = LegacyGUI(legacy_objects)
        if hasattr(self._gui, 'image_label'):
            self._gui.image_label.hide()

        layout = QVBoxLayout(self)
        central = getattr(self._gui, "centralWidget", lambda: None)()
        if central is not None:
            central.setParent(self)
            layout.addWidget(central)
        else:
            layout.addWidget(self._gui)

        # Wrap the provided napari.Viewer in the thin adapter
        nv = NapariViewer(viewer)
        self.viewer_widget = ViewerWidget(
            viewer=nv,
            image_queue=image_queue,
            visualize_queue=visualize_queue
        )
        layout.addWidget(self.viewer_widget)

