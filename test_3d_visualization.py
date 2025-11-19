#!/usr/bin/env python3
"""
Test script for the 3D Sample Visualization window.
This tests the window in standalone mode without requiring a full connection.
"""

import sys
import numpy as np
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from PyQt5.QtWidgets import QApplication
from py2flamingo.views.sample_3d_visualization_window import Sample3DVisualizationWindow


def simulate_frame_data(z_index=0, num_z_planes=20):
    """Generate simulated multi-channel microscopy data.

    Args:
        z_index: Current Z-plane index (0 to num_z_planes-1)
        num_z_planes: Total number of Z-planes being simulated
    """
    # Simulate a smaller 256x256 4-channel image (faster for testing)
    h, w, c = 256, 256, 4
    frame = np.zeros((h, w, c), dtype=np.uint16)

    # Channel 0 (405nm DAPI): Small bright spots that vary with Z
    # Simulate nuclei that are brighter when in focus
    focus_factor = 1.0 - abs(z_index - num_z_planes//2) / (num_z_planes//2)
    num_spots = max(5, int(15 * focus_factor))

    for _ in range(num_spots):
        x, y = np.random.randint(30, w-30), np.random.randint(30, h-30)
        r = np.random.randint(3, 8)
        yy, xx = np.ogrid[-y:h-y, -x:w-x]
        mask = xx**2 + yy**2 <= r**2
        intensity = int(30000 * focus_factor + 10000)
        frame[mask, 0] = np.random.randint(intensity, min(intensity + 10000, 65000))

    # Channel 1 (488nm GFP): Diffuse signal with Z-variation
    x, y = w//2, h//2
    yy, xx = np.ogrid[:h, :w]
    dist = np.sqrt((xx - x)**2 + (yy - y)**2)
    z_modulation = 0.5 + 0.5 * np.cos(2 * np.pi * z_index / num_z_planes)
    frame[:, :, 1] = np.clip(15000 * z_modulation * np.exp(-dist/80), 0, 65535).astype(np.uint16)

    # Channel 2 (561nm RFP): Horizontal gradient that shifts with Z
    shift = int(w * z_index / num_z_planes)
    gradient = np.roll(np.linspace(0, 25000, w), shift)
    frame[:, :, 2] = np.tile(gradient, (h, 1)).astype(np.uint16)

    # Channel 3 (640nm Far-Red): Sparse bright regions
    if z_index % 5 == 0:  # Only show in some Z-planes
        for _ in range(3):
            x, y = np.random.randint(40, w-40), np.random.randint(40, h-40)
            r = np.random.randint(15, 25)
            yy, xx = np.ogrid[-y:h-y, -x:w-x]
            mask = xx**2 + yy**2 <= r**2
            frame[mask, 3] = np.random.randint(20000, 35000)

    return frame


def test_window():
    """Test the 3D visualization window with simulated data."""
    app = QApplication(sys.argv)

    # Create the visualization window
    window = Sample3DVisualizationWindow()
    window.show()

    # Simulate some frames with different Z positions and rotations
    print("Simulating frame data...")

    # Enable streaming
    window.is_streaming = True

    # Simulate scanning through Z with rotation
    # Using smaller region in the middle of the chamber for visibility
    chamber_height_um = 36000  # 36mm chamber
    scan_start_um = chamber_height_um * 0.3  # Start at 30% height
    scan_end_um = chamber_height_um * 0.7    # End at 70% height
    num_z_planes = 20

    z_positions = np.linspace(scan_start_um, scan_end_um, num_z_planes)

    # Start with just no rotation for simplicity
    rotations = [
        {'rx': 0, 'ry': 0, 'rz': 0},      # No rotation
    ]

    frame_count = 0
    for rotation_idx, rotation in enumerate(rotations):
        print(f"\nProcessing rotation {rotation_idx + 1}/{len(rotations)}: {rotation}")
        for z_idx, z in enumerate(z_positions):
            # Generate frame with Z-dependent features
            frame = simulate_frame_data(z_idx, num_z_planes)

            metadata = {
                'z_position': z,
                'rotation': rotation,
                'timestamp': frame_count * 0.1,
                'pixel_to_micron': 10.0,  # 10 µm/pixel for reasonable sample size
                'active_channels': [0, 1, 2, 3]
            }

            # Process the frame
            window.process_frame(frame, metadata)
            frame_count += 1

            # Update the UI periodically
            if frame_count % 2 == 0:
                window._update_visualization()
                app.processEvents()
                print(f"  Processed Z-plane {z_idx + 1}/{num_z_planes} at {z:.0f} µm")

    # Final update
    window._update_visualization()
    app.processEvents()

    print(f"\nProcessed {frame_count} total frames")
    print("Test complete. Window is now interactive.")
    print("\nTips:")
    print("  - Toggle channel visibility in the Channels tab")
    print("  - Adjust contrast/opacity for each channel")
    print("  - Rotate the view with your mouse in the napari viewer")
    print("  - Try different rendering modes (MIP, Average, Additive)")

    # Show memory usage
    memory_stats = window.voxel_storage.get_memory_usage()
    print(f"Memory usage: {memory_stats['total_mb']:.1f} MB")
    print(f"Storage voxels: {memory_stats['storage_voxels']:,}")
    print(f"Display voxels: {memory_stats['display_voxels']:,}")

    # Run the application
    sys.exit(app.exec_())


if __name__ == "__main__":
    test_window()