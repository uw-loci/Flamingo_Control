# Flamingo 3D Visualization — Coordinate System & Display Pipeline

## Physical Setup

### Sample Chamber (Fixed Container)
- Like a **glass filled with media**
- Fixed position in space
- Dimensions defined by stage limits:
  - X: 1.0 - 12.31 mm (width, left-right)
  - Y: 5.0 - 25.0 mm (height, up-down)
  - Z: 12.5 - 26.0 mm (depth, detection axis)

### Z Axis Orientation
- **Smaller Z** = closer to the detection objective (back wall of chamber in 3D viewer)
- **Larger Z** = farther from the detection objective (front of chamber in 3D viewer)
- The objective is painted on the back wall of the 3D visualization
- Stage +Z movement moves the sample AWAY from the objective

### Sample Holder (Movable Straw)
- Like a **straw dipped into the glass**
- Moves with the stage (X, Y, Z, R axes)
- Extends from top of chamber down to current Y position
- Has fine extension at the tip (~220 µm diameter)
- **Sample is glued to the tip of the extension**

### Focal Plane (FIXED HARDWARE - Critical Concept)

**The focal plane is a fixed physical location - it NEVER moves.**

In this light sheet microscope:
- The **focal plane** is where the light sheet intersects the detection objective's focal plane
- This is a **fixed location** in the imaging chamber - part of the hardware setup
- The focal plane is defined by a specific **Z position** (the detection/focus axis)
- X and Y coordinates define the field of view within that plane
- Field of view: ~518 µm (at 25.69x magnification)

### How Z-Stacks Work

**The stage moves the SAMPLE through the fixed focal plane:**

1. Stage moves to position (X, Y, Z)
2. The sample (attached to the holder) passes through the focal plane
3. Camera captures the slice of sample currently AT the focal plane
4. Stage moves to next Z position, capturing the next slice
5. Result: A 3D stack of 2D images, each captured at the same physical location (the focal plane)

**Important distinction:**
- The focal plane does NOT move
- The SAMPLE moves through the focal plane
- All imaging happens AT the focal plane

## Critical Concept: Data Placement in 3D Viewer

**All data is captured at the focal plane location, so it should be placed there in the 3D viewer.**

1. **During acquisition**: Every frame is captured at the focal plane (fixed location)
2. **Storage**: Data is placed at the focal plane location in napari coordinates (`base_y_um = 7000`)
3. **Z variation**: The Z coordinate varies to represent the sample's 3D structure (different slices captured as sample moves through focal plane)
4. **Display transform**: When stage moves after acquisition, the display transform shifts the volume to show relationship to current stage position
5. **Visual result**: Data appears at the yellow square (focal plane) and moves with the stage

## Coordinate Systems

### 1. Stage Coordinates (mm)
- Physical position of the motorized stage
- X, Y, Z: Linear position in millimeters
- R: Rotation angle in degrees
- **This is the authoritative position source**

### 2. World Coordinates (µm)
- Absolute 3D coordinate system for the chamber
- Origin and scale defined in `visualization_3d_config.yaml`
- All coordinates in micrometers
- **Array order is always (Z, Y, X)** — napari convention
- Used for voxel storage and rotation transformations

### 3. Napari Display Coordinates (voxels/pixels)
- Visualization coordinate system
- Origin at back-top-left of chamber
- Voxel size: 50 µm (configurable in `display.voxel_size_um`)
- Axes: (Z, Y, X) order (napari convention)

### 4. Camera Coordinates (pixels)
- 2D image from camera sensor
- Origin at camera center
- Pixel size: 6.5 µm at sensor, ~253 nm at sample (with 25.69x mag)
- Converted to world coordinates using stage position + rotation

## Coordinate Transformations

### Stage → World Coordinates (Data Placement)

**Key insight:** Stage coordinates are in real-world mm, but data is placed in napari's world coordinate system (µm). The transformation depends on `use_stage_y_delta`:

