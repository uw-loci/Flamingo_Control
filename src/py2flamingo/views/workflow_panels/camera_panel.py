"""
Camera panel for workflow configuration.

Provides UI for exposure time and frame rate display.
Advanced settings (AOI, dual camera capture) available via dialog.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QGroupBox, QGridLayout, QPushButton
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
    - Exposure time input (microseconds)
    - Frame rate display (calculated from exposure)
    - Advanced button for AOI and dual camera capture settings

    Signals:
        settings_changed: Emitted when camera settings change
    """

    settings_changed = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize camera panel.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)

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

        # Header with Advanced button
        header_layout = QHBoxLayout()
        header_layout.addStretch()

        self._advanced_btn = QPushButton("Advanced...")
        self._advanced_btn.setFixedWidth(90)
        self._advanced_btn.clicked.connect(self._on_advanced_clicked)
        header_layout.addWidget(self._advanced_btn)
        group_layout.addLayout(header_layout)

        # Main settings row
        settings_layout = QHBoxLayout()
        settings_layout.setSpacing(16)

        # Exposure time
        exp_layout = QHBoxLayout()
        exp_layout.setSpacing(4)
        exp_layout.addWidget(QLabel("Exposure:"))
        self._exposure_spinbox = QDoubleSpinBox()
        self._exposure_spinbox.setRange(0.1, 100000.0)  # 0.1us to 100ms
        self._exposure_spinbox.setValue(10000.0)  # Default 10ms
        self._exposure_spinbox.setDecimals(1)
        self._exposure_spinbox.setSingleStep(100.0)
        self._exposure_spinbox.setSuffix(" us")
        self._exposure_spinbox.setFixedWidth(110)
        self._exposure_spinbox.valueChanged.connect(self._on_exposure_changed)
        exp_layout.addWidget(self._exposure_spinbox)
        settings_layout.addLayout(exp_layout)

        # Frame rate (calculated, read-only display)
        fps_layout = QHBoxLayout()
        fps_layout.setSpacing(4)
        fps_layout.addWidget(QLabel("Frame Rate:"))
        self._framerate_label = QLabel("100.0 fps")
        self._framerate_label.setStyleSheet("font-weight: bold; color: #27ae60;")
        fps_layout.addWidget(self._framerate_label)
        settings_layout.addLayout(fps_layout)

        settings_layout.addStretch()
        group_layout.addLayout(settings_layout)

        # AOI info (compact display of current setting)
        self._aoi_info = QLabel(f"AOI: {self._aoi_width}x{self._aoi_height}")
        self._aoi_info.setStyleSheet("color: gray; font-size: 9pt;")
        group_layout.addWidget(self._aoi_info)

        group.setLayout(group_layout)
        layout.addWidget(group)

        # Initial frame rate calculation
        self._update_framerate()

    def _on_exposure_changed(self) -> None:
        """Handle exposure time change."""
        self._update_framerate()
        self._on_settings_changed()

    def _update_framerate(self) -> None:
        """Update frame rate display based on exposure time."""
        exposure_us = self._exposure_spinbox.value()
        if exposure_us > 0:
            exposure_s = exposure_us / 1_000_000.0
            # Cap frame rate at 40 fps (hardware limit)
            framerate = min(1.0 / exposure_s, 40.0)
            self._framerate_label.setText(f"{framerate:.1f} fps")
        else:
            self._framerate_label.setText("N/A")

    def _on_advanced_clicked(self) -> None:
        """Open advanced camera settings dialog."""
        from py2flamingo.views.dialogs import AdvancedCameraDialog

        dialog = AdvancedCameraDialog(self)
        dialog.set_settings({
            'aoi_width': self._aoi_width,
            'aoi_height': self._aoi_height,
            'cam1_capture_percentage': self._cam1_percentage,
            'cam1_capture_mode': self._cam1_mode,
            'cam2_capture_percentage': self._cam2_percentage,
            'cam2_capture_mode': self._cam2_mode,
        })

        if dialog.exec_() == dialog.Accepted:
            settings = dialog.get_settings()
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
        exposure_us = self._exposure_spinbox.value()
        exposure_s = exposure_us / 1_000_000.0
        # Cap frame rate at 40 fps (hardware limit)
        framerate = min(1.0 / exposure_s, 40.0) if exposure_s > 0 else 0

        return {
            'exposure_us': exposure_us,
            'frame_rate': framerate,
            'aoi_width': self._aoi_width,
            'aoi_height': self._aoi_height,
            'cam1_capture_percentage': self._cam1_percentage,
            'cam1_capture_mode': self._cam1_mode,
            'cam2_capture_percentage': self._cam2_percentage,
            'cam2_capture_mode': self._cam2_mode,
        }

    def get_exposure_us(self) -> float:
        """Get exposure time in microseconds."""
        return self._exposure_spinbox.value()

    def get_frame_rate(self) -> float:
        """Get calculated frame rate (capped at 40 fps)."""
        exposure_us = self._exposure_spinbox.value()
        if exposure_us > 0:
            # Cap frame rate at 40 fps (hardware limit)
            return min(1_000_000.0 / exposure_us, 40.0)
        return 0.0

    def set_exposure(self, exposure_us: float) -> None:
        """Set exposure time."""
        self._exposure_spinbox.setValue(exposure_us)

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
