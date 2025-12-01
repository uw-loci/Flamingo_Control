#!/usr/bin/env python3
"""
Test script to simulate GUI operations for debugging 3D voxel movement issue.
This script reproduces the sequence of operations from flamingo_20251201_000042.log

Run this from the Connection tab after establishing connection to the microscope.
It will simulate GUI button clicks and operations to create a reproducible test case.
"""

import time
import logging
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QApplication

logger = logging.getLogger(__name__)

def test_3d_voxel_movement(main_window):
    """
    Simulate the exact sequence of operations from the log file.

    Args:
        main_window: Reference to the FlamingoApplication main window
    """

    print("\n" + "="*60)
    print("Starting 3D Voxel Movement Test Sequence")
    print("Based on log file: flamingo_20251201_000042")
    print("="*60)

    # Get references to the necessary components
    workflow_view = main_window.workflow_view
    connection_view = main_window.connection_view
    controller = main_window.connection_controller

    # Store initial position for returning at end
    initial_position = {'x': 8.197, 'y': 13.889, 'z': 22.182, 'r': 68.0}

    def step1_query_position():
        """Step 1: Query current stage position"""
        nonlocal initial_position
        print("\n[Step 1] Querying current stage position...")
        if controller and controller.tcp_client:
            position = controller.get_current_stage_position()
            if position:
                # Update initial_position with actual values
                initial_position.update(position)
                print(f"  Current position: X={position['x']:.3f}, Y={position['y']:.3f}, "
                      f"Z={position['z']:.3f}, R={position.get('r', 0):.1f}°")
            else:
                print("  Failed to get stage position, using defaults")
        QTimer.singleShot(1000, step2_open_live_viewer)

    def step2_open_live_viewer():
        """Step 2: Open Live Viewer window"""
        print("\n[Step 2] Opening Live Viewer...")
        if hasattr(workflow_view, 'open_live_viewer_button'):
            # Simulate button click
            workflow_view.open_live_viewer_button.click()
            print("  Live Viewer opened")
        else:
            print("  WARNING: Could not find Live Viewer button")
        QTimer.singleShot(2000, step3_configure_laser)

    def step3_configure_laser():
        """Step 3: Configure and enable Laser 4 (640nm)"""
        print("\n[Step 3] Configuring Laser 4 (640nm)...")

        # Find laser controls in workflow view
        if hasattr(workflow_view, 'laser_checkboxes'):
            # Uncheck all lasers first
            for i in range(4):
                if i < len(workflow_view.laser_checkboxes):
                    workflow_view.laser_checkboxes[i].setChecked(False)

            # Check Laser 4 (index 3)
            if len(workflow_view.laser_checkboxes) > 3:
                workflow_view.laser_checkboxes[3].setChecked(True)
                print("  Laser 4 checkbox enabled")

                # Set laser power to 14.4%
                if hasattr(workflow_view, 'laser_power_inputs') and len(workflow_view.laser_power_inputs) > 3:
                    workflow_view.laser_power_inputs[3].setText("14.4")
                    print("  Laser 4 power set to 14.4%")

        # Enable laser preview
        if controller and controller.tcp_client:
            success = controller.tcp_client.laser_enable_preview(4)
            print(f"  Laser 4 preview enabled: {success}")

        QTimer.singleShot(2000, step4_start_live_view)

    def step4_start_live_view():
        """Step 4: Start Live View"""
        print("\n[Step 4] Starting Live View...")

        # Find and click start live view button
        if hasattr(workflow_view, 'start_live_view_button'):
            workflow_view.start_live_view_button.click()
            print("  Live View started")
        elif controller:
            # Direct controller call as fallback
            controller.start_live_view()
            print("  Live View started (via controller)")

        QTimer.singleShot(3000, step5_initial_stage_moves)

    def step5_initial_stage_moves():
        """Step 5: Initial stage movements before 3D population"""
        print("\n[Step 5] Performing initial stage adjustments...")

        if controller and controller.tcp_client:
            # Move 1: Z adjustment (22.182 -> 22.380 mm)
            print("  Moving Z: 22.182 -> 22.380 mm")
            controller.move_stage_absolute('Z', 22.380)
            time.sleep(1.5)

            # Move 2: Y adjustment (13.889 -> 13.690 mm)
            print("  Moving Y: 13.889 -> 13.690 mm")
            controller.move_stage_absolute('Y', 13.690)
            time.sleep(1.5)

            # Move 3: Y fine adjustment (13.690 -> 13.490 mm)
            print("  Moving Y: 13.690 -> 13.490 mm")
            controller.move_stage_absolute('Y', 13.490)
            time.sleep(1.5)

        QTimer.singleShot(2000, step6_open_3d_viewer)

    def step6_open_3d_viewer():
        """Step 6: Open 3D Visualization window"""
        print("\n[Step 6] Opening 3D Visualization window...")

        if hasattr(workflow_view, 'open_3d_visualization_button'):
            workflow_view.open_3d_visualization_button.click()
            print("  3D Visualization window opened")
        else:
            print("  WARNING: Could not find 3D Visualization button")

        QTimer.singleShot(3000, step7_start_3d_population)

    def step7_start_3d_population():
        """Step 7: Start populating 3D view from live stream"""
        print("\n[Step 7] Starting 3D population from Live View...")

        # Access the 3D visualization window
        if hasattr(main_window, 'visualization_3d_window') and main_window.visualization_3d_window:
            viz_window = main_window.visualization_3d_window

            # Click the "Populate from Live View" checkbox/button
            if hasattr(viz_window, 'populate_live_checkbox'):
                viz_window.populate_live_checkbox.setChecked(True)
                print("  3D population started at 2 Hz")
            else:
                print("  WARNING: Could not find populate checkbox")
        else:
            print("  WARNING: 3D Visualization window not found")

        QTimer.singleShot(3000, step8_stage_moves_during_capture)

    def step8_stage_moves_during_capture():
        """Step 8: Move stage while 3D capture is active"""
        print("\n[Step 8] Moving stage during 3D capture...")
        print("  This tests if voxels move with the stage")

        if controller and controller.tcp_client:
            # Get current position
            pos = controller.get_current_stage_position()
            if pos:
                print(f"  Starting position: X={pos['x']:.3f}, Y={pos['y']:.3f}, Z={pos['z']:.3f}")

            # Move 4: Y movement during capture (13.490 -> 14.981 mm, ~1.5mm = 3 FOVs)
            print("\n  Moving Y by ~3 FOVs (1.5mm): 13.490 -> 14.981 mm")
            controller.move_stage_absolute('Y', 14.981)
            time.sleep(3)

            # Log voxel count if available
            if hasattr(main_window, 'visualization_3d_window') and main_window.visualization_3d_window:
                viz = main_window.visualization_3d_window
                if hasattr(viz, 'dual_storage') and viz.dual_storage:
                    for ch_id in range(4):
                        count = viz.dual_storage.get_voxel_count(ch_id)
                        if count > 0:
                            print(f"    Channel {ch_id}: {count} voxels")

            # Move 5: Z movement during capture (22.380 -> 22.880 mm, 0.5mm = 1 FOV)
            print("\n  Moving Z by ~1 FOV (0.5mm): 22.380 -> 22.880 mm")
            controller.move_stage_absolute('Z', 22.880)
            time.sleep(3)

            # Move 6: X movement (not in original log, but requested - move 1 FOV)
            print("\n  Moving X by ~1 FOV (0.5mm): 8.197 -> 8.697 mm")
            controller.move_stage_absolute('X', 8.697)
            time.sleep(3)

            # Get final position
            pos = controller.get_current_stage_position()
            if pos:
                print(f"\n  Final position: X={pos['x']:.3f}, Y={pos['y']:.3f}, Z={pos['z']:.3f}")

        QTimer.singleShot(3000, step9_stop_3d_population)

    def step9_stop_3d_population():
        """Step 9: Stop 3D population"""
        print("\n[Step 9] Stopping 3D population...")

        if hasattr(main_window, 'visualization_3d_window') and main_window.visualization_3d_window:
            viz_window = main_window.visualization_3d_window

            if hasattr(viz_window, 'populate_live_checkbox'):
                viz_window.populate_live_checkbox.setChecked(False)
                print("  3D population stopped")

                # Export voxel data for analysis
                print("\n  Exporting voxel data for analysis...")
                export_voxel_data(viz_window)

        QTimer.singleShot(2000, step10_stop_live_view)

    def step10_stop_live_view():
        """Step 10: Stop Live View"""
        print("\n[Step 10] Stopping Live View...")

        if hasattr(workflow_view, 'stop_live_view_button'):
            workflow_view.stop_live_view_button.click()
            print("  Live View stopped")
        elif controller:
            controller.stop_live_view()
            print("  Live View stopped (via controller)")

        QTimer.singleShot(1000, step11_disable_lasers)

    def step11_disable_lasers():
        """Step 11: Disable all lasers"""
        print("\n[Step 11] Disabling all light sources...")

        # Uncheck all laser checkboxes
        if hasattr(workflow_view, 'laser_checkboxes'):
            for i, checkbox in enumerate(workflow_view.laser_checkboxes):
                checkbox.setChecked(False)
                print(f"  Laser {i+1} unchecked")

        # Disable preview mode
        if controller and controller.tcp_client:
            controller.tcp_client.laser_disable_preview()
            print("  Laser preview disabled")

        QTimer.singleShot(1000, step11b_return_to_origin)

    def step11b_return_to_origin():
        """Step 11b: Return stage to original position for repeatability"""
        nonlocal initial_position
        print("\n[Step 11b] Returning to original position for test repeatability...")

        if controller and controller.tcp_client:
            # Use the stored initial positions
            original_x = initial_position['x']
            original_y = initial_position['y']
            original_z = initial_position['z']
            original_r = initial_position.get('r', 68.0)

            print(f"  Moving to original position: X={original_x:.3f}, Y={original_y:.3f}, Z={original_z:.3f}, R={original_r:.1f}°")

            # Move each axis back to original position
            print("  Moving X axis...")
            controller.move_stage_absolute('X', original_x)
            time.sleep(1.5)

            print("  Moving Y axis...")
            controller.move_stage_absolute('Y', original_y)
            time.sleep(1.5)

            print("  Moving Z axis...")
            controller.move_stage_absolute('Z', original_z)
            time.sleep(1.5)

            # Verify final position
            pos = controller.get_current_stage_position()
            if pos:
                print(f"  Final position: X={pos['x']:.3f}, Y={pos['y']:.3f}, Z={pos['z']:.3f}, R={pos.get('r', 0):.1f}°")
                print("  Stage returned to original position - test is repeatable")
            else:
                print("  Could not verify final position")
        else:
            print("  WARNING: Could not return to origin - no controller available")

        QTimer.singleShot(1000, step12_final_summary)

    def step12_final_summary():
        """Step 12: Generate final summary and export data"""
        print("\n" + "="*60)
        print("Test Sequence Complete!")
        print("="*60)

        # Generate summary report
        if hasattr(main_window, 'visualization_3d_window') and main_window.visualization_3d_window:
            viz = main_window.visualization_3d_window
            if hasattr(viz, 'dual_storage') and viz.dual_storage:
                print("\nFinal voxel counts:")
                for ch_id in range(4):
                    count = viz.dual_storage.get_voxel_count(ch_id)
                    if count > 0:
                        print(f"  Channel {ch_id}: {count} non-zero voxels")

                print("\nExpected behavior:")
                print("  - Voxels should have appeared at objective position during capture")
                print("  - Existing voxels should have moved when stage moved")
                print("  - New voxels should have been added as stage explored new regions")
                print("\nActual behavior:")
                print("  - Check if voxels moved with stage movements")
                print("  - Check if new regions were populated")
                print("  - Review the exported numpy arrays for analysis")

        print("\nTest complete. Check logs and exported data for analysis.")

    def export_voxel_data(viz_window):
        """Export voxel data as numpy arrays for analysis"""
        import numpy as np
        import os

        try:
            if hasattr(viz_window, 'dual_storage') and viz_window.dual_storage:
                storage = viz_window.dual_storage

                # Create export directory
                export_dir = "/home/msnelson/LSControl/Flamingo_Control/tests/voxel_data_export"
                os.makedirs(export_dir, exist_ok=True)

                timestamp = time.strftime("%Y%m%d_%H%M%S")

                for ch_id in range(4):
                    if storage.get_voxel_count(ch_id) > 0:
                        # Get display data
                        display_data = storage.get_display_array(ch_id)
                        if display_data is not None:
                            filename = f"{export_dir}/channel_{ch_id}_display_{timestamp}.npy"
                            np.save(filename, display_data)
                            print(f"    Exported Channel {ch_id} display data: {filename}")
                            print(f"      Shape: {display_data.shape}, Non-zero: {np.count_nonzero(display_data)}")

                # Export metadata
                metadata = {
                    'timestamp': timestamp,
                    'voxel_size_um': storage.display_voxel_size_um,
                    'storage_voxel_size_um': storage.storage_voxel_size_um,
                    'napari_shape': storage.napari_shape,
                    'world_center_um': storage.world_center_um.tolist() if hasattr(storage, 'world_center_um') else None
                }

                import json
                metadata_file = f"{export_dir}/metadata_{timestamp}.json"
                with open(metadata_file, 'w') as f:
                    json.dump(metadata, f, indent=2)
                print(f"    Exported metadata: {metadata_file}")

        except Exception as e:
            print(f"    Error exporting voxel data: {e}")

    # Start the test sequence
    QTimer.singleShot(100, step1_query_position)


def run_test_from_connection_tab(connection_controller):
    """
    Entry point to run the test from the Connection tab.

    Usage from Connection tab console:
    >>> from tests.test_3d_voxel_movement import run_test_from_connection_tab
    >>> run_test_from_connection_tab(self)
    """
    # Get the main application window
    app = QApplication.instance()
    if app:
        # Find the main window
        for widget in app.topLevelWidgets():
            if hasattr(widget, 'workflow_view') and hasattr(widget, 'connection_controller'):
                print("Found FlamingoApplication main window")
                test_3d_voxel_movement(widget)
                return True

    print("Error: Could not find main application window")
    return False


if __name__ == "__main__":
    print("This script should be run from within the Flamingo Control application")
    print("From the Connection tab console, run:")
    print(">>> from tests.test_3d_voxel_movement import run_test_from_connection_tab")
    print(">>> run_test_from_connection_tab(self)")