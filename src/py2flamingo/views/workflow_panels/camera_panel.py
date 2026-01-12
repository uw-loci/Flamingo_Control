"""
Camera panel for workflow configuration.

Provides auto-detection of camera settings with frame rate and exposure display.
Advanced settings (exposure, AOI, dual camera capture) available via dialog.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QGroupBox, QGridLayout, QPushButton, QMessageBox
)
from PyQt5.QtCore import pyqtSignal


# AOI preset configurations (used by dialog)
AOI_PRESETS = {
    "Full Frame (2048x2048)": (2048, 2048),
    "Half (1024x1024)": (1024, 1024),
    "Quarter (512x512)": (512, 512),
    "Custom": None,
}

# Camera capture mode options (used by dialog)
CAPTURE_MODES = [
    ("Full Stack", 0),
    ("Front Half", 1),
    ("Back Half", 2),
    ("None", 3),
]


class CameraPanel(QWidget):
    """
    Panel for configuring camera settings for workflows.

    Provides:
    - Auto-detection of camera exposure/frame rate
    - Detected settings display (read-only in main panel)
    - Camera button to trigger detection or show warning
    - Advanced button for exposure, AOI and dual camera capture settings

    Signals:
        settings_changed: Emitted when camera settings change
    """

    settings_changed = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None, app=None):
        """
        Initialize camera panel.

        Args:
            parent: Parent widget
            app: FlamingoApplication instance for camera service access
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._app = app

        # Auto-detection state
        self._auto_detected = False
        self._detection_warning = ""

        # Camera settings (detected or manually set)
        self._exposure_us = 10000.0  # Default 10ms
        self._frame_rate = 40.0  # Default (will be calculated from exposure)

        # Advanced settings (stored here, edited via dialog)
        self._aoi_width = 2048
        self._aoi_height = 2048
        self._cam1_percentage = 100
        self._cam1_mode = 0
        self._cam2_percentage = 100
        self._cam2_mode = 0

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Camera settings group
        group = QGroupBox("Camera")
        group_layout = QVBoxLayout()
        group_layout.setSpacing(8)

        # Header with Camera and Advanced buttons
        header_layout = QHBoxLayout()

        self._camera_btn = QPushButton("Camera")
        self._camera_btn.setFixedWidth(70)
        self._camera_btn.setToolTip("Detect camera settings from microscope")
        self._camera_btn.clicked.connect(self._on_camera_clicked)
        header_layout.addWidget(self._camera_btn)

        header_layout.addStretch()

        self._advanced_btn = QPushButton("Advanced...")
        self._advanced_btn.setFixedWidth(90)
        self._advanced_btn.clicked.connect(self._on_advanced_clicked)
        header_layout.addWidget(self._advanced_btn)
        group_layout.addLayout(header_layout)

        # Main settings row - detected values (read-only display)
        settings_layout = QHBoxLayout()
        settings_layout.setSpacing(16)

        # Detected settings display
        self._detected_label = QLabel("Detected: -- fps @ -- us")
        self._detected_label.setStyleSheet("font-weight: bold; color: #27ae60;")
        self._detected_label.setToolTip("Auto-detected from camera. Click 'Camera' to refresh.")
        settings_layout.addWidget(self._detected_label)

        settings_layout.addStretch()
        group_layout.addLayout(settings_layout)

        # Warning indicator (hidden by default)
        self._warning_indicator = QLabel("")
        self._warning_indicator.setStyleSheet("color: #e67e22; font-size: 9pt;")
        self._warning_indicator.setVisible(False)
        group_layout.addWidget(self._warning_indicator)

        # AOI info (compact display of current setting)
        self._aoi_info = QLabel(f"AOI: {self._aoi_width}x{self._aoi_height}")
        self._aoi_info.setStyleSheet("color: gray; font-size: 9pt;")
        group_layout.addWidget(self._aoi_info)

        group.setLayout(group_layout)
        layout.addWidget(group)

        # Update display with default values
        self._update_detected_display()

    def _update_detected_display(self) -> None:
        """Update the detected settings display."""
        self._detected_label.setText(
            f"Detected: {self._frame_rate:.1f} fps @ {self._exposure_us:.0f} us"
        )

        if self._auto_detected:
            self._detected_label.setStyleSheet("font-weight: bold; color: #27ae60;")
            self._camera_btn.setStyleSheet("")
        else:
            self._detected_label.setStyleSheet("font-weight: bold; color: #666;")
            if self._detection_warning:
                self._camera_btn.setStyleSheet("background-color: #fff3cd;")

    def _on_camera_clicked(self) -> None:
        """Handle Camera button click - attempt detection or show warning."""
        success = self.detect_camera_settings()
        if not success:
            QMessageBox.warning(
                self,
                "Camera Detection",
                f"Could not detect camera settings.\n\n"
                f"Reason: {self._detection_warning}\n\n"
                "You can set exposure manually in Advanced settings."
            )

    def detect_camera_settings(self) -> bool:
        """
        Auto-detect camera settings from hardware.

        Queries the camera service to get current exposure time,
        then calculates frame rate from exposure.

        Returns:
            True if detection succeeded, False otherwise
        """
        if not self._app:
            self._detection_warning = "No application context"
            self._auto_detected = False
            self._update_detected_display()
            return False

        camera_service = getattr(self._app, 'camera_service', None)
        if not camera_service:
            self._detection_warning = "Camera service not available"
            self._auto_detected = False
            self._update_detected_display()
            return False

        try:
            # Query exposure from camera
            exposure_us = camera_service.get_exposure()
            self._exposure_us = exposure_us

            # Calculate frame rate (capped at 40 fps)
            if exposure_us > 0:
                exposure_s = exposure_us / 1_000_000.0
                self._frame_rate = min(1.0 / exposure_s, 40.0)

            self._auto_detected = True
            self._detection_warning = ""
            self._warning_indicator.setVisible(False)
            self._update_detected_display()

            self._logger.info(f"Camera settings detected: {self._exposure_us} us, {self._frame_rate:.1f} fps")
            self._on_settings_changed()
            return True

        except Exception as e:
            self._detection_warning = str(e)
            self._auto_detected = False
            self._warning_indicator.setText(f"Warning: {e}")
            self._warning_indicator.setVisible(True)
            self._update_detected_display()
            self._logger.warning(f"Camera detection failed: {e}")
            return False

    def _on_advanced_clicked(self) -> None:
        """Open advanced camera settings dialog."""
        from py2flamingo.views.dialogs import AdvancedCameraDialog

        dialog = AdvancedCameraDialog(self)
        dialog.set_settings({
            'exposure_us': self._exposure_us,
            'aoi_width': self._aoi_width,
            'aoi_height': self._aoi_height,
            'cam1_capture_percentage': self._cam1_percentage,
            'cam1_capture_mode': self._cam1_mode,
            'cam2_capture_percentage': self._cam2_percentage,
            'cam2_capture_mode': self._cam2_mode,
        })

        if dialog.exec_() == dialog.Accepted:
            settings = dialog.get_settings()

            # Update exposure if changed
            if 'exposure_us' in settings:
                self._exposure_us = settings['exposure_us']
                # Recalculate frame rate
                if self._exposure_us > 0:
                    exposure_s = self._exposure_us / 1_000_000.0
                    self._frame_rate = min(1.0 / exposure_s, 40.0)
                self._update_detected_display()

            self._aoi_width = settings['aoi_width']
            self._aoi_height = settings['aoi_height']
            self._cam1_percentage = settings['cam1_capture_percentage']
            self._cam1_mode = settings['cam1_capture_mode']
            self._cam2_percentage = settings['cam2_capture_percentage']
            self._cam2_mode = settings['cam2_capture_mode']

            # Update AOI info display
            self._aoi_info.setText(f"AOI: {self._aoi_width}x{self._aoi_height}")
            self._on_settings_changed()

    def _on_settings_changed(self) -> None:
        """Handle any settings change."""
        settings = self.get_settings()
        self.settings_changed.emit(settings)

    def get_settings(self) -> Dict[str, Any]:
        """
        Get current camera settings.

        Returns:
            Dictionary with camera settings
        """
        return {
            'exposure_us': self._exposure_us,
            'frame_rate': self._frame_rate,
            'aoi_width': self._aoi_width,
            'aoi_height': self._aoi_height,
            'cam1_capture_percentage': self._cam1_percentage,
            'cam1_capture_mode': self._cam1_mode,
            'cam2_capture_percentage': self._cam2_percentage,
            'cam2_capture_mode': self._cam2_mode,
        }

    def set_settings(self, settings: Dict[str, Any]) -> None:
        """
        Set camera settings from dictionary.

        Used for restoring settings from persistence.

        Args:
            settings: Dictionary with camera settings
        """
        if not settings:
            return

        if 'exposure_us' in settings:
            self._exposure_us = settings['exposure_us']
            # Recalculate frame rate from exposure
            if self._exposure_us > 0:
                exposure_s = self._exposure_us / 1_000_000.0
                self._frame_rate = min(1.0 / exposure_s, 40.0)

        if 'frame_rate' in settings:
            # Override with stored frame rate if provided
            self._frame_rate = settings['frame_rate']

        if 'aoi_width' in settings:
            self._aoi_width = settings['aoi_width']
        if 'aoi_height' in settings:
            self._aoi_height = settings['aoi_height']
        if 'cam1_capture_percentage' in settings:
            self._cam1_percentage = settings['cam1_capture_percentage']
        if 'cam1_capture_mode' in settings:
            self._cam1_mode = settings['cam1_capture_mode']
        if 'cam2_capture_percentage' in settings:
            self._cam2_percentage = settings['cam2_capture_percentage']
        if 'cam2_capture_mode' in settings:
            self._cam2_mode = settings['cam2_capture_mode']

        # Update displays
        self._update_detected_display()
        self._aoi_info.setText(f"AOI: {self._aoi_width}x{self._aoi_height}")

    def get_exposure_us(self) -> float:
        """Get exposure time in microseconds."""
        return self._exposure_us

    def get_frame_rate(self) -> float:
        """Get frame rate (capped at 40 fps)."""
        return self._frame_rate

    def set_exposure(self, exposure_us: float) -> None:
        """Set exposure time."""
        self._exposure_us = exposure_us
        # Recalculate frame rate
        if exposure_us > 0:
            exposure_s = exposure_us / 1_000_000.0
            self._frame_rate = min(1.0 / exposure_s, 40.0)
        self._update_detected_display()

    def set_aoi(self, width: int, height: int) -> None:
        """Set AOI dimensions."""
        self._aoi_width = width
        self._aoi_height = height
        self._aoi_info.setText(f"AOI: {width}x{height}")

    def set_camera_capture(
        self,
        cam1_percentage: int = 100,
        cam1_mode: int = 0,
        cam2_percentage: int = 100,
        cam2_mode: int = 0
    ) -> None:
        """Set camera capture settings."""
        self._cam1_percentage = cam1_percentage
        self._cam1_mode = cam1_mode
        self._cam2_percentage = cam2_percentage
        self._cam2_mode = cam2_mode

    # Advanced settings accessors
    def get_advanced_settings(self) -> Dict[str, Any]:
        """Get advanced camera settings."""
        return {
            'aoi_width': self._aoi_width,
            'aoi_height': self._aoi_height,
            'cam1_capture_percentage': self._cam1_percentage,
            'cam1_capture_mode': self._cam1_mode,
            'cam2_capture_percentage': self._cam2_percentage,
            'cam2_capture_mode': self._cam2_mode,
        }

    def set_advanced_settings(self, settings: Dict[str, Any]) -> None:
        """Set advanced camera settings."""
        if 'aoi_width' in settings:
            self._aoi_width = settings['aoi_width']
        if 'aoi_height' in settings:
            self._aoi_height = settings['aoi_height']
        if 'cam1_capture_percentage' in settings:
            self._cam1_percentage = settings['cam1_capture_percentage']
        if 'cam1_capture_mode' in settings:
            self._cam1_mode = settings['cam1_capture_mode']
        if 'cam2_capture_percentage' in settings:
            self._cam2_percentage = settings['cam2_capture_percentage']
        if 'cam2_capture_mode' in settings:
            self._cam2_mode = settings['cam2_capture_mode']
        # Update display
        self._aoi_info.setText(f"AOI: {self._aoi_width}x{self._aoi_height}")
