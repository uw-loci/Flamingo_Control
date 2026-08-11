# ============================================================================
# src/py2flamingo/controllers/multi_angle_controller.py
"""
Controller for multi-angle data collection.
"""

import logging
from pathlib import Path
from typing import List, Optional

import numpy as np

import py2flamingo.functions.text_file_parsing as txt
import py2flamingo.utils.calculations as calc

from ..controllers.microscope_controller import MicroscopeController
from ..models.collection import CollectionParameters, MultiAngleCollection
from ..models.microscope import Position
from ..services.workflow_service import MVCWorkflowService, WorkflowService
from ..utils.workflow_parser import WorkflowTextFormatter


class MultiAngleController:
    """
    Controller for multi-angle collection workflows.

    Handles automated collection at multiple rotation angles.
    """

    def __init__(
        self,
        microscope_controller: MicroscopeController,
        workflow_service: WorkflowService,
        mvc_workflow_service: MVCWorkflowService,
        connection_service=None,
    ):
        """
        Initialize multi-angle controller.

        Args:
            microscope_controller: Main microscope controller
            workflow_service: Workflow service (for validation)
            mvc_workflow_service: MVCWorkflowService for sending workflows
            connection_service: Optional connection service for microscope queries
        """
        self.microscope = microscope_controller
        self.workflow_service = workflow_service
        self._mvc_workflow_service = mvc_workflow_service
        self._text_formatter = WorkflowTextFormatter()
        self.connection_service = connection_service
        self.logger = logging.getLogger(__name__)
        self._is_executing = False

        # Current collection
        self.current_collection: Optional[MultiAngleCollection] = None
