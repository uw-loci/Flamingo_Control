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

def test_voxel_movement(controller, main_window=None):
    """
    Simple test sequence to verify voxel movement.

    Args:
        controller: Position controller for stage movements
        main_window: Main application window (optional, will search if not provided)

    Run from Connection tab button or manually:
    >>> from tests.test_3d_movement_simple import test_voxel_movement
    >>> test_voxel_movement(self.controller)
    """
    print("\n" + "="*60)
    print("3D Voxel Movement Test - Simplified Version")
    print("="*60)

    if not controller or not controller.connection.is_connected():
        print("ERROR: No active connection to microscope")
        return False

    # Step 1: Query initial position
    print("\n1. Getting initial stage position...")
    pos = controller.get_current_position()
    if pos:
        initial_x = pos.x
        initial_y = pos.y
        initial_z = pos.z
        initial_r = getattr(pos, 'r', 0.0)  # r might not always be present
        print(f"   Position: X={initial_x:.3f}, Y={initial_y:.3f}, Z={initial_z:.3f}, R={initial_r:.1f}°")
    else:
        print("   WARNING: Could not get position, using defaults")
        initial_x, initial_y, initial_z, initial_r = 8.197, 13.889, 22.182, 68.0

    # Step 2: Enable Laser 4 (640nm) directly via controller
    print("\n2. Enabling Laser 4 (640nm) at 14.4% power...")
    try:
        # Use provided main_window or find it
        if not main_window:
            from PyQt5.QtWidgets import QApplication
            app = QApplication.instance()
            for widget in app.topLevelWidgets():
                if widget.__class__.__name__ == 'MainWindow':
                    main_window = widget
                    break

        # Use the laser controller directly
        laser_enabled = False
        if main_window:
            # Try to find laser controller in different possible locations
            laser_controller = None
            if hasattr(main_window, 'laser_led_controller'):
                laser_controller = main_window.laser_led_controller
            elif hasattr(main_window, 'laser_controller'):
                laser_controller = main_window.laser_controller

            if laser_controller:
                # Make sure to disable all light sources first
                laser_controller.disable_all_light_sources()
                time.sleep(0.5)

                # Set the laser power level first
                if hasattr(laser_controller, 'set_laser_power'):
                    result = laser_controller.set_laser_power(4, 14.4)
                    print(f"   Laser 4 power set to 14.4%: {result}")
                    time.sleep(0.5)

                # Enable the laser for preview on left path
                try:
                    result = laser_controller.enable_laser_for_preview(4, "left")
                    laser_enabled = result
                    print(f"   Laser 4 preview enabled: {result}")

                    # Verify laser is active
                    if hasattr(laser_controller, 'get_active_laser'):
                        active = laser_controller.get_active_laser()
                        print(f"   Active laser confirmed: {active}")
                except Exception as e:
                    print(f"   Failed to enable laser: {e}")
            else:
                print("   WARNING: Could not find laser controller")
        else:
            print("   WARNING: Could not find main window")

        if not laser_enabled:
            print("   WARNING: Laser may not be enabled - fluorescence data may not be captured")

        time.sleep(1)
    except Exception as e:
        print(f"   Error setting up laser: {e}")

    # Step 3: Open Camera Live Viewer window and start live view
    print("\n3. Opening Camera Live Viewer and starting live view...")
    camera_viewer = None
    try:
        # First, open the camera live viewer window (required for data flow)
        if main_window and hasattr(main_window, 'camera_live_viewer'):
            camera_viewer = main_window.camera_live_viewer
            if camera_viewer and not camera_viewer.isVisible():
                camera_viewer.show()
                print("   Camera Live Viewer window opened")
                print("   Waiting 5 seconds for camera initialization and OpenGL...")
                time.sleep(5)  # Allow camera and OpenGL to fully initialize

        # Now start live view through the camera controller
        if main_window and hasattr(main_window, 'camera_controller'):
            result = main_window.camera_controller.start_live_view()
            print(f"   Live view started: {result}")
            print("   Waiting 3 seconds for camera stream to stabilize...")
            time.sleep(3)  # Allow camera stream to stabilize
        else:
            print("   WARNING: Could not find camera controller")
    except Exception as e:
        print(f"   Error starting live view: {e}")

    # Step 4: Open 3D viewer window
    print("\n4. Opening 3D Visualization window...")
    viz_window = None
    try:
        # Check if 3D window already exists in main_window
        if main_window and hasattr(main_window, 'sample_3d_visualization_window'):
            viz_window = main_window.sample_3d_visualization_window
            if viz_window and not viz_window.isVisible():
                viz_window.show()
                print("   Showing existing 3D Visualization window")

        # If not found, try to create it
        if not viz_window and main_window:
            # Check if main window has a method to open the 3D viewer
            if hasattr(main_window, 'open_3d_visualization'):
                main_window.open_3d_visualization()
                print("   Opened 3D Visualization via main window method")
                time.sleep(2)
                # Try to get reference again
                if hasattr(main_window, 'sample_3d_visualization_window'):
                    viz_window = main_window.sample_3d_visualization_window

        # If still not found, search all top-level widgets
        if not viz_window:
            from PyQt5.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                for widget in app.topLevelWidgets():
                    # Check if widget itself is the viz window
                    if widget.__class__.__name__ == 'Sample3DVisualizationWindow':
                        viz_window = widget
                        break

        if viz_window:
            print("   Found 3D visualization window")
        else:
            print("   WARNING: Could not find 3D visualization window")
            print("   Please ensure it's open and start 'Populate from Live View' manually")
            time.sleep(3)
    except Exception as e:
        print(f"   Error accessing 3D viewer: {e}")
        time.sleep(3)

    # Step 6: Start populating (if we have window reference)
    if viz_window and hasattr(viz_window, 'populate_button'):
        print("\n5. Starting 3D population from live view...")
        viz_window.populate_button.setChecked(True)
        print("   Population started at 2 Hz")
        print("   Waiting 10 seconds to capture baseline data (~20 frames)...")
        time.sleep(10)  # Critical: Allow adequate baseline frames before movement
        print("   Baseline capture complete")
    else:
        print("\n5. Please manually start 'Populate from Live View'")
        print("   Waiting 12 seconds for manual start and baseline...")
        time.sleep(12)

    # Step 7: Capture initial voxel state
    print("\n6. Capturing initial voxel state...")
    initial_voxels = capture_voxel_state(viz_window)

    # Step 8: Move stage and capture data
    print("\n7. Moving stage in X, Y, Z (one FOV each = ~0.5mm)...")

    movements = []

    # Move X by 0.5mm (1 FOV)
    print("\n   Moving X by +0.5mm...")
    new_x = initial_x + 0.5
    controller.move_x(new_x)
    print("   Waiting 5 seconds for movement and data capture...")
    time.sleep(5)  # Allow movement to complete and capture ~10 frames
    movements.append(('X', initial_x, new_x))
    x_voxels = capture_voxel_state(viz_window)
    print(f"   X movement complete. Voxel state: {x_voxels}")

    # Move Y by 0.5mm (1 FOV)
    print("\n   Moving Y by +0.5mm...")
    new_y = initial_y + 0.5
    controller.move_y(new_y)
    print("   Waiting 5 seconds for movement and data capture...")
    time.sleep(5)  # Allow movement to complete and capture ~10 frames
    movements.append(('Y', initial_y, new_y))
    y_voxels = capture_voxel_state(viz_window)
    print(f"   Y movement complete. Voxel state: {y_voxels}")

    # Move Z by 0.5mm (1 FOV)
    print("\n   Moving Z by +0.5mm...")
    new_z = initial_z + 0.5
    controller.move_z(new_z)
    print("   Waiting 5 seconds for movement and data capture...")
    time.sleep(5)  # Allow movement to complete and capture ~10 frames
    movements.append(('Z', initial_z, new_z))
    z_voxels = capture_voxel_state(viz_window)
    print(f"   Z movement complete. Voxel state: {z_voxels}")

    # Step 9: Capture final state at moved position
    print("\n8. Capturing final state at moved position...")
    final_moved_voxels = capture_voxel_state(viz_window)

    # Step 10: Stop population
    if viz_window and hasattr(viz_window, 'populate_button'):
        print("\n9. Stopping 3D population...")
        viz_window.populate_button.setChecked(False)
        time.sleep(1)
    else:
        print("\n9. Please manually stop 'Populate from Live View'")

    # Step 11: Stop live view
    print("\n10. Stopping live view...")
    try:
        # Use camera controller directly (stop_live_view_button doesn't exist in workflow_view)
        if main_window and hasattr(main_window, 'camera_controller'):
            result = main_window.camera_controller.stop_live_view()
            print(f"   Live view stopped: {result}")
    except Exception as e:
        print(f"   Error stopping live view: {e}")
    time.sleep(1)

    # Step 12: Disable laser
    print("\n11. Disabling laser...")
    try:
        # Use existing main_window or find it if needed
        if not main_window:
            from PyQt5.QtWidgets import QApplication
            app = QApplication.instance()
            for widget in app.topLevelWidgets():
                if widget.__class__.__name__ == 'MainWindow':
                    main_window = widget
                    break

        # Use the laser controller directly (laser_checkboxes don't exist in workflow_view)
        if main_window:
            laser_controller = None
            if hasattr(main_window, 'laser_controller'):
                laser_controller = main_window.laser_controller
            elif hasattr(main_window, 'laser_led_controller'):
                laser_controller = main_window.laser_led_controller

            if laser_controller:
                laser_controller.disable_all_light_sources()
                print("   All light sources disabled")
    except Exception as e:
        print(f"   Error disabling laser: {e}")

    # Step 13: Return to original position for repeatability
    print("\n12. Returning to original position for test repeatability...")
    controller.move_x(initial_x)
    time.sleep(1)
    controller.move_y(initial_y)
    time.sleep(1)
    controller.move_z(initial_z)
    time.sleep(2)

    # Verify we're back at origin
    pos = controller.get_current_position()
    if pos:
        print(f"   Returned to: X={pos.x:.3f}, Y={pos.y:.3f}, Z={pos.z:.3f}")
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
    if not viz_window:
        return {'error': 'No visualization window'}

    # Check for correct attribute name (voxel_storage vs dual_storage)
    storage = None
    if hasattr(viz_window, 'voxel_storage'):
        storage = viz_window.voxel_storage
    elif hasattr(viz_window, 'dual_storage'):
        storage = viz_window.dual_storage

    if not storage:
        return {'error': 'No voxel storage found'}

    try:
        state = {'total_voxels': 0}
        for ch_id in range(4):
            count = storage.get_voxel_count(ch_id)
            if count > 0:
                state[f'ch{ch_id}'] = count
                state['total_voxels'] += count

        # Also try to get center of mass for active channel
        if hasattr(storage, 'storage_arrays'):
            for ch_id, array_dict in storage.storage_arrays.items():
                if f'ch{ch_id}' in state:
                    # Get approximate center of mass
                    try:
                        data = storage.get_display_array(ch_id)
                        if data is not None and np.any(data > 0):
                            coords = np.argwhere(data > 0)
                            com = coords.mean(axis=0)
                            state[f'ch{ch_id}_com'] = com.tolist()
                    except:
                        pass

        # Add frame count estimate (at 2 Hz capture rate)
        if state['total_voxels'] > 0:
            state['approx_frames'] = 'Data is flowing'

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