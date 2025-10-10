# ============================================================================
# src/py2flamingo/controllers/__init__.py
"""
Controllers for Py2Flamingo MVC architecture.

This package contains controller classes that orchestrate interactions
between the UI layer and service/model layers.
"""

# MVC Refactoring - New Controllers (use these for new MVC architecture)
# Import these first as they have no legacy dependencies
from .connection_controller import ConnectionController
from .workflow_controller import WorkflowController

# Legacy controllers (existing functionality) - import with try/except
try:
    from .microscope_controller import MicroscopeController
except Exception:
    MicroscopeController = None

try:
    from .position_controller import PositionController
except Exception:
    PositionController = None

try:
    from .settings_controller import SettingsController
except Exception:
    SettingsController = None

try:
    from .snapshot_controller import SnapshotController
except Exception:
    SnapshotController = None

try:
    from .sample_controller import SampleController
except Exception:
    SampleController = None

__all__ = [
    # MVC Controllers (new architecture - always available)
    'ConnectionController',
    'WorkflowController',
    # Legacy controllers (may be None if imports fail)
    'MicroscopeController',
    'PositionController',
    'SettingsController',
    'SnapshotController',
    'SampleController',
]
