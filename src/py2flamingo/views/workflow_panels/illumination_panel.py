"""
Illumination panel for workflow configuration.

Provides UI for selecting multiple laser light sources and LED simultaneously.
"""

import logging
from typing import Optional, Dict, Any, List

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QSpinBox, QComboBox, QCheckBox, QGroupBox, QGridLayout,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from PyQt5.QtCore import pyqtSignal, Qt

from py2flamingo.models.data.workflow import IlluminationSettings


# Available laser channels (matching WORKFLOW_REFERENCE.md format)
# Format: (Display Name, Workflow Key, Default Power)
LASER_CHANNELS = [
    ("Laser 1: 405 nm", "Laser 1 405 nm", 5.0),
    ("Laser 2: 445 nm", "Laser 2 445 nm", 5.0),
    ("Laser 3: 488 nm", "Laser 3 488 nm", 5.0),
    ("Laser 3: 515 nm MLE", "Laser 3 3: 515 nm MLE", 5.0),
    ("Laser 3: 561 nm MLE", "Laser 3 3: 561 nm MLE", 5.0),
    ("Laser 3: 638 nm MLE", "Laser 3 3: 638 nm MLE", 5.0),
    ("Laser 4: 640 nm", "Laser 4 640 nm", 5.0),
]

LED_COLORS = ["Red", "Green", "Blue", "White"]


