# ============================================================================
# src/py2flamingo/controllers/ellipse_controller.py
"""
Controller for ellipse tracing and sample tracking by angle.
"""

import logging
from typing import Optional, List, Tuple
import numpy as np
from pathlib import Path

from py2flamingo.models.microscope import Position
from py2flamingo.models.ellipse import EllipseModel, EllipseParameters
from controllers.microscope_controller import MicroscopeController
from controllers.sample_controller import SampleController
from py2flamingo.services.communication.connection_manager import ConnectionManager
from py2flamingo.services.ellipse_tracing_service import EllipseTracingService
import py2flamingo.utils.calculations as calc
import py2flamingo.utils.file_handlers as txt


class EllipseController:
    """
    Controller for ellipse-based sample tracking.
    
    Handles sample tracking through rotation and ellipse fitting.
    """
    
    def __init__(self,
                 microscope_controller: MicroscopeController,
                 sample_controller: SampleController,
                 connection_manager: ConnectionManager):
        """
        Initialize ellipse controller.
        
        Args:
            microscope_controller: Main microscope controller
            sample_controller: Sample controller
            connection_manager: Connection manager
        """
        self.logger = logging.getLogger(__name__)
        self.microscope_controller = microscope_controller
        self.sample_controller = sample_controller
        self.connection = connection_manager
        # Service for ellipse tracing
        self.ellipse_service = EllipseTracingService()
        # Current ellipse model
        self.ellipse_model: Optional[EllipseModel] = None
    
    def trace_sample(self, start_angle: float = 0.0, end_angle: float = 360.0, step: float = 90.0) -> Optional[EllipseParameters]:
        """
        Trace the sample by rotating and capturing its outline as an ellipse.
        
        Args:
            start_angle: Starting angle for rotation.
            end_angle: Ending angle for rotation.
            step: Rotation step in degrees.
        
        Returns:
            EllipseParameters if ellipse traced successfully, otherwise None.
        """
        try:
            angles = np.arange(start_angle, end_angle, step)
            ellipse_params_list: List[EllipseParameters] = []
            
            # Rotate sample through specified angles and gather ellipse parameters
            for angle in angles:
                self.microscope_controller.rotate_to(angle)
                image = self.sample_controller.capture_image()  # capture image at this angle
                params = self.ellipse_service.fit_ellipse(image)
                if params is not None:
                    ellipse_params_list.append(params)
            
            # Combine ellipse parameters from different angles
            if ellipse_params_list:
                combined_params = self.ellipse_service.combine_ellipse_parameters(ellipse_params_list)
                self.ellipse_model = EllipseModel(parameters=combined_params)
                return combined_params
            else:
                return None
        except Exception as e:
            self.logger.error(f"Error tracing sample ellipse: {e}")
            return None