```python
# In add_frame_to_volume() - sample_view.py

# Stage position comes in as mm
pos_x, pos_y, pos_z = stage_position_mm['x'], stage_position_mm['y'], stage_position_mm['z']

# Reference position (first frame's stage position)
ref_x, ref_y, ref_z = reference_stage_position['x'], 'y'], ['z']

# Calculate delta from reference (in mm)
delta_x = pos_x - ref_x
delta_y = pos_y - ref_y
delta_z = pos_z - ref_z

# Base position in napari world coordinates (µm)
# sample_region_center_um = [6655, 7000, 19250]  # [X, Y, Z] in config
base_x_um = 6655   # X center of sample region
base_y_um = 7000   # Y position of focal plane in 3D viewer
base_z_um = 19250  # Z center of sample region

# Calculate world coordinates (ZYX order for napari)
# Y behavior depends on workflow mode:
y_offset = delta_y * 1000 if use_stage_y_delta else 0  # 0 for live view and tiles

world_center_um = [
    base_z_um + delta_z * 1000,        # Z: varies with stage Z (same orientation)
    base_y_um + y_offset,               # Y: fixed at focal plane (7000µm)
    base_x_um + delta_x_storage * 1000  # X: varies with stage X
]
```

**Result with `use_stage_y_delta=False` (live view and tile workflows):**
- Y is ALWAYS `base_y_um = 7000` (focal plane location)
- Z varies to show the sample's 3D structure
- X varies with stage X movement
- All data appears at the yellow square (focal plane) in the 3D viewer

### Camera Pixel → World Coordinates
```python
# Each camera pixel at (cam_x_px, cam_y_px):

# 1. Convert camera pixels to micrometers (offsets from frame center)
cam_x_um = (cam_x_px - width/2) * pixel_size_um
cam_y_um = (cam_y_px - height/2) * pixel_size_um

# 2. Add to world_center to get final world position
world_coords_3d = [
    world_center_um[0] + z_offset,    # Z
    world_center_um[1] + cam_y_um,    # Y
    world_center_um[2] + cam_x_um     # X
]
```

### World → Napari Voxel Coordinates
```python
# World coordinates (µm) to voxel indices
# voxel_size = 15 µm (display resolution)

voxel_z = world_z_um / voxel_size
voxel_y = world_y_um / voxel_size
voxel_x = world_x_um / voxel_size
```

### Display Transform (After Acquisition)
```python
# When stage moves after data is captured, display transform shifts the volume
# In get_display_volume_transformed() - dual_resolution_storage.py

# Current stage position vs reference (where data was captured)
dx = current_stage_x - reference_x  # mm
dy = current_stage_y - reference_y  # mm
dz = current_stage_z - reference_z  # mm

# Convert to voxel offset
offset_voxels = [
    dz * 1000 / voxel_size,   # Z
    -dy * 1000 / voxel_size,  # Y (inverted)
    dx * 1000 / voxel_size    # X
]

# Shift the display volume by this offset
# Result: data appears to move with the stage
```

### World → Napari Coordinates
```python
# Physical ranges define the mapping
napari_x = (world_x_mm - x_min) / (x_max - x_min) * napari_width
napari_y = (world_y_mm - y_min) / (y_max - y_min) * napari_height
napari_z = (world_z_mm - z_min) / (z_max - z_min) * napari_depth
```

## Sample Region

The **sample region** defines where high-resolution data is stored (5 µm voxels vs 50 µm display):

**Current settings** (from `visualization_3d_config.yaml`):
- Center: (6655, 7000, 19250) µm
- Asymmetric half-widths: X ±6000, Y ±12000, Z ±7000 µm
- Fallback radius: 8000 µm (backward compatibility)

**Purpose**:
- Limits memory usage by only storing data where the sample can be
- Data outside this region is rejected (logged as warning)
- Should be large enough to cover all positions where you image the sample

## Sample Holder Visualization

The sample holder is a visual reference showing where the physical holder is positioned:

- **Top**: Always at Y=0 in napari (top of chamber)
- **Bottom**: Current stage Y position converted to napari coordinates
- **X, Z position**: Current stage X, Z position
- **Rotation indicator**: Shows current rotation angle

