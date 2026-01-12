"""
Illumination panel for workflow configuration.

Provides UI for selecting multiple laser light sources and LED simultaneously.
Compact layout with Advanced settings dialog for rarely-used options.
"""

import logging
from typing import Optional, Dict, Any, List, Tuple

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QSpinBox, QCheckBox, QGroupBox, QGridLayout,
    QPushButton, QSlider
)
from PyQt5.QtCore import pyqtSignal, Qt

from py2flamingo.models.data.workflow import IlluminationSettings


# Default laser channels - used when no instrument configuration is provided
# Format: (Display Name, Workflow Key, Default Power)
DEFAULT_LASER_CHANNELS = [
    ("405 nm", "Laser 1 405 nm", 5.0),
    ("488 nm", "Laser 2 488 nm", 5.0),
    ("561 nm", "Laser 3 561 nm", 5.0),
    ("640 nm", "Laser 4 640 nm", 5.0),
]

LED_COLORS = ["Red", "Green", "Blue", "White"]


class LaserRow(QWidget):
    """Compact horizontal row for a single laser channel."""

    value_changed = pyqtSignal()

    def __init__(self, display_name: str, default_power: float = 5.0, parent=None):
        super().__init__(parent)
        self._display_name = display_name

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        # Enable checkbox with laser name
        self._checkbox = QCheckBox(display_name)
        self._checkbox.setMinimumWidth(70)
        self._checkbox.stateChanged.connect(self._on_state_changed)
        layout.addWidget(self._checkbox)

        # Power slider
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(int(default_power))
        self._slider.setEnabled(False)
        self._slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self._slider, 1)

        # Power spinbox (synchronized with slider)
        self._spinbox = QDoubleSpinBox()
        self._spinbox.setRange(0.0, 100.0)
        self._spinbox.setValue(default_power)
        self._spinbox.setDecimals(1)
        self._spinbox.setSingleStep(0.5)
        self._spinbox.setSuffix(" %")
        self._spinbox.setFixedWidth(75)
        self._spinbox.setEnabled(False)
        self._spinbox.valueChanged.connect(self._on_spinbox_changed)
        layout.addWidget(self._spinbox)

    def _on_state_changed(self) -> None:
        """Handle checkbox state change."""
        enabled = self._checkbox.isChecked()
        self._slider.setEnabled(enabled)
        self._spinbox.setEnabled(enabled)
        self.value_changed.emit()

    def _on_slider_changed(self, value: int) -> None:
        """Handle slider value change."""
        self._spinbox.blockSignals(True)
        self._spinbox.setValue(float(value))
        self._spinbox.blockSignals(False)
        self.value_changed.emit()

    def _on_spinbox_changed(self, value: float) -> None:
        """Handle spinbox value change."""
        self._slider.blockSignals(True)
        self._slider.setValue(int(value))
        self._slider.blockSignals(False)
        self.value_changed.emit()

    def is_enabled(self) -> bool:
        """Check if this laser is enabled."""
        return self._checkbox.isChecked()

    def set_enabled(self, enabled: bool) -> None:
        """Set whether this laser is enabled."""
        self._checkbox.setChecked(enabled)

    def get_power(self) -> float:
        """Get current power value."""
        return self._spinbox.value()

    def set_power(self, power: float) -> None:
        """Set power value."""
        self._spinbox.setValue(power)
        self._slider.setValue(int(power))


