# ============================================================================
# src/py2flamingo/services/__init__.py
"""
Services for Py2Flamingo.

This package contains service classes that provide specific functionality
such as communication, workflow management, and analysis algorithms.
"""


try:
    from .workflow_service import WorkflowService
except Exception:
    WorkflowService = None
try:
    from .sample_search_service import SampleSearchService
except Exception:
    SampleSearchService = None
try:
    from .ellipse_tracing_service import EllipseTracingService
except Exception:
    EllipseTracingService = None

# MVC Refactoring - New Services (use these for new MVC architecture)
# Import these first as they have no numpy/scipy dependencies
from .connection_service import MVCConnectionService
from .workflow_service import MVCWorkflowService
from .status_service import StatusService
from .configuration_manager import ConfigurationManager, MicroscopeConfiguration

# Legacy services (require numpy/scipy) - import with try/except
try:
    from .workflow_service import WorkflowService
except ImportError:
    WorkflowService = None

try:
    from .sample_search_service import SampleSearchService
except ImportError:
    SampleSearchService = None

try:
    from .ellipse_tracing_service import EllipseTracingService
except ImportError:
    EllipseTracingService = None

__all__ = [
    # MVC Services (new architecture - always available)
    'MVCConnectionService',
    'MVCWorkflowService',
    'StatusService',
    'ConfigurationManager',
    'MicroscopeConfiguration',
    # Legacy services (may be None if numpy/scipy not installed)
    'WorkflowService',
    'SampleSearchService',
    'EllipseTracingService',
]
