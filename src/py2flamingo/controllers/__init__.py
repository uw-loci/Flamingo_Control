# src/py2flamingo/controllers/__init__.py
"""
Controllers for Py2Flamingo MVC architecture.

This package contains all controller classes that handle business logic
and coordinate between models and views.
"""

from .microscope_controller import MicroscopeController
try:
    from .position_controller import PositionController
except Exception:
    PositionController = None

from .settings_controller import SettingsController
try:
    from .snapshot_controller import SnapshotController
except Exception:
    SnapshotController = None
try:
    from .sample_controller import SampleController
except Exception:
    SampleController = None
# from .ellipse_controller import EllipseController
# from .multi_angle_controller import MultiAngleController
# src/py2flamingo/controllers/__init__.py
"""
Controllers package for Py2Flamingo.

This package contains controller classes that handle business logic
and coordinate between models and views.
"""

# Import existing controllers or create stubs
try:
    from .microscope_controller import MicroscopeController
except ImportError:
    class MicroscopeController:
        def __init__(self, *args, **kwargs):
            pass
        def disconnect(self):
            pass

# Create stub controllers for missing ones
class PositionController:
    def __init__(self, *args, **kwargs):
        pass

class SettingsController:
    def __init__(self, *args, **kwargs):
        pass

class SnapshotController:
    def __init__(self, *args, **kwargs):
        pass

class SampleController:
    def __init__(self, *args, **kwargs):
        pass

class EllipseController:
    def __init__(self, *args, **kwargs):
        pass

class MultiAngleController:
    def __init__(self, *args, **kwargs):
        pass

__all__ = [
    'MicroscopeController',
    'PositionController',
    'SettingsController',
    'SnapshotController',
    'SampleController',
    'EllipseController',
    'MultiAngleController',
]
