# ============================================================================
# src/py2flamingo/controllers/ellipse_controller.py
"""
Controller for ellipse tracing and sample tracking by angle.
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from controllers.microscope_controller import MicroscopeController
from controllers.sample_controller import SampleController

import py2flamingo.utils.calculations as calc
import py2flamingo.utils.file_handlers as txt
from py2flamingo.models.ellipse import EllipseModel, EllipseParameters
from py2flamingo.models.microscope import Position
from py2flamingo.services.communication.connection_manager import ConnectionManager
from py2flamingo.services.ellipse_tracing_service import EllipseTracingService


class EllipseController:
    """
    Controller for ellipse-based sample tracking.

    Handles sample tracking through rotation and ellipse fitting.
    """

    def __init__(
        self,
        microscope_controller: MicroscopeController,
        sample_controller: SampleController,
        connection_manager: ConnectionManager,
    ):
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
