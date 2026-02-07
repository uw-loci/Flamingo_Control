"""Application Settings Dialog.

Dialog for configuring application-wide and microscope-specific settings
stored in JSON format. Settings include:
- Stage movement limits
- 3D visualization display settings
- File paths and directories
- Position history configuration
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any

from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QSpinBox, QDoubleSpinBox, QComboBox, QGroupBox, QGridLayout,
    QPushButton, QDialogButtonBox, QFileDialog, QTabWidget, QWidget,
    QMessageBox
)
from PyQt5.QtCore import Qt

from py2flamingo.services.window_geometry_manager import PersistentDialog
from py2flamingo.resources import get_app_icon


# Downsample factor presets (storage to display ratio)
DOWNSAMPLE_PRESETS = {
    "High Quality (2x)": 2,
    "Balanced (3x)": 3,
    "Performance (4x)": 4,
    "Fast (5x)": 5,
    "Very Fast (6x)": 6,
}


class SettingsDialog(PersistentDialog):
    """Dialog for editing application and microscope settings.

    Settings are organized into tabs:
    - Stage: Axis movement limits
    - Display: 3D visualization settings including downsample factor
    - Paths: File and directory paths
    - General: Position history and other settings

    Settings are loaded from and saved to the microscope-specific JSON file.
    """

    def __init__(self, settings_service=None, parent=None):
        """Initialize the settings dialog.

        Args:
            settings_service: MicroscopeSettingsService instance for load/save
            parent: Parent widget
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._settings_service = settings_service
        self._original_settings = {}

        self.setWindowTitle("Settings")
        self.setWindowIcon(get_app_icon())
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        self.setModal(True)

        self._setup_ui()
        self._load_current_settings()

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Tab widget for organizing settings
        self._tabs = QTabWidget()

        # Create tabs
        self._tabs.addTab(self._create_stage_tab(), "Stage Limits")
        self._tabs.addTab(self._create_display_tab(), "Display")
        self._tabs.addTab(self._create_paths_tab(), "Paths")
        self._tabs.addTab(self._create_general_tab(), "General")

        layout.addWidget(self._tabs)

        # Button row
        button_layout = QHBoxLayout()

        # Reset button on left
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self._reset_defaults)
        button_layout.addWidget(reset_btn)

        button_layout.addStretch()

        # Dialog buttons on right
        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        button_box.button(QDialogButtonBox.Apply).clicked.connect(self._apply_settings)
        button_layout.addWidget(button_box)

        layout.addLayout(button_layout)

    def _create_stage_tab(self) -> QWidget:
        """Create the Stage Limits settings tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # Stage limits group
        limits_group = QGroupBox("Axis Movement Limits")
        grid = QGridLayout()
        grid.setSpacing(8)

        # Headers
        grid.addWidget(QLabel("Axis"), 0, 0)
        min_header = QLabel("Minimum")
        min_header.setAlignment(Qt.AlignCenter)
        grid.addWidget(min_header, 0, 1)
        max_header = QLabel("Maximum")
        max_header.setAlignment(Qt.AlignCenter)
        grid.addWidget(max_header, 0, 2)
        grid.addWidget(QLabel("Unit"), 0, 3)

        # X axis
        grid.addWidget(QLabel("X:"), 1, 0)
        self._x_min = QDoubleSpinBox()
        self._x_min.setRange(-100, 100)
        self._x_min.setDecimals(2)
        self._x_min.setSuffix(" mm")
        grid.addWidget(self._x_min, 1, 1)

        self._x_max = QDoubleSpinBox()
        self._x_max.setRange(-100, 100)
        self._x_max.setDecimals(2)
        self._x_max.setSuffix(" mm")
        grid.addWidget(self._x_max, 1, 2)
        grid.addWidget(QLabel("mm"), 1, 3)

        # Y axis
        grid.addWidget(QLabel("Y:"), 2, 0)
        self._y_min = QDoubleSpinBox()
        self._y_min.setRange(-100, 100)
        self._y_min.setDecimals(2)
        self._y_min.setSuffix(" mm")
        grid.addWidget(self._y_min, 2, 1)

        self._y_max = QDoubleSpinBox()
        self._y_max.setRange(-100, 100)
        self._y_max.setDecimals(2)
        self._y_max.setSuffix(" mm")
        grid.addWidget(self._y_max, 2, 2)
        grid.addWidget(QLabel("mm"), 2, 3)

        # Z axis
        grid.addWidget(QLabel("Z:"), 3, 0)
        self._z_min = QDoubleSpinBox()
        self._z_min.setRange(-100, 100)
        self._z_min.setDecimals(2)
        self._z_min.setSuffix(" mm")
        grid.addWidget(self._z_min, 3, 1)

        self._z_max = QDoubleSpinBox()
        self._z_max.setRange(-100, 100)
        self._z_max.setDecimals(2)
        self._z_max.setSuffix(" mm")
        grid.addWidget(self._z_max, 3, 2)
        grid.addWidget(QLabel("mm"), 3, 3)

        # R axis (rotation)
        grid.addWidget(QLabel("R:"), 4, 0)
        self._r_min = QDoubleSpinBox()
        self._r_min.setRange(-1000, 1000)
        self._r_min.setDecimals(1)
        self._r_min.setSuffix(" °")
        grid.addWidget(self._r_min, 4, 1)

        self._r_max = QDoubleSpinBox()
        self._r_max.setRange(-1000, 1000)
        self._r_max.setDecimals(1)
        self._r_max.setSuffix(" °")
        grid.addWidget(self._r_max, 4, 2)
        grid.addWidget(QLabel("degrees"), 4, 3)

        # Info label
        info = QLabel(
            "Stage limits define the allowed range of motion for each axis. "
            "Values outside these limits will be rejected."
        )
        info.setStyleSheet("color: gray; font-size: 9pt;")
        info.setWordWrap(True)
        grid.addWidget(info, 5, 0, 1, 4)

        limits_group.setLayout(grid)
        layout.addWidget(limits_group)

        layout.addStretch()
        return tab

    def _create_display_tab(self) -> QWidget:
        """Create the Display settings tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # 3D Viewer group
        viewer_group = QGroupBox("3D Viewer Settings")
        grid = QGridLayout()
        grid.setSpacing(8)

        # Downsample factor
        grid.addWidget(QLabel("Display Resolution:"), 0, 0)
        self._downsample_preset = QComboBox()
        for preset_name in DOWNSAMPLE_PRESETS.keys():
            self._downsample_preset.addItem(preset_name)
        self._downsample_preset.setCurrentIndex(1)  # Default to "Balanced (3x)"
        self._downsample_preset.currentIndexChanged.connect(self._on_downsample_preset_changed)
        grid.addWidget(self._downsample_preset, 0, 1)

        # Custom downsample value
        grid.addWidget(QLabel("Downsample Factor:"), 1, 0)
        self._downsample_factor = QSpinBox()
        self._downsample_factor.setRange(1, 10)
        self._downsample_factor.setValue(3)
        self._downsample_factor.setSuffix("x")
        self._downsample_factor.setToolTip(
            "Ratio of display voxel size to storage voxel size.\n"
            "Higher values = faster rendering but lower quality."
        )
        self._downsample_factor.valueChanged.connect(self._on_downsample_value_changed)
        grid.addWidget(self._downsample_factor, 1, 1)

        # Calculated voxel sizes display
        self._voxel_info_label = QLabel("Storage: 5 µm → Display: 15 µm")
        self._voxel_info_label.setStyleSheet("color: #27ae60; font-weight: bold;")
        grid.addWidget(self._voxel_info_label, 2, 0, 1, 2)

        # Info
        downsample_info = QLabel(
            "Controls the resolution reduction between stored data and 3D display. "
            "Lower factors give higher quality but slower performance."
        )
        downsample_info.setStyleSheet("color: gray; font-size: 9pt;")
        downsample_info.setWordWrap(True)
        grid.addWidget(downsample_info, 3, 0, 1, 2)

        viewer_group.setLayout(grid)
        layout.addWidget(viewer_group)

        # Storage voxel size group
        storage_group = QGroupBox("Storage Resolution")
        storage_grid = QGridLayout()
        storage_grid.setSpacing(8)

        storage_grid.addWidget(QLabel("Storage Voxel Size:"), 0, 0)
        self._storage_voxel_size = QSpinBox()
        self._storage_voxel_size.setRange(1, 50)
        self._storage_voxel_size.setValue(5)
        self._storage_voxel_size.setSuffix(" µm")
        self._storage_voxel_size.setToolTip(
            "Size of voxels in the high-resolution storage array.\n"
            "Smaller values capture more detail but use more memory."
        )
        self._storage_voxel_size.valueChanged.connect(self._update_voxel_info)
        storage_grid.addWidget(self._storage_voxel_size, 0, 1)

        storage_info = QLabel(
            "The storage voxel size determines the finest detail captured. "
            "Requires restart to take effect."
        )
        storage_info.setStyleSheet("color: gray; font-size: 9pt;")
        storage_info.setWordWrap(True)
        storage_grid.addWidget(storage_info, 1, 0, 1, 2)

        storage_group.setLayout(storage_grid)
        layout.addWidget(storage_group)

        layout.addStretch()
        return tab

    def _create_paths_tab(self) -> QWidget:
        """Create the Paths settings tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # Output directory group
        output_group = QGroupBox("Output Directories")
        grid = QGridLayout()
        grid.setSpacing(8)

        # Default output directory
        grid.addWidget(QLabel("Default Output:"), 0, 0)
        self._output_dir = QLineEdit()
        self._output_dir.setPlaceholderText("Select default save location...")
        grid.addWidget(self._output_dir, 0, 1)

        browse_output = QPushButton("Browse...")
        browse_output.clicked.connect(lambda: self._browse_directory(self._output_dir))
        grid.addWidget(browse_output, 0, 2)

        # Workflows directory
        grid.addWidget(QLabel("Workflows:"), 1, 0)
        self._workflows_dir = QLineEdit()
        self._workflows_dir.setPlaceholderText("Workflow templates directory...")
        grid.addWidget(self._workflows_dir, 1, 1)

        browse_workflows = QPushButton("Browse...")
        browse_workflows.clicked.connect(lambda: self._browse_directory(self._workflows_dir))
        grid.addWidget(browse_workflows, 1, 2)

        # Sessions directory
        grid.addWidget(QLabel("Sessions:"), 2, 0)
        self._sessions_dir = QLineEdit()
        self._sessions_dir.setPlaceholderText("3D visualization sessions...")
        grid.addWidget(self._sessions_dir, 2, 1)

        browse_sessions = QPushButton("Browse...")
        browse_sessions.clicked.connect(lambda: self._browse_directory(self._sessions_dir))
        grid.addWidget(browse_sessions, 2, 2)

        # Info
        path_info = QLabel(
            "Set default directories for saving data, workflows, and visualization sessions."
        )
        path_info.setStyleSheet("color: gray; font-size: 9pt;")
        path_info.setWordWrap(True)
        grid.addWidget(path_info, 3, 0, 1, 3)

        output_group.setLayout(grid)
        layout.addWidget(output_group)

        layout.addStretch()
        return tab

    def _create_general_tab(self) -> QWidget:
        """Create the General settings tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # Position history group
        history_group = QGroupBox("Position History")
        grid = QGridLayout()
        grid.setSpacing(8)

        grid.addWidget(QLabel("Max Stored Positions:"), 0, 0)
        self._history_max = QSpinBox()
        self._history_max.setRange(10, 1000)
        self._history_max.setValue(100)
        self._history_max.setToolTip("Maximum number of positions to remember")
        grid.addWidget(self._history_max, 0, 1)

        grid.addWidget(QLabel("Display Count:"), 1, 0)
        self._history_display = QSpinBox()
        self._history_display.setRange(5, 100)
        self._history_display.setValue(20)
        self._history_display.setToolTip("Number of positions shown in history list")
        grid.addWidget(self._history_display, 1, 1)

        history_info = QLabel(
            "Configure how many stage positions are remembered and displayed."
        )
        history_info.setStyleSheet("color: gray; font-size: 9pt;")
        history_info.setWordWrap(True)
        grid.addWidget(history_info, 2, 0, 1, 2)

        history_group.setLayout(grid)
        layout.addWidget(history_group)

        # Microscope info (read-only)
        info_group = QGroupBox("Microscope Information")
        info_grid = QGridLayout()
        info_grid.setSpacing(8)

        info_grid.addWidget(QLabel("Microscope Name:"), 0, 0)
        self._microscope_name_label = QLabel("--")
        self._microscope_name_label.setStyleSheet("font-weight: bold;")
        info_grid.addWidget(self._microscope_name_label, 0, 1)

        info_grid.addWidget(QLabel("Settings File:"), 1, 0)
        self._settings_file_label = QLabel("--")
        self._settings_file_label.setStyleSheet("color: gray; font-size: 9pt;")
        self._settings_file_label.setWordWrap(True)
        info_grid.addWidget(self._settings_file_label, 1, 1)

        info_group.setLayout(info_grid)
        layout.addWidget(info_group)

        layout.addStretch()
        return tab

    def _browse_directory(self, line_edit: QLineEdit) -> None:
        """Open directory browser and set result to line edit."""
        current = line_edit.text() or str(Path.home())
        directory = QFileDialog.getExistingDirectory(
            self, "Select Directory", current
        )
        if directory:
            line_edit.setText(directory)

    def _on_downsample_preset_changed(self, index: int) -> None:
        """Handle downsample preset selection."""
        preset_name = self._downsample_preset.currentText()
        if preset_name in DOWNSAMPLE_PRESETS:
            factor = DOWNSAMPLE_PRESETS[preset_name]
            self._downsample_factor.blockSignals(True)
            self._downsample_factor.setValue(factor)
            self._downsample_factor.blockSignals(False)
            self._update_voxel_info()

    def _on_downsample_value_changed(self, value: int) -> None:
        """Handle custom downsample value change."""
        # Update preset combo to show "Custom" if value doesn't match any preset
        for name, factor in DOWNSAMPLE_PRESETS.items():
            if factor == value:
                self._downsample_preset.blockSignals(True)
                self._downsample_preset.setCurrentText(name)
                self._downsample_preset.blockSignals(False)
                break
        self._update_voxel_info()

    def _update_voxel_info(self) -> None:
        """Update the voxel size info label."""
        storage = self._storage_voxel_size.value()
        factor = self._downsample_factor.value()
        display = storage * factor
        self._voxel_info_label.setText(f"Storage: {storage} µm → Display: {display} µm")

    def _load_current_settings(self) -> None:
        """Load current settings from settings service."""
        if not self._settings_service:
            self._logger.warning("No settings service available")
            return

        try:
            # Store original settings for reset
            self._original_settings = self._settings_service.settings.copy()

            # Microscope info
            self._microscope_name_label.setText(
                self._settings_service.microscope_name or "--"
            )
            self._settings_file_label.setText(
                str(self._settings_service.settings_file)
            )

            # Stage limits
            limits = self._settings_service.get_stage_limits()
            self._x_min.setValue(limits['x']['min'])
            self._x_max.setValue(limits['x']['max'])
            self._y_min.setValue(limits['y']['min'])
            self._y_max.setValue(limits['y']['max'])
            self._z_min.setValue(limits['z']['min'])
            self._z_max.setValue(limits['z']['max'])
            self._r_min.setValue(limits['r']['min'])
            self._r_max.setValue(limits['r']['max'])

            # Position history
            self._history_max.setValue(
                self._settings_service.get_position_history_max_size()
            )
            self._history_display.setValue(
                self._settings_service.get_position_history_display_count()
            )

            # Display settings (new)
            display_settings = self._settings_service.get_setting("display", {})
            downsample = display_settings.get("downsample_factor", 3)
            storage_voxel = display_settings.get("storage_voxel_size_um", 5)

            self._downsample_factor.setValue(downsample)
            self._storage_voxel_size.setValue(storage_voxel)

            # Paths
            paths = self._settings_service.get_setting("paths", {})
            self._output_dir.setText(paths.get("output_dir", ""))
            self._workflows_dir.setText(paths.get("workflows_dir", ""))
            self._sessions_dir.setText(paths.get("sessions_dir", ""))

            self._update_voxel_info()
            self._logger.info("Settings loaded successfully")

        except Exception as e:
            self._logger.error(f"Error loading settings: {e}")

    def _apply_settings(self) -> None:
        """Apply current settings without closing dialog."""
        if not self._settings_service:
            QMessageBox.warning(
                self, "Warning",
                "No settings service available. Settings cannot be saved."
            )
            return

        try:
            # Stage limits
            self._settings_service.update_setting("stage_limits.x.min", self._x_min.value())
            self._settings_service.update_setting("stage_limits.x.max", self._x_max.value())
            self._settings_service.update_setting("stage_limits.y.min", self._y_min.value())
            self._settings_service.update_setting("stage_limits.y.max", self._y_max.value())
            self._settings_service.update_setting("stage_limits.z.min", self._z_min.value())
            self._settings_service.update_setting("stage_limits.z.max", self._z_max.value())
            self._settings_service.update_setting("stage_limits.r.min", self._r_min.value())
            self._settings_service.update_setting("stage_limits.r.max", self._r_max.value())

            # Position history
            self._settings_service.update_setting("position_history.max_size", self._history_max.value())
            self._settings_service.update_setting("position_history.display_count", self._history_display.value())

            # Display settings
            self._settings_service.update_setting("display.downsample_factor", self._downsample_factor.value())
            self._settings_service.update_setting("display.storage_voxel_size_um", self._storage_voxel_size.value())

            # Paths
            if self._output_dir.text():
                self._settings_service.update_setting("paths.output_dir", self._output_dir.text())
            if self._workflows_dir.text():
                self._settings_service.update_setting("paths.workflows_dir", self._workflows_dir.text())
            if self._sessions_dir.text():
                self._settings_service.update_setting("paths.sessions_dir", self._sessions_dir.text())

            # Update timestamp
            import time
            self._settings_service.update_setting("last_updated", time.strftime("%Y-%m-%d"))

            # Save to file
            self._settings_service.save_settings()

            self._logger.info("Settings applied and saved")
            QMessageBox.information(
                self, "Settings Saved",
                "Settings have been saved. Some changes may require restart to take effect."
            )

        except Exception as e:
            self._logger.error(f"Error applying settings: {e}")
            QMessageBox.critical(
                self, "Error",
                f"Failed to save settings: {e}"
            )

    def _on_accept(self) -> None:
        """Handle OK button - apply settings and close."""
        self._apply_settings()
        self.accept()

    def _reset_defaults(self) -> None:
        """Reset all settings to defaults."""
        reply = QMessageBox.question(
            self, "Reset Settings",
            "Reset all settings to defaults?\n\n"
            "This will restore factory defaults for the current microscope.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            # Stage limits - conservative defaults
            self._x_min.setValue(0.0)
            self._x_max.setValue(26.0)
            self._y_min.setValue(0.0)
            self._y_max.setValue(26.0)
            self._z_min.setValue(0.0)
            self._z_max.setValue(26.0)
            self._r_min.setValue(-720.0)
            self._r_max.setValue(720.0)

            # Display settings
            self._downsample_factor.setValue(3)
            self._storage_voxel_size.setValue(5)
            self._downsample_preset.setCurrentIndex(1)  # "Balanced (3x)"

            # Position history
            self._history_max.setValue(100)
            self._history_display.setValue(20)

            # Clear paths
            self._output_dir.clear()
            self._workflows_dir.clear()
            self._sessions_dir.clear()

            self._update_voxel_info()

    def get_settings(self) -> Dict[str, Any]:
        """Get current settings from dialog.

        Returns:
            Dictionary with all settings
        """
        return {
            'stage_limits': {
                'x': {'min': self._x_min.value(), 'max': self._x_max.value()},
                'y': {'min': self._y_min.value(), 'max': self._y_max.value()},
                'z': {'min': self._z_min.value(), 'max': self._z_max.value()},
                'r': {'min': self._r_min.value(), 'max': self._r_max.value()},
            },
            'display': {
                'downsample_factor': self._downsample_factor.value(),
                'storage_voxel_size_um': self._storage_voxel_size.value(),
            },
            'position_history': {
                'max_size': self._history_max.value(),
                'display_count': self._history_display.value(),
            },
            'paths': {
                'output_dir': self._output_dir.text(),
                'workflows_dir': self._workflows_dir.text(),
                'sessions_dir': self._sessions_dir.text(),
            },
        }

    def set_settings(self, settings: Dict[str, Any]) -> None:
        """Set dialog values from settings dictionary.

        Args:
            settings: Dictionary with settings to apply
        """
        if 'stage_limits' in settings:
            limits = settings['stage_limits']
            if 'x' in limits:
                self._x_min.setValue(limits['x'].get('min', 0))
                self._x_max.setValue(limits['x'].get('max', 26))
            if 'y' in limits:
                self._y_min.setValue(limits['y'].get('min', 0))
                self._y_max.setValue(limits['y'].get('max', 26))
            if 'z' in limits:
                self._z_min.setValue(limits['z'].get('min', 0))
                self._z_max.setValue(limits['z'].get('max', 26))
            if 'r' in limits:
                self._r_min.setValue(limits['r'].get('min', -720))
                self._r_max.setValue(limits['r'].get('max', 720))

        if 'display' in settings:
            display = settings['display']
            if 'downsample_factor' in display:
                self._downsample_factor.setValue(display['downsample_factor'])
            if 'storage_voxel_size_um' in display:
                self._storage_voxel_size.setValue(display['storage_voxel_size_um'])

        if 'position_history' in settings:
            history = settings['position_history']
            if 'max_size' in history:
                self._history_max.setValue(history['max_size'])
            if 'display_count' in history:
                self._history_display.setValue(history['display_count'])

        if 'paths' in settings:
            paths = settings['paths']
            if 'output_dir' in paths:
                self._output_dir.setText(paths['output_dir'])
            if 'workflows_dir' in paths:
                self._workflows_dir.setText(paths['workflows_dir'])
            if 'sessions_dir' in paths:
                self._sessions_dir.setText(paths['sessions_dir'])

        self._update_voxel_info()
