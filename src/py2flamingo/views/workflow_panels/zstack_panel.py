"""
Z-Stack settings panel for workflow configuration.

Provides UI for Z-stack acquisition parameters.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QSpinBox, QCheckBox, QGroupBox, QGridLayout
)
from PyQt5.QtCore import pyqtSignal

from py2flamingo.models.data.workflow import StackSettings


class ZStackPanel(QWidget):
    """
    Panel for configuring Z-stack acquisition settings.

    Provides:
    - Number of planes
    - Z step size (um)
    - Z velocity (mm/s)
    - Bidirectional option
    - Calculated Z range display

    Signals:
        settings_changed: Emitted when Z-stack settings change
    """

    settings_changed = pyqtSignal(object)  # Emits StackSettings

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize Z-stack panel.

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

        # Z-Stack settings group
        group = QGroupBox("Z-Stack Settings")
        grid = QGridLayout()
        grid.setSpacing(8)

        # Number of planes
        grid.addWidget(QLabel("Number of Planes:"), 0, 0)
        self._num_planes = QSpinBox()
        self._num_planes.setRange(1, 10000)
        self._num_planes.setValue(100)
        self._num_planes.valueChanged.connect(self._on_settings_changed)
        grid.addWidget(self._num_planes, 0, 1)

        # Z step size (in micrometers, stored as mm in workflow)
        grid.addWidget(QLabel("Z Step:"), 1, 0)
        self._z_step = QDoubleSpinBox()
        self._z_step.setRange(0.1, 100.0)
        self._z_step.setValue(2.5)  # Default 2.5 um
        self._z_step.setDecimals(2)
        self._z_step.setSingleStep(0.1)
        self._z_step.setSuffix(" um")
        self._z_step.valueChanged.connect(self._on_settings_changed)
        grid.addWidget(self._z_step, 1, 1)

        # Z range (calculated, read-only display)
        grid.addWidget(QLabel("Z Range:"), 2, 0)
        self._z_range_label = QLabel("250.0 um")
        self._z_range_label.setStyleSheet("font-weight: bold;")
        grid.addWidget(self._z_range_label, 2, 1)

        # Z velocity
        grid.addWidget(QLabel("Z Velocity:"), 3, 0)
        self._z_velocity = QDoubleSpinBox()
        self._z_velocity.setRange(0.01, 2.0)
        self._z_velocity.setValue(0.4)  # Default 0.4 mm/s
        self._z_velocity.setDecimals(2)
        self._z_velocity.setSingleStep(0.05)
        self._z_velocity.setSuffix(" mm/s")
        self._z_velocity.valueChanged.connect(self._on_settings_changed)
        grid.addWidget(self._z_velocity, 3, 1)

        # Estimated acquisition time
        grid.addWidget(QLabel("Est. Time:"), 4, 0)
        self._time_label = QLabel("~5.0 s")
        self._time_label.setStyleSheet("color: #666;")
        grid.addWidget(self._time_label, 4, 1)

        # Options row
        opts_layout = QHBoxLayout()

        self._bidirectional = QCheckBox("Bidirectional")
        self._bidirectional.setToolTip("Acquire in both Z directions (faster)")
        self._bidirectional.stateChanged.connect(self._on_settings_changed)
        opts_layout.addWidget(self._bidirectional)

        self._return_to_start = QCheckBox("Return to Start")
        self._return_to_start.setChecked(True)
        self._return_to_start.setToolTip("Return to start Z position after stack")
        self._return_to_start.stateChanged.connect(self._on_settings_changed)
        opts_layout.addWidget(self._return_to_start)

        opts_layout.addStretch()
        grid.addLayout(opts_layout, 5, 0, 1, 2)

        group.setLayout(grid)
        layout.addWidget(group)

        # Initial calculations
        self._update_calculations()

    def _on_settings_changed(self) -> None:
        """Handle any settings change."""
        self._update_calculations()
        settings = self.get_settings()
        self.settings_changed.emit(settings)

    def _update_calculations(self) -> None:
        """Update calculated values (Z range, time estimate)."""
        num_planes = self._num_planes.value()
        z_step_um = self._z_step.value()
        z_velocity = self._z_velocity.value()

        # Calculate Z range
        z_range_um = (num_planes - 1) * z_step_um
        if z_range_um < 1000:
            self._z_range_label.setText(f"{z_range_um:.1f} um")
        else:
            self._z_range_label.setText(f"{z_range_um / 1000:.2f} mm")

        # Estimate acquisition time
        # Time = distance / velocity + overhead per plane
        z_range_mm = z_range_um / 1000.0
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
        return StackSettings(
            num_planes=self._num_planes.value(),
            z_step_um=self._z_step.value(),
            z_velocity_mm_s=self._z_velocity.value(),
            bidirectional=self._bidirectional.isChecked(),
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

        return {
            'Number of planes': num_planes,
            'Change in Z axis (mm)': z_range_mm,  # Total range, not step!
            'Z stage velocity (mm/s)': self._z_velocity.value(),
            'Stack option': 'None' if not self._bidirectional.isChecked() else 'Bidirectional',
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
        self._bidirectional.setChecked(settings.bidirectional)
        self._return_to_start.setChecked(settings.return_to_start)

    def get_z_range_um(self) -> float:
        """Get total Z range in micrometers."""
        return (self._num_planes.value() - 1) * self._z_step.value()

    def get_z_range_mm(self) -> float:
        """Get total Z range in millimeters."""
        return self.get_z_range_um() / 1000.0
