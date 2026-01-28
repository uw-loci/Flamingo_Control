"""Advanced Camera Settings Dialog.

Dialog for configuring rarely-changed camera settings
such as AOI (Area of Interest) and dual camera capture settings.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QSpinBox, QDoubleSpinBox, QComboBox, QGroupBox, QGridLayout,
    QPushButton, QDialogButtonBox
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon


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


class AdvancedCameraDialog(QDialog):
    """Dialog for advanced camera settings.

    Settings included:
    - AOI (Area of Interest) width and height
    - AOI presets
    - Camera 1 capture percentage and mode
    - Camera 2 capture percentage and mode
    """

    def __init__(self, parent: Optional[QDialog] = None):
        """Initialize the dialog.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._updating_preset = False

        self.setWindowTitle("Advanced Camera Settings")
        self.setWindowIcon(QIcon())  # Clear inherited napari icon
        self.setMinimumWidth(400)
        self.setModal(True)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Exposure Section
        exposure_group = QGroupBox("Exposure (Manual Override)")
        exposure_grid = QGridLayout()
        exposure_grid.setSpacing(8)

        exposure_grid.addWidget(QLabel("Exposure:"), 0, 0)
        exp_layout = QHBoxLayout()
        self._exposure_spinbox = QDoubleSpinBox()
        self._exposure_spinbox.setRange(0.1, 100000.0)  # 0.1us to 100ms
        self._exposure_spinbox.setValue(10000.0)  # Default 10ms
        self._exposure_spinbox.setDecimals(1)
        self._exposure_spinbox.setSingleStep(100.0)
        self._exposure_spinbox.setSuffix(" us")
        self._exposure_spinbox.valueChanged.connect(self._on_exposure_changed)
        exp_layout.addWidget(self._exposure_spinbox)

        # Frame rate display (calculated)
        self._fps_label = QLabel("= 40.0 fps")
        self._fps_label.setStyleSheet("color: #27ae60; font-weight: bold;")
        exp_layout.addWidget(self._fps_label)
        exposure_grid.addLayout(exp_layout, 0, 1)

        exposure_info = QLabel(
            "Override auto-detected exposure. Frame rate is calculated from exposure "
            "(capped at 40 fps)."
        )
        exposure_info.setStyleSheet("color: gray; font-size: 9pt;")
        exposure_info.setWordWrap(True)
        exposure_grid.addWidget(exposure_info, 1, 0, 1, 2)

        exposure_group.setLayout(exposure_grid)
        layout.addWidget(exposure_group)

        # AOI Section
        aoi_group = QGroupBox("Area of Interest (AOI)")
        aoi_grid = QGridLayout()
        aoi_grid.setSpacing(8)

        # AOI Preset
        aoi_grid.addWidget(QLabel("Preset:"), 0, 0)
        self._aoi_preset = QComboBox()
        for preset_name in AOI_PRESETS.keys():
            self._aoi_preset.addItem(preset_name)
        self._aoi_preset.setCurrentIndex(0)
        self._aoi_preset.currentIndexChanged.connect(self._on_preset_changed)
        aoi_grid.addWidget(self._aoi_preset, 0, 1)

        # AOI Width
        aoi_grid.addWidget(QLabel("Width:"), 1, 0)
        self._aoi_width = QSpinBox()
        self._aoi_width.setRange(1, 2048)
        self._aoi_width.setValue(2048)
        self._aoi_width.setSuffix(" px")
        self._aoi_width.valueChanged.connect(self._on_aoi_changed)
        aoi_grid.addWidget(self._aoi_width, 1, 1)

        # AOI Height
        aoi_grid.addWidget(QLabel("Height:"), 2, 0)
        self._aoi_height = QSpinBox()
        self._aoi_height.setRange(1, 2048)
        self._aoi_height.setValue(2048)
        self._aoi_height.setSuffix(" px")
        self._aoi_height.valueChanged.connect(self._on_aoi_changed)
        aoi_grid.addWidget(self._aoi_height, 2, 1)

        # Info label
        aoi_info = QLabel("Reducing AOI can increase frame rate for fast acquisitions")
        aoi_info.setStyleSheet("color: gray; font-size: 9pt;")
        aoi_info.setWordWrap(True)
        aoi_grid.addWidget(aoi_info, 3, 0, 1, 2)

        aoi_group.setLayout(aoi_grid)
        layout.addWidget(aoi_group)

        # Dual Camera Capture Section
        capture_group = QGroupBox("Dual Camera Capture")
        capture_grid = QGridLayout()
        capture_grid.setSpacing(8)

        # Camera 1 section
        cam1_label = QLabel("Camera 1:")
        cam1_label.setStyleSheet("font-weight: bold;")
        capture_grid.addWidget(cam1_label, 0, 0, 1, 2)

        capture_grid.addWidget(QLabel("Capture %:"), 1, 0)
        self._cam1_percentage = QSpinBox()
        self._cam1_percentage.setRange(0, 100)
        self._cam1_percentage.setValue(100)
        self._cam1_percentage.setSuffix(" %")
        self._cam1_percentage.setToolTip("Percentage of Z-planes captured by Camera 1")
        capture_grid.addWidget(self._cam1_percentage, 1, 1)

        capture_grid.addWidget(QLabel("Mode:"), 2, 0)
        self._cam1_mode = QComboBox()
        for mode_name, _ in CAPTURE_MODES:
            self._cam1_mode.addItem(mode_name)
        self._cam1_mode.setCurrentIndex(0)
        self._cam1_mode.setToolTip(
            "Full Stack: Capture all planes\n"
            "Front Half: Capture first half of planes\n"
            "Back Half: Capture second half of planes\n"
            "None: Disable this camera"
        )
        capture_grid.addWidget(self._cam1_mode, 2, 1)

        # Camera 2 section
        cam2_label = QLabel("Camera 2:")
        cam2_label.setStyleSheet("font-weight: bold;")
        capture_grid.addWidget(cam2_label, 3, 0, 1, 2)

        capture_grid.addWidget(QLabel("Capture %:"), 4, 0)
        self._cam2_percentage = QSpinBox()
        self._cam2_percentage.setRange(0, 100)
        self._cam2_percentage.setValue(100)
        self._cam2_percentage.setSuffix(" %")
        self._cam2_percentage.setToolTip("Percentage of Z-planes captured by Camera 2")
        capture_grid.addWidget(self._cam2_percentage, 4, 1)

        capture_grid.addWidget(QLabel("Mode:"), 5, 0)
        self._cam2_mode = QComboBox()
        for mode_name, _ in CAPTURE_MODES:
            self._cam2_mode.addItem(mode_name)
        self._cam2_mode.setCurrentIndex(0)
        capture_grid.addWidget(self._cam2_mode, 5, 1)

        # Reset button
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self._reset_defaults)
        capture_grid.addWidget(reset_btn, 6, 1)

        capture_group.setLayout(capture_grid)
        layout.addWidget(capture_group)

        # Add stretch to push buttons to bottom
        layout.addStretch()

        # Dialog buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _on_exposure_changed(self) -> None:
        """Handle exposure time change - update frame rate display."""
        exposure_us = self._exposure_spinbox.value()
        if exposure_us > 0:
            exposure_s = exposure_us / 1_000_000.0
            # Cap frame rate at 40 fps (hardware limit)
            framerate = min(1.0 / exposure_s, 40.0)
            self._fps_label.setText(f"= {framerate:.1f} fps")
        else:
            self._fps_label.setText("= N/A")

    def _on_preset_changed(self, index: int) -> None:
        """Handle AOI preset selection change."""
        if self._updating_preset:
            return

        preset_name = self._aoi_preset.currentText()
        preset_size = AOI_PRESETS[preset_name]

        if preset_size is not None:
            self._updating_preset = True
            self._aoi_width.setValue(preset_size[0])
            self._aoi_height.setValue(preset_size[1])
            self._updating_preset = False

    def _on_aoi_changed(self) -> None:
        """Handle manual AOI width/height change."""
        if self._updating_preset:
            return

        current_width = self._aoi_width.value()
        current_height = self._aoi_height.value()

        for preset_name, preset_size in AOI_PRESETS.items():
            if preset_size is not None and preset_size == (current_width, current_height):
                self._updating_preset = True
                self._aoi_preset.setCurrentText(preset_name)
                self._updating_preset = False
                return

        # No matching preset, set to Custom
        self._updating_preset = True
        self._aoi_preset.setCurrentText("Custom")
        self._updating_preset = False

    def _reset_defaults(self) -> None:
        """Reset all settings to defaults."""
        self._exposure_spinbox.setValue(10000.0)  # 10ms default
        self._aoi_preset.setCurrentIndex(0)  # Full Frame
        self._aoi_width.setValue(2048)
        self._aoi_height.setValue(2048)
        self._cam1_percentage.setValue(100)
        self._cam1_mode.setCurrentIndex(0)
        self._cam2_percentage.setValue(100)
        self._cam2_mode.setCurrentIndex(0)

    def get_settings(self) -> Dict[str, Any]:
        """Get current advanced camera settings.

        Returns:
            Dictionary with settings
        """
        _, cam1_mode_value = CAPTURE_MODES[self._cam1_mode.currentIndex()]
        _, cam2_mode_value = CAPTURE_MODES[self._cam2_mode.currentIndex()]

        return {
            'exposure_us': self._exposure_spinbox.value(),
            'aoi_width': self._aoi_width.value(),
            'aoi_height': self._aoi_height.value(),
            'cam1_capture_percentage': self._cam1_percentage.value(),
            'cam1_capture_mode': cam1_mode_value,
            'cam2_capture_percentage': self._cam2_percentage.value(),
            'cam2_capture_mode': cam2_mode_value,
        }

    def set_settings(self, settings: Dict[str, Any]) -> None:
        """Set advanced camera settings.

        Args:
            settings: Dictionary with settings to apply
        """
        if 'exposure_us' in settings:
            self._exposure_spinbox.setValue(settings['exposure_us'])
        if 'aoi_width' in settings:
            self._aoi_width.setValue(settings['aoi_width'])
        if 'aoi_height' in settings:
            self._aoi_height.setValue(settings['aoi_height'])
        if 'cam1_capture_percentage' in settings:
            self._cam1_percentage.setValue(settings['cam1_capture_percentage'])
        if 'cam1_capture_mode' in settings:
            # Find index for mode value
            for i, (_, value) in enumerate(CAPTURE_MODES):
                if value == settings['cam1_capture_mode']:
                    self._cam1_mode.setCurrentIndex(i)
                    break
        if 'cam2_capture_percentage' in settings:
            self._cam2_percentage.setValue(settings['cam2_capture_percentage'])
        if 'cam2_capture_mode' in settings:
            for i, (_, value) in enumerate(CAPTURE_MODES):
                if value == settings['cam2_capture_mode']:
                    self._cam2_mode.setCurrentIndex(i)
                    break

    # Individual property accessors
    @property
    def aoi_width(self) -> int:
        """Get AOI width."""
        return self._aoi_width.value()

    @aoi_width.setter
    def aoi_width(self, value: int) -> None:
        """Set AOI width."""
        self._aoi_width.setValue(value)

    @property
    def aoi_height(self) -> int:
        """Get AOI height."""
        return self._aoi_height.value()

    @aoi_height.setter
    def aoi_height(self, value: int) -> None:
        """Set AOI height."""
        self._aoi_height.setValue(value)
