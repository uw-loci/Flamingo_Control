"""
Laser and LED Control Panel View

Provides UI controls for:
- Selecting laser or LED for live view
- Adjusting laser power and LED intensity
- Visual feedback of active light source
"""

import logging
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
    QRadioButton, QButtonGroup, QGroupBox, QGridLayout
)
from PyQt5.QtCore import Qt, pyqtSlot

from py2flamingo.controllers.laser_led_controller import LaserLEDController


class LaserLEDControlPanel(QWidget):
    """
    Control panel for laser and LED selection and power/intensity adjustment.

    Features:
    - Radio buttons to select active light source
    - Power/intensity sliders for each source
    - Visual indication of active source
    - Automatic preview mode enabling
    """

    def __init__(self, laser_led_controller: LaserLEDController):
        """
        Initialize laser/LED control panel.

        Args:
            laser_led_controller: LaserLEDController instance
        """
        super().__init__()

        self.laser_led_controller = laser_led_controller
        self.logger = logging.getLogger(__name__)

        # Track widgets
        self._laser_radios = {}  # laser_index -> QRadioButton
        self._laser_sliders = {}  # laser_index -> QSlider
        self._laser_labels = {}  # laser_index -> QLabel
        self._led_radio = None
        self._led_slider = None
        self._led_label = None

        # Button group for radio buttons
        self._source_button_group = QButtonGroup()
        self._source_button_group.buttonClicked.connect(self._on_source_selected)

        # Connect controller signals
        self.laser_led_controller.preview_enabled.connect(self._on_preview_enabled)
        self.laser_led_controller.preview_disabled.connect(self._on_preview_disabled)
        self.laser_led_controller.error_occurred.connect(self._on_error)

        self._setup_ui()

        self.logger.info("LaserLEDControlPanel initialized")

    def _setup_ui(self) -> None:
        """Create and layout all UI components."""
        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)

        # Title
        title = QLabel("<b>Light Source Control</b>")
        title.setStyleSheet("font-size: 12pt;")
        main_layout.addWidget(title)

        # Info label
        info = QLabel("Select a light source for live view and adjust power/intensity:")
        info.setWordWrap(True)
        info.setStyleSheet("color: #666; font-style: italic; font-size: 9pt;")
        main_layout.addWidget(info)

        # Lasers section
        lasers_group = self._create_lasers_section()
        if lasers_group:
            main_layout.addWidget(lasers_group)

        # LED section
        led_group = self._create_led_section()
        if led_group:
            main_layout.addWidget(led_group)

        # Status
        self._status_label = QLabel("No light source active")
        self._status_label.setStyleSheet(
            "background-color: #fff3cd; color: #856404; padding: 8px; "
            "border: 1px solid #ffc107; border-radius: 4px; font-weight: bold;"
        )
        main_layout.addWidget(self._status_label)

        main_layout.addStretch()
        self.setLayout(main_layout)

    def _create_lasers_section(self) -> QGroupBox:
        """Create lasers control section."""
        lasers = self.laser_led_controller.get_available_lasers()

        if not lasers:
            return None

        group = QGroupBox("Lasers")
        layout = QGridLayout()
        layout.setSpacing(8)

        # Headers
        layout.addWidget(QLabel("<b>Select</b>"), 0, 0)
        layout.addWidget(QLabel("<b>Laser</b>"), 0, 1)
        layout.addWidget(QLabel("<b>Power (%)</b>"), 0, 2)
        layout.addWidget(QLabel("<b>Level</b>"), 0, 3)

        # Create controls for each laser
        for i, laser in enumerate(lasers):
            row = i + 1

            # Radio button
            radio = QRadioButton()
            self._laser_radios[laser.index] = radio
            self._source_button_group.addButton(radio, laser.index)  # ID is laser index
            layout.addWidget(radio, row, 0, Qt.AlignCenter)

            # Laser name
            name_label = QLabel(laser.name)
            layout.addWidget(name_label, row, 1)

            # Power percentage label
            power = self.laser_led_controller.get_laser_power(laser.index)
            power_label = QLabel(f"{power:.1f}%")
            power_label.setMinimumWidth(50)
            power_label.setStyleSheet("font-weight: bold;")
            self._laser_labels[laser.index] = power_label
            layout.addWidget(power_label, row, 2)

            # Power slider
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)  # 0-100%
            slider.setValue(int(power))
            slider.setTickPosition(QSlider.TicksBelow)
            slider.setTickInterval(10)
            slider.valueChanged.connect(
                lambda val, idx=laser.index: self._on_laser_power_changed(idx, val)
            )
            self._laser_sliders[laser.index] = slider
            layout.addWidget(slider, row, 3)

        group.setLayout(layout)
        return group

    def _create_led_section(self) -> QGroupBox:
        """Create LED control section."""
        if not self.laser_led_controller.is_led_available():
            return None

        group = QGroupBox("LED")
        layout = QGridLayout()
        layout.setSpacing(8)

        # Headers
        layout.addWidget(QLabel("<b>Select</b>"), 0, 0)
        layout.addWidget(QLabel("<b>Source</b>"), 0, 1)
        layout.addWidget(QLabel("<b>Intensity (%)</b>"), 0, 2)
        layout.addWidget(QLabel("<b>Level</b>"), 0, 3)

        # Radio button
        self._led_radio = QRadioButton()
        self._source_button_group.addButton(self._led_radio, -1)  # ID is -1 for LED
        layout.addWidget(self._led_radio, 1, 0, Qt.AlignCenter)

        # LED label
        led_label = QLabel("White LED")
        layout.addWidget(led_label, 1, 1)

        # Intensity percentage label
        intensity = self.laser_led_controller.get_led_intensity()
        self._led_label = QLabel(f"{intensity:.1f}%")
        self._led_label.setMinimumWidth(50)
        self._led_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._led_label, 1, 2)

        # Intensity slider
        self._led_slider = QSlider(Qt.Horizontal)
        self._led_slider.setRange(0, 100)  # 0-100%
        self._led_slider.setValue(int(intensity))
        self._led_slider.setTickPosition(QSlider.TicksBelow)
        self._led_slider.setTickInterval(10)
        self._led_slider.valueChanged.connect(self._on_led_intensity_changed)
        layout.addWidget(self._led_slider, 1, 3)

        group.setLayout(layout)
        return group

    def _on_laser_power_changed(self, laser_index: int, value: int) -> None:
        """Handle laser power slider change."""
        power_percent = float(value)

        # Update label
        if laser_index in self._laser_labels:
            self._laser_labels[laser_index].setText(f"{power_percent:.1f}%")

        # Send to controller
        self.laser_led_controller.set_laser_power(laser_index, power_percent)

    def _on_led_intensity_changed(self, value: int) -> None:
        """Handle LED intensity slider change."""
        intensity_percent = float(value)

        # Update label
        if self._led_label:
            self._led_label.setText(f"{intensity_percent:.1f}%")

        # Send to controller
        self.laser_led_controller.set_led_intensity(intensity_percent)

    def _on_source_selected(self, button: QRadioButton) -> None:
        """Handle light source selection."""
        source_id = self._source_button_group.id(button)

        if source_id == -1:  # LED
            self.logger.info("LED selected for preview")
            self.laser_led_controller.enable_led_for_preview()
        elif source_id > 0:  # Laser
            self.logger.info(f"Laser {source_id} selected for preview")
            self.laser_led_controller.enable_laser_for_preview(source_id)

    @pyqtSlot(str)
    def _on_preview_enabled(self, source_name: str) -> None:
        """Update UI when preview is enabled."""
        self._status_label.setText(f"✓ Active: {source_name}")
        self._status_label.setStyleSheet(
            "background-color: #d4edda; color: #155724; padding: 8px; "
            "border: 1px solid #c3e6cb; border-radius: 4px; font-weight: bold;"
        )
        self.logger.info(f"Preview enabled: {source_name}")

    @pyqtSlot()
    def _on_preview_disabled(self) -> None:
        """Update UI when preview is disabled."""
        # Uncheck all radio buttons
        self._source_button_group.setExclusive(False)
        for button in self._source_button_group.buttons():
            button.setChecked(False)
        self._source_button_group.setExclusive(True)

        self._status_label.setText("No light source active")
        self._status_label.setStyleSheet(
            "background-color: #fff3cd; color: #856404; padding: 8px; "
            "border: 1px solid #ffc107; border-radius: 4px; font-weight: bold;"
        )
        self.logger.info("Preview disabled")

    @pyqtSlot(str)
    def _on_error(self, error_message: str) -> None:
        """Display error message."""
        self._status_label.setText(f"Error: {error_message}")
        self._status_label.setStyleSheet(
            "background-color: #f8d7da; color: #721c24; padding: 8px; "
            "border: 1px solid #f5c6cb; border-radius: 4px; font-weight: bold;"
        )
        self.logger.error(error_message)

    def get_selected_source(self) -> str:
        """
        Get currently selected light source.

        Returns:
            String like "laser_3" or "led" or "none"
        """
        checked_button = self._source_button_group.checkedButton()
        if not checked_button:
            return "none"

        source_id = self._source_button_group.id(checked_button)
        if source_id == -1:
            return "led"
        else:
            return f"laser_{source_id}"

    def is_source_active(self) -> bool:
        """Check if any light source is currently active."""
        return self.laser_led_controller.is_preview_active()
