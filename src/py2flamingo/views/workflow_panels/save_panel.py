"""
Save location panel for workflow configuration.

Provides UI for configuring essential data save settings.
Save drive selection is prominently displayed since it's required.
Advanced settings (region, subfolders, comments) available via dialog.
"""

import logging
from typing import Optional, Dict, Any, List

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QComboBox, QCheckBox, QGroupBox, QGridLayout, QPushButton,
    QFileDialog, QMessageBox
)
from PyQt5.QtCore import pyqtSignal


# Save format options matching workflow file format
SAVE_FORMATS = [
    ("TIFF", "Tiff"),
    ("BigTIFF", "BigTiff"),
    ("Raw", "Raw"),
    ("Not Saved", "NotSaved"),
]

# Key for storing last used drive in configuration
LAST_USED_DRIVE_KEY = 'last_used_save_drive'


class SavePanel(QWidget):
    """
    Panel for configuring workflow data save settings.

    Provides:
    - Save enable checkbox
    - Save drive selection with refresh (REQUIRED - prominently displayed)
    - Directory and sample name inputs
    - Save format selection
    - MIP options
    - Advanced button for region, subfolders, comments

    Signals:
        settings_changed: Emitted when save settings change
    """

    settings_changed = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None, app=None, connection_service=None):
        """
        Initialize save panel.

        Args:
            parent: Parent widget
            app: FlamingoApplication instance for getting system settings
            connection_service: MVCConnectionService for querying available drives
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._app = app
        self._connection_service = connection_service
        self._available_drives: List[str] = []

        # Get last used drive from configuration (preferred over system default)
        self._last_used_drive = self._get_last_used_drive()
        self._default_save_drive = self._get_default_save_drive()

        # Use last used drive if available, otherwise fall back to system default
        initial_drive = self._last_used_drive or self._default_save_drive

        # Advanced settings (stored here, edited via dialog)
        self._save_drive = initial_drive
        self._region = ""
        self._save_subfolders = False
        self._live_view = True
        self._comments = ""

        self._setup_ui()

        # Try to auto-refresh drives on creation if connected
        self._try_auto_refresh_drives()

    def _get_last_used_drive(self) -> str:
        """Get last used save drive from configuration.

        Returns:
            Last used drive path, or empty string if not set.
        """
        if self._app is None:
            return ""

        try:
            config_service = getattr(self._app, 'config_service', None)
            if config_service is not None:
                # Check if config has the last used drive stored
                last_used = config_service.config.get(LAST_USED_DRIVE_KEY, '')
                if last_used:
                    self._logger.info(f"Loaded last used save drive: {last_used}")
                    return last_used
        except Exception as e:
            self._logger.debug(f"Could not get last used save drive: {e}")

        return ""

    def _save_last_used_drive(self, drive: str) -> None:
        """Save the last used drive to configuration.

        Args:
            drive: Drive path to save
        """
        if self._app is None or not drive:
            return

        try:
            config_service = getattr(self._app, 'config_service', None)
            if config_service is not None:
                config_service.config[LAST_USED_DRIVE_KEY] = drive
                self._logger.info(f"Saved last used save drive: {drive}")
        except Exception as e:
            self._logger.warning(f"Could not save last used drive: {e}")

    def _get_default_save_drive(self) -> str:
        """Get default save drive from system configuration.

        Returns empty string if no valid drive is configured.
        """
        if self._app is None:
            return ""

        try:
            config_service = getattr(self._app, 'config_service', None)
            if config_service is not None:
                location = config_service.get_data_storage_location()
                if location:
                    self._logger.info(f"Using data storage location from system: {location}")
                    return location
        except Exception as e:
            self._logger.warning(f"Could not get data storage location from system: {e}")

        return ""

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
        self._advanced_btn.setToolTip("Configure region, subfolders, and comments")
        self._advanced_btn.clicked.connect(self._on_advanced_clicked)
        header_layout.addWidget(self._advanced_btn)
        group_layout.addLayout(header_layout)

        # Main settings container
        self._settings_widget = QWidget()
        settings_layout = QGridLayout(self._settings_widget)
        settings_layout.setContentsMargins(0, 4, 0, 0)
        settings_layout.setSpacing(6)

        # Save Drive (REQUIRED - prominently displayed at top)
        drive_label = QLabel("Save Drive:")
        drive_label.setStyleSheet("font-weight: bold;")
        settings_layout.addWidget(drive_label, 0, 0)

        drive_layout = QHBoxLayout()
        self._save_drive_combo = QComboBox()
        self._save_drive_combo.setEditable(True)
        self._save_drive_combo.setMinimumWidth(200)
        self._save_drive_combo.setToolTip(
            "Storage location for image data (REQUIRED).\n"
            "Click 'Refresh' to query available drives from microscope."
        )
        # Initialize with current drive if set
        if self._save_drive:
            self._save_drive_combo.addItem(self._save_drive)
            self._save_drive_combo.setCurrentText(self._save_drive)
        else:
            self._save_drive_combo.setPlaceholderText("Click Refresh to load drives...")
        self._save_drive_combo.currentTextChanged.connect(self._on_drive_changed)
        drive_layout.addWidget(self._save_drive_combo, 1)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setToolTip("Query available storage drives from microscope")
        self._refresh_btn.clicked.connect(self._refresh_drives)
        # Disable refresh button if no connection service
        self._refresh_btn.setEnabled(self._connection_service is not None)
        drive_layout.addWidget(self._refresh_btn)

        self._configure_local_btn = QPushButton("Local Path...")
        self._configure_local_btn.setToolTip(
            "Configure local path for this drive.\n"
            "Required for post-collection folder reorganization\n"
            "into nested structure for MIP Overview compatibility."
        )
        self._configure_local_btn.clicked.connect(self._configure_local_path)
        drive_layout.addWidget(self._configure_local_btn)

        settings_layout.addLayout(drive_layout, 0, 1)

        # Warning label for no drive
        self._drive_warning = QLabel()
        self._update_drive_warning()
        settings_layout.addWidget(self._drive_warning, 1, 0, 1, 2)

        # Directory (subdirectory within save drive)
        settings_layout.addWidget(QLabel("Directory:"), 2, 0)
        self._save_directory = QLineEdit()
        self._save_directory.setPlaceholderText("experiment_01")
        self._save_directory.textChanged.connect(self._on_settings_changed)
        settings_layout.addWidget(self._save_directory, 2, 1)

        # Sample name
        settings_layout.addWidget(QLabel("Sample:"), 3, 0)
        self._sample_name = QLineEdit()
        self._sample_name.setPlaceholderText("Sample_001")
        self._sample_name.textChanged.connect(self._on_settings_changed)
        settings_layout.addWidget(self._sample_name, 3, 1)

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
        settings_layout.addLayout(format_row, 4, 0, 1, 2)

        group_layout.addWidget(self._settings_widget)

        group.setLayout(group_layout)
        layout.addWidget(group)

    def _update_drive_warning(self) -> None:
        """Update the drive warning label based on current save drive."""
        if self._save_drive:
            self._drive_warning.hide()
        else:
            self._drive_warning.setText("⚠ No save drive selected - workflow cannot save data")
            self._drive_warning.setStyleSheet("color: #e74c3c; font-size: 9pt; font-weight: bold;")
            self._drive_warning.show()

    def _on_drive_changed(self, drive: str) -> None:
        """Handle save drive selection change."""
        self._save_drive = drive
        self._update_drive_warning()
        # Save as last used drive
        self._save_last_used_drive(drive)
        self._on_settings_changed()

    def _refresh_drives(self) -> None:
        """Query available drives from microscope server."""
        if not self._connection_service:
            self._logger.warning("No connection service available for drive refresh")
            return

        try:
            # Disable button during query
            self._refresh_btn.setEnabled(False)
            self._refresh_btn.setText("...")

            # Query available drives
            drives = self._connection_service.query_available_drives(timeout=3.0)

            if drives:
                self._available_drives = drives
                # Remember current selection
                current = self._save_drive_combo.currentText()

                # Update dropdown
                self._save_drive_combo.clear()
                for drive in drives:
                    self._save_drive_combo.addItem(drive)

                # Try to restore previous selection or last used drive
                preferred = current or self._last_used_drive
                if preferred:
                    index = self._save_drive_combo.findText(preferred)
                    if index >= 0:
                        self._save_drive_combo.setCurrentIndex(index)
                        self._logger.info(f"Restored save drive selection: {preferred}")
                    elif drives:
                        # Preferred not available, use first drive
                        self._save_drive_combo.setCurrentIndex(0)
                        self._logger.info(f"Last used drive not available, using: {drives[0]}")
                elif drives:
                    self._save_drive_combo.setCurrentIndex(0)

                self._logger.info(f"Refreshed {len(drives)} available drives")
            else:
                self._logger.warning("No drives returned from server")

        except Exception as e:
            self._logger.error(f"Failed to refresh drives: {e}")

        finally:
            # Re-enable button
            self._refresh_btn.setEnabled(True)
            self._refresh_btn.setText("Refresh")

    def _try_auto_refresh_drives(self) -> None:
        """Try to auto-refresh drives on panel creation if connected."""
        if not self._connection_service:
            return

        # Check if we're connected
        try:
            if hasattr(self._connection_service, 'is_connected') and self._connection_service.is_connected():
                self._logger.info("Auto-refreshing drives on panel creation")
                self._refresh_drives()
        except Exception as e:
            self._logger.debug(f"Auto-refresh not performed: {e}")

    def _configure_local_path(self) -> None:
        """Open dialog to configure local path for current drive.

        This mapping allows post-collection folder reorganization from
        flattened structure (required by server) to nested structure
        (required by MIP Overview).
        """
        current_drive = self._save_drive_combo.currentText()
        if not current_drive:
            QMessageBox.warning(self, "No Drive", "Select a save drive first.")
            return

        # Get config service
        config_service = self._get_config_service()
        if not config_service:
            QMessageBox.warning(
                self, "Not Available",
                "Configuration service not available.\n"
                "Local path mapping requires the application to be fully initialized."
            )
            return

        # Get current local path if set
        current_local = config_service.get_local_path_for_drive(current_drive) or ""

        # Open directory selection dialog
        from pathlib import Path
        local_path = QFileDialog.getExistingDirectory(
            self,
            f"Select Local Path for {current_drive}",
            current_local or str(Path.home())
        )

        if local_path:
            config_service.set_drive_mapping(current_drive, local_path)
            QMessageBox.information(
                self, "Local Path Configured",
                f"Local path mapping saved:\n\n"
                f"Server drive: {current_drive}\n"
                f"Local path: {local_path}\n\n"
                "After tile collection completes, folders will be\n"
                "automatically reorganized into nested structure\n"
                "for MIP Overview compatibility."
            )
            self._logger.info(f"Configured local path: {current_drive} -> {local_path}")

    def _get_config_service(self):
        """Get ConfigurationService from application."""
        if self._app and hasattr(self._app, 'config_service'):
            return self._app.config_service
        return None

    def _on_save_enabled_changed(self, state: int) -> None:
        """Handle save enabled checkbox change."""
        self._settings_widget.setEnabled(state != 0)
        self._on_settings_changed()

    def _on_settings_changed(self) -> None:
        """Handle any settings change."""
        settings = self.get_settings()
        self.settings_changed.emit(settings)

    def _on_advanced_clicked(self) -> None:
        """Open advanced save settings dialog.

        Note: Save drive is now in the main UI, not in Advanced dialog.
        Advanced dialog handles: region, subfolders, live view, comments.
        """
        from py2flamingo.views.dialogs import AdvancedSaveDialog

        dialog = AdvancedSaveDialog(
            parent=self,
            default_drive=self._default_save_drive,
            connection_service=self._connection_service,
            hide_drive_selection=True  # Drive is now in main UI
        )
        dialog.set_settings({
            'save_drive': self._save_drive,  # Pass for reference but hidden
            'region': self._region,
            'save_subfolders': self._save_subfolders,
            'live_view': self._live_view,
            'comments': self._comments,
        })

        if dialog.exec_() == dialog.Accepted:
            settings = dialog.get_settings()
            # Drive is set in main UI, not here
            self._region = settings['region']
            self._save_subfolders = settings['save_subfolders']
            self._live_view = settings['live_view']
            self._comments = settings['comments']
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

    def set_settings(self, settings: Dict[str, Any]) -> None:
        """
        Set save settings from dictionary.

        Used for restoring settings from persistence.

        Args:
            settings: Dictionary with save settings
        """
        if not settings:
            return

        if 'save_enabled' in settings:
            self._save_enabled.setChecked(settings['save_enabled'])

        if 'save_drive' in settings:
            self._save_drive = settings['save_drive']
            # Try to set combo box if the drive is in the list
            idx = self._save_drive_combo.findText(settings['save_drive'])
            if idx >= 0:
                self._save_drive_combo.setCurrentIndex(idx)
            else:
                self._save_drive_combo.setCurrentText(settings['save_drive'])

        if 'save_directory' in settings:
            self._save_directory.setText(settings['save_directory'])

        if 'sample_name' in settings:
            self._sample_name.setText(settings['sample_name'])

        if 'region' in settings:
            self._region = settings['region']

        if 'save_format' in settings:
            # Find format index by value
            for i, (_, format_value) in enumerate(SAVE_FORMATS):
                if format_value == settings['save_format']:
                    self._format_combo.setCurrentIndex(i)
                    break

        if 'save_mip' in settings:
            self._save_mip.setChecked(settings['save_mip'])

        if 'display_mip' in settings:
            self._display_mip.setChecked(settings['display_mip'])

        if 'save_subfolders' in settings:
            self._save_subfolders = settings['save_subfolders']

        if 'live_view' in settings:
            self._live_view = settings['live_view']

        if 'comments' in settings:
            self._comments = settings['comments']

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
        self._save_drive_combo.setCurrentText(drive)
        self._update_drive_warning()

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
            self.set_save_drive(settings['save_drive'])
        if 'region' in settings:
            self._region = settings['region']
        if 'save_subfolders' in settings:
            self._save_subfolders = settings['save_subfolders']
        if 'live_view' in settings:
            self._live_view = settings['live_view']
        if 'comments' in settings:
            self._comments = settings['comments']

    def set_app(self, app) -> None:
        """Set application reference for configuration access.

        This can be called after construction if app wasn't available at init time.
        Enables last-used drive persistence and auto-refresh features.

        Args:
            app: FlamingoApplication instance
        """
        self._app = app
        # Now that we have app, try to load last used drive
        self._last_used_drive = self._get_last_used_drive()
        if self._last_used_drive and not self._save_drive:
            self.set_save_drive(self._last_used_drive)
            self._logger.info(f"Set last used drive from app: {self._last_used_drive}")
        # Try auto-refresh if connected
        self._try_auto_refresh_drives()
