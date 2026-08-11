# ============================================================================
# src/py2flamingo/controllers/multi_angle_controller.py
"""
Controller for multi-angle data collection.
"""

import logging
from typing import Optional

import numpy as np
from controllers.microscope_controller import MicroscopeController
from controllers.sample_controller import SampleController

import py2flamingo.utils.calculations as calc
import py2flamingo.utils.file_handlers as txt
from py2flamingo.models.microscope import Position
from py2flamingo.services.communication.connection_manager import ConnectionManager


class MultiAngleController:
    """
    Controller to handle multi-angle image collection.
    """

    def __init__(
        self,
        microscope_controller: MicroscopeController,
        sample_controller: SampleController,
        connection_manager: ConnectionManager,
    ):
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
