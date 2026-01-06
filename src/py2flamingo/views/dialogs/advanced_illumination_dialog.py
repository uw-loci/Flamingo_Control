"""Advanced Illumination Settings Dialog.

Dialog for configuring rarely-changed illumination settings
such as light path selection, multi-laser mode, and LED DAC values.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QSpinBox, QComboBox, QCheckBox, QGroupBox, QGridLayout,
    QPushButton, QDialogButtonBox
)
from PyQt5.QtCore import Qt


LED_COLORS = ["Red", "Green", "Blue", "White"]


class AdvancedIlluminationDialog(QDialog):
    """Dialog for advanced illumination settings.

    Settings included:
    - Light path selection (Left/Right)
    - Multi-laser mode (run stack with multiple lasers on)
    - LED color selection
    - LED DAC value
    """

    def __init__(self, parent: Optional[QDialog] = None):
        """Initialize the dialog.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)

        self.setWindowTitle("Advanced Illumination Settings")
        self.setMinimumWidth(400)
        self.setModal(True)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Light Path Section
        path_group = QGroupBox("Light Path")
        path_layout = QHBoxLayout()

        path_layout.addWidget(QLabel("Illumination Path:"))

        self._left_path = QCheckBox("Left")
        self._left_path.setChecked(True)
        path_layout.addWidget(self._left_path)

        self._right_path = QCheckBox("Right")
        self._right_path.setChecked(False)
        path_layout.addWidget(self._right_path)

        path_layout.addStretch()
        path_group.setLayout(path_layout)
        layout.addWidget(path_group)

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
        led_grid = QGridLayout()
        led_grid.setSpacing(8)

        # LED Color
        led_grid.addWidget(QLabel("LED Color:"), 0, 0)
        self._led_color = QComboBox()
        self._led_color.addItems(LED_COLORS)
        self._led_color.setToolTip("Select the LED color channel")
        led_grid.addWidget(self._led_color, 0, 1)

        # LED DAC Value
        led_grid.addWidget(QLabel("LED DAC Value:"), 1, 0)
        self._led_dac = QSpinBox()
        self._led_dac.setRange(0, 65535)
        self._led_dac.setValue(32768)
        self._led_dac.setToolTip(
            "Direct DAC value for LED brightness control.\n"
            "Default: 32768 (calibrated value)\n"
            "Range: 0-65535"
        )
        led_grid.addWidget(self._led_dac, 1, 1)

        # Reset to default button
        reset_led_btn = QPushButton("Reset to Default")
        reset_led_btn.clicked.connect(self._reset_led_defaults)
        led_grid.addWidget(reset_led_btn, 2, 1)

        led_group.setLayout(led_grid)
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
        self._led_dac.setValue(32768)

    def get_settings(self) -> Dict[str, Any]:
        """Get current advanced illumination settings.

        Returns:
            Dictionary with settings
        """
        return {
            'left_path': self._left_path.isChecked(),
            'right_path': self._right_path.isChecked(),
            'multi_laser_mode': self._multi_laser_mode.isChecked(),
            'led_color_index': self._led_color.currentIndex(),
            'led_color': self._led_color.currentText(),
            'led_dac': self._led_dac.value(),
        }

    def set_settings(self, settings: Dict[str, Any]) -> None:
        """Set advanced illumination settings.

        Args:
            settings: Dictionary with settings to apply
        """
        if 'left_path' in settings:
            self._left_path.setChecked(settings['left_path'])
        if 'right_path' in settings:
            self._right_path.setChecked(settings['right_path'])
        if 'multi_laser_mode' in settings:
            self._multi_laser_mode.setChecked(settings['multi_laser_mode'])
        if 'led_color_index' in settings:
            self._led_color.setCurrentIndex(settings['led_color_index'])
        if 'led_dac' in settings:
            self._led_dac.setValue(settings['led_dac'])

    # Individual property accessors
    @property
    def left_path(self) -> bool:
        """Get left path enabled state."""
        return self._left_path.isChecked()

    @left_path.setter
    def left_path(self, enabled: bool) -> None:
        """Set left path enabled state."""
        self._left_path.setChecked(enabled)

    @property
    def right_path(self) -> bool:
        """Get right path enabled state."""
        return self._right_path.isChecked()

    @right_path.setter
    def right_path(self, enabled: bool) -> None:
        """Set right path enabled state."""
        self._right_path.setChecked(enabled)

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
    def led_dac(self) -> int:
        """Get LED DAC value."""
        return self._led_dac.value()

    @led_dac.setter
    def led_dac(self, value: int) -> None:
        """Set LED DAC value."""
        self._led_dac.setValue(value)