**Important**: The holder visualization is just a REFERENCE. The actual imaging data is separate and should appear near the holder tip (where the sample is glued).

---

# Display Pipeline — Complete Data Flow

## Architecture Overview

```
Camera Sensor (2048x2048 uint16)
       │
       ├──── LIVE VIEW PATH ──────────────────── TILE WORKFLOW PATH ────┐
       │                                                                │
   CameraController                                       CameraController
   get_latest_frame()                              _workflow_tile_mode = True
       │                                           _current_tile_position = {...}
       │                                                    │
  Sample3DVisualizationWindow                         SampleView
  _on_populate_tick() [10 Hz timer]              _on_tile_zstack_frame()
       │                                                    │
  _process_camera_frame_to_3d()                  _add_frame_to_3d_volume()
       │                                                    │
       ├─ Downsample 4x (512x512)                ├─ Threshold > 100
       ├─ Calc delta from reference               ├─ Absolute world coords
       ├─ Store at (base - delta)                 ├─ World = tile_center ± pixel_offset
       │                                                    │
       └──── DualResolutionVoxelStorage.update_storage() ───┘
                          │
              ┌───────────┴──────────┐
              │   Sparse Storage     │    5 µm voxels, dict-based
              │   (high-res)         │    Only stores non-zero pixels
              └───────────┬──────────┘
                          │ downsample_to_display()
              ┌───────────┴──────────┐
              │   Display Cache      │    15 µm voxels, dense numpy
              │   (low-res)          │    Full chamber extent
              └───────────┬──────────┘
                          │ get_display_volume_transformed()
                          │ (rotation + translation for live view)
                          │ (passthrough for tile workflow)
              ┌───────────┴──────────┐
              │   Napari Image Layer  │    One layer per channel
              │   channel_layers[id]  │    Additive blending, MIP rendering
              └──────────────────────┘
```

## Key Classes

| Class | File | Line | Purpose |
|-------|------|------|---------|
| `SampleView` | `views/sample_view.py` | 664 | Bridge: camera/controllers → 3D visualization |
| `Sample3DVisualizationWindow` | `views/sample_3d_visualization_window.py` | 42 | Napari 3D viewer, live view capture, channel layers |
| `ViewerControlsDialog` | `views/sample_view.py` | 271 | Channel visibility/contrast UI (has `_get_channel_layer()`) |
| `DualResolutionVoxelStorage` | `visualization/dual_resolution_storage.py` | 69 | Dual-res sparse/dense voxel storage |
| `CoordinateTransformer` | `visualization/coordinate_transforms.py` | 15 | Rotation matrices, camera→world transforms |
| `PhysicalToNapariMapper` | `visualization/coordinate_transforms.py` | 419 | Physical mm ↔ napari voxel mapping |

## Dual Resolution Storage

`DualResolutionVoxelStorage` manages two layers of data:

### High-Resolution Storage (5 µm voxels)
- **Format**: Python dictionaries (sparse) — `{(z, y, x): value}`
- **Centered at**: `sample_region_center_um` from config
- **Bounded by**: `sample_region_half_width_{x,y,z}_um`
- **Per channel**: Separate `storage_data[ch_id]`, `storage_timestamps[ch_id]`, `storage_confidence[ch_id]`
- Memory proportional to actual data, not chamber volume

### Low-Resolution Display Cache (15 µm voxels)
- **Format**: Dense numpy arrays `(Z, Y, X)` uint16
- **Covers**: Full chamber extent (from `chamber_origin` to `chamber_dimensions`)
- **Per channel**: `display_cache[ch_id]` — one dense array each
- Rebuilt via `downsample_to_display()` when storage is dirty

### Key Methods

