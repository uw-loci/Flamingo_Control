"""Dialog windows for py2flamingo.

This package contains modal and non-modal dialog windows used throughout
the application.
"""

from py2flamingo.views.dialogs.led_2d_overview_dialog import LED2DOverviewDialog
from py2flamingo.views.dialogs.led_2d_overview_result import LED2DOverviewResultWindow
from py2flamingo.views.dialogs.tile_collection_dialog import TileCollectionDialog
from py2flamingo.views.dialogs.advanced_illumination_dialog import AdvancedIlluminationDialog
from py2flamingo.views.dialogs.advanced_camera_dialog import AdvancedCameraDialog
from py2flamingo.views.dialogs.advanced_save_dialog import AdvancedSaveDialog
from py2flamingo.views.dialogs.mip_overview_dialog import MIPOverviewDialog
from py2flamingo.views.dialogs.settings_dialog import SettingsDialog

__all__ = [
    'LED2DOverviewDialog',
    'LED2DOverviewResultWindow',
    'TileCollectionDialog',
    'AdvancedIlluminationDialog',
    'AdvancedCameraDialog',
    'AdvancedSaveDialog',
    'MIPOverviewDialog',
    'SettingsDialog',
]
