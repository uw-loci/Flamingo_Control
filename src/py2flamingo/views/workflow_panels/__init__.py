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

from .camera_panel import CameraPanel
from .illumination_panel import IlluminationPanel
from .multiangle_panel import MultiAnglePanel
from .position_panel import PositionPanel
from .save_panel import SavePanel
from .tiling_panel import TilingPanel
from .timelapse_panel import TimeLapsePanel
from .zstack_panel import ZStackPanel

__all__ = [
    "PositionPanel",
    "IlluminationPanel",
    "CameraPanel",
    "SavePanel",
    "ZStackPanel",
    "TimeLapsePanel",
    "TilingPanel",
    "MultiAnglePanel",
]
