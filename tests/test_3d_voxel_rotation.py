#!/usr/bin/env python3
"""
Test script to debug 3D voxel rotation issues using the Sample View interface.

This test:
1. Opens Sample View
2. Configures Red LED at 50% intensity
3. Starts Live View and 3D population
4. Moves stage along Y axis (data collected near rotation axis)
5. Applies 90° rotation
6. Verifies data is still present and correctly positioned

Based on log file: flamingo_20251212_181755.log
Coordinates from log: X=6.86mm, Z=18.6mm, Y range ~11-16mm

Run this from the Connection tab after establishing connection to the microscope.
"""

import time
import logging
import numpy as np
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QApplication

logger = logging.getLogger(__name__)

# Motion polling settings
MOTION_POLL_INTERVAL = 200  # ms - how often to check if motion complete
MOTION_WAIT_TIMEOUT = 15000  # ms - max time to wait for motion
MOTION_SETTLE_DELAY = 500  # ms - delay after motion completes before next move


def safe_move_via_ui(sample_view, axis, position, callback, retry_count=0):
    """
    Safely execute a move using Sample View's UI method (same as slider release).

    This uses sample_view._send_position_command() which is exactly what happens
    when the user releases a position slider, ensuring we test the actual UI code path.

    Since move_absolute() is ASYNCHRONOUS (returns immediately after sending command),
    we wait for motion completion by polling is_waiting_for_motion() with an initial
    delay to let the background wait thread start.

    Args:
        sample_view: SampleView instance
        axis: Axis to move ('x', 'y', 'z', 'r')
        position: Target position
        callback: Function to call after move completes
        retry_count: Internal retry counter
    """
    max_retries = 10  # Max retries (10 * 500ms = 5s max wait)

    try:
        # Use UI method - same as slider release
        sample_view._send_position_command(axis, position)
        # Move command sent - wait for background thread to start, then poll for completion
        pc = sample_view.movement_controller.position_controller
        # Delay 300ms to let _wait_for_motion_complete_async spawn its thread
        # and call motion_tracker.wait_for_motion_complete() before we start polling
        QTimer.singleShot(300, lambda: wait_for_motion_complete(pc, callback))
    except RuntimeError as e:
        if "already in progress" in str(e).lower() and retry_count < max_retries:
            # Motion still in progress - wait and retry
            print(f"    (waiting for previous motion to finish, retry {retry_count + 1})")
            QTimer.singleShot(500, lambda: safe_move_via_ui(sample_view, axis, position, callback, retry_count + 1))
        else:
            # Other error or max retries - log and continue
            print(f"    ERROR: {e}")
            QTimer.singleShot(100, callback)


def wait_for_motion_complete(position_controller, callback, poll_count=0):
    """
    Wait for motion to complete by polling, then call callback.

    Uses QTimer to poll position_controller.motion_tracker.is_waiting_for_motion()
    until motion is complete, then calls the callback with a settle delay.

    Args:
        position_controller: The PositionController instance
        callback: Function to call when motion is complete
        poll_count: Internal counter for timeout tracking
    """
    max_polls = MOTION_WAIT_TIMEOUT // MOTION_POLL_INTERVAL

    # Check if motion is still in progress
    if hasattr(position_controller, 'motion_tracker'):
        is_moving = position_controller.motion_tracker.is_waiting_for_motion()
    else:
        is_moving = False

    if not is_moving:
        # Motion complete - add settle delay before callback
        QTimer.singleShot(MOTION_SETTLE_DELAY, callback)
    elif poll_count >= max_polls:
        # Timeout - proceed anyway
        print(f"    WARNING: Motion wait timeout ({MOTION_WAIT_TIMEOUT/1000}s), proceeding...")
        QTimer.singleShot(MOTION_SETTLE_DELAY, callback)
    else:
        # Still moving - check again after interval
        QTimer.singleShot(
            MOTION_POLL_INTERVAL,
            lambda: wait_for_motion_complete(position_controller, callback, poll_count + 1)
        )


# Test parameters from log file
TEST_X_POSITION = 6.86   # mm - constant X during Y scan
TEST_Z_POSITION = 18.6   # mm - constant Z during Y scan
TEST_Y_START = 16.0      # mm - start Y position
TEST_Y_END = 11.0        # mm - end Y position (moving "up")
TEST_ROTATION = 90.0     # degrees - clean rotation for debugging
LED_INTENSITY = 50.0     # percent


