# src/py2flamingo/views/viewer_widget.py
"""
Simple widget that integrates with any viewer implementation.
"""
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel
from PyQt5.QtCore import QTimer, pyqtSignal
import logging
from typing import Optional, Dict, Any
from queue import Empty

from py2flamingo.views.viewer_interface import ViewerInterface

class ViewerWidget(QWidget):
    """
    Minimal widget for displaying microscope images in any viewer.
    
    This is a clean, simple widget that just handles image display,
    not the full GUI functionality.
    """
    
    # Signal for thread-safe updates
    image_ready = pyqtSignal(object, str, dict)
    
    def __init__(self, viewer: ViewerInterface, 
                 image_queue: 'Queue',
                 visualize_queue: 'Queue'):
        """
        Initialize viewer widget.
        
        Args:
            viewer: An implementation of ViewerInterface.
            image_queue: Queue with images from microscope.
            visualize_queue: Queue for images to be visualized.
        """
        super().__init__()
        self.viewer = viewer
        self.image_queue = image_queue
        self.visualize_queue = visualize_queue
        self.logger = logging.getLogger(__name__)
        
        self.layout = QVBoxLayout()
        self.setLayout(self.layout)
        # Example UI elements
        self.label = QLabel("Viewer Status: Ready")
        self.layout.addWidget(self.label)
        
        # Timer to poll for images
        self.timer = QTimer()
        self.timer.timeout.connect(self._check_for_image)
        self.timer.start(500)  # check every 500 ms
        
        # Connect signal to viewer display
        self.image_ready.connect(self._display_image)
    
    def _check_for_image(self):
        """
        Internal method to check queues for new images.
        """
        try:
            image = self.visualize_queue.get_nowait()
            # Emit signal for thread-safe update
            self.image_ready.emit(image, "Live Image", {})
        except Empty:
            return
    
    def _display_image(self, image: Any, title: str, metadata: Dict[str, Any]):
        """
        Internal slot to display the image using the provided viewer.
        """
        try:
            self.viewer.display_image(image, title=title, metadata=metadata)
            self.label.setText("Last update: Image displayed.")
        except Exception as e:
            self.logger.error(f"Failed to display image: {e}")
            self.label.setText("Error displaying image.")
