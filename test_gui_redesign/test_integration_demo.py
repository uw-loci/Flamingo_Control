#!/usr/bin/env python3
"""
Integration demonstration for redesigned GUI components.

This script demonstrates both test windows (CameraLiveViewer and
Sample3DVisualizationWindow) displayed side-by-side on a 1920px screen.

Usage:
    python test_gui_redesign/test_integration_demo.py

Features:
    - Launches both test windows
    - Positions them side-by-side automatically
    - Shows dimension validation
    - Provides mock controllers for testing without hardware

Requirements:
    - PyQt5
    - Python 3.7+
    - Mock controllers (included in this script)
"""

import sys
from pathlib import Path
from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel, QVBoxLayout, QWidget
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QScreen
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add src to path
sys.path.append(str(Path(__file__).parent.parent / "src"))

# Import test implementations
from test_gui_redesign.test_camera_live_viewer import TestCameraLiveViewer
from test_gui_redesign.test_sample_3d_visualization_window import TestSample3DVisualizationWindow


# ============================================================================
# MOCK CONTROLLERS FOR TESTING WITHOUT HARDWARE
# ============================================================================

class MockCameraController(QObject):
    """Mock camera controller for testing."""

    # Signals
    new_image = pyqtSignal(object, object)  # image, header
    state_changed = pyqtSignal(object)  # CameraState
    error_occurred = pyqtSignal(str)  # error message
    frame_rate_updated = pyqtSignal(float)  # fps

    def __init__(self):
        super().__init__()
        from py2flamingo.controllers.camera_controller import CameraState
        self.state = CameraState.IDLE
        self._exposure_time = 10000  # 10ms
        self._auto_scale = False
        self._display_range = (0, 65535)

    def start_live_view(self):
        logger.info("Mock: Start live view")

    def stop_live_view(self):
        logger.info("Mock: Stop live view")

    def set_exposure_time(self, value_us):
        self._exposure_time = value_us
        logger.info(f"Mock: Set exposure to {value_us} µs")

    def is_auto_scale(self):
        return self._auto_scale

    def set_auto_scale(self, enabled):
        self._auto_scale = enabled
        logger.info(f"Mock: Set auto-scale to {enabled}")

    def set_display_range(self, min_val, max_val):
        self._display_range = (min_val, max_val)
        logger.info(f"Mock: Set display range to {min_val}-{max_val}")

    def get_display_range(self):
        return self._display_range


class MockMovementController:
    """Mock movement controller for testing."""

    def __init__(self):
        from PyQt5.QtCore import pyqtSignal, QObject
        # Add signals for position updates
        self._signal_emitter = QObject()
        self.position_changed = self._signal_emitter

    def get_position(self):
        """Return mock position."""
        from py2flamingo.models import Position
        return Position(x=6.655, y=7.45, z=19.25, r=0.0)

    def get_stage_limits(self):
        """Return mock stage limits."""
        return {
            'x': {'min': 1.0, 'max': 12.31},
            'y': {'min': -5.0, 'max': 10.0},
            'z': {'min': 12.5, 'max': 26.0},
            'r': {'min': -180.0, 'max': 180.0}
        }


class MockLaserLEDController:
    """Mock laser/LED controller for testing."""

    def __init__(self):
        pass

    def is_preview_active(self):
        return True


# ============================================================================
# INTEGRATION DEMO APPLICATION
# ============================================================================

