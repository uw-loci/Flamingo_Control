"""
Save location panel for workflow configuration.

Provides UI for configuring data save settings.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QComboBox, QCheckBox, QGroupBox, QGridLayout, QPlainTextEdit
)
from PyQt5.QtCore import pyqtSignal


# Save format options matching workflow file format
SAVE_FORMATS = [
    ("TIFF", "Tiff"),
    ("BigTIFF", "BigTiff"),
    ("Raw", "Raw"),
    ("Not Saved", "NotSaved"),
]


class SavePanel(QWidget):
    """
    Panel for configuring workflow data save settings.

    Provides:
    - Save location (drive/directory)
    - Save format selection
    - Save options (MIP, subfolders, etc.)
    - Region and comments fields

    Signals:
        settings_changed: Emitted when save settings change
    """

    settings_changed = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize save panel.

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

        # Save settings group
        group = QGroupBox("Data Saving")
        group_layout = QVBoxLayout()

        # Save enable checkbox
        self._save_enabled = QCheckBox("Save Images to Disk")
        self._save_enabled.setChecked(True)
        self._save_enabled.stateChanged.connect(self._on_save_enabled_changed)
        group_layout.addWidget(self._save_enabled)

        # Save options container (enabled/disabled based on checkbox)
        self._options_widget = QWidget()
        options_layout = QGridLayout(self._options_widget)
        options_layout.setContentsMargins(0, 5, 0, 0)
        options_layout.setSpacing(6)

        # Save drive (network share base)
        options_layout.addWidget(QLabel("Save Drive:"), 0, 0)
        self._save_drive = QLineEdit()
        self._save_drive.setPlaceholderText("/media/deploy/ctlsm1")
        self._save_drive.setText("/media/deploy/ctlsm1")
        self._save_drive.textChanged.connect(self._on_settings_changed)
        options_layout.addWidget(self._save_drive, 0, 1)

        # Save directory (subdirectory)
        options_layout.addWidget(QLabel("Directory:"), 1, 0)
        self._save_directory = QLineEdit()
        self._save_directory.setPlaceholderText("experiment_01")
        self._save_directory.textChanged.connect(self._on_settings_changed)
        options_layout.addWidget(self._save_directory, 1, 1)

        # Sample name
        options_layout.addWidget(QLabel("Sample Name:"), 2, 0)
        self._sample_name = QLineEdit()
        self._sample_name.setPlaceholderText("Sample_001")
        self._sample_name.textChanged.connect(self._on_settings_changed)
        options_layout.addWidget(self._sample_name, 2, 1)

        # Region
        options_layout.addWidget(QLabel("Region:"), 3, 0)
        self._region = QLineEdit()
        self._region.setPlaceholderText("Optional region identifier")
        self._region.textChanged.connect(self._on_settings_changed)
        options_layout.addWidget(self._region, 3, 1)

        # Save format
        options_layout.addWidget(QLabel("Format:"), 4, 0)
        self._format_combo = QComboBox()
        for display_name, _ in SAVE_FORMATS:
            self._format_combo.addItem(display_name)
        self._format_combo.setCurrentIndex(0)  # Default to TIFF
        self._format_combo.currentIndexChanged.connect(self._on_settings_changed)
        options_layout.addWidget(self._format_combo, 4, 1)

        # Additional options row
        opts_row = QHBoxLayout()

        self._save_mip = QCheckBox("Save MIP")
        self._save_mip.setToolTip("Save Maximum Intensity Projection (for Z-stacks)")
        self._save_mip.stateChanged.connect(self._on_settings_changed)
        opts_row.addWidget(self._save_mip)

        self._display_mip = QCheckBox("Display MIP")
        self._display_mip.setToolTip("Display Maximum Intensity Projection during acquisition")
        self._display_mip.setChecked(True)
        self._display_mip.stateChanged.connect(self._on_settings_changed)
        opts_row.addWidget(self._display_mip)

        self._save_subfolders = QCheckBox("Subfolders")
        self._save_subfolders.setToolTip("Save to subfolders (separate folder per timepoint/position)")
        self._save_subfolders.stateChanged.connect(self._on_settings_changed)
        opts_row.addWidget(self._save_subfolders)

        opts_row.addStretch()
        options_layout.addLayout(opts_row, 5, 0, 1, 2)

        # Live view option
        self._live_view = QCheckBox("Enable Live View During Acquisition")
        self._live_view.setChecked(True)
        self._live_view.stateChanged.connect(self._on_settings_changed)
        options_layout.addWidget(self._live_view, 6, 0, 1, 2)

        # Comments section (multi-line)
        comments_label = QLabel("Comments:")
        comments_label.setStyleSheet("margin-top: 8px;")
        options_layout.addWidget(comments_label, 7, 0, 1, 2)

        self._comments = QPlainTextEdit()
        self._comments.setPlaceholderText("Enter any comments or notes about this acquisition...")
        self._comments.setMaximumHeight(80)
        self._comments.textChanged.connect(self._on_settings_changed)
        options_layout.addWidget(self._comments, 8, 0, 1, 2)

        group_layout.addWidget(self._options_widget)
        group.setLayout(group_layout)
        layout.addWidget(group)

    def _on_save_enabled_changed(self, state: int) -> None:
        """Handle save enabled checkbox change."""
        self._options_widget.setEnabled(state != 0)
        self._on_settings_changed()

    def _on_settings_changed(self) -> None:
        """Handle any settings change."""
        settings = self.get_settings()
        self.settings_changed.emit(settings)

    def get_settings(self) -> Dict[str, Any]:
        """
        Get current save settings.

        Returns:
            Dictionary with save settings
        """
        _, format_value = SAVE_FORMATS[self._format_combo.currentIndex()]

        return {
            'save_enabled': self._save_enabled.isChecked(),
            'save_drive': self._save_drive.text(),
            'save_directory': self._save_directory.text(),
            'sample_name': self._sample_name.text(),
            'region': self._region.text(),
            'save_format': format_value,
            'save_mip': self._save_mip.isChecked(),
            'display_mip': self._display_mip.isChecked(),
            'save_subfolders': self._save_subfolders.isChecked(),
            'live_view': self._live_view.isChecked(),
            'comments': self._comments.toPlainText(),
        }

    def get_workflow_save_dict(self) -> Dict[str, str]:
        """
        Get save settings as workflow dictionary format.

        Returns:
            Dictionary for workflow file Experiment Settings section
        """
        settings = self.get_settings()

        workflow_dict = {
            'Save image drive': settings['save_drive'],
            'Save image directory': settings['save_directory'],
            'Sample': settings['sample_name'],
            'Region': settings['region'],
            'Save image data': settings['save_format'] if settings['save_enabled'] else 'NotSaved',
            'Save max projection': 'true' if settings['save_mip'] else 'false',
            'Display max projection': 'true' if settings['display_mip'] else 'false',
            'Save to subfolders': 'true' if settings['save_subfolders'] else 'false',
            'Work flow live view enabled': 'true' if settings['live_view'] else 'false',
            'Comments': settings['comments'],
        }

        return workflow_dict

    def set_save_drive(self, drive: str) -> None:
        """Set save drive path."""
        self._save_drive.setText(drive)

    def set_save_directory(self, directory: str) -> None:
        """Set save directory."""
        self._save_directory.setText(directory)

    def set_sample_name(self, name: str) -> None:
        """Set sample name."""
        self._sample_name.setText(name)

    def set_region(self, region: str) -> None:
        """Set region identifier."""
        self._region.setText(region)

    def set_comments(self, comments: str) -> None:
        """Set comments text."""
        self._comments.setPlainText(comments)

    def set_format(self, format_value: str) -> None:
        """
        Set save format.

        Args:
            format_value: Format value (Tiff, BigTiff, Raw, NotSaved)
        """
        for i, (_, value) in enumerate(SAVE_FORMATS):
            if value == format_value:
                self._format_combo.setCurrentIndex(i)
                break

    def set_save_mip(self, enabled: bool) -> None:
        """Set save MIP option."""
        self._save_mip.setChecked(enabled)

    def set_display_mip(self, enabled: bool) -> None:
        """Set display MIP option."""
        self._display_mip.setChecked(enabled)

    def set_save_subfolders(self, enabled: bool) -> None:
        """Set save to subfolders option."""
        self._save_subfolders.setChecked(enabled)

    def set_live_view(self, enabled: bool) -> None:
        """Set live view option."""
        self._live_view.setChecked(enabled)
