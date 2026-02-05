"""Advanced Save Settings Dialog.

Dialog for configuring rarely-changed save settings
such as storage drive, region, subfolder options, and extended comments.
"""

import logging
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QCheckBox, QGroupBox, QGridLayout,
    QPushButton, QDialogButtonBox, QPlainTextEdit, QFileDialog,
    QComboBox
)
from py2flamingo.services.window_geometry_manager import PersistentDialog
from py2flamingo.resources import get_app_icon
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon


class AdvancedSaveDialog(PersistentDialog):
    """Dialog for advanced save settings.

    Settings included:
    - Save drive (storage location) - optional, can be hidden if in main UI
    - Region identifier
    - Save to subfolders option
    - Enable live view option
    - Extended comments field
    """

    def __init__(self, parent=None,
                 default_drive: str = "",
                 connection_service = None,
                 hide_drive_selection: bool = False):
        """Initialize the dialog.

        Args:
            parent: Parent widget
            default_drive: Default save drive path (empty if not configured)
            connection_service: MVCConnectionService for querying available drives
            hide_drive_selection: If True, hide drive selection (it's in main UI)
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._default_drive = default_drive
        self._connection_service = connection_service
        self._hide_drive_selection = hide_drive_selection

        self.setWindowTitle("Advanced Save Settings")
        self.setWindowIcon(get_app_icon())  # Use flamingo icon
        self.setMinimumWidth(500)
        self.setMinimumHeight(350 if hide_drive_selection else 400)
        self.setModal(True)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Storage Location Section - only show if drive selection not hidden
        if not self._hide_drive_selection:
            storage_group = QGroupBox("Storage Location")
            storage_layout = QGridLayout()
            storage_layout.setSpacing(8)

            # Save drive (dropdown with refresh button)
            storage_layout.addWidget(QLabel("Save Drive:"), 0, 0)

            drive_layout = QHBoxLayout()
            self._save_drive = QComboBox()
            self._save_drive.setEditable(True)
            self._save_drive.setMinimumWidth(300)
            # Only add default if it's a valid path
            if self._default_drive:
                self._save_drive.addItem(self._default_drive)
                self._save_drive.setCurrentText(self._default_drive)
            else:
                # No default - show placeholder
                self._save_drive.setPlaceholderText("Click Refresh to query available drives...")
            self._save_drive.setToolTip(
                "Base path for data storage.\n"
                "This is typically a network share or local disk.\n\n"
                "Click 'Refresh' to query available drives from microscope."
            )
            drive_layout.addWidget(self._save_drive, 1)

            self._refresh_btn = QPushButton("Refresh")
            self._refresh_btn.setToolTip("Query available storage drives from microscope")
            self._refresh_btn.clicked.connect(self._refresh_drives)
            # Disable refresh button if no connection service
            self._refresh_btn.setEnabled(self._connection_service is not None)
            drive_layout.addWidget(self._refresh_btn)

            browse_btn = QPushButton("Browse...")
            browse_btn.setToolTip("Browse for local directory")
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
        else:
            # Drive selection is in main UI, just create hidden storage for it
            self._save_drive = None  # No widget, will use _hidden_save_drive
            self._hidden_save_drive = ""

            # Region-only section
            region_group = QGroupBox("Region Settings")
            region_layout = QGridLayout()
            region_layout.setSpacing(8)

            region_layout.addWidget(QLabel("Region:"), 0, 0)
            self._region = QLineEdit()
            self._region.setPlaceholderText("Optional region identifier (e.g., ROI_1)")
            self._region.setToolTip(
                "Optional identifier for the region being imaged.\n"
                "Used for organizing data from multiple regions."
            )
            region_layout.addWidget(self._region, 0, 1)

            region_group.setLayout(region_layout)
            layout.addWidget(region_group)

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

    def _refresh_drives(self) -> None:
        """Query available drives from microscope server."""
        if not self._connection_service or self._save_drive is None:
            self._logger.warning("No connection service available or drive selection hidden")
            return

        try:
            # Disable button during query
            self._refresh_btn.setEnabled(False)
            self._refresh_btn.setText("Querying...")

            # Query available drives
            drives = self._connection_service.query_available_drives(timeout=3.0)

            if drives:
                # Remember current selection
                current = self._save_drive.currentText()

                # Update dropdown
                self._save_drive.clear()
                for drive in drives:
                    self._save_drive.addItem(drive)

                # Try to restore previous selection
                index = self._save_drive.findText(current)
                if index >= 0:
                    self._save_drive.setCurrentIndex(index)
                elif drives:
                    self._save_drive.setCurrentIndex(0)

                self._logger.info(f"Refreshed {len(drives)} available drives")
            else:
                # No drives returned - warn user
                self._logger.warning("No drives returned from server, using default")
                if self._save_drive.count() == 0 and self._default_drive:
                    self._save_drive.addItem(self._default_drive)

        except Exception as e:
            self._logger.error(f"Failed to refresh drives: {e}")

        finally:
            # Re-enable button
            self._refresh_btn.setEnabled(True)
            self._refresh_btn.setText("Refresh")

    def _browse_drive(self) -> None:
        """Open file dialog to browse for save drive."""
        if self._save_drive is None:
            return

        current = self._save_drive.currentText() or self._default_drive or ""
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Save Drive",
            current,
            QFileDialog.ShowDirsOnly
        )
        if directory:
            self._save_drive.setCurrentText(directory)

    def _reset_defaults(self) -> None:
        """Reset all settings to defaults."""
        if self._save_drive is not None:
            if self._default_drive:
                self._save_drive.setCurrentText(self._default_drive)
            else:
                self._save_drive.setCurrentText("")
        self._region.clear()
        self._save_subfolders.setChecked(False)
        self._live_view.setChecked(True)
        self._comments.clear()

    def get_settings(self) -> Dict[str, Any]:
        """Get current advanced save settings.

        Returns:
            Dictionary with settings
        """
        # Handle hidden drive selection
        if self._save_drive is not None:
            save_drive = self._save_drive.currentText()
        else:
            save_drive = self._hidden_save_drive

        return {
            'save_drive': save_drive,
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
            if self._save_drive is not None:
                self._save_drive.setCurrentText(settings['save_drive'])
            else:
                self._hidden_save_drive = settings['save_drive']
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
        if self._save_drive is not None:
            return self._save_drive.currentText()
        return self._hidden_save_drive

    @save_drive.setter
    def save_drive(self, value: str) -> None:
        """Set save drive path."""
        if self._save_drive is not None:
            self._save_drive.setCurrentText(value)
        else:
            self._hidden_save_drive = value

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
