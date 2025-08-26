# ============================================================================
# src/py2flamingo/controllers/multi_angle_controller.py
"""
Controller for multi-angle data collection.
"""

import logging
from typing import Optional
import numpy as np

from py2flamingo.models.microscope import Position
from controllers.microscope_controller import MicroscopeController
from controllers.sample_controller import SampleController
from py2flamingo.services.communication.connection_manager import ConnectionManager
import py2flamingo.utils.calculations as calc
import py2flamingo.utils.file_handlers as txt


class MultiAngleController:
    """
    Controller to handle multi-angle image collection.
    """
    def __init__(self,
                 microscope_controller: MicroscopeController,
                 sample_controller: SampleController,
                 connection_manager: ConnectionManager):
        """
        Initialize multi-angle controller.
        
        Args:
            microscope_controller: Main microscope controller.
            sample_controller: Sample controller for image capture.
            connection_manager: Connection manager for microscope communication.
        """
        self.logger = logging.getLogger(__name__)
        self.microscope_controller = microscope_controller
        self.sample_controller = sample_controller
        self.connection = connection_manager

    def collect_images(self, angles: Optional[list] = None, save_directory: str = "multi_angle_images"):
        """
        Collect images of the sample at multiple angles and save to disk.
        
        Args:
            angles: List of angles (degrees) at which to capture images. If None, uses default angles.
            save_directory: Directory to save collected images.
        """
        if angles is None:
            angles = [0, 90, 180, 270]
        try:
            # Ensure save directory exists
            import os
            os.makedirs(save_directory, exist_ok=True)
            for angle in angles:
                # Rotate microscope to the angle
                self.microscope_controller.rotate_to(angle)
                # Capture image from sample controller
                image = self.sample_controller.capture_image()
                # Save image to file
                filename = os.path.join(save_directory, f"image_{angle}.png")
                image.save(filename)  # assuming image is PIL Image or similar
                self.logger.info(f"Saved image at {angle} degrees to {filename}.")
        except Exception as e:
            self.logger.error(f"Error during multi-angle collection: {e}")
