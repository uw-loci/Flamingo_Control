# Flamingo 3D Visualization Coordinate System

## Physical Setup

### Sample Chamber (Fixed Container)
- Like a **glass filled with media**
- Fixed position in space
- Dimensions defined by stage limits:
  - X: 1.0 - 12.31 mm (width, left-right)
  - Y: 5.0 - 25.0 mm (height, up-down from objective)
  - Z: 12.5 - 26.0 mm (depth, toward objective)

### Sample Holder (Movable Straw)
- Like a **straw dipped into the glass**
- Moves with the stage (X, Y, Z, R axes)
- Extends from top of chamber down to current Y position
- Has fine extension at the tip (~220 µm diameter)
- **Sample is glued to the tip of the extension**

### Imaging Plane (Where Data is Captured)
- Determined by objective focus + Z position
- Camera always images perpendicular to the sample holder axis
- Field of view: ~518 µm (at 25.69x magnification)

## Critical Concept: Data Attachment

**The imaging data is PHYSICALLY ATTACHED to the sample holder**, not to the chamber:

1. **When capturing**: Image is taken at the focal plane (specific Z position)
2. **Data location**: Voxels are placed at the stage position (X, Y, Z, R) when captured
3. **When stage moves**: The captured data conceptually moves with it (because it's attached to the sample)
4. **3D reconstruction**: All captured slices must align in the sample's reference frame

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
- Used for voxel storage and rotation transformations

### 3. Napari Display Coordinates (voxels/pixels)
- Visualization coordinate system
- Origin at back-top-left of chamber
- Voxel size: 50 µm (configurable)
- Axes: (Z, Y, X) order (napari convention)

### 4. Camera Coordinates (pixels)
- 2D image from camera sensor
- Origin at camera center
- Pixel size: 6.5 µm at sensor, ~253 nm at sample (with 25.69x mag)
- Converted to world coordinates using stage position + rotation

## Coordinate Transformations

### Stage → World Coordinates
```python
# For imaging data captured at stage position (stage_x, stage_y, stage_z, stage_r)
# Each camera pixel at (cam_x_px, cam_y_px):

# 1. Convert camera pixels to micrometers (offsets from stage position)
cam_x_um = (cam_x_px - width/2) * voxel_size_um
cam_y_um = (cam_y_px - height/2) * voxel_size_um

# 2. Translate to world coordinates using stage position
world_x = cam_x_um + (stage_x * 1000)  # mm to µm
world_y = cam_y_um + (stage_y * 1000)
world_z = stage_z * 1000

# 3. Apply rotation around sample center
centered = [world_x, world_y, world_z] - sample_region_center
rotated = apply_rotation_matrix(centered, stage_r)
final_world = rotated + sample_region_center
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

**Current settings**:
- Center: (6655, 15000, 19250) µm = center of stage ranges
- Radius: 8000 µm = 8 mm sphere
- Covers: ±8mm from center in all directions

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

## Troubleshooting

### If data isn't appearing in napari:

1. **Check coordinate transformation**: Look for "World coordinate ranges" in debug logs
2. **Check voxel rejection**: Look for "All voxels rejected" warnings
3. **Verify sample region**: Ensure imaging positions are within `sample_region_center ± sample_region_radius`
4. **Check channel visibility**: Ensure the active laser's channel is visible in napari
5. **Check contrast limits**: Data may be present but contrast range too low to see

### If sample holder is in wrong position:

- Holder Y should span from napari Y=0 (top) to current stage Y
- Holder X, Z should match current stage X, Z
- If holder appears misaligned, check `PhysicalToNapariMapper` initialization
