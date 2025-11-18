# 3D Sample Visualization - Usage Guide

## Overview

The 3D Sample Visualization window provides rotation-aware data accumulation with separate high-resolution storage (5 µm voxels) and low-resolution display (15 µm voxels). This ensures data quality is preserved during rotations while maintaining smooth real-time performance.

## Running the Standalone Test

### With Simulated Data

The test script includes simulated multi-channel microscopy data to demonstrate the visualization capabilities:

```bash
cd /home/msnelson/LSControl/Flamingo_Control
python test_3d_visualization.py
```

This will:
- Generate 4-channel synthetic microscopy data
- Simulate scanning through 20 Z-planes (0-5000 µm)
- Apply different rotation configurations (0°, 45°, combined rotations)
- Display the accumulated 3D volume in napari

### Controls in the Test Window

- **Channels Tab**: Toggle visibility, adjust colors, opacity, and update strategies for each channel
- **Rotation Tab**: Control sample rotation around X, Y, Z axes with sliders
- **Data Tab**: Monitor memory usage and resolution settings
- **Display Tab**: Toggle chamber wireframe, objective position, and rotation axes

## Using Real Data

To use real microscopy data instead of simulated data, modify the test script:

### Option 1: Load from TIFF Files

```python
import tifffile

def load_real_frame(filepath):
    """Load a multi-channel TIFF image."""
    # Load your multi-channel TIFF
    frame = tifffile.imread(filepath)

    # Ensure it's in the right format (H, W, C)
    if frame.ndim == 2:
        frame = frame[..., np.newaxis]  # Add channel dimension

    return frame.astype(np.uint16)

# In the test loop, replace simulate_frame_data() with:
frame = load_real_frame(f"path/to/your/data/z_{z_position:04d}.tif")
```

### Option 2: Connect to Live Flamingo TCP Stream

```python
from py2flamingo.tcp_client import TCPClient

# Create TCP connection
client = TCPClient("192.168.1.1", 53717)
client.connect()

# In your acquisition loop:
async def acquire_and_visualize():
    # Get current position and rotation from Flamingo
    position = await client.get_position()
    rotation = await client.get_rotation()

    # Acquire frame from camera
    frame_data = await client.acquire_frame()

    # Create metadata
    metadata = {
        'z_position': position['z'],
        'rotation': {'rx': rotation['rx'], 'ry': rotation['ry'], 'rz': rotation['rz']},
        'timestamp': time.time(),
        'pixel_to_micron': 0.65,  # Adjust based on your magnification
        'active_channels': [0, 1, 2, 3]
    }

    # Process in visualization
    window.process_frame(frame_data, metadata)
```

### Option 3: Load from HDF5 Dataset

```python
import h5py

def load_hdf5_frames(h5_path):
    """Load frames from HDF5 file."""
    with h5py.File(h5_path, 'r') as f:
        # Assuming structure: /frames/z_0000, /frames/z_0001, etc.
        for z_key in sorted(f['frames'].keys()):
            frame = f['frames'][z_key][:]
            z_position = f['metadata'][z_key]['z_position'][()]
            rotation = {
                'rx': f['metadata'][z_key]['rx'][()],
                'ry': f['metadata'][z_key]['ry'][()],
                'rz': f['metadata'][z_key]['rz'][()]
            }
            yield frame, z_position, rotation

# Use in test:
for frame, z_pos, rotation in load_hdf5_frames("your_data.h5"):
    metadata = {
        'z_position': z_pos,
        'rotation': rotation,
        'timestamp': time.time(),
        'pixel_to_micron': 0.65,
        'active_channels': [0, 1, 2, 3]
    }
    window.process_frame(frame, metadata)
```

## Configuration Customization

Edit `src/py2flamingo/configs/visualization_3d_config.yaml` to adjust:

- **Storage resolution**: `storage.voxel_size_um` (default: [5, 5, 5])
- **Display resolution**: `display.voxel_size_um` (default: [15, 15, 15])
- **Chamber dimensions**: `sample_chamber.inner_dimensions_mm`
- **Channel settings**: Colors, update strategies, visibility
- **Memory limits**: `storage.max_memory_mb`

## Integration with Main Application

From the Flamingo Control main window:

1. Start the application: `python -m py2flamingo`
2. Navigate to **View → 3D Sample Visualization** (or press **Ctrl+3**)
3. Connect to your microscope
4. Start streaming to see real-time 3D accumulation

## Performance Tips

1. **Memory Management**:
   - Enable "Auto-clear when memory exceeds limit" in the Data tab
   - Adjust memory limit based on your system RAM

2. **Frame Rate**:
   - Reduce `display.downsample_factor` in config for faster updates
   - Increase `display.voxel_size_um` for lower resolution but better performance

3. **Data Persistence**:
   - Use "Clear Data" button to reset accumulation
   - Export accumulated volumes via "Export..." button (future feature)

## Troubleshooting

- **"napari not installed"**: Install with `pip install napari[all]`
- **Memory issues**: Increase voxel size or reduce sample region radius in config
- **Slow performance**: Check GPU acceleration is enabled in napari preferences
- **No data visible**: Verify channel visibility and opacity settings

## Understanding the Visualization

- **High-res storage (5 µm)**: Preserves fine details during rotation
- **Low-res display (15 µm)**: Smooth real-time rendering
- **3× resolution ratio**: Prevents thin structures from disappearing during small rotations
- **Sparse storage**: Only stores voxels with data, saving memory

The system accumulates data persistently - as you scan through Z positions and rotate the sample, the data builds up in 3D space, creating the characteristic "pipe" patterns you described.