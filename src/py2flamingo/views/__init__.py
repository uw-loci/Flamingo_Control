"""
Views package for PyQt5 UI components.

This package provides view components following the MVC pattern.
Views are responsible for displaying data and capturing user interactions.
"""

from .connection_view import ConnectionView
from .workflow_view import WorkflowView
from .sample_info_view import SampleInfoView
from .stage_control_view import StageControlView

__all__ = [
    'ConnectionView',
    'WorkflowView',
    'SampleInfoView',
    'StageControlView',
]
