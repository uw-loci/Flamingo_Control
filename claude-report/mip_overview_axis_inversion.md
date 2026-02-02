# Claude Report: MIP Overview Axis Inversion Fix

**Date:** 2026-01-28

## Issue

MIP Overview was not correctly handling inverted stage axes when displaying tiles. The `invert_x` setting from `visualization_3d_config.yaml` was being ignored, causing tiles to be displayed with incorrect orientation.

## Solution

Added proper axis inversion support to MIP Overview, matching the behavior of LED 2D Overview.

## Files Modified

| File | Changes |
|------|---------|
| `models/mip_overview.py` | Added `invert_x` field to `MIPOverviewConfig`; added `load_invert_x_setting()` helper function |
| `views/dialogs/mip_overview_dialog.py` | Load `invert_x` from config; invert tile placement during stitching; pass to `set_tile_coordinates` |

## Implementation Details

### MIPOverviewConfig Changes

```python
@dataclass
class MIPOverviewConfig:
    # ... existing fields ...
    invert_x: bool = False  # NEW: Whether X-axis is inverted for display
```

### Config Loading

```python
def load_invert_x_setting() -> bool:
    """Load invert_x from visualization_3d_config.yaml."""
    # Reads stage_control.invert_x_default from config file
    # Default: False if config not found
```

### Tile Placement During Stitching

```python
# Calculate position (invert X if needed to match stage orientation)
if self._config.invert_x:
    inverted_x_idx = (tiles_x - 1) - tile.tile_x_idx
    x_pos = inverted_x_idx * tile_w
else:
    x_pos = tile.tile_x_idx * tile_w
```

## Config Setting

In `configs/visualization_3d_config.yaml`:
```yaml
stage_control:
  invert_x_default: true  # Low X stage values appear on RIGHT side
```

## Behavior

- When `invert_x=True`: Low X stage values (e.g., X=4mm) appear on the RIGHT side of the overview
- When `invert_x=False`: Low X stage values appear on the LEFT side (standard orientation)
- Saved sessions preserve their `invert_x` setting for consistent display
