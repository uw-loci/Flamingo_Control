# ============================================================================
# src/py2flamingo/services/image_acquisition_service.py
"""
Service for acquiring images from the microscope.

This service provides high-level methods for various image acquisition modes
including snapshots, brightfield images, and z-stacks. It coordinates between
workflow creation, execution, and image retrieval following the MVC pattern.

Based on legacy code from:
- Flamingo_Control/oldcodereference/take_snapshot.py (lines 14-93)
- Flamingo_Control/oldcodereference/microscope_interactions.py acquire_brightfield_image() (lines 333-389)
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from py2flamingo.core.events import EventManager
from py2flamingo.core.queue_manager import QueueManager
from py2flamingo.models.microscope import Position
from py2flamingo.utils.file_handlers import (
    dict_comment,
    dict_save_directory,
    dict_to_workflow,
    workflow_to_dict,
)


class ImageAcquisitionService:
    """
    Service for acquiring images from the microscope.

    This service handles creating workflow configurations for different
    acquisition modes (snapshot, brightfield, z-stack) and executing them
    to retrieve image data.

    Attributes:
        workflow_execution_service: Service for executing workflows
        connection_service: Service for microscope communication
        queue_manager: Manager for data queues
        event_manager: Manager for synchronization events
        position_controller: Controller for stage positioning (optional)
        logger: Logger instance
    """

    # Default constants
    DEFAULT_FRAMERATE = 40.0032  # frames per second
    DEFAULT_PLANE_SPACING = 10  # microns
    DEFAULT_LASER_CHANNEL = "Laser 3 488 nm"
    DEFAULT_LASER_SETTING = "5.00 1"

    def __init__(
        self,
        workflow_execution_service: "WorkflowExecutionService",
        connection_service: "ConnectionService",
        queue_manager: QueueManager,
        event_manager: EventManager,
        position_controller: Optional["PositionController"] = None,
    ):
        """
        Initialize image acquisition service with dependency injection.

        Args:
            workflow_execution_service: WorkflowExecutionService instance
            connection_service: ConnectionService instance
            queue_manager: QueueManager instance for data flow
            event_manager: EventManager instance for synchronization
            position_controller: Optional PositionController for stage management
        """
        self.workflow_execution_service = workflow_execution_service
        self.connection_service = connection_service
        self.queue_manager = queue_manager
        self.event_manager = event_manager
        self.position_controller = position_controller
        self.logger = logging.getLogger(__name__)

        # Cache workflow base path
        self.workflow_dir = Path("workflows")
        self.workflow_dir.mkdir(parents=True, exist_ok=True)

    # ========================================================================
    # Private helper methods
    # ========================================================================
