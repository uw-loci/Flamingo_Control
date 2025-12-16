"""LED 2D Overview Dialog.

Configuration dialog for the LED 2D Overview extension that creates
focus-stacked 2D maps at two rotation angles.
"""

import logging
from typing import Optional, List, Tuple, NamedTuple
from dataclasses import dataclass

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QDoubleSpinBox, QSpinBox,
    QMessageBox, QFrame, QSizePolicy
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont


@dataclass
class BoundingBox:
    """Axis-aligned bounding box for scan region."""
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        return self.y_max - self.y_min

    @property
    def depth(self) -> float:
        return self.z_max - self.z_min


@dataclass
class ScanConfiguration:
    """Complete scan configuration for LED 2D Overview."""
    bounding_box: BoundingBox
    starting_r: float  # Rotation angle in degrees
    z_stack_range: float  # +/- mm from center
    z_step_size: float  # um
    tile_overlap: float  # percentage
    led_name: str
    led_intensity: float


class LED2DOverviewDialog(QDialog):
    """Configuration dialog for LED 2D Overview scans.

    This dialog allows users to:
    - Define 2-3 bounding points (A, B, optional C)
    - Set scan parameters (rotation, Z-stack, tile overlap)
    - Preview the tile grid layout
    - Start the scan

    The scan creates two 2D focus-stacked maps at R and R+90 degrees.
    """

    # Emitted when user requests to start scan
    scan_requested = pyqtSignal(object)  # ScanConfiguration

    # Default FOV for N7 camera (mm per pixel * pixels)
    DEFAULT_FOV_MM = 0.5182  # mm

    def __init__(self, app, parent=None):
        """Initialize the dialog.

        Args:
            app: FlamingoApplication instance for accessing sample_view etc.
            parent: Parent widget
        """
        super().__init__(parent)
        self._app = app
        self._logger = logging.getLogger(__name__)

        self.setWindowTitle("LED 2D Overview")
        self.setMinimumWidth(500)
        self.setModal(True)

        self._setup_ui()
        self._load_current_settings()
        self._update_scan_info()

    def _setup_ui(self):
        """Create the dialog UI."""
        layout = QVBoxLayout()
        layout.setSpacing(12)

        # Bounding Points group
        points_group = self._create_points_group()
        layout.addWidget(points_group)

        # Scan Settings group
        settings_group = self._create_settings_group()
        layout.addWidget(settings_group)

        # Imaging (LED) display group
        imaging_group = self._create_imaging_group()
        layout.addWidget(imaging_group)

        # Scan Info display
        info_group = self._create_info_group()
        layout.addWidget(info_group)

        # Button row
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.preview_btn = QPushButton("Preview Grid")
        self.preview_btn.setToolTip("Show empty tile grid layout before scanning")
        self.preview_btn.clicked.connect(self._on_preview_clicked)
        button_layout.addWidget(self.preview_btn)

        self.start_btn = QPushButton("Start Scan")
        self.start_btn.setToolTip("Start the 2D overview scan")
        self.start_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; "
            "padding: 8px 16px; }"
            "QPushButton:disabled { background-color: #ccc; }"
        )
        self.start_btn.clicked.connect(self._on_start_clicked)
        button_layout.addWidget(self.start_btn)

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.close_btn)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def _create_points_group(self) -> QGroupBox:
        """Create the bounding points input group."""
        group = QGroupBox("Bounding Points")
        layout = QVBoxLayout()

        # Create point input rows
        grid = QGridLayout()
        grid.setSpacing(8)

        # Headers
        grid.addWidget(QLabel(""), 0, 0)
        grid.addWidget(QLabel("X (mm)"), 0, 1)
        grid.addWidget(QLabel("Y (mm)"), 0, 2)
        grid.addWidget(QLabel("Z (mm)"), 0, 3)
        grid.addWidget(QLabel(""), 0, 4)

        # Point A
        self.point_a_label = QLabel("Point A:")
        self.point_a_x = QDoubleSpinBox()
        self.point_a_y = QDoubleSpinBox()
        self.point_a_z = QDoubleSpinBox()
        self.point_a_btn = QPushButton("Get Pos")
        self._setup_coordinate_spinbox(self.point_a_x)
        self._setup_coordinate_spinbox(self.point_a_y)
        self._setup_coordinate_spinbox(self.point_a_z)
        self.point_a_btn.clicked.connect(lambda: self._get_current_position('A'))
        grid.addWidget(self.point_a_label, 1, 0)
        grid.addWidget(self.point_a_x, 1, 1)
        grid.addWidget(self.point_a_y, 1, 2)
        grid.addWidget(self.point_a_z, 1, 3)
        grid.addWidget(self.point_a_btn, 1, 4)

        # Point B
        self.point_b_label = QLabel("Point B:")
        self.point_b_x = QDoubleSpinBox()
        self.point_b_y = QDoubleSpinBox()
        self.point_b_z = QDoubleSpinBox()
        self.point_b_btn = QPushButton("Get Pos")
        self._setup_coordinate_spinbox(self.point_b_x)
        self._setup_coordinate_spinbox(self.point_b_y)
        self._setup_coordinate_spinbox(self.point_b_z)
        self.point_b_btn.clicked.connect(lambda: self._get_current_position('B'))
        grid.addWidget(self.point_b_label, 2, 0)
        grid.addWidget(self.point_b_x, 2, 1)
        grid.addWidget(self.point_b_y, 2, 2)
        grid.addWidget(self.point_b_z, 2, 3)
        grid.addWidget(self.point_b_btn, 2, 4)

        # Point C (optional)
        self.point_c_label = QLabel("Point C:")
        self.point_c_x = QDoubleSpinBox()
        self.point_c_y = QDoubleSpinBox()
        self.point_c_z = QDoubleSpinBox()
        self.point_c_btn = QPushButton("Get Pos")
        self.point_c_clear = QPushButton("Clear")
        self._setup_coordinate_spinbox(self.point_c_x, optional=True)
        self._setup_coordinate_spinbox(self.point_c_y, optional=True)
        self._setup_coordinate_spinbox(self.point_c_z, optional=True)
        self.point_c_btn.clicked.connect(lambda: self._get_current_position('C'))
        self.point_c_clear.clicked.connect(self._clear_point_c)

        c_layout = QHBoxLayout()
        c_layout.addWidget(self.point_c_btn)
        c_layout.addWidget(self.point_c_clear)

        grid.addWidget(self.point_c_label, 3, 0)
        grid.addWidget(self.point_c_x, 3, 1)
        grid.addWidget(self.point_c_y, 3, 2)
        grid.addWidget(self.point_c_z, 3, 3)
        grid.addLayout(c_layout, 3, 4)

        layout.addLayout(grid)

        # Note about Point C
        note = QLabel("(Point C is optional - expands bounding box if provided)")
        note.setStyleSheet("color: gray; font-style: italic; font-size: 10px;")
        layout.addWidget(note)

        group.setLayout(layout)
        return group

    def _setup_coordinate_spinbox(self, spinbox: QDoubleSpinBox, optional: bool = False):
        """Configure a coordinate spinbox."""
        spinbox.setRange(-50.0, 50.0)
        spinbox.setDecimals(3)
        spinbox.setSingleStep(0.1)
        spinbox.setSuffix(" mm")
        if optional:
            spinbox.setSpecialValueText("--")
            spinbox.setValue(spinbox.minimum())
        else:
            spinbox.setValue(0.0)
        spinbox.valueChanged.connect(self._update_scan_info)

    def _create_settings_group(self) -> QGroupBox:
        """Create the scan settings group."""
        group = QGroupBox("Scan Settings")
        layout = QGridLayout()
        layout.setSpacing(8)

        row = 0

        # Starting R
        layout.addWidget(QLabel("Starting R:"), row, 0)
        self.starting_r = QDoubleSpinBox()
        self.starting_r.setRange(-180.0, 180.0)
        self.starting_r.setDecimals(1)
        self.starting_r.setSingleStep(1.0)
        self.starting_r.setSuffix("°")
        self.starting_r.setValue(0.0)
        self.starting_r.setToolTip("First rotation angle (second will be +90°)")
        layout.addWidget(self.starting_r, row, 1)
        row += 1

        # Z Stack Range
        layout.addWidget(QLabel("Z Stack Range:"), row, 0)
        self.z_stack_range = QDoubleSpinBox()
        self.z_stack_range.setRange(0.1, 5.0)
        self.z_stack_range.setDecimals(2)
        self.z_stack_range.setSingleStep(0.1)
        self.z_stack_range.setSuffix(" mm")
        self.z_stack_range.setValue(0.5)
        self.z_stack_range.setToolTip("+/- distance from center Z for focus stacking")
        self.z_stack_range.valueChanged.connect(self._update_scan_info)
        layout.addWidget(self.z_stack_range, row, 1)
        layout.addWidget(QLabel("(+/-)"), row, 2)
        row += 1

        # Z Step Size
        layout.addWidget(QLabel("Z Step Size:"), row, 0)
        self.z_step_size = QSpinBox()
        self.z_step_size.setRange(10, 500)
        self.z_step_size.setSingleStep(10)
        self.z_step_size.setSuffix(" µm")
        self.z_step_size.setValue(50)
        self.z_step_size.setToolTip("Distance between Z positions in focus stack")
        self.z_step_size.valueChanged.connect(self._update_scan_info)
        layout.addWidget(self.z_step_size, row, 1)
        row += 1

        # Tile Overlap
        layout.addWidget(QLabel("Tile Overlap:"), row, 0)
        self.tile_overlap = QSpinBox()
        self.tile_overlap.setRange(0, 50)
        self.tile_overlap.setSingleStep(5)
        self.tile_overlap.setSuffix(" %")
        self.tile_overlap.setValue(10)
        self.tile_overlap.setToolTip("Percentage overlap between adjacent tiles")
        self.tile_overlap.valueChanged.connect(self._update_scan_info)
        layout.addWidget(self.tile_overlap, row, 1)

        group.setLayout(layout)
        return group

    def _create_imaging_group(self) -> QGroupBox:
        """Create the imaging (LED) display group."""
        group = QGroupBox("Imaging (from Sample View)")
        layout = QVBoxLayout()

        self.led_info_label = QLabel("LED: --")
        self.led_info_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.led_info_label)

        self.intensity_info_label = QLabel("Intensity: --")
        layout.addWidget(self.intensity_info_label)

        # Refresh button
        refresh_btn = QPushButton("Refresh from Sample View")
        refresh_btn.setToolTip("Update LED settings from Sample View")
        refresh_btn.clicked.connect(self._load_current_settings)
        refresh_btn.setMaximumWidth(200)
        layout.addWidget(refresh_btn)

        group.setLayout(layout)
        return group

    def _create_info_group(self) -> QGroupBox:
        """Create the scan info display group."""
        group = QGroupBox("Scan Info")
        layout = QVBoxLayout()

        # Info display (calculated based on settings)
        self.tiles_label = QLabel("Tiles: calculating...")
        self.total_tiles_label = QLabel("Total tiles: calculating...")
        self.est_time_label = QLabel("Est. time: calculating...")

        layout.addWidget(self.tiles_label)
        layout.addWidget(self.total_tiles_label)
        layout.addWidget(self.est_time_label)

        group.setLayout(layout)
        return group

    def _get_current_position(self, point: str):
        """Get current stage position and fill in the corresponding point.

        Args:
            point: 'A', 'B', or 'C'
        """
        if not self._app or not self._app.sample_view:
            QMessageBox.warning(self, "Error", "Sample View not available")
            return

        movement_controller = self._app.sample_view.movement_controller
        if not movement_controller:
            QMessageBox.warning(self, "Error", "Movement controller not available")
            return

        try:
            pos = movement_controller.get_current_position()
            if pos is None:
                QMessageBox.warning(self, "Error", "Could not read stage position")
                return

            # Fill in the appropriate spinboxes
            if point == 'A':
                self.point_a_x.setValue(pos.x)
                self.point_a_y.setValue(pos.y)
                self.point_a_z.setValue(pos.z)
            elif point == 'B':
                self.point_b_x.setValue(pos.x)
                self.point_b_y.setValue(pos.y)
                self.point_b_z.setValue(pos.z)
            elif point == 'C':
                self.point_c_x.setValue(pos.x)
                self.point_c_y.setValue(pos.y)
                self.point_c_z.setValue(pos.z)

            self._logger.info(f"Set Point {point} to ({pos.x:.3f}, {pos.y:.3f}, {pos.z:.3f})")
            self._update_scan_info()

        except Exception as e:
            self._logger.error(f"Error getting position: {e}")
            QMessageBox.warning(self, "Error", f"Failed to get position: {e}")

    def _clear_point_c(self):
        """Clear Point C values."""
        self.point_c_x.setValue(self.point_c_x.minimum())
        self.point_c_y.setValue(self.point_c_y.minimum())
        self.point_c_z.setValue(self.point_c_z.minimum())
        self._update_scan_info()

    def _load_current_settings(self):
        """Load current LED settings from Sample View."""
        if not self._app or not self._app.sample_view:
            self.led_info_label.setText("LED: Sample View not open")
            self.intensity_info_label.setText("Intensity: --")
            return

        try:
            panel = self._app.sample_view.laser_led_panel
            if panel:
                source = panel.get_selected_source()
                self.led_info_label.setText(f"LED: {source}")
                # TODO: Get intensity value from panel
                self.intensity_info_label.setText("Intensity: (using current setting)")
            else:
                self.led_info_label.setText("LED: Panel not available")
                self.intensity_info_label.setText("Intensity: --")
        except Exception as e:
            self._logger.error(f"Error loading LED settings: {e}")
            self.led_info_label.setText(f"LED: Error - {e}")

    def _get_bounding_box(self) -> Optional[BoundingBox]:
        """Calculate bounding box from entered points.

        Returns:
            BoundingBox or None if insufficient points
        """
        points = []

        # Point A (required)
        points.append((
            self.point_a_x.value(),
            self.point_a_y.value(),
            self.point_a_z.value()
        ))

        # Point B (required)
        points.append((
            self.point_b_x.value(),
            self.point_b_y.value(),
            self.point_b_z.value()
        ))

        # Point C (optional - only include if not at minimum)
        if (self.point_c_x.value() > self.point_c_x.minimum() or
            self.point_c_y.value() > self.point_c_y.minimum() or
            self.point_c_z.value() > self.point_c_z.minimum()):
            points.append((
                self.point_c_x.value(),
                self.point_c_y.value(),
                self.point_c_z.value()
            ))

        if len(points) < 2:
            return None

        x_coords = [p[0] for p in points]
        y_coords = [p[1] for p in points]
        z_coords = [p[2] for p in points]

        return BoundingBox(
            x_min=min(x_coords), x_max=max(x_coords),
            y_min=min(y_coords), y_max=max(y_coords),
            z_min=min(z_coords), z_max=max(z_coords)
        )

    def _calculate_tile_count(self, bbox: BoundingBox) -> Tuple[int, int]:
        """Calculate number of tiles needed.

        Args:
            bbox: Bounding box for scan region

        Returns:
            Tuple of (tiles_x, tiles_y)
        """
        fov = self.DEFAULT_FOV_MM
        overlap = self.tile_overlap.value() / 100.0
        effective_step = fov * (1 - overlap)

        # Calculate tiles needed in each dimension
        tiles_x = max(1, int((bbox.width / effective_step) + 1))
        tiles_y = max(1, int((bbox.height / effective_step) + 1))

        return tiles_x, tiles_y

    def _calculate_z_positions(self) -> int:
        """Calculate number of Z positions in the stack."""
        z_range_mm = self.z_stack_range.value() * 2  # +/- range
        z_step_mm = self.z_step_size.value() / 1000.0  # convert um to mm
        return max(1, int(z_range_mm / z_step_mm) + 1)

    def _update_scan_info(self):
        """Update the scan info display based on current settings."""
        bbox = self._get_bounding_box()

        if bbox is None:
            self.tiles_label.setText("Tiles: Enter bounding points")
            self.total_tiles_label.setText("Total tiles: --")
            self.est_time_label.setText("Est. time: --")
            self.start_btn.setEnabled(False)
            self.preview_btn.setEnabled(False)
            return

        tiles_x, tiles_y = self._calculate_tile_count(bbox)
        tiles_per_view = tiles_x * tiles_y
        total_tiles = tiles_per_view * 2  # Two rotation angles

        z_positions = self._calculate_z_positions()

        # Estimate time: ~2 seconds per tile (Z-stack + movement)
        # This is a rough estimate
        est_seconds = total_tiles * z_positions * 0.3 + total_tiles * 1.5
        est_minutes = est_seconds / 60.0

        self.tiles_label.setText(f"Tiles: {tiles_x} x {tiles_y} = {tiles_per_view} tiles per view")
        self.total_tiles_label.setText(f"Total tiles: {total_tiles} (2 rotations), {z_positions} Z positions each")

        if est_minutes < 1:
            self.est_time_label.setText(f"Est. time: ~{int(est_seconds)} seconds")
        else:
            self.est_time_label.setText(f"Est. time: ~{est_minutes:.1f} minutes")

        self.start_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)

    def _validate_configuration(self) -> Optional[str]:
        """Validate the current configuration.

        Returns:
            Error message string if invalid, None if valid
        """
        bbox = self._get_bounding_box()
        if bbox is None:
            return "Please enter at least two bounding points"

        if bbox.width < 0.001 and bbox.height < 0.001:
            return "Bounding box is too small (points are nearly identical)"

        return None

    def _get_configuration(self) -> Optional[ScanConfiguration]:
        """Get the current scan configuration.

        Returns:
            ScanConfiguration or None if invalid
        """
        error = self._validate_configuration()
        if error:
            return None

        bbox = self._get_bounding_box()
        if bbox is None:
            return None

        return ScanConfiguration(
            bounding_box=bbox,
            starting_r=self.starting_r.value(),
            z_stack_range=self.z_stack_range.value(),
            z_step_size=self.z_step_size.value(),
            tile_overlap=self.tile_overlap.value(),
            led_name=self.led_info_label.text().replace("LED: ", ""),
            led_intensity=0.0  # TODO: Get actual intensity
        )

    def _on_preview_clicked(self):
        """Handle Preview Grid button click."""
        error = self._validate_configuration()
        if error:
            QMessageBox.warning(self, "Invalid Configuration", error)
            return

        config = self._get_configuration()
        if config is None:
            return

        try:
            from py2flamingo.views.dialogs.led_2d_overview_result import LED2DOverviewResultWindow

            # Create preview window showing empty tile grid
            preview = LED2DOverviewResultWindow(
                config=config,
                preview_mode=True,
                parent=self
            )
            preview.show()

        except ImportError as e:
            self._logger.error(f"Could not import result window: {e}")
            QMessageBox.information(
                self, "Preview",
                f"Preview not yet implemented.\n\n"
                f"Configuration:\n"
                f"- Tiles: {self._calculate_tile_count(config.bounding_box)}\n"
                f"- Z positions: {self._calculate_z_positions()}\n"
                f"- Rotations: {config.starting_r}° and {config.starting_r + 90}°"
            )

    def _on_start_clicked(self):
        """Handle Start Scan button click."""
        error = self._validate_configuration()
        if error:
            QMessageBox.warning(self, "Invalid Configuration", error)
            return

        config = self._get_configuration()
        if config is None:
            return

        # Confirm start
        bbox = config.bounding_box
        tiles_x, tiles_y = self._calculate_tile_count(bbox)
        total_tiles = tiles_x * tiles_y * 2
        z_positions = self._calculate_z_positions()

        reply = QMessageBox.question(
            self,
            "Start Scan",
            f"Ready to start LED 2D Overview scan?\n\n"
            f"Region: X [{bbox.x_min:.2f} to {bbox.x_max:.2f}] mm\n"
            f"        Y [{bbox.y_min:.2f} to {bbox.y_max:.2f}] mm\n"
            f"        Z [{bbox.z_min:.2f} to {bbox.z_max:.2f}] mm\n\n"
            f"Tiles: {tiles_x} x {tiles_y} = {tiles_x * tiles_y} per rotation\n"
            f"Z-stack: {z_positions} positions per tile\n"
            f"Rotations: {config.starting_r}° and {config.starting_r + 90}°\n\n"
            f"Total operations: {total_tiles * z_positions} frame captures\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        self._logger.info(f"Starting LED 2D Overview scan with config: {config}")

        # Emit signal and close dialog
        self.scan_requested.emit(config)

        try:
            from py2flamingo.workflows.led_2d_overview_workflow import LED2DOverviewWorkflow

            workflow = LED2DOverviewWorkflow(
                app=self._app,
                config=config,
                parent=self
            )

            # Close this dialog and run workflow
            self.accept()
            workflow.start()

        except ImportError as e:
            self._logger.error(f"Could not import workflow: {e}")
            QMessageBox.information(
                self, "Scan",
                "Scan workflow not yet implemented.\n\n"
                "The LED 2D Overview feature is under development."
            )
        except Exception as e:
            self._logger.error(f"Error starting scan: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to start scan: {e}")
