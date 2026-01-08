"""Tile Collection Dialog.

Dialog for configuring and creating workflows for selected tiles
from the LED 2D Overview result window.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Dict

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QComboBox, QScrollArea, QWidget,
    QMessageBox, QProgressDialog, QFrame
)
from PyQt5.QtCore import Qt

from py2flamingo.views.workflow_panels import (
    IlluminationPanel, ZStackPanel, SavePanel, CameraPanel
)
from py2flamingo.models.data.workflow import WorkflowType, Workflow, StackSettings
from py2flamingo.models.microscope import Position

logger = logging.getLogger(__name__)


def calculate_tile_z_ranges(
    primary_tiles: List,
    secondary_tiles: List,
    fallback_z_min: float,
    fallback_z_max: float
) -> Dict[Tuple[int, int], Tuple[float, float]]:
    """Calculate Z range for each primary tile based on secondary tile overlap.

    When tiles are selected from two orthogonal views (e.g., 0° and 90°),
    this function determines the Z range for each primary tile by finding
    the min/max Z values from the secondary view.

    For simplicity, this implementation uses the global Z range from all
    secondary tiles for all primary tiles (no per-tile spatial overlap
    calculation). This assumes no gaps in the sample.

    Args:
        primary_tiles: Tiles from the primary view (where Z-stacks will be taken)
        secondary_tiles: Tiles from the secondary view (defines Z limits)
        fallback_z_min: Fallback minimum Z if no secondary tiles
        fallback_z_max: Fallback maximum Z if no secondary tiles

    Returns:
        Dictionary mapping (tile_x_idx, tile_y_idx) to (z_min, z_max)
    """
    # If no secondary tiles, use fallback range for all primary tiles
    if not secondary_tiles:
        return {
            (tile.tile_x_idx, tile.tile_y_idx): (fallback_z_min, fallback_z_max)
            for tile in primary_tiles
        }

    # Get global Z range from secondary tiles
    secondary_z_values = [tile.z for tile in secondary_tiles]
    z_min = min(secondary_z_values)
    z_max = max(secondary_z_values)

    # Apply same Z range to all primary tiles
    return {
        (tile.tile_x_idx, tile.tile_y_idx): (z_min, z_max)
        for tile in primary_tiles
    }


class TileCollectionDialog(QDialog):
    """Dialog for creating workflows for selected tiles.

    Provides workflow configuration (illumination, Z-stack, save settings)
    without position inputs - positions come from selected tiles.
    """

    def __init__(self, left_tiles: List, right_tiles: List,
                 left_rotation: float, right_rotation: float,
                 config=None, app=None, parent=None):
        """Initialize the dialog.

        Args:
            left_tiles: List of TileResult from left panel
            right_tiles: List of TileResult from right panel
            left_rotation: Rotation angle for left panel tiles
            right_rotation: Rotation angle for right panel tiles
            config: ScanConfiguration with bounding box info
            app: FlamingoApplication instance for accessing services
            parent: Parent widget
        """
        super().__init__(parent)

        self._left_tiles = left_tiles
        self._right_tiles = right_tiles
        self._left_rotation = left_rotation
        self._right_rotation = right_rotation
        self._config = config
        self._app = app
        self._workflow_type = WorkflowType.SNAPSHOT

        # Determine if 90-degree overlap mode is available
        self._has_dual_view = bool(left_tiles) and bool(right_tiles)
        self._primary_is_left = True  # Default: left panel is primary

        # Calculate Z ranges for tiles
        self._tile_z_ranges: Dict[Tuple[int, int], Tuple[float, float]] = {}
        self._update_z_ranges()

        self.setWindowTitle("Collect Tiles - Workflow Configuration")
        self.setMinimumWidth(500)
        self.setMinimumHeight(600)

        self._setup_ui()

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

        # Camera panel for exposure/frame rate settings
        self._camera_panel = CameraPanel()
        self._camera_panel.settings_changed.connect(self._on_camera_settings_changed)
        container_layout.addWidget(self._camera_panel)

        # Z-Stack panel (shown only for Z-Stack type) - pass app for system defaults
        self._zstack_panel = ZStackPanel(app=self._app)
        self._zstack_panel.setVisible(False)
        container_layout.addWidget(self._zstack_panel)

        # Initialize Z velocity with current frame rate
        camera_settings = self._camera_panel.get_settings()
        self._zstack_panel.set_frame_rate(camera_settings['frame_rate'])

        # Save panel - pass app for system storage location and connection_service for drive refresh
        connection_service = getattr(self._app, 'connection_service', None) if self._app else None
        self._save_panel = SavePanel(app=self._app, connection_service=connection_service)
        container_layout.addWidget(self._save_panel)

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
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        layout.addWidget(self._type_combo)

        self._type_description = QLabel("Single image at each tile position")
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
        else:
            self._workflow_type = WorkflowType.ZSTACK
            if self._has_dual_view:
                self._type_description.setText("Z-stack using 90° overlap Z range")
            else:
                self._type_description.setText("Z-stack using bounding box Z range")
            self._zstack_panel.setVisible(True)

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

        # Validate illumination
        illumination = self._illumination_panel.get_settings()
        if not illumination.laser_enabled and not illumination.led_enabled:
            QMessageBox.warning(self, "No Illumination", "Please enable at least one light source.")
            return

        # Get save settings
        save_settings = self._save_panel.get_settings()

        # Create output folder for workflow files
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        workflow_folder = Path(save_settings['save_drive']) / save_settings['save_directory'] / f"workflows_{timestamp}"

        try:
            workflow_folder.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created workflow folder: {workflow_folder}")
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
            # Single view mode: use all tiles with bounding box Z range
            bbox_z_min = self._config.bounding_box.z_min if self._config else 0.0
            bbox_z_max = self._config.bounding_box.z_max if self._config else 10.0

            for tile in self._left_tiles:
                tiles_to_process.append((tile, self._left_rotation, bbox_z_min, bbox_z_max))
            for tile in self._right_tiles:
                tiles_to_process.append((tile, self._right_rotation, bbox_z_min, bbox_z_max))

        total = len(tiles_to_process)
        if total == 0:
            QMessageBox.warning(self, "No Tiles", "No tiles selected.")
            return

        # Create progress dialog
        progress = QProgressDialog("Creating workflows...", "Cancel", 0, total, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        # Create workflows
        created_files = []
        for i, (tile, rotation, z_min, z_max) in enumerate(tiles_to_process):
            if progress.wasCanceled():
                break

            progress.setValue(i)
            progress.setLabelText(f"Creating workflow {i+1}/{total}...")

            # Create workflow name
            workflow_name = f"{name_prefix}_R{rotation:.0f}_X{tile.x:.2f}_Y{tile.y:.2f}"

            # Create position
            position = Position(x=tile.x, y=tile.y, z=tile.z, r=rotation)

            # Build workflow text with per-tile Z range
            workflow_text = self._build_workflow_text(
                workflow_name, position, illumination, save_settings, z_min, z_max
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
            msg = f"Created {len(created_files)} workflow files in:\n{workflow_folder}\n\n"
            msg += "Would you like to execute them now?"

            result = QMessageBox.question(
                self, "Workflows Created", msg,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )

            if result == QMessageBox.Yes:
                self._execute_workflows(created_files)

        self.accept()

    def _build_workflow_text(self, name: str, position: Position,
                             illumination, save_settings: dict,
                             z_min: float, z_max: float) -> str:
        """Build workflow file text content.

        Args:
            name: Workflow name
            position: Start position
            illumination: IlluminationSettings
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

        # Experiment Settings
        lines.append("    <Experiment Settings>")

        stack = self._zstack_panel.get_settings() if self._workflow_type == WorkflowType.ZSTACK else None
        plane_spacing = stack.z_step_um if stack else 1.0

        lines.append(f"    Plane spacing (um) = {plane_spacing}")
        lines.append(f"    Frame rate (f/s) = {frame_rate:.4f}")
        lines.append(f"    Exposure time (us) = {exposure_us}")
        lines.append("    Duration (dd:hh:mm:ss) = 00:00:00:01")
        lines.append("    Interval (dd:hh:mm:ss) = 00:00:00:01")
        lines.append(f"    Sample = {name}")
        lines.append("    Number of angles = 1")
        lines.append("    Angle step size = 0")
        lines.append("    Region = ")
        lines.append(f"    Save image drive = {save_settings['save_drive']}")
        lines.append(f"    Save image directory = {save_settings['save_directory']}")
        lines.append(f"    Comments = Tile collection workflow")
        lines.append(f"    Save max projection = {'true' if save_settings['save_mip'] else 'false'}")
        lines.append(f"    Display max projection = {'true' if save_settings['display_mip'] else 'true'}")
        lines.append(f"    Save image data = {save_settings['save_format'] if save_settings['save_enabled'] else 'NotSaved'}")
        lines.append("    Save to subfolders = false")
        lines.append(f"    Work flow live view enabled = {'true' if save_settings['live_view'] else 'true'}")
        lines.append("    </Experiment Settings>")

        # Camera Settings
        lines.append("")
        lines.append("    <Camera Settings>")
        lines.append(f"    Exposure time (us) = {exposure_us}")
        lines.append(f"    Frame rate (f/s) = {frame_rate:.4f}")
        lines.append(f"    AOI width = {camera_settings['aoi_width']}")
        lines.append(f"    AOI height = {camera_settings['aoi_height']}")
        lines.append("    </Camera Settings>")

        # Stack Settings
        lines.append("")
        lines.append("    <Stack Settings>")
        lines.append("    Stack index = ")

        if self._workflow_type == WorkflowType.ZSTACK and stack:
            # Use full Z range from bounding box
            z_range_mm = z_max - z_min
            num_planes = max(1, int(z_range_mm / (stack.z_step_um / 1000.0)) + 1)
            lines.append(f"    Change in Z axis (mm) = {z_range_mm:.6f}")
            lines.append(f"    Number of planes = {num_planes}")
            lines.append(f"    Z stage velocity (mm/s) = {stack.z_velocity_mm_s}")
        else:
            lines.append("    Change in Z axis (mm) = 0.001")
            lines.append("    Number of planes = 1")
            lines.append("    Z stage velocity (mm/s) = 0.4")

        lines.append("    Rotational stage velocity (°/s) = 0")
        lines.append("    Auto update stack calculations = true")
        lines.append("    Camera 1 capture percentage = 100")
        lines.append("    Camera 1 capture mode = 0")
        lines.append("    Stack option = None")
        lines.append("    Stack option settings 1 = 0")
        lines.append("    Stack option settings 2 = 0")
        lines.append("    </Stack Settings>")

        # Start Position
        lines.append("")
        lines.append("    <Start Position>")
        start_z = z_min if self._workflow_type == WorkflowType.ZSTACK else position.z
        lines.append(f"    X (mm) = {position.x:.6f}")
        lines.append(f"    Y (mm) = {position.y:.6f}")
        lines.append(f"    Z (mm) = {start_z:.6f}")
        lines.append(f"    Angle (degrees) = {position.r:.2f}")
        lines.append("    </Start Position>")

        # End Position
        lines.append("")
        lines.append("    <End Position>")
        end_z = z_max if self._workflow_type == WorkflowType.ZSTACK else position.z
        lines.append(f"    X (mm) = {position.x:.6f}")
        lines.append(f"    Y (mm) = {position.y:.6f}")
        lines.append(f"    Z (mm) = {end_z:.6f}")
        lines.append(f"    Angle (degrees) = {position.r:.2f}")
        lines.append("    </End Position>")

        # Illumination Source
        lines.append("")
        lines.append("    <Illumination Source>")
        if illumination.laser_enabled and illumination.laser_channel:
            lines.append(f"    {illumination.laser_channel} = {illumination.laser_power_mw:.2f} 1")
        if illumination.led_enabled and illumination.led_channel:
            lines.append(f"    {illumination.led_channel} = {illumination.led_intensity_percent:.1f} 1")
            lines.append("    LED selection = 0 0")
        lines.append("    LED DAC = 42000 0")
        lines.append("    </Illumination Source>")

        # Illumination Path
        lines.append("")
        lines.append("    <Illumination Path>")
        lines.append("    Left path = ON")
        lines.append("    Right path = OFF")
        lines.append("    </Illumination Path>")

        # Illumination Options
        lines.append("")
        lines.append("    <Illumination Options>")
        lines.append("    Run stack with multiple lasers on = false")
        lines.append("    </Illumination Options>")

        lines.append("</Workflow Settings>")

        return "\n".join(lines)

    def _execute_workflows(self, workflow_files: List[Path]):
        """Execute the created workflow files.

        Args:
            workflow_files: List of workflow file paths to execute
        """
        # Try to get the application and workflow controller
        try:
            from PyQt5.QtWidgets import QApplication
            app = QApplication.instance()

            # Find the main application
            if hasattr(app, 'flamingo_app'):
                flamingo_app = app.flamingo_app
            else:
                # Try to find through parent widgets
                parent = self.parent()
                while parent:
                    if hasattr(parent, '_app'):
                        flamingo_app = parent._app
                        break
                    parent = parent.parent()
                else:
                    logger.warning("Could not find FlamingoApplication - workflows saved but not executed")
                    QMessageBox.information(
                        self, "Workflows Saved",
                        f"Workflow files saved. Execute them manually from the Workflow tab."
                    )
                    return

            # Execute each workflow
            progress = QProgressDialog("Executing workflows...", "Cancel", 0, len(workflow_files), self)
            progress.setWindowModality(Qt.WindowModal)

            for i, workflow_file in enumerate(workflow_files):
                if progress.wasCanceled():
                    break

                progress.setValue(i)
                progress.setLabelText(f"Executing {workflow_file.name}...")

                try:
                    # Load and execute workflow
                    if hasattr(flamingo_app, 'workflow_controller'):
                        controller = flamingo_app.workflow_controller
                        success, msg = controller.load_workflow(str(workflow_file))
                        if success:
                            success, msg = controller.start_workflow()
                            if success:
                                logger.info(f"Started workflow: {workflow_file.name}")
                                # Wait for workflow to complete (simplified - real impl would monitor)
                                import time
                                time.sleep(0.5)  # Brief pause between workflows
                            else:
                                logger.error(f"Failed to start {workflow_file.name}: {msg}")
                        else:
                            logger.error(f"Failed to load {workflow_file.name}: {msg}")
                except Exception as e:
                    logger.error(f"Error executing {workflow_file.name}: {e}")

            progress.setValue(len(workflow_files))
            QMessageBox.information(
                self, "Execution Complete",
                f"Executed {len(workflow_files)} workflows."
            )

        except Exception as e:
            logger.error(f"Error during workflow execution: {e}")
            QMessageBox.warning(
                self, "Execution Error",
                f"Error executing workflows: {e}\n\nWorkflow files have been saved."
            )
