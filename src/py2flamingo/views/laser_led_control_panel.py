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
    QRadioButton, QButtonGroup, QGroupBox, QGridLayout, QComboBox, QDoubleSpinBox
)
from PyQt5.QtCore import Qt, pyqtSlot, QTimer

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
        self._laser_spinboxes = {}  # laser_index -> QDoubleSpinBox

        # LED widgets - single combobox and slider
        self._led_radio = None  # Single radio button for LED
        self._led_combobox = None  # Combobox for LED color selection
        self._led_slider = None  # Single slider for LED intensity
        self._led_spinbox = None  # Editable intensity spinbox

        # Timers for delayed logging (reduce spam)
        self._laser_log_timers = {}  # laser_index -> QTimer
        self._led_log_timer = None

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
        self._status_label = QLabel("Select a light source for live viewing")
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

            # Power percentage spinbox (editable)
            power = self.laser_led_controller.get_laser_power(laser.index)
            power_spinbox = QDoubleSpinBox()
            power_spinbox.setRange(0.0, 100.0)
            power_spinbox.setValue(power)
            power_spinbox.setDecimals(1)
            power_spinbox.setSuffix("%")
            power_spinbox.setMinimumWidth(70)
            power_spinbox.setStyleSheet("font-weight: bold;")
            power_spinbox.valueChanged.connect(
                lambda val, idx=laser.index: self._on_laser_power_spinbox_changed(idx, val)
            )
            self._laser_spinboxes[laser.index] = power_spinbox
            layout.addWidget(power_spinbox, row, 2)

            # Power slider
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)  # 0-100%
            slider.setValue(int(power))
            slider.setTickPosition(QSlider.TicksBelow)
            slider.setTickInterval(10)
            slider.valueChanged.connect(
                lambda val, idx=laser.index: self._on_laser_power_slider_changed(idx, val)
            )
            slider.sliderReleased.connect(
                lambda idx=laser.index: self._on_laser_slider_released(idx)
            )
            self._laser_sliders[laser.index] = slider
            layout.addWidget(slider, row, 3)

            # Create log timer for this laser
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(lambda idx=laser.index: self._log_laser_power(idx))
            self._laser_log_timers[laser.index] = timer

        group.setLayout(layout)
        return group

    def _create_led_section(self) -> QGroupBox:
        """Create LED RGB control section with single row and combobox."""
        if not self.laser_led_controller.is_led_available():
            return None

        group = QGroupBox("LED (RGB)")
        layout = QGridLayout()
        layout.setSpacing(8)

        # Headers
        layout.addWidget(QLabel("<b>Select</b>"), 0, 0)
        layout.addWidget(QLabel("<b>Type</b>"), 0, 1)
        layout.addWidget(QLabel("<b>Intensity (%)</b>"), 0, 2)
        layout.addWidget(QLabel("<b>Level</b>"), 0, 3)

        # Single LED row
        row = 1

        # Radio button for selecting LED as light source
        self._led_radio = QRadioButton()
        # Use ID -1 for LED
        self._source_button_group.addButton(self._led_radio, -1)
        layout.addWidget(self._led_radio, row, 0, Qt.AlignCenter)

        # Combobox for LED color selection
        self._led_combobox = QComboBox()
        self._led_combobox.addItems(["Red", "Green", "Blue", "White"])
        self._led_combobox.setCurrentIndex(0)  # Default to Red
        self._led_combobox.currentIndexChanged.connect(self._on_led_color_changed)
        layout.addWidget(self._led_combobox, row, 1)

        # Intensity percentage spinbox (editable)
        intensity = self.laser_led_controller.get_led_intensity(0)  # Start with Red (0)
        self._led_spinbox = QDoubleSpinBox()
        self._led_spinbox.setRange(0.0, 100.0)
        self._led_spinbox.setValue(intensity)
        self._led_spinbox.setDecimals(1)
        self._led_spinbox.setSuffix("%")
        self._led_spinbox.setMinimumWidth(70)
        self._led_spinbox.setStyleSheet("font-weight: bold;")
        self._led_spinbox.valueChanged.connect(self._on_led_intensity_spinbox_changed)
        layout.addWidget(self._led_spinbox, row, 2)

        # Intensity slider
        self._led_slider = QSlider(Qt.Horizontal)
        self._led_slider.setRange(0, 100)  # 0-100%
        self._led_slider.setValue(int(intensity))
        self._led_slider.setTickPosition(QSlider.TicksBelow)
        self._led_slider.setTickInterval(10)
        self._led_slider.valueChanged.connect(self._on_led_intensity_slider_changed)
        self._led_slider.sliderReleased.connect(self._on_led_slider_released)
        layout.addWidget(self._led_slider, row, 3)

        # Create log timer for LED
        self._led_log_timer = QTimer()
        self._led_log_timer.setSingleShot(True)
        self._led_log_timer.timeout.connect(self._log_led_intensity)

        group.setLayout(layout)
        return group

    def _on_laser_power_slider_changed(self, laser_index: int, value: int) -> None:
        """Handle laser power slider change (while dragging)."""
        power_percent = float(value)

        # Update spinbox (block signals to prevent recursion)
        if laser_index in self._laser_spinboxes:
            self._laser_spinboxes[laser_index].blockSignals(True)
            self._laser_spinboxes[laser_index].setValue(power_percent)
            self._laser_spinboxes[laser_index].blockSignals(False)

        # Send to controller immediately (for live feedback)
        self.laser_led_controller.set_laser_power(laser_index, power_percent)

        # Restart timer for delayed logging (1 second after last change)
        if laser_index in self._laser_log_timers:
            self._laser_log_timers[laser_index].stop()
            self._laser_log_timers[laser_index].start(1000)  # 1 second delay

    def _on_laser_power_spinbox_changed(self, laser_index: int, value: float) -> None:
        """Handle laser power spinbox change (direct edit)."""
        power_percent = value

        # Update slider (block signals to prevent recursion)
        if laser_index in self._laser_sliders:
            self._laser_sliders[laser_index].blockSignals(True)
            self._laser_sliders[laser_index].setValue(int(power_percent))
            self._laser_sliders[laser_index].blockSignals(False)

        # Send to controller
        self.laser_led_controller.set_laser_power(laser_index, power_percent)

        # Log immediately for spinbox changes (user typed it in)
        self.logger.info(f"Laser {laser_index} power set to {power_percent:.1f}%")

    def _on_laser_slider_released(self, laser_index: int) -> None:
        """Handle laser slider release - log final value immediately."""
        if laser_index in self._laser_log_timers:
            # Stop timer and log now
            self._laser_log_timers[laser_index].stop()
            self._log_laser_power(laser_index)

    def _log_laser_power(self, laser_index: int) -> None:
        """Log laser power value (called after delay or slider release)."""
        if laser_index in self._laser_spinboxes:
            power = self._laser_spinboxes[laser_index].value()
            self.logger.info(f"Laser {laser_index} power set to {power:.1f}%")

    def _on_led_intensity_slider_changed(self, value: int) -> None:
        """Handle LED intensity slider change (while dragging)."""
        intensity_percent = float(value)

        # Update spinbox (block signals to prevent recursion)
        if self._led_spinbox:
            self._led_spinbox.blockSignals(True)
            self._led_spinbox.setValue(intensity_percent)
            self._led_spinbox.blockSignals(False)

        # Get current LED color from combobox
        led_color = self._led_combobox.currentIndex() if self._led_combobox else 0

        # Send to controller immediately (for live feedback)
        self.laser_led_controller.set_led_intensity(led_color, intensity_percent)

        # Restart timer for delayed logging (1 second after last change)
        if self._led_log_timer:
            self._led_log_timer.stop()
            self._led_log_timer.start(1000)  # 1 second delay

    def _on_led_intensity_spinbox_changed(self, value: float) -> None:
        """Handle LED intensity spinbox change (direct edit)."""
        intensity_percent = value

        # Update slider (block signals to prevent recursion)
        if self._led_slider:
            self._led_slider.blockSignals(True)
            self._led_slider.setValue(int(intensity_percent))
            self._led_slider.blockSignals(False)

        # Get current LED color from combobox
        led_color = self._led_combobox.currentIndex() if self._led_combobox else 0

        # Send to controller
        self.laser_led_controller.set_led_intensity(led_color, intensity_percent)

        # Log immediately for spinbox changes (user typed it in)
        color_names = ["Red", "Green", "Blue", "White"]
        self.logger.info(f"{color_names[led_color]} LED intensity set to {intensity_percent:.1f}%")

    def _on_led_slider_released(self) -> None:
        """Handle LED slider release - log final value immediately."""
        if self._led_log_timer:
            # Stop timer and log now
            self._led_log_timer.stop()
            self._log_led_intensity()

    def _log_led_intensity(self) -> None:
        """Log LED intensity value (called after delay or slider release)."""
        if self._led_spinbox and self._led_combobox:
            intensity = self._led_spinbox.value()
            led_color = self._led_combobox.currentIndex()
            color_names = ["Red", "Green", "Blue", "White"]
            self.logger.info(f"{color_names[led_color]} LED intensity set to {intensity:.1f}%")

    def _on_led_color_changed(self, index: int) -> None:
        """Handle LED color combobox change."""
        # Update slider and spinbox to reflect the selected LED color's current intensity
        intensity = self.laser_led_controller.get_led_intensity(index)

        if self._led_slider:
            self._led_slider.blockSignals(True)  # Prevent triggering intensity change
            self._led_slider.setValue(int(intensity))
            self._led_slider.blockSignals(False)

        if self._led_spinbox:
            self._led_spinbox.blockSignals(True)
            self._led_spinbox.setValue(intensity)
            self._led_spinbox.blockSignals(False)

        # If LED is currently selected, re-enable preview with new color
        if self._led_radio and self._led_radio.isChecked():
            color_names = ["Red", "Green", "Blue", "White"]
            self.logger.info(f"{color_names[index]} LED selected for preview")
            self.laser_led_controller.enable_led_for_preview(index)

    def _on_source_selected(self, button: QRadioButton) -> None:
        """Handle light source selection."""
        source_id = self._source_button_group.id(button)

        if source_id == -1:  # LED (get color from combobox)
            led_color = self._led_combobox.currentIndex() if self._led_combobox else 0
            color_names = ["Red", "Green", "Blue", "White"]
            self.logger.info(f"{color_names[led_color]} LED selected for preview")
            self.laser_led_controller.enable_led_for_preview(led_color)
        elif source_id > 0:  # Laser
            self.logger.info(f"Laser {source_id} selected for preview")
            self.laser_led_controller.enable_laser_for_preview(source_id)

    @pyqtSlot(str)
    def _on_preview_enabled(self, source_name: str) -> None:
        """Update UI when preview is enabled."""
        self._status_label.setText(f"✓ Active: {source_name} (allows live viewing)")
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

        self._status_label.setText("Select a light source for live viewing")
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
            String like "laser_3", "led_red", "led_green", "led_blue", "led_white", or "none"
        """
        checked_button = self._source_button_group.checkedButton()
        if not checked_button:
            return "none"

        source_id = self._source_button_group.id(checked_button)
        if source_id == -1:  # LED (get color from combobox)
            led_color = self._led_combobox.currentIndex() if self._led_combobox else 0
            color_names = ["red", "green", "blue", "white"]
            return f"led_{color_names[led_color]}"
        else:
            return f"laser_{source_id}"

    def is_source_active(self) -> bool:
        """Check if any light source is currently active."""
        return self.laser_led_controller.is_preview_active()
