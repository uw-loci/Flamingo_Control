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

from ..viewer_interface import ViewerInterface

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