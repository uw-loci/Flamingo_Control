"""
Views package for PyQt5 UI components.

This package provides view components following the MVC pattern.
Views are responsible for displaying data and capturing user interactions.
"""

from .connection_view import ConnectionView
from .image_controls_window import ImageControlsWindow
from .sample_info_view import SampleInfoView
from .sample_view import SampleView
from .stage_control_view import StageControlView
from .workflow_view import WorkflowView

__all__ = [
    "ConnectionView",
    "WorkflowView",
    "SampleInfoView",
    "StageControlView",
    "ImageControlsWindow",
    "SampleView",
]