def test_3d_voxel_rotation(app):
    """
    Test 3D voxel rotation using Sample View interface.

    Args:
        app: Reference to the FlamingoApplication instance
    """

    print("\n" + "="*60)
    print("Starting 3D Voxel Rotation Test")
    print("Using Sample View Interface")
    print("="*60)
    print(f"\nTest parameters:")
    print(f"  X position: {TEST_X_POSITION} mm (constant)")
    print(f"  Z position: {TEST_Z_POSITION} mm (constant)")
    print(f"  Y range: {TEST_Y_START} -> {TEST_Y_END} mm")
    print(f"  Rotation: {TEST_ROTATION}°")
    print(f"  LED: Red at {LED_INTENSITY}%")

    # Store initial position for returning at end
    initial_position = {'x': TEST_X_POSITION, 'y': TEST_Y_START, 'z': TEST_Z_POSITION, 'r': 0.0}
    voxel_counts_before = {}
    voxel_counts_after = {}

    def step1_open_sample_view():
        """Step 1: Open Sample View"""
        print("\n[Step 1] Opening Sample View...")

        # Open Sample View via application method
        if hasattr(app, '_open_sample_view'):
            app._open_sample_view()
            print("  Sample View opened")
        else:
            print("  ERROR: Could not find _open_sample_view method")
            return

        QTimer.singleShot(2000, step2_query_position)

    def step2_query_position():
        """Step 2: Query and store current position"""
        nonlocal initial_position
        print("\n[Step 2] Querying current stage position...")

        sample_view = app.sample_view
        if sample_view and sample_view.movement_controller:
            position = sample_view.movement_controller.get_position()
            if position:
                initial_position = {
                    'x': position.x,
                    'y': position.y,
                    'z': position.z,
                    'r': position.r
                }
                print(f"  Current position: X={position.x:.3f}, Y={position.y:.3f}, "
                      f"Z={position.z:.3f}, R={position.r:.1f}°")

        QTimer.singleShot(1000, step3_configure_led)

    def step3_configure_led():
        """Step 3: Configure Red LED at 50% intensity"""
        print("\n[Step 3] Configuring Red LED at 50%...")

        sample_view = app.sample_view
        if sample_view and hasattr(sample_view, 'laser_led_panel'):
            panel = sample_view.laser_led_panel

            # Set LED color to Red (index 0) FIRST, before enabling
            if hasattr(panel, '_led_combobox') and panel._led_combobox:
                panel._led_combobox.setCurrentIndex(0)  # Red
                print("  LED color set to Red")

            # Set LED intensity to 50% BEFORE enabling
            if hasattr(panel, '_led_slider') and panel._led_slider:
                panel._led_slider.setValue(int(LED_INTENSITY))
                print(f"  LED intensity set to {LED_INTENSITY}%")

            # Also set via spinbox if available
            if hasattr(panel, '_led_spinbox') and panel._led_spinbox:
                panel._led_spinbox.setValue(LED_INTENSITY)

            # NOW click the LED radio button - use .click() to trigger buttonClicked signal
            # (setChecked() doesn't trigger the signal that actually sends command to server)
            if hasattr(panel, '_led_radio') and panel._led_radio:
                panel._led_radio.click()
                print("  LED radio button clicked (enabling LED)")
        else:
            print("  WARNING: Could not access laser_led_panel")

        QTimer.singleShot(1000, step4_move_to_start_position)

    def step4_move_to_start_position():
        """Step 4: Move to starting position (using UI's _send_position_command)"""
        print("\n[Step 4] Moving to start position...")
        print(f"  Target: X={TEST_X_POSITION}, Y={TEST_Y_START}, Z={TEST_Z_POSITION}, R=0°")

        sample_view = app.sample_view
        if not (sample_view and sample_view.movement_controller):
            print("  ERROR: No movement controller")
            QTimer.singleShot(1000, step5_start_live_view)
            return

        def move_x():
            print("  Moving X axis...")
            safe_move_via_ui(sample_view, 'x', TEST_X_POSITION, move_z)

        def move_z():
            print("  Moving Z axis...")
            safe_move_via_ui(sample_view, 'z', TEST_Z_POSITION, move_y)

        def move_y():
            print("  Moving Y axis...")
            safe_move_via_ui(sample_view, 'y', TEST_Y_START, move_r)

        def move_r():
            print("  Setting rotation to 0°...")
            safe_move_via_ui(sample_view, 'r', 0.0, verify_position)

        def verify_position():
            position = sample_view.movement_controller.get_position()
            if position:
                print(f"  Actual position: X={position.x:.3f}, Y={position.y:.3f}, "
                      f"Z={position.z:.3f}, R={position.r:.1f}°")
            QTimer.singleShot(1000, step5_start_live_view)

        move_x()

    def step5_start_live_view():
        """Step 5: Start Live View"""
        print("\n[Step 5] Starting Live View...")

        sample_view = app.sample_view
        if sample_view and hasattr(sample_view, 'live_view_toggle_btn'):
            # Click Live View button if not already active
            if sample_view.camera_controller and not sample_view.camera_controller.is_live_view_active():
                sample_view.live_view_toggle_btn.click()
                print("  Live View started")
            else:
                print("  Live View already active")
        else:
            print("  WARNING: Could not find live_view_toggle_btn")

        QTimer.singleShot(2000, step6_start_populating)

    def step6_start_populating():
        """Step 6: Start populating 3D view"""
        print("\n[Step 6] Starting 3D population...")

        sample_view = app.sample_view
        if sample_view and hasattr(sample_view, 'populate_btn'):
            if not sample_view.populate_btn.isChecked():
                sample_view.populate_btn.click()
                print("  3D population started")
            else:
                print("  3D population already active")
        else:
            print("  WARNING: Could not find populate_btn")

        QTimer.singleShot(2000, step7_move_y_axis)

    def step7_move_y_axis():
        """Step 7: Move Y axis to collect data along rotation axis (via UI)"""
        print("\n[Step 7] Collecting data along Y axis...")
        print(f"  Moving Y from {TEST_Y_START} to {TEST_Y_END} mm")

        sample_view = app.sample_view
        if not (sample_view and sample_view.movement_controller):
            print("  ERROR: No movement controller")
            QTimer.singleShot(2000, step8_record_voxel_counts)
            return

        y_positions = list(np.linspace(TEST_Y_START, TEST_Y_END, 6))  # 6 positions
        current_step = [0]  # Use list to allow modification in nested function

        def move_next_y():
            if current_step[0] < len(y_positions):
                y_pos = y_positions[current_step[0]]
                print(f"  Step {current_step[0]+1}/6: Moving to Y={y_pos:.3f} mm...")
                current_step[0] += 1
                # Use UI method with data collection delay after
                safe_move_via_ui(sample_view, 'y', y_pos, lambda: QTimer.singleShot(1000, move_next_y))
            else:
                print("  Y axis scan complete")
                QTimer.singleShot(2000, step8_record_voxel_counts)

        move_next_y()

    def step8_record_voxel_counts():
        """Step 8: Record voxel counts before rotation"""
        nonlocal voxel_counts_before
        print("\n[Step 8] Recording voxel counts before rotation...")

        # Access voxel storage
        if app.sample_3d_visualization_window:
            viz = app.sample_3d_visualization_window
            if hasattr(viz, 'voxel_storage') and viz.voxel_storage:
                storage = viz.voxel_storage

                for ch_id in range(4):
                    # Get display volume and count non-zero voxels
                    volume = storage.get_display_volume(ch_id)
                    if volume is not None:
                        count = np.count_nonzero(volume)
                        voxel_counts_before[ch_id] = count
                        if count > 0:
                            # Find bounding box of data
                            nonzero_indices = np.nonzero(volume)
                            if len(nonzero_indices[0]) > 0:
                                z_range = (nonzero_indices[0].min(), nonzero_indices[0].max())
                                y_range = (nonzero_indices[1].min(), nonzero_indices[1].max())
                                x_range = (nonzero_indices[2].min(), nonzero_indices[2].max())
                                print(f"  Channel {ch_id}: {count} voxels")
                                print(f"    Bounding box: Z={z_range}, Y={y_range}, X={x_range}")
        else:
            print("  WARNING: Could not access voxel storage")

        QTimer.singleShot(1000, step9_stop_populating)

    def step9_stop_populating():
        """Step 9: Stop populating before rotation"""
        print("\n[Step 9] Stopping 3D population...")

        sample_view = app.sample_view
        if sample_view and hasattr(sample_view, 'populate_btn'):
            if sample_view.populate_btn.isChecked():
                sample_view.populate_btn.click()
                print("  3D population stopped")

        QTimer.singleShot(1000, step10_apply_rotation)

    def step10_apply_rotation():
        """Step 10: Apply 90° rotation (via UI)"""
        print(f"\n[Step 10] Applying {TEST_ROTATION}° rotation...")

        sample_view = app.sample_view
        if not (sample_view and sample_view.movement_controller):
            print("  ERROR: No movement controller")
            QTimer.singleShot(3000, step11_check_voxels_after_rotation)
            return

        print(f"  Rotating stage to {TEST_ROTATION}°...")

        def verify_rotation():
            position = sample_view.movement_controller.get_position()
            if position:
                print(f"  Actual rotation: {position.r:.1f}°")
            # Extra delay for rotation transform processing
            QTimer.singleShot(3000, step11_check_voxels_after_rotation)

        safe_move_via_ui(sample_view, 'r', TEST_ROTATION, verify_rotation)

    def step11_check_voxels_after_rotation():
        """Step 11: Check voxel counts after rotation"""
        nonlocal voxel_counts_after
        print("\n[Step 11] Checking voxels after rotation...")

        # Access voxel storage
        if app.sample_3d_visualization_window:
            viz = app.sample_3d_visualization_window
            if hasattr(viz, 'voxel_storage') and viz.voxel_storage:
                storage = viz.voxel_storage

                # Get current stage position for transform
                sample_view = app.sample_view
                stage_pos = None
                if sample_view and sample_view.movement_controller:
                    pos = sample_view.movement_controller.get_position()
                    if pos:
                        stage_pos = {'x': pos.x, 'y': pos.y, 'z': pos.z, 'r': pos.r}

                for ch_id in range(4):
                    # Get TRANSFORMED display volume (after rotation)
                    if stage_pos:
                        volume = storage.get_display_volume_transformed(ch_id, stage_pos)
                    else:
                        volume = storage.get_display_volume(ch_id)

                    if volume is not None:
                        count = np.count_nonzero(volume)
                        voxel_counts_after[ch_id] = count
                        if count > 0:
                            nonzero_indices = np.nonzero(volume)
                            if len(nonzero_indices[0]) > 0:
                                z_range = (nonzero_indices[0].min(), nonzero_indices[0].max())
                                y_range = (nonzero_indices[1].min(), nonzero_indices[1].max())
                                x_range = (nonzero_indices[2].min(), nonzero_indices[2].max())
                                print(f"  Channel {ch_id}: {count} voxels (was {voxel_counts_before.get(ch_id, 0)})")
                                print(f"    Bounding box: Z={z_range}, Y={y_range}, X={x_range}")

                # Compare counts
                print("\n  Voxel count comparison:")
                for ch_id in set(list(voxel_counts_before.keys()) + list(voxel_counts_after.keys())):
                    before = voxel_counts_before.get(ch_id, 0)
                    after = voxel_counts_after.get(ch_id, 0)
                    if before > 0 or after > 0:
                        pct_change = ((after - before) / before * 100) if before > 0 else float('inf')
                        status = "OK" if after > 0 else "LOST!"
                        print(f"    Channel {ch_id}: {before} -> {after} ({pct_change:+.1f}%) [{status}]")

        QTimer.singleShot(1000, step12_export_data)

    def step12_export_data():
        """Step 12: Export voxel data for analysis"""
        print("\n[Step 12] Exporting voxel data...")

        if app.sample_3d_visualization_window:
            viz = app.sample_3d_visualization_window
            export_voxel_data(viz, "rotation_test")

        QTimer.singleShot(1000, step13_stop_live_view)

    def step13_stop_live_view():
        """Step 13: Stop Live View"""
        print("\n[Step 13] Stopping Live View...")

        sample_view = app.sample_view
        if sample_view and sample_view.camera_controller:
            if sample_view.camera_controller.is_live_view_active():
                if hasattr(sample_view, 'live_view_toggle_btn'):
                    sample_view.live_view_toggle_btn.click()
                    print("  Live View stopped")
                else:
                    print("  WARNING: Could not find live_view_toggle_btn")

        QTimer.singleShot(1000, step14_return_to_origin)

    def step14_return_to_origin():
        """Step 14: Return to original position (via UI)"""
        nonlocal initial_position
        print("\n[Step 14] Returning to original position...")

        sample_view = app.sample_view
        if not (sample_view and sample_view.movement_controller):
            print("  ERROR: No movement controller")
            QTimer.singleShot(1000, step15_final_summary)
            return

        print(f"  Moving to: X={initial_position['x']:.3f}, Y={initial_position['y']:.3f}, "
              f"Z={initial_position['z']:.3f}, R={initial_position['r']:.1f}°")

        def return_r():
            print("  Returning rotation...")
            safe_move_via_ui(sample_view, 'r', initial_position['r'], return_x)

        def return_x():
            print("  Returning X...")
            safe_move_via_ui(sample_view, 'x', initial_position['x'], return_y)

        def return_y():
            print("  Returning Y...")
            safe_move_via_ui(sample_view, 'y', initial_position['y'], return_z)

        def return_z():
            print("  Returning Z...")
            safe_move_via_ui(sample_view, 'z', initial_position['z'], finish_return)

        def finish_return():
            print("  Stage returned to original position")
            QTimer.singleShot(1000, step15_final_summary)

        return_r()

    def step15_final_summary():
        """Step 15: Print final summary"""
        print("\n" + "="*60)
        print("Test Complete!")
        print("="*60)

        print("\n== RESULTS ==")
        print("\nVoxel counts before rotation:")
        for ch_id, count in voxel_counts_before.items():
            if count > 0:
                print(f"  Channel {ch_id}: {count}")

        print("\nVoxel counts after 90° rotation:")
        for ch_id, count in voxel_counts_after.items():
            if count > 0:
                print(f"  Channel {ch_id}: {count}")

        # Check if rotation caused data loss
        data_lost = False
        for ch_id in voxel_counts_before:
            if voxel_counts_before[ch_id] > 0 and voxel_counts_after.get(ch_id, 0) == 0:
                data_lost = True
                print(f"\n*** BUG CONFIRMED: Channel {ch_id} data LOST after rotation! ***")

        if not data_lost and any(v > 0 for v in voxel_counts_before.values()):
            print("\n** Data survived rotation - transform appears to work **")

        print("\nCheck exported files in tests/voxel_data_export/ for detailed analysis")

    # Start the test sequence
    QTimer.singleShot(100, step1_open_sample_view)


