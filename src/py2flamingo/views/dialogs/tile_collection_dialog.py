"""Tile Collection Dialog.

Dialog for configuring and creating workflows for selected tiles
from the LED 2D Overview result window.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Dict

from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QComboBox, QScrollArea, QWidget,
    QMessageBox, QProgressDialog, QFrame, QCheckBox
)
from py2flamingo.services.window_geometry_manager import PersistentDialog
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QIcon

from py2flamingo.views.workflow_panels import (
    IlluminationPanel, ZStackPanel, SavePanel, CameraPanel
)
from py2flamingo.models.data.workflow import WorkflowType, Workflow, StackSettings
from py2flamingo.models.microscope import Position
from py2flamingo.services.tiff_size_validator import (
    validate_workflow_params, parse_workflow_file, get_recommended_planes,
    TiffSizeEstimate, TIFF_4GB_LIMIT
)
from py2flamingo.utils.tile_z_range import calculate_tile_z_ranges
from py2flamingo.utils.tile_workflow_parser import (
    parse_workflow_position, read_z_range_from_workflow,
    read_laser_channels_from_workflow, read_z_velocity_from_workflow
)
from py2flamingo.utils.tile_folder_organizer import reorganize_tile_folders

logger = logging.getLogger(__name__)


class TileCollectionDialog(PersistentDialog):
    """Dialog for creating workflows for selected tiles.

    Provides workflow configuration (illumination, Z-stack, save settings)
    without position inputs - positions come from selected tiles.
    """

    def __init__(self, left_tiles: List, right_tiles: List,
                 left_rotation: float, right_rotation: float,
                 config=None, app=None, parent=None,
                 local_base_folder: str = None):
        """Initialize the dialog.

        Args:
            left_tiles: List of TileResult from left panel
            right_tiles: List of TileResult from right panel
            left_rotation: Rotation angle for left panel tiles
            right_rotation: Rotation angle for right panel tiles
            config: ScanConfiguration with bounding box info
            app: FlamingoApplication instance for accessing services
            parent: Parent widget
            local_base_folder: Local drive root path for auto-configuring
                post-processing (e.g. from MIP Overview)
        """
        super().__init__(parent)

        self._left_tiles = left_tiles
        self._right_tiles = right_tiles
        self._left_rotation = left_rotation
        self._right_rotation = right_rotation
        self._config = config
        self._app = app
        self._local_base_folder_hint = local_base_folder
        self._workflow_type = WorkflowType.ZSTACK  # Default to Z-Stack (user preference)

        # Determine if 90-degree overlap mode is available
        self._has_dual_view = bool(left_tiles) and bool(right_tiles)
        self._primary_is_left = True  # Default: left panel is primary

        # Calculate Z ranges for tiles
        self._tile_z_ranges: Dict[Tuple[int, int], Tuple[float, float]] = {}
        self._update_z_ranges()

        self.setWindowTitle("Collect Tiles - Workflow Configuration")
        self.setMinimumWidth(550)
        self.setMinimumHeight(720)

        self._setup_ui()

        # Restore persisted settings (after UI setup)
        self._restore_dialog_state()

        # Auto-configure local access if hint provided (e.g. from MIP Overview)
        if self._local_base_folder_hint:
            self._auto_configure_local_access(self._local_base_folder_hint)

    def _auto_configure_local_access(self, local_base_folder: str) -> None:
        """Auto-configure local access for post-processing folder reorganization.

        Args:
            local_base_folder: Local drive root path (e.g. 'G:\\CTLSM1')
        """
        current_drive = self._save_panel._save_drive_combo.currentText()
        if not current_drive:
            logger.info("No save drive selected - skipping local access auto-config")
            return

        # Don't override if already configured
        config_service = None
        if self._app and hasattr(self._app, 'config_service'):
            config_service = self._app.config_service

        if config_service:
            existing = config_service.get_local_path_for_drive(current_drive)
            if existing:
                logger.info(f"Local access already configured for {current_drive}: {existing}")
                return

        # Configure the mapping and enable local access in the UI
        self._save_panel.enable_local_access(local_base_folder)
        logger.info(f"Auto-configured local access for {current_drive} -> {local_base_folder}")

    def _update_z_ranges(self) -> None:
        """Update Z ranges for tiles based on primary direction and overlap."""
        # Get fallback Z range from bounding box
        if self._config:
            fallback_z_min = self._config.bounding_box.z_min
            fallback_z_max = self._config.bounding_box.z_max
        else:
            fallback_z_min = 0.0
            fallback_z_max = 10.0

        # Determine primary and secondary tiles
        if self._primary_is_left:
            primary_tiles = self._left_tiles
            secondary_tiles = self._right_tiles
        else:
            primary_tiles = self._right_tiles
            secondary_tiles = self._left_tiles

        # Calculate Z ranges
        self._tile_z_ranges = calculate_tile_z_ranges(
            primary_tiles, secondary_tiles, fallback_z_min, fallback_z_max
        )

    def _get_z_range_for_tile(self, tile) -> Tuple[float, float]:
        """Get Z range for a specific tile.

        Args:
            tile: TileResult object

        Returns:
            Tuple of (z_min, z_max) in mm
        """
        key = (tile.tile_x_idx, tile.tile_y_idx)
        if key in self._tile_z_ranges:
            return self._tile_z_ranges[key]

        # Fallback to bounding box
        if self._config:
            return (self._config.bounding_box.z_min, self._config.bounding_box.z_max)
        return (0.0, 10.0)

    def _setup_ui(self):
        """Create the dialog UI."""
        layout = QVBoxLayout()

        # Scroll area for settings
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        container_layout = QVBoxLayout(container)

        # Summary section
        summary_group = self._create_summary_section()
        container_layout.addWidget(summary_group)

        # Primary direction section (only shown when both views have tiles)
        if self._has_dual_view:
            direction_group = self._create_direction_section()
            container_layout.addWidget(direction_group)

        # Workflow name section
        name_group = self._create_name_section()
        container_layout.addWidget(name_group)

        # Workflow type section
        type_group = self._create_type_section()
        container_layout.addWidget(type_group)

        # Illumination panel - pass app for instrument laser configuration
        self._illumination_panel = IlluminationPanel(app=self._app)
        container_layout.addWidget(self._illumination_panel)

        # Camera panel for exposure/frame rate settings - pass app for auto-detection
        self._camera_panel = CameraPanel(app=self._app)
        self._camera_panel.settings_changed.connect(self._on_camera_settings_changed)
        container_layout.addWidget(self._camera_panel)

        # Z-Stack panel - pass app for system defaults
        # Default to visible since we default to Z-Stack mode
        self._zstack_panel = ZStackPanel(app=self._app)
        self._zstack_panel.setVisible(True)  # Default visible for Z-Stack
        self._zstack_panel.enable_tile_mode(True)  # Enable tile mode
        container_layout.addWidget(self._zstack_panel)

        # Initialize Z range for Z-Stack mode
        z_min, z_max = self._get_representative_z_range()
        self._zstack_panel.set_z_range(z_min, z_max)

        # Initialize Z velocity with current frame rate
        camera_settings = self._camera_panel.get_settings()
        self._zstack_panel.set_frame_rate(camera_settings['frame_rate'])

        # Save panel - pass app for system storage location and connection_service for drive refresh
        # Only pass connection_service if it has query_available_drives method
        connection_service = getattr(self._app, 'connection_service', None) if self._app else None
        if connection_service and not hasattr(connection_service, 'query_available_drives'):
            logger.warning("Connection service lacks query_available_drives method - disabling drive refresh")
            connection_service = None
        self._save_panel = SavePanel(app=self._app, connection_service=connection_service)
        container_layout.addWidget(self._save_panel)

        # Sample View Integration checkbox
        self._add_to_sample_view_checkbox = QCheckBox("Add Z-stacks to Sample View (live)")
        self._add_to_sample_view_checkbox.setToolTip(
            "If checked, Z-stack frames will be added to Sample View 3D\n"
            "visualization in real-time as each tile workflow executes.\n"
            "Requires Sample View window to be open."
        )
        self._add_to_sample_view_checkbox.setChecked(True)  # Default enabled
        container_layout.addWidget(self._add_to_sample_view_checkbox)

        container_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, stretch=1)

        # Button row
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self._create_btn = QPushButton("Create Workflows")
        self._create_btn.setMinimumHeight(40)
        self._create_btn.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                font-weight: bold;
                padding: 8px 24px;
            }
            QPushButton:hover {
                background-color: #2ecc71;
            }
        """)
        self._create_btn.clicked.connect(self._on_create_workflows)
        button_layout.addWidget(self._create_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumHeight(40)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def _create_summary_section(self) -> QGroupBox:
        """Create the selected tiles summary section."""
        group = QGroupBox("Selected Tiles")
        layout = QVBoxLayout()

        total = len(self._left_tiles) + len(self._right_tiles)

        summary_text = f"Total tiles selected: {total}\n"
        if self._left_tiles:
            summary_text += f"  - Left panel (R={self._left_rotation}°): {len(self._left_tiles)} tiles\n"
        if self._right_tiles:
            summary_text += f"  - Right panel (R={self._right_rotation}°): {len(self._right_tiles)} tiles\n"

        if self._config:
            bbox = self._config.bounding_box
            summary_text += f"\nBounding box Z range: {bbox.z_min:.2f} to {bbox.z_max:.2f} mm"

        # Show overlap Z range info if both views have tiles
        if self._has_dual_view:
            # Calculate Z range from current settings
            if self._tile_z_ranges:
                z_values = [(z_min, z_max) for z_min, z_max in self._tile_z_ranges.values()]
                if z_values:
                    global_z_min = min(z[0] for z in z_values)
                    global_z_max = max(z[1] for z in z_values)
                    summary_text += f"\n\n90° overlap Z range: {global_z_min:.2f} to {global_z_max:.2f} mm"

        self._summary_label = QLabel(summary_text)
        self._summary_label.setStyleSheet("color: #666;")
        layout.addWidget(self._summary_label)

        group.setLayout(layout)
        return group

    def _create_direction_section(self) -> QGroupBox:
        """Create the primary direction selection section.

        This section allows the user to choose which view (0° or 90°)
        should be the primary direction for Z-stack workflows.
        """
        group = QGroupBox("Primary Direction (90° Overlap Mode)")
        layout = QVBoxLayout()

        # Description
        desc = QLabel(
            "Select the primary view direction. Z-stacks will be taken at "
            "tile positions from the primary view. The Z range for each stack "
            "is determined by the overlap with the secondary view."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666; font-size: 11px; margin-bottom: 8px;")
        layout.addWidget(desc)

        # Radio-like combo for direction selection
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Primary view:"))

        self._direction_combo = QComboBox()
        self._direction_combo.addItem(f"Left panel (R={self._left_rotation}°)", "left")
        self._direction_combo.addItem(f"Right panel (R={self._right_rotation}°)", "right")
        self._direction_combo.currentIndexChanged.connect(self._on_direction_changed)
        dir_layout.addWidget(self._direction_combo)
        dir_layout.addStretch()

        layout.addLayout(dir_layout)

        # Z range info label
        self._z_range_info = QLabel()
        self._z_range_info.setStyleSheet("color: #27ae60; font-weight: bold;")
        self._update_z_range_info()
        layout.addWidget(self._z_range_info)

        group.setLayout(layout)
        return group

    def _on_direction_changed(self, index: int) -> None:
        """Handle primary direction change."""
        self._primary_is_left = (self._direction_combo.currentData() == "left")
        self._update_z_ranges()
        self._update_z_range_info()
        self._update_summary_label()

    def _update_z_range_info(self) -> None:
        """Update the Z range info label."""
        if not hasattr(self, '_z_range_info'):
            return

        if self._primary_is_left:
            primary_count = len(self._left_tiles)
            primary_angle = self._left_rotation
            secondary_count = len(self._right_tiles)
            secondary_angle = self._right_rotation
        else:
            primary_count = len(self._right_tiles)
            primary_angle = self._right_rotation
            secondary_count = len(self._left_tiles)
            secondary_angle = self._left_rotation

        if self._tile_z_ranges:
            z_values = list(self._tile_z_ranges.values())
            if z_values:
                z_min = z_values[0][0]
                z_max = z_values[0][1]
                z_range = z_max - z_min
                self._z_range_info.setText(
                    f"{primary_count} Z-stacks at R={primary_angle}°, "
                    f"Z range from {secondary_count} tiles at R={secondary_angle}°: "
                    f"{z_min:.2f} to {z_max:.2f} mm ({z_range:.2f} mm)"
                )

    def _update_summary_label(self) -> None:
        """Update the summary label with current Z range info."""
        if not hasattr(self, '_summary_label'):
            return

        total = len(self._left_tiles) + len(self._right_tiles)

        summary_text = f"Total tiles selected: {total}\n"
        if self._left_tiles:
            summary_text += f"  - Left panel (R={self._left_rotation}°): {len(self._left_tiles)} tiles\n"
        if self._right_tiles:
            summary_text += f"  - Right panel (R={self._right_rotation}°): {len(self._right_tiles)} tiles\n"

        if self._config:
            bbox = self._config.bounding_box
            summary_text += f"\nBounding box Z range: {bbox.z_min:.2f} to {bbox.z_max:.2f} mm"

        if self._has_dual_view and self._tile_z_ranges:
            z_values = [(z_min, z_max) for z_min, z_max in self._tile_z_ranges.values()]
            if z_values:
                global_z_min = min(z[0] for z in z_values)
                global_z_max = max(z[1] for z in z_values)
                summary_text += f"\n\n90° overlap Z range: {global_z_min:.2f} to {global_z_max:.2f} mm"

        self._summary_label.setText(summary_text)

    def _create_name_section(self) -> QGroupBox:
        """Create the workflow name section."""
        group = QGroupBox("Workflow Name")
        layout = QHBoxLayout()

        layout.addWidget(QLabel("Name prefix:"))
        self._name_prefix = QLineEdit()
        self._name_prefix.setPlaceholderText("e.g., Sample1_scan")
        self._name_prefix.setText("tile_collection")
        layout.addWidget(self._name_prefix, stretch=1)

        group.setLayout(layout)
        return group

    def _create_type_section(self) -> QGroupBox:
        """Create the workflow type selection section."""
        group = QGroupBox("Workflow Type")
        layout = QHBoxLayout()

        self._type_combo = QComboBox()
        self._type_combo.addItems(["Snapshot", "Z-Stack"])
        self._type_combo.setCurrentIndex(1)  # Default to Z-Stack
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        layout.addWidget(self._type_combo)

        # Description will be updated by _on_type_changed
        self._type_description = QLabel("Z-stack at each tile position")
        self._type_description.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(self._type_description)

        layout.addStretch()
        group.setLayout(layout)
        return group

    def _on_type_changed(self, index: int):
        """Handle workflow type change."""
        if index == 0:
            self._workflow_type = WorkflowType.SNAPSHOT
            self._type_description.setText("Single image at each tile position")
            self._zstack_panel.setVisible(False)
            self._zstack_panel.enable_tile_mode(False)
        else:
            self._workflow_type = WorkflowType.ZSTACK

            # Enable tile mode and set Z range from tiles
            self._zstack_panel.enable_tile_mode(True)
            z_min, z_max = self._get_representative_z_range()
            self._zstack_panel.set_z_range(z_min, z_max)

            # Update description with Z range info
            z_range_mm = z_max - z_min
            if self._has_dual_view:
                desc = f"Z-stack using 90° overlap Z range ({z_range_mm*1000:.0f} µm)"
            else:
                desc = f"Z-stack using bounding box Z range ({z_range_mm*1000:.0f} µm)"
            self._type_description.setText(desc)
            self._zstack_panel.setVisible(True)

    def _get_representative_z_range(self) -> Tuple[float, float]:
        """Get representative Z range from all tiles.

        Uses the maximum Z range across all tiles since that determines
        the maximum number of planes needed for complete coverage.

        Returns:
            Tuple of (z_min, z_max) in mm
        """
        if not self._tile_z_ranges:
            # Fallback to bounding box
            if self._config:
                return (self._config.bounding_box.z_min, self._config.bounding_box.z_max)
            return (0.0, 10.0)

        # Find the largest Z range (for UI display)
        # Each tile workflow will use its specific Z range
        max_range = 0.0
        best_z_min, best_z_max = 0.0, 0.0

        for (z_min, z_max) in self._tile_z_ranges.values():
            z_range = z_max - z_min
            if z_range > max_range:
                max_range = z_range
                best_z_min, best_z_max = z_min, z_max

        return (best_z_min, best_z_max)

    def _on_camera_settings_changed(self, settings: dict):
        """Handle camera settings change - update Z velocity calculation."""
        frame_rate = settings.get('frame_rate', 100.0)
        self._zstack_panel.set_frame_rate(frame_rate)

    def _on_create_workflows(self):
        """Create and execute workflows for selected tiles."""
        name_prefix = self._name_prefix.text().strip()
        if not name_prefix:
            QMessageBox.warning(self, "Missing Name", "Please enter a workflow name prefix.")
            return

        # Validate illumination - get_settings() returns a list of IlluminationSettings
        illumination_list = self._illumination_panel.get_settings()
        if not illumination_list:
            QMessageBox.warning(self, "No Illumination", "Please enable at least one light source.")
            return

        # Get save settings
        save_settings = self._save_panel.get_settings()

        # Save workflow files to the project's "workflows" directory
        # The save_drive in workflow content tells the SERVER where to save images
        # But the workflow files themselves must be local so Python can read and send them
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Find project root (where the workflows directory should be)
        project_root = Path(__file__).parent.parent.parent.parent.parent  # Up from views/dialogs to project root
        workflow_folder = project_root / "workflows" / f"{save_settings['save_directory']}_{timestamp}"

        try:
            workflow_folder.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created local workflow folder: {workflow_folder}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create workflow folder:\n{e}")
            return

        # Collect tiles based on mode:
        # - If dual view (90° overlap): use only primary tiles with calculated Z ranges
        # - If single view: use all tiles from that view with bounding box Z range
        tiles_to_process = []

        if self._has_dual_view:
            # 90-degree overlap mode: use only primary view tiles
            if self._primary_is_left:
                primary_tiles = self._left_tiles
                primary_rotation = self._left_rotation
            else:
                primary_tiles = self._right_tiles
                primary_rotation = self._right_rotation

            for tile in primary_tiles:
                z_min, z_max = self._get_z_range_for_tile(tile)
                tiles_to_process.append((tile, primary_rotation, z_min, z_max))

            logger.info(f"90° overlap mode: {len(primary_tiles)} primary tiles at R={primary_rotation}°")
        else:
            # Single view mode: use per-tile Z range if available, else bounding box
            bbox_z_min = self._config.bounding_box.z_min if self._config else 0.0
            bbox_z_max = self._config.bounding_box.z_max if self._config else 10.0

            for tile in self._left_tiles:
                z_min = tile.z_stack_min if tile.z_stack_min != tile.z_stack_max else bbox_z_min
                z_max = tile.z_stack_max if tile.z_stack_min != tile.z_stack_max else bbox_z_max
                tiles_to_process.append((tile, self._left_rotation, z_min, z_max))
            for tile in self._right_tiles:
                z_min = tile.z_stack_min if tile.z_stack_min != tile.z_stack_max else bbox_z_min
                z_max = tile.z_stack_max if tile.z_stack_min != tile.z_stack_max else bbox_z_max
                tiles_to_process.append((tile, self._right_rotation, z_min, z_max))

        total = len(tiles_to_process)
        if total == 0:
            QMessageBox.warning(self, "No Tiles", "No tiles selected.")
            return

        # Create progress dialog
        progress = QProgressDialog("Creating workflows...", "Cancel", 0, total, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        # Create workflows with per-tile save directories
        # Use FLATTENED directory names for server compatibility (single directory level)
        # Format: base_date_tile (e.g., Test_2026-01-27_X11.09_Y14.46)
        # Post-collection reorganization will move to nested structure if local access available
        base_save_directory = save_settings['save_directory']
        date_folder = datetime.now().strftime("%Y-%m-%d")

        # Track folders for post-collection reorganization
        # Maps flattened_name -> (date_folder, tile_folder) for later reorganization
        self._tile_folder_mapping: Dict[str, Tuple[str, str]] = {}
        self._base_save_directory = base_save_directory
        self._save_drive = save_settings['save_drive']
        # Get local path directly from save settings (configured via Browse button)
        self._local_path = save_settings.get('local_path')
        self._local_access_enabled = save_settings.get('local_access_enabled', False)

        created_files = []
        for i, (tile, rotation, z_min, z_max) in enumerate(tiles_to_process):
            if progress.wasCanceled():
                break

            progress.setValue(i)
            progress.setLabelText(f"Creating workflow {i+1}/{total}...")

            # Create workflow name
            workflow_name = f"{name_prefix}_R{rotation:.0f}_X{tile.x:.2f}_Y{tile.y:.2f}"

            # Create per-tile save directory using FLATTENED format for server compatibility
            # Server can only create single-level directories, so use underscores instead of slashes
            tile_folder = f"X{tile.x:.2f}_Y{tile.y:.2f}"
            # Flattened format: base_date_tile (no slashes!)
            tile_save_directory = f"{base_save_directory}_{date_folder}_{tile_folder}"

            # Track for post-collection reorganization
            self._tile_folder_mapping[tile_save_directory] = (date_folder, tile_folder)

            # Create a copy of save_settings with the tile-specific directory
            tile_save_settings = save_settings.copy()
            tile_save_settings['save_directory'] = tile_save_directory

            # Create position
            position = Position(x=tile.x, y=tile.y, z=tile.z, r=rotation)

            # Build workflow text with per-tile Z range and per-tile save directory
            workflow_text = self._build_workflow_text(
                workflow_name, position, illumination_list, tile_save_settings, z_min, z_max
            )

            # Save to file
            workflow_file = workflow_folder / f"{workflow_name}.txt"
            try:
                with open(workflow_file, 'w') as f:
                    f.write(workflow_text)
                created_files.append(workflow_file)
                logger.info(f"Created workflow: {workflow_file.name}")
            except Exception as e:
                logger.error(f"Failed to save workflow {workflow_name}: {e}")

        progress.setValue(total)

        # Report results
        if created_files:
            # Validate TIFF size before execution
            tiff_warning = self._validate_tiff_size(created_files)

            if tiff_warning:
                # Show warning with detailed information
                warning_result = QMessageBox.warning(
                    self, "TIFF File Size Warning",
                    tiff_warning + "\n\nDo you want to proceed anyway?",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Help,
                    QMessageBox.No
                )

                if warning_result == QMessageBox.Help:
                    # Show detailed help
                    QMessageBox.information(
                        self, "TIFF 4GB Limit Explained",
                        "Standard TIFF format uses 32-bit file offsets, which limits "
                        "files to 4GB (4,294,967,296 bytes).\n\n"
                        "When acquiring large Z-stacks, the server writes images to a "
                        "single TIFF file. If this file exceeds 4GB, the write operation "
                        "fails and the acquisition is aborted.\n\n"
                        "Solutions:\n"
                        "1. Reduce the Z range to keep each file under 4GB\n"
                        "2. Increase the Z step size (fewer planes)\n"
                        "3. Use camera binning to reduce image size\n\n"
                        "For 2048x2048 16-bit images, the maximum safe number of planes "
                        "is approximately 500 per acquisition."
                    )
                    # Ask again after showing help
                    warning_result = QMessageBox.warning(
                        self, "TIFF File Size Warning",
                        tiff_warning + "\n\nDo you want to proceed anyway?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No
                    )

                if warning_result != QMessageBox.Yes:
                    logger.info("User cancelled workflow execution due to TIFF size warning - returning to dialog")
                    # Don't close the dialog - let user adjust settings and try again
                    # The workflow files were created but we return to let user modify parameters
                    return

            msg = f"Created {len(created_files)} workflow files in:\n{workflow_folder}\n\n"
            msg += f"Images will be saved to:\n{save_settings['save_drive']}/{base_save_directory}_{date_folder}_X_Y/\n"
            msg += "(Flattened structure for server compatibility)\n\n"
            msg += "Would you like to execute them now?"

            result = QMessageBox.question(
                self, "Workflows Created", msg,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )

            if result == QMessageBox.Yes:
                self._execute_workflows(created_files)

        self.accept()

    def _validate_tiff_size(self, workflow_files: List[Path]) -> Optional[str]:
        """Validate TIFF file size for workflow files.

        Checks if the workflow parameters would produce TIFF files
        that exceed the 4GB standard TIFF limit.

        Only applies to standard TIFF format. BigTIFF and Raw formats
        don't have this limitation.

        Args:
            workflow_files: List of workflow file paths to validate

        Returns:
            Warning message if size exceeds limit, None if OK
        """
        if not workflow_files:
            return None

        # Check save format - only standard TIFF has 4GB limit
        save_settings = self._save_panel.get_settings()
        save_format = save_settings.get('save_format', 'Tiff')
        if save_format != 'Tiff':
            # BigTiff, Raw, and NotSaved don't have the 4GB limit
            logger.debug(f"Skipping TIFF size validation - format is {save_format}")
            return None

        # Parse the first workflow file to get parameters
        estimate = parse_workflow_file(workflow_files[0])
        if estimate is None:
            # Couldn't parse - fall back to current panel settings
            camera_settings = self._camera_panel.get_settings()
            stack_settings = self._zstack_panel.get_settings()

            # Get Z range from panel
            z_range_mm = stack_settings.get('z_range_mm', 4.0)
            z_step_um = stack_settings.get('z_step_um', 2.5)

            estimate = validate_workflow_params(
                z_range_mm=z_range_mm,
                z_step_um=z_step_um,
                image_width=camera_settings.get('aoi_width', 2048),
                image_height=camera_settings.get('aoi_height', 2048),
                bytes_per_pixel=2
            )

        if estimate.exceeds_limit:
            logger.warning(
                f"TIFF size warning: {estimate.num_planes} planes = "
                f"{estimate.estimated_gb:.2f} GB (exceeds 4GB limit)"
            )

            # Get recommended settings
            camera_settings = self._camera_panel.get_settings()
            max_planes, min_step_um = get_recommended_planes(
                z_range_mm=abs(estimate.num_planes * 0.0025),  # Estimate from num_planes
                image_width=camera_settings.get('aoi_width', 2048),
                image_height=camera_settings.get('aoi_height', 2048)
            )

            warning_msg = (
                f"TIFF FILE SIZE LIMIT WARNING\n\n"
                f"Your workflow will create TIFF files of approximately "
                f"{estimate.estimated_gb:.2f} GB, which exceeds the 4GB limit.\n\n"
                f"Current settings:\n"
                f"  - Number of planes: {estimate.num_planes:,}\n"
                f"  - Image size: {estimate.image_width}x{estimate.image_height}\n"
                f"  - Estimated size: {estimate.estimated_gb:.2f} GB\n\n"
                f"The acquisition will FAIL after approximately {estimate.max_safe_planes:,} planes.\n\n"
                f"Recommendation: Reduce to ≤{estimate.max_safe_planes:,} planes or split into "
                f"multiple smaller acquisitions."
            )
            return warning_msg

        return None

    def _build_workflow_text(self, name: str, position: Position,
                             illumination_list: List, save_settings: dict,
                             z_min: float, z_max: float) -> str:
        """Build workflow file text content.

        Args:
            name: Workflow name
            position: Start position
            illumination_list: List of IlluminationSettings for enabled sources
            save_settings: Save settings dict
            z_min: Minimum Z for Z-stack
            z_max: Maximum Z for Z-stack

        Returns:
            Workflow file content as string
        """
        lines = ["<Workflow Settings>"]

        # Get camera settings
        camera_settings = self._camera_panel.get_settings()
        exposure_us = camera_settings['exposure_us']
        frame_rate = camera_settings['frame_rate']

        # Experiment Settings - 2 spaces for section tags, 4 spaces for fields
        lines.append("  <Experiment Settings>")

        stack = self._zstack_panel.get_settings() if self._workflow_type == WorkflowType.ZSTACK else None
        plane_spacing = stack.z_step_um if stack else 1.0

        lines.append(f"    Plane spacing (um) = {plane_spacing}")
        lines.append(f"    Frame rate (f/s) = {frame_rate:.6f}")
        # Exposure with comma formatting like "9,002"
        lines.append(f"    Exposure time (us) = {int(exposure_us):,}")
        lines.append("    Duration (dd:hh:mm:ss) = 00:00:00:00")
        lines.append("    Interval (dd:hh:mm:ss) = 00:00:00:00")
        lines.append(f"    Sample = {name}")
        lines.append("    Number of angles = ")
        lines.append("    Angle step size = ")
        lines.append("    Region = ")
        lines.append(f"    Save image drive = {save_settings['save_drive']}")
        lines.append(f"    Save image directory = {save_settings['save_directory']}")
        lines.append("    Comments = Tile collection workflow")
        lines.append(f"    Save max projection = {'true' if save_settings['save_mip'] else 'false'}")
        lines.append(f"    Display max projection = {'true' if save_settings['display_mip'] else 'false'}")
        lines.append(f"    Save image data = {save_settings['save_format'] if save_settings['save_enabled'] else 'NotSaved'}")
        lines.append("    Save to subfolders = false")
        lines.append(f"    Work flow live view enabled = {'true' if save_settings['live_view'] else 'false'}")
        lines.append("  </Experiment Settings>")
        # Camera Settings
        lines.append("  <Camera Settings>")
        lines.append("    Exposure time (us) = ")
        lines.append("    Frame rate (f/s) = ")
        lines.append(f"    AOI width = {camera_settings['aoi_width']}")
        lines.append(f"    AOI height = {camera_settings['aoi_height']}")
        lines.append("  </Camera Settings>")
        # Stack Settings
        lines.append("  <Stack Settings>")
        lines.append("    Stack index = ")

        if self._workflow_type == WorkflowType.ZSTACK and stack:
            # Use full Z range from bounding box
            z_range_mm = z_max - z_min
            # Calculate number of planes from Z range and step size
            num_planes = max(1, int(z_range_mm / (stack.z_step_um / 1000.0)) + 1)
            lines.append(f"    Change in Z axis (mm) = {z_range_mm:.3f}")
            lines.append(f"    Number of planes = {num_planes}")
        else:
            lines.append("    Change in Z axis (mm) = 0.01")
            lines.append("    Number of planes = 1")

        lines.append("    Number of planes saved = ")
        if self._workflow_type == WorkflowType.ZSTACK and stack:
            lines.append(f"    Z stage velocity (mm/s) = {stack.z_velocity_mm_s:.6f}")
        else:
            lines.append("    Z stage velocity (mm/s) = 0.1")
        lines.append("    Rotational stage velocity (°/s) = 0")
        lines.append("    Auto update stack calculations = true")
        lines.append("    Date time stamp = ")
        lines.append("    Stack file name = ")
        lines.append(f"    Camera 1 capture percentage = {camera_settings['cam1_capture_percentage']}")
        lines.append(f"    Camera 1 capture mode (0 full, 1 from front, 2 from back, 3 none) = {camera_settings['cam1_capture_mode']}")
        lines.append("    Camera 1 capture range = ")
        lines.append(f"    Camera 2 capture percentage = {camera_settings['cam2_capture_percentage']}")
        lines.append(f"    Camera 2 capture mode (0 full, 1 from front, 2 from back, 3 none) = {camera_settings['cam2_capture_mode']}")
        lines.append("    Camera 2 capture range = ")
        # Stack option determines workflow type - ZStack for z-stack, Tile for tiling
        stack_option = "ZStack" if self._workflow_type == WorkflowType.ZSTACK else "Snapshot"
        lines.append(f"    Stack option = {stack_option}")
        lines.append("    Stack option settings 1 = ")
        lines.append("    Stack option settings 2 = ")
        lines.append("  </Stack Settings>")
        # Start Position
        lines.append("  <Start Position>")
        start_z = z_min if self._workflow_type == WorkflowType.ZSTACK else position.z
        lines.append(f"    X (mm) = {position.x:.3f}")
        lines.append(f"    Y (mm) = {position.y:.3f}")
        lines.append(f"    Z (mm) = {start_z:.3f}")
        lines.append(f"    Angle (degrees) = {position.r:.3f}")
        lines.append("  </Start Position>")
        # End Position
        lines.append("  <End Position>")
        end_z = z_max if self._workflow_type == WorkflowType.ZSTACK else position.z
        lines.append(f"    X (mm) = {position.x:.3f}")
        lines.append(f"    Y (mm) = {position.y:.3f}")
        lines.append(f"    Z (mm) = {end_z:.3f}")
        lines.append(f"    Angle (degrees) = {position.r:.3f}")
        lines.append("  </End Position>")
        # Illumination Source - list ALL lasers in exact format server expects
        # Format: "Laser N N: XXX nm MLE = power enabled"
        lines.append("  <Illumination Source>")

        # Build dict of enabled lasers from illumination_list
        enabled_lasers = {}
        led_settings = None
        for illum in illumination_list:
            if illum.laser_enabled and illum.laser_channel:
                # Extract laser number from channel name like "Laser 4 640 nm"
                enabled_lasers[illum.laser_channel] = illum.laser_power_mw
            if illum.led_enabled:
                led_settings = illum

        # List all 7 laser slots in exact format (even disabled ones)
        laser_configs = [
            (1, "405 nm"),
            (2, "488 nm"),
            (3, "561 nm"),
            (4, "640 nm"),
            (5, None),  # Empty slot
            (6, None),  # Empty slot
            (7, None),  # Empty slot
        ]

        for laser_num, wavelength in laser_configs:
            if wavelength:
                # Check if this laser is enabled
                channel_key = f"Laser {laser_num} {wavelength}"
                if channel_key in enabled_lasers:
                    power = enabled_lasers[channel_key]
                    enabled = 1
                else:
                    power = 0.0
                    enabled = 0
                lines.append(f"    Laser {laser_num} {laser_num}: {wavelength} MLE = {power:.2f} {enabled}")
            else:
                # Empty laser slot
                lines.append(f"    Laser {laser_num} = 0.00 0")

        # LED settings
        if led_settings:
            lines.append(f"    LED_RGB_Board = {led_settings.led_intensity_percent:.2f} 1")
            lines.append("    LED selection = 0 0")
            lines.append("    LED DAC = 42000 0")
        else:
            lines.append("    LED_RGB_Board = 0.00 0")
            lines.append("    LED selection = 0 0")
            lines.append("    LED DAC = 42000 0")
        lines.append("  </Illumination Source>")
        # Illumination Path - format is "path = ON/OFF value" where value is 1 for ON, 0 for OFF
        lines.append("  <Illumination Path>")
        # Get illumination path settings from panel's UI state (not get_settings which returns a list)
        illum_ui_state = self._illumination_panel.get_ui_state()
        left_on = illum_ui_state.get('left_path', True)
        right_on = illum_ui_state.get('right_path', False)
        lines.append(f"    Left path = {'ON' if left_on else 'OFF'} {1 if left_on else 0}")
        lines.append(f"    Right path = {'ON' if right_on else 'OFF'} {1 if right_on else 0}")
        lines.append("  </Illumination Path>")
        # Illumination Options
        lines.append("  <Illumination Options>")
        multi_laser = len(enabled_lasers) > 1
        lines.append(f"    Run stack with multiple lasers on = {'true' if multi_laser else 'false'}")
        lines.append("  </Illumination Options>")
        lines.append("</Workflow Settings>")

        # Join with LF line endings (server uses Unix line endings)
        return "\n".join(lines)

    def _get_sample_view_instance(self):
        """Get Sample View instance from application.

        Returns:
            Sample View instance if available, None otherwise
        """
        if self._app and hasattr(self._app, 'sample_view'):
            return self._app.sample_view
        return None

    def _setup_sample_view_integration(self, workflow_files: List[Path], sample_view):
        """Setup Sample View to receive workflow Z-stack frames.

        Args:
            workflow_files: List of workflow file paths
            sample_view: Sample View instance
        """
        # Calculate expected Z-stack parameters from workflows
        z_stack_info = []
        for wf_file in workflow_files:
            position = parse_workflow_position(wf_file)
            if position:
                # Read Z-range from workflow file
                z_min, z_max = read_z_range_from_workflow(wf_file)
                position['z_min'] = z_min
                position['z_max'] = z_max
                z_stack_info.append(position)

        # Clear old data before starting new tile workflows
        if hasattr(sample_view, 'clear_data_for_workflows'):
            sample_view.clear_data_for_workflows()

        # Pass to Sample View for initialization
        if hasattr(sample_view, 'prepare_for_tile_workflows'):
            sample_view.prepare_for_tile_workflows(z_stack_info)
            logger.info(f"Sample View prepared to receive {len(z_stack_info)} tile workflows")
        else:
            logger.warning("Sample View does not have prepare_for_tile_workflows method")

    def _execute_workflows(self, workflow_files: List[Path]):
        """Execute the created workflow files using the workflow queue.

        Uses WorkflowQueueService to execute workflows sequentially,
        waiting for each to complete before starting the next.

        Args:
            workflow_files: List of workflow file paths to execute
        """
        # Check if Sample View integration is enabled
        add_to_sample_view = self._add_to_sample_view_checkbox.isChecked()

        if add_to_sample_view:
            # Get Sample View reference
            sample_view = self._get_sample_view_instance()
            if sample_view:
                # Register workflow metadata for frame interception
                self._setup_sample_view_integration(workflow_files, sample_view)
            else:
                logger.warning("Sample View not available - 3D integration disabled")
                add_to_sample_view = False

        # Try to get the application and workflow queue service
        try:
            if not self._app:
                from PyQt5.QtWidgets import QApplication
                app = QApplication.instance()

                # Find the main application
                if hasattr(app, 'flamingo_app'):
                    self._app = app.flamingo_app
                else:
                    parent = self.parent()
                    while parent:
                        if hasattr(parent, '_app'):
                            self._app = parent._app
                            break
                        parent = parent.parent()

            if not self._app:
                logger.warning("Could not find FlamingoApplication - workflows saved but not executed")
                QMessageBox.information(
                    self, "Workflows Saved",
                    "Workflow files saved. Execute them manually from the Workflow tab."
                )
                return

            # Check for workflow queue service
            has_queue = hasattr(self._app, 'workflow_queue_service')
            queue_service = getattr(self._app, 'workflow_queue_service', None) if has_queue else None
            logger.info(f"Workflow execution: has_queue_attr={has_queue}, queue_service_exists={queue_service is not None}")

            if queue_service is not None:
                logger.info("Using WorkflowQueueService for sequential execution")
                self._execute_with_queue_service(workflow_files, add_to_sample_view)
            else:
                # Fallback to workflow controller (sequential, but no completion detection)
                logger.warning("WorkflowQueueService not available - using fallback execution")
                self._execute_workflows_fallback(workflow_files, add_to_sample_view)

        except Exception as e:
            logger.error(f"Error during workflow execution: {e}")
            QMessageBox.warning(
                self, "Execution Error",
                f"Error executing workflows: {e}\n\nWorkflow files have been saved."
            )

    def _execute_with_queue_service(self, workflow_files: List[Path], add_to_sample_view: bool):
        """Execute workflows using WorkflowQueueService.

        Args:
            workflow_files: List of workflow file paths
            add_to_sample_view: Whether to integrate with Sample View
        """
        from py2flamingo.services.workflow_queue_service import WorkflowQueueService

        queue_service = self._app.workflow_queue_service

        # Build metadata list for Sample View integration
        metadata_list = []
        for wf_file in workflow_files:
            metadata = {}
            if add_to_sample_view:
                tile_position = parse_workflow_position(wf_file)
                if tile_position:
                    z_min, z_max = read_z_range_from_workflow(wf_file)
                    tile_position['z_min'] = z_min
                    tile_position['z_max'] = z_max
                    tile_position['channels'] = read_laser_channels_from_workflow(wf_file)
                    tile_position['z_velocity'] = read_z_velocity_from_workflow(wf_file)
                    metadata = tile_position
            metadata_list.append(metadata)

        # Set up workflow start callback for Sample View integration
        # Instead of using set_active_tile_position (signal-based, queued by exec_()),
        # we update tile metadata directly from the background thread (GIL-safe).
        camera_controller = None
        if add_to_sample_view and hasattr(self._app, 'workflow_controller'):
            wc = self._app.workflow_controller
            camera_controller = getattr(wc, '_camera_controller', None)

            def on_workflow_start(file_path: Path, metadata: Dict):
                """Update tile metadata directly from background thread (GIL-safe)."""
                if camera_controller and metadata:
                    camera_controller._current_tile_position = metadata  # atomic under GIL
                    camera_controller._z_plane_counter = 0

            queue_service.set_workflow_start_callback(on_workflow_start)

        # Create progress dialog as a top-level window (no parent)
        # This allows the tile collection dialog to close while progress remains visible
        # Use 0-100 range for percentage-based progress
        progress = QProgressDialog(
            "Executing workflows...", "Cancel", 0, 100, None
        )
        progress.setWindowModality(Qt.NonModal)  # Non-modal so user can interact with other windows
        progress.setMinimumDuration(0)
        progress.setWindowTitle("Workflow Progress")
        progress.setMinimumWidth(400)
        progress.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        progress.setAttribute(Qt.WA_DeleteOnClose)  # Clean up when closed

        # Track completion and current state
        self._queue_completed = False
        self._queue_error = None
        total_workflows = len(workflow_files)
        current_workflow_images = [0, 0]  # [acquired, expected]

        def calculate_overall_progress(workflow_idx: int, img_acquired: int, img_expected: int) -> int:
            """Calculate overall progress 0-100 based on workflow and image progress."""
            if total_workflows == 0:
                return 0
            # Base progress from completed workflows
            base_progress = (workflow_idx / total_workflows) * 100
            # Progress within current workflow
            if img_expected > 0:
                workflow_progress = (img_acquired / img_expected) * (100 / total_workflows)
            else:
                workflow_progress = 0
            return min(99, int(base_progress + workflow_progress))  # Cap at 99 until complete

        # Connect signals for progress updates
        def update_sample_view(status, pct):
            """Update Sample View's workflow progress display."""
            if self._app and hasattr(self._app, 'sample_view') and self._app.sample_view:
                self._app.sample_view.update_workflow_progress(status, pct, "--:--")

        def on_progress(current, total, message):
            if self._queue_completed:
                return
            # current is 1-indexed workflow number
            workflow_idx = current - 1
            pct = calculate_overall_progress(
                workflow_idx, current_workflow_images[0], current_workflow_images[1]
            )
            progress.setValue(pct)
            progress.setLabelText(message)

        def on_image_progress(acquired, expected):
            """Handle image-level progress updates."""
            if self._queue_completed:
                return  # Don't overwrite "Not Running" after completion
            current_workflow_images[0] = acquired
            current_workflow_images[1] = expected
            # Calculate overall progress
            workflow_idx = queue_service.current_index
            pct = calculate_overall_progress(workflow_idx, acquired, expected)
            progress.setValue(pct)
            status = f"Tile {workflow_idx + 1}/{total_workflows}: {acquired}/{expected} images"
            progress.setLabelText(status)
            update_sample_view(status, pct)

        def on_workflow_completed(index, total, path):
            if self._queue_completed:
                return
            # Reset image progress for next workflow
            current_workflow_images[0] = 0
            current_workflow_images[1] = 0
            pct = calculate_overall_progress(index + 1, 0, 0)
            progress.setValue(pct)
            # Update Sample View to show transition between workflows
            if index + 1 < total:
                update_sample_view(f"Tile {index + 2}/{total}: Starting...", pct)
            else:
                update_sample_view(f"Completing...", pct)
            logger.info(f"Workflow {index + 1}/{total} completed: {Path(path).name}")

        def on_queue_completed():
            self._queue_completed = True
            progress.setValue(100)  # 100% complete
            update_sample_view("Complete!", 100)  # Show 100% with status
            QTimer.singleShot(1500, lambda: update_sample_view("Not Running", 0))  # Delayed reset

            # Clean up signals before closing progress dialog
            if hasattr(progress, '_cleanup_signals'):
                progress._cleanup_signals()

            progress.close()  # Close the progress dialog

            # Clean up tile mode when all workflows are done
            if camera_controller:
                camera_controller.clear_tile_mode()
            if add_to_sample_view and hasattr(self._app, 'workflow_controller'):
                self._app.workflow_controller._suppress_tile_clear = False

            # Notify Sample View that tile workflows are complete
            if add_to_sample_view:
                sample_view = self._get_sample_view_instance()
                if sample_view and hasattr(sample_view, 'finish_tile_workflows'):
                    sample_view.finish_tile_workflows()

            # Reorganize folders AFTER all workflows confirmed complete
            # This is safe because queue_completed only fires after all
            # SYSTEM_STATE_IDLE callbacks have been received
            reorganized = reorganize_tile_folders(
                self._local_path, self._base_save_directory,
                self._tile_folder_mapping, self._local_access_enabled
            )

            # Use None as parent since tile collection dialog is closed
            if reorganized:
                QMessageBox.information(
                    None, "Execution Complete",
                    f"Successfully executed {len(workflow_files)} workflows.\n\n"
                    f"Folders reorganized into nested structure for MIP Overview compatibility."
                )
            else:
                QMessageBox.information(
                    None, "Execution Complete",
                    f"Successfully executed {len(workflow_files)} workflows."
                )

        def on_queue_cancelled():
            self._queue_completed = True
            update_sample_view("Not Running", 0)

            # Clean up signals before closing progress dialog
            if hasattr(progress, '_cleanup_signals'):
                progress._cleanup_signals()

            if camera_controller:
                camera_controller.clear_tile_mode()
            if add_to_sample_view and hasattr(self._app, 'workflow_controller'):
                self._app.workflow_controller._suppress_tile_clear = False

            # Notify Sample View that tile workflows are complete (even if cancelled)
            if add_to_sample_view:
                sample_view = self._get_sample_view_instance()
                if sample_view and hasattr(sample_view, 'finish_tile_workflows'):
                    sample_view.finish_tile_workflows()

            progress.close()  # Close the progress dialog
            # Use None as parent since tile collection dialog is closed
            QMessageBox.warning(
                None, "Execution Cancelled",
                "Workflow queue was cancelled."
            )

        def on_error(message):
            self._queue_error = message
            logger.error(f"Workflow queue error: {message}")

        # Connect signals
        queue_service.progress_updated.connect(on_progress)
        queue_service.workflow_progress.connect(on_image_progress)  # Image-level progress
        queue_service.workflow_completed.connect(on_workflow_completed)
        queue_service.queue_completed.connect(on_queue_completed)
        queue_service.queue_cancelled.connect(on_queue_cancelled)
        queue_service.error_occurred.connect(on_error)

        # Connect cancel button
        progress.canceled.connect(queue_service.cancel)

        try:
            # Update Sample View status at start
            update_sample_view(f"Tile Collection: 0/{total_workflows} tiles", 0)

            # Start data receiver and display timer ONCE (on main thread)
            # This avoids relying on cross-thread signals that get queued by exec_()
            if camera_controller:
                wc = self._app.workflow_controller
                wc._suppress_tile_clear = True  # Prevent per-workflow clear
                camera_controller._workflow_tile_mode = True
                camera_controller._current_tile_position = metadata_list[0] if metadata_list else {}
                camera_controller._z_plane_counter = 0
                # Start display timer on main thread (QTimer thread affinity)
                if not camera_controller._display_timer.isActive():
                    camera_controller._workflow_started_timer = True
                    camera_controller._display_timer.start(camera_controller._display_timer_interval_ms)
                # Enlarge frame buffer so GUI-thread stalls don't cause frame loss
                camera_controller.camera_service.set_tile_mode_buffer(True)
                # Start data receiver (listen-only, no LIVE_VIEW_START)
                try:
                    camera_controller.camera_service.ensure_data_receiver_running()
                    camera_controller._workflow_started_streaming = True
                except Exception as e:
                    logger.warning(f"Could not start data receiver: {e}")

            # Enqueue and start workflows
            logger.info(f"Enqueueing {len(workflow_files)} workflows to queue service")
            queue_service.enqueue(workflow_files, metadata_list)
            logger.info("Starting workflow queue execution")
            started = queue_service.start()
            logger.info(f"Queue service started: {started}")

            if not started:
                update_sample_view("Not Running", 0)
                progress.close()
                QMessageBox.warning(
                    None, "Queue Error",
                    "Failed to start workflow queue. Check logs for details."
                )
                return

            # Show progress dialog (non-blocking - allows tile collection dialog to close)
            progress.show()

            # Store cleanup function for callbacks to use
            def cleanup_signals():
                """Disconnect signals to avoid issues with stale connections."""
                try:
                    queue_service.progress_updated.disconnect(on_progress)
                    queue_service.workflow_progress.disconnect(on_image_progress)
                    queue_service.workflow_completed.disconnect(on_workflow_completed)
                    queue_service.queue_completed.disconnect(on_queue_completed)
                    queue_service.queue_cancelled.disconnect(on_queue_cancelled)
                    queue_service.error_occurred.disconnect(on_error)
                except Exception:
                    pass  # Signals may already be disconnected

            # Store cleanup function on progress dialog for callbacks to access
            progress._cleanup_signals = cleanup_signals

        except Exception as e:
            logger.error(f"Error setting up workflow execution: {e}")
            if camera_controller and camera_controller._workflow_tile_mode:
                camera_controller.clear_tile_mode()
            if add_to_sample_view and hasattr(self._app, 'workflow_controller'):
                self._app.workflow_controller._suppress_tile_clear = False

    def _execute_workflows_fallback(self, workflow_files: List[Path], add_to_sample_view: bool):
        """Fallback workflow execution without queue service.

        Uses simple sequential execution with estimated timing.
        Not ideal but maintains backward compatibility.

        Args:
            workflow_files: List of workflow file paths
            add_to_sample_view: Whether to integrate with Sample View
        """
        progress = QProgressDialog("Executing workflows...", "Cancel", 0, len(workflow_files), self)
        progress.setWindowModality(Qt.WindowModal)

        for i, workflow_file in enumerate(workflow_files):
            if progress.wasCanceled():
                break

            progress.setValue(i)
            progress.setLabelText(f"Executing {workflow_file.name}...")

            # Parse workflow position for Sample View integration
            tile_position = None
            if add_to_sample_view:
                tile_position = parse_workflow_position(workflow_file)
                if tile_position:
                    z_min, z_max = read_z_range_from_workflow(workflow_file)
                    tile_position['z_min'] = z_min
                    tile_position['z_max'] = z_max

            try:
                if hasattr(self._app, 'workflow_controller'):
                    controller = self._app.workflow_controller
                    success, msg = controller.load_workflow(str(workflow_file))
                    if success:
                        if add_to_sample_view and tile_position and hasattr(controller, 'set_active_tile_position'):
                            controller.set_active_tile_position(tile_position)

                        success, msg = controller.start_workflow()
                        if success:
                            logger.info(f"Started workflow: {workflow_file.name}")
                            # Estimate workflow time based on Z range
                            # This is a rough estimate - actual time depends on many factors
                            z_range = (tile_position['z_max'] - tile_position['z_min']) if tile_position else 1.0
                            estimated_time = max(5.0, z_range * 10.0)  # ~10s per mm of Z
                            logger.info(f"Waiting {estimated_time:.1f}s for workflow completion...")
                            import time
                            time.sleep(estimated_time)
                        else:
                            logger.error(f"Failed to start {workflow_file.name}: {msg}")
                    else:
                        logger.error(f"Failed to load {workflow_file.name}: {msg}")
            except Exception as e:
                logger.error(f"Error executing {workflow_file.name}: {e}")

        progress.setValue(len(workflow_files))
        QMessageBox.information(
            self, "Execution Complete",
            f"Executed {len(workflow_files)} workflows.\n\n"
            "Note: Used fallback timing. For better reliability, "
            "ensure WorkflowQueueService is configured."
        )

    def _get_config_service(self):
        """Get ConfigurationService from application."""
        if self._app and hasattr(self._app, 'config_service'):
            return self._app.config_service
        return None

    def _get_geometry_manager(self):
        """Get WindowGeometryManager from application."""
        if self._app and hasattr(self._app, 'geometry_manager'):
            return self._app.geometry_manager
        return None

    def _save_dialog_state(self) -> None:
        """Save all dialog settings for persistence."""
        gm = self._get_geometry_manager()
        if not gm:
            return

        state = {
            # Dialog-level settings
            'workflow_type': self._type_combo.currentIndex(),
            'name_prefix': self._name_prefix.text(),
            'add_to_sample_view': self._add_to_sample_view_checkbox.isChecked(),

            # Panel settings (using ui_state methods for raw dict persistence)
            'illumination': self._illumination_panel.get_ui_state(),
            'camera': self._camera_panel.get_settings(),
            'zstack': self._zstack_panel.get_ui_state(),
            'save': self._save_panel.get_settings(),
        }

        # Primary direction (only if dual view mode available)
        if self._has_dual_view:
            state['primary_is_left'] = self._primary_is_left

        try:
            gm.save_dialog_state("TileCollectionDialog", state)
            gm.save_all()
            logger.debug("Saved TileCollectionDialog state")
        except Exception as e:
            logger.warning(f"Failed to save dialog state: {e}")

    def _restore_dialog_state(self) -> None:
        """Restore dialog settings from persistence."""
        gm = self._get_geometry_manager()
        if not gm:
            return

        try:
            state = gm.restore_dialog_state("TileCollectionDialog")
        except Exception as e:
            logger.warning(f"Failed to restore dialog state: {e}")
            state = None

        if not state:
            # Apply defaults (Z-Stack mode already set)
            return

        logger.debug("Restoring TileCollectionDialog state")

        # Restore workflow type
        if 'workflow_type' in state:
            idx = state['workflow_type']
            self._type_combo.setCurrentIndex(idx)
            self._on_type_changed(idx)

        # Restore name prefix
        if 'name_prefix' in state:
            self._name_prefix.setText(state['name_prefix'])

        # Restore add to sample view checkbox
        if 'add_to_sample_view' in state:
            self._add_to_sample_view_checkbox.setChecked(state['add_to_sample_view'])

        # Restore panel settings
        if 'illumination' in state:
            try:
                self._illumination_panel.set_ui_state(state['illumination'])
            except Exception as e:
                logger.warning(f"Failed to restore illumination settings: {e}")

        if 'camera' in state:
            try:
                self._camera_panel.set_settings(state['camera'])
            except Exception as e:
                logger.warning(f"Failed to restore camera settings: {e}")

        if 'zstack' in state:
            try:
                self._zstack_panel.set_ui_state(state['zstack'])
            except Exception as e:
                logger.warning(f"Failed to restore zstack settings: {e}")

        if 'save' in state:
            try:
                self._save_panel.set_settings(state['save'])
            except Exception as e:
                logger.warning(f"Failed to restore save settings: {e}")

        # Restore primary direction
        if 'primary_is_left' in state and self._has_dual_view:
            self._primary_is_left = state['primary_is_left']
            # Update combo box
            if hasattr(self, '_direction_combo'):
                self._direction_combo.setCurrentIndex(0 if self._primary_is_left else 1)

    def accept(self):
        """Save state before accepting."""
        self._save_dialog_state()
        super().accept()

    def reject(self):
        """Save state before rejecting."""
        self._save_dialog_state()
        super().reject()

    def showEvent(self, event):
        """Handle show event - trigger camera auto-detection."""
        super().showEvent(event)

        # Auto-detect camera settings on first show
        if not self._camera_panel._auto_detected:
            self._camera_panel.detect_camera_settings()
