# controllers/sample_controller.py
"""
Controller for sample location and management operations.

This controller handles all business logic related to finding,
tracking, and managing samples within the microscope field of view.
"""

import logging
from threading import Thread
from typing import Callable, Optional, Tuple

from ..controllers.base_controller import BaseController
from ..models.microscope import MicroscopeState, Position
from ..models.sample import Sample, SampleBounds
from ..services.sample_search_service import SampleSearchService
from ..services.workflow_service import WorkflowService


class SampleController(BaseController):
    """
    Controller responsible for sample location and tracking operations.

    This controller orchestrates the sample finding process, including
    Y-axis scanning, Z-stack acquisition, and boundary detection.

    Attributes:
        microscope_controller: Reference to microscope controller
        search_service: Service for sample detection algorithms
        workflow_service: Service for workflow generation
        logger: Logger instance for this controller
    """

    def __init__(
        self,
        microscope_controller: "MicroscopeController",
        search_service: SampleSearchService,
        workflow_service: WorkflowService,
    ):
        """
        Initialize the sample controller.

        Args:
            microscope_controller: Controller for microscope operations
            search_service: Service implementing sample search algorithms
            workflow_service: Service for creating workflow configurations
        """
        super().__init__()
        self.microscope = microscope_controller
        self.search_service = search_service
        self.workflow_service = workflow_service
        self.logger = logging.getLogger(__name__)

        # Subscribe to microscope state changes
        self.microscope.subscribe(self._on_microscope_state_change)

    def _on_microscope_state_change(self, state: MicroscopeState) -> None:
        """
        Handle microscope state changes during sample location.

        Args:
            state: New microscope state
        """
        if state == MicroscopeState.ERROR:
            self.logger.error("Microscope error during sample location")
            # Handle error state
