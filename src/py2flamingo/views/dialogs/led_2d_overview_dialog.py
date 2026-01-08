"""LED 2D Overview Dialog.

Configuration dialog for the LED 2D Overview extension that creates
2D overview maps at two rotation angles.
"""

import logging
from typing import Optional, List, Tuple
from dataclasses import dataclass

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QPushButton, QDoubleSpinBox, QComboBox, QCheckBox,
    QMessageBox, QSizePolicy
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QShowEvent, QCloseEvent, QHideEvent

from py2flamingo.views.colors import WARNING_COLOR, ERROR_COLOR


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
    led_name: str
    led_intensity: float
    z_step_size: float = 0.050  # mm (50 um default)
    use_focus_stacking: bool = False  # If True, use full focus stacking (TODO)
    fast_mode: bool = True  # If True, use continuous scanning (no Z-stacks, much faster)


class LED2DOverviewDialog(QDialog):
    """Configuration dialog for LED 2D Overview scans.

    This is a non-modal dialog that allows users to:
    - Define 2-3 bounding points (A, B, optional C)
    - Load saved position presets into points
    - Set starting rotation angle
    - Preview the tile grid layout
    - Start the scan

    The scan creates two 2D overview maps at R and R+90 degrees.
    """

    # Emitted when user requests to start scan
    scan_requested = pyqtSignal(object)  # ScanConfiguration

    # No hardcoded FOV - must be queried from camera to avoid equipment damage

    def __init__(self, app, parent=None):
        """Initialize the dialog.

        Args:
            app: FlamingoApplication instance for accessing sample_view etc.
            parent: Parent widget
        """
        super().__init__(parent)
        self._app = app
        self._logger = logging.getLogger(__name__)
        self._geometry_restored = False

        # Stage limits (will be loaded from settings)
        self._stage_limits = {
            'x': {'min': 0.0, 'max': 26.0},
            'y': {'min': 0.0, 'max': 26.0},
            'z': {'min': 0.0, 'max': 26.0},
            'r': {'min': -720.0, 'max': 720.0}
        }

        self.setWindowTitle("LED 2D Overview")
        self.setMinimumWidth(520)
        # Non-modal so user can interact with Sample View and other dialogs
        self.setModal(False)

        self._load_stage_limits()
        self._setup_ui()
        self._load_current_settings()
        self._refresh_presets()
        self._update_scan_info()

    def _load_stage_limits(self):
        """Load stage limits from microscope settings."""
        try:
            if self._app and hasattr(self._app, 'microscope_settings'):
                limits = self._app.microscope_settings.get_stage_limits()
                self._stage_limits = limits
                self._logger.info(f"Loaded stage limits: X[{limits['x']['min']}-{limits['x']['max']}], "
                                 f"Y[{limits['y']['min']}-{limits['y']['max']}], "
                                 f"Z[{limits['z']['min']}-{limits['z']['max']}]")
        except Exception as e:
            self._logger.warning(f"Could not load stage limits: {e}")

    def _setup_ui(self):
        """Create the dialog UI."""
        layout = QVBoxLayout()
        layout.setSpacing(12)

        # Bounding Points group
        self.points_group = self._create_points_group()
        layout.addWidget(self.points_group)

        # Scan Settings group
        settings_group = self._create_settings_group()
        layout.addWidget(settings_group)

        # Imaging (LED) display group
        self.imaging_group = self._create_imaging_group()
        layout.addWidget(self.imaging_group)

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
        # Set minimum width to fit "In Progress... 100%" without layout shifts
        self.start_btn.setMinimumWidth(160)
        self.start_btn.clicked.connect(self._on_start_clicked)
        button_layout.addWidget(self.start_btn)

        self.cancel_btn = QPushButton("Cancel Scan")
        self.cancel_btn.setToolTip("Cancel the current scan")
        self.cancel_btn.setStyleSheet(
            f"QPushButton {{ background-color: {ERROR_COLOR}; color: white; font-weight: bold; "
            "padding: 8px 16px; }"
        )
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        self.cancel_btn.setVisible(False)  # Hidden until scan starts
        button_layout.addWidget(self.cancel_btn)

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        button_layout.addWidget(self.close_btn)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def _create_points_group(self) -> QGroupBox:
        """Create the bounding points input group."""
        group = QGroupBox("Bounding Points")
        layout = QVBoxLayout()

        # Preset loading row
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("Load preset into:"))

        self.preset_target_combo = QComboBox()
        self.preset_target_combo.addItem("Point A")
        self.preset_target_combo.addItem("Point B")
        self.preset_target_combo.addItem("Point C")
        self.preset_target_combo.setToolTip("Select which point to load the preset into")
        preset_layout.addWidget(self.preset_target_combo)

        self.preset_combo = QComboBox()
        self.preset_combo.setToolTip("Select a saved position preset")
        self.preset_combo.setMinimumWidth(150)
        preset_layout.addWidget(self.preset_combo)

        self.load_preset_btn = QPushButton("Load")
        self.load_preset_btn.setToolTip("Load selected preset into target point")
        self.load_preset_btn.clicked.connect(self._on_load_preset)
        preset_layout.addWidget(self.load_preset_btn)

        self.refresh_presets_btn = QPushButton("Refresh")
        self.refresh_presets_btn.setToolTip("Refresh preset list")
        self.refresh_presets_btn.clicked.connect(self._refresh_presets)
        preset_layout.addWidget(self.refresh_presets_btn)

        preset_layout.addStretch()
        layout.addLayout(preset_layout)

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
        self.point_a_x = self._create_coord_spinbox('x')
        self.point_a_y = self._create_coord_spinbox('y')
        self.point_a_z = self._create_coord_spinbox('z')
        self.point_a_btn = QPushButton("Get Pos")
        self.point_a_btn.setToolTip("Get current stage position")
        self.point_a_btn.clicked.connect(lambda: self._get_current_position('A'))
        grid.addWidget(self.point_a_label, 1, 0)
        grid.addWidget(self.point_a_x, 1, 1)
        grid.addWidget(self.point_a_y, 1, 2)
        grid.addWidget(self.point_a_z, 1, 3)
        grid.addWidget(self.point_a_btn, 1, 4)

        # Point B
        self.point_b_label = QLabel("Point B:")
        self.point_b_x = self._create_coord_spinbox('x')
        self.point_b_y = self._create_coord_spinbox('y')
        self.point_b_z = self._create_coord_spinbox('z')
        self.point_b_btn = QPushButton("Get Pos")
        self.point_b_btn.setToolTip("Get current stage position")
        self.point_b_btn.clicked.connect(lambda: self._get_current_position('B'))
        grid.addWidget(self.point_b_label, 2, 0)
        grid.addWidget(self.point_b_x, 2, 1)
        grid.addWidget(self.point_b_y, 2, 2)
        grid.addWidget(self.point_b_z, 2, 3)
        grid.addWidget(self.point_b_btn, 2, 4)

        # Point C (optional)
        self.point_c_label = QLabel("Point C:")
        self.point_c_x = self._create_coord_spinbox('x', optional=True)
        self.point_c_y = self._create_coord_spinbox('y', optional=True)
        self.point_c_z = self._create_coord_spinbox('z', optional=True)
        self.point_c_btn = QPushButton("Get Pos")
        self.point_c_btn.setToolTip("Get current stage position")
        self.point_c_clear = QPushButton("Clear")
        self.point_c_clear.setToolTip("Clear Point C")
        self.point_c_btn.clicked.connect(lambda: self._get_current_position('C'))
        self.point_c_clear.clicked.connect(self._clear_point_c)

        c_btn_layout = QHBoxLayout()
        c_btn_layout.setSpacing(4)
        c_btn_layout.addWidget(self.point_c_btn)
        c_btn_layout.addWidget(self.point_c_clear)

        grid.addWidget(self.point_c_label, 3, 0)
        grid.addWidget(self.point_c_x, 3, 1)
        grid.addWidget(self.point_c_y, 3, 2)
        grid.addWidget(self.point_c_z, 3, 3)
        grid.addLayout(c_btn_layout, 3, 4)

        layout.addLayout(grid)

        # Note about Point C
        note = QLabel("(Point C is optional - expands bounding box if provided)")
        note.setStyleSheet("color: gray; font-style: italic; font-size: 10px;")
        layout.addWidget(note)

        group.setLayout(layout)
        return group

    def _create_coord_spinbox(self, axis: str, optional: bool = False) -> QDoubleSpinBox:
        """Create a coordinate spinbox with stage limits.

        Args:
            axis: 'x', 'y', or 'z'
            optional: If True, allow special "not set" value
        """
        spinbox = QDoubleSpinBox()

        limits = self._stage_limits.get(axis, {'min': 0.0, 'max': 26.0})
        min_val = limits['min']
        max_val = limits['max']

        if optional:
            # For optional fields, use a value below min to indicate "not set"
            spinbox.setRange(min_val - 1, max_val)
            spinbox.setSpecialValueText("--")
            spinbox.setValue(min_val - 1)  # Start as "not set"
        else:
            spinbox.setRange(min_val, max_val)
            spinbox.setValue(min_val)

        spinbox.setDecimals(3)
        spinbox.setSingleStep(0.1)
        spinbox.setSuffix(" mm")
        spinbox.valueChanged.connect(self._update_scan_info)

        return spinbox

    def _create_settings_group(self) -> QGroupBox:
        """Create the scan settings group."""
        group = QGroupBox("Scan Settings")
        layout = QGridLayout()
        layout.setSpacing(8)

        # Starting R
        layout.addWidget(QLabel("Starting R:"), 0, 0)
        self.starting_r = QDoubleSpinBox()
        r_limits = self._stage_limits.get('r', {'min': -720.0, 'max': 720.0})
        self.starting_r.setRange(r_limits['min'], r_limits['max'])
        self.starting_r.setDecimals(1)
        self.starting_r.setSingleStep(1.0)
        self.starting_r.setSuffix("°")
        self.starting_r.setValue(0.0)
        self.starting_r.setToolTip("First rotation angle (second will be +90°)")
        layout.addWidget(self.starting_r, 0, 1)

        # Get current R button
        self.get_r_btn = QPushButton("Get Current R")
        self.get_r_btn.setToolTip("Set starting R to current rotation")
        self.get_r_btn.clicked.connect(self._get_current_r)
        layout.addWidget(self.get_r_btn, 0, 2)

        # Z step size - larger default for fast overview
        layout.addWidget(QLabel("Z Step Size:"), 1, 0)
        self.z_step_size = QDoubleSpinBox()
        self.z_step_size.setRange(0.050, 1.000)
        self.z_step_size.setDecimals(3)
        self.z_step_size.setSingleStep(0.050)
        self.z_step_size.setSuffix(" mm")
        self.z_step_size.setValue(0.250)  # 250µm default for fast scan (~6 Z planes)
        self.z_step_size.setToolTip("Z step size for focus search (250 µm default for speed)")
        self.z_step_size.valueChanged.connect(self._update_scan_info)
        layout.addWidget(self.z_step_size, 1, 1)

        # Focus stacking checkbox
        self.focus_stacking_checkbox = QCheckBox("Use focus stacking (slower)")
        self.focus_stacking_checkbox.setToolTip(
            "If checked, combines best-focused regions from all Z planes.\n"
            "If unchecked, uses single best-focused frame per tile."
        )
        self.focus_stacking_checkbox.setChecked(False)
        layout.addWidget(self.focus_stacking_checkbox, 2, 0, 1, 3)

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
        refresh_btn.setToolTip(
            "Update LED settings from Sample View.\n"
            "Start Live View with an LED enabled first, then click to detect settings."
        )
        refresh_btn.clicked.connect(self._load_current_settings)
        refresh_btn.setMaximumWidth(200)
        layout.addWidget(refresh_btn)

        group.setLayout(layout)
        return group

    def _create_info_group(self) -> QGroupBox:
        """Create the scan info display group."""
        group = QGroupBox("Scan Info")
        layout = QVBoxLayout()

        # Warning about Live View requirement
        self.live_view_warning = QLabel(
            "Note: Start Live View with LED enabled, then click\n"
            "'Refresh from Sample View' to detect LED settings."
        )
        self.live_view_warning.setStyleSheet(
            "color: #856404; background-color: #fff3cd; "
            "border: 1px solid #ffc107; border-radius: 4px; "
            "padding: 6px; margin-bottom: 8px;"
        )
        layout.addWidget(self.live_view_warning)

        self.tiles_label = QLabel("Tiles: calculating...")
        self.total_tiles_label = QLabel("Total tiles: calculating...")
        self.z_planes_label = QLabel("Z planes: calculating...")
        self.region_label = QLabel("Region: --")

        layout.addWidget(self.tiles_label)
        layout.addWidget(self.total_tiles_label)
        layout.addWidget(self.z_planes_label)
        layout.addWidget(self.region_label)

        group.setLayout(layout)
        return group

    def _refresh_presets(self):
        """Refresh the preset combo box from PositionPresetService."""
        self.preset_combo.clear()
        self.preset_combo.addItem("-- Select preset --")

        try:
            from py2flamingo.services.position_preset_service import PositionPresetService
            preset_service = PositionPresetService()
            preset_names = preset_service.get_preset_names()

            for name in preset_names:
                self.preset_combo.addItem(name)

            self._logger.info(f"Loaded {len(preset_names)} position presets")
        except Exception as e:
            self._logger.error(f"Error loading presets: {e}")

    def _on_load_preset(self):
        """Load selected preset into target point."""
        preset_name = self.preset_combo.currentText()
        if preset_name == "-- Select preset --":
            return

        target = self.preset_target_combo.currentText()  # "Point A", "Point B", or "Point C"

        try:
            from py2flamingo.services.position_preset_service import PositionPresetService
            preset_service = PositionPresetService()
            preset = preset_service.get_preset(preset_name)

            if preset is None:
                QMessageBox.warning(self, "Error", f"Preset '{preset_name}' not found")
                return

            # Load into the appropriate point
            if target == "Point A":
                self.point_a_x.setValue(preset.x)
                self.point_a_y.setValue(preset.y)
                self.point_a_z.setValue(preset.z)
            elif target == "Point B":
                self.point_b_x.setValue(preset.x)
                self.point_b_y.setValue(preset.y)
                self.point_b_z.setValue(preset.z)
            elif target == "Point C":
                self.point_c_x.setValue(preset.x)
                self.point_c_y.setValue(preset.y)
                self.point_c_z.setValue(preset.z)

            self._logger.info(f"Loaded preset '{preset_name}' into {target}")
            self._update_scan_info()

        except Exception as e:
            self._logger.error(f"Error loading preset: {e}")
            QMessageBox.warning(self, "Error", f"Failed to load preset: {e}")

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
            pos = movement_controller.get_position()
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

    def _get_current_r(self):
        """Get current rotation and set starting R."""
        if not self._app or not self._app.sample_view:
            QMessageBox.warning(self, "Error", "Sample View not available")
            return

        movement_controller = self._app.sample_view.movement_controller
        if not movement_controller:
            QMessageBox.warning(self, "Error", "Movement controller not available")
            return

        try:
            pos = movement_controller.get_position()
            if pos is None:
                QMessageBox.warning(self, "Error", "Could not read stage position")
                return

            self.starting_r.setValue(pos.r)
            self._logger.info(f"Set Starting R to {pos.r:.1f}°")

        except Exception as e:
            self._logger.error(f"Error getting rotation: {e}")
            QMessageBox.warning(self, "Error", f"Failed to get rotation: {e}")

    def _clear_point_c(self):
        """Clear Point C values."""
        # Set to special "not set" value (min - 1)
        x_min = self._stage_limits['x']['min']
        y_min = self._stage_limits['y']['min']
        z_min = self._stage_limits['z']['min']
        self.point_c_x.setValue(x_min - 1)
        self.point_c_y.setValue(y_min - 1)
        self.point_c_z.setValue(z_min - 1)
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
                self.intensity_info_label.setText("Intensity: (using current setting)")
            else:
                self.led_info_label.setText("LED: Panel not available")
                self.intensity_info_label.setText("Intensity: --")
        except Exception as e:
            self._logger.error(f"Error loading LED settings: {e}")
            self.led_info_label.setText(f"LED: Error - {e}")

        # Update start button state after LED info changes
        self._update_start_button_state()

    def _is_point_set(self, x_spinbox: QDoubleSpinBox, y_spinbox: QDoubleSpinBox, z_spinbox: QDoubleSpinBox) -> bool:
        """Check if a point has valid values (not the "not set" special value)."""
        x_min = self._stage_limits['x']['min']
        y_min = self._stage_limits['y']['min']
        z_min = self._stage_limits['z']['min']

        # If any coordinate is at the special "not set" value (min - 1), point is not set
        return (x_spinbox.value() >= x_min and
                y_spinbox.value() >= y_min and
                z_spinbox.value() >= z_min)

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

        # Point C (optional - only include if set)
        if self._is_point_set(self.point_c_x, self.point_c_y, self.point_c_z):
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

    def _get_actual_fov(self) -> Optional[float]:
        """Get actual field of view from camera.

        Returns:
            FOV in mm, or None if it cannot be determined
        """
        try:
            if not self._app or not hasattr(self._app, 'camera_service') or not self._app.camera_service:
                return None

            pixel_size_mm = self._app.camera_service.get_pixel_field_of_view()
            width, height = self._app.camera_service.get_image_size()
            frame_size = min(width, height)

            if frame_size <= 0 or pixel_size_mm <= 0:
                return None

            fov = pixel_size_mm * frame_size
            if fov < 0.01 or fov > 50:
                return None

            return fov
        except Exception:
            return None

    def _calculate_tile_count(self, bbox: BoundingBox) -> Optional[Tuple[int, int]]:
        """Calculate number of tiles needed (no overlap).

        Args:
            bbox: Bounding box for scan region

        Returns:
            Tuple of (tiles_x, tiles_y), or None if FOV is unknown
        """
        fov = self._get_actual_fov()
        if fov is None:
            return None

        # No overlap - tiles are adjacent
        tiles_x = max(1, int((bbox.width / fov) + 1))
        tiles_y = max(1, int((bbox.height / fov) + 1))

        return tiles_x, tiles_y

    def _get_tip_position(self):
        """Get sample holder tip position from presets.

        Returns:
            Tuple of (x, z) or None if not calibrated
        """
        try:
            from py2flamingo.services.position_preset_service import PositionPresetService
            preset_service = PositionPresetService()
            preset = preset_service.get_preset("Tip of sample mount")
            if preset:
                return (preset.x, preset.z)
            return None
        except Exception:
            return None

    def _rotate_point_90(self, x: float, z: float, tip_x: float, tip_z: float):
        """Rotate a point 90° around the tip position."""
        x_new = tip_x + (z - tip_z)
        z_new = tip_z - (x - tip_x)
        return (x_new, z_new)

    def _update_scan_info(self):
        """Update the scan info display based on current settings.

        Shows tile counts for both rotations if tip is calibrated:
        - R: Tiles across X-Y, Z-stack through Z
        - R+90: Transformed bbox around tip, different tile/Z-stack ranges
        """
        bbox = self._get_bounding_box()

        if bbox is None:
            self.tiles_label.setText("Tiles: Enter bounding points")
            self.total_tiles_label.setText("Total tiles: --")
            self.z_planes_label.setText("Z planes: --")
            self.region_label.setText("Region: --")
            self.start_btn.setEnabled(False)
            self.preview_btn.setEnabled(False)
            return

        # Get actual FOV from camera - required for safe operation
        fov = self._get_actual_fov()
        if fov is None:
            self.tiles_label.setText("Tiles: ⚠ FOV unknown - camera not ready")
            self.total_tiles_label.setText("Total tiles: Cannot calculate")
            self.z_planes_label.setText("Z planes: --")
            self.region_label.setText(
                f"Region: X={bbox.width:.2f}mm, Y={bbox.height:.2f}mm, Z={bbox.z_max-bbox.z_min:.2f}mm"
            )
            self.start_btn.setEnabled(False)
            self.preview_btn.setEnabled(False)
            return

        z_step = self.z_step_size.value()

        # Rotation 1 (R): tile across X-Y, Z-stack through Z
        tiles_x_r1 = max(1, int((bbox.width / fov) + 1))
        tiles_y_r1 = max(1, int((bbox.height / fov) + 1))
        tiles_r1 = tiles_x_r1 * tiles_y_r1
        z_depth_r1 = bbox.z_max - bbox.z_min
        z_planes_r1 = max(1, int(z_depth_r1 / z_step) + 1)

        # Check if tip is calibrated for second rotation
        tip_pos = self._get_tip_position()

        if tip_pos is not None:
            tip_x, tip_z = tip_pos

            # Transform bbox corners for rotated view
            corners = [
                (bbox.x_min, bbox.z_min),
                (bbox.x_min, bbox.z_max),
                (bbox.x_max, bbox.z_min),
                (bbox.x_max, bbox.z_max),
            ]
            rotated = [self._rotate_point_90(x, z, tip_x, tip_z) for x, z in corners]
            new_x_min = min(c[0] for c in rotated)
            new_x_max = max(c[0] for c in rotated)
            new_z_min = min(c[1] for c in rotated)
            new_z_max = max(c[1] for c in rotated)

            # Rotation 2 (R+90): transformed bbox
            tiles_x_r2 = max(1, int(((new_x_max - new_x_min) / fov) + 1))
            tiles_y_r2 = tiles_y_r1  # Y unchanged
            tiles_r2 = tiles_x_r2 * tiles_y_r2
            z_depth_r2 = new_z_max - new_z_min
            z_planes_r2 = max(1, int(z_depth_r2 / z_step) + 1)

            total_tiles = tiles_r1 + tiles_r2
            total_frames = (tiles_r1 * z_planes_r1) + (tiles_r2 * z_planes_r2)

            self.tiles_label.setText(
                f"R: {tiles_x_r1}×{tiles_y_r1}={tiles_r1} tiles  |  "
                f"R+90: {tiles_x_r2}×{tiles_y_r2}={tiles_r2} tiles"
            )
            self.total_tiles_label.setText(f"Total: {total_tiles} tiles, {total_frames} frames (2 rotations)")
            self.z_planes_label.setText(
                f"Z planes: R={z_planes_r1} ({z_depth_r1:.2f}mm), R+90={z_planes_r2} ({z_depth_r2:.2f}mm)"
            )
        else:
            # No tip calibrated - single rotation only
            total_frames = tiles_r1 * z_planes_r1

            self.tiles_label.setText(
                f"R: {tiles_x_r1}×{tiles_y_r1}={tiles_r1} tiles, {z_planes_r1} Z planes"
            )
            self.total_tiles_label.setText(
                f"Total: {tiles_r1} tiles, {total_frames} frames (1 rotation only)"
            )
            self.z_planes_label.setText(
                f"⚠ Tip not calibrated - use Tools > Calibrate for R+90 view"
            )

        self.region_label.setText(
            f"Bbox: X [{bbox.x_min:.2f}, {bbox.x_max:.2f}], "
            f"Y [{bbox.y_min:.2f}, {bbox.y_max:.2f}], "
            f"Z [{bbox.z_min:.2f}, {bbox.z_max:.2f}] mm"
        )

        # Update button states based on full validation
        self._update_start_button_state()
        self.preview_btn.setEnabled(True)  # Preview doesn't need LED/live view

    def _validate_configuration(self) -> Optional[str]:
        """Validate the current configuration.

        Returns:
            Error message string if invalid, None if valid
        """
        # Check if coordinates are within valid stage bounds
        if not self._are_coordinates_valid():
            x_limits = self._stage_limits['x']
            y_limits = self._stage_limits['y']
            z_limits = self._stage_limits['z']
            return (f"One or more coordinates are outside valid stage bounds. "
                   f"Valid ranges: X[{x_limits['min']:.1f}-{x_limits['max']:.1f}], "
                   f"Y[{y_limits['min']:.1f}-{y_limits['max']:.1f}], "
                   f"Z[{z_limits['min']:.1f}-{z_limits['max']:.1f}]")

        # Check bounding box
        bbox = self._get_bounding_box()
        if bbox is None:
            return "Please enter at least two bounding points"

        if bbox.width < 0.001 and bbox.height < 0.001:
            return "Bounding box is too small (points are nearly identical)"

        # Check LED source
        led_text = self.led_info_label.text().replace("LED: ", "").lower()
        if led_text in ('none', '--', 'sample view not open', 'panel not available'):
            return "No light source selected. Please select an LED in the Sample View."

        # Check if live viewer is active
        if self._app and self._app.sample_view:
            camera_controller = self._app.sample_view.camera_controller
            if camera_controller and not camera_controller.is_live_view_active():
                return "Live viewer is not active. Please start the live viewer in Sample View."

        return None

    def _update_start_button_state(self):
        """Update Start button enabled state based on validation."""
        error = self._validate_configuration()
        self.start_btn.setEnabled(error is None)
        if error:
            self.start_btn.setToolTip(error)
        else:
            self.start_btn.setToolTip("Start the LED 2D Overview scan")

        # Update visual highlighting on incomplete sections
        self._update_section_highlighting()

    def _are_coordinates_valid(self) -> bool:
        """Check if Point A and Point B coordinates are within valid stage bounds.

        Returns:
            False if any coordinate in Point A or Point B is outside the valid range
        """
        # Check Point A
        x_limits = self._stage_limits['x']
        y_limits = self._stage_limits['y']
        z_limits = self._stage_limits['z']

        point_a_x_valid = x_limits['min'] <= self.point_a_x.value() <= x_limits['max']
        point_a_y_valid = y_limits['min'] <= self.point_a_y.value() <= y_limits['max']
        point_a_z_valid = z_limits['min'] <= self.point_a_z.value() <= z_limits['max']
        point_a_valid = point_a_x_valid and point_a_y_valid and point_a_z_valid

        point_b_x_valid = x_limits['min'] <= self.point_b_x.value() <= x_limits['max']
        point_b_y_valid = y_limits['min'] <= self.point_b_y.value() <= y_limits['max']
        point_b_z_valid = z_limits['min'] <= self.point_b_z.value() <= z_limits['max']
        point_b_valid = point_b_x_valid and point_b_y_valid and point_b_z_valid

        # Log invalid coordinates for debugging
        if not point_a_valid:
            self._logger.warning(
                f"Point A has invalid coordinates: "
                f"X={self.point_a_x.value():.3f} (valid: {x_limits['min']}-{x_limits['max']}), "
                f"Y={self.point_a_y.value():.3f} (valid: {y_limits['min']}-{y_limits['max']}), "
                f"Z={self.point_a_z.value():.3f} (valid: {z_limits['min']}-{z_limits['max']})"
            )
        if not point_b_valid:
            self._logger.warning(
                f"Point B has invalid coordinates: "
                f"X={self.point_b_x.value():.3f} (valid: {x_limits['min']}-{x_limits['max']}), "
                f"Y={self.point_b_y.value():.3f} (valid: {y_limits['min']}-{y_limits['max']}), "
                f"Z={self.point_b_z.value():.3f} (valid: {z_limits['min']}-{z_limits['max']})"
            )

        return point_a_valid and point_b_valid

    def _update_section_highlighting(self):
        """Update visual highlighting on sections that need attention.

        Uses subtle border to indicate incomplete sections without being overwhelming.
        """
        from py2flamingo.views.colors import WARNING_COLOR

        # Check bounding points - validate coordinates AND bounding box
        bbox = self._get_bounding_box()
        coords_valid = self._are_coordinates_valid()
        points_incomplete = (
            not coords_valid or
            bbox is None or
            (bbox.width < 0.001 and bbox.height < 0.001)
        )

        if points_incomplete:
            self.points_group.setStyleSheet(
                f"QGroupBox {{ border: 2px solid {WARNING_COLOR}; border-radius: 4px; "
                "padding-top: 10px; margin-top: 6px; }}"
                f"QGroupBox::title {{ color: {WARNING_COLOR}; }}"
            )
        else:
            self.points_group.setStyleSheet("")

        # Check imaging (LED + Live View)
        led_text = self.led_info_label.text().replace("LED: ", "").lower()
        led_not_selected = led_text in ('none', '--', 'sample view not open', 'panel not available')

        live_view_inactive = False
        if self._app and self._app.sample_view:
            sample_view = self._app.sample_view
            if sample_view.camera_controller:
                live_view_inactive = not sample_view.camera_controller.is_live_view_active()

        imaging_incomplete = led_not_selected or live_view_inactive

        if imaging_incomplete:
            self.imaging_group.setStyleSheet(
                f"QGroupBox {{ border: 2px solid {WARNING_COLOR}; border-radius: 4px; "
                "padding-top: 10px; margin-top: 6px; }}"
                f"QGroupBox::title {{ color: {WARNING_COLOR}; }}"
            )
        else:
            self.imaging_group.setStyleSheet("")

    def _set_scan_in_progress(self, in_progress: bool, percent: int = 0) -> None:
        """Update button appearance to reflect scan state.

        Args:
            in_progress: True if scan is running, False when complete
            percent: Progress percentage (0-100) to display when in_progress=True
        """
        if in_progress:
            self.start_btn.setText(f"In Progress... {percent}%")
            self.start_btn.setEnabled(False)
            self.start_btn.setStyleSheet(
                f"QPushButton {{ background-color: {WARNING_COLOR}; color: black; "
                "font-weight: bold; padding: 8px 16px; }}"
            )
            self.start_btn.setToolTip(f"Scan is in progress... {percent}% complete")
            self.cancel_btn.setVisible(True)
            self.close_btn.setEnabled(False)
        else:
            self.start_btn.setText("Start Scan")
            self.start_btn.setStyleSheet(
                "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; "
                "padding: 8px 16px; }"
                "QPushButton:disabled { background-color: #ccc; }"
            )
            # Re-validate to set proper enabled state and tooltip
            self._update_start_button_state()
            self.cancel_btn.setVisible(False)
            self.close_btn.setEnabled(True)

    def _on_tile_completed(self, rotation_idx: int, tile_idx: int, total_tiles: int) -> None:
        """Handle tile completion - update progress display.

        Args:
            rotation_idx: Current rotation index (0 or 1)
            tile_idx: Current tile index within rotation
            total_tiles: Total tiles per rotation
        """
        # Get actual number of rotations from workflow (1 if tip not calibrated, 2 otherwise)
        num_rotations = 2  # Default assumption
        if self._workflow and hasattr(self._workflow, '_rotation_angles'):
            num_rotations = len(self._workflow._rotation_angles)

        # Calculate overall progress across all rotations
        # rotation_idx is 0-based, tile_idx is 0-based within rotation
        tiles_done = rotation_idx * total_tiles + tile_idx + 1
        total_all_rotations = total_tiles * num_rotations
        percent = int((tiles_done / total_all_rotations) * 100)

        self._logger.info(f"Tile completed: rotation {rotation_idx}, tile {tile_idx}/{total_tiles}, "
                         f"progress: {tiles_done}/{total_all_rotations} = {percent}%")

        self._set_scan_in_progress(True, percent)

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
            led_name=self.led_info_label.text().replace("LED: ", ""),
            led_intensity=0.0,  # Using current setting
            z_step_size=self.z_step_size.value(),
            use_focus_stacking=self.focus_stacking_checkbox.isChecked()
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
            # Keep reference to prevent garbage collection
            self._preview_window = LED2DOverviewResultWindow(
                config=config,
                preview_mode=True,
                app=self._app,
                parent=None  # Independent window
            )
            self._preview_window.show()

        except ImportError as e:
            self._logger.error(f"Could not import result window: {e}")
            bbox = config.bounding_box
            tiles_x, tiles_y = self._calculate_tile_count(bbox)
            QMessageBox.information(
                self, "Preview",
                f"Preview not yet implemented.\n\n"
                f"Configuration:\n"
                f"- Tiles: {tiles_x} x {tiles_y}\n"
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
        tile_count = self._calculate_tile_count(bbox)
        if tile_count is None:
            QMessageBox.critical(
                self,
                "Cannot Start Scan",
                "Field of View (FOV) could not be determined from camera.\n\n"
                "The camera returned invalid image dimensions (0x0).\n"
                "Please ensure the camera is properly connected and configured."
            )
            return
        tiles_x, tiles_y = tile_count
        total_tiles = tiles_x * tiles_y * 2

        reply = QMessageBox.question(
            self,
            "Start Scan",
            f"Ready to start LED 2D Overview scan?\n\n"
            f"Region: X [{bbox.x_min:.2f} to {bbox.x_max:.2f}] mm\n"
            f"        Y [{bbox.y_min:.2f} to {bbox.y_max:.2f}] mm\n"
            f"        Z [{bbox.z_min:.2f} to {bbox.z_max:.2f}] mm\n\n"
            f"Tiles: {tiles_x} x {tiles_y} = {tiles_x * tiles_y} per rotation\n"
            f"Rotations: {config.starting_r}° and {config.starting_r + 90}°\n\n"
            f"Total: {total_tiles} tiles\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        self._logger.info(f"Starting LED 2D Overview scan with config: {config}")

        # Emit signal
        self.scan_requested.emit(config)

        try:
            from py2flamingo.workflows.led_2d_overview_workflow import LED2DOverviewWorkflow

            # Keep reference to prevent garbage collection
            self._workflow = LED2DOverviewWorkflow(
                app=self._app,
                config=config,
                parent=self  # Parent to dialog so it stays alive
            )

            # Connect workflow completion signals to stop live view
            self._workflow.scan_completed.connect(self._on_workflow_completed)
            self._workflow.scan_cancelled.connect(self._on_workflow_completed)
            self._workflow.scan_error.connect(self._on_workflow_error)
            self._workflow.tile_completed.connect(self._on_tile_completed)
            self._logger.info("Connected to workflow signals (scan_completed, scan_cancelled, scan_error, tile_completed)")

            # Don't close dialog - user might want to run again
            self._workflow.start()

            # Update button to show scan is in progress
            self._set_scan_in_progress(True)

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

    def _on_workflow_completed(self, *args) -> None:
        """Handle workflow completion (success or cancel) - stop live view."""
        self._logger.info("Workflow completed - stopping live view in SampleView")
        self._set_scan_in_progress(False)
        self._stop_sample_view_live()

    def _on_workflow_error(self, error_msg: str) -> None:
        """Handle workflow error - stop live view."""
        self._logger.error(f"Workflow error: {error_msg} - stopping live view")
        self._set_scan_in_progress(False)
        self._stop_sample_view_live()

    def _on_cancel_clicked(self) -> None:
        """Handle Cancel Scan button click."""
        if self._workflow:
            self._logger.info("User requested scan cancellation")
            self._workflow.cancel()
            # _on_workflow_completed will be called via scan_cancelled signal

    def _stop_sample_view_live(self) -> None:
        """Stop the live view in SampleView and update button state."""
        if not self._app or not self._app.sample_view:
            self._logger.warning("Cannot stop live view - SampleView not available")
            return

        sample_view = self._app.sample_view

        # Stop camera live view
        if sample_view.camera_controller:
            try:
                sample_view.camera_controller.stop_live_view()
                self._logger.info("Camera live view stopped")
            except Exception as e:
                self._logger.error(f"Error stopping camera: {e}")

        # Update SampleView button state
        sample_view._update_live_view_state()

    # ========== Window Events ==========

    def showEvent(self, event: QShowEvent) -> None:
        """Handle dialog show event - restore geometry on first show."""
        super().showEvent(event)

        # Restore geometry on first show
        if not self._geometry_restored and self._app and hasattr(self._app, 'geometry_manager'):
            geometry_manager = self._app.geometry_manager
            if geometry_manager:
                geometry_manager.restore_geometry("LED2DOverviewDialog", self)
                self._geometry_restored = True

    def hideEvent(self, event: QHideEvent) -> None:
        """Handle dialog hide event - save geometry when hidden."""
        # Save geometry when hiding
        if self._app and hasattr(self._app, 'geometry_manager'):
            geometry_manager = self._app.geometry_manager
            if geometry_manager:
                geometry_manager.save_geometry("LED2DOverviewDialog", self)

        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Handle dialog close event - save geometry."""
        # Save geometry when closing
        if self._app and hasattr(self._app, 'geometry_manager'):
            geometry_manager = self._app.geometry_manager
            if geometry_manager:
                geometry_manager.save_geometry("LED2DOverviewDialog", self)

        super().closeEvent(event)
