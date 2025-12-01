#!/usr/bin/env python3
"""
Simplified test script for 3D voxel movement debugging.
Run this directly from the Connection tab Python console.

This version uses direct API calls rather than GUI simulation.
"""

import time
import numpy as np
import logging

logger = logging.getLogger(__name__)

def test_voxel_movement(controller):
    """
    Simple test sequence to verify voxel movement.

    Run from Connection tab:
    >>> from tests.test_3d_movement_simple import test_voxel_movement
    >>> test_voxel_movement(self.controller)
    """
    print("\n" + "="*60)
    print("3D Voxel Movement Test - Simplified Version")
    print("="*60)

    if not controller or not controller.tcp_client:
        print("ERROR: No active connection to microscope")
        return False

    client = controller.tcp_client

    # Step 1: Query initial position
    print("\n1. Getting initial stage position...")
    pos = controller.get_current_stage_position()
    if pos:
        initial_x = pos.get('x', 8.197)
        initial_y = pos.get('y', 13.889)
        initial_z = pos.get('z', 22.182)
        initial_r = pos.get('r', 68.0)
        print(f"   Position: X={initial_x:.3f}, Y={initial_y:.3f}, Z={initial_z:.3f}, R={initial_r:.1f}°")
    else:
        print("   WARNING: Could not get position, using defaults")
        initial_x, initial_y, initial_z, initial_r = 8.197, 13.889, 22.182, 68.0

    # Step 2: Enable Laser 4 (640nm)
    print("\n2. Enabling Laser 4 (640nm) at 14.4% power...")
    try:
        # Set laser power
        client.send_command_and_wait("LASER_LEVEL_SET", int32Data0=4, buffer="14.4")
        time.sleep(0.5)

        # Enable preview
        result = client.laser_enable_preview(4)
        print(f"   Laser preview enabled: {result}")
        time.sleep(0.5)
    except Exception as e:
        print(f"   Error setting up laser: {e}")

    # Step 3: Start live view
    print("\n3. Starting live view...")
    try:
        controller.start_live_view()
        print("   Live view started")
        time.sleep(2)
    except Exception as e:
        print(f"   Error starting live view: {e}")

    # Step 4: Open 3D viewer (must be done through GUI)
    print("\n4. Please manually open the 3D Visualization window if not already open")
    print("   Waiting 3 seconds...")
    time.sleep(3)

    # Step 5: Get 3D visualization window reference
    viz_window = None
    try:
        # Try to find the 3D visualization window
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            for widget in app.topLevelWidgets():
                if hasattr(widget, 'visualization_3d_window'):
                    viz_window = widget.visualization_3d_window
                    break
    except:
        pass

    if not viz_window:
        print("   WARNING: Could not find 3D visualization window programmatically")
        print("   Please ensure it's open and start 'Populate from Live View' manually")
        time.sleep(5)
    else:
        print("   Found 3D visualization window")

    # Step 6: Start populating (if we have window reference)
    if viz_window and hasattr(viz_window, 'populate_live_checkbox'):
        print("\n5. Starting 3D population from live view...")
        viz_window.populate_live_checkbox.setChecked(True)
        print("   Population started")
        time.sleep(3)
    else:
        print("\n5. Please manually start 'Populate from Live View'")
        print("   Waiting 5 seconds...")
        time.sleep(5)

    # Step 7: Capture initial voxel state
    print("\n6. Capturing initial voxel state...")
    initial_voxels = capture_voxel_state(viz_window)

    # Step 8: Move stage and capture data
    print("\n7. Moving stage in X, Y, Z (one FOV each = ~0.5mm)...")

    movements = []

    # Move X by 0.5mm (1 FOV)
    print("\n   Moving X by +0.5mm...")
    new_x = initial_x + 0.5
    controller.move_stage_absolute('X', new_x)
    time.sleep(3)
    movements.append(('X', initial_x, new_x))
    x_voxels = capture_voxel_state(viz_window)

    # Move Y by 0.5mm (1 FOV)
    print("   Moving Y by +0.5mm...")
    new_y = initial_y + 0.5
    controller.move_stage_absolute('Y', new_y)
    time.sleep(3)
    movements.append(('Y', initial_y, new_y))
    y_voxels = capture_voxel_state(viz_window)

    # Move Z by 0.5mm (1 FOV)
    print("   Moving Z by +0.5mm...")
    new_z = initial_z + 0.5
    controller.move_stage_absolute('Z', new_z)
    time.sleep(3)
    movements.append(('Z', initial_z, new_z))
    z_voxels = capture_voxel_state(viz_window)

    # Step 9: Capture final state at moved position
    print("\n8. Capturing final state at moved position...")
    final_moved_voxels = capture_voxel_state(viz_window)

    # Step 10: Stop population
    if viz_window and hasattr(viz_window, 'populate_live_checkbox'):
        print("\n9. Stopping 3D population...")
        viz_window.populate_live_checkbox.setChecked(False)
        time.sleep(1)
    else:
        print("\n9. Please manually stop 'Populate from Live View'")

    # Step 11: Stop live view
    print("\n10. Stopping live view...")
    controller.stop_live_view()
    time.sleep(1)

    # Step 12: Disable laser
    print("\n11. Disabling laser...")
    try:
        client.laser_disable_preview()
    except:
        pass

    # Step 13: Return to original position for repeatability
    print("\n12. Returning to original position for test repeatability...")
    controller.move_stage_absolute('X', initial_x)
    time.sleep(1)
    controller.move_stage_absolute('Y', initial_y)
    time.sleep(1)
    controller.move_stage_absolute('Z', initial_z)
    time.sleep(2)

    # Verify we're back at origin
    pos = controller.get_current_stage_position()
    if pos:
        print(f"   Returned to: X={pos['x']:.3f}, Y={pos['y']:.3f}, Z={pos['z']:.3f}")
        print(f"   Original was: X={initial_x:.3f}, Y={initial_y:.3f}, Z={initial_z:.3f}")

    final_origin_voxels = capture_voxel_state(viz_window)

    # Step 14: Analyze results
    print("\n" + "="*60)
    print("TEST RESULTS:")
    print("="*60)

    print("\nStage Movements:")
    for axis, old_pos, new_pos in movements:
        print(f"  {axis}: {old_pos:.3f} -> {new_pos:.3f} mm (Δ={new_pos-old_pos:.3f} mm)")

    print("\nVoxel Analysis:")
    print(f"  Initial voxels:      {initial_voxels}")
    print(f"  After X move:        {x_voxels}")
    print(f"  After Y move:        {y_voxels}")
    print(f"  After Z move:        {z_voxels}")
    print(f"  Final (moved):       {final_moved_voxels}")
    print(f"  Back at origin:      {final_origin_voxels}")

    print("\nExpected Behavior:")
    print("  ✓ Voxels should increase as stage explores new regions")
    print("  ✓ Original voxels should appear shifted when stage moves")
    print("  ✓ Returning to origin should show original voxels in same place")
    print("  ✓ Stage should be back at exact starting position for repeatability")

    print("\nActual Behavior:")
    print("  ? Check if voxel counts increased with movement")
    print("  ? Check if voxels appeared to move in the viewer")
    print("  ? Check if original position was restored")
    print("  ? Verify stage returned to exact starting coordinates")

    # Export data for analysis
    export_test_data(movements, initial_voxels, x_voxels, y_voxels, z_voxels, final_moved_voxels, final_origin_voxels)

    return True


