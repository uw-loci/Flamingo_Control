# src/py2flamingo/views/viewer_interface.py
"""
Abstract viewer interface for modular visualization support.

This allows Py2Flamingo to work with different viewers (Napari, NDV, etc.)
without tight coupling to any specific implementation.
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Tuple
import numpy as np

class ViewerInterface(ABC):
    """
    Abstract interface for image viewers.
    
    Any viewer (Napari, NDV, etc.) can implement this interface
    to work with Py2Flamingo.
    """
    
    @abstractmethod
    def add_image(self, 
                  data: np.ndarray, 
                  name: str,
                  scale: Optional[Tuple[float, ...]] = None,
                  metadata: Optional[Dict[str, Any]] = None) -> Any:
        """Add an image to the viewer."""
        pass
    
    @abstractmethod
    def update_image(self, name: str, data: np.ndarray) -> None:
        """Update existing image data."""
        pass
    
    @abstractmethod
    def remove_image(self, name: str) -> None:
        """Remove an image from the viewer."""
        pass
    
    @abstractmethod
    def clear_all(self) -> None:
        """Remove all images."""
        pass
    
    @abstractmethod
    def set_3d_mode(self, enabled: bool) -> None:
        """Toggle between 2D and 3D visualization."""
        pass

# ============================================================================
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

# ============================================================================
# src/py2flamingo/views/viewer_widget.py
"""
Simple widget that integrates with any viewer implementation.
"""
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel
from PyQt5.QtCore import QTimer, pyqtSignal
import numpy as np
import logging
from typing import Optional, Dict, Any
from queue import Empty

from .viewer_interface import ViewerInterface

class ViewerWidget(QWidget):
    """
    Minimal widget for displaying microscope images in any viewer.
    
    This is a clean, simple widget that just handles image display,
    not the full GUI functionality.
    """
    
    # Signal for thread-safe updates
    image_ready = pyqtSignal(np.ndarray, str, dict)
    
    def __init__(self, viewer: ViewerInterface, 
                 image_queue: 'Queue',
                 visualize_queue: 'Queue'):
        """
        Initialize viewer widget.
        
        Args:
            viewer: Any viewer implementing ViewerInterface
            image_queue: Queue for acquired images
            visualize_queue: Queue for preview images
        """
        super().__init__()
        self.viewer = viewer
        self.image_queue = image_queue
        self.visualize_queue = visualize_queue
        self.logger = logging.getLogger(__name__)
        
        self._setup_ui()
        self._setup_polling()
        
        # Connect signal
        self.image_ready.connect(self._display_image)
    
    def _setup_ui(self):
        """Create minimal UI."""
        layout = QVBoxLayout(self)
        
        # Title
        title = QLabel("Image Display")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)
        
        # Control buttons
        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self.viewer.clear_all)
        layout.addWidget(clear_btn)
        
        toggle_3d_btn = QPushButton("Toggle 3D")
        toggle_3d_btn.clicked.connect(lambda: self.viewer.set_3d_mode(True))
        layout.addWidget(toggle_3d_btn)
        
        layout.addStretch()
    
    def _setup_polling(self):
        """Setup timer to poll image queues."""
        self.poll_timer = QTimer()
        self.poll_timer.timeout.connect(self._check_queues)
        self.poll_timer.start(100)  # 100ms
    
    def _check_queues(self):
        """Check for new images in queues."""
        # Check visualize queue (preview)
        try:
            image = self.visualize_queue.get_nowait()
            if isinstance(image, np.ndarray):
                self.image_ready.emit(image, "Preview", {"type": "preview"})
        except Empty:
            pass
        
        # Check image queue (acquisitions)
        try:
            image = self.image_queue.get_nowait()
            if isinstance(image, np.ndarray):
                self.image_ready.emit(image, "Acquisition", {"type": "acquisition"})
        except Empty:
            pass
    
    def _display_image(self, data: np.ndarray, name: str, metadata: dict):
        """Display image in viewer (thread-safe)."""
        self.viewer.update_image(name, data)
