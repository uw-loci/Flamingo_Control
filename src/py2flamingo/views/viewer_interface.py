# src/py2flamingo/views/viewer_interface.py
"""
Interface class for viewer integration.
Defines methods that any viewer (Napari or custom) should implement to work with ViewerWidget.
"""

from abc import ABC, abstractmethod
import numpy as np


class ViewerInterface(ABC):
    """
    Abstract base class defining the interface for a viewer to display images.
    """
    
    @abstractmethod
    def display_image(self, image: np.ndarray, title: str = "", metadata: dict = None):
        """
        Display an image in the viewer.
        
        Args:
            image: Numpy array of image data.
            title: Title or name for the image.
            metadata: Additional metadata for the image.
        """
        pass