class IntegrationDemo(QMainWindow):
    """
    Main demo application showing both test windows side-by-side.

    This launcher:
    1. Creates mock controllers
    2. Instantiates both test windows
    3. Positions them side-by-side
    4. Displays dimension validation
    """

    def __init__(self):
        super().__init__()

        self.setWindowTitle("GUI Redesign Integration Demo")
        self.setGeometry(100, 100, 400, 300)

        # Create central widget with instructions
        central_widget = QWidget()
        layout = QVBoxLayout()

        title = QLabel("GUI Redesign Integration Demo")
        title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        instructions = QLabel(
            "This demo shows the redesigned GUI components side-by-side.\n\n"
            "Both windows will open automatically:\n"
            "• CameraLiveViewer (660px wide) on the left\n"
            "• Sample3DVisualizationWindow (950px wide) on the right\n\n"
            "Total width: 1610px (fits comfortably on 1920px screen)\n\n"
            "Note: Mock controllers are used for testing without hardware."
        )
        instructions.setStyleSheet("font-size: 11pt;")
        instructions.setAlignment(Qt.AlignCenter)
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        self.status_label = QLabel("Initializing...")
        self.status_label.setStyleSheet("font-size: 10pt; color: #666;")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

        # Initialize windows (will be created after short delay)
        self.camera_viewer = None
        self.viz_window = None

        # Use QTimer to allow main window to show first
        QTimer.singleShot(500, self.initialize_windows)

    def initialize_windows(self):
        """Initialize both test windows with mock controllers."""
        try:
            self.status_label.setText("Creating mock controllers...")

            # Create mock controllers
            logger.info("Creating mock controllers...")
            camera_controller = MockCameraController()
            movement_controller = MockMovementController()
            laser_led_controller = MockLaserLEDController()

            self.status_label.setText("Creating CameraLiveViewer...")

            # Create CameraLiveViewer
            logger.info("Creating TestCameraLiveViewer...")
            self.camera_viewer = TestCameraLiveViewer(
                camera_controller,
                laser_led_controller,
                image_controls_window=None
            )

            self.status_label.setText("Creating Sample3DVisualizationWindow...")

            # Create Sample3DVisualizationWindow
            logger.info("Creating TestSample3DVisualizationWindow...")
            try:
                self.viz_window = TestSample3DVisualizationWindow(
                    movement_controller,
                    camera_controller,
                    laser_led_controller
                )
            except Exception as e:
                logger.error(f"Error creating 3D window: {e}")
                logger.info("This is expected if napari is not installed")
                self.status_label.setText(
                    "Note: 3D window requires napari.\n"
                    "Only showing CameraLiveViewer."
                )

            # Position windows side-by-side
            self.status_label.setText("Positioning windows...")
            self.position_windows()

            # Show windows
            logger.info("Showing windows...")
            if self.camera_viewer:
                self.camera_viewer.show()

            if self.viz_window:
                self.viz_window.show()

            self.status_label.setText("Demo running - both windows open")

            # Validate dimensions
            QTimer.singleShot(1000, self.validate_dimensions)

        except Exception as e:
            logger.error(f"Error initializing windows: {e}", exc_info=True)
            self.status_label.setText(f"Error: {e}")

    def position_windows(self):
        """Position windows side-by-side on screen."""
        try:
            # Get primary screen geometry
            screen = QApplication.primaryScreen()
            screen_geometry = screen.availableGeometry()
            screen_width = screen_geometry.width()
            screen_height = screen_geometry.height()

            logger.info(f"Screen dimensions: {screen_width}x{screen_height}")

            # Calculate positions
            # Leave 50px margin on left
            margin_left = 50
            margin_top = 50
            gap = 20  # Gap between windows

            # Position CameraLiveViewer on left
            if self.camera_viewer:
                camera_x = margin_left
                camera_y = margin_top
                self.camera_viewer.move(camera_x, camera_y)
                logger.info(f"CameraLiveViewer positioned at ({camera_x}, {camera_y})")

            # Position Sample3DVisualizationWindow on right
            if self.viz_window and self.camera_viewer:
                viz_x = margin_left + self.camera_viewer.width() + gap
                viz_y = margin_top
                self.viz_window.move(viz_x, viz_y)
                logger.info(f"Sample3DVisualizationWindow positioned at ({viz_x}, {viz_y})")

                # Calculate total width used
                total_width = viz_x + self.viz_window.width()
                logger.info(f"Total width used: {total_width}px of {screen_width}px available")

        except Exception as e:
            logger.error(f"Error positioning windows: {e}", exc_info=True)

    def validate_dimensions(self):
        """Validate that window dimensions match Agent 1 specifications."""
        logger.info("\n" + "="*60)
        logger.info("DIMENSION VALIDATION")
        logger.info("="*60)

        # Check CameraLiveViewer
        if self.camera_viewer:
            camera_width = self.camera_viewer.width()
            camera_height = self.camera_viewer.height()

            target_width = 660
            target_height = 730

            camera_match = (
                abs(camera_width - target_width) <= 50 and
                abs(camera_height - target_height) <= 50
            )

            logger.info(f"CameraLiveViewer:")
            logger.info(f"  Current:  {camera_width}x{camera_height}")
            logger.info(f"  Target:   {target_width}x{target_height}")
            logger.info(f"  Status:   {'✓ PASS' if camera_match else '✗ FAIL (needs adjustment)'}")

        # Check Sample3DVisualizationWindow
        if self.viz_window:
            viz_width = self.viz_window.width()
            viz_height = self.viz_window.height()

            target_width = 950
            target_height = 800

            viz_match = (
                abs(viz_width - target_width) <= 50 and
                abs(viz_height - target_height) <= 50
            )

            logger.info(f"\nSample3DVisualizationWindow:")
            logger.info(f"  Current:  {viz_width}x{viz_height}")
            logger.info(f"  Target:   {target_width}x{target_height}")
            logger.info(f"  Status:   {'✓ PASS' if viz_match else '✗ FAIL (needs adjustment)'}")

        # Check total width
        if self.camera_viewer and self.viz_window:
            total_width = self.camera_viewer.width() + 20 + self.viz_window.width()
            target_total = 660 + 20 + 950  # 1630 with gap

            screen = QApplication.primaryScreen()
            screen_width = screen.availableGeometry().width()

            fits = total_width <= screen_width

            logger.info(f"\nTotal Layout:")
            logger.info(f"  Combined width: {total_width}px")
            logger.info(f"  Target width:   {target_total}px")
            logger.info(f"  Screen width:   {screen_width}px")
            logger.info(f"  Fits on screen: {'✓ YES' if fits else '✗ NO'}")

        logger.info("="*60 + "\n")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Main entry point for integration demo."""
    logger.info("Starting GUI Redesign Integration Demo")

    # Create application
    app = QApplication(sys.argv)

    # Create and show demo launcher
    demo = IntegrationDemo()
    demo.show()

    # Run application
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
