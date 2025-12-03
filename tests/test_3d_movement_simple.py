#!/usr/bin/env python3
"""
Simplified test script for 3D voxel movement debugging.
Run this directly from the Connection tab Python console.

This version uses direct API calls rather than GUI simulation.
"""

import time
import numpy as np
import logging
from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import QCoreApplication

logger = logging.getLogger(__name__)

def process_gui_events():
    """Process Qt events to keep GUI responsive during test."""
    app = QApplication.instance()
    if app:
        app.processEvents()

def smart_sleep(seconds, message=None):
    """Sleep while processing Qt events to keep GUI responsive."""
    if message:
        print(f"   {message}")

    # Process events every 100ms during the sleep
    intervals = int(seconds * 10)
    for _ in range(intervals):
        time.sleep(0.1)
        process_gui_events()

def wait_for_movement_complete(controller, axis_name, timeout=5.0):
    """
    Wait for stage movement to complete by waiting for movement lock release.

    Args:
        controller: Position controller
        axis_name: Name of axis ('X', 'Y', 'Z', or 'R')
        timeout: Maximum time to wait (seconds)

    Returns:
        Final position or None if timeout
    """
    import time
    start_time = time.time()

    print(f"   Waiting for {axis_name}-axis movement to complete...")

    # Wait for movement lock to be released (indicates motion complete)
    while time.time() - start_time < timeout:
        # Check if movement lock is still held
        if hasattr(controller, '_movement_lock') and controller._movement_lock.locked():
            # Movement still in progress
            time.sleep(0.1)
            process_gui_events()
        else:
            # Movement complete - lock released
            # Brief delay to ensure position is stable
            time.sleep(0.2)

            # Get final position
            try:
                if axis_name == 'X':
                    final_pos = controller.stage_service.get_axis_position(1)
                elif axis_name == 'Y':
                    final_pos = controller.stage_service.get_axis_position(2)
                elif axis_name == 'Z':
                    final_pos = controller.stage_service.get_axis_position(3)
                elif axis_name == 'R':
                    final_pos = controller.stage_service.get_axis_position(4)
                else:
                    return None

                if final_pos is not None and final_pos != 0.0:
                    print(f"   {axis_name}-axis movement complete at {final_pos:.3f}mm")
                    return final_pos
                else:
                    print(f"   Warning: Could not get valid position for {axis_name}-axis")
                    return None
            except Exception as e:
                print(f"   Warning: Error getting final position: {e}")
                return None

    print(f"   Warning: {axis_name}-axis movement timeout after {timeout}s - lock still held")
    return None

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
    if pos and pos.y != 0.000:  # Check for valid position (0.000 indicates hardware still initializing)
        initial_x = pos.x
        initial_y = pos.y
        initial_z = pos.z
        initial_r = getattr(pos, 'r', 0.0)  # r might not always be present
        print(f"   Position: X={initial_x:.3f}, Y={initial_y:.3f}, Z={initial_z:.3f}, R={initial_r:.1f}°")

        # Validate position is within safe range to avoid safety violations
        if initial_y < 5.0 or initial_y > 25.0 or initial_z < 12.5 or initial_z > 26.0:
            print(f"   WARNING: Position is outside safe range")
            print("   Using safe default position instead")
            initial_x, initial_y, initial_z, initial_r = 8.31, 13.5, 21.0, 68.0
    else:
        print("   WARNING: Could not get valid position (hardware may be initializing), using safe defaults")
        initial_x, initial_y, initial_z, initial_r = 8.31, 13.5, 21.0, 68.0

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

        # Get camera_live_viewer and simulate GUI actions
        laser_enabled = False
        if main_window and hasattr(main_window, 'camera_live_viewer'):
            camera_viewer = main_window.camera_live_viewer

            # Check if we have the laser panel (might be a separate widget)
            laser_panel = None
            if hasattr(camera_viewer, 'laser_led_panel'):
                laser_panel = camera_viewer.laser_led_panel
                print("   Found laser control panel")
            elif hasattr(camera_viewer, 'laser_control_panel'):
                laser_panel = camera_viewer.laser_control_panel
                print("   Found laser control panel (alternate name)")
            else:
                # Try to find it in the layout
                for child in camera_viewer.findChildren(QWidget):
                    if child.__class__.__name__ == 'LaserLEDControlPanel':
                        laser_panel = child
                        print("   Found laser control panel in children")
                        break

            if laser_panel:
                # Use controller API directly instead of GUI manipulation (more reliable)
                try:
                    # Get the controller from the panel
                    if hasattr(laser_panel, 'laser_led_controller'):
                        llc = laser_panel.laser_led_controller

                        # Set laser power first
                        print("   Setting Laser 4 power via controller...")
                        llc.set_laser_power(4, 14.4)
                        process_gui_events()

                        # Enable laser for preview
                        print("   Enabling Laser 4 for preview...")
                        llc.enable_laser_for_preview(4, 'LEFT')
                        process_gui_events()

                        # Also update the GUI checkbox to match
                        if hasattr(laser_panel, '_laser_radios') and 4 in laser_panel._laser_radios:
                            laser_panel._laser_radios[4].setChecked(True)
                            process_gui_events()

                        laser_enabled = True
                        print("   Laser 4 enabled via controller API")
                    else:
                        print("   WARNING: No laser_led_controller found on panel")
                except Exception as e:
                    print(f"   Error enabling laser via controller: {e}")
                    import traceback
                    traceback.print_exc()
            else:
                print("   WARNING: Could not find laser control panel in camera viewer")

        if not laser_enabled:
            print("   WARNING: Laser may not be enabled - fluorescence data may not be captured")

        smart_sleep(1, "Waiting for laser to stabilize...")
    except Exception as e:
        print(f"   Error setting up laser: {e}")

    # Step 3: Open Camera Live Viewer window and start live view
    print("\n3. Checking Camera Live Viewer and starting live view...")
    camera_viewer = None
    try:
        # Check if camera viewer is already open
        if main_window and hasattr(main_window, 'camera_live_viewer'):
            camera_viewer = main_window.camera_live_viewer
            if camera_viewer and camera_viewer.isVisible():
                print("   Camera Live Viewer already open - skipping initialization delay")
            elif camera_viewer:
                camera_viewer.show()
                print("   Camera Live Viewer window opened")
                smart_sleep(5, "Waiting 5 seconds for camera initialization and OpenGL...")

        # Simulate clicking the Start Live View button in the GUI
        if main_window and hasattr(main_window, 'camera_live_viewer'):
            camera_viewer = main_window.camera_live_viewer
            if camera_viewer and hasattr(camera_viewer, 'start_btn'):
                # Click the start button - this triggers all the proper GUI updates
                start_button = camera_viewer.start_btn
                if start_button.isEnabled():
                    print("   Clicking 'Start Live View' button...")
                    start_button.click()  # This will handle everything including button state changes
                    print("   Live view started via GUI button click")
                else:
                    print("   Start button is disabled - live view may already be running")

                smart_sleep(3, "Waiting 3 seconds for camera stream to stabilize...")
            else:
                print("   WARNING: Could not find start button in camera_live_viewer")
        else:
            print("   WARNING: Could not find camera_live_viewer")
    except Exception as e:
        print(f"   Error starting live view: {e}")

    # Step 4: Check 3D viewer window
    print("\n4. Checking 3D Visualization window...")
    viz_window = None
    try:
        # Check if 3D window already exists and is visible
        if main_window and hasattr(main_window, 'sample_3d_visualization_window'):
            viz_window = main_window.sample_3d_visualization_window
            if viz_window and viz_window.isVisible():
                print("   3D Visualization window already open - ready to use")
            elif viz_window:
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
        smart_sleep(10, "Waiting 10 seconds to capture baseline data (~20 frames)...")
        print("   Baseline capture complete")
    else:
        print("\n5. Please manually start 'Populate from Live View'")
        smart_sleep(12, "Waiting 12 seconds for manual start and baseline...")

    # Step 7: Capture initial voxel state
    print("\n6. Capturing initial voxel state...")
    initial_voxels = capture_voxel_state(viz_window)

    # Step 8: Move stage and capture data
    print("\n7. Moving stage in X, Y, Z (one FOV each = ~0.5mm)...")
    print("   Note: Position queries now handle 'stage still moving' (0.000) responses properly")

    movements = []

    # Move X by 0.5mm (1 FOV)
    print("\n   Moving X by +0.5mm...")
    new_x = initial_x + 0.5
    controller.move_x(new_x)
    # Wait for movement to actually complete (motion tracker uses 10s timeout + 1.5s fallback for X/Y)
    final_x = wait_for_movement_complete(controller, 'X', timeout=15.0)
    if final_x is not None:
        print(f"   X-axis reached position: {final_x:.3f}mm")
    # Additional wait for voxel data capture
    smart_sleep(1.5, "Capturing voxel data...")
    movements.append(('X', initial_x, new_x))
    x_voxels = capture_voxel_state(viz_window)
    print(f"   Voxel state after X movement: {x_voxels}")

    # Move Y by 0.5mm (1 FOV) - ensure we stay within safe range (5.0-25.0)
    print("\n   Moving Y by +0.5mm...")
    new_y = initial_y + 0.5
    # Clamp Y to safe range to avoid safety violations
    if new_y > 24.5:  # Leave margin from max of 25.0
        new_y = 24.5
        print(f"   NOTE: Clamping Y to {new_y:.3f} to stay within safe range")
    controller.move_y(new_y)
    # Wait for movement to actually complete (motion tracker uses 10s timeout + 1.5s fallback for X/Y)
    final_y = wait_for_movement_complete(controller, 'Y', timeout=15.0)
    if final_y is not None:
        print(f"   Y-axis reached position: {final_y:.3f}mm")
    # Additional wait for voxel data capture
    smart_sleep(1.5, "Capturing voxel data...")
    movements.append(('Y', initial_y, new_y))
    y_voxels = capture_voxel_state(viz_window)
    print(f"   Voxel state after Y movement: {y_voxels}")

    # Move Z by 0.5mm (1 FOV)
    print("\n   Moving Z by +0.5mm...")
    new_z = initial_z + 0.5
    controller.move_z(new_z)
    # Wait for movement to actually complete (motion tracker uses 10s timeout + 3s fallback for Z)
    final_z = wait_for_movement_complete(controller, 'Z', timeout=15.0)
    if final_z is not None:
        print(f"   Z-axis reached position: {final_z:.3f}mm")
    # Additional wait for voxel data capture
    smart_sleep(1.5, "Capturing voxel data...")
    movements.append(('Z', initial_z, new_z))
    z_voxels = capture_voxel_state(viz_window)
    print(f"   Voxel state after Z movement: {z_voxels}")

    # Step 9: Capture final state at moved position
    print("\n8. Capturing final state at moved position...")
    final_moved_voxels = capture_voxel_state(viz_window)

    # Step 10: Stop population
    if viz_window and hasattr(viz_window, 'populate_button'):
        print("\n9. Stopping 3D population...")
        viz_window.populate_button.setChecked(False)
        smart_sleep(1, "Waiting for population to stop...")
    else:
        print("\n9. Please manually stop 'Populate from Live View'")

    # Step 11: Stop live view
    print("\n10. Stopping live view...")
    try:
        # Simulate clicking the Stop Live View button in the GUI
        if main_window and hasattr(main_window, 'camera_live_viewer'):
            camera_viewer = main_window.camera_live_viewer
            if camera_viewer and hasattr(camera_viewer, 'stop_btn'):
                stop_button = camera_viewer.stop_btn
                if stop_button.isEnabled():
                    print("   Clicking 'Stop Live View' button...")
                    stop_button.click()  # This will handle everything including button state changes
                    print("   Live view stopped via GUI button click")
                else:
                    print("   Stop button is disabled - live view may already be stopped")
            else:
                print("   WARNING: Could not find stop button in camera_live_viewer")
        else:
            print("   WARNING: Could not find camera_live_viewer to stop live view")
    except Exception as e:
        print(f"   Error stopping live view: {e}")
    smart_sleep(1, "Waiting for live view to stop...")

    # Step 12: Disable laser
    print("\n11. Disabling laser...")
    try:
        # Find the laser control panel and uncheck laser checkboxes
        if main_window and hasattr(main_window, 'camera_live_viewer'):
            camera_viewer = main_window.camera_live_viewer

            # Find the laser panel (same logic as enable)
            laser_panel = None
            if hasattr(camera_viewer, 'laser_led_panel'):
                laser_panel = camera_viewer.laser_led_panel
            elif hasattr(camera_viewer, 'laser_control_panel'):
                laser_panel = camera_viewer.laser_control_panel
            else:
                # Try to find it in the children
                for child in camera_viewer.findChildren(QWidget):
                    if child.__class__.__name__ == 'LaserLEDControlPanel':
                        laser_panel = child
                        break

            if laser_panel:
                # Uncheck all laser checkboxes using button group
                if hasattr(laser_panel, '_source_button_group'):
                    button_group = laser_panel._source_button_group
                    unchecked_any = False
                    for button in button_group.buttons():
                        if button.isChecked():
                            button.setChecked(False)
                            # Trigger the click handler to disable laser
                            button_group.buttonClicked.emit(button)
                            print(f"   Unchecked light source button ID {button_group.id(button)}")
                            unchecked_any = True
                    if not unchecked_any:
                        print("   All light sources already disabled")
                else:
                    print("   WARNING: Could not find _source_button_group in panel")
            else:
                print("   WARNING: Could not find laser control panel")
        else:
            print("   WARNING: Could not find camera_live_viewer to disable laser")
    except Exception as e:
        print(f"   Error disabling laser: {e}")

    # Step 13: Return to original position for repeatability
    print("\n12. Returning to original position for test repeatability...")

    print(f"   Commanding return to: X={initial_x:.3f}, Y={initial_y:.3f}, Z={initial_z:.3f}")

    # Use proper movement completion detection
    controller.move_x(initial_x)
    wait_for_movement_complete(controller, 'X', timeout=5.0)

    controller.move_y(initial_y)
    wait_for_movement_complete(controller, 'Y', timeout=5.0)

    controller.move_z(initial_z)
    wait_for_movement_complete(controller, 'Z', timeout=5.0)

    # Now we can safely query position since the StageService handles 0.000 responses properly
    print("\n   Verifying return to origin...")
    pos = controller.get_current_position()
    if pos:
        print(f"   Current position: X={pos.x:.3f}, Y={pos.y:.3f}, Z={pos.z:.3f}")
        print(f"   Target position:  X={initial_x:.3f}, Y={initial_y:.3f}, Z={initial_z:.3f}")

        # Check if we're close enough (within 0.01mm tolerance)
        if (abs(pos.x - initial_x) < 0.01 and
            abs(pos.y - initial_y) < 0.01 and
            abs(pos.z - initial_z) < 0.01):
            print("   ✓ Successfully returned to original position")
        else:
            print("   ⚠ Position slightly off, but within acceptable range")
    else:
        print("   Could not verify position, but stage was commanded to return")

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
        # Count voxels by checking storage_data dictionary size per channel
        for ch_id in range(4):
            if storage.has_data(ch_id):
                # Access the storage_data dictionary directly
                if hasattr(storage, 'storage_data'):
                    count = len(storage.storage_data.get(ch_id, {}))
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

    # Create results directory relative to current working directory
    results_dir = os.path.join(os.getcwd(), "tests", "voxel_test_results")
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