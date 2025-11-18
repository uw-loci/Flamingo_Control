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


def simulate_frame_data():
    """Generate simulated multi-channel microscopy data."""
    # Simulate a 512x512 4-channel image
    h, w, c = 512, 512, 4
    frame = np.zeros((h, w, c), dtype=np.uint16)

    # Channel 0 (DAPI): Small bright spots (nuclei)
    for _ in range(20):
        x, y = np.random.randint(50, w-50), np.random.randint(50, h-50)
        r = np.random.randint(5, 15)
        yy, xx = np.ogrid[-y:h-y, -x:w-x]
        mask = xx**2 + yy**2 <= r**2
        frame[mask, 0] = np.random.randint(20000, 40000)

    # Channel 1 (GFP): Diffuse signal
    x, y = w//2, h//2
    yy, xx = np.ogrid[:h, :w]
    dist = np.sqrt((xx - x)**2 + (yy - y)**2)
    frame[:, :, 1] = np.clip(10000 * np.exp(-dist/100), 0, 65535).astype(np.uint16)

    # Channel 2 (RFP): Linear gradient
    frame[:, :, 2] = np.linspace(0, 30000, h)[:, np.newaxis].astype(np.uint16)

    # Channel 3 (Brightfield): Uniform with noise
    frame[:, :, 3] = np.random.randint(5000, 10000, (h, w), dtype=np.uint16)

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
    z_positions = np.linspace(0, 5000, 20)  # 20 Z-planes from 0 to 5000 µm
    rotations = [
        {'rx': 0, 'ry': 0, 'rz': 0},      # No rotation
        {'rx': 0, 'ry': 0, 'rz': 45},     # 45° rotation around Z
        {'rx': 0, 'ry': 45, 'rz': 0},     # 45° rotation around Y
        {'rx': 30, 'ry': 30, 'rz': 30},   # Combined rotation
    ]

    frame_count = 0
    for rotation in rotations:
        print(f"Processing rotation: {rotation}")
        for z in z_positions:
            frame = simulate_frame_data()
            metadata = {
                'z_position': z,
                'rotation': rotation,
                'timestamp': frame_count * 0.1,
                'pixel_to_micron': 0.65,
                'active_channels': [0, 1, 2]
            }

            # Process the frame
            window.process_frame(frame, metadata)
            frame_count += 1

            # Update the UI periodically
            if frame_count % 5 == 0:
                window._update_visualization()
                app.processEvents()

    print(f"Processed {frame_count} frames")
    print("Test complete. Window is now interactive.")

    # Show memory usage
    memory_stats = window.voxel_storage.get_memory_usage()
    print(f"Memory usage: {memory_stats['total_mb']:.1f} MB")
    print(f"Storage voxels: {memory_stats['storage_voxels']:,}")
    print(f"Display voxels: {memory_stats['display_voxels']:,}")

    # Run the application
    sys.exit(app.exec_())


if __name__ == "__main__":
    test_window()