```python
# dual_resolution_storage.py

def update_storage(channel_id, world_coords, pixel_values, timestamp, update_mode):
    """Add voxels to sparse high-res storage.
    Args:
        channel_id:  0-3 (405nm, 488nm, 561nm, 640nm)
        world_coords: (N, 3) array in µm, (Z, Y, X) order
        pixel_values: (N,) uint16 intensities
        update_mode:  'latest' | 'maximum' | 'average' | 'additive'
    """  # Line 178

def downsample_to_display(channel_id, force=False):
    """Downsample sparse high-res → dense low-res display cache.
    Returns: (Z, Y, X) uint16 array"""  # Line 290

def get_display_volume(channel_id):
    """Get display-resolution volume (triggers downsample if dirty)."""  # Line 457

def get_display_volume_transformed(channel_id, current_stage_pos, holder_position_voxels):
    """Get display volume with rotation + translation transforms.
    - If reference_stage_position is None → returns untransformed (tile mode)
    - Otherwise → applies rotation around holder + translation delta
    Args:
        current_stage_pos: {'x', 'y', 'z', 'r'} in mm/degrees
    """  # Line 551

def set_reference_position(stage_pos):
    """Set reference stage position for delta calculations.
    Called once on first live-view frame."""  # Line 709

def invalidate_transform_cache():
    """Clear cached rotated volumes. Call after reference changes."""  # Line 730

def world_to_storage_voxel(world_coords):
    """(N,3) µm → (N,3) storage voxel indices. Centered at sample_region_center."""  # Line 145

def world_to_display_voxel(world_coords):
    """(N,3) µm → (N,3) display voxel indices. Offset from chamber_origin."""  # Line 172
```

---

## Channel Management

### Channel Configuration

Defined in `src/py2flamingo/configs/visualization_3d_config.yaml`:

| Channel ID | Name | Colormap | Laser |
|------------|------|----------|-------|
| 0 | `405nm (DAPI)` | blue | Laser 1 |
| 1 | `488nm (GFP)` | green | Laser 2 |
| 2 | `561nm (RFP)` | red | Laser 3 |
| 3 | `640nm (Far-Red)` | magenta | Laser 4 |

### Napari Layer Creation

Layers are created in `Sample3DVisualizationWindow._setup_data_layers()` (line 1853):

```python
for ch_config in self.config['channels']:
    ch_id = ch_config['id']
    ch_name = ch_config['name']  # e.g., "488nm (GFP)"

    empty_volume = np.zeros(self.voxel_storage.display_dims, dtype=np.uint16)

    layer = self.viewer.add_image(
        empty_volume,
        name=ch_name,              # Layer name from config, NOT "Channel N"
        colormap=ch_config['default_colormap'],
        visible=ch_config.get('default_visible', True),
        blending='additive',
        opacity=0.8,
        rendering='mip',
        contrast_limits=(0, 50)
    )

    self.channel_layers[ch_id] = layer  # Dict: int → napari.layers.Image
```

**Critical**: Layer names come from config (`"488nm (GFP)"`), NOT from `f"Channel {N}"`. Always use `channel_layers[ch_id]` to access layers — never look up by string name.

### Channel Visibility & Contrast (SampleView)

`SampleView` controls channels via `sample_3d_window.channel_layers`:

```python
# sample_view.py — SampleView class

def _on_channel_visibility_changed(self, channel: int, state: int):
    """Toggle napari layer visibility."""  # Line 1946
    visible = (state == Qt.Checked)
    self._channel_states[channel]['visible'] = visible
    if self.sample_3d_window and hasattr(self.sample_3d_window, 'channel_layers'):
        layer = self.sample_3d_window.channel_layers.get(channel)
        if layer is not None:
            layer.visible = visible

def _on_channel_contrast_changed(self, channel: int, value: tuple):
    """Update napari layer contrast limits."""  # Line 1957
    min_val, max_val = value
    if self.sample_3d_window and hasattr(self.sample_3d_window, 'channel_layers'):
        layer = self.sample_3d_window.channel_layers.get(channel)
        if layer is not None:
            layer.contrast_limits = [min_val, max_val]
```

**Note**: `ViewerControlsDialog` (line 271, same file) has a helper `_get_channel_layer(channel_id)` but that method is on `ViewerControlsDialog`, NOT on `SampleView`. Do not call it from `SampleView`.

