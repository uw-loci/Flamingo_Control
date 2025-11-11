"""
Example: Using the Enhanced Stage Control System

This example demonstrates how to use the MovementController and
EnhancedStageControlView for complete stage control.

Run with:
    python -m examples.stage_control_example
"""

import sys
import logging
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from PyQt5.QtWidgets import QApplication, QMainWindow, QTabWidget, QSplitter
from PyQt5.QtCore import Qt

from py2flamingo.services.connection_service import ConnectionService
from py2flamingo.controllers.position_controller import PositionController
from py2flamingo.controllers.movement_controller import MovementController
from py2flamingo.views.enhanced_stage_control_view import EnhancedStageControlView
from py2flamingo.views.widgets.stage_map_widget import StageMapWidget


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


class StageControlDemo(QMainWindow):
    """
    Demo application showing enhanced stage control features.
    """

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Flamingo Stage Control - Demo")
        self.setGeometry(100, 100, 1200, 800)

        # Initialize connection (replace with actual connection)
        self.setup_connection()

        # Initialize controllers
        self.setup_controllers()

        # Setup UI
        self.setup_ui()

    def setup_connection(self):
        """Setup microscope connection."""
        # NOTE: Replace this with your actual connection setup
        # For demo purposes, we'll create a mock connection

        print("\n" + "="*60)
        print("DEMO MODE - Connection Setup")
        print("="*60)
        print("\nTo use with real microscope:")
        print("1. Update this method with actual connection code")
        print("2. Connect to microscope before creating controllers")
        print("\nExample:")
        print("    self.connection_service = ConnectionService()")
        print("    self.connection_service.connect('192.168.1.100', 5000)")
        print("="*60 + "\n")

        # For demo, create connection service
        # (will work in offline mode for UI testing)
        self.connection_service = ConnectionService()

    def setup_controllers(self):
        """Initialize controllers."""
        print("Initializing controllers...")

        # Position controller
        self.position_controller = PositionController(
            connection_service=self.connection_service
        )

        # Enhanced movement controller
        self.movement_controller = MovementController(
            connection_service=self.connection_service,
            position_controller=self.position_controller
        )

        print("✓ Controllers initialized")

    def setup_ui(self):
        """Setup user interface."""
        print("Setting up UI...")

        # Create tab widget
        self.tab_widget = QTabWidget()

        # Tab 1: Stage Control Only
        self.create_control_only_tab()

        # Tab 2: Stage Control with Map
        self.create_control_with_map_tab()

        # Tab 3: Map Only
        self.create_map_only_tab()

        # Set as central widget
        self.setCentralWidget(self.tab_widget)

        print("✓ UI setup complete\n")

    def create_control_only_tab(self):
        """Create tab with stage control view only."""
        # Create enhanced stage control view
        stage_view = EnhancedStageControlView(
            movement_controller=self.movement_controller
        )

        # Add to tabs
        self.tab_widget.addTab(stage_view, "Stage Control")

    def create_control_with_map_tab(self):
        """Create tab with stage control and map in split view."""
        # Create splitter (horizontal split)
        splitter = QSplitter(Qt.Horizontal)

        # Left: Stage control view
        stage_view = EnhancedStageControlView(
            movement_controller=self.movement_controller
        )
        splitter.addWidget(stage_view)

        # Right: Map visualization
        limits = self.movement_controller.get_stage_limits()
        map_widget = StageMapWidget(stage_limits=limits)

        # Connect to position updates
        self.movement_controller.position_changed.connect(
            map_widget.update_position
        )

        # Connect to motion events for visual feedback
        self.movement_controller.motion_started.connect(
            lambda axis: map_widget.set_moving(True)
        )
        self.movement_controller.motion_stopped.connect(
            lambda axis: map_widget.set_moving(False)
        )

        splitter.addWidget(map_widget)

        # Set splitter sizes (60% controls, 40% map)
        splitter.setSizes([700, 500])

        # Add to tabs
        self.tab_widget.addTab(splitter, "Control + Map")

    def create_map_only_tab(self):
        """Create tab with map visualization only."""
        # Create map widget
        limits = self.movement_controller.get_stage_limits()
        map_widget = StageMapWidget(stage_limits=limits)

        # Connect to position updates
        self.movement_controller.position_changed.connect(
            map_widget.update_position
        )

        # Add to tabs
        self.tab_widget.addTab(map_widget, "Position Map")


