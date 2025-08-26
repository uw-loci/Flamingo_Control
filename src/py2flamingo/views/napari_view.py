# src/py2flamingo/views/napari_view.py
"""
Napari adapter for the generic ViewerInterface.

This keeps integration with Napari minimal. The rest of the app talks to
ViewerInterface; swapping to a different viewer (e.g., NDV) just means
providing another small adapter implementing the same methods.
"""

from typing import Optional, Dict, Any
import numpy as np

try:
    import napari
except ImportError:  # make napari strictly optional
    napari = None

from .viewer_interface import ViewerInterface


class NapariViewer(ViewerInterface):
    """
    Thin adapter that implements ViewerInterface on top of a napari.Viewer.
    """
    def __init__(self, viewer: Optional["napari.Viewer"] = None):
        if napari is None:
            raise ImportError("Napari is not installed. Install napari or use a different viewer adapter.")
        self.viewer = viewer or napari.Viewer()
        self._layer_name = "Flamingo Live"

    def display_image(self, image: np.ndarray, title: str = "", metadata: Dict[str, Any] = None):
        """
        Display/refresh a single live image layer.
        """
        if image is None:
            return
        try:
            # Reuse the layer if present (faster), otherwise add it
            if self._layer_name in self.viewer.layers:
                layer = self.viewer.layers[self._layer_name]
                layer.data = image
                if title:
                    layer.name = title
            else:
                self.viewer.add_image(image, name=title or self._layer_name, metadata=metadata or {})
        except Exception:
            # Keep adapter fail-safe; don't propagate viewer failures into control logic
            pass


# (Optional) Legacy full Napari-only GUI – not used by the new architecture.
# Kept for backward compatibility and manual testing.
class NapariFlamingoGui:
    """
    Minimal wrapper to launch a naked napari.Viewer if someone really wants it.
    Not used by the core app (which embeds a ViewerWidget).
    """
    def __init__(self):
        if napari is None:
            raise ImportError("Napari is not installed.")
        self.viewer = napari.Viewer()

    def show(self):
        if self.viewer:
            napari.run()