### Channel Detection (Live View)

`Sample3DVisualizationWindow._detect_active_channel()` (line 3298) determines which laser is active by querying the laser/LED controller. Returns 0-3 for laser channels, `None` for LED/brightfield.

---

## Live View Data Path (Detailed)

### 1. Frame Capture Timer

`Sample3DVisualizationWindow` has a `populate_timer` (100ms interval, 10 Hz):

```python
# sample_3d_visualization_window.py
self.populate_timer = QTimer()
self.populate_timer.timeout.connect(self._on_populate_tick)
self.populate_timer.setInterval(100)  # 10 Hz
```

### 2. Frame Processing (`_on_populate_tick`, line 3001)

Each tick:
1. Check `is_populating` and live view active
2. Get current stage position from `movement_controller`
3. Get latest camera frame via `camera_controller.get_latest_frame()`
4. Skip duplicate frames (check `local_frame_num`)
5. Detect active channel (`_detect_active_channel()`)
6. If stage is moving → buffer frame; if stationary → process immediately

### 3. Camera-to-World Transform (`_process_camera_frame_to_3d`, line 3101)

```python
# Step 1: Downsample 4x (2048x2048 → 512x512)
downsampled = self._downsample_for_storage(image)

# Step 2: Create pixel coordinate grid
y_indices, x_indices = np.meshgrid(np.arange(H), np.arange(W), indexing='ij')

# Step 3: Convert to physical µm
FOV_mm = 0.5182  # Fixed for 25.69× magnification
pixel_size_um = FOV_mm * 1000 / W
camera_x = (x_indices - W/2) * pixel_size_um
camera_y = (y_indices - H/2) * pixel_size_um

# Step 4: NO rotation for placement (camera/objective don't rotate)
self.transformer.set_rotation(rx=0, ry=0, rz=0)

# Step 5: Calculate storage position RELATIVE TO REFERENCE
# Reference is set on first frame
delta_x = position.x - ref_x
delta_y = position.y - ref_y
delta_z = position.z - ref_z
# Storage position = base_center - delta  (in sample coordinates)
world_center_um = [base_z - delta_z*1000, base_y - delta_y*1000, base_x - delta_x*1000]

# Step 6: Build (Z, Y, X) world coordinate array
world_coords_3d = camera_offsets_3d + world_center_um

# Step 7: Set reference on first frame
if self.voxel_storage.reference_stage_position is None:
    self.voxel_storage.set_reference_position({'x': pos.x, 'y': pos.y, 'z': pos.z, 'r': pos.r})

# Step 8: Store
self.voxel_storage.update_storage(channel_id, world_coords_3d, pixel_values, ...)
```

### 4. reference_stage_position — How It Works

| Mode | reference_stage_position | Data Placement | Display Transform |
|------|--------------------------|----------------|-------------------|
| **Live View** | Set on first frame | Relative to `sample_region_center - (current - reference)` | `get_display_volume_transformed()` applies rotation + translation delta |
| **Tile Workflow** | Cleared to `None` | Absolute world coordinates (`tile_x * 1000 + pixel_offset`) | `get_display_volume_transformed()` returns untransformed volume |

**Why this matters**: If live view sets `reference_stage_position` and then tile collection starts without clearing it, `get_display_volume_transformed()` will shift all tile data by `(current_stage - stale_reference)`, corrupting positions.

**Fix**: `prepare_for_tile_workflows()` clears `reference_stage_position = None` and `invalidate_transform_cache()`.

---

## Tile Workflow Data Path (Detailed)

### 1. Preparation (`SampleView.prepare_for_tile_workflows`, line 2615)

Called from `TileCollectionDialog._setup_sample_view_integration()` before execution:

