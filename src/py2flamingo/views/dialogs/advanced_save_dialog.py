"""Advanced Save Settings Dialog.

Dialog for configuring rarely-changed save settings
such as storage drive, region, subfolder options, and extended comments.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QCheckBox, QGroupBox, QGridLayout,
    QPushButton, QDialogButtonBox, QPlainTextEdit, QFileDialog
)
from PyQt5.QtCore import Qt


class AdvancedSaveDialog(QDialog):
    """Dialog for advanced save settings.

    Settings included:
    - Save drive (storage location)
    - Region identifier
    - Save to subfolders option
    - Enable live view option
    - Extended comments field
    """

    def __init__(self, parent: Optional[QDialog] = None, default_drive: str = "/media/deploy/ctlsm1"):
        """Initialize the dialog.

        Args:
            parent: Parent widget
            default_drive: Default save drive path
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._default_drive = default_drive

        self.setWindowTitle("Advanced Save Settings")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        self.setModal(True)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Storage Location Section
        storage_group = QGroupBox("Storage Location")
        storage_layout = QGridLayout()
        storage_layout.setSpacing(8)

        # Save drive
        storage_layout.addWidget(QLabel("Save Drive:"), 0, 0)

        drive_layout = QHBoxLayout()
        self._save_drive = QLineEdit()
        self._save_drive.setText(self._default_drive)
        self._save_drive.setPlaceholderText("/media/deploy/ctlsm1")
        self._save_drive.setToolTip(
            "Base path for data storage.\n"
            "This is typically a network share or local disk."
        )
        drive_layout.addWidget(self._save_drive)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_drive)
        drive_layout.addWidget(browse_btn)

        storage_layout.addLayout(drive_layout, 0, 1)

        # Region identifier
        storage_layout.addWidget(QLabel("Region:"), 1, 0)
        self._region = QLineEdit()
        self._region.setPlaceholderText("Optional region identifier (e.g., ROI_1)")
        self._region.setToolTip(
            "Optional identifier for the region being imaged.\n"
            "Used for organizing data from multiple regions."
        )
        storage_layout.addWidget(self._region, 1, 1)

        storage_group.setLayout(storage_layout)
        layout.addWidget(storage_group)

        # Save Options Section
        options_group = QGroupBox("Save Options")
        options_layout = QVBoxLayout()

        self._save_subfolders = QCheckBox("Save to Subfolders")
        self._save_subfolders.setToolTip(
            "When enabled, creates separate subfolders for:\n"
            "- Each timepoint (time-lapse)\n"
            "- Each position (tiling)\n"
            "- Each channel (multi-laser)"
        )
        options_layout.addWidget(self._save_subfolders)

        self._live_view = QCheckBox("Enable Live View During Acquisition")
        self._live_view.setChecked(True)
        self._live_view.setToolTip(
            "Show live images in the viewer during acquisition.\n"
            "Disable for maximum performance on long acquisitions."
        )
        options_layout.addWidget(self._live_view)

        options_group.setLayout(options_layout)
        layout.addWidget(options_group)

        # Comments Section
        comments_group = QGroupBox("Extended Comments")
        comments_layout = QVBoxLayout()

        comments_info = QLabel("Add detailed notes about this acquisition:")
        comments_info.setStyleSheet("color: gray; font-size: 9pt;")
        comments_layout.addWidget(comments_info)

        self._comments = QPlainTextEdit()
        self._comments.setPlaceholderText(
            "Enter detailed notes about this acquisition...\n\n"
            "Examples:\n"
            "- Sample preparation details\n"
            "- Experimental conditions\n"
            "- Imaging objectives\n"
            "- Notes for analysis"
        )
        self._comments.setMinimumHeight(120)
        comments_layout.addWidget(self._comments)

        comments_group.setLayout(comments_layout)
        layout.addWidget(comments_group)

        # Reset button
        reset_layout = QHBoxLayout()
        reset_layout.addStretch()
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self._reset_defaults)
        reset_layout.addWidget(reset_btn)
        layout.addLayout(reset_layout)

        # Dialog buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _browse_drive(self) -> None:
        """Open file dialog to browse for save drive."""
        current = self._save_drive.text() or self._default_drive
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Save Drive",
            current,
            QFileDialog.ShowDirsOnly
        )
        if directory:
            self._save_drive.setText(directory)

    def _reset_defaults(self) -> None:
        """Reset all settings to defaults."""
        self._save_drive.setText(self._default_drive)
        self._region.clear()
        self._save_subfolders.setChecked(False)
        self._live_view.setChecked(True)
        self._comments.clear()

    def get_settings(self) -> Dict[str, Any]:
        """Get current advanced save settings.

        Returns:
            Dictionary with settings
        """
        return {
            'save_drive': self._save_drive.text(),
            'region': self._region.text(),
            'save_subfolders': self._save_subfolders.isChecked(),
            'live_view': self._live_view.isChecked(),
            'comments': self._comments.toPlainText(),
        }

    def set_settings(self, settings: Dict[str, Any]) -> None:
        """Set advanced save settings.

        Args:
            settings: Dictionary with settings to apply
        """
        if 'save_drive' in settings:
            self._save_drive.setText(settings['save_drive'])
        if 'region' in settings:
            self._region.setText(settings['region'])
        if 'save_subfolders' in settings:
            self._save_subfolders.setChecked(settings['save_subfolders'])
        if 'live_view' in settings:
            self._live_view.setChecked(settings['live_view'])
        if 'comments' in settings:
            self._comments.setPlainText(settings['comments'])

    # Individual property accessors
    @property
    def save_drive(self) -> str:
        """Get save drive path."""
        return self._save_drive.text()

    @save_drive.setter
    def save_drive(self, value: str) -> None:
        """Set save drive path."""
        self._save_drive.setText(value)

    @property
    def region(self) -> str:
        """Get region identifier."""
        return self._region.text()

    @region.setter
    def region(self, value: str) -> None:
        """Set region identifier."""
        self._region.setText(value)

    @property
    def save_subfolders(self) -> bool:
        """Get save to subfolders option."""
        return self._save_subfolders.isChecked()

    @save_subfolders.setter
    def save_subfolders(self, enabled: bool) -> None:
        """Set save to subfolders option."""
        self._save_subfolders.setChecked(enabled)

    @property
    def live_view(self) -> bool:
        """Get live view enabled option."""
        return self._live_view.isChecked()

    @live_view.setter
    def live_view(self, enabled: bool) -> None:
        """Set live view enabled option."""
        self._live_view.setChecked(enabled)

    @property
    def comments(self) -> str:
        """Get comments text."""
        return self._comments.toPlainText()

    @comments.setter
    def comments(self, text: str) -> None:
        """Set comments text."""
        self._comments.setPlainText(text)
