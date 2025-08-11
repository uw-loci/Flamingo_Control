# src/py2flamingo/views/napari_viewer.py
"""
Napari implementation of the viewer interface.
"""
import napari
import numpy as np
from typing import Optional, Dict, Any, Tuple
import logging

from .viewer_interface import ViewerInterface

class NapariViewer(ViewerInterface):
    """
    Napari-specific implementation of the viewer interface.
    """
    
    def __init__(self, viewer: napari.Viewer):
        """
        Initialize with existing Napari viewer.
        
        Args:
            viewer: Napari viewer instance
        """
        self.viewer = viewer
        self.layers = {}  # name -> layer mapping
        self.logger = logging.getLogger(__name__)
    
    def add_image(self, 
                  data: np.ndarray, 
                  name: str,
                  scale: Optional[Tuple[float, ...]] = None,
                  metadata: Optional[Dict[str, Any]] = None) -> Any:
        """Add image to Napari viewer."""
        try:
            # Remove existing layer with same name
            if name in self.layers:
                self.remove_image(name)
            
            # Add new layer
            layer = self.viewer.add_image(
                data,
                name=name,
                scale=scale,
                metadata=metadata or {}
            )
            
            self.layers[name] = layer
            return layer
            
        except Exception as e:
            self.logger.error(f"Failed to add image {name}: {e}")
            return None
    
    def update_image(self, name: str, data: np.ndarray) -> None:
        """Update existing image data."""
        if name in self.layers:
            self.layers[name].data = data
        else:
            # Create new if doesn't exist
            self.add_image(data, name)
    
    def remove_image(self, name: str) -> None:
        """Remove image from viewer."""
        if name in self.layers:
            layer = self.layers.pop(name)
            if layer in self.viewer.layers:
                self.viewer.layers.remove(layer)
    
    def clear_all(self) -> None:
        """Remove all images."""
        for name in list(self.layers.keys()):
            self.remove_image(name)
    
    def set_3d_mode(self, enabled: bool) -> None:
        """Toggle 3D mode in Napari."""
        self.viewer.dims.ndisplay = 3 if enabled else 2