```python
def prepare_for_tile_workflows(self, tile_info: list):
    """Args: tile_info: [{x, y, z_min, z_max, filename}, ...]"""
    self._tile_workflow_active = True
    self._expected_tiles = tile_info
    self._accumulated_zstacks = {}

    # CRITICAL: Clear reference so transform returns untransformed volumes
    if self.voxel_storage:
        self.voxel_storage.reference_stage_position = None
        self.voxel_storage.invalidate_transform_cache()

    # Cache pixel FOV (avoid TCP calls per frame)
    self._cached_pixel_size_mm = camera_service.get_pixel_field_of_view()
    self._tile_xy_cache = {}  # Pre-computed XY coords per tile
```

### 2. Frame Arrival (`SampleView._on_tile_zstack_frame`, line 2644)

Called by `CameraController` during tile workflow acquisition:

```python
def _on_tile_zstack_frame(self, image, position, z_index, frame_num):
    """
    Args:
        image: (H, W) uint16
        position: {'x', 'y', 'z_min', 'z_max', 'channels', 'filename'}
        z_index: Z-plane index (0-based, across ALL channels)
        frame_num: Global frame number
    """
    # Determine which channel this frame belongs to
    channels = position.get('channels', [0])     # e.g., [1, 3] for 488nm + 640nm
    num_channels = len(channels)
    estimated_planes_per_channel = max(1, int(z_range / 0.0025) + 1)

    channel_idx = min(z_index // estimated_planes_per_channel, num_channels - 1)
    self._current_channel = channels[channel_idx]  # e.g., 1 for 488nm

    z_within_channel = z_index % estimated_planes_per_channel
    z_position_mm = z_min + (z_within_channel / max(1, planes-1)) * z_range

    self._add_frame_to_3d_volume(image, position, z_position_mm)
```

### 3. World Coordinate Calculation (`_add_frame_to_3d_volume`, line 2687)

Tile data uses **absolute world coordinates** — no reference position involved:

```python
def _add_frame_to_3d_volume(self, image, position, z_mm):
    pixel_size_um = self._cached_pixel_size_mm * 1000
    tile_x_um = position['x'] * 1000  # Absolute tile center in µm
    tile_y_um = position['y'] * 1000
    tile_z_um = z_mm * 1000

    # Cache XY world coordinates per tile (reuse across Z-planes)
    h, w = image.shape[:2]
    x_world = tile_x_um + (x_indices - w/2) * pixel_size_um  # Per-pixel X
    y_world = tile_y_um + (y_indices - h/2) * pixel_size_um  # Per-pixel Y

    # Threshold: only store pixels > 100
    mask = image.ravel() > 100

    # Build (Z, Y, X) coordinate array for masked pixels
    world_coords = np.column_stack([
        np.full(mask.sum(), tile_z_um),   # Z: same for all pixels in frame
        y_world[mask],                     # Y: tile center + pixel offset
        x_world[mask]                      # X: tile center + pixel offset
    ])

    self.voxel_storage.update_storage(
        channel_id=self._current_channel,
        world_coords=world_coords,
        pixel_values=pixel_values[mask],
        update_mode='maximum'
    )
```

### 4. Coordinate Flow Summary

```
Tile from TileCollectionDialog:
  position = {x: 10.576, y: 13.945}  (stage mm)
  z_range = [21.5, 22.0] mm
      │
      ▼
For each Z-plane at z_mm:
  tile_x_um = 10576.0     tile_y_um = 13945.0     tile_z_um = z_mm * 1000
      │
      ▼
For each camera pixel (px, py) in 2048x2048:
  X_world = 10576.0 + (px - 1024) * 0.253  µm
  Y_world = 13945.0 + (py - 1024) * 0.253  µm
  Z_world = tile_z_um                        µm
      │
      ▼
world_coords array (Z, Y, X) in µm
      │
      ▼
DualResolutionVoxelStorage.update_storage()
  → world_to_storage_voxel(): subtract storage_origin, divide by 5µm
  → Filter: keep voxels within storage bounds
  → Store in sparse dict: storage_data[channel][(z_vox, y_vox, x_vox)] = value
      │
      ▼
downsample_to_display()
  → Map storage voxels to display voxels (÷3 ratio)
  → Write into dense display_cache[channel] array
      │
      ▼
get_display_volume_transformed()
  → reference_stage_position is None → return untransformed volume
      │
      ▼
channel_layers[channel_id].data = volume
channel_layers[channel_id].refresh()
```

