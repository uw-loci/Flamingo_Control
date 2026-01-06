"""
Save location panel for workflow configuration.

Provides UI for configuring essential data save settings.
Advanced settings (drive, region, subfolders) available via dialog.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QComboBox, QCheckBox, QGroupBox, QGridLayout, QPushButton
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
    - Save enable checkbox
    - Directory and sample name inputs
    - Save format selection
    - MIP options
    - Advanced button for drive, region, subfolders, comments

    Signals:
        settings_changed: Emitted when save settings change
    """

    settings_changed = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None, app=None):
        """
        Initialize save panel.

        Args:
            parent: Parent widget
            app: FlamingoApplication instance for getting system settings
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._app = app

        # Get default storage location from system
        self._default_save_drive = self._get_default_save_drive()

        # Advanced settings (stored here, edited via dialog)
        self._save_drive = self._default_save_drive
        self._region = ""
        self._save_subfolders = False
        self._live_view = True
        self._comments = ""

        self._setup_ui()

    def _get_default_save_drive(self) -> str:
        """Get default save drive from system configuration."""
        default = "/media/deploy/ctlsm1"

        if self._app is None:
            return default

        try:
            config_service = getattr(self._app, 'config_service', None)
            if config_service is not None:
                location = config_service.get_data_storage_location()
                if location:
                    self._logger.info(f"Using data storage location from system: {location}")
                    return location
        except Exception as e:
            self._logger.warning(f"Could not get data storage location from system: {e}")

        return default

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Save settings group
        group = QGroupBox("Data Saving")
        group_layout = QVBoxLayout()
        group_layout.setSpacing(6)

        # Header with save enable and Advanced button
        header_layout = QHBoxLayout()

        self._save_enabled = QCheckBox("Save Images")
        self._save_enabled.setChecked(True)
        self._save_enabled.stateChanged.connect(self._on_save_enabled_changed)
        header_layout.addWidget(self._save_enabled)

        header_layout.addStretch()

        self._advanced_btn = QPushButton("Advanced...")
        self._advanced_btn.setFixedWidth(90)
        self._advanced_btn.clicked.connect(self._on_advanced_clicked)
        header_layout.addWidget(self._advanced_btn)
        group_layout.addLayout(header_layout)

        # Main settings container
        self._settings_widget = QWidget()
        settings_layout = QGridLayout(self._settings_widget)
        settings_layout.setContentsMargins(0, 4, 0, 0)
        settings_layout.setSpacing(6)

        # Directory (subdirectory within save drive)
        settings_layout.addWidget(QLabel("Directory:"), 0, 0)
        self._save_directory = QLineEdit()
        self._save_directory.setPlaceholderText("experiment_01")
        self._save_directory.textChanged.connect(self._on_settings_changed)
        settings_layout.addWidget(self._save_directory, 0, 1)

        # Sample name
        settings_layout.addWidget(QLabel("Sample:"), 1, 0)
        self._sample_name = QLineEdit()
        self._sample_name.setPlaceholderText("Sample_001")
        self._sample_name.textChanged.connect(self._on_settings_changed)
        settings_layout.addWidget(self._sample_name, 1, 1)

        # Format and MIP options row
        format_row = QHBoxLayout()

        format_row.addWidget(QLabel("Format:"))
        self._format_combo = QComboBox()
        for display_name, _ in SAVE_FORMATS:
            self._format_combo.addItem(display_name)
        self._format_combo.setCurrentIndex(0)  # Default to TIFF
        self._format_combo.setFixedWidth(90)
        self._format_combo.currentIndexChanged.connect(self._on_settings_changed)
        format_row.addWidget(self._format_combo)

        format_row.addSpacing(16)

        self._save_mip = QCheckBox("Save MIP")
        self._save_mip.setToolTip("Save Maximum Intensity Projection")
        self._save_mip.stateChanged.connect(self._on_settings_changed)
        format_row.addWidget(self._save_mip)

        self._display_mip = QCheckBox("Display MIP")
        self._display_mip.setToolTip("Display MIP during acquisition")
        self._display_mip.setChecked(True)
        self._display_mip.stateChanged.connect(self._on_settings_changed)
        format_row.addWidget(self._display_mip)

        format_row.addStretch()
        settings_layout.addLayout(format_row, 2, 0, 1, 2)

        # Drive info (compact display)
        self._drive_info = QLabel(f"Drive: {self._save_drive}")
        self._drive_info.setStyleSheet("color: gray; font-size: 9pt;")
        settings_layout.addWidget(self._drive_info, 3, 0, 1, 2)

        group_layout.addWidget(self._settings_widget)

        group.setLayout(group_layout)
        layout.addWidget(group)

    def _on_save_enabled_changed(self, state: int) -> None:
        """Handle save enabled checkbox change."""
        self._settings_widget.setEnabled(state != 0)
        self._on_settings_changed()

    def _on_settings_changed(self) -> None:
        """Handle any settings change."""
        settings = self.get_settings()
        self.settings_changed.emit(settings)

    def _on_advanced_clicked(self) -> None:
        """Open advanced save settings dialog."""
        from py2flamingo.views.dialogs import AdvancedSaveDialog

        dialog = AdvancedSaveDialog(self, default_drive=self._default_save_drive)
        dialog.set_settings({
            'save_drive': self._save_drive,
            'region': self._region,
            'save_subfolders': self._save_subfolders,
            'live_view': self._live_view,
            'comments': self._comments,
        })

        if dialog.exec_() == dialog.Accepted:
            settings = dialog.get_settings()
            self._save_drive = settings['save_drive']
            self._region = settings['region']
            self._save_subfolders = settings['save_subfolders']
            self._live_view = settings['live_view']
            self._comments = settings['comments']

            # Update drive info display
            self._drive_info.setText(f"Drive: {self._save_drive}")
            self._on_settings_changed()

    def get_settings(self) -> Dict[str, Any]:
        """
        Get current save settings.

        Returns:
            Dictionary with save settings
        """
        _, format_value = SAVE_FORMATS[self._format_combo.currentIndex()]

        return {
            'save_enabled': self._save_enabled.isChecked(),
            'save_drive': self._save_drive,
            'save_directory': self._save_directory.text(),
            'sample_name': self._sample_name.text(),
            'region': self._region,
            'save_format': format_value,
            'save_mip': self._save_mip.isChecked(),
            'display_mip': self._display_mip.isChecked(),
            'save_subfolders': self._save_subfolders,
            'live_view': self._live_view,
            'comments': self._comments,
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
        self._save_drive = drive
        self._drive_info.setText(f"Drive: {drive}")

    def set_save_directory(self, directory: str) -> None:
        """Set save directory."""
        self._save_directory.setText(directory)

    def set_sample_name(self, name: str) -> None:
        """Set sample name."""
        self._sample_name.setText(name)

    def set_region(self, region: str) -> None:
        """Set region identifier."""
        self._region = region

    def set_comments(self, comments: str) -> None:
        """Set comments text."""
        self._comments = comments

    def set_format(self, format_value: str) -> None:
        """Set save format."""
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
        self._save_subfolders = enabled

    def set_live_view(self, enabled: bool) -> None:
        """Set live view option."""
        self._live_view = enabled

    # Advanced settings accessors
    def get_advanced_settings(self) -> Dict[str, Any]:
        """Get advanced save settings."""
        return {
            'save_drive': self._save_drive,
            'region': self._region,
            'save_subfolders': self._save_subfolders,
            'live_view': self._live_view,
            'comments': self._comments,
        }

    def set_advanced_settings(self, settings: Dict[str, Any]) -> None:
        """Set advanced save settings."""
        if 'save_drive' in settings:
            self._save_drive = settings['save_drive']
            self._drive_info.setText(f"Drive: {self._save_drive}")
        if 'region' in settings:
            self._region = settings['region']
        if 'save_subfolders' in settings:
            self._save_subfolders = settings['save_subfolders']
        if 'live_view' in settings:
            self._live_view = settings['live_view']
        if 'comments' in settings:
            self._comments = settings['comments']
