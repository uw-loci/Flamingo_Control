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

__all__ = [
    'WorkflowService',
    'SampleSearchService',
    'EllipseTracingService'
]