---

## Visualization Update Cycle

### Update Timer

`Sample3DVisualizationWindow` has an `update_timer` (100ms, 10 Hz) that calls `_update_visualization()` (line 2422):

```python
def _update_visualization(self):
    """Update napari layers with latest transformed data."""
    if not self.update_mutex.tryLock():
        return  # Skip if already updating
    try:
        for ch_id in range(4):
            if ch_id in self.channel_layers:
                volume = self.voxel_storage.get_display_volume_transformed(
                    ch_id, self.last_stage_position, holder_pos_voxels
                )
                self.channel_layers[ch_id].data = volume
                self._update_contrast_slider_range(ch_id)
    finally:
        self.update_mutex.unlock()
```

### Stage Position Updates

When stage moves, `_handle_stage_update_threadsafe()` (line 2473) receives position, then `_process_pending_stage_update()` (line 2487) updates layers with the new transform.

---

## Troubleshooting

### Data not appearing in napari

1. **Check coordinate transformation**: Look for "World coordinate ranges" in debug logs
2. **Check voxel rejection**: Look for `"All voxels rejected"` warnings — means world coordinates are outside sample region
3. **Verify sample region**: Ensure imaging positions are within `sample_region_center ± sample_region_half_widths`
4. **Check channel visibility**: Ensure the active laser's channel is visible in napari
5. **Check contrast limits**: Data may be present but contrast range too narrow to see
6. **Check reference_stage_position**: If stale from a prior live-view session, tile data will be shifted. `prepare_for_tile_workflows()` should clear it.

### Tile data at wrong position

- Verify `reference_stage_position` is `None` during tile workflows
- Check `_cached_pixel_size_mm` is valid (not 0 or None)
- Look for "Added N voxels at tile (X, Y)" debug logs
- Check that Z position calculation in `_on_tile_zstack_frame` is correct

### Channel toggle not working

- **Never use string names** like `"Channel 1"` to find layers — layers are named from config (e.g., `"488nm (GFP)"`)
- Always use `sample_3d_window.channel_layers[channel_id]` to get the napari layer
- `ViewerControlsDialog._get_channel_layer()` only works within that class, not from `SampleView`

### Sample holder in wrong position

- Holder Y should span from napari Y=0 (top) to current stage Y
- Holder X, Z should match current stage X, Z
- If holder appears misaligned, check `PhysicalToNapariMapper` initialization

### Progress dialog stuck

- `QProgressDialog.exec_()` does NOT auto-close when `setValue` reaches max
- Must call `progress.accept()` (success) or `progress.reject()` (cancel) to close

---

## Function Reference

### SampleView (`views/sample_view.py`, line 664)

| Method | Line | Purpose |
|--------|------|---------|
| `prepare_for_tile_workflows(tile_info)` | 2615 | Clear reference, setup tile capture state |
| `_on_tile_zstack_frame(image, position, z_index, frame_num)` | 2644 | Route incoming tile frame to correct channel + Z |
| `_add_frame_to_3d_volume(image, position, z_mm)` | 2687 | Convert frame to world coords, store in voxel storage |
| `_on_channel_visibility_changed(channel, state)` | 1946 | Toggle napari layer visibility |
| `_on_channel_contrast_changed(channel, value)` | 1957 | Update napari layer contrast limits |
| `_update_channel_availability()` | 1981 | Enable/disable checkboxes based on data presence |
| `update_workflow_progress(status, pct, eta)` | — | Update progress bar in Sample View |

### Sample3DVisualizationWindow (`views/sample_3d_visualization_window.py`, line 42)

