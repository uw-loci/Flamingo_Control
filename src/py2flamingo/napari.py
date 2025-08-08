# src/py2flamingo/napari.py
from PyQt5.QtWidgets import QWidget, QVBoxLayout
from typing import Dict, Any
from .GUI import GUI as LegacyGUI
from .views.widgets.viewer_widget import ViewerWidget
from .views.napari_view import NapariViewer
from .core.legacy_adapter import image_queue, visualize_queue

class NapariFlamingoGui(QWidget):
    """Dockable widget for using Flamingo controls inside Napari.
    
    This wraps the existing GUI's central widget (controls) and replaces
    its image area with a lightweight viewer widget that forwards images
    into Napari via the ViewerInterface implementation.
    """
    def __init__(self, viewer, legacy_objects: Dict[str, Any]):
        super().__init__()
        # Instantiate legacy GUI but do not show as a separate window
        self._gui = LegacyGUI(legacy_objects)
        # Hide the standalone window frame; we only use its central widget
        # and avoid showing/closing the QMainWindow directly.
        # Hide image label if present (Napari will render images)
        if hasattr(self._gui, 'image_label'):
            self._gui.image_label.hide()
        
        # Build layout
        layout = QVBoxLayout(self)
        # Add the GUI's central widget into our dock
        try:
            central = self._gui.centralWidget()
        except Exception:
            central = None
        if central is not None:
            central.setParent(self)
            layout.addWidget(central)
        else:
            # Fallback: add entire GUI as a widget (not ideal, but functional)
            layout.addWidget(self._gui)
        
        # Add viewer widget that pushes data into Napari canvas
        nv = NapariViewer(viewer)
        self.viewer_widget = ViewerWidget(
            viewer=nv,
            image_queue=image_queue,
            visualize_queue=visualize_queue
        )
        layout.addWidget(self.viewer_widget)
