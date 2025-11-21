"""
Sample Information View for Flamingo Control GUI.

This view provides UI elements for configuring sample information including:
- Sample name
- Data save path

These settings are used for naming and organizing acquired image files.
"""

import logging
from pathlib import Path
from typing import Optional

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QFileDialog, QFormLayout
)
from PyQt5.QtCore import pyqtSignal

from py2flamingo.views.colors import SUCCESS_COLOR, ERROR_COLOR


class SampleInfoView(QWidget):
    """
    View for configuring sample information and data save paths.

    This view allows users to specify:
    - Sample name: Used in naming output files
    - Save path: Directory where acquired images will be saved

    Signals:
        sample_name_changed: Emitted when sample name is updated
        save_path_changed: Emitted when save path is updated
    """

    # Signals
    sample_name_changed = pyqtSignal(str)  # New sample name
    save_path_changed = pyqtSignal(str)  # New save path

    def __init__(self, parent: Optional[QWidget] = None):
        """
        Initialize sample information view.

        Args:
            parent: Parent widget (optional)
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)

        # Default values
        self._sample_name = ""
        self._save_path = ""  # Relative path or subdirectory

        # Network share configuration (microscope's perspective)
        # This is the base network path that the microscope can access
        self._network_share_base = r"\\192.168.1.2\CTLSM1"  # Default
        self._local_mount_point = None  # Optional: where this is mounted locally

        self._init_ui()

    def _init_ui(self):
        """Initialize user interface."""
        layout = QVBoxLayout(self)

        # Network Configuration Group
        network_group = self._create_network_config_group()
        layout.addWidget(network_group)

        # Sample Information Group
        sample_group = self._create_sample_info_group()
        layout.addWidget(sample_group)

        # Save Path Group
        path_group = self._create_save_path_group()
        layout.addWidget(path_group)

        # Info/Help Text
        info_group = self._create_info_group()
        layout.addWidget(info_group)

        # Add stretch to push everything to the top
        layout.addStretch()

    def _create_sample_info_group(self) -> QGroupBox:
        """
        Create sample information input group.

        Returns:
            QGroupBox with sample name input
        """
        group = QGroupBox("Sample Information")
        layout = QFormLayout()

        # Sample name input
        self.sample_name_input = QLineEdit()
        self.sample_name_input.setPlaceholderText("Enter sample name (e.g., Sample_001)")
        self.sample_name_input.textChanged.connect(self._on_sample_name_changed)
        layout.addRow("Sample Name:", self.sample_name_input)

        # Display current sample name
        self.sample_name_display = QLabel("Current: <not set>")
        self.sample_name_display.setStyleSheet("color: gray; font-style: italic;")
        layout.addRow("", self.sample_name_display)

        group.setLayout(layout)
        return group

    def _create_network_config_group(self) -> QGroupBox:
        """
        Create network share configuration group.

        Returns:
            QGroupBox with network share settings
        """
        group = QGroupBox("Network Share Configuration")
        layout = QVBoxLayout()

        # Info text
        info = QLabel(
            "<b>Important:</b> Paths must be accessible from the microscope PC.<br>"
            "Configure the network share base path (UNC format)."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #d35400; padding: 5px; background-color: #fef5e7;")
        layout.addWidget(info)

        # Network share base path
        share_layout = QHBoxLayout()
        share_layout.addWidget(QLabel("Network Share Base:"))

        self.network_share_input = QLineEdit()
        self.network_share_input.setText(self._network_share_base)
        self.network_share_input.setPlaceholderText(r"\\192.168.1.2\CTLSM1")
        self.network_share_input.textChanged.connect(self._on_network_share_changed)
        share_layout.addWidget(self.network_share_input)

        layout.addLayout(share_layout)

        # Optional: Local mount point (for browsing)
        mount_layout = QHBoxLayout()
        mount_layout.addWidget(QLabel("Local Mount Point (optional):"))

        self.local_mount_input = QLineEdit()
        self.local_mount_input.setPlaceholderText(r"Z:\ or D:\microscope_data (must be the network share)")
        self.local_mount_input.textChanged.connect(self._on_local_mount_changed)
        mount_layout.addWidget(self.local_mount_input)

        browse_mount_btn = QPushButton("Browse...")
        browse_mount_btn.clicked.connect(self._browse_local_mount)
        mount_layout.addWidget(browse_mount_btn)

        layout.addLayout(mount_layout)

        # Warning about mount point
        mount_warning = QLabel(
            "⚠ Local Mount Point must be where the network share is mounted/mapped on YOUR PC. "
            "Example: If \\\\192.168.1.2\\CTLSM1 is mapped to Z:\\, enter Z:\\"
        )
        mount_warning.setWordWrap(True)
        mount_warning.setStyleSheet(
            "color: #d35400; font-size: 9pt; padding: 3px; "
            "background-color: #fef5e7; border-left: 3px solid #d35400;"
        )
        layout.addWidget(mount_warning)

        group.setLayout(layout)
        return group

    def _create_save_path_group(self) -> QGroupBox:
        """
        Create save path configuration group.

        Returns:
            QGroupBox with save path input and browse button
        """
        group = QGroupBox("Data Subdirectory")
        layout = QVBoxLayout()

        # Subdirectory input
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("Subdirectory:"))

        self.save_path_input = QLineEdit()
        self.save_path_input.setText(self._save_path)
        self.save_path_input.setPlaceholderText("data/experiment1 (relative to network share)")
        self.save_path_input.textChanged.connect(self._on_save_path_changed)
        path_layout.addWidget(self.save_path_input)

        # Browse button (only works if local mount configured)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_save_path)
        path_layout.addWidget(browse_btn)

        layout.addLayout(path_layout)

        # Display network path (what microscope will use)
        self.network_path_display = QLabel()
        self.network_path_display.setStyleSheet(
            "color: #27ae60; font-weight: bold; font-size: 10pt; "
            "padding: 5px; background-color: #eafaf1; border: 1px solid #27ae60;"
        )
        self.network_path_display.setWordWrap(True)
        layout.addWidget(QLabel("Network Path (sent to microscope):"))
        layout.addWidget(self.network_path_display)

        # Display local path (if mount point configured)
        self.local_path_display = QLabel()
        self.local_path_display.setStyleSheet("color: gray; font-size: 10pt;")
        self.local_path_display.setWordWrap(True)
        layout.addWidget(self.local_path_display)

        # Create directory button with warning
        create_dir_layout = QVBoxLayout()

        btn_layout = QHBoxLayout()
        self.create_dir_btn = QPushButton("Create Directory Locally")
        self.create_dir_btn.clicked.connect(self._create_directory)
        self.create_dir_btn.setToolTip(
            "Creates directory at the local mount point.\n"
            "Only works if mount point is properly configured as the network share."
        )
        btn_layout.addWidget(self.create_dir_btn)
        btn_layout.addStretch()
        create_dir_layout.addLayout(btn_layout)

        # Warning label
        create_warning = QLabel(
            "⚠ Only creates locally! Directory must be within the network share to be accessible to microscope."
        )
        create_warning.setWordWrap(True)
        create_warning.setStyleSheet("color: #d35400; font-size: 9pt; font-style: italic;")
        create_dir_layout.addWidget(create_warning)

        layout.addLayout(create_dir_layout)

        # Initial update
        self._update_path_displays()

        group.setLayout(layout)
        return group

    def _create_info_group(self) -> QGroupBox:
        """
        Create information/help group.

        Returns:
            QGroupBox with usage information
        """
        group = QGroupBox("Usage Information")
        layout = QVBoxLayout()

        info_text = QLabel(
            "<b>Network Share Base:</b> The base UNC path that the microscope PC can access.<br>"
            "<b>Local Mount Point:</b> (Optional) Where the share is mounted/mapped on YOUR PC.<br>"
            "<b>Subdirectory:</b> Relative path appended to network share base.<br>"
            "<b>Sample Name:</b> Used to identify and organize acquired images.<br><br>"
            "<b>Example Configuration:</b><br>"
            "• Network Share: <code>\\\\192.168.1.2\\CTLSM1</code><br>"
            "• Local Mount: <code>Z:\\</code> (if share is mapped to Z: drive)<br>"
            "• Subdirectory: <code>data/sample1</code><br>"
            "• Result: Microscope saves to <code>\\\\192.168.1.2\\CTLSM1\\data\\sample1</code><br><br>"
            "<b>⚠ Important:</b><br>"
            "• The microscope saves the data, so paths must be from its perspective<br>"
            "• Local Mount Point MUST be the actual network share (mapped or direct)<br>"
            "• Creating directories locally only works if mount point is correctly configured<br>"
            "• Directories created must be INSIDE the shared folder to be accessible"
        )
        info_text.setWordWrap(True)
        info_text.setStyleSheet("color: #555; font-size: 9pt; padding: 10px;")
        layout.addWidget(info_text)

        group.setLayout(layout)
        return group

    def _on_sample_name_changed(self, name: str):
        """
        Handle sample name change.

        Args:
            name: New sample name
        """
        self._sample_name = name
        if name:
            self.sample_name_display.setText(f"Current: {name}")
            self.sample_name_display.setStyleSheet(f"color: {SUCCESS_COLOR}; font-weight: bold;")
        else:
            self.sample_name_display.setText("Current: <not set>")
            self.sample_name_display.setStyleSheet("color: gray; font-style: italic;")

        self.sample_name_changed.emit(name)
        # Note: Don't log on every keystroke - logging done when used in actions

    def _on_network_share_changed(self, share_path: str):
        """
        Handle network share base path change.

        Args:
            share_path: New network share base path
        """
        self._network_share_base = share_path
        self._update_path_displays()
        # Note: Don't log on every keystroke - logging done when used in actions

    def _on_local_mount_changed(self, mount_path: str):
        """
        Handle local mount point change.

        Args:
            mount_path: New local mount point
        """
        self._local_mount_point = mount_path if mount_path else None
        self._update_path_displays()
        # Note: Don't log on every keystroke - logging done when used in actions

    def _on_save_path_changed(self, path: str):
        """
        Handle save path (subdirectory) change.

        Args:
            path: New subdirectory path
        """
        self._save_path = path
        self._update_path_displays()

        # Emit the full network path that will be sent to microscope
        full_network_path = self.get_network_path()
        self.save_path_changed.emit(full_network_path)
        # Note: Don't log on every keystroke - logging done when used in actions

    def _update_path_displays(self):
        """Update the path display labels with current configuration."""
        # Get full network path
        network_path = self.get_network_path()
        self.network_path_display.setText(f"📡 {network_path}")

        # Get local path if mount point configured
        if self._local_mount_point:
            local_path = self.get_local_path()
            if local_path:
                local_path_obj = Path(local_path)
                exists = local_path_obj.exists()
                status = "✓ exists" if exists else "⚠ does not exist"
                color = "green" if exists else "orange"
                self.local_path_display.setText(
                    f"Local equivalent: {local_path} ({status})"
                )
                self.local_path_display.setStyleSheet(f"color: {color}; font-size: 10pt;")
                self.create_dir_btn.setEnabled(True)
            else:
                self.local_path_display.setText("Local equivalent: <invalid configuration>")
                self.local_path_display.setStyleSheet(f"color: {ERROR_COLOR}; font-size: 10pt;")
                self.create_dir_btn.setEnabled(False)
        else:
            self.local_path_display.setText("Local equivalent: <no mount point configured>")
            self.local_path_display.setStyleSheet("color: gray; font-size: 10pt;")
            self.create_dir_btn.setEnabled(False)

    def _browse_local_mount(self):
        """Open file dialog to browse for local mount point."""
        current_path = self.local_mount_input.text() or str(Path.cwd())

        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Local Mount Point (where network share is mounted)",
            current_path,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )

        if directory:
            self.local_mount_input.setText(directory)
            self._logger.info(f"Local mount point selected: {directory}")

    def _browse_save_path(self):
        """Open file dialog to browse for subdirectory (only if local mount configured)."""
        if not self._local_mount_point:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                "Mount Point Required",
                "Configure the 'Local Mount Point' first to enable browsing.\n\n"
                "Alternatively, type the subdirectory path directly."
            )
            return

        # Start browsing from local mount point + current subdirectory
        start_path = Path(self._local_mount_point)
        if self._save_path:
            start_path = start_path / self._save_path

        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Data Subdirectory",
            str(start_path),
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )

        if directory:
            # Convert absolute path to relative path from mount point
            try:
                rel_path = Path(directory).relative_to(Path(self._local_mount_point))
                # Use forward slashes for consistency
                self.save_path_input.setText(str(rel_path).replace('\\', '/'))
                self._logger.info(f"Subdirectory selected: {rel_path}")
            except ValueError:
                # Selected directory is not under mount point
                from PyQt5.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self,
                    "Invalid Selection",
                    f"Selected directory must be under the mount point:\n{self._local_mount_point}"
                )

    def _create_directory(self):
        """Create the save directory locally (if mount point configured)."""
        if not self._local_mount_point:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Cannot Create Directory",
                "No local mount point configured.\n\n"
                "Configure the 'Local Mount Point' first, ensuring it points to "
                "where the network share is mounted on your PC."
            )
            return

        local_path = self.get_local_path()
        if not local_path:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Invalid Configuration",
                "Cannot determine local path. Check your configuration."
            )
            return

        # Confirmation dialog with warning
        from PyQt5.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Create Directory",
            f"This will create the directory locally at:\n{local_path}\n\n"
            f"⚠ IMPORTANT: This will only work if your 'Local Mount Point' "
            f"is configured as the network share location.\n\n"
            f"The microscope will access it via:\n{self.get_network_path()}\n\n"
            f"Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        try:
            path = Path(local_path)
            path.mkdir(parents=True, exist_ok=True)
            self._logger.info(f"Directory created: {path}")
            self._update_path_displays()

            # Show success message with reminder
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                "Directory Created",
                f"Directory created locally:\n{path}\n\n"
                f"Microscope will access via:\n{self.get_network_path()}\n\n"
                f"✓ If your mount point is configured correctly, the microscope "
                f"can now access this directory.\n\n"
                f"⚠ If microscope cannot access it, verify that your Local Mount Point "
                f"is actually the network share location."
            )

        except Exception as e:
            self._logger.error(f"Failed to create directory: {e}")
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to create directory:\n{str(e)}"
            )

    # Public methods for accessing current values

    def get_sample_name(self) -> str:
        """
        Get current sample name.

        Returns:
            Current sample name string
        """
        return self._sample_name

    def get_save_path(self) -> str:
        """
        Get current subdirectory path (relative).

        Returns:
            Current subdirectory path string
        """
        return self._save_path

    def get_network_path(self) -> str:
        """
        Get full network path that will be sent to microscope.

        This combines the network share base with the subdirectory.

        Returns:
            Full network path in UNC format (e.g., \\\\192.168.1.2\\CTLSM1\\data\\sample1)
        """
        if not self._network_share_base:
            return ""

        # Ensure we're using backslashes for UNC paths
        base = self._network_share_base.rstrip('\\/')

        if self._save_path:
            # Convert forward slashes to backslashes for Windows UNC paths
            subdir = self._save_path.strip('/\\').replace('/', '\\')
            return f"{base}\\{subdir}"
        else:
            return base

    def get_local_path(self) -> Optional[str]:
        """
        Get local path equivalent (if mount point configured).

        Returns:
            Local path string or None if no mount point configured
        """
        if not self._local_mount_point:
            return None

        base = Path(self._local_mount_point)

        if self._save_path:
            # Keep path separators consistent with OS
            return str(base / self._save_path)
        else:
            return str(base)

    def set_sample_name(self, name: str):
        """
        Set sample name programmatically.

        Args:
            name: Sample name to set
        """
        self.sample_name_input.setText(name)

    def set_save_path(self, path: str):
        """
        Set subdirectory path programmatically.

        Args:
            path: Subdirectory path to set
        """
        self.save_path_input.setText(path)

    def set_network_share_base(self, share_path: str):
        """
        Set network share base path programmatically.

        Args:
            share_path: Network share base path (UNC format)
        """
        self.network_share_input.setText(share_path)

    def set_local_mount_point(self, mount_path: str):
        """
        Set local mount point programmatically.

        Args:
            mount_path: Local mount point path
        """
        self.local_mount_input.setText(mount_path)