| Method | Line | Purpose |
|--------|------|---------|
| `_setup_data_layers()` | 1853 | Create napari Image layers from config (populates `channel_layers`) |
| `_init_storage_with_mapper()` | 337 | Initialize `DualResolutionVoxelStorage` with coordinate mapper |
| `_on_populate_tick()` | 3001 | 10 Hz timer: capture camera frame for live view |
| `_process_camera_frame_to_3d(image, header, ch_id, position)` | 3101 | Transform camera frame → world coords → storage |
| `_detect_active_channel()` | 3298 | Query laser controller for active channel (0-3 or None) |
| `_downsample_for_storage(image)` | 3340 | 4x downsample camera image |
| `_update_visualization()` | 2422 | 10 Hz timer: refresh napari layers from storage |
| `_handle_stage_update_threadsafe(position)` | 2473 | Thread-safe stage position update |
| `_on_channel_visibility_changed(ch_id, visible)` | 2284 | Toggle channel layer visibility |

### DualResolutionVoxelStorage (`visualization/dual_resolution_storage.py`, line 69)

| Method | Line | Purpose |
|--------|------|---------|
| `update_storage(ch_id, world_coords, pixel_values, timestamp, mode)` | 178 | Write voxels to sparse high-res storage |
| `downsample_to_display(ch_id, force)` | 290 | Sparse → dense display cache |
| `get_display_volume(ch_id)` | 457 | Get display array (triggers downsample if dirty) |
| `get_display_volume_transformed(ch_id, stage_pos, holder_voxels)` | 551 | Apply rotation + translation for display |
| `set_reference_position(stage_pos_dict)` | 709 | Set reference for delta calculations |
| `invalidate_transform_cache()` | 730 | Clear cached rotated volumes |
| `world_to_storage_voxel(world_coords)` | 145 | µm → storage voxel indices |
| `world_to_display_voxel(world_coords)` | 172 | µm → display voxel indices |
| `get_memory_usage()` | 489 | Storage/display/total MB stats |

### CoordinateTransformer (`visualization/coordinate_transforms.py`, line 15)

| Method | Line | Purpose |
|--------|------|---------|
| `set_rotation(rx, ry, rz)` | 34 | Set rotation angles (degrees) |
| `camera_to_world(camera_coords, z_pos)` | 51 | 2D camera + Z → 3D world |
| `world_to_camera(world_coords)` | 85 | 3D world → 2D camera + Z |
| `transform_voxel_volume_affine(volume, offset, rotation, center, voxel_size)` | 308 | Rotate entire volume with padding |

### PhysicalToNapariMapper (`visualization/coordinate_transforms.py`, line 419)

| Method | Line | Purpose |
|--------|------|---------|
| `physical_to_napari(x_mm, y_mm, z_mm)` | 497 | Stage mm → napari voxels |
| `napari_to_physical(x_vox, y_vox, z_vox)` | 535 | Napari voxels → stage mm |
| `validate_physical_position(x, y, z)` | 584 | Check bounds |

---

## Configuration Reference

All visualization parameters live in `src/py2flamingo/configs/visualization_3d_config.yaml`:

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| `display` | `voxel_size_um` | [50, 50, 50] | Display resolution |
| `display` | `downsample_factor` | 4 | Camera image downsampling |
| `storage` | `voxel_size_um` | [5, 5, 5] | High-res storage resolution |
| `storage` | `max_memory_mb` | 2000 | Memory limit |
| `sample_chamber` | `sample_region_center_um` | [6655, 7000, 19250] | Center of storage region |
| `sample_chamber` | `sample_region_half_width_x_um` | 6000 | ±X storage extent |
| `sample_chamber` | `sample_region_half_width_y_um` | 12000 | ±Y storage extent |
| `sample_chamber` | `sample_region_half_width_z_um` | 7000 | ±Z storage extent |
| `stage_control` | `x_range_mm` | [1.0, 12.31] | Visualization X bounds |
| `stage_control` | `y_range_mm` | [0.0, 14.0] | Visualization Y bounds |
| `stage_control` | `z_range_mm` | [12.5, 26.0] | Visualization Z bounds |
| `channels` | (list of 4) | 405/488/561/640nm | Channel names, colormaps, defaults |
