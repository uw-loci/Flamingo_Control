"""
Z-Stack settings panel for workflow configuration.

Provides UI for Z-stack acquisition parameters.
"""

import logging
import math
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QSpinBox, QCheckBox, QGroupBox, QGridLayout, QComboBox
)
from PyQt5.QtCore import pyqtSignal

from py2flamingo.models.data.workflow import StackSettings


# System limits for Z velocity (from C++ SystemLimits.h)
Z_VELOCITY_MIN_MM_S = 0.001
Z_VELOCITY_MAX_MM_S = 1.0

# Stack option values
STACK_OPTIONS = [
    "None",
    "ZStack",
    "ZStack Movie",
    "Tile",
    "ZSweep",
    "OPT",
    "OPT ZStacks",
    "Bidirectional"
]


class ZStackPanel(QWidget):
    """
    Panel for configuring Z-stack acquisition settings.

    Provides:
    - Number of planes
    - Z step size (um)
    - Z velocity (mm/s)
    - Stack option dropdown (ZStack, Tile, OPT, etc.)
    - Tile settings (when Tile option selected)
    - Rotational stage velocity
    - Return to start option
    - Calculated Z range display

    Signals:
        settings_changed: Emitted when Z-stack settings change
    """

    settings_changed = pyqtSignal(object)  # Emits StackSettings

    def __init__(self, parent: Optional[QWidget] = None, app=None):
        """
        Initialize Z-stack panel.

        Args:
            parent: Parent widget
            app: FlamingoApplication instance for getting system settings
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._app = app

        # Get system defaults
        self._default_z_velocity = self._get_default_z_velocity()

        # Frame rate for Z velocity calculation (default 100 fps)
        self._frame_rate = 100.0

        # Flag to prevent recursive updates
        self._updating = False

        # Z range can be set externally (for tile collection)
        self._z_range_mm = None  # None means calculate from num_planes * z_step
        self._auto_num_planes = False  # Auto-calculate num_planes from z_range and z_step

        self._setup_ui()

    def _get_default_z_velocity(self) -> float:
        """Get default Z velocity from system configuration.

        Returns:
            Default Z stage velocity in mm/s
        """
        default = 0.4  # mm/s

        if self._app is None:
            return default

        try:
            # Try to get from config_service -> scope settings
            config_service = getattr(self._app, 'config_service', None)
            if config_service is not None:
                scope_settings = config_service.config.get('scope_settings', {})
                stage_limits = scope_settings.get('Stage limits', {})
                z_velocity = stage_limits.get('Default velocity z-axis', None)
                if z_velocity is not None:
                    velocity = float(z_velocity)
                    self._logger.info(f"Using Z velocity from system: {velocity} mm/s")
                    return velocity
        except Exception as e:
            self._logger.warning(f"Could not get Z velocity from system: {e}")

        return default

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Z-Stack settings group
        group = QGroupBox("Z-Stack Settings")
        grid = QGridLayout()
        grid.setSpacing(8)

        # Z range (can be set externally for tile collection, or calculated from num_planes)
        grid.addWidget(QLabel("Z Range:"), 0, 0)
        z_range_layout = QHBoxLayout()
        z_range_layout.setContentsMargins(0, 0, 0, 0)
        z_range_layout.setSpacing(8)

        self._z_range_spinbox = QDoubleSpinBox()
        self._z_range_spinbox.setRange(0.001, 100.0)  # 1 um to 100 mm
        self._z_range_spinbox.setValue(0.250)  # Default 250 um = 0.25 mm
        self._z_range_spinbox.setDecimals(4)
        self._z_range_spinbox.setSingleStep(0.001)
        self._z_range_spinbox.setSuffix(" mm")
        self._z_range_spinbox.valueChanged.connect(self._on_z_range_changed)
        self._z_range_spinbox.setVisible(False)  # Hidden by default, shown only in tile mode
        z_range_layout.addWidget(self._z_range_spinbox)

        self._z_range_label = QLabel("250.0 um")
        self._z_range_label.setStyleSheet("font-weight: bold;")
        z_range_layout.addWidget(self._z_range_label)

        grid.addLayout(z_range_layout, 0, 1)

        # Number of planes row with auto-calculate checkbox
        num_planes_layout = QHBoxLayout()
        num_planes_layout.setContentsMargins(0, 0, 0, 0)
        num_planes_layout.setSpacing(8)

        self._num_planes = QSpinBox()
        self._num_planes.setRange(1, 50000)  # Support large Z ranges (100mm / 0.002mm = 50000)
        self._num_planes.setValue(100)
        self._num_planes.valueChanged.connect(self._on_num_planes_changed)
        num_planes_layout.addWidget(self._num_planes)

        self._auto_num_planes_checkbox = QCheckBox("Auto")
        self._auto_num_planes_checkbox.setChecked(False)
        self._auto_num_planes_checkbox.setToolTip(
            "Auto-calculate number of planes from Z range and Z step.\n"
            "Formula: Num_planes = ceiling(Z_range / Z_step) + 1"
        )
        self._auto_num_planes_checkbox.stateChanged.connect(self._on_auto_num_planes_changed)
        self._auto_num_planes_checkbox.setVisible(False)  # Hidden by default, shown only in tile mode
        num_planes_layout.addWidget(self._auto_num_planes_checkbox)

        grid.addWidget(QLabel("Number of Planes:"), 1, 0)
        grid.addLayout(num_planes_layout, 1, 1)

        # Z step size (in micrometers, stored as mm in workflow)
        grid.addWidget(QLabel("Z Step:"), 2, 0)
        self._z_step = QDoubleSpinBox()
        self._z_step.setRange(0.1, 100.0)
        self._z_step.setValue(2.5)  # Default 2.5 um
        self._z_step.setDecimals(2)
        self._z_step.setSingleStep(0.1)
        self._z_step.setSuffix(" um")
        self._z_step.valueChanged.connect(self._on_settings_changed)
        grid.addWidget(self._z_step, 2, 1)

        # Frame rate display (read-only, set by CameraPanel)
        grid.addWidget(QLabel("Frame Rate:"), 3, 0)
        self._frame_rate_label = QLabel("100.0 fps")
        self._frame_rate_label.setStyleSheet("color: #666;")
        self._frame_rate_label.setToolTip("Frame rate from Camera settings")
        grid.addWidget(self._frame_rate_label, 3, 1)

        # Z velocity row with auto-calculate checkbox
        z_vel_layout = QHBoxLayout()
        z_vel_layout.setContentsMargins(0, 0, 0, 0)
        z_vel_layout.setSpacing(8)

        self._z_velocity = QDoubleSpinBox()
        self._z_velocity.setRange(0.001, 2.0)
        self._z_velocity.setValue(self._default_z_velocity)
        self._z_velocity.setDecimals(4)
        self._z_velocity.setSingleStep(0.01)
        self._z_velocity.setSuffix(" mm/s")
        self._z_velocity.valueChanged.connect(self._on_velocity_changed)
        z_vel_layout.addWidget(self._z_velocity)

        self._auto_velocity = QCheckBox("Auto")
        self._auto_velocity.setChecked(True)
        self._auto_velocity.setToolTip(
            "Auto-calculate Z velocity from plane spacing and frame rate.\n"
            "Formula: Z_velocity = (Z_step / 1000) × Frame_rate"
        )
        self._auto_velocity.stateChanged.connect(self._on_auto_velocity_changed)
        z_vel_layout.addWidget(self._auto_velocity)

        grid.addWidget(QLabel("Z Velocity:"), 4, 0)
        grid.addLayout(z_vel_layout, 4, 1)

        # Velocity warning label (hidden by default)
        self._velocity_warning = QLabel("")
        self._velocity_warning.setStyleSheet("color: #856404; font-size: 11px;")
        self._velocity_warning.setWordWrap(True)
        self._velocity_warning.setVisible(False)
        grid.addWidget(self._velocity_warning, 5, 0, 1, 2)

        # Z range validation warning (hidden by default)
        self._z_range_warning = QLabel("")
        self._z_range_warning.setStyleSheet("color: #d9534f; font-size: 11px; font-weight: bold;")
        self._z_range_warning.setWordWrap(True)
        self._z_range_warning.setVisible(False)
        grid.addWidget(self._z_range_warning, 6, 0, 1, 2)

        # Apply initial auto-calculation state
        self._z_velocity.setReadOnly(True)
        self._z_velocity.setStyleSheet("QDoubleSpinBox { background-color: #f0f0f0; }")
        self._update_auto_velocity()

        # Stack option dropdown (hidden by default - auto-managed by workflow type)
        self._stack_option_label = QLabel("Stack Option:")
        grid.addWidget(self._stack_option_label, 7, 0)
        self._stack_option = QComboBox()
        self._stack_option.addItems(STACK_OPTIONS)
        self._stack_option.setCurrentText("None")
        self._stack_option.currentTextChanged.connect(self._on_stack_option_changed)
        grid.addWidget(self._stack_option, 7, 1)

        # Hide stack option by default (auto-managed by WorkflowView)
        self._stack_option_label.setVisible(False)
        self._stack_option.setVisible(False)

        # Tile settings (only visible when Tile option selected)
        self._tile_widget = QWidget()
        tile_layout = QGridLayout(self._tile_widget)
        tile_layout.setContentsMargins(0, 0, 0, 0)

        tile_layout.addWidget(QLabel("Tiles X:"), 0, 0)
        self._tiles_x = QSpinBox()
        self._tiles_x.setRange(1, 100)
        self._tiles_x.setValue(1)
        self._tiles_x.valueChanged.connect(self._on_settings_changed)
        tile_layout.addWidget(self._tiles_x, 0, 1)

        tile_layout.addWidget(QLabel("Tiles Y:"), 1, 0)
        self._tiles_y = QSpinBox()
        self._tiles_y.setRange(1, 100)
        self._tiles_y.setValue(1)
        self._tiles_y.valueChanged.connect(self._on_settings_changed)
        tile_layout.addWidget(self._tiles_y, 1, 1)

        self._tile_widget.setVisible(False)
        grid.addWidget(self._tile_widget, 8, 0, 1, 2)

        # Rotational stage velocity (only for OPT modes)
        self._rotational_label = QLabel("Rotational Velocity:")
        grid.addWidget(self._rotational_label, 9, 0)
        self._rotational_velocity = QDoubleSpinBox()
        self._rotational_velocity.setRange(0.0, 10.0)
        self._rotational_velocity.setValue(0.0)
        self._rotational_velocity.setDecimals(2)
        self._rotational_velocity.setSingleStep(0.1)
        self._rotational_velocity.setSuffix(" °/s")
        self._rotational_velocity.valueChanged.connect(self._on_settings_changed)
        grid.addWidget(self._rotational_velocity, 9, 1)

        # Hide rotational velocity by default (shown for Multi-Angle mode)
        self._rotational_label.setVisible(False)
        self._rotational_velocity.setVisible(False)

        # Estimated acquisition time
        grid.addWidget(QLabel("Est. Time:"), 10, 0)
        self._time_label = QLabel("~5.0 s")
        self._time_label.setStyleSheet("color: #666;")
        grid.addWidget(self._time_label, 10, 1)

        # Options row
        opts_layout = QHBoxLayout()

        self._return_to_start = QCheckBox("Return to Start")
        self._return_to_start.setChecked(True)
        self._return_to_start.setToolTip("Return to start Z position after stack")
        self._return_to_start.stateChanged.connect(self._on_settings_changed)
        opts_layout.addWidget(self._return_to_start)

        opts_layout.addStretch()
        grid.addLayout(opts_layout, 11, 0, 1, 2)

        group.setLayout(grid)
        layout.addWidget(group)

        # Initial calculations
        self._update_calculations()

    def _on_stack_option_changed(self, option: str) -> None:
        """
        Handle stack option change.

        Shows/hides tile settings when Tile option is selected.

        Args:
            option: Selected stack option
        """
        # Show tile settings only when Tile option is selected
        self._tile_widget.setVisible(option == "Tile")
        self._on_settings_changed()

    def _on_settings_changed(self) -> None:
        """Handle any settings change."""
        # If in tile mode with auto num_planes, recalculate when Z step changes
        if self._auto_num_planes and self._z_range_mm is not None:
            z_step_mm = self._z_step.value() / 1000.0
            if z_step_mm > 0:
                num_planes = math.ceil(self._z_range_mm / z_step_mm) + 1

                self._updating = True
                self._num_planes.setValue(num_planes)
                self._updating = False

        # Update auto-velocity if enabled
        if self._auto_velocity.isChecked():
            self._update_auto_velocity()
        self._update_calculations()
        settings = self.get_settings()
        self.settings_changed.emit(settings)

    def _on_velocity_changed(self) -> None:
        """Handle manual velocity change."""
        if self._updating:
            return
        self._update_calculations()
        settings = self.get_settings()
        self.settings_changed.emit(settings)

    def _on_auto_velocity_changed(self, state: int) -> None:
        """Handle auto-velocity checkbox change."""
        is_auto = state != 0

        # Update UI state
        self._z_velocity.setReadOnly(is_auto)
        if is_auto:
            self._z_velocity.setStyleSheet("QDoubleSpinBox { background-color: #f0f0f0; }")
            self._update_auto_velocity()
        else:
            self._z_velocity.setStyleSheet("")

        self._on_settings_changed()

    def _on_z_range_changed(self) -> None:
        """Handle Z range spinbox change."""
        if self._updating:
            return

        # Update stored Z range
        self._z_range_mm = self._z_range_spinbox.value()

        # If auto num planes is enabled, recalculate num_planes
        if self._auto_num_planes:
            z_step_mm = self._z_step.value() / 1000.0
            if z_step_mm > 0:
                # Formula: Num_planes = ceiling(Z_range / Z_step) + 1
                num_planes = math.ceil(self._z_range_mm / z_step_mm) + 1

                # Update num_planes without triggering recursive updates
                self._updating = True
                self._num_planes.setValue(num_planes)
                self._updating = False

        self._on_settings_changed()

    def _on_num_planes_changed(self) -> None:
        """Handle number of planes change."""
        if self._updating:
            return

        # If in tile mode with auto num_planes disabled, user can manually override
        # In this case, we don't update Z range - they're independently set
        self._on_settings_changed()

    def _on_auto_num_planes_changed(self, state: int) -> None:
        """Handle auto-calculate num planes checkbox change."""
        is_auto = state != 0
        self._auto_num_planes = is_auto

        # Update UI state
        self._num_planes.setReadOnly(is_auto)
        if is_auto:
            self._num_planes.setStyleSheet("QSpinBox { background-color: #f0f0f0; }")

            # Recalculate num_planes from Z range and Z step
            if self._z_range_mm is not None:
                z_step_mm = self._z_step.value() / 1000.0
                if z_step_mm > 0:
                    num_planes = math.ceil(self._z_range_mm / z_step_mm) + 1

                    self._updating = True
                    self._num_planes.setValue(num_planes)
                    self._updating = False
        else:
            self._num_planes.setStyleSheet("")

        self._on_settings_changed()

    def _update_auto_velocity(self) -> None:
        """Calculate and set Z velocity from plane spacing and frame rate.

        Formula: Z_velocity (mm/s) = (Z_step_um / 1000) × Frame_rate (fps)
        """
        if not self._auto_velocity.isChecked():
            return

        # Calculate Z velocity
        z_step_mm = self._z_step.value() / 1000.0  # µm to mm
        z_velocity = z_step_mm * self._frame_rate

        # Check against limits and show warning if clamped
        warning_msg = ""
        original_velocity = z_velocity

        if z_velocity < Z_VELOCITY_MIN_MM_S:
            z_velocity = Z_VELOCITY_MIN_MM_S
            warning_msg = f"Calculated velocity ({original_velocity:.4f} mm/s) below minimum. Using {Z_VELOCITY_MIN_MM_S} mm/s."
        elif z_velocity > Z_VELOCITY_MAX_MM_S:
            z_velocity = Z_VELOCITY_MAX_MM_S
            warning_msg = f"Calculated velocity ({original_velocity:.4f} mm/s) above maximum. Using {Z_VELOCITY_MAX_MM_S} mm/s."

        # Update warning display
        if warning_msg:
            self._velocity_warning.setText(warning_msg)
            self._velocity_warning.setVisible(True)
        else:
            self._velocity_warning.setVisible(False)

        # Update velocity spinbox without triggering recursive updates
        self._updating = True
        self._z_velocity.setValue(z_velocity)
        self._updating = False

    def set_frame_rate(self, frame_rate: float) -> None:
        """Set frame rate for Z velocity calculation.

        This should be called when CameraPanel exposure time changes.

        Args:
            frame_rate: Frame rate in fps (calculated from exposure time)
        """
        self._frame_rate = frame_rate
        self._frame_rate_label.setText(f"{frame_rate:.1f} fps")

        # Recalculate velocity if in auto mode
        if self._auto_velocity.isChecked():
            self._update_auto_velocity()
            self._update_calculations()

    def get_frame_rate(self) -> float:
        """Get current frame rate.

        Returns:
            Frame rate in fps
        """
        return self._frame_rate

    def _update_calculations(self) -> None:
        """Update calculated values (Z range, time estimate)."""
        num_planes = self._num_planes.value()
        z_step_um = self._z_step.value()
        z_velocity = self._z_velocity.value()

        # Calculate or display Z range
        if self._z_range_mm is not None:
            # In tile mode - Z range is set externally
            z_range_mm = self._z_range_mm
            z_range_um = z_range_mm * 1000.0
        else:
            # Normal mode - calculate Z range from num_planes and z_step
            z_range_um = (num_planes - 1) * z_step_um
            z_range_mm = z_range_um / 1000.0

        # Validate Z step vs Z range (only in tile mode where range is fixed)
        if self._z_range_mm is not None:
            z_step_mm = z_step_um / 1000.0
            if z_step_mm > z_range_mm:
                warning = (f"Warning: Z step ({z_step_um:.1f} µm) is larger than Z range "
                          f"({z_range_um:.1f} µm). Only {num_planes} plane(s) will be acquired, "
                          f"covering {(num_planes - 1) * z_step_um:.1f} µm.")
                self._z_range_warning.setText(warning)
                self._z_range_warning.setVisible(True)
            else:
                self._z_range_warning.setVisible(False)
        else:
            self._z_range_warning.setVisible(False)

        # Update Z range label
        if z_range_um < 1000:
            self._z_range_label.setText(f"{z_range_um:.1f} um")
        else:
            self._z_range_label.setText(f"{z_range_mm:.2f} mm")

        # Estimate acquisition time
        # Time = distance / velocity + overhead per plane
        z_travel_time = z_range_mm / z_velocity if z_velocity > 0 else 0
        overhead_per_plane = 0.01  # ~10ms overhead per plane
        total_overhead = num_planes * overhead_per_plane

        total_time = z_travel_time + total_overhead
        if self._return_to_start.isChecked():
            total_time += z_travel_time  # Return time

        if total_time < 60:
            self._time_label.setText(f"~{total_time:.1f} s")
        elif total_time < 3600:
            minutes = total_time / 60
            self._time_label.setText(f"~{minutes:.1f} min")
        else:
            hours = total_time / 3600
            self._time_label.setText(f"~{hours:.1f} hr")

    def get_settings(self) -> StackSettings:
        """
        Get current Z-stack settings.

        Returns:
            StackSettings object with current values
        """
        # For backward compatibility, set bidirectional based on stack option
        bidirectional = self._stack_option.currentText() == "Bidirectional"

        return StackSettings(
            num_planes=self._num_planes.value(),
            z_step_um=self._z_step.value(),
            z_velocity_mm_s=self._z_velocity.value(),
            bidirectional=bidirectional,
            return_to_start=self._return_to_start.isChecked(),
        )

    def get_workflow_stack_dict(self) -> Dict[str, Any]:
        """
        Get stack settings as workflow dictionary format.

        Returns:
            Dictionary for workflow file Stack Settings section
        """
        num_planes = self._num_planes.value()
        z_step_um = self._z_step.value()
        z_step_mm = z_step_um / 1000.0
        z_range_mm = (num_planes - 1) * z_step_mm

        # Get stack option value
        stack_option = self._stack_option.currentText()

        # Get tile settings (or 0 if not tiling)
        tiles_x = self._tiles_x.value() if stack_option == "Tile" else 0
        tiles_y = self._tiles_y.value() if stack_option == "Tile" else 0

        return {
            'Number of planes': num_planes,
            'Change in Z axis (mm)': z_range_mm,  # Total range, not step!
            'Z stage velocity (mm/s)': self._z_velocity.value(),
            'Rotational stage velocity (°/s)': self._rotational_velocity.value(),
            'Stack option': stack_option,
            'Stack option settings 1': tiles_x,
            'Stack option settings 2': tiles_y,
            'Auto update stack calculations': 'true',
            'Camera 1 capture percentage': 100,
            'Camera 1 capture mode': 0,
        }

    def set_settings(self, settings: StackSettings) -> None:
        """
        Set Z-stack settings from object.

        Args:
            settings: StackSettings to apply
        """
        self._num_planes.setValue(settings.num_planes)
        self._z_step.setValue(settings.z_step_um)
        self._z_velocity.setValue(settings.z_velocity_mm_s)

        # Set stack option based on bidirectional flag (for backward compatibility)
        if settings.bidirectional:
            self._stack_option.setCurrentText("Bidirectional")

        self._return_to_start.setChecked(settings.return_to_start)

    def get_z_range_um(self) -> float:
        """Get total Z range in micrometers."""
        return (self._num_planes.value() - 1) * self._z_step.value()

    def get_z_range_mm(self) -> float:
        """Get total Z range in millimeters."""
        return self.get_z_range_um() / 1000.0

    def set_z_range(self, z_min_mm: float, z_max_mm: float) -> None:
        """Set Z range externally for tile collection mode.

        Args:
            z_min_mm: Minimum Z position in mm
            z_max_mm: Maximum Z position in mm
        """
        z_range_mm = abs(z_max_mm - z_min_mm)
        self._z_range_mm = z_range_mm

        # Update spinbox
        self._updating = True
        self._z_range_spinbox.setValue(z_range_mm)
        self._updating = False

        # If auto num_planes is enabled, recalculate
        if self._auto_num_planes:
            z_step_mm = self._z_step.value() / 1000.0
            if z_step_mm > 0:
                num_planes = math.ceil(z_range_mm / z_step_mm) + 1

                self._updating = True
                self._num_planes.setValue(num_planes)
                self._updating = False

        self._update_calculations()

    def enable_tile_mode(self, enable: bool = True) -> None:
        """Enable tile collection mode with Z range input and auto num_planes.

        Args:
            enable: True to enable tile mode, False to disable
        """
        self._z_range_spinbox.setVisible(enable)
        self._auto_num_planes_checkbox.setVisible(enable)

        if enable:
            # Default to auto mode when enabling tile mode
            self._auto_num_planes_checkbox.setChecked(True)
            self._z_range_label.setVisible(False)  # Hide calculated label when showing spinbox
        else:
            # Reset to normal mode
            self._z_range_mm = None
            self._auto_num_planes = False
            self._auto_num_planes_checkbox.setChecked(False)
            self._z_range_label.setVisible(True)  # Show calculated label
            self._num_planes.setReadOnly(False)
            self._num_planes.setStyleSheet("")

        self._update_calculations()

    # Visibility control methods for workflow type integration

    def set_stack_option(self, option: str) -> None:
        """Set the stack option programmatically.

        Args:
            option: One of STACK_OPTIONS values
        """
        if option in STACK_OPTIONS:
            self._stack_option.setCurrentText(option)

    def get_stack_option(self) -> str:
        """Get current stack option."""
        return self._stack_option.currentText()

    def set_stack_option_enabled(self, enabled: bool) -> None:
        """Enable/disable the stack option dropdown.

        When managed by workflow type, this should be disabled.

        Args:
            enabled: Whether the dropdown should be user-editable
        """
        self._stack_option.setEnabled(enabled)

    def set_rotational_velocity_visible(self, visible: bool) -> None:
        """Show/hide the rotational velocity setting.

        Only needed for Multi-Angle/OPT modes.

        Args:
            visible: Whether to show rotational velocity
        """
        self._rotational_label.setVisible(visible)
        self._rotational_velocity.setVisible(visible)

    def set_tile_settings_visible(self, visible: bool) -> None:
        """Show/hide the tile settings.

        Args:
            visible: Whether to show tile X/Y settings
        """
        self._tile_widget.setVisible(visible)

    def set_stack_option_visible(self, visible: bool) -> None:
        """Show/hide the stack option dropdown.

        Usually hidden since it's auto-managed by workflow type.

        Args:
            visible: Whether to show stack option dropdown
        """
        self._stack_option_label.setVisible(visible)
        self._stack_option.setVisible(visible)
