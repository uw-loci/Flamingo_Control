"""Advanced Illumination Settings Dialog.

Dialog for configuring rarely-changed illumination settings
such as light path selection, multi-laser mode, and LED DAC values.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QComboBox, QCheckBox, QGroupBox,
    QPushButton, QDialogButtonBox
)
from py2flamingo.services.window_geometry_manager import PersistentDialog
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon


LED_COLORS = ["Red", "Green", "Blue", "White"]


class AdvancedIlluminationDialog(PersistentDialog):
    """Dialog for advanced illumination settings.

    Settings included:
    - Multi-laser mode (run stack with multiple lasers on)
    - LED color selection
    - LED brightness (percentage)

    Note: Light path selection (Left/Right) is now on the main panel.
    """

    def __init__(self, parent=None):
        """Initialize the dialog.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)

        self.setWindowTitle("Advanced Illumination Settings")
        self.setWindowIcon(QIcon())  # Clear inherited napari icon
        self.setMinimumWidth(400)
        self.setModal(True)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Multi-Laser Mode Section
        laser_group = QGroupBox("Multi-Laser Options")
        laser_layout = QVBoxLayout()

        self._multi_laser_mode = QCheckBox("Run stack with multiple lasers on")
        self._multi_laser_mode.setToolTip(
            "When enabled, all selected lasers illuminate simultaneously.\n"
            "When disabled, each laser is used sequentially for separate channels."
        )
        laser_layout.addWidget(self._multi_laser_mode)

        laser_group.setLayout(laser_layout)
        layout.addWidget(laser_group)

        # LED Advanced Section
        led_group = QGroupBox("LED Advanced Settings")
        led_layout = QVBoxLayout()
        led_layout.setSpacing(8)

        # LED Color
        color_layout = QHBoxLayout()
        color_layout.addWidget(QLabel("LED Color:"))
        self._led_color = QComboBox()
        self._led_color.addItems(LED_COLORS)
        self._led_color.setToolTip("Select the LED color channel")
        color_layout.addWidget(self._led_color)
        color_layout.addStretch()
        led_layout.addLayout(color_layout)

        # LED Brightness (percentage)
        brightness_layout = QHBoxLayout()
        brightness_layout.addWidget(QLabel("LED Brightness:"))
        self._led_brightness = QDoubleSpinBox()
        self._led_brightness.setRange(0.0, 100.0)
        self._led_brightness.setValue(50.0)
        self._led_brightness.setDecimals(1)
        self._led_brightness.setSingleStep(5.0)
        self._led_brightness.setSuffix(" %")
        self._led_brightness.setToolTip("LED brightness percentage (default: 50%)")
        brightness_layout.addWidget(self._led_brightness)
        brightness_layout.addStretch()
        led_layout.addLayout(brightness_layout)

        # Description label explaining LED brightness
        desc_label = QLabel(
            "Controls the LED brightness via internal DAC conversion.\n"
            "50% = calibrated default (DAC 32768). Adjust if LED appears too bright or dim."
        )
        desc_label.setStyleSheet("color: gray; font-style: italic; padding: 5px;")
        desc_label.setWordWrap(True)
        led_layout.addWidget(desc_label)

        # Reset to default button
        reset_led_btn = QPushButton("Reset to Default")
        reset_led_btn.clicked.connect(self._reset_led_defaults)
        led_layout.addWidget(reset_led_btn)

        led_group.setLayout(led_layout)
        layout.addWidget(led_group)

        # Add stretch to push buttons to bottom
        layout.addStretch()

        # Dialog buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _reset_led_defaults(self) -> None:
        """Reset LED settings to defaults."""
        self._led_color.setCurrentIndex(3)  # White
        self._led_brightness.setValue(50.0)  # 50%

    def get_settings(self) -> Dict[str, Any]:
        """Get current advanced illumination settings.

        Returns:
            Dictionary with settings
        """
        return {
            'multi_laser_mode': self._multi_laser_mode.isChecked(),
            'led_color_index': self._led_color.currentIndex(),
            'led_color': self._led_color.currentText(),
            'led_dac_percent': self._led_brightness.value(),
        }

    def set_settings(self, settings: Dict[str, Any]) -> None:
        """Set advanced illumination settings.

        Args:
            settings: Dictionary with settings to apply
        """
        if 'multi_laser_mode' in settings:
            self._multi_laser_mode.setChecked(settings['multi_laser_mode'])
        if 'led_color_index' in settings:
            self._led_color.setCurrentIndex(settings['led_color_index'])
        if 'led_dac_percent' in settings:
            self._led_brightness.setValue(settings['led_dac_percent'])

    # Individual property accessors
    @property
    def multi_laser_mode(self) -> bool:
        """Get multi-laser mode state."""
        return self._multi_laser_mode.isChecked()

    @multi_laser_mode.setter
    def multi_laser_mode(self, enabled: bool) -> None:
        """Set multi-laser mode state."""
        self._multi_laser_mode.setChecked(enabled)

    @property
    def led_color_index(self) -> int:
        """Get LED color index."""
        return self._led_color.currentIndex()

    @led_color_index.setter
    def led_color_index(self, index: int) -> None:
        """Set LED color index."""
        self._led_color.setCurrentIndex(index)

    @property
    def led_brightness(self) -> float:
        """Get LED brightness percentage."""
        return self._led_brightness.value()

    @led_brightness.setter
    def led_brightness(self, value: float) -> None:
        """Set LED brightness percentage."""
        self._led_brightness.setValue(value)