class IlluminationPanel(QWidget):
    """
    Panel for configuring workflow illumination settings.

    Provides:
    - Compact laser rows with checkbox, slider, and power value
    - Simple LED enable with intensity
    - Advanced button for path selection, multi-laser mode, LED DAC

    Signals:
        settings_changed: Emitted when illumination settings change
    """

    settings_changed = pyqtSignal(object)  # Emits dict of settings

    def __init__(self, parent: Optional[QWidget] = None,
                 laser_channels: Optional[List[Tuple[str, str, float]]] = None,
                 app=None):
        """
        Initialize illumination panel.

        Args:
            parent: Parent widget
            laser_channels: Optional list of (display_name, workflow_key, default_power) tuples.
                           If not provided, will try to get from app or use defaults.
            app: Optional FlamingoApplication instance for getting laser info from instrument
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._app = app

        # Determine laser channels to use
        self._laser_channels = self._resolve_laser_channels(laser_channels, app)

        # Storage for laser rows
        self._laser_rows: List[LaserRow] = []

        # Advanced settings (stored here, edited via dialog)
        self._multi_laser_mode = False
        self._led_color_index = 3  # White
        self._led_dac_percent = 50.0  # Default 50% (was 32768 / 65535)

        self._setup_ui()

    def _resolve_laser_channels(self, laser_channels: Optional[List], app) -> List[Tuple[str, str, float]]:
        """Resolve laser channels from provided list, app, or defaults."""
        if laser_channels is not None:
            self._logger.info(f"Using provided laser channels: {len(laser_channels)} lasers")
            return laser_channels

        if app is not None:
            try:
                laser_led_service = getattr(app, 'laser_led_service', None)
                if laser_led_service is not None:
                    lasers = laser_led_service.get_available_lasers()
                    if lasers:
                        channels = []
                        for laser in lasers:
                            display_name = f"{laser.wavelength} nm"
                            workflow_key = f"Laser {laser.index} {laser.wavelength} nm"
                            channels.append((display_name, workflow_key, 5.0))
                        self._logger.info(f"Loaded {len(channels)} lasers from instrument")
                        return channels
            except Exception as e:
                self._logger.warning(f"Could not get lasers from app: {e}")

        self._logger.info("Using default laser channels")
        return DEFAULT_LASER_CHANNELS

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Illumination group
        group = QGroupBox("Illumination")
        group_layout = QVBoxLayout()
        group_layout.setSpacing(4)

        # Illumination Path (always visible - fundamental setting)
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("Path:"))

        self._left_path = QCheckBox("Left")
        self._left_path.setChecked(True)
        self._left_path.stateChanged.connect(self._on_settings_changed)
        path_layout.addWidget(self._left_path)

        self._right_path = QCheckBox("Right")
        self._right_path.setChecked(False)
        self._right_path.stateChanged.connect(self._on_settings_changed)
        path_layout.addWidget(self._right_path)

        path_layout.addStretch()

        self._advanced_btn = QPushButton("Advanced...")
        self._advanced_btn.setFixedWidth(90)
        self._advanced_btn.clicked.connect(self._on_advanced_clicked)
        path_layout.addWidget(self._advanced_btn)

        group_layout.addLayout(path_layout)

        # Laser header
        laser_label = QLabel("Lasers")
        laser_label.setStyleSheet("font-weight: bold;")
        group_layout.addWidget(laser_label)

        # Create compact laser rows
        for display_name, workflow_key, default_power in self._laser_channels:
            row = LaserRow(display_name, default_power)
            row.value_changed.connect(self._on_settings_changed)
            self._laser_rows.append(row)
            group_layout.addWidget(row)

        # LED section - compact single row
        led_layout = QHBoxLayout()
        led_layout.setSpacing(8)

        self._led_enable = QCheckBox("LED")
        self._led_enable.setMinimumWidth(70)
        self._led_enable.stateChanged.connect(self._on_led_enable_changed)
        led_layout.addWidget(self._led_enable)

        self._led_slider = QSlider(Qt.Horizontal)
        self._led_slider.setRange(0, 100)
        self._led_slider.setValue(50)
        self._led_slider.setEnabled(False)
        self._led_slider.valueChanged.connect(self._on_led_slider_changed)
        led_layout.addWidget(self._led_slider, 1)

        self._led_intensity = QDoubleSpinBox()
        self._led_intensity.setRange(0.0, 100.0)
        self._led_intensity.setValue(50.0)
        self._led_intensity.setDecimals(1)
        self._led_intensity.setSingleStep(5.0)
        self._led_intensity.setSuffix(" %")
        self._led_intensity.setFixedWidth(75)
        self._led_intensity.setEnabled(False)
        self._led_intensity.valueChanged.connect(self._on_led_spinbox_changed)
        led_layout.addWidget(self._led_intensity)

        group_layout.addLayout(led_layout)

        group.setLayout(group_layout)
        layout.addWidget(group)

    def _on_led_enable_changed(self) -> None:
        """Handle LED enable checkbox state change."""
        enabled = self._led_enable.isChecked()
        self._led_slider.setEnabled(enabled)
        self._led_intensity.setEnabled(enabled)
        self._on_settings_changed()

    def _on_led_slider_changed(self, value: int) -> None:
        """Handle LED slider change."""
        self._led_intensity.blockSignals(True)
        self._led_intensity.setValue(float(value))
        self._led_intensity.blockSignals(False)
        self._on_settings_changed()

    def _on_led_spinbox_changed(self, value: float) -> None:
        """Handle LED spinbox change."""
        self._led_slider.blockSignals(True)
        self._led_slider.setValue(int(value))
        self._led_slider.blockSignals(False)
        self._on_settings_changed()

    def _on_advanced_clicked(self) -> None:
        """Open advanced illumination settings dialog."""
        from py2flamingo.views.dialogs import AdvancedIlluminationDialog

        dialog = AdvancedIlluminationDialog(self)
        dialog.set_settings({
            'multi_laser_mode': self._multi_laser_mode,
            'led_color_index': self._led_color_index,
            'led_dac_percent': self._led_dac_percent,
        })

        if dialog.exec_() == dialog.Accepted:
            settings = dialog.get_settings()
            self._multi_laser_mode = settings['multi_laser_mode']
            self._led_color_index = settings['led_color_index']
            self._led_dac_percent = settings['led_dac_percent']
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
        for i, (display_name, workflow_key, _) in enumerate(self._laser_channels):
            if self._laser_rows[i].is_enabled():
                power = self._laser_rows[i].get_power()
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
        for i, (display_name, workflow_key, _) in enumerate(self._laser_channels):
            if self._laser_rows[i].is_enabled():
                power = self._laser_rows[i].get_power()
                settings[workflow_key] = f"{power:.2f} 1"
            else:
                settings[workflow_key] = "0.00 0"

        # LED settings
        if self._led_enable.isChecked():
            intensity = self._led_intensity.value()
            # Convert LED DAC percentage to hardware value (0-65535)
            led_dac_value = int(self._led_dac_percent * 655.35)
            settings["LED_RGB_Board"] = f"{intensity:.1f} 1"
            settings["LED selection"] = f"{self._led_color_index} 1"
            settings["LED DAC"] = f"{led_dac_value} 1"
        else:
            settings["LED_RGB_Board"] = "0.0 0"
            settings["LED selection"] = "0 0"
            settings["LED DAC"] = "0 0"

        # Light paths (now on main panel)
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
            "Run stack with multiple lasers on": "true" if self._multi_laser_mode else "false"
        }

    def set_settings(self, settings: List[IlluminationSettings]) -> None:
        """
        Set illumination settings from list of objects.

        Args:
            settings: List of IlluminationSettings to apply
        """
        # Reset all
        for row in self._laser_rows:
            row.set_enabled(False)
        self._led_enable.setChecked(False)

        # Apply each setting
        for setting in settings:
            if setting.laser_enabled and setting.laser_channel:
                for i, (_, workflow_key, _) in enumerate(self._laser_channels):
                    if workflow_key == setting.laser_channel:
                        self._laser_rows[i].set_enabled(True)
                        self._laser_rows[i].set_power(setting.laser_power_mw)
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
        for row in self._laser_rows:
            row.set_enabled(False)
        self._led_enable.setChecked(False)

        # Parse laser settings
        for i, (_, workflow_key, default_power) in enumerate(self._laser_channels):
            if workflow_key in illumination_dict:
                value_str = illumination_dict[workflow_key]
                parts = value_str.split()
                if len(parts) == 2:
                    power = float(parts[0])
                    enabled = int(parts[1]) == 1
                    if enabled:
                        self._laser_rows[i].set_enabled(True)
                        self._laser_rows[i].set_power(power)

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
                self._led_color_index = int(parts[0])

        # Parse LED DAC (convert from hardware value to percentage)
        if "LED DAC" in illumination_dict:
            value_str = illumination_dict["LED DAC"]
            parts = value_str.split()
            if len(parts) >= 1:
                led_dac_value = int(parts[0])
                # Convert hardware DAC (0-65535) to percentage (0-100)
                self._led_dac_percent = led_dac_value / 655.35

        # Parse light paths (now on main panel)
        if "Left path" in illumination_dict:
            self._left_path.setChecked(illumination_dict["Left path"].startswith("ON"))

        if "Right path" in illumination_dict:
            self._right_path.setChecked(illumination_dict["Right path"].startswith("ON"))

        # Parse illumination options
        if options_dict and "Run stack with multiple lasers on" in options_dict:
            self._multi_laser_mode = options_dict["Run stack with multiple lasers on"].lower() == "true"

    def get_enabled_laser_count(self) -> int:
        """Get the number of enabled lasers."""
        return sum(1 for row in self._laser_rows if row.is_enabled())

    def get_enabled_source_count(self) -> int:
        """Get the total number of enabled illumination sources."""
        count = self.get_enabled_laser_count()
        if self._led_enable.isChecked():
            count += 1
        return count

    # Advanced settings accessors
    def get_advanced_settings(self) -> Dict[str, Any]:
        """Get advanced illumination settings."""
        return {
            'multi_laser_mode': self._multi_laser_mode,
            'led_color_index': self._led_color_index,
            'led_dac_percent': self._led_dac_percent,
        }

    def set_advanced_settings(self, settings: Dict[str, Any]) -> None:
        """Set advanced illumination settings."""
        if 'multi_laser_mode' in settings:
            self._multi_laser_mode = settings['multi_laser_mode']
        if 'led_color_index' in settings:
            self._led_color_index = settings['led_color_index']
        if 'led_dac_percent' in settings:
            self._led_dac_percent = settings['led_dac_percent']

    def get_ui_state(self) -> Dict[str, Any]:
        """
        Get UI state for persistence.

        Returns a dictionary with all UI settings that can be saved and restored.

        Returns:
            Dictionary with UI state
        """
        # Collect laser states
        lasers = []
        for i, (display_name, workflow_key, _) in enumerate(self._laser_channels):
            lasers.append({
                'workflow_key': workflow_key,
                'enabled': self._laser_rows[i].is_enabled(),
                'power': self._laser_rows[i].get_power(),
            })

        return {
            'lasers': lasers,
            'led_enabled': self._led_enable.isChecked(),
            'led_intensity': self._led_intensity.value(),
            'left_path': self._left_path.isChecked(),
            'right_path': self._right_path.isChecked(),
            'multi_laser_mode': self._multi_laser_mode,
            'led_color_index': self._led_color_index,
            'led_dac_percent': self._led_dac_percent,
        }

    def set_ui_state(self, state: Dict[str, Any]) -> None:
        """
        Restore UI state from persistence.

        Args:
            state: Dictionary with UI state from get_ui_state()
        """
        if not state:
            return

        # Restore laser states
        if 'lasers' in state:
            for laser_state in state['lasers']:
                workflow_key = laser_state.get('workflow_key', '')
                for i, (_, key, _) in enumerate(self._laser_channels):
                    if key == workflow_key:
                        self._laser_rows[i].set_enabled(laser_state.get('enabled', False))
                        self._laser_rows[i].set_power(laser_state.get('power', 5.0))
                        break

        if 'led_enabled' in state:
            self._led_enable.setChecked(state['led_enabled'])

        if 'led_intensity' in state:
            self._led_intensity.setValue(state['led_intensity'])

        if 'left_path' in state:
            self._left_path.setChecked(state['left_path'])

        if 'right_path' in state:
            self._right_path.setChecked(state['right_path'])

        if 'multi_laser_mode' in state:
            self._multi_laser_mode = state['multi_laser_mode']

        if 'led_color_index' in state:
            self._led_color_index = state['led_color_index']

        if 'led_dac_percent' in state:
            self._led_dac_percent = state['led_dac_percent']
