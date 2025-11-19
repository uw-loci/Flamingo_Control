#!/usr/bin/env python3
"""
Test script for the 3D Sample Visualization window.
This tests the window in standalone mode without requiring a full connection.
"""

import sys
import numpy as np
from pathlib import Path
import logging

# Configure logging to show INFO messages
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)

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
    """Test the 3D visualization window with the new coordinate system."""
    app = QApplication(sys.argv)

    # Create the visualization window
    window = Sample3DVisualizationWindow()
    window.show()

    print("=" * 60)
    print("3D VISUALIZATION TEST - New Coordinate System")
    print("=" * 60)
    print()
    print("KEY FEATURES:")
    print("  ✓ Color-coded sliders matching napari axes")
    print("    - Z (Yellow): Napari Axis 0")
    print("    - Y (Magenta): Napari Axis 1")
    print("    - X (Cyan): Napari Axis 2")
    print("  ✓ Physical mm coordinates (user-facing)")
    print("  ✓ Napari pixel coordinates (internal)")
    print("  ✓ Y-axis inverted for intuitive 'up' direction")
    print("  ✓ Objective at Z=0 (back wall)")
    print("  ✓ Rotation indicator at Y=0 (top, XZ plane)")
    print("  ✓ X/Z invert checkboxes for stage configuration")
    print()
    print("=" * 60)
    print()

    # Test coordinate transformation
    print("Testing coordinate transformation...")
    print()

    # Get config ranges
    config = window.config['stage_control']
    x_range = config['x_range_mm']
    y_range = config['y_range_mm']
    z_range = config['z_range_mm']

    # Test corner positions
    test_positions = [
        ("Bottom-Back-Left", x_range[0], y_range[0], z_range[0]),
        ("Bottom-Back-Right", x_range[1], y_range[0], z_range[0]),
        ("Top-Back-Left", x_range[0], y_range[1], z_range[0]),
        ("Top-Front-Right", x_range[1], y_range[1], z_range[1]),
        ("Center", (x_range[0] + x_range[1])/2, (y_range[0] + y_range[1])/2, (z_range[0] + z_range[1])/2)
    ]

    print("Coordinate Transformation Tests:")
    print("-" * 60)
    for name, x_mm, y_mm, z_mm in test_positions:
        napari_coords = window.coord_mapper.physical_to_napari(x_mm, y_mm, z_mm)
        back_to_physical = window.coord_mapper.napari_to_physical(*napari_coords)
        round_trip_ok = window.coord_mapper.test_round_trip(x_mm, y_mm, z_mm)

        print(f"{name}:")
        print(f"  Physical: ({x_mm:.2f}, {y_mm:.2f}, {z_mm:.2f}) mm")
        print(f"  Napari:   ({napari_coords[0]}, {napari_coords[1]}, {napari_coords[2]}) pixels")
        print(f"  Round-trip: {'✓ PASS' if round_trip_ok else '✗ FAIL'}")
        print()

    print("=" * 60)
    print()
    print("INTERACTIVE TEST:")
    print("  1. Use the color-coded sliders to move the sample holder")
    print("  2. Observe the holder position change in napari viewer")
    print("  3. Try the X/Z invert checkboxes")
    print("  4. Rotate the stage with the rotation slider")
    print("  5. Watch the red rotation indicator rotate in the XZ plane")
    print()
    print("VISUALIZATION FEATURES:")
    print("  • Yellow circle at Z=0: Objective position (back wall)")
    print("  • Red line at Y=0: Rotation indicator (0° reference)")
    print("  • Gray cylinder: Sample holder (extends from position to top)")
    print("  • Cyan wireframe: Chamber boundaries")
    print()
    print("=" * 60)
    print()

    # Manually trigger sample data visualization update
    # (in case it wasn't called during init)
    print("Updating sample data visualization...")
    if hasattr(window, '_update_sample_data_visualization'):
        window._update_sample_data_visualization()
        print("✓ Sample data visualization updated")

    # Force a GUI update
    app.processEvents()

    # Optionally enable streaming for simulated data
    # (commented out by default - user can test manually with sliders)
    enable_simulated_data = False

    if enable_simulated_data:
        print("\nSimulating frame data...")
        window.is_streaming = True

        # Simulate scanning through Z
        z_start_mm = (z_range[0] + z_range[1]) * 0.4
        z_end_mm = (z_range[0] + z_range[1]) * 0.6
        num_z_planes = 10

        z_positions_mm = np.linspace(z_start_mm, z_end_mm, num_z_planes)

        for z_idx, z_mm in enumerate(z_positions_mm):
            frame = simulate_frame_data(z_idx, num_z_planes)

            metadata = {
                'x_position': config['x_default_mm'],
                'y_position': config['y_default_mm'],
                'z_position': z_mm * 1000,  # Convert to µm
                'rotation': {'rx': 0, 'ry': 0, 'rz': 0},
                'timestamp': z_idx * 0.1,
                'pixel_to_micron': 10.0,
                'active_channels': [0, 1, 2, 3]
            }

            window.process_frame(frame, metadata)

            if z_idx % 2 == 0:
                window._update_visualization()
                app.processEvents()
                print(f"  Processed Z-plane {z_idx + 1}/{num_z_planes} at {z_mm:.2f} mm")

        window._update_visualization()
        app.processEvents()

        memory_stats = window.voxel_storage.get_memory_usage()
        print(f"\nMemory usage: {memory_stats['total_mb']:.1f} MB")

    # Run the application
    sys.exit(app.exec_())


if __name__ == "__main__":
    test_window()