def print_usage_examples():
    """Print example code snippets."""
    print("\n" + "="*60)
    print("PROGRAMMATIC USAGE EXAMPLES")
    print("="*60 + "\n")

    print("1. ABSOLUTE MOVEMENT")
    print("-" * 40)
    print("# Move X axis to 10.5 mm")
    print("movement_controller.move_absolute('x', 10.5)")
    print()
    print("# Move rotation to 45 degrees")
    print("movement_controller.move_absolute('r', 45.0)")
    print()

    print("2. RELATIVE MOVEMENT")
    print("-" * 40)
    print("# Jog Y axis by +1.0 mm")
    print("movement_controller.move_relative('y', 1.0)")
    print()
    print("# Jog Z axis by -0.5 mm")
    print("movement_controller.move_relative('z', -0.5)")
    print()

    print("3. HOME AXIS")
    print("-" * 40)
    print("# Home X axis")
    print("movement_controller.home_axis('x')")
    print()
    print("# Home all axes")
    print("movement_controller.position_controller.go_home()")
    print()

    print("4. GET POSITION")
    print("-" * 40)
    print("# Get all positions")
    print("pos = movement_controller.get_position()")
    print("print(f'X={pos.x}, Y={pos.y}, Z={pos.z}, R={pos.r}')")
    print()
    print("# Get single axis")
    print("x = movement_controller.get_position(axis='x')")
    print("print(f'X position: {x} mm')")
    print()

    print("5. N7 REFERENCE POSITION")
    print("-" * 40)
    print("# Save current position as N7 reference")
    print("movement_controller.save_n7_reference()")
    print()
    print("# Go to N7 reference")
    print("n7_ref = movement_controller.get_n7_reference()")
    print("if n7_ref:")
    print("    movement_controller.position_controller.move_to_position(n7_ref)")
    print()

    print("6. POSITION VERIFICATION")
    print("-" * 40)
    print("from py2flamingo.models.microscope import Position")
    print()
    print("target = Position(x=10.0, y=5.0, z=2.0, r=45.0)")
    print("success, msg = movement_controller.verify_position(target)")
    print("if success:")
    print("    print('Position verified!')")
    print("else:")
    print("    print(f'Verification failed: {msg}')")
    print()

    print("7. CONNECT TO SIGNALS")
    print("-" * 40)
    print("# Position changed callback")
    print("def on_position_changed(x, y, z, r):")
    print("    print(f'Position: X={x:.3f}, Y={y:.3f}, Z={z:.3f}, R={r:.2f}')")
    print()
    print("movement_controller.position_changed.connect(on_position_changed)")
    print()
    print("# Motion events")
    print("movement_controller.motion_started.connect(")
    print("    lambda axis: print(f'{axis} motion started')")
    print(")")
    print("movement_controller.motion_stopped.connect(")
    print("    lambda axis: print(f'{axis} motion stopped')")
    print(")")
    print()

    print("8. EMERGENCY STOP")
    print("-" * 40)
    print("# Halt all motion")
    print("movement_controller.halt_motion()")
    print()
    print("# Clear emergency stop (allow movements again)")
    print("movement_controller.position_controller.clear_emergency_stop()")
    print()

    print("="*60 + "\n")


def main():
    """Main entry point."""
    print("\n" + "="*60)
    print("Flamingo Stage Control - Demo Application")
    print("="*60 + "\n")

    # Print usage examples
    print_usage_examples()

    print("Starting GUI application...")
    print("(Close window to exit)\n")

    # Create Qt application
    app = QApplication(sys.argv)

    # Create and show demo window
    demo = StageControlDemo()
    demo.show()

    # Run application
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
