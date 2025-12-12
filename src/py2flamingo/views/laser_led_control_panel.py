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
    QRadioButton, QCheckBox, QButtonGroup, QGroupBox, QGridLayout, QComboBox, QDoubleSpinBox
)
from PyQt5.QtCore import Qt, pyqtSlot, QTimer

from py2flamingo.controllers.laser_led_controller import LaserLEDController
from py2flamingo.views.colors import SUCCESS_BG, WARNING_BG, ERROR_BG


class LaserLEDControlPanel(QWidget):
    """
    Control panel for laser and LED selection and power/intensity adjustment.

    Features:
    - Radio buttons to select active light source
    - Power/intensity sliders for each source
    - Visual indication of active source
    - Automatic preview mode enabling
    """

    # Button ID for LED in the source button group.
    # IMPORTANT: Cannot use -1 because Qt auto-assigns IDs starting from -1.
    # Using a large negative number to avoid collision with laser indices (1-4).
    LED_BUTTON_ID = -100

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

        # Light path selection (TSPIM - for lasers only)
        self._path_group = None  # QGroupBox for path selection
        self._left_path_radio = None  # Left path radio button
        self._right_path_radio = None  # Right path radio button
        self._laser_path = "left"  # Current path selection (default: left)

        # Timers for delayed logging (reduce spam)
        self._laser_log_timers = {}  # laser_index -> QTimer
        self._led_log_timer = None

        # Button group for checkboxes (with manual mutual exclusivity)
        self._source_button_group = QButtonGroup()
        self._source_button_group.setExclusive(False)  # Allow unchecking all
        self._source_button_group.buttonClicked.connect(self._on_source_clicked)

        # Connect controller signals
        self.laser_led_controller.preview_enabled.connect(self._on_preview_enabled)
        self.laser_led_controller.preview_disabled.connect(self._on_preview_disabled)
        self.laser_led_controller.error_occurred.connect(self._on_error)
        self.laser_led_controller.laser_power_changed.connect(self._on_laser_power_updated)
        self.laser_led_controller.led_intensity_changed.connect(self._on_led_intensity_updated)

        self._setup_ui()

        self.logger.info("LaserLEDControlPanel initialized")

    def _setup_ui(self) -> None:
        """Create and layout all UI components."""
        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)

        # Title
        title = QLabel("<b>Light Source Control</b>")
        title.setStyleSheet("font-size: 11pt;")
        main_layout.addWidget(title)

        # Lasers section
        lasers_group = self._create_lasers_section()
        if lasers_group:
            main_layout.addWidget(lasers_group)

        # LED section
        led_group = self._create_led_section()
        if led_group:
            main_layout.addWidget(led_group)

        # Light path selection (for lasers only)
        self._path_group = self._create_path_selection_section()
        main_layout.addWidget(self._path_group)
        # Initially hidden until a laser is selected
        self._path_group.setVisible(False)

        # Status
        self._status_label = QLabel("Select a light source for live viewing")
        self._status_label.setStyleSheet(
            f"background-color: {WARNING_BG}; color: #856404; padding: 8px; "
            f"border: 1px solid #ffc107; border-radius: 4px; font-weight: bold;"
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

            # Checkbox (with mutual exclusivity managed manually)
            checkbox = QCheckBox()
            self._laser_radios[laser.index] = checkbox
            self._source_button_group.addButton(checkbox, laser.index)  # ID is laser index
            layout.addWidget(checkbox, row, 0, Qt.AlignCenter)

            # Laser name
            name_label = QLabel(laser.name)
            layout.addWidget(name_label, row, 1)

            # Power percentage spinbox (editable)
            power = self.laser_led_controller.get_laser_power(laser.index)
            power_spinbox = QDoubleSpinBox()
            power_spinbox.setRange(0.0, 100.0)
            power_spinbox.setValue(power)
            power_spinbox.setDecimals(2)  # Show 2 decimals for scientific precision
            power_spinbox.setSuffix("%")
            power_spinbox.setMinimumWidth(80)  # Wider for 2 decimals
            power_spinbox.setStyleSheet("font-weight: bold;")
            # Use editingFinished to only update when user presses Enter or clicks away
            power_spinbox.editingFinished.connect(
                lambda idx=laser.index: self._on_laser_power_spinbox_finished(idx)
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

        # Checkbox for selecting LED as light source (with mutual exclusivity managed manually)
        self._led_radio = QCheckBox()
        # Use constant LED_BUTTON_ID for LED (not -1, which Qt auto-assigns)
        self._source_button_group.addButton(self._led_radio, self.LED_BUTTON_ID)
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
        # Use editingFinished to only update when user presses Enter or clicks away
        self._led_spinbox.editingFinished.connect(self._on_led_intensity_spinbox_finished)
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

    def _create_path_selection_section(self) -> QGroupBox:
        """
        Create light path selection section (TSPIM - for lasers only).

        This section allows selecting between left and right illumination paths.
        It is only visible when a laser is selected (not for LED).
        """
        group = QGroupBox("Light Path Selection (Preview)")
        layout = QHBoxLayout()
        layout.setSpacing(15)

        # Info label
        info = QLabel("Select illumination path:")
        layout.addWidget(info)

        # Left path radio button
        self._left_path_radio = QRadioButton("Left Path")
        self._left_path_radio.setChecked(True)  # Default to left
        self._left_path_radio.toggled.connect(self._on_path_selection_changed)
        layout.addWidget(self._left_path_radio)

        # Right path radio button
        self._right_path_radio = QRadioButton("Right Path")
        self._right_path_radio.toggled.connect(self._on_path_selection_changed)
        layout.addWidget(self._right_path_radio)

        layout.addStretch()

        group.setLayout(layout)
        return group

    def _on_path_selection_changed(self) -> None:
        """Handle light path selection change."""
        # Determine which path is selected
        if self._left_path_radio and self._left_path_radio.isChecked():
            self._laser_path = "left"
            path_name = "LEFT"
        elif self._right_path_radio and self._right_path_radio.isChecked():
            self._laser_path = "right"
            path_name = "RIGHT"
        else:
            return

        self.logger.info(f"Light path changed to {path_name}")

        # If a laser is currently active, re-enable it with new path
        checked_button = self._source_button_group.checkedButton()
        if checked_button and checked_button != self._led_radio:
            # This is a laser button - button ID IS the laser number (1-4)
            source_id = self._source_button_group.id(checked_button)
            if source_id >= 1:  # Laser (button IDs are laser.index: 1, 2, 3, 4)
                laser_number = source_id
                self.logger.info(f"Re-enabling laser {laser_number} with {path_name} path")
                self.laser_led_controller.enable_laser_for_preview(laser_number, self._laser_path)

    def _on_laser_power_slider_changed(self, laser_index: int, value: int) -> None:
        """Handle laser power slider change (while dragging)."""
        power_percent = float(value)

        # Update spinbox (block signals to prevent recursion)
        if laser_index in self._laser_spinboxes:
            self._laser_spinboxes[laser_index].blockSignals(True)
            self._laser_spinboxes[laser_index].setValue(power_percent)
            self._laser_spinboxes[laser_index].blockSignals(False)

        # IMPORTANT: Don't verify during drag for performance - will verify on release
        # Just update the cached value for now
        self.laser_led_controller._laser_powers[laser_index] = power_percent

        # Restart timer for delayed logging (1 second after last change)
        if laser_index in self._laser_log_timers:
            self._laser_log_timers[laser_index].stop()
            self._laser_log_timers[laser_index].start(1000)  # 1 second delay

    def _on_laser_power_spinbox_finished(self, laser_index: int) -> None:
        """Handle laser power spinbox editing finished (Enter or focus loss)."""
        # Get current value from spinbox
        if laser_index not in self._laser_spinboxes:
            return

        requested_power = self._laser_spinboxes[laser_index].value()

        # Send to controller and get actual power from hardware
        success, actual_power = self.laser_led_controller.set_laser_power(laser_index, requested_power)

        if success:
            # Update GUI with actual power from hardware (may differ due to DAC quantization)
            if abs(actual_power - requested_power) > 0.1:
                # Power was adjusted by hardware - update spinbox and slider
                self._laser_spinboxes[laser_index].blockSignals(True)
                self._laser_spinboxes[laser_index].setValue(actual_power)
                self._laser_spinboxes[laser_index].blockSignals(False)

                self.logger.info(f"Laser {laser_index}: requested {requested_power:.1f}%, "
                               f"actual {actual_power:.1f}% (hardware quantization)")

            # Update slider with actual power (block signals to prevent recursion)
            if laser_index in self._laser_sliders:
                self._laser_sliders[laser_index].blockSignals(True)
                self._laser_sliders[laser_index].setValue(int(actual_power))
                self._laser_sliders[laser_index].blockSignals(False)
        else:
            self.logger.error(f"Failed to set laser {laser_index} power")

    def _on_laser_slider_released(self, laser_index: int) -> None:
        """Handle laser slider release - send command and verify actual power."""
        # Stop logging timer
        if laser_index in self._laser_log_timers:
            self._laser_log_timers[laser_index].stop()

        # Get current slider value
        if laser_index not in self._laser_sliders:
            return

        requested_power = float(self._laser_sliders[laser_index].value())

        # Send to controller and get actual power from hardware
        success, actual_power = self.laser_led_controller.set_laser_power(laser_index, requested_power)

        if success:
            # Update GUI with actual power from hardware (may differ due to DAC quantization)
            if abs(actual_power - requested_power) > 0.1:
                # Power was adjusted by hardware - update spinbox and slider
                self._laser_spinboxes[laser_index].blockSignals(True)
                self._laser_spinboxes[laser_index].setValue(actual_power)
                self._laser_spinboxes[laser_index].blockSignals(False)

                self._laser_sliders[laser_index].blockSignals(True)
                self._laser_sliders[laser_index].setValue(int(actual_power))
                self._laser_sliders[laser_index].blockSignals(False)

                self.logger.info(f"Laser {laser_index}: requested {requested_power:.1f}%, "
                               f"actual {actual_power:.1f}% (hardware quantization)")
            else:
                self.logger.info(f"Laser {laser_index} power set to {actual_power:.1f}%")
        else:
            self.logger.error(f"Failed to set laser {laser_index} power")
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

    def _on_led_intensity_spinbox_finished(self) -> None:
        """Handle LED intensity spinbox editing finished (Enter or focus loss)."""
        # Get current value from spinbox
        if not self._led_spinbox:
            return

        intensity_percent = self._led_spinbox.value()

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

    def _on_source_clicked(self, button: QCheckBox) -> None:
        """
        Handle light source checkbox clicks with manual mutual exclusivity.

        Allows only one checkbox to be checked at a time, but permits unchecking all
        (unlike radio buttons which always have one selected).
        """
        source_id = self._source_button_group.id(button)
        is_checked = button.isChecked()

        self.logger.debug(f"Light source clicked: button ID = {source_id}, checked = {is_checked}")

        if is_checked:
            # Checkbox was just checked - uncheck all others (manual mutual exclusivity)
            for other_button in self._source_button_group.buttons():
                if other_button != button and other_button.isChecked():
                    other_button.setChecked(False)

            # Now enable the selected source (async for UI responsiveness)
            if button == self._led_radio:
                # LED selected - hide path selection (LED doesn't use paths)
                if self._path_group:
                    self._path_group.setVisible(False)

                led_color = self._led_combobox.currentIndex() if self._led_combobox else 0
                color_names = ["Red", "Green", "Blue", "White"]
                self.logger.info(f"{color_names[led_color]} LED selected for preview")
                # Use async method to avoid blocking UI
                self.laser_led_controller.enable_led_for_preview_async(led_color)

            elif source_id >= 1:  # Laser (button IDs are laser.index: 1, 2, 3, 4)
                # Laser selected - show path selection
                if self._path_group:
                    self._path_group.setVisible(True)

                # Button ID IS the laser number (1-4) - set from laser.index in _create_lasers_section
                laser_number = source_id
                # Enable laser with current path selection (async for responsiveness)
                self.logger.info(f"Laser {laser_number} selected for preview on {self._laser_path.upper()} path")
                self.laser_led_controller.enable_laser_for_preview_async(laser_number, self._laser_path)

            else:
                # This shouldn't happen - all buttons should be LED or lasers (1-4)
                self.logger.warning(f"Unhandled source button with ID {source_id}")

        else:
            # Checkbox was unchecked - disable all sources
            self.logger.info("All light sources unchecked - disabling preview")
            if self._path_group:
                self._path_group.setVisible(False)
            self.laser_led_controller.disable_all_light_sources()

    @pyqtSlot(int, float)
    def _on_laser_power_updated(self, laser_index: int, power_percent: float) -> None:
        """
        Update laser power widgets when controller reports power change.

        This is called when the controller queries hardware or sets power.
        """
        # Update spinbox
        if laser_index in self._laser_spinboxes:
            self._laser_spinboxes[laser_index].blockSignals(True)
            self._laser_spinboxes[laser_index].setValue(power_percent)
            self._laser_spinboxes[laser_index].blockSignals(False)

        # Update slider
        if laser_index in self._laser_sliders:
            self._laser_sliders[laser_index].blockSignals(True)
            self._laser_sliders[laser_index].setValue(int(power_percent))
            self._laser_sliders[laser_index].blockSignals(False)

        self.logger.debug(f"GUI updated: Laser {laser_index} power = {power_percent:.1f}%")

    @pyqtSlot(float)
    def _on_led_intensity_updated(self, intensity_percent: float) -> None:
        """
        Update LED intensity widgets when controller reports intensity change.

        This is called when the controller sets LED intensity.
        """
        # Update spinbox
        if self._led_spinbox:
            self._led_spinbox.blockSignals(True)
            self._led_spinbox.setValue(intensity_percent)
            self._led_spinbox.blockSignals(False)

        # Update slider
        if self._led_slider:
            self._led_slider.blockSignals(True)
            self._led_slider.setValue(int(intensity_percent))
            self._led_slider.blockSignals(False)

        self.logger.debug(f"GUI updated: LED intensity = {intensity_percent:.1f}%")

    @pyqtSlot(str)
    def _on_preview_enabled(self, source_name: str) -> None:
        """Update UI when preview is enabled."""
        self._status_label.setText(f"✓ Active: {source_name} (allows live viewing)")
        self._status_label.setStyleSheet(
            f"background-color: {SUCCESS_BG}; color: #155724; padding: 8px; "
            f"border: 1px solid #c3e6cb; border-radius: 4px; font-weight: bold;"
        )
        self.logger.info(f"Preview enabled: {source_name}")

    @pyqtSlot()
    def _on_preview_disabled(self) -> None:
        """
        Update UI when preview is disabled.

        Note: Checkboxes are NOT unchecked - they remember the user's desired state.
        When live view restarts, checked sources will be automatically re-enabled.
        """
        # Don't uncheck checkboxes - remember user's intent for when live view restarts

        # Hide path selection when nothing is active
        if self._path_group:
            self._path_group.setVisible(False)

        self._status_label.setText("Illumination off (live view stopped) - restart to restore")
        self._status_label.setStyleSheet(
            f"background-color: {WARNING_BG}; color: #856404; padding: 8px; "
            f"border: 1px solid #ffc107; border-radius: 4px; font-weight: bold;"
        )
        self.logger.info("Preview disabled (checkboxes left checked to remember user intent)")

    @pyqtSlot(str)
    def _on_error(self, error_message: str) -> None:
        """Display error message."""
        self._status_label.setText(f"Error: {error_message}")
        self._status_label.setStyleSheet(
            f"background-color: {ERROR_BG}; color: #721c24; padding: 8px; "
            f"border: 1px solid #f5c6cb; border-radius: 4px; font-weight: bold;"
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
        if source_id == self.LED_BUTTON_ID:  # LED (get color from combobox)
            led_color = self._led_combobox.currentIndex() if self._led_combobox else 0
            color_names = ["red", "green", "blue", "white"]
            return f"led_{color_names[led_color]}"
        elif source_id >= 1:  # Laser (button IDs are laser.index: 1-4)
            laser_number = source_id
            return f"laser_{laser_number}"
        else:
            # Unknown source (shouldn't happen)
            self.logger.warning(f"Unknown source button ID: {source_id}")
            return "none"

    def is_source_active(self) -> bool:
        """Check if any light source is currently active."""
        return self.laser_led_controller.is_preview_active()

    def restore_checked_illumination(self) -> None:
        """
        Restore illumination for any checked light source.

        Called when live view is restarted to automatically re-enable
        the light source that was previously selected by the user.
        """
        checked_button = self._source_button_group.checkedButton()
        if not checked_button:
            self.logger.debug("No light source checked - nothing to restore")
            return

        source_id = self._source_button_group.id(checked_button)

        if source_id == self.LED_BUTTON_ID:  # LED
            # Get LED color from combobox
            led_color = self._led_combobox.currentIndex() if self._led_combobox else 0
            color_names = ["red", "green", "blue", "white"]
            color_name = color_names[led_color]

            # Get LED intensity from slider and set it in the controller
            intensity = self._led_slider.value() if self._led_slider else 50
            self.laser_led_controller.set_led_intensity(led_color, float(intensity))

            self.logger.info(f"Restoring LED illumination: {color_name} at {intensity}%")
            self.laser_led_controller.enable_led_for_preview(led_color)

        elif source_id >= 1:  # Laser (button IDs are laser.index: 1-4)
            laser_number = source_id

            # Get laser power from slider (sliders are keyed by laser.index, same as source_id)
            if laser_number in self._laser_sliders:
                power = self._laser_sliders[laser_number].value()
            else:
                power = 5.0  # Default power

            self.logger.info(f"Restoring laser {laser_number} illumination at {power}% on {self._laser_path} path")
            # First ensure power is set in controller's cache, then enable
            self.laser_led_controller.set_laser_power(laser_number, power)
            self.laser_led_controller.enable_laser_for_preview(laser_number, self._laser_path)

        else:
            # Unknown source ID (shouldn't happen with proper button setup)
            self.logger.warning(f"Cannot restore illumination - unknown source ID: {source_id}")
