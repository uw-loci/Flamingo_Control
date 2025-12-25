"""
Workflow panel components for the Flamingo Control GUI.

These panels provide UI components for building workflows:
- PositionPanel: Start/end position configuration
- IlluminationPanel: Multi-laser/LED settings with power control
- CameraPanel: Exposure, AOI, and capture settings
- SavePanel: Data save location and format configuration
- ZStackPanel: Z-stack specific settings with stack options
- TimeLapsePanel: Time-lapse duration and interval settings
- TilingPanel: Tile/mosaic acquisition settings
- MultiAnglePanel: Multi-angle/OPT acquisition settings
"""

from .position_panel import PositionPanel
from .illumination_panel import IlluminationPanel
from .camera_panel import CameraPanel
from .save_panel import SavePanel
from .zstack_panel import ZStackPanel
from .timelapse_panel import TimeLapsePanel
from .tiling_panel import TilingPanel
from .multiangle_panel import MultiAnglePanel

__all__ = [
    'PositionPanel',
    'IlluminationPanel',
    'CameraPanel',
    'SavePanel',
    'ZStackPanel',
    'TimeLapsePanel',
    'TilingPanel',
    'MultiAnglePanel',
]
