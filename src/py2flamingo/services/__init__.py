# ============================================================================
# src/py2flamingo/services/__init__.py
"""
Services for Py2Flamingo.

This package contains service classes that provide specific functionality
such as communication, workflow management, and analysis algorithms.
"""

from .acquisition_timing_service import AcquisitionTimingService
from .configuration_manager import ConfigurationManager, MicroscopeConfiguration

# MVC Refactoring - New Services (use these for new MVC architecture)
# Import these first as they have no numpy/scipy dependencies
from .connection_service import MVCConnectionService
from .image_acquisition_service import ImageAcquisitionService
from .initialization_service import InitializationData, MicroscopeInitializationService
from .status_indicator_service import GlobalStatus, StatusIndicatorService
from .status_service import StatusService
from .tiff_size_validator import (
    TIFF_4GB_LIMIT,
    TiffSizeEstimate,
    calculate_tiff_size,
    get_recommended_planes,
    parse_workflow_file,
    validate_workflow_params,
)
from .window_geometry_manager import (
    GeometryPersistenceMixin,
    PersistentDialog,
    PersistentWidget,
    WindowGeometryManager,
    set_default_geometry_manager,
)
from .workflow_execution_service import WorkflowExecutionService
from .workflow_queue_service import WorkflowQueueService
from .workflow_service import MVCWorkflowService

__all__ = [
    # MVC Services (new architecture - always available)
    "MVCConnectionService",
    "MVCWorkflowService",
    "StatusService",
    "StatusIndicatorService",
    "GlobalStatus",
    "ConfigurationManager",
    "MicroscopeConfiguration",
    "WindowGeometryManager",
    "GeometryPersistenceMixin",
    "PersistentDialog",
    "PersistentWidget",
    "set_default_geometry_manager",
    "WorkflowExecutionService",
    "MicroscopeInitializationService",
    "InitializationData",
    "ImageAcquisitionService",
    "AcquisitionTimingService",
    "WorkflowQueueService",
    # TIFF size validation
    "calculate_tiff_size",
    "validate_workflow_params",
    "parse_workflow_file",
    "get_recommended_planes",
    "TiffSizeEstimate",
    "TIFF_4GB_LIMIT",
]