def export_voxel_data(viz_window, prefix: str):
    """Export voxel data as numpy arrays for analysis"""
    import os

    try:
        if hasattr(viz_window, 'voxel_storage') and viz_window.voxel_storage:
            storage = viz_window.voxel_storage

            # Create export directory
            export_dir = "/home/msnelson/LSControl/Flamingo_Control/tests/voxel_data_export"
            os.makedirs(export_dir, exist_ok=True)

            timestamp = time.strftime("%Y%m%d_%H%M%S")

            for ch_id in range(4):
                volume = storage.get_display_volume(ch_id)
                if volume is not None and np.count_nonzero(volume) > 0:
                    filename = f"{export_dir}/{prefix}_channel_{ch_id}_{timestamp}.npy"
                    np.save(filename, volume)
                    print(f"    Exported Channel {ch_id}: {filename}")
                    print(f"      Shape: {volume.shape}, Non-zero: {np.count_nonzero(volume)}")

            # Export metadata
            import json
            metadata = {
                'timestamp': timestamp,
                'test_parameters': {
                    'x_position': TEST_X_POSITION,
                    'z_position': TEST_Z_POSITION,
                    'y_start': TEST_Y_START,
                    'y_end': TEST_Y_END,
                    'rotation': TEST_ROTATION,
                    'led_intensity': LED_INTENSITY,
                },
                'voxel_size_um': list(storage.config.display_voxel_size) if hasattr(storage, 'config') else None,
            }

            metadata_file = f"{export_dir}/{prefix}_metadata_{timestamp}.json"
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            print(f"    Exported metadata: {metadata_file}")

    except Exception as e:
        print(f"    Error exporting voxel data: {e}")
        import traceback
        traceback.print_exc()


def run_test_from_connection_tab(connection_controller=None):
    """
    Entry point to run the test from the Connection tab.

    Usage from Connection tab console:
    >>> from tests.test_3d_voxel_rotation import run_test_from_connection_tab
    >>> run_test_from_connection_tab()
    """
    # Get the main application window
    app = QApplication.instance()
    if app:
        # Find the main window
        for widget in app.topLevelWidgets():
            if hasattr(widget, 'sample_view') or hasattr(widget, '_open_sample_view'):
                print("Found FlamingoApplication main window")
                test_3d_voxel_rotation(widget)
                return True

    print("Error: Could not find main application window")
    return False


if __name__ == "__main__":
    print("This script should be run from within the Flamingo Control application")
    print("From the Connection tab console, run:")
    print(">>> from tests.test_3d_voxel_rotation import run_test_from_connection_tab")
    print(">>> run_test_from_connection_tab()")