def capture_voxel_state(viz_window):
    """Capture current voxel counts for all channels."""
    if not viz_window or not hasattr(viz_window, 'dual_storage'):
        return {'error': 'No visualization window'}

    try:
        storage = viz_window.dual_storage
        state = {}
        for ch_id in range(4):
            count = storage.get_voxel_count(ch_id)
            if count > 0:
                state[f'ch{ch_id}'] = count

        # Also try to get center of mass for active channel
        if hasattr(storage, 'storage_arrays'):
            for ch_id, array_dict in storage.storage_arrays.items():
                if ch_id in state:
                    # Get approximate center of mass
                    try:
                        data = storage.get_display_array(ch_id)
                        if data is not None and np.any(data > 0):
                            coords = np.argwhere(data > 0)
                            com = coords.mean(axis=0)
                            state[f'ch{ch_id}_com'] = com.tolist()
                    except:
                        pass

        return state
    except Exception as e:
        return {'error': str(e)}


def export_test_data(movements, initial, x_move, y_move, z_move, final_moved, final_origin):
    """Export test data for further analysis."""
    import json
    import os
    from datetime import datetime

    # Create results directory
    results_dir = "/home/msnelson/LSControl/Flamingo_Control/tests/voxel_test_results"
    os.makedirs(results_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{results_dir}/voxel_movement_test_{timestamp}.json"

    data = {
        'timestamp': timestamp,
        'movements': [{'axis': m[0], 'from_mm': m[1], 'to_mm': m[2]} for m in movements],
        'voxel_states': {
            'initial': initial,
            'after_x_move': x_move,
            'after_y_move': y_move,
            'after_z_move': z_move,
            'final_at_moved_position': final_moved,
            'back_at_origin': final_origin
        },
        'test_repeatable': True  # Indicates stage was returned to origin
    }

    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\nTest data exported to: {filename}")
    return filename


if __name__ == "__main__":
    print("Run this from the Connection tab console:")
    print(">>> from tests.test_3d_movement_simple import test_voxel_movement")
    print(">>> test_voxel_movement(self.controller)")