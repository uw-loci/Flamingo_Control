"""
Camera panel for workflow configuration.

Provides UI for exposure time, frame rate, AOI, and camera capture settings.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QSpinBox, QGroupBox, QGridLayout, QComboBox
)
from PyQt5.QtCore import pyqtSignal


# AOI preset configurations
AOI_PRESETS = {
    "Full Frame (2048x2048)": (2048, 2048),
    "Half (1024x1024)": (1024, 1024),
    "Quarter (512x512)": (512, 512),
    "Custom": None,
}

# Camera capture mode options
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
    - AOI (Area of Interest) configuration
    - Camera capture settings (percentage and mode)

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
        self._updating_preset = False  # Flag to prevent recursion
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Camera settings group
        group = QGroupBox("Camera Settings")
        grid = QGridLayout()
        grid.setSpacing(8)

        # Exposure time (microseconds)
        grid.addWidget(QLabel("Exposure Time:"), 0, 0)
        self._exposure_spinbox = QDoubleSpinBox()
        self._exposure_spinbox.setRange(0.1, 100000.0)  # 0.1us to 100ms
        self._exposure_spinbox.setValue(10000.0)  # Default 10ms
        self._exposure_spinbox.setDecimals(1)
        self._exposure_spinbox.setSingleStep(100.0)
        self._exposure_spinbox.setSuffix(" us")
        self._exposure_spinbox.valueChanged.connect(self._on_exposure_changed)
        grid.addWidget(self._exposure_spinbox, 0, 1)

        # Frame rate (calculated, read-only display)
        grid.addWidget(QLabel("Frame Rate:"), 1, 0)
        self._framerate_label = QLabel("100.0 fps")
        self._framerate_label.setStyleSheet("font-weight: bold;")
        grid.addWidget(self._framerate_label, 1, 1)

        # Info about frame rate
        info_label = QLabel("Frame rate is calculated from exposure time")
        info_label.setStyleSheet("color: gray; font-size: 9pt;")
        grid.addWidget(info_label, 2, 0, 1, 2)

        group.setLayout(grid)
        layout.addWidget(group)

        # AOI (Area of Interest) group
        aoi_group = QGroupBox("Area of Interest (AOI)")
        aoi_grid = QGridLayout()
        aoi_grid.setSpacing(8)

        # AOI Width
        aoi_grid.addWidget(QLabel("Width:"), 0, 0)
        self._aoi_width = QSpinBox()
        self._aoi_width.setRange(1, 2048)
        self._aoi_width.setValue(2048)
        self._aoi_width.setSuffix(" px")
        self._aoi_width.valueChanged.connect(self._on_aoi_changed)
        aoi_grid.addWidget(self._aoi_width, 0, 1)

        # AOI Height
        aoi_grid.addWidget(QLabel("Height:"), 1, 0)
        self._aoi_height = QSpinBox()
        self._aoi_height.setRange(1, 2048)
        self._aoi_height.setValue(2048)
        self._aoi_height.setSuffix(" px")
        self._aoi_height.valueChanged.connect(self._on_aoi_changed)
        aoi_grid.addWidget(self._aoi_height, 1, 1)

        # AOI Preset dropdown
        aoi_grid.addWidget(QLabel("Preset:"), 2, 0)
        self._aoi_preset = QComboBox()
        for preset_name in AOI_PRESETS.keys():
            self._aoi_preset.addItem(preset_name)
        self._aoi_preset.setCurrentIndex(0)  # Default to Full Frame
        self._aoi_preset.currentIndexChanged.connect(self._on_preset_changed)
        aoi_grid.addWidget(self._aoi_preset, 2, 1)

        aoi_group.setLayout(aoi_grid)
        layout.addWidget(aoi_group)

        # Camera Capture group
        capture_group = QGroupBox("Camera Capture Settings")
        capture_grid = QGridLayout()
        capture_grid.setSpacing(8)

        # Camera 1 Capture Percentage
        capture_grid.addWidget(QLabel("Camera 1 Percentage:"), 0, 0)
        self._cam1_percentage = QSpinBox()
        self._cam1_percentage.setRange(0, 100)
        self._cam1_percentage.setValue(100)
        self._cam1_percentage.setSuffix(" %")
        self._cam1_percentage.valueChanged.connect(self._on_settings_changed)
        capture_grid.addWidget(self._cam1_percentage, 0, 1)

        # Camera 1 Capture Mode
        capture_grid.addWidget(QLabel("Camera 1 Mode:"), 1, 0)
        self._cam1_mode = QComboBox()
        for mode_name, _ in CAPTURE_MODES:
            self._cam1_mode.addItem(mode_name)
        self._cam1_mode.setCurrentIndex(0)  # Default to Full Stack
        self._cam1_mode.currentIndexChanged.connect(self._on_settings_changed)
        capture_grid.addWidget(self._cam1_mode, 1, 1)

        # Camera 2 Capture Percentage
        capture_grid.addWidget(QLabel("Camera 2 Percentage:"), 2, 0)
        self._cam2_percentage = QSpinBox()
        self._cam2_percentage.setRange(0, 100)
        self._cam2_percentage.setValue(100)
        self._cam2_percentage.setSuffix(" %")
        self._cam2_percentage.valueChanged.connect(self._on_settings_changed)
        capture_grid.addWidget(self._cam2_percentage, 2, 1)

        # Camera 2 Capture Mode
        capture_grid.addWidget(QLabel("Camera 2 Mode:"), 3, 0)
        self._cam2_mode = QComboBox()
        for mode_name, _ in CAPTURE_MODES:
            self._cam2_mode.addItem(mode_name)
        self._cam2_mode.setCurrentIndex(0)  # Default to Full Stack
        self._cam2_mode.currentIndexChanged.connect(self._on_settings_changed)
        capture_grid.addWidget(self._cam2_mode, 3, 1)

        capture_group.setLayout(capture_grid)
        layout.addWidget(capture_group)

        # Add stretch to push everything to the top
        layout.addStretch()

        # Initial frame rate calculation
        self._update_framerate()

    def _on_exposure_changed(self) -> None:
        """Handle exposure time change."""
        self._update_framerate()
        self._on_settings_changed()

    def _on_preset_changed(self, index: int) -> None:
        """Handle AOI preset selection change."""
        if self._updating_preset:
            return

        preset_name = self._aoi_preset.currentText()
        preset_size = AOI_PRESETS[preset_name]

        if preset_size is not None:
            # Update width and height based on preset
            self._updating_preset = True
            self._aoi_width.setValue(preset_size[0])
            self._aoi_height.setValue(preset_size[1])
            self._updating_preset = False
            self._on_settings_changed()

    def _on_aoi_changed(self) -> None:
        """Handle manual AOI width/height change."""
        if self._updating_preset:
            return

        # Check if current values match any preset
        current_width = self._aoi_width.value()
        current_height = self._aoi_height.value()

        for preset_name, preset_size in AOI_PRESETS.items():
            if preset_size is not None and preset_size == (current_width, current_height):
                self._updating_preset = True
                self._aoi_preset.setCurrentText(preset_name)
                self._updating_preset = False
                self._on_settings_changed()
                return

        # No matching preset, set to Custom
        self._updating_preset = True
        self._aoi_preset.setCurrentText("Custom")
        self._updating_preset = False
        self._on_settings_changed()

    def _on_settings_changed(self) -> None:
        """Handle any settings change."""
        settings = self.get_settings()
        self.settings_changed.emit(settings)

    def _update_framerate(self) -> None:
        """Update frame rate display based on exposure time."""
        exposure_us = self._exposure_spinbox.value()
        if exposure_us > 0:
            # Frame rate = 1 / exposure time
            # exposure_us is in microseconds, convert to seconds
            exposure_s = exposure_us / 1_000_000.0
            framerate = 1.0 / exposure_s
            self._framerate_label.setText(f"{framerate:.1f} fps")
        else:
            self._framerate_label.setText("N/A")

    def get_settings(self) -> Dict[str, Any]:
        """
        Get current camera settings.

        Returns:
            Dictionary with camera settings
        """
        exposure_us = self._exposure_spinbox.value()
        exposure_s = exposure_us / 1_000_000.0
        framerate = 1.0 / exposure_s if exposure_s > 0 else 0

        _, cam1_mode_value = CAPTURE_MODES[self._cam1_mode.currentIndex()]
        _, cam2_mode_value = CAPTURE_MODES[self._cam2_mode.currentIndex()]

        return {
            'exposure_us': exposure_us,
            'frame_rate': framerate,
            'aoi_width': self._aoi_width.value(),
            'aoi_height': self._aoi_height.value(),
            'cam1_capture_percentage': self._cam1_percentage.value(),
            'cam1_capture_mode': cam1_mode_value,
            'cam2_capture_percentage': self._cam2_percentage.value(),
            'cam2_capture_mode': cam2_mode_value,
        }

    def get_exposure_us(self) -> float:
        """
        Get exposure time in microseconds.

        Returns:
            Exposure time in microseconds
        """
        return self._exposure_spinbox.value()

    def get_frame_rate(self) -> float:
        """
        Get calculated frame rate.

        Returns:
            Frame rate in frames per second
        """
        exposure_us = self._exposure_spinbox.value()
        if exposure_us > 0:
            return 1_000_000.0 / exposure_us
        return 0.0

    def set_exposure(self, exposure_us: float) -> None:
        """
        Set exposure time.

        Args:
            exposure_us: Exposure time in microseconds
        """
        self._exposure_spinbox.setValue(exposure_us)

    def set_aoi(self, width: int, height: int) -> None:
        """
        Set AOI dimensions.

        Args:
            width: AOI width in pixels
            height: AOI height in pixels
        """
        self._aoi_width.setValue(width)
        self._aoi_height.setValue(height)

    def set_camera_capture(
        self,
        cam1_percentage: int = 100,
        cam1_mode: int = 0,
        cam2_percentage: int = 100,
        cam2_mode: int = 0
    ) -> None:
        """
        Set camera capture settings.

        Args:
            cam1_percentage: Camera 1 capture percentage (0-100)
            cam1_mode: Camera 1 capture mode (0-3)
            cam2_percentage: Camera 2 capture percentage (0-100)
            cam2_mode: Camera 2 capture mode (0-3)
        """
        self._cam1_percentage.setValue(cam1_percentage)
        self._cam1_mode.setCurrentIndex(cam1_mode)
        self._cam2_percentage.setValue(cam2_percentage)
        self._cam2_mode.setCurrentIndex(cam2_mode)
