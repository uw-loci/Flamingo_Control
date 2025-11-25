"""
Test version of CameraLiveViewer with redesigned layout.

This file contains a test implementation that inherits from the original
CameraLiveViewer and overrides only the layout (_setup_ui method).

IMPORTANT: This does NOT modify the original camera_live_viewer.py file.
The original CameraLiveViewer remains fully functional.

Usage:
    from test_gui_redesign.test_camera_live_viewer import TestCameraLiveViewer

    # Use exactly like the original
    viewer = TestCameraLiveViewer(camera_controller, laser_led_controller, image_controls_window)
    viewer.show()

Changes from original:
    - Layout restructured per Agent 1's design specifications
    - Window dimensions adjusted to fit side-by-side with 3D viewer
    - All original functionality preserved
    - All signal connections unchanged
"""

import logging
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QSlider, QSpinBox, QCheckBox, QSizePolicy
)
from PyQt5.QtCore import Qt

# Import the original CameraLiveViewer
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))
from py2flamingo.views.camera_live_viewer import CameraLiveViewer


class TestCameraLiveViewer(CameraLiveViewer):
    """
    Test version of CameraLiveViewer with redesigned layout.

    This class inherits from the original CameraLiveViewer and overrides
    only the _setup_ui() method to implement the new layout design.

    All other methods (signal handling, image display, etc.) are inherited
    unchanged from the parent class.
    """

    def __init__(self, camera_controller, laser_led_controller=None, image_controls_window=None, parent=None):
        """
        Initialize test camera live viewer.

        Args:
            camera_controller: CameraController instance
            laser_led_controller: Optional LaserLEDController for light source control
            image_controls_window: Optional ImageControlsWindow for slider feedback
            parent: Parent widget
        """
        # Call parent constructor which will call _setup_ui()
        # Our override of _setup_ui will be used
        super().__init__(camera_controller, laser_led_controller, image_controls_window, parent)

        # Adjust window size for new layout
        # TODO: Update these dimensions based on Agent 1's specifications
        self.setMinimumSize(600, 700)  # Narrower, taller for side-by-side display

        self.logger.info("TestCameraLiveViewer initialized with redesigned layout")

    def _setup_ui(self) -> None:
        """
        Override parent's _setup_ui to implement new layout.

        This method is called by parent's __init__, so we override it
        to create our new layout instead.
        """
        self.test_setup_ui()

    def test_setup_ui(self) -> None:
        """
        Create and layout UI components with new design.

        LAYOUT DESIGN:
        TODO: Implement Agent 1's specific layout design here.

        Current placeholder implements a basic vertical layout:
        - Image display on top (full width)
        - Controls underneath (horizontally arranged)

        This should be updated based on Agent 1's final design specifications.
        """
        # ====================================================================
        # AGENT 1 DESIGN IMPLEMENTATION SECTION
        # ====================================================================
        # TODO: Replace this placeholder with Agent 1's approved design
        #
        # Key considerations:
        # 1. Target window width: ??? pixels (from Agent 1)
        # 2. Control arrangement: Vertical, horizontal, or hybrid?
        # 3. Control grouping: How to organize buttons, sliders, info?
        # 4. Image display size: What minimum/maximum dimensions?
        #
        # All widgets from original are available to use:
        # - self.image_label (QLabel for image display)
        # - self.start_btn, self.stop_btn, self.snapshot_btn (QPushButton)
        # - self.exposure_spinbox (QSpinBox)
        # - self.exposure_ms_label (QLabel)
        # - self.status_label, self.img_info_label, self.fps_label (QLabel)
        # - self.laser_led_panel (LaserLEDControlPanel, if available)
        # - self.image_controls_btn (QPushButton)
        #
        # DO NOT recreate these widgets - they are created in this method.
        # Just arrange them in the layout per Agent 1's design.
        # ====================================================================

        # Main vertical layout: Image on top, controls below
        main_layout = QVBoxLayout()
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # ===== TOP: Image Display =====
        display_group = QGroupBox("Live Image")
        display_layout = QVBoxLayout()

        self.image_label = QLabel("No image - Click 'Start Live View'")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(480, 360)  # Smaller minimum for compact layout
        self.image_label.setStyleSheet("QLabel { background-color: black; color: gray; border: 2px solid gray; }")
        self.image_label.setScaledContents(False)
        display_layout.addWidget(self.image_label)

        display_group.setLayout(display_layout)
        main_layout.addWidget(display_group, stretch=1)

        # ===== BOTTOM: Controls (Horizontal arrangement) =====
        controls_container = QHBoxLayout()
        controls_container.setSpacing(5)

        # --- Left: Camera Controls ---
        camera_controls_group = QGroupBox("Camera Controls")
        camera_layout = QVBoxLayout()
        camera_layout.setSpacing(3)

        # Live view control buttons
        lv_layout = QHBoxLayout()

        self.start_btn = QPushButton("Start")
        self.start_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 6px;")
        self.start_btn.clicked.connect(self._on_start_clicked)
        lv_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 6px;")
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.stop_btn.setEnabled(False)
        lv_layout.addWidget(self.stop_btn)

        self.snapshot_btn = QPushButton("Snapshot")
        self.snapshot_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 6px;")
        self.snapshot_btn.clicked.connect(self._on_snapshot_clicked)
        lv_layout.addWidget(self.snapshot_btn)

        camera_layout.addLayout(lv_layout)

        # Exposure time control
        exp_layout = QHBoxLayout()
        exp_layout.addWidget(QLabel("Exposure:"))

        self.exposure_spinbox = QSpinBox()
        self.exposure_spinbox.setRange(100, 1000000)
        self.exposure_spinbox.setValue(10000)
        self.exposure_spinbox.setSingleStep(1000)
        self.exposure_spinbox.valueChanged.connect(self._on_exposure_changed)
        self.exposure_spinbox.setSuffix(" µs")
        exp_layout.addWidget(self.exposure_spinbox)

        self.exposure_ms_label = QLabel("10.0 ms")
        exp_layout.addWidget(self.exposure_ms_label)

        camera_layout.addLayout(exp_layout)

        # Image Controls button
        self.image_controls_btn = QPushButton("Image Controls")
        self.image_controls_btn.setStyleSheet(
            "background-color: #9C27B0; color: white; font-weight: bold; padding: 6px;"
        )
        self.image_controls_btn.clicked.connect(self._on_image_controls_clicked)
        camera_layout.addWidget(self.image_controls_btn)

        camera_controls_group.setLayout(camera_layout)
        controls_container.addWidget(camera_controls_group)

        # --- Middle: Laser/LED Panel (if available) ---
        if self.laser_led_controller:
            from py2flamingo.views.laser_led_control_panel import LaserLEDControlPanel
            self.laser_led_panel = LaserLEDControlPanel(self.laser_led_controller)
            controls_container.addWidget(self.laser_led_panel)

        # --- Right: Image Information ---
        info_group = QGroupBox("Image Info")
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        # Status
        status_layout = QHBoxLayout()
        status_layout.addWidget(QLabel("Status:"))
        self.status_label = QLabel("Idle")
        self.status_label.setStyleSheet("color: gray; font-weight: bold;")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        info_layout.addLayout(status_layout)

        # Image info
        img_info_layout = QHBoxLayout()
        img_info_layout.addWidget(QLabel("Image:"))
        self.img_info_label = QLabel("--")
        img_info_layout.addWidget(self.img_info_label)
        img_info_layout.addStretch()
        info_layout.addLayout(img_info_layout)

        # Frame rate
        fps_layout = QHBoxLayout()
        fps_layout.addWidget(QLabel("FPS:"))
        self.fps_label = QLabel("-- FPS")
        self.fps_label.setStyleSheet("font-weight: bold;")
        fps_layout.addWidget(self.fps_label)
        fps_layout.addStretch()
        info_layout.addLayout(fps_layout)

        # Actual exposure
        exp_actual_layout = QHBoxLayout()
        exp_actual_layout.addWidget(QLabel("Exp:"))
        self.actual_exposure_label = QLabel("--")
        exp_actual_layout.addWidget(self.actual_exposure_label)
        exp_actual_layout.addStretch()
        info_layout.addLayout(exp_actual_layout)

        # Intensity range
        intensity_layout = QHBoxLayout()
        intensity_layout.addWidget(QLabel("Range:"))
        self.intensity_label = QLabel("--")
        intensity_layout.addWidget(self.intensity_label)
        self.auto_scale_warning = QLabel("")
        self.auto_scale_warning.setStyleSheet("color: red; font-weight: bold;")
        intensity_layout.addWidget(self.auto_scale_warning)
        intensity_layout.addStretch()
        info_layout.addLayout(intensity_layout)

        info_group.setLayout(info_layout)
        controls_container.addWidget(info_group)

        # Add controls to main layout
        main_layout.addLayout(controls_container)

        self.setLayout(main_layout)

        # ====================================================================
        # END AGENT 1 DESIGN IMPLEMENTATION SECTION
        # ====================================================================

        self.logger.info("Test UI setup complete - awaiting Agent 1's final design")


# ============================================================================
# DEMONSTRATION AND TESTING
# ============================================================================

def main():
    """
    Standalone test of TestCameraLiveViewer.

    This requires a mock camera controller for testing without hardware.
    """
    from PyQt5.QtWidgets import QApplication
    import sys

    # TODO: Add mock camera controller for standalone testing
    print("TestCameraLiveViewer template created.")
    print("This file demonstrates the structure for the test implementation.")
    print("")
    print("Next steps:")
    print("1. Agent 1 completes design specifications")
    print("2. Update test_setup_ui() to implement Agent 1's design")
    print("3. Test with real camera controller")
    print("4. Verify all functionality preserved")
    print("5. Measure window dimensions and adjust as needed")


if __name__ == "__main__":
    main()
