# Claude Report: Napari Icon Removal from Dialogs

**Date:** 2026-01-28

## Issue

The napari logo appeared in the title bar of most dialog windows, confusing users who thought the application was a napari plugin. The icon was being inherited from napari when it was imported/initialized for the 3D visualization.

## Solution

Added `self.setWindowIcon(QIcon())` to dialog constructors to clear the inherited napari icon. The Sample View window (which actually uses napari) retains the napari icon since it's appropriate there.

## Files Modified

| File | Dialog Name |
|------|-------------|
| `views/dialogs/led_2d_overview_dialog.py` | LED 2D Overview |
| `views/dialogs/led_2d_overview_result.py` | LED 2D Overview - Results |
| `views/dialogs/tile_collection_dialog.py` | Collect Tiles - Workflow Configuration |
| `views/dialogs/overview_thresholder_dialog.py` | 2D Overview Tile Thresholder |
| `views/dialogs/mip_overview_dialog.py` | MIP Overview |
| `views/dialogs/advanced_illumination_dialog.py` | Advanced Illumination Settings |
| `views/dialogs/advanced_save_dialog.py` | Advanced Save Settings |
| `views/dialogs/advanced_camera_dialog.py` | Advanced Camera Settings |

## Implementation

Each dialog received two changes:

1. **Import addition:**
   ```python
   from PyQt5.QtGui import QIcon  # Added to existing QtGui import
   ```

2. **Icon clearing in constructor:**
   ```python
   self.setWindowTitle("Dialog Name")
   self.setWindowIcon(QIcon())  # Clear inherited napari icon
   ```

## Result

- Dialog windows now show the default system icon (or no icon) instead of the napari logo
- Sample View retains the napari icon since it genuinely uses napari for 3D visualization
- Users are no longer confused about whether the application is a napari plugin
