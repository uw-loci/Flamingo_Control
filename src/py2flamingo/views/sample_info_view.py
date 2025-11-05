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
        self._save_path = str(Path.cwd() / "data")

        self._init_ui()

    def _init_ui(self):
        """Initialize user interface."""
        layout = QVBoxLayout(self)

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

    def _create_save_path_group(self) -> QGroupBox:
        """
        Create save path configuration group.

        Returns:
            QGroupBox with save path input and browse button
        """
        group = QGroupBox("Data Save Path")
        layout = QVBoxLayout()

        # Path input with browse button
        path_layout = QHBoxLayout()

        self.save_path_input = QLineEdit()
        self.save_path_input.setText(self._save_path)
        self.save_path_input.setPlaceholderText("Path to save acquired images")
        self.save_path_input.textChanged.connect(self._on_save_path_changed)
        path_layout.addWidget(self.save_path_input)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_save_path)
        path_layout.addWidget(browse_btn)

        layout.addLayout(path_layout)

        # Display absolute path
        self.abs_path_display = QLabel(f"Full path: {Path(self._save_path).absolute()}")
        self.abs_path_display.setStyleSheet("color: gray; font-size: 10pt;")
        self.abs_path_display.setWordWrap(True)
        layout.addWidget(self.abs_path_display)

        # Create directory button
        create_dir_layout = QHBoxLayout()
        self.create_dir_btn = QPushButton("Create Directory")
        self.create_dir_btn.clicked.connect(self._create_directory)
        create_dir_layout.addWidget(self.create_dir_btn)
        create_dir_layout.addStretch()
        layout.addLayout(create_dir_layout)

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
            "<b>Sample Name:</b> Used to identify and organize acquired images.<br>"
            "<b>Save Path:</b> Directory where image files will be saved.<br><br>"
            "Image files will be named using the pattern:<br>"
            "<i>&lt;save_path&gt;/&lt;sample_name&gt;_&lt;timestamp&gt;.png</i><br><br>"
            "<b>Note:</b> Make sure the save path exists and is writable before starting acquisition."
        )
        info_text.setWordWrap(True)
        info_text.setStyleSheet("color: #555; font-size: 10pt; padding: 10px;")
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
            self.sample_name_display.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.sample_name_display.setText("Current: <not set>")
            self.sample_name_display.setStyleSheet("color: gray; font-style: italic;")

        self.sample_name_changed.emit(name)
        self._logger.info(f"Sample name changed to: {name}")

    def _on_save_path_changed(self, path: str):
        """
        Handle save path change.

        Args:
            path: New save path
        """
        self._save_path = path
        abs_path = Path(path).absolute()
        self.abs_path_display.setText(f"Full path: {abs_path}")

        # Check if path exists
        if abs_path.exists():
            self.abs_path_display.setStyleSheet("color: green; font-size: 10pt;")
        else:
            self.abs_path_display.setStyleSheet("color: orange; font-size: 10pt;")

        self.save_path_changed.emit(path)
        self._logger.info(f"Save path changed to: {path}")

    def _browse_save_path(self):
        """Open file dialog to browse for save path."""
        current_path = self.save_path_input.text() or str(Path.cwd())

        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Data Save Directory",
            current_path,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )

        if directory:
            self.save_path_input.setText(directory)
            self._logger.info(f"Save path selected: {directory}")

    def _create_directory(self):
        """Create the save directory if it doesn't exist."""
        path = Path(self.save_path_input.text())

        try:
            path.mkdir(parents=True, exist_ok=True)
            self._logger.info(f"Directory created: {path}")
            self.abs_path_display.setText(f"Full path: {path.absolute()} ✓")
            self.abs_path_display.setStyleSheet("color: green; font-size: 10pt;")

            # Show success message briefly
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                "Success",
                f"Directory created successfully:\n{path.absolute()}"
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
        Get current save path.

        Returns:
            Current save path string
        """
        return self._save_path

    def set_sample_name(self, name: str):
        """
        Set sample name programmatically.

        Args:
            name: Sample name to set
        """
        self.sample_name_input.setText(name)

    def set_save_path(self, path: str):
        """
        Set save path programmatically.

        Args:
            path: Save path to set
        """
        self.save_path_input.setText(path)
