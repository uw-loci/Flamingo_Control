# ============================================================================
# src/py2flamingo/controllers/__init__.py
"""
Controllers for Py2Flamingo MVC architecture.

This package contains controller classes that orchestrate interactions
between the UI layer and service/model layers.
"""

from .connection_controller import ConnectionController
from .workflow_controller import WorkflowController

try:
    from .position_controller import PositionController
except Exception:
    PositionController = None

__all__ = [
    "ConnectionController",
    "WorkflowController",
    "PositionController",
]
