"""
Views package for PyQt5 UI components.

This package provides view components following the MVC pattern.
Views are responsible for displaying data and capturing user interactions.
"""

from .connection_view import ConnectionView
from .workflow_view import WorkflowView

__all__ = [
    'ConnectionView',
    'WorkflowView',
]
