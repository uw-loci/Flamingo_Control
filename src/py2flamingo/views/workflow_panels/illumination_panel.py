"""
Illumination panel for workflow configuration.

Provides UI for selecting light source and power settings.
"""

import logging
from typing import Optional, Dict, Any, List

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QComboBox, QCheckBox, QGroupBox, QGridLayout,
    QRadioButton, QButtonGroup
)
from PyQt5.QtCore import pyqtSignal

from py2flamingo.models.data.workflow import IlluminationSettings


# Available laser channels (matching WORKFLOW_REFERENCE.md format)
LASER_CHANNELS = [
    ("Laser 1: 405 nm", "Laser 1 405 nm"),
    ("Laser 2: 445 nm", "Laser 2 445 nm"),
    ("Laser 3: 488 nm", "Laser 3 488 nm"),
    ("Laser 3: 515 nm MLE", "Laser 3 3: 515 nm MLE"),
    ("Laser 3: 561 nm MLE", "Laser 3 3: 561 nm MLE"),
    ("Laser 3: 638 nm MLE", "Laser 3 3: 638 nm MLE"),
    ("Laser 4: 640 nm", "Laser 4 640 nm"),
]

LED_COLORS = ["Red", "Green", "Blue", "White"]


class IlluminationPanel(QWidget):
    """
    Panel for configuring workflow illumination settings.

    Provides:
    - Laser/LED source selection
    - Power/intensity control
    - Left/Right path selection

    Signals:
        settings_changed: Emitted when illumination settings change
    """

    settings_changed = pyqtSignal(object)  # Emits IlluminationSettings

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize illumination panel.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Illumination group
        group = QGroupBox("Illumination")
        group_layout = QVBoxLayout()

        # Source selection (Laser vs LED)
        source_layout = QHBoxLayout()
        source_layout.addWidget(QLabel("Source:"))

        self._source_group = QButtonGroup(self)
        self._laser_radio = QRadioButton("Laser")
        self._led_radio = QRadioButton("LED")
        self._laser_radio.setChecked(True)  # Default to laser
        self._source_group.addButton(self._laser_radio, 0)
        self._source_group.addButton(self._led_radio, 1)
        self._source_group.buttonClicked.connect(self._on_source_changed)

        source_layout.addWidget(self._laser_radio)
        source_layout.addWidget(self._led_radio)
        source_layout.addStretch()

        group_layout.addLayout(source_layout)

        # Laser settings (shown when laser selected)
        self._laser_widget = QWidget()
        laser_layout = QGridLayout(self._laser_widget)
        laser_layout.setContentsMargins(0, 5, 0, 0)

        laser_layout.addWidget(QLabel("Channel:"), 0, 0)
        self._laser_combo = QComboBox()
        for display_name, _ in LASER_CHANNELS:
            self._laser_combo.addItem(display_name)
        self._laser_combo.setCurrentIndex(2)  # Default to 488nm
        self._laser_combo.currentIndexChanged.connect(self._on_settings_changed)
        laser_layout.addWidget(self._laser_combo, 0, 1)

        laser_layout.addWidget(QLabel("Power (%):"), 1, 0)
        self._laser_power = QDoubleSpinBox()
        self._laser_power.setRange(0.0, 100.0)
        self._laser_power.setValue(5.0)
        self._laser_power.setDecimals(2)
        self._laser_power.setSingleStep(0.5)
        self._laser_power.valueChanged.connect(self._on_settings_changed)
        laser_layout.addWidget(self._laser_power, 1, 1)

        group_layout.addWidget(self._laser_widget)

        # LED settings (shown when LED selected)
        self._led_widget = QWidget()
        led_layout = QGridLayout(self._led_widget)
        led_layout.setContentsMargins(0, 5, 0, 0)

        led_layout.addWidget(QLabel("Color:"), 0, 0)
        self._led_color = QComboBox()
        self._led_color.addItems(LED_COLORS)
        self._led_color.currentIndexChanged.connect(self._on_settings_changed)
        led_layout.addWidget(self._led_color, 0, 1)

        led_layout.addWidget(QLabel("Intensity (%):"), 1, 0)
        self._led_intensity = QDoubleSpinBox()
        self._led_intensity.setRange(0.0, 100.0)
        self._led_intensity.setValue(50.0)
        self._led_intensity.setDecimals(1)
        self._led_intensity.setSingleStep(5.0)
        self._led_intensity.valueChanged.connect(self._on_settings_changed)
        led_layout.addWidget(self._led_intensity, 1, 1)

        self._led_widget.setVisible(False)
        group_layout.addWidget(self._led_widget)

        # Light path selection
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("Light Path:"))

        self._left_path = QCheckBox("Left")
        self._left_path.setChecked(True)
        self._left_path.stateChanged.connect(self._on_settings_changed)
        path_layout.addWidget(self._left_path)

        self._right_path = QCheckBox("Right")
        self._right_path.stateChanged.connect(self._on_settings_changed)
        path_layout.addWidget(self._right_path)

        path_layout.addStretch()
        group_layout.addLayout(path_layout)

        group.setLayout(group_layout)
        layout.addWidget(group)

    def _on_source_changed(self) -> None:
        """Handle source selection change (Laser vs LED)."""
        is_laser = self._laser_radio.isChecked()
        self._laser_widget.setVisible(is_laser)
        self._led_widget.setVisible(not is_laser)
        self._on_settings_changed()

    def _on_settings_changed(self) -> None:
        """Handle any settings change."""
        settings = self.get_settings()
        self.settings_changed.emit(settings)

    def get_settings(self) -> IlluminationSettings:
        """
        Get current illumination settings.

        Returns:
            IlluminationSettings object with current values
        """
        if self._laser_radio.isChecked():
            # Laser mode
            channel_idx = self._laser_combo.currentIndex()
            _, channel_name = LASER_CHANNELS[channel_idx]
            return IlluminationSettings(
                laser_channel=channel_name,
                laser_power_mw=self._laser_power.value(),
                laser_enabled=True,
                led_channel=None,
                led_intensity_percent=0.0,
                led_enabled=False,
            )
        else:
            # LED mode
            led_color = LED_COLORS[self._led_color.currentIndex()]
            return IlluminationSettings(
                laser_channel=None,
                laser_power_mw=0.0,
                laser_enabled=False,
                led_channel="LED_RGB_Board",
                led_intensity_percent=self._led_intensity.value(),
                led_enabled=True,
            )

    def get_workflow_illumination_dict(self) -> Dict[str, str]:
        """
        Get illumination settings as workflow dictionary format.

        Returns:
            Dictionary for workflow file format
        """
        settings = {}

        if self._laser_radio.isChecked():
            # Laser settings in format: "power on/off"
            channel_idx = self._laser_combo.currentIndex()
            _, channel_name = LASER_CHANNELS[channel_idx]
            power = self._laser_power.value()
            settings[channel_name] = f"{power:.2f} 1"
        else:
            # LED settings
            led_color_idx = self._led_color.currentIndex()
            intensity = self._led_intensity.value()
            settings["LED_RGB_Board"] = f"{intensity:.1f} 1"
            settings["LED selection"] = f"{led_color_idx} 0"

        # Light paths
        settings["Left path"] = "ON" if self._left_path.isChecked() else "OFF"
        settings["Right path"] = "ON" if self._right_path.isChecked() else "OFF"

        return settings

    def set_settings(self, settings: IlluminationSettings) -> None:
        """
        Set illumination settings from object.

        Args:
            settings: IlluminationSettings to apply
        """
        if settings.laser_enabled and settings.laser_channel:
            self._laser_radio.setChecked(True)
            self._on_source_changed()

            # Find matching channel
            for i, (_, channel_name) in enumerate(LASER_CHANNELS):
                if channel_name == settings.laser_channel:
                    self._laser_combo.setCurrentIndex(i)
                    break

            self._laser_power.setValue(settings.laser_power_mw)

        elif settings.led_enabled:
            self._led_radio.setChecked(True)
            self._on_source_changed()
            self._led_intensity.setValue(settings.led_intensity_percent)
