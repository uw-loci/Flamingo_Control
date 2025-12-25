"""
Multi-angle settings panel for workflow configuration.

Provides UI for multi-angle/OPT acquisition parameters.
"""

import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSpinBox, QDoubleSpinBox, QGroupBox, QGridLayout, QFrame
)
from PyQt5.QtCore import pyqtSignal


@dataclass
class MultiAngleSettings:
    """Settings for multi-angle acquisition."""
    num_angles: int = 1
    angle_step_degrees: float = 0.0

    @property
    def total_rotation(self) -> float:
        """Calculate total rotation in degrees."""
        return (self.num_angles - 1) * self.angle_step_degrees


class MultiAnglePanel(QWidget):
    """
    Panel for configuring multi-angle/OPT acquisition settings.

    Provides:
    - Number of angles input
    - Angle step size input
    - Calculated total rotation display
    - Start angle from current position

    Signals:
        settings_changed: Emitted when multi-angle settings change
    """

    settings_changed = pyqtSignal(object)  # Emits MultiAngleSettings

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize multi-angle panel.

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

        # Multi-angle settings group
        group = QGroupBox("Multi-Angle / OPT Settings")
        grid = QGridLayout()
        grid.setSpacing(8)

        # Number of angles
        grid.addWidget(QLabel("Number of Angles:"), 0, 0)
        self._num_angles = QSpinBox()
        self._num_angles.setRange(1, 3600)  # Up to 0.1 degree resolution for 360
        self._num_angles.setValue(36)  # Default: 36 angles (10 degree steps)
        self._num_angles.valueChanged.connect(self._on_settings_changed)
        grid.addWidget(self._num_angles, 0, 1)

        # Angle step size
        grid.addWidget(QLabel("Angle Step:"), 1, 0)
        self._angle_step = QDoubleSpinBox()
        self._angle_step.setRange(0.1, 180.0)
        self._angle_step.setValue(10.0)  # Default: 10 degrees
        self._angle_step.setDecimals(2)
        self._angle_step.setSingleStep(1.0)
        self._angle_step.setSuffix(" deg")
        self._angle_step.valueChanged.connect(self._on_settings_changed)
        grid.addWidget(self._angle_step, 1, 1)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        grid.addWidget(separator, 2, 0, 1, 2)

        # Total rotation display
        grid.addWidget(QLabel("Total Rotation:"), 3, 0)
        self._total_rotation_label = QLabel("350.0 deg")
        self._total_rotation_label.setStyleSheet("font-weight: bold; color: #2980b9;")
        grid.addWidget(self._total_rotation_label, 3, 1)

        # Full 360 indicator
        grid.addWidget(QLabel("Coverage:"), 4, 0)
        self._coverage_label = QLabel("97.2% of 360")
        self._coverage_label.setStyleSheet("font-weight: bold;")
        grid.addWidget(self._coverage_label, 4, 1)

        # Info text
        info_label = QLabel("Multi-angle acquisition rotates the sample and captures "
                           "images at each angle. For OPT (Optical Projection Tomography), "
                           "typically use 360+ angles for good reconstruction.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: gray; font-size: 9pt;")
        grid.addWidget(info_label, 5, 0, 1, 2)

        # Preset buttons row
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("Presets:"))

        from PyQt5.QtWidgets import QPushButton

        btn_360_36 = QPushButton("36x10")
        btn_360_36.setToolTip("36 angles at 10 degree steps (full 360)")
        btn_360_36.clicked.connect(lambda: self._apply_preset(36, 10.0))
        preset_layout.addWidget(btn_360_36)

        btn_360_90 = QPushButton("90x4")
        btn_360_90.setToolTip("90 angles at 4 degree steps (full 360)")
        btn_360_90.clicked.connect(lambda: self._apply_preset(90, 4.0))
        preset_layout.addWidget(btn_360_90)

        btn_360_180 = QPushButton("180x2")
        btn_360_180.setToolTip("180 angles at 2 degree steps (full 360)")
        btn_360_180.clicked.connect(lambda: self._apply_preset(180, 2.0))
        preset_layout.addWidget(btn_360_180)

        btn_360_360 = QPushButton("360x1")
        btn_360_360.setToolTip("360 angles at 1 degree steps (OPT quality)")
        btn_360_360.clicked.connect(lambda: self._apply_preset(360, 1.0))
        preset_layout.addWidget(btn_360_360)

        preset_layout.addStretch()
        grid.addLayout(preset_layout, 6, 0, 1, 2)

        group.setLayout(grid)
        layout.addWidget(group)

        # Initial calculation
        self._update_calculations()

    def _apply_preset(self, num_angles: int, angle_step: float) -> None:
        """Apply a preset configuration."""
        self._num_angles.setValue(num_angles)
        self._angle_step.setValue(angle_step)

    def _on_settings_changed(self) -> None:
        """Handle any settings change."""
        self._update_calculations()
        settings = self.get_settings()
        self.settings_changed.emit(settings)

    def _update_calculations(self) -> None:
        """Update calculated values (total rotation, coverage)."""
        num_angles = self._num_angles.value()
        angle_step = self._angle_step.value()

        # Total rotation
        total_rotation = (num_angles - 1) * angle_step
        self._total_rotation_label.setText(f"{total_rotation:.1f} deg")

        # Coverage (percentage of 360)
        coverage = (total_rotation / 360.0) * 100
        if coverage >= 100:
            self._coverage_label.setText("Full 360+ coverage")
            self._coverage_label.setStyleSheet("font-weight: bold; color: #27ae60;")
        elif coverage >= 90:
            self._coverage_label.setText(f"{coverage:.1f}% of 360")
            self._coverage_label.setStyleSheet("font-weight: bold; color: #f39c12;")
        else:
            self._coverage_label.setText(f"{coverage:.1f}% of 360")
            self._coverage_label.setStyleSheet("font-weight: bold; color: #e74c3c;")

    def get_settings(self) -> MultiAngleSettings:
        """
        Get current multi-angle settings.

        Returns:
            MultiAngleSettings object with current values
        """
        return MultiAngleSettings(
            num_angles=self._num_angles.value(),
            angle_step_degrees=self._angle_step.value(),
        )

    def get_workflow_multiangle_dict(self) -> Dict[str, Any]:
        """
        Get multi-angle settings as workflow dictionary format.

        Returns:
            Dictionary for workflow file Experiment Settings section
        """
        return {
            'Number of angles': self._num_angles.value(),
            'Angle step size': self._angle_step.value(),
        }

    def set_settings(self, settings: MultiAngleSettings) -> None:
        """
        Set multi-angle settings from object.

        Args:
            settings: MultiAngleSettings to apply
        """
        self._num_angles.setValue(settings.num_angles)
        self._angle_step.setValue(settings.angle_step_degrees)

    def get_num_angles(self) -> int:
        """Get number of angles."""
        return self._num_angles.value()

    def get_angle_step(self) -> float:
        """Get angle step in degrees."""
        return self._angle_step.value()

    def get_total_rotation(self) -> float:
        """Get total rotation in degrees."""
        return (self._num_angles.value() - 1) * self._angle_step.value()
