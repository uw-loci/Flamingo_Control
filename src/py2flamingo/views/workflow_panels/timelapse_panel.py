"""
Time-lapse settings panel for workflow configuration.

Provides UI for time-lapse acquisition parameters.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSpinBox, QGroupBox, QGridLayout, QFrame
)
from PyQt5.QtCore import pyqtSignal

from py2flamingo.models.data.workflow import TimeLapseSettings


class TimeLapsePanel(QWidget):
    """
    Panel for configuring time-lapse acquisition settings.

    Provides:
    - Duration input (days:hours:minutes:seconds)
    - Interval input (days:hours:minutes:seconds)
    - Calculated number of timepoints
    - Estimated data size

    Signals:
        settings_changed: Emitted when time-lapse settings change
    """

    settings_changed = pyqtSignal(object)  # Emits TimeLapseSettings

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize time-lapse panel.

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

        # Time-lapse settings group
        group = QGroupBox("Time-Lapse Settings")
        grid = QGridLayout()
        grid.setSpacing(8)

        # Duration row
        grid.addWidget(QLabel("Total Duration:"), 0, 0)

        duration_layout = QHBoxLayout()

        self._duration_days = QSpinBox()
        self._duration_days.setRange(0, 365)
        self._duration_days.setSuffix(" d")
        self._duration_days.valueChanged.connect(self._on_settings_changed)
        duration_layout.addWidget(self._duration_days)

        self._duration_hours = QSpinBox()
        self._duration_hours.setRange(0, 23)
        self._duration_hours.setSuffix(" h")
        self._duration_hours.valueChanged.connect(self._on_settings_changed)
        duration_layout.addWidget(self._duration_hours)

        self._duration_mins = QSpinBox()
        self._duration_mins.setRange(0, 59)
        self._duration_mins.setSuffix(" m")
        self._duration_mins.setValue(1)  # Default 1 minute
        self._duration_mins.valueChanged.connect(self._on_settings_changed)
        duration_layout.addWidget(self._duration_mins)

        self._duration_secs = QSpinBox()
        self._duration_secs.setRange(0, 59)
        self._duration_secs.setSuffix(" s")
        self._duration_secs.valueChanged.connect(self._on_settings_changed)
        duration_layout.addWidget(self._duration_secs)

        duration_layout.addStretch()
        grid.addLayout(duration_layout, 0, 1)

        # Interval row
        grid.addWidget(QLabel("Interval:"), 1, 0)

        interval_layout = QHBoxLayout()

        self._interval_days = QSpinBox()
        self._interval_days.setRange(0, 365)
        self._interval_days.setSuffix(" d")
        self._interval_days.valueChanged.connect(self._on_settings_changed)
        interval_layout.addWidget(self._interval_days)

        self._interval_hours = QSpinBox()
        self._interval_hours.setRange(0, 23)
        self._interval_hours.setSuffix(" h")
        self._interval_hours.valueChanged.connect(self._on_settings_changed)
        interval_layout.addWidget(self._interval_hours)

        self._interval_mins = QSpinBox()
        self._interval_mins.setRange(0, 59)
        self._interval_mins.setSuffix(" m")
        self._interval_mins.valueChanged.connect(self._on_settings_changed)
        interval_layout.addWidget(self._interval_mins)

        self._interval_secs = QSpinBox()
        self._interval_secs.setRange(1, 59)  # At least 1 second interval
        self._interval_secs.setSuffix(" s")
        self._interval_secs.setValue(10)  # Default 10 seconds
        self._interval_secs.valueChanged.connect(self._on_settings_changed)
        interval_layout.addWidget(self._interval_secs)

        interval_layout.addStretch()
        grid.addLayout(interval_layout, 1, 1)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        grid.addWidget(separator, 2, 0, 1, 2)

        # Calculated values row
        grid.addWidget(QLabel("Timepoints:"), 3, 0)
        self._timepoints_label = QLabel("6")
        self._timepoints_label.setStyleSheet("font-weight: bold; color: #2980b9;")
        grid.addWidget(self._timepoints_label, 3, 1)

        grid.addWidget(QLabel("Total Time:"), 4, 0)
        self._total_time_label = QLabel("00:01:00")
        self._total_time_label.setStyleSheet("font-weight: bold;")
        grid.addWidget(self._total_time_label, 4, 1)

        # Info text
        info_label = QLabel("Acquisition will run for the specified duration, "
                           "capturing at each interval.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: gray; font-size: 9pt;")
        grid.addWidget(info_label, 5, 0, 1, 2)

        group.setLayout(grid)
        layout.addWidget(group)

        # Initial calculation
        self._update_calculations()

    def _on_settings_changed(self) -> None:
        """Handle any settings change."""
        self._update_calculations()
        settings = self.get_settings()
        self.settings_changed.emit(settings)

    def _update_calculations(self) -> None:
        """Update calculated values (timepoints, total time)."""
        duration_secs = self._get_duration_seconds()
        interval_secs = self._get_interval_seconds()

        if interval_secs > 0:
            timepoints = max(1, duration_secs // interval_secs + 1)
            self._timepoints_label.setText(str(timepoints))
        else:
            self._timepoints_label.setText("1")

        # Format total time
        self._total_time_label.setText(self._format_time(duration_secs))

    def _get_duration_seconds(self) -> int:
        """Get total duration in seconds."""
        return (self._duration_days.value() * 86400 +
                self._duration_hours.value() * 3600 +
                self._duration_mins.value() * 60 +
                self._duration_secs.value())

    def _get_interval_seconds(self) -> int:
        """Get interval in seconds."""
        return (self._interval_days.value() * 86400 +
                self._interval_hours.value() * 3600 +
                self._interval_mins.value() * 60 +
                self._interval_secs.value())

    def _format_time(self, total_seconds: int) -> str:
        """Format seconds as dd:hh:mm:ss string."""
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        mins = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{days:02d}:{hours:02d}:{mins:02d}:{secs:02d}"

    def get_settings(self) -> TimeLapseSettings:
        """
        Get current time-lapse settings.

        Returns:
            TimeLapseSettings object with current values
        """
        duration_secs = self._get_duration_seconds()
        interval_secs = self._get_interval_seconds()
        timepoints = max(1, duration_secs // interval_secs + 1) if interval_secs > 0 else 1

        return TimeLapseSettings(
            duration_seconds=duration_secs,
            interval_seconds=interval_secs,
            num_timepoints=timepoints,
        )

    def get_workflow_timelapse_dict(self) -> Dict[str, Any]:
        """
        Get time-lapse settings as workflow dictionary format.

        Returns:
            Dictionary for workflow file Experiment Settings section
        """
        duration_str = self._format_time(self._get_duration_seconds())
        interval_str = self._format_time(self._get_interval_seconds())

        return {
            'Duration (dd:hh:mm:ss)': duration_str,
            'Interval (dd:hh:mm:ss)': interval_str,
        }

    def set_settings(self, settings: TimeLapseSettings) -> None:
        """
        Set time-lapse settings from object.

        Args:
            settings: TimeLapseSettings to apply
        """
        # Set duration
        duration = settings.duration_seconds
        self._duration_days.setValue(duration // 86400)
        self._duration_hours.setValue((duration % 86400) // 3600)
        self._duration_mins.setValue((duration % 3600) // 60)
        self._duration_secs.setValue(duration % 60)

        # Set interval
        interval = settings.interval_seconds
        self._interval_days.setValue(interval // 86400)
        self._interval_hours.setValue((interval % 86400) // 3600)
        self._interval_mins.setValue((interval % 3600) // 60)
        self._interval_secs.setValue(interval % 60)

    def get_timepoints(self) -> int:
        """Get calculated number of timepoints."""
        duration_secs = self._get_duration_seconds()
        interval_secs = self._get_interval_seconds()
        return max(1, duration_secs // interval_secs + 1) if interval_secs > 0 else 1
