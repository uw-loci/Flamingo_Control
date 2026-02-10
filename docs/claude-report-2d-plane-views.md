# Claude Report: Enhanced 2D Plane Views for 3D Volume View

## Overview

The `SlicePlaneViewer` widgets (XZ, XY, YZ) in `sample_view.py` provide 2D Maximum Intensity Projection (MIP) views of the 3D volume data. These views offer quick navigation and overview of the sample while the full 3D napari viewer handles detailed visualization.

## Architecture

### Location
- **File**: `src/py2flamingo/views/sample_view.py`
- **Class**: `SlicePlaneViewer` (lines 59-627)
- **Parent**: `SampleView` contains three instances (`xz_plane_viewer`, `xy_plane_viewer`, `yz_plane_viewer`)

### Plane Configuration
| Plane | Horizontal Axis | Vertical Axis | Projection Axis | Dimensions |
|-------|-----------------|---------------|-----------------|------------|
| XZ    | X               | Z             | Y               | 180×220 px |
| XY    | X               | Y             | Z               | 130×240 px |
| YZ    | Z               | Y             | X               | 160×240 px |

## Features

### Pan/Zoom Interaction
- **Drag**: Click and drag to pan the view
- **Scroll**: Mousewheel to zoom in/out (0.5x to 10x range)
- **Zoom Center**: Zooms centered on cursor position
- **Reset**: `reset_view()` method resets to default state

### Multi-Channel Display
- Supports up to 4 channels with independent settings
- Per-channel settings:
  - Visibility (on/off)
  - Colormap (blue, cyan, green, red, magenta, yellow, gray)
  - Contrast limits (min, max)
- Additive RGB blending for overlapping channels
- Settings synchronized from napari layer properties

### Stage Navigation
- **Double-click**: Moves stage to clicked position
- **Target Marker**: Orange crosshair appears at target
- **Stale Marker**: Turns purple when stage reaches position (within 50µm)

### Overlays
| Overlay | Color | Description |
|---------|-------|-------------|
| Stage Position | White cross | Current sample holder position |
| Objective | Green circle | Fixed objective lens position |
| Target Marker | Orange/Purple | Navigation target (active/stale) |
| Viewing Frame | Cyan dashed | Current 3D view boundaries |
| Focal Plane | Cyan dashed line | Current Z position (XY plane only) |

### Coordinate Display
- **Axis Labels**: Min/max values shown at display edges (e.g., `X:1.0`, `X:12.3`)
- **Coordinate Readout**: Mouse position shown at top-right corner (e.g., `X:6.55 Z:18.32 mm`)
- Updates in real-time as mouse moves

## Key Methods

### Data Input
```python
set_mip_data(data: np.ndarray)           # Single-channel grayscale
set_multi_channel_mip(                    # Multi-channel with settings
    channel_mips: Dict[int, np.ndarray],
    channel_settings: Dict[int, dict]
)
```

### Overlay Control
```python
set_holder_position(h, v)        # Stage position
set_objective_position(h, v)     # Objective marker
set_target_position(h, v, active=True)  # Navigation target
set_target_stale()               # Mark target as reached
clear_target_position()          # Remove target
set_focal_plane_position(pos)    # Focal plane line
```

### Display Options
```python
set_show_axis_labels(show: bool)       # Toggle axis min/max labels
set_show_coordinate_readout(show: bool) # Toggle mouse position display
reset_view()                            # Reset pan/zoom
```

## Integration with ViewerControlsDialog

When channel settings change in the Viewer Controls dialog, the plane views update automatically:

1. `ViewerControlsDialog` emits `plane_views_update_requested` signal
2. Signal connected to `SampleView._update_plane_views()`
3. `_update_plane_views()` fetches per-channel data from `voxel_storage`
4. Channel settings read from napari layer properties
5. Calls `set_multi_channel_mip()` on each plane viewer

## Signals

```python
position_clicked = pyqtSignal(float, float)  # Emitted on double-click
```

Connected to `SampleView._on_plane_click(plane, h_coord, v_coord)` which triggers stage movement.

## Colormap Implementation

Colormaps are implemented as lambda functions that convert normalized intensity (0-1) to RGB:

```python
CHANNEL_COLORMAPS = {
    'blue': lambda v: np.stack([zeros, zeros, v], axis=-1),
    'cyan': lambda v: np.stack([zeros, v, v], axis=-1),
    'green': lambda v: np.stack([zeros, v, zeros], axis=-1),
    'red': lambda v: np.stack([v, zeros, zeros], axis=-1),
    'magenta': lambda v: np.stack([v, zeros, v], axis=-1),
    'yellow': lambda v: np.stack([v, v, zeros], axis=-1),
    'gray': lambda v: np.stack([v, v, v], axis=-1),
}
```

## Performance Considerations

- MIP projections computed from display-resolution voxel data (not full resolution)
- Display updates throttled during drag operations
- Coordinate display updates on mouse move (no throttling needed due to simple QPainter operations)
- Multi-channel blending uses numpy vectorized operations

## Border Colors

Border colors match napari 3D axis colors for visual consistency:
- X axis: Cyan (`#008B8B`)
- Y axis: Magenta (`#8B008B`)
- Z axis: Yellow/Olive (`#8B8B00`)

## Future Enhancements

Potential improvements:
- Ruler/measurement tool overlay
- Screenshot export for individual planes
- Configurable crosshair style for target marker
- Optional grid overlay
- Brightness/contrast adjustment within plane viewer
