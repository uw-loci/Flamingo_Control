"""
Camera panel for workflow configuration.

Provides UI for exposure time and frame rate settings.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QDoubleSpinBox, QGroupBox, QGridLayout
)
from PyQt5.QtCore import pyqtSignal


class CameraPanel(QWidget):
    """
    Panel for configuring camera settings for workflows.

    Provides:
    - Exposure time input (microseconds)
    - Frame rate display (calculated from exposure)

    Signals:
        settings_changed: Emitted when camera settings change
    """

    settings_changed = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize camera panel.

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

        # Camera settings group
        group = QGroupBox("Camera Settings")
        grid = QGridLayout()
        grid.setSpacing(8)

        # Exposure time (microseconds)
        grid.addWidget(QLabel("Exposure Time:"), 0, 0)
        self._exposure_spinbox = QDoubleSpinBox()
        self._exposure_spinbox.setRange(0.1, 100000.0)  # 0.1us to 100ms
        self._exposure_spinbox.setValue(10000.0)  # Default 10ms
        self._exposure_spinbox.setDecimals(1)
        self._exposure_spinbox.setSingleStep(100.0)
        self._exposure_spinbox.setSuffix(" us")
        self._exposure_spinbox.valueChanged.connect(self._on_exposure_changed)
        grid.addWidget(self._exposure_spinbox, 0, 1)

        # Frame rate (calculated, read-only display)
        grid.addWidget(QLabel("Frame Rate:"), 1, 0)
        self._framerate_label = QLabel("100.0 fps")
        self._framerate_label.setStyleSheet("font-weight: bold;")
        grid.addWidget(self._framerate_label, 1, 1)

        # Info about frame rate
        info_label = QLabel("Frame rate is calculated from exposure time")
        info_label.setStyleSheet("color: gray; font-size: 9pt;")
        grid.addWidget(info_label, 2, 0, 1, 2)

        group.setLayout(grid)
        layout.addWidget(group)

        # Initial frame rate calculation
        self._update_framerate()

    def _on_exposure_changed(self) -> None:
        """Handle exposure time change."""
        self._update_framerate()
        settings = self.get_settings()
        self.settings_changed.emit(settings)

    def _update_framerate(self) -> None:
        """Update frame rate display based on exposure time."""
        exposure_us = self._exposure_spinbox.value()
        if exposure_us > 0:
            # Frame rate = 1 / exposure time
            # exposure_us is in microseconds, convert to seconds
            exposure_s = exposure_us / 1_000_000.0
            framerate = 1.0 / exposure_s
            self._framerate_label.setText(f"{framerate:.1f} fps")
        else:
            self._framerate_label.setText("N/A")

    def get_settings(self) -> Dict[str, Any]:
        """
        Get current camera settings.

        Returns:
            Dictionary with camera settings
        """
        exposure_us = self._exposure_spinbox.value()
        exposure_s = exposure_us / 1_000_000.0
        framerate = 1.0 / exposure_s if exposure_s > 0 else 0

        return {
            'exposure_us': exposure_us,
            'frame_rate': framerate,
        }

    def get_exposure_us(self) -> float:
        """
        Get exposure time in microseconds.

        Returns:
            Exposure time in microseconds
        """
        return self._exposure_spinbox.value()

    def get_frame_rate(self) -> float:
        """
        Get calculated frame rate.

        Returns:
            Frame rate in frames per second
        """
        exposure_us = self._exposure_spinbox.value()
        if exposure_us > 0:
            return 1_000_000.0 / exposure_us
        return 0.0

    def set_exposure(self, exposure_us: float) -> None:
        """
        Set exposure time.

        Args:
            exposure_us: Exposure time in microseconds
        """
        self._exposure_spinbox.setValue(exposure_us)
