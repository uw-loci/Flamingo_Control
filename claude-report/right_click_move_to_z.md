# Right-Click to Move to Center Z Feature

## Overview
Added the ability to right-click on any tile in the LED 2D Overview result window to move the stage to the center Z position of that tile's bounding box.

## User Interface
- **Hint text** displayed below each panel title: "Right-click tile to move to center Z"
- **Info bar** at bottom updates to show: "Moving to Z=X.XXX mm (tile X,Y)"

## Implementation Details

### Signal Flow
```
ZoomableImageLabel.tile_right_clicked(tile_x_idx, tile_y_idx)
    ↓
ImagePanel.tile_right_clicked(tile_x_idx, tile_y_idx)
    ↓
LED2DOverviewResultWindow._on_tile_right_clicked(tile_x_idx, tile_y_idx, panel)
    ↓
movement_controller.position_controller.move_z(z_center)
```

### Files Modified

**`src/py2flamingo/views/dialogs/led_2d_overview_result.py`**

#### ZoomableImageLabel (lines 24-195)
```python
# New signal
tile_right_clicked = pyqtSignal(int, int)

# In mouseReleaseEvent - handle right button
elif event.button() == Qt.RightButton:
    if self._tiles_x > 0 and self._tiles_y > 0:
        self._handle_tile_right_click(event.pos())
    event.accept()

# New method to calculate tile index and emit signal
def _handle_tile_right_click(self, pos: QPoint):
    # Same tile index calculation as left-click
    # Emits tile_right_clicked(tile_x_idx, tile_y_idx)
```

#### ImagePanel (lines 240-460)
```python
# New signal
tile_right_clicked = pyqtSignal(int, int)

# In _setup_ui - connect to ZoomableImageLabel
self.image_label.tile_right_clicked.connect(self._on_tile_right_clicked)

# New hint label
self.hint_label = QLabel("Right-click tile to move to center Z")
self.hint_label.setStyleSheet("color: #888; font-size: 9pt; font-style: italic;")

# New handler - propagate signal
def _on_tile_right_clicked(self, tile_x_idx: int, tile_y_idx: int):
    self.tile_right_clicked.emit(tile_x_idx, tile_y_idx)
```

#### LED2DOverviewResultWindow (lines 700+)
```python
# In _setup_ui - connect panel signals with lambda to track which panel
self.left_panel.tile_right_clicked.connect(
    lambda x, y: self._on_tile_right_clicked(x, y, panel='left')
)
self.right_panel.tile_right_clicked.connect(
    lambda x, y: self._on_tile_right_clicked(x, y, panel='right')
)

# New handler method
def _on_tile_right_clicked(self, tile_x_idx: int, tile_y_idx: int, panel: str):
    """Handle tile right-click - move stage to center Z of tile."""

    # 1. Get the correct panel
    image_panel = self.left_panel if panel == 'left' else self.right_panel

    # 2. Find the TileResult matching (tile_x_idx, tile_y_idx)
    for tile in image_panel._tile_results:
        if tile.tile_x == tile_x_idx and tile.tile_y == tile_y_idx:
            target_tile = tile
            break

    # 3. Calculate center Z from bounding box
    bbox = target_tile.effective_bounding_box
    z_center = (bbox.z_min + bbox.z_max) / 2

    # 4. Move stage
    self._app.movement_controller.position_controller.move_z(z_center)
```

### Center Z Calculation
The center Z is calculated from the tile's **effective bounding box**:
```python
z_center = (bbox.z_min + bbox.z_max) / 2
```

The effective bounding box accounts for:
- Original tile Z range from LED 2D Overview scan
- Any rotation transformations applied to the bounding box

### Error Handling
The handler shows warnings for:
- **Tile Not Found**: If the clicked tile index doesn't match any TileResult
- **No Z Data**: If the tile has no effective_bounding_box
- **Not Connected**: If movement_controller is not available

### Dependencies
- `movement_controller.position_controller.move_z(z_mm)` - Stage movement
- `TileResult.effective_bounding_box` - Contains z_min, z_max
- `ImagePanel._tile_results` - List of TileResult objects for the panel

## Testing
1. Open LED 2D Overview result window with scan data
2. Right-click on any tile
3. Stage should move to center Z of that tile
4. Info bar should update with movement details
