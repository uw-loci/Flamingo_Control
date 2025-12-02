#!/usr/bin/env python3
"""
Quick launcher script for the 3D voxel movement test.

This script can be run directly from the command line or executed from within
the Flamingo Control application. It ensures the test module is properly imported
and the test is executed.

Usage:
    # From command line in Flamingo_Control directory:
    python run_3d_voxel_test.py

    # From within Flamingo Control Connection tab console:
    exec(open('run_3d_voxel_test.py').read())
"""

import sys
import os

# Add src directory to path if needed
src_path = os.path.join(os.path.dirname(__file__), 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Add tests directory to path
tests_path = os.path.join(os.path.dirname(__file__), 'tests')
if tests_path not in sys.path:
    sys.path.insert(0, tests_path)

def run_test():
    """Run the 3D voxel movement test."""
    try:
        # Try to find the main window and controller
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()

        if app:
            # Find the main window
            main_window = None
            for widget in app.topLevelWidgets():
                if hasattr(widget, 'connection_controller'):
                    main_window = widget
                    break

            if main_window and hasattr(main_window, 'connection_controller'):
                controller = main_window.connection_controller

                # Import and run the test
                from tests.test_3d_movement_simple import test_voxel_movement

                print("\n" + "="*60)
                print("Starting 3D Voxel Movement Test")
                print("="*60)
                print("\nIMPORTANT: Before running this test:")
                print("1. Ensure you are connected to the microscope")
                print("2. Open the Camera Live Viewer window")
                print("3. Open the 3D Visualization window")
                print("4. Make sure the stage is at a safe starting position")
                print("\nThe test will:")
                print("- Enable Laser 4 (640nm) at 14.4% power")
                print("- Start live view")
                print("- Begin 3D population from live view")
                print("- Move stage in X, Y, Z by 0.5mm each")
                print("- Capture voxel data at each position")
                print("- Return to original position")
                print("- Export results to JSON file")
                print("\n" + "="*60)

                # Run the test
                success = test_voxel_movement(controller, main_window)

                if success:
                    print("\n✓ Test completed successfully!")
                    print("Check the tests/voxel_test_results/ directory for exported data.")
                else:
                    print("\n✗ Test failed or was interrupted.")

                return success
            else:
                print("ERROR: Could not find connection controller.")
                print("Please ensure you are running this from within Flamingo Control")
                print("and that you have an active connection.")
                return False
        else:
            print("ERROR: No Qt application found.")
            print("This test must be run from within the Flamingo Control application.")
            return False

    except Exception as e:
        print(f"ERROR: Failed to run test: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    # Check if we're running inside Flamingo Control
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance()

    if app:
        # Running inside Flamingo Control
        success = run_test()
        sys.exit(0 if success else 1)
    else:
        print("This test must be run from within the Flamingo Control application.")
        print("\nTo run the test:")
        print("1. Open Flamingo Control")
        print("2. Connect to the microscope")
        print("3. Go to the Connection tab")
        print("4. Click the 'Test 3D Voxel Movement' button")
        print("\nAlternatively, from the Connection tab console:")
        print(">>> exec(open('run_3d_voxel_test.py').read())")
        sys.exit(1)