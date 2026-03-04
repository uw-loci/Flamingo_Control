# src/py2flamingo/views/ndv_view.py
from typing import Any, Dict

import numpy as np

from .viewer_interface import ViewerInterface


class NDVViewer(ViewerInterface):
    def __init__(self, ndv_viewer):
        self.viewer = ndv_viewer  # your NDV viewer object

    def display_image(
        self, image: np.ndarray, title: str = "", metadata: Dict[str, Any] = None
    ):
        # Call NDV's API to show/refresh the image
        # self.viewer.show(image, name=title, meta=metadata)  # example
        pass