class IlluminationPanel(QWidget):
    """
    Panel for configuring workflow illumination settings.

    Provides:
    - Multiple laser source selection with individual power control
    - LED source with color and intensity control
    - Left/Right path selection
    - Multi-laser mode option

    Signals:
        settings_changed: Emitted when illumination settings change
    """

    settings_changed = pyqtSignal(object)  # Emits dict of settings

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize illumination panel.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)

        # Storage for laser checkboxes and power spinboxes
        self._laser_checkboxes: List[QCheckBox] = []
        self._laser_power_spinboxes: List[QDoubleSpinBox] = []

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Illumination group
        group = QGroupBox("Illumination")
        group_layout = QVBoxLayout()

        # === LASER TABLE ===
        laser_label = QLabel("Lasers:")
        laser_label.setStyleSheet("font-weight: bold;")
        group_layout.addWidget(laser_label)

        # Create laser table
        self._laser_table = QTableWidget()
        self._laser_table.setRowCount(len(LASER_CHANNELS))
        self._laser_table.setColumnCount(3)
        self._laser_table.setHorizontalHeaderLabels(["Enable", "Laser", "Power (%)"])

        # Configure table appearance
        self._laser_table.verticalHeader().setVisible(False)
        self._laser_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._laser_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._laser_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._laser_table.setMaximumHeight(250)

        # Populate laser table
        for row, (display_name, workflow_key, default_power) in enumerate(LASER_CHANNELS):
            # Enable checkbox
            checkbox = QCheckBox()
            checkbox.setChecked(False)
            checkbox.stateChanged.connect(self._on_settings_changed)
            self._laser_checkboxes.append(checkbox)

            # Center the checkbox in the cell
            checkbox_widget = QWidget()
            checkbox_layout = QHBoxLayout(checkbox_widget)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.setAlignment(Qt.AlignCenter)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            self._laser_table.setCellWidget(row, 0, checkbox_widget)

            # Laser name label
            name_item = QTableWidgetItem(display_name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self._laser_table.setItem(row, 1, name_item)

            # Power spinbox
            power_spinbox = QDoubleSpinBox()
            power_spinbox.setRange(0.0, 100.0)
            power_spinbox.setValue(default_power)
            power_spinbox.setDecimals(2)
            power_spinbox.setSingleStep(0.5)
            power_spinbox.valueChanged.connect(self._on_settings_changed)
            self._laser_power_spinboxes.append(power_spinbox)
            self._laser_table.setCellWidget(row, 2, power_spinbox)

        group_layout.addWidget(self._laser_table)

        # === LED SECTION ===
        led_label = QLabel("LED:")
        led_label.setStyleSheet("font-weight: bold;")
        group_layout.addWidget(led_label)

        led_widget = QWidget()
        led_layout = QGridLayout(led_widget)
        led_layout.setContentsMargins(0, 5, 0, 5)

        # LED enable checkbox
        led_layout.addWidget(QLabel("Enable:"), 0, 0)
        self._led_enable = QCheckBox()
        self._led_enable.setChecked(False)
        self._led_enable.stateChanged.connect(self._on_led_enable_changed)
        led_layout.addWidget(self._led_enable, 0, 1)

        # LED color dropdown
        led_layout.addWidget(QLabel("Color:"), 1, 0)
        self._led_color = QComboBox()
        self._led_color.addItems(LED_COLORS)
        self._led_color.setEnabled(False)
        self._led_color.currentIndexChanged.connect(self._on_settings_changed)
        led_layout.addWidget(self._led_color, 1, 1)

        # LED intensity spinbox
        led_layout.addWidget(QLabel("Intensity (%):"), 2, 0)
        self._led_intensity = QDoubleSpinBox()
        self._led_intensity.setRange(0.0, 100.0)
        self._led_intensity.setValue(50.0)
        self._led_intensity.setDecimals(1)
        self._led_intensity.setSingleStep(5.0)
        self._led_intensity.setEnabled(False)
        self._led_intensity.valueChanged.connect(self._on_settings_changed)
        led_layout.addWidget(self._led_intensity, 2, 1)

        # LED DAC spinbox (advanced)
        led_layout.addWidget(QLabel("LED DAC:"), 3, 0)
        self._led_dac = QSpinBox()
        self._led_dac.setRange(0, 65535)
        self._led_dac.setValue(32768)
        self._led_dac.setEnabled(False)
        self._led_dac.valueChanged.connect(self._on_settings_changed)
        led_layout.addWidget(self._led_dac, 3, 1)

        group_layout.addWidget(led_widget)

        # === GLOBAL OPTIONS ===
        options_label = QLabel("Options:")
        options_label.setStyleSheet("font-weight: bold;")
        group_layout.addWidget(options_label)

        # Light path selection
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("Light Path:"))

        self._left_path = QCheckBox("Left")
        self._left_path.setChecked(True)
        self._left_path.stateChanged.connect(self._on_settings_changed)
        path_layout.addWidget(self._left_path)

        self._right_path = QCheckBox("Right")
        self._right_path.setChecked(False)
        self._right_path.stateChanged.connect(self._on_settings_changed)
        path_layout.addWidget(self._right_path)

        path_layout.addStretch()
        group_layout.addLayout(path_layout)

        # Multi-laser mode checkbox
        multi_laser_layout = QHBoxLayout()
        self._multi_laser_mode = QCheckBox("Run stack with multiple lasers on")
        self._multi_laser_mode.setChecked(False)
        self._multi_laser_mode.stateChanged.connect(self._on_settings_changed)
        multi_laser_layout.addWidget(self._multi_laser_mode)
        multi_laser_layout.addStretch()
        group_layout.addLayout(multi_laser_layout)

        group.setLayout(group_layout)
        layout.addWidget(group)

    def _on_led_enable_changed(self) -> None:
        """Handle LED enable checkbox state change."""
        enabled = self._led_enable.isChecked()
        self._led_color.setEnabled(enabled)
        self._led_intensity.setEnabled(enabled)
        self._led_dac.setEnabled(enabled)
        self._on_settings_changed()

    def _on_settings_changed(self) -> None:
        """Handle any settings change."""
        settings = self.get_settings()
        self.settings_changed.emit(settings)

    def get_settings(self) -> List[IlluminationSettings]:
        """
        Get current illumination settings.

        Returns:
            List of IlluminationSettings objects for all enabled sources
        """
        settings_list = []

        # Add enabled lasers
        for i, (display_name, workflow_key, _) in enumerate(LASER_CHANNELS):
            if self._laser_checkboxes[i].isChecked():
                power = self._laser_power_spinboxes[i].value()
                settings_list.append(IlluminationSettings(
                    laser_channel=workflow_key,
                    laser_power_mw=power,
                    laser_enabled=True,
                    led_channel=None,
                    led_intensity_percent=0.0,
                    led_enabled=False,
                ))

        # Add LED if enabled
        if self._led_enable.isChecked():
            settings_list.append(IlluminationSettings(
                laser_channel=None,
                laser_power_mw=0.0,
                laser_enabled=False,
                led_channel="LED_RGB_Board",
                led_intensity_percent=self._led_intensity.value(),
                led_enabled=True,
            ))

        return settings_list

    def get_workflow_illumination_dict(self) -> Dict[str, str]:
        """
        Get illumination settings as workflow dictionary format.

        Returns:
            Dictionary for workflow file format with all illumination sources
        """
        settings = {}

        # Add all lasers (enabled ones with power, disabled ones as 0.00 0)
        for i, (display_name, workflow_key, _) in enumerate(LASER_CHANNELS):
            if self._laser_checkboxes[i].isChecked():
                power = self._laser_power_spinboxes[i].value()
                settings[workflow_key] = f"{power:.2f} 1"
            else:
                # Include disabled lasers as 0.00 0 for completeness
                settings[workflow_key] = "0.00 0"

        # LED settings
        if self._led_enable.isChecked():
            intensity = self._led_intensity.value()
            led_color_idx = self._led_color.currentIndex()
            dac_value = self._led_dac.value()

            settings["LED_RGB_Board"] = f"{intensity:.1f} 1"
            settings["LED selection"] = f"{led_color_idx} 1"
            settings["LED DAC"] = f"{dac_value} 1"
        else:
            settings["LED_RGB_Board"] = "0.0 0"
            settings["LED selection"] = "0 0"
            settings["LED DAC"] = "0 0"

        # Light paths
        settings["Left path"] = "ON 1" if self._left_path.isChecked() else "OFF 0"
        settings["Right path"] = "ON 1" if self._right_path.isChecked() else "OFF 0"

        return settings

    def get_workflow_illumination_options_dict(self) -> Dict[str, str]:
        """
        Get illumination options section for workflow file.

        Returns:
            Dictionary for Illumination Options section
        """
        return {
            "Run stack with multiple lasers on": "true" if self._multi_laser_mode.isChecked() else "false"
        }

    def set_settings(self, settings: List[IlluminationSettings]) -> None:
        """
        Set illumination settings from list of objects.

        Args:
            settings: List of IlluminationSettings to apply
        """
        # Reset all checkboxes first
        for checkbox in self._laser_checkboxes:
            checkbox.setChecked(False)
        self._led_enable.setChecked(False)

        # Apply each setting
        for setting in settings:
            if setting.laser_enabled and setting.laser_channel:
                # Find matching laser channel
                for i, (_, workflow_key, _) in enumerate(LASER_CHANNELS):
                    if workflow_key == setting.laser_channel:
                        self._laser_checkboxes[i].setChecked(True)
                        self._laser_power_spinboxes[i].setValue(setting.laser_power_mw)
                        break

            elif setting.led_enabled and setting.led_channel:
                self._led_enable.setChecked(True)
                self._led_intensity.setValue(setting.led_intensity_percent)

    def set_settings_from_workflow_dict(self, illumination_dict: Dict[str, str],
                                        options_dict: Optional[Dict[str, str]] = None) -> None:
        """
        Set illumination settings from workflow dictionary format.

        Args:
            illumination_dict: Dictionary from Illumination Source section
            options_dict: Optional dictionary from Illumination Options section
        """
        # Reset all
        for checkbox in self._laser_checkboxes:
            checkbox.setChecked(False)
        self._led_enable.setChecked(False)

        # Parse laser settings
        for i, (_, workflow_key, default_power) in enumerate(LASER_CHANNELS):
            if workflow_key in illumination_dict:
                value_str = illumination_dict[workflow_key]
                parts = value_str.split()
                if len(parts) == 2:
                    power = float(parts[0])
                    enabled = int(parts[1]) == 1
                    if enabled:
                        self._laser_checkboxes[i].setChecked(True)
                        self._laser_power_spinboxes[i].setValue(power)

        # Parse LED settings
        if "LED_RGB_Board" in illumination_dict:
            value_str = illumination_dict["LED_RGB_Board"]
            parts = value_str.split()
            if len(parts) == 2:
                intensity = float(parts[0])
                enabled = int(parts[1]) == 1
                if enabled:
                    self._led_enable.setChecked(True)
                    self._led_intensity.setValue(intensity)

        # Parse LED color selection
        if "LED selection" in illumination_dict:
            value_str = illumination_dict["LED selection"]
            parts = value_str.split()
            if len(parts) >= 1:
                color_idx = int(parts[0])
                if 0 <= color_idx < len(LED_COLORS):
                    self._led_color.setCurrentIndex(color_idx)

        # Parse LED DAC
        if "LED DAC" in illumination_dict:
            value_str = illumination_dict["LED DAC"]
            parts = value_str.split()
            if len(parts) >= 1:
                dac_value = int(parts[0])
                self._led_dac.setValue(dac_value)

        # Parse light paths
        if "Left path" in illumination_dict:
            self._left_path.setChecked(illumination_dict["Left path"].startswith("ON"))

        if "Right path" in illumination_dict:
            self._right_path.setChecked(illumination_dict["Right path"].startswith("ON"))

        # Parse illumination options
        if options_dict and "Run stack with multiple lasers on" in options_dict:
            multi_laser_enabled = options_dict["Run stack with multiple lasers on"].lower() == "true"
            self._multi_laser_mode.setChecked(multi_laser_enabled)

    def get_enabled_laser_count(self) -> int:
        """
        Get the number of enabled lasers.

        Returns:
            Count of enabled laser channels
        """
        return sum(1 for checkbox in self._laser_checkboxes if checkbox.isChecked())

    def get_enabled_source_count(self) -> int:
        """
        Get the total number of enabled illumination sources (lasers + LED).

        Returns:
            Count of all enabled sources
        """
        count = self.get_enabled_laser_count()
        if self._led_enable.isChecked():
            count += 1
        return count
