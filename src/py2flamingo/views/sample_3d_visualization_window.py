"""
3D Sample Chamber Visualization Window with rotation-aware data accumulation.
"""

import numpy as np
import time
import yaml
from pathlib import Path
from typing import Optional, Dict, Tuple
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QSlider, QCheckBox, QComboBox, QSpinBox,
    QSplitter, QTabWidget, QGridLayout, QMessageBox
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QMutex, QMutexLocker
from PyQt5.QtGui import QIcon
import logging

try:
    import napari
    from napari.qt import thread_worker
    NAPARI_AVAILABLE = True
except ImportError:
    NAPARI_AVAILABLE = False
    napari = None
    thread_worker = None

import sys
sys.path.append(str(Path(__file__).parent.parent))

from visualization.dual_resolution_storage import DualResolutionVoxelStorage, DualResolutionConfig
from visualization.coordinate_transforms import CoordinateTransformer, PhysicalToNapariMapper
from visualization.sparse_volume_renderer import SparseVolumeRenderer
from py2flamingo.services.position_preset_service import PositionPresetService

logger = logging.getLogger(__name__)


class Sample3DVisualizationWindow(QWidget):
    """
    3D visualization window for sample chamber with rotation-aware data accumulation.

    Features:
    - Separate high-resolution storage and low-resolution display
    - Real-time rotation transformations
    - Multi-channel support with independent controls
    - Persistent data accumulation during scanning
    """

    # Signals
    rotation_changed = pyqtSignal(dict)  # Emits {'rx': float, 'ry': float, 'rz': float}
    x_position_changed = pyqtSignal(float)
    y_position_changed = pyqtSignal(float)
    z_position_changed = pyqtSignal(float)
    channel_visibility_changed = pyqtSignal(int, bool)
    stage_position_update_signal = pyqtSignal(object)  # Thread-safe stage position updates

    def __init__(self, movement_controller=None, camera_controller=None, laser_led_controller=None, parent=None):
        super().__init__(parent)

        self.movement_controller = movement_controller
        self.camera_controller = camera_controller
        self.laser_led_controller = laser_led_controller

        # Load configuration
        self.config = self._load_config()

        # Initialize physical to napari coordinate mapper FIRST
        # (storage system depends on this)
        mapper_config = {
            'x_range_mm': self.config['stage_control']['x_range_mm'],
            'y_range_mm': self.config['stage_control']['y_range_mm'],
            'z_range_mm': self.config['stage_control']['z_range_mm'],
            'voxel_size_um': self.config['display']['voxel_size_um'][0],  # Assume isotropic
            'invert_x': self.config['stage_control']['invert_x_default'],
            'invert_z': self.config['stage_control']['invert_z_default']
        }
        self.coord_mapper = PhysicalToNapariMapper(mapper_config)

        # Initialize coordinate transformers (for rotation) BEFORE storage
        # CRITICAL: Must use correct sample center from config for accurate rotation
        sample_center_um = self.config['sample_chamber']['sample_region_center_um']
        self.transformer = CoordinateTransformer(sample_center=sample_center_um)

        # Initialize storage system using coord_mapper dimensions (requires transformer)
        self._init_storage_with_mapper()

        # Current state
        self.current_rotation = {'rx': 0, 'ry': 0, 'rz': 0}
        self.current_z = 0
        self.is_populating = False  # Renamed from is_streaming for clarity
        self._controls_enabled = True  # Track if controls should be enabled

        # Sample holder position (will be initialized in _add_sample_holder)
        self.holder_position = {'x': 0, 'y': 0, 'z': 0}
        self.rotation_indicator_length = 0

        # Test sample data (raw, unrotated)
        self.test_sample_data_raw = None
        self.test_sample_size_mm = 2.0  # 2mm cube of sample data
        self.test_sample_offset_mm = 0.5  # 0.5mm below extension tip

        # Fine extension parameters (simplified visualization - just show the thin part)
        self.extension_length_mm = 10.0  # Extension extends 10mm upward from tip (will hit chamber top)
        self.extension_diameter_mm = 0.22  # Very fine extension (220 micrometers)

        # Stage-to-chamber coordinate reference point
        # At stage Y=7.45mm, the extension tip is centered at the objective focal plane
        self.STAGE_Y_AT_OBJECTIVE = 7.45  # mm - calibration reference
        self.OBJECTIVE_CHAMBER_Y_MM = 7.0  # mm - objective focal plane in chamber coordinates

        # XY Focus Frame calibration - where the objective optical axis intersects the sample
        # This is calibrated by finding the tip of the sample holder when centered in live view
        self.objective_xy_calibration = None  # Will be loaded from position presets
        self._load_objective_calibration()

        # Cache previous sample data bounds for efficient clearing (dense array optimization)
        self.previous_sample_bounds = {}  # {ch_id: (z_start, z_end, y_start, y_end, x_start, x_end)}

        # Thread safety for updates - MUST be initialized before any UI/viewer operations
        self.update_mutex = QMutex()
        self.pending_stage_update = None
        self.last_stage_position = {'x': 0, 'y': 0, 'z': 0, 'r': 0}

        # Setup UI
        self._setup_ui()
        self._connect_signals()
        self._connect_movement_controller()

        # Initialize napari viewer if available
        self.viewer = None
        if NAPARI_AVAILABLE:
            self._init_napari_viewer()
        else:
            logger.warning("napari not available - 3D visualization disabled")

        # Update timer for live data
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._update_visualization)
        self.update_timer.setInterval(100)  # 10 Hz update rate
        self.update_timer.start()  # Start the timer to enable real-time visualization updates

        # Debounce timer for rotation spinbox (typing vs arrows)
        self.rotation_debounce_timer = QTimer()
        self.rotation_debounce_timer.setSingleShot(True)
        self.rotation_debounce_timer.timeout.connect(self._apply_rotation_from_spinbox)
        self.rotation_debounce_delay_ms = 150  # 150ms delay for typing

        # Populate timer for capturing frames from Live View
        # 100ms (10 Hz) provides enough frames to fill voxels during motion:
        # - 0.5mm movement at 50µm voxels = 10 voxels to fill
        # - At 10 Hz over 1s movement = 10 frames captured
        self.populate_timer = QTimer()
        self.populate_timer.timeout.connect(self._on_populate_tick)
        self.populate_timer.setInterval(100)  # Capture every 100ms (10 Hz)

        # Track last processed frame to avoid duplicates
        self._last_processed_frame_number = -1

        # Motion-aware frame buffering for accurate position interpolation
        # When stage is moving, buffer frames and interpolate positions after motion stops
        # Uses motion_started/motion_stopped signals instead of position-based detection
        # because position queries return OLD value during motion then jump to NEW after complete
        self._motion_tracking = {
            'in_progress': False,     # True when motion_started signal received
            'start_position': None,   # (x, y, z, r) in mm/degrees at motion start
            'start_time': None,       # time.time() when motion_started received
            'end_position': None,     # (x, y, z, r) at motion end
            'end_time': None,         # time.time() when motion_stopped received
            'frame_buffer': [],       # List of {'frame', 'header', 'channel_id', 'capture_time'}
            'moving_axis': None,      # Which axis is moving (for logging)
        }

        # Update throttle timer for stage movements
        self.update_throttle_timer = QTimer()
        self.update_throttle_timer.timeout.connect(self._process_pending_stage_update)
        self.update_throttle_timer.setInterval(50)  # 20 FPS max for stage updates

        # Connect thread-safe signal
        self.stage_position_update_signal.connect(self._handle_stage_update_threadsafe)

        # Configure window
        self.setWindowTitle("3D Sample Chamber Visualization")
        self.setWindowFlags(Qt.Window)
        self.resize(1200, 800)

    def _load_config(self) -> dict:
        """Load configuration from YAML file."""
        config_path = Path(__file__).parent.parent / "configs" / "visualization_3d_config.yaml"

        if config_path.exists():
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                logger.info(f"Loaded 3D visualization config from {config_path}")
        else:
            # Use defaults if config file doesn't exist
            config = {
                'display': {
                    'voxel_size_um': [50, 50, 50],
                    'fps_target': 30,
                    'downsample_factor': 4,
                    'max_channels': 4
                },
                'storage': {
                    'voxel_size_um': [5, 5, 5],
                    'backend': 'sparse',
                    'max_memory_mb': 2000
                },
                'sample_chamber': {
                    'inner_dimensions_mm': [10, 10, 43],
                    'holder_diameter_mm': 1.0,
                    'chamber_width_x_mm': 11.31,
                    'chamber_x_offset_mm': 1.0,
                    'chamber_depth_z_mm': 13.5,
                    'chamber_z_offset_mm': 12.5,
                    'y_min_anchor_mm': 5.0,
                    'chamber_below_anchor_mm': 10.0,
                    'chamber_above_anchor_mm': 5.0
                },
                'stage_control': {
                    'x_range_mm': [1.0, 12.31],
                    'y_range_mm': [-5.0, 10.0],
                    'z_range_mm': [12.5, 26.0],
                    'y_stage_min_mm': 5.0,
                    'y_stage_max_mm': 15.0,
                    'x_default_mm': 6.655,
                    'y_default_mm': 5.0,
                    'z_default_mm': 19.25,
                    'invert_x_default': False,
                    'invert_z_default': False,
                    'rotation_angle_offset_deg': 0.0,
                    'rotation_range_deg': [-180, 180],
                    'rotation_default_deg': 0.0
                },
                'coordinate_mapping': {
                    'napari_origin': 'back_upper_left',
                    'napari_axis_mapping': {'z': 0, 'y': 1, 'x': 2},
                    'invert_y_display': True,
                    'axis_colors': {
                        'x': '#00FFFF',
                        'y': '#FF00FF',
                        'z': '#FFD700'
                    }
                },
                'channels': [
                    {'id': 0, 'name': '405nm (DAPI)', 'default_colormap': 'cyan', 'default_visible': True},
                    {'id': 1, 'name': '488nm (GFP)', 'default_colormap': 'green', 'default_visible': True},
                    {'id': 2, 'name': '561nm (RFP)', 'default_colormap': 'red', 'default_visible': True},
                    {'id': 3, 'name': '640nm (Far-Red)', 'default_colormap': 'magenta', 'default_visible': False}
                ]
            }
            logger.warning("Using default 3D visualization config")

        return config

    def _load_objective_calibration(self):
        """
        Load objective XY calibration from position presets.

        The calibration point is saved as "Tip of sample mount" in position presets.
        This represents the stage position when the sample holder tip is centered
        in the live view - i.e., where the optical axis intersects the sample plane.
        """
        try:
            preset_service = PositionPresetService()
            preset_name = self.config.get('focus_frame', {}).get(
                'calibration_preset_name', 'Tip of sample mount'
            )

            if preset_service.preset_exists(preset_name):
                preset = preset_service.get_preset(preset_name)
                self.objective_xy_calibration = {
                    'x': preset.x,
                    'y': preset.y,
                    'z': preset.z,
                    'r': preset.r
                }
                logger.info(f"Loaded objective calibration from '{preset_name}': "
                          f"X={preset.x:.3f}, Y={preset.y:.3f}, Z={preset.z:.3f}")
            else:
                # Use default center position if not calibrated
                self.objective_xy_calibration = {
                    'x': self.config['stage_control']['x_default_mm'],
                    'y': self.config['stage_control']['y_default_mm'],
                    'z': self.config['stage_control']['z_default_mm'],
                    'r': 0
                }
                logger.info(f"No '{preset_name}' calibration found, using defaults")
        except Exception as e:
            logger.warning(f"Failed to load objective calibration: {e}")
            self.objective_xy_calibration = None

    def set_objective_calibration(self, x: float, y: float, z: float, r: float = 0):
        """
        Set and save the objective XY calibration point.

        Args:
            x, y, z: Stage position in mm when sample holder tip is centered in live view
            r: Rotation angle (stored but not critical for calibration)
        """
        from py2flamingo.models.microscope import Position

        self.objective_xy_calibration = {'x': x, 'y': y, 'z': z, 'r': r}

        # Save to position presets
        try:
            preset_service = PositionPresetService()
            preset_name = self.config.get('focus_frame', {}).get(
                'calibration_preset_name', 'Tip of sample mount'
            )
            position = Position(x=x, y=y, z=z, r=r)
            preset_service.save_preset(
                preset_name, position,
                "Calibration point: sample holder tip centered in live view"
            )
            logger.info(f"Saved objective calibration to '{preset_name}': "
                      f"X={x:.3f}, Y={y:.3f}, Z={z:.3f}")
        except Exception as e:
            logger.error(f"Failed to save objective calibration: {e}")

        # Update focus frame if it exists
        if self.viewer and 'XY Focus Frame' in self.viewer.layers:
            self._update_xy_focus_frame()

    def _init_storage(self):
        """Initialize the dual-resolution storage system using coordinate mapper dimensions."""
        # Will be initialized after coord_mapper is created
        # This method will be called again in __init__ after coord_mapper exists
        pass

    def _init_storage_with_mapper(self):
        """Initialize storage system after coordinate mapper is available."""
        # Get dimensions from coordinate mapper (X, Y, Z)
        mapper_dims = self.coord_mapper.get_napari_dimensions()
        voxel_size_um = self.config['display']['voxel_size_um'][0]  # Assume isotropic

        # Napari expects dimensions in (Z, Y, X) order since:
        # Axis 0 = Z (yellow), Axis 1 = Y (magenta), Axis 2 = X (cyan)
        napari_dims = (mapper_dims[2], mapper_dims[1], mapper_dims[0])  # (Z, Y, X)

        # Calculate chamber dimensions in µm (Z, Y, X order)
        chamber_dims_um = (
            napari_dims[0] * voxel_size_um,  # Z depth (Axis 0)
            napari_dims[1] * voxel_size_um,  # Y height (Axis 1)
            napari_dims[2] * voxel_size_um   # X width (Axis 2)
        )

        # Calculate chamber origin in world coordinates (Z, Y, X order in µm)
        # Origin is the minimum corner of the chamber in world space
        chamber_origin_um = (
            self.config['stage_control']['z_range_mm'][0] * 1000,  # Z min
            self.config['stage_control']['y_range_mm'][0] * 1000,  # Y min
            self.config['stage_control']['x_range_mm'][0] * 1000   # X min
        )

        # Check if asymmetric bounds are specified in config
        if all(key in self.config['sample_chamber'] for key in
               ['sample_region_half_width_x_um', 'sample_region_half_width_y_um', 'sample_region_half_width_z_um']):
            # Reorder from X,Y,Z config format to Z,Y,X storage format
            half_widths = (
                self.config['sample_chamber']['sample_region_half_width_z_um'],  # Z first
                self.config['sample_chamber']['sample_region_half_width_y_um'],  # Y second
                self.config['sample_chamber']['sample_region_half_width_x_um']   # X third
            )
        else:
            half_widths = None

        # Reorder sample_region_center from config's X,Y,Z to storage's Z,Y,X format
        center_xyz = self.config['sample_chamber']['sample_region_center_um']
        center_zyx = (center_xyz[2], center_xyz[1], center_xyz[0])  # Reorder to Z,Y,X

        # Reorder voxel sizes from X,Y,Z to Z,Y,X
        storage_voxel_xyz = self.config['storage']['voxel_size_um']
        storage_voxel_zyx = (storage_voxel_xyz[2], storage_voxel_xyz[1], storage_voxel_xyz[0])

        display_voxel_xyz = self.config['display']['voxel_size_um']
        display_voxel_zyx = (display_voxel_xyz[2], display_voxel_xyz[1], display_voxel_xyz[0])

        storage_config = DualResolutionConfig(
            storage_voxel_size=storage_voxel_zyx,  # Now in Z,Y,X order
            display_voxel_size=display_voxel_zyx,  # Now in Z,Y,X order
            chamber_dimensions=chamber_dims_um,     # Already in Z,Y,X order
            chamber_origin=chamber_origin_um,       # Already in Z,Y,X order
            sample_region_center=center_zyx,        # Now in Z,Y,X order
            sample_region_radius=self.config['sample_chamber']['sample_region_radius_um'],
            sample_region_half_widths=half_widths   # Now in Z,Y,X order
        )

        self.voxel_storage = DualResolutionVoxelStorage(storage_config)

        # Set the coordinate transformer on storage for volume transformations
        self.voxel_storage.set_coordinate_transformer(self.transformer)

        logger.info(f"Initialized dual-resolution voxel storage")
        logger.info(f"  Napari dimensions (Z, Y, X): {napari_dims}")
        logger.info(f"  Chamber dimensions (µm): {chamber_dims_um}")
        logger.info(f"  Display dimensions (voxels): {self.voxel_storage.display_dims}")
        logger.info(f"  Voxel size (µm): {self.config['display']['voxel_size_um']}")
        logger.info(f"  Using direct dense array updates for performance")

    def _setup_ui(self):
        """Setup the user interface."""
        main_layout = QHBoxLayout(self)

        # Create splitter for resizable panels
        splitter = QSplitter(Qt.Horizontal)

        # Left panel: Controls
        control_panel = self._create_control_panel()
        splitter.addWidget(control_panel)

        # Right panel: Status bar + Napari viewer
        viewer_container = QWidget()
        viewer_layout = QVBoxLayout(viewer_container)

        # Horizontal status bar (centered above viewer)
        status_layout = QHBoxLayout()
        status_layout.addStretch()

        self.status_label = QLabel("Status: Ready")
        self.status_label.setStyleSheet("font-weight: bold;")
        status_layout.addWidget(self.status_label)

        status_layout.addSpacing(40)

        self.memory_label = QLabel("Memory: 0 MB")
        status_layout.addWidget(self.memory_label)

        status_layout.addSpacing(40)

        self.voxel_count_label = QLabel("Voxels: 0")
        status_layout.addWidget(self.voxel_count_label)

        status_layout.addStretch()
        viewer_layout.addLayout(status_layout)

        # Scale information bar (below status, above viewer)
        scale_layout = QHBoxLayout()
        scale_layout.addStretch()

        # Get dimensions from config for display
        storage_voxel = self.config.get('storage', {}).get('voxel_size_um', [5, 5, 5])[0]
        display_voxel = self.config.get('display', {}).get('voxel_size_um', [50, 50, 50])[0]
        stage_ctrl = self.config.get('stage_control', {})
        x_range = stage_ctrl.get('x_range_mm', [1.0, 12.31])
        y_range = stage_ctrl.get('y_range_mm', [0.0, 14.0])
        z_range = stage_ctrl.get('z_range_mm', [12.5, 26.0])

        x_dim = x_range[1] - x_range[0]
        y_dim = y_range[1] - y_range[0]
        z_dim = z_range[1] - z_range[0]

        self.scale_label = QLabel(
            f"Resolution: {storage_voxel}µm/voxel (storage), {display_voxel}µm/voxel (display)  |  "
            f"Volume: {x_dim:.1f} × {y_dim:.1f} × {z_dim:.1f} mm (X×Y×Z)"
        )
        self.scale_label.setStyleSheet("color: #888; font-size: 10px;")
        scale_layout.addWidget(self.scale_label)

        scale_layout.addStretch()
        viewer_layout.addLayout(scale_layout)

        if NAPARI_AVAILABLE:
            # Placeholder - napari viewer will be embedded here
            self.viewer_placeholder = QWidget()
            self.viewer_placeholder.setMinimumSize(600, 600)
            viewer_layout.addWidget(self.viewer_placeholder)
        else:
            # Show message if napari not available
            msg_label = QLabel("napari is not installed.\n\n"
                              "Install with: pip install napari[all]")
            msg_label.setAlignment(Qt.AlignCenter)
            viewer_layout.addWidget(msg_label)

        splitter.addWidget(viewer_container)
        splitter.setSizes([270, 930])  # Reduced control panel to 2/3 width

        main_layout.addWidget(splitter)

    def _create_control_panel(self) -> QWidget:
        """Create the control panel with tabs."""
        control_widget = QWidget()
        layout = QVBoxLayout(control_widget)

        # Tab widget for organized controls
        tabs = QTabWidget()

        # Channel Controls tab
        channel_tab = self._create_channel_controls()
        tabs.addTab(channel_tab, "Channels")

        # Sample Control tab (position and rotation)
        sample_control_tab = self._create_sample_controls()
        tabs.addTab(sample_control_tab, "Sample Control")

        # Data Management tab
        data_tab = self._create_data_controls()
        tabs.addTab(data_tab, "Data")

        # Display tab removed - controls moved to Sample Control tab

        layout.addWidget(tabs)

        # Control buttons
        button_layout = QHBoxLayout()

        self.populate_button = QPushButton("Populate from Live View")
        self.populate_button.setCheckable(True)
        self.populate_button.setToolTip("Capture frames from Live Viewer and accumulate into 3D volume")
        self.clear_button = QPushButton("Clear Data")
        self.export_button = QPushButton("Export...")

        button_layout.addWidget(self.populate_button)
        button_layout.addWidget(self.clear_button)
        button_layout.addWidget(self.export_button)

        layout.addLayout(button_layout)

        return control_widget

    def _create_channel_controls(self) -> QWidget:
        """Create channel control widgets."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Global accumulation strategy (applies to all channels)
        strategy_group = QGroupBox("Data Accumulation")
        strategy_layout = QHBoxLayout()
        strategy_layout.addWidget(QLabel("Strategy:"))

        self.global_strategy_combo = QComboBox()
        self.global_strategy_combo.addItems(['maximum', 'latest', 'average', 'additive'])
        self.global_strategy_combo.setCurrentText('maximum')  # Default for fluorescence
        self.global_strategy_combo.setToolTip("How to accumulate data when the same voxel is imaged multiple times")
        strategy_layout.addWidget(self.global_strategy_combo)

        strategy_group.setLayout(strategy_layout)
        layout.addWidget(strategy_group)

        self.channel_controls = {}

        for ch_config in self.config['channels']:
            ch_id = ch_config['id']
            ch_name = ch_config['name']

            # Display channel number as 1-4 (user-facing) instead of 0-3 (internal)
            display_channel_num = ch_id + 1
            group = QGroupBox(f"Channel {display_channel_num}: {ch_name}")
            ch_layout = QGridLayout()

            # Visibility checkbox
            visible_cb = QCheckBox("Visible")
            visible_cb.setChecked(ch_config.get('default_visible', True))

            # Colormap selector
            colormap_combo = QComboBox()
            colormap_combo.addItems(['cyan', 'green', 'red', 'magenta', 'yellow', 'gray'])
            colormap_combo.setCurrentText(ch_config['default_colormap'])

            # Opacity slider
            opacity_slider = QSlider(Qt.Horizontal)
            opacity_slider.setRange(0, 100)
            opacity_slider.setValue(80)
            opacity_label = QLabel("80%")

            # Contrast range slider (min and max in one slider)
            # Default to 0-50 for downsampled display (typical useful range)
            from superqt import QRangeSlider
            contrast_range_slider = QRangeSlider(Qt.Horizontal)
            contrast_range_slider.setRange(0, 65535)
            contrast_range_slider.setValue((0, 50))  # Default 0-50 for downsampled data
            contrast_range_label = QLabel("0 - 50")

            # Layout channel controls (more compact)
            ch_layout.addWidget(visible_cb, 0, 0, 1, 3)
            ch_layout.addWidget(QLabel("Color:"), 1, 0)
            ch_layout.addWidget(colormap_combo, 1, 1, 1, 2)
            ch_layout.addWidget(QLabel("Opacity:"), 2, 0)
            ch_layout.addWidget(opacity_slider, 2, 1)
            ch_layout.addWidget(opacity_label, 2, 2)
            ch_layout.addWidget(QLabel("Contrast:"), 3, 0)
            ch_layout.addWidget(contrast_range_slider, 3, 1)
            ch_layout.addWidget(contrast_range_label, 3, 2)

            group.setLayout(ch_layout)
            layout.addWidget(group)

            # Store references
            self.channel_controls[ch_id] = {
                'visible': visible_cb,
                'colormap': colormap_combo,
                'opacity': opacity_slider,
                'opacity_label': opacity_label,
                'contrast_range': contrast_range_slider,
                'contrast_label': contrast_range_label
            }

            # Connect controls to napari layer updates
            colormap_combo.currentTextChanged.connect(
                lambda colormap, cid=ch_id: self._on_colormap_changed(cid, colormap)
            )
            opacity_slider.valueChanged.connect(
                lambda v, label=opacity_label, cid=ch_id: (
                    label.setText(f"{v}%"),
                    self._on_opacity_changed(cid, v/100.0)
                )
            )
            contrast_range_slider.valueChanged.connect(
                lambda value, label=contrast_range_label, cid=ch_id: self._on_contrast_range_changed(cid, value, label)
            )

        layout.addStretch()
        return widget

    def _create_sample_controls(self) -> QWidget:
        """Create sample position and rotation control widgets with color-coded sliders."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Position controls with color-coded sliders
        position_group = QGroupBox("Sample Position")
        pos_layout = QVBoxLayout()

        # Store slider and checkbox references
        self.position_sliders = {}

        # Get config values
        stage_config = self.config['stage_control']
        axis_colors = self.config['coordinate_mapping']['axis_colors']

        # X Slider - Cyan (Napari Axis 2)
        x_widget = self._create_axis_slider(
            label="X Position (Width)",
            color=axis_colors['x'],
            napari_axis=2,
            min_val=stage_config['x_range_mm'][0],
            max_val=stage_config['x_range_mm'][1],
            default_val=stage_config['x_default_mm'],
            has_invert=False,  # Invert config now in JSON file
            tooltip="X-axis (horizontal, left-right)\nNapari Axis 2 (Cyan)"
        )
        pos_layout.addWidget(x_widget)

        # Y Slider - Magenta (Napari Axis 1)
        # Use stage limits, not chamber extent
        y_min = stage_config.get('y_stage_min_mm', stage_config['y_range_mm'][0])
        y_max = stage_config.get('y_stage_max_mm', stage_config['y_range_mm'][1])

        y_widget = self._create_axis_slider(
            label="Y Position (Height)",
            color=axis_colors['y'],
            napari_axis=1,
            min_val=y_min,
            max_val=y_max,
            default_val=stage_config['y_default_mm'],
            has_invert=False,  # Y doesn't need invert (handled by coord transform)
            tooltip="Y-axis (vertical height)\nNapari Axis 1 (Magenta)\nY-axis is inverted for intuitive display\nMinimum: collision prevention limit"
        )
        pos_layout.addWidget(y_widget)

        # Z Slider - Yellow (Napari Axis 0)
        z_widget = self._create_axis_slider(
            label="Z Position (Depth)",
            color=axis_colors['z'],
            napari_axis=0,
            min_val=stage_config['z_range_mm'][0],
            max_val=stage_config['z_range_mm'][1],
            default_val=stage_config['z_default_mm'],
            has_invert=False,  # Invert config now in JSON file
            tooltip="Z-axis (depth, toward objective)\nNapari Axis 0 (Yellow)"
        )
        pos_layout.addWidget(z_widget)

        position_group.setLayout(pos_layout)
        layout.addWidget(position_group)

        # Rotation control
        rotation_group = QGroupBox("Stage Rotation (Y-Axis)")
        rot_layout = QVBoxLayout()

        # Rotation header with editable value
        rot_header = QHBoxLayout()
        rot_header.addWidget(QLabel("Rotation Angle:"))
        rot_header.addStretch()

        # Editable rotation spinbox
        # Display range: 0-360° (but software handles -720 to 720 with wrapping)
        # TODO: Handle edge case when stage starts at angles like -570°
        from PyQt5.QtWidgets import QDoubleSpinBox
        self.rotation_spinbox = QDoubleSpinBox()
        self.rotation_spinbox.setRange(0, 360)  # User-facing range
        self.rotation_spinbox.setValue(stage_config['rotation_default_deg'])
        self.rotation_spinbox.setDecimals(1)
        self.rotation_spinbox.setSuffix("°")
        self.rotation_spinbox.setWrapping(True)  # Wraps from 360 to 0
        self.rotation_spinbox.setSingleStep(1.0)  # 1° per arrow click
        self.rotation_spinbox.setStyleSheet("""
            QDoubleSpinBox {
                background-color: #2a2a2a;
                color: #FFFFFF;
                font-weight: bold;
                font-size: 14px;
                border: 1px solid #666;
                border-radius: 3px;
                padding: 3px;
            }
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                background-color: #3a3a3a;
                border: 1px solid #555;
            }
            QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
                background-color: #4a4a4a;
            }
        """)
        self.rotation_spinbox.setMaximumWidth(100)
        rot_header.addWidget(self.rotation_spinbox)
        rot_layout.addLayout(rot_header)

        # Rotation slider
        self.rotation_slider = QSlider(Qt.Horizontal)
        # Note: Software can handle -720 to 720 with angle wrapping
        self.rotation_slider.setRange(0, 360)  # Match spinbox range
        self.rotation_slider.setValue(int(stage_config['rotation_default_deg']))
        self.rotation_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: 1px solid #999999;
                height: 8px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4169E1, stop:0.25 #FF4500, stop:0.5 #FFD700,
                    stop:0.75 #FF4500, stop:1 #4169E1);
                margin: 2px 0;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF;
                border: 2px solid #555555;
                width: 18px;
                margin: -5px 0;
                border-radius: 9px;
            }
        """)
        rot_layout.addWidget(self.rotation_slider)

        # Range labels
        range_layout = QHBoxLayout()
        range_layout.addWidget(QLabel("0°"))
        range_layout.addStretch()
        range_layout.addWidget(QLabel("360°"))
        rot_layout.addLayout(range_layout)

        # Reset button
        reset_btn = QPushButton("Reset to 0°")
        reset_btn.clicked.connect(lambda: (self.rotation_slider.setValue(0), self.rotation_spinbox.setValue(0)))
        rot_layout.addWidget(reset_btn)

        rotation_group.setLayout(rot_layout)
        layout.addWidget(rotation_group)

        # Display Settings (moved from Display tab)
        display_group = QGroupBox("Display Settings")
        disp_layout = QGridLayout()

        # Chamber wireframe checkbox
        self.show_chamber_cb = QCheckBox("Show Chamber Wireframe")
        self.show_chamber_cb.setChecked(True)
        disp_layout.addWidget(self.show_chamber_cb, 0, 0, 1, 2)

        # Objective indicator checkbox
        self.show_objective_cb = QCheckBox("Show Objective Position")
        self.show_objective_cb.setChecked(True)
        disp_layout.addWidget(self.show_objective_cb, 1, 0, 1, 2)

        # Rendering mode
        disp_layout.addWidget(QLabel("Rendering:"), 2, 0)
        self.rendering_combo = QComboBox()
        self.rendering_combo.addItems(['mip', 'minip', 'average', 'iso'])
        disp_layout.addWidget(self.rendering_combo, 2, 1)

        # Reset view button
        self.reset_view_btn = QPushButton("Reset View")
        self.reset_view_btn.setToolTip("Reset camera to default orientation and zoom")
        disp_layout.addWidget(self.reset_view_btn, 3, 0, 1, 2)

        display_group.setLayout(disp_layout)
        layout.addWidget(display_group)

        # Info label
        info_label = QLabel("ℹ Rotation indicator (red line) shows 0° reference orientation in XZ plane.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("QLabel { color: #888; font-style: italic; padding: 5px; font-size: 11px; }")
        layout.addWidget(info_label)

        layout.addStretch()
        return widget

    def _create_axis_slider(self, label: str, color: str, napari_axis: int,
                           min_val: float, max_val: float, default_val: float,
                           has_invert: bool, tooltip: str) -> QWidget:
        """Create a single color-coded axis slider."""
        # Container widget
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(5)
        layout.setContentsMargins(8, 8, 8, 8)

        # Header with color indicator and label
        header_layout = QHBoxLayout()

        # Color indicator (colored square matching napari axis)
        color_indicator = QLabel("█")  # Unicode block character
        color_indicator.setStyleSheet(f"color: {color}; font-size: 20px;")
        color_indicator.setToolTip(f"Napari Axis {napari_axis}")
        header_layout.addWidget(color_indicator)

        # Axis label
        axis_label = QLabel(label)
        axis_label.setStyleSheet(f"font-weight: bold; color: {color};")
        axis_label.setToolTip(tooltip)
        header_layout.addWidget(axis_label)

        header_layout.addStretch()

        # Napari axis number badge
        axis_badge = QLabel(f"Axis {napari_axis}")
        axis_badge.setStyleSheet(f"""
            background-color: {color};
            color: black;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 10px;
            font-weight: bold;
        """)
        header_layout.addWidget(axis_badge)

        layout.addLayout(header_layout)

        # Slider with colored groove
        slider = QSlider(Qt.Horizontal)
        slider.setRange(int(min_val * 1000), int(max_val * 1000))  # µm precision
        slider.setValue(int(default_val * 1000))
        slider.setToolTip(tooltip)

        # Calculate lighter color for gradient
        lighter_color = self._lighten_color(color, 1.3)

        # Style slider with colored groove matching axis color
        slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                border: 1px solid #999999;
                height: 8px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #444444, stop:1 {color});
                margin: 2px 0;
                border-radius: 4px;
            }}
            QSlider::handle:horizontal {{
                background: {color};
                border: 2px solid #555555;
                width: 18px;
                margin: -5px 0;
                border-radius: 9px;
            }}
            QSlider::handle:horizontal:hover {{
                background: {lighter_color};
                border: 2px solid {color};
            }}
        """)

        layout.addWidget(slider)

        # Value display with range info
        value_layout = QHBoxLayout()

        range_label = QLabel(f"{min_val:.2f}")
        range_label.setStyleSheet("color: #888888; font-size: 10px;")
        value_layout.addWidget(range_label)

        value_layout.addStretch()

        # Editable value input (QDoubleSpinBox)
        from PyQt5.QtWidgets import QDoubleSpinBox
        value_spinbox = QDoubleSpinBox()
        value_spinbox.setRange(min_val, max_val)
        value_spinbox.setValue(default_val)
        value_spinbox.setDecimals(2)
        value_spinbox.setSuffix(" mm")
        value_spinbox.setStyleSheet(f"""
            QDoubleSpinBox {{
                color: {color};
                font-weight: bold;
                font-size: 14px;
                border: 2px solid {color};
                border-radius: 4px;
                padding: 2px;
                background-color: #1a1a1a;
            }}
        """)
        value_spinbox.setMaximumWidth(120)
        value_layout.addWidget(value_spinbox)

        value_layout.addStretch()

        range_label_max = QLabel(f"{max_val:.2f}")
        range_label_max.setStyleSheet("color: #888888; font-size: 10px;")
        value_layout.addWidget(range_label_max)

        layout.addLayout(value_layout)

        # Store slider and spinbox references
        axis_key = label.split()[0].lower()
        self.position_sliders[f'{axis_key}_slider'] = slider
        self.position_sliders[f'{axis_key}_spinbox'] = value_spinbox

        # Connect slider to update spinbox (real-time)
        slider.valueChanged.connect(
            lambda v, sb=value_spinbox: sb.blockSignals(True) or sb.setValue(v/1000.0) or sb.blockSignals(False)
        )

        # Connect spinbox to update slider (only when editing finished)
        # This prevents intermediate updates while typing
        value_spinbox.editingFinished.connect(
            lambda sb=value_spinbox, sl=slider: sl.setValue(int(sb.value() * 1000))
        )

        # Add subtle colored border to entire widget
        widget.setStyleSheet(f"""
            QWidget {{
                border: 2px solid {color};
                border-radius: 5px;
                padding: 8px;
                background-color: #2a2a2a;
            }}
        """)

        return widget

    def _get_rotation_gradient_color(self, angle_deg: float) -> str:
        """
        Get color from rotation slider gradient based on angle.

        Gradient: Blue(0°) → Orange(90°) → Yellow(180°) → Orange(270°) → Blue(360°)

        Args:
            angle_deg: Rotation angle in degrees (0-360)

        Returns:
            Hex color string
        """
        # Normalize angle to 0-360 range
        angle = angle_deg % 360

        # Color stops: Blue, Orange, Yellow, Orange, Blue
        # Positions:    0°    90°     180°    270°    360°
        colors = [
            (0.0,   (0x41, 0x69, 0xE1)),  # Royal Blue
            (0.25,  (0xFF, 0x45, 0x00)),  # Orange Red
            (0.5,   (0xFF, 0xD7, 0x00)),  # Gold/Yellow
            (0.75,  (0xFF, 0x45, 0x00)),  # Orange Red
            (1.0,   (0x41, 0x69, 0xE1))   # Royal Blue
        ]

        # Find which segment we're in
        t = angle / 360.0  # Normalize to 0-1

        # Find the two color stops to interpolate between
        for i in range(len(colors) - 1):
            t_start, color_start = colors[i]
            t_end, color_end = colors[i + 1]

            if t_start <= t <= t_end:
                # Interpolate between these two colors
                local_t = (t - t_start) / (t_end - t_start)

                r = int(color_start[0] + (color_end[0] - color_start[0]) * local_t)
                g = int(color_start[1] + (color_end[1] - color_start[1]) * local_t)
                b = int(color_start[2] + (color_end[2] - color_start[2]) * local_t)

                return f"#{r:02x}{g:02x}{b:02x}"

        # Fallback (shouldn't reach here)
        return "#4169E1"

    def _lighten_color(self, hex_color: str, factor: float = 1.3) -> str:
        """Lighten a hex color for hover effects."""
        # Remove '#' if present
        hex_color = hex_color.lstrip('#')

        # Convert to RGB
        r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

        # Lighten
        r = min(255, int(r * factor))
        g = min(255, int(g * factor))
        b = min(255, int(b * factor))

        # Convert back to hex
        return f"#{r:02x}{g:02x}{b:02x}"

    def _create_data_controls(self) -> QWidget:
        """Create data management controls."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Memory management
        memory_group = QGroupBox("Memory Management")
        mem_layout = QVBoxLayout()

        self.auto_clear_cb = QCheckBox("Auto-clear when memory exceeds limit")
        self.memory_limit_spin = QSpinBox()
        self.memory_limit_spin.setRange(100, 10000)
        self.memory_limit_spin.setValue(self.config['storage']['max_memory_mb'])
        self.memory_limit_spin.setSuffix(" MB")

        mem_layout.addWidget(self.auto_clear_cb)
        mem_layout.addWidget(self.memory_limit_spin)

        memory_group.setLayout(mem_layout)
        layout.addWidget(memory_group)

        # Data info
        info_group = QGroupBox("Data Information")
        info_layout = QGridLayout()

        self.storage_res_label = QLabel(f"Storage: {self.config['storage']['voxel_size_um']} µm/voxel")
        self.display_res_label = QLabel(f"Display: {self.config['display']['voxel_size_um']} µm/voxel")
        self.ratio_label = QLabel(f"Ratio: {self.voxel_storage.config.resolution_ratio}")

        info_layout.addWidget(self.storage_res_label, 0, 0)
        info_layout.addWidget(self.display_res_label, 1, 0)
        info_layout.addWidget(self.ratio_label, 2, 0)

        info_group.setLayout(info_layout)
        layout.addWidget(info_group)

        layout.addStretch()
        return widget

    def _create_display_controls(self) -> QWidget:
        """Create display setting controls."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        display_group = QGroupBox("Display Settings")
        disp_layout = QGridLayout()

        # Chamber wireframe
        self.show_chamber_cb = QCheckBox("Show Chamber Wireframe")
        self.show_chamber_cb.setChecked(True)

        # Objective indicator
        self.show_objective_cb = QCheckBox("Show Objective Position")
        self.show_objective_cb.setChecked(True)

        # Rendering mode
        disp_layout.addWidget(QLabel("Rendering:"), 3, 0)
        self.rendering_combo = QComboBox()
        self.rendering_combo.addItems(['mip', 'minip', 'average', 'iso'])
        disp_layout.addWidget(self.rendering_combo, 3, 1)

        disp_layout.addWidget(self.show_chamber_cb, 0, 0, 1, 2)
        disp_layout.addWidget(self.show_objective_cb, 1, 0, 1, 2)

        # Reset view button
        self.reset_view_btn = QPushButton("Reset View")
        self.reset_view_btn.setToolTip("Reset camera to default orientation and zoom")
        disp_layout.addWidget(self.reset_view_btn, 4, 0, 1, 2)

        display_group.setLayout(disp_layout)
        layout.addWidget(display_group)

        layout.addStretch()
        return widget

    def _connect_signals(self):
        """Connect widget signals to slots."""
        # Start/stop populating from live view
        self.populate_button.toggled.connect(self._on_populate_toggled)

        # Clear data
        self.clear_button.clicked.connect(self._on_clear_data)

        # Export
        self.export_button.clicked.connect(self._on_export_data)

        # Position sliders
        self.position_sliders['x_slider'].valueChanged.connect(self._on_x_slider_changed)
        self.position_sliders['y_slider'].valueChanged.connect(self._on_y_slider_changed)
        self.position_sliders['z_slider'].valueChanged.connect(self._on_z_slider_changed)

        # Rotation controls
        # Slider: real-time updates
        self.rotation_slider.valueChanged.connect(self._on_rotation_slider_changed)

        # Spinbox: debounced updates (instant for arrows, delayed for typing)
        self.rotation_spinbox.valueChanged.connect(self._on_rotation_spinbox_changed)

        # Spinbox editing finished: immediate update (for Enter key)
        self.rotation_spinbox.editingFinished.connect(self._apply_rotation_from_spinbox)

        # Channel visibility
        for ch_id, controls in self.channel_controls.items():
            controls['visible'].toggled.connect(
                lambda checked, cid=ch_id: self._on_channel_visibility_changed(cid, checked)
            )

        # Display settings (now in Sample Control tab)
        self.show_chamber_cb.toggled.connect(self._on_display_settings_changed)
        self.show_objective_cb.toggled.connect(self._on_display_settings_changed)
        self.reset_view_btn.clicked.connect(self._on_reset_view)
        self.rendering_combo.currentTextChanged.connect(self._on_rendering_mode_changed)

    def _connect_movement_controller(self):
        """Connect to movement controller signals for bidirectional sync."""
        if not self.movement_controller:
            logger.info("No movement controller - 3D window will operate in visualization-only mode")
            return

        # Connect to movement controller signals
        try:
            # Connect position changes to thread-safe handler
            # Note: position_changed emits (x, y, z, r) as separate floats
            self.movement_controller.position_changed.connect(self._on_position_changed_for_transform)
            # Also connect to UI update handler
            self.movement_controller.position_changed.connect(self._on_position_changed_from_controller)
            self.movement_controller.motion_started.connect(self._on_motion_started)
            self.movement_controller.motion_stopped.connect(self._on_motion_stopped)

            # Connect slider release events to send movement commands
            self.position_sliders['x_slider'].sliderReleased.connect(self._on_x_slider_released)
            self.position_sliders['y_slider'].sliderReleased.connect(self._on_y_slider_released)
            self.position_sliders['z_slider'].sliderReleased.connect(self._on_z_slider_released)
            self.rotation_slider.sliderReleased.connect(self._on_rotation_slider_released)

            # Connect spinbox Enter key (editingFinished) to also send commands
            self.position_sliders['x_spinbox'].editingFinished.connect(self._on_x_slider_released)
            self.position_sliders['y_spinbox'].editingFinished.connect(self._on_y_slider_released)
            self.position_sliders['z_spinbox'].editingFinished.connect(self._on_z_slider_released)
            self.rotation_spinbox.editingFinished.connect(self._on_rotation_slider_released)

            logger.info("Connected to movement controller - 3D window synchronized with stage")
        except Exception as e:
            logger.error(f"Failed to connect movement controller: {e}")

    def _init_napari_viewer(self):
        """Initialize the napari viewer."""
        print(f"DEBUG: _init_napari_viewer called, sparse_renderer exists: {hasattr(self, 'sparse_renderer') and self.sparse_renderer is not None}")

        if not NAPARI_AVAILABLE:
            return

        try:
            # Create napari viewer with axis display for debugging
            self.viewer = napari.Viewer(ndisplay=3, show=False)

            # Enable axis display
            self.viewer.axes.visible = True
            self.viewer.axes.labels = True  # Show default 0,1,2 labels (napari doesn't support custom)
            self.viewer.axes.colored = True
            # Note: Axis 0=X, Axis 1=Y (vertical), Axis 2=Z (depth)

            # Set initial camera orientation
            # Default napari view is often upside down, so we need to set a proper orientation
            # Camera angles: (azimuth, elevation) in degrees
            self.viewer.camera.angles = (45, 30, 0)  # Good 3D perspective
            self.viewer.camera.zoom = 1.57  # Zoomed out to fit entire chamber

            # Embed viewer in our widget FIRST before adding layers
            # This ensures the viewer is properly initialized
            from napari.qt import Window
            napari_window = self.viewer.window
            viewer_widget = napari_window._qt_viewer

            # Replace placeholder with actual viewer
            if hasattr(self, 'viewer_placeholder'):
                layout = self.viewer_placeholder.parent().layout()
                layout.replaceWidget(self.viewer_placeholder, viewer_widget)
                self.viewer_placeholder.deleteLater()

            # Now setup visualization components
            try:
                self._setup_chamber_visualization()
            except Exception as e:
                logger.warning(f"Failed to setup chamber visualization: {e}")

            try:
                self._setup_data_layers()
            except Exception as e:
                logger.warning(f"Failed to setup data layers: {e}")

            logger.info("napari viewer initialized successfully")

            # Initial visualization update (will show empty until data is populated)
            try:
                self._update_visualization()
                logger.info("Initial visualization ready")

                # Sync contrast sliders with napari's auto-scaled values
                self._sync_contrast_sliders_with_napari()
            except Exception as e:
                logger.error(f"Failed to initialize visualization: {e}")

        except Exception as e:
            logger.error(f"Failed to initialize napari viewer: {e}")
            self.viewer = None

    def _setup_chamber_visualization(self):
        """Setup the fixed chamber wireframe, sample holder, and objective indicator."""
        if not self.viewer:
            return

        # Generate chamber wireframe as shapes (box edges)
        self._add_chamber_wireframe()

        # Add sample holder (cylinder coming down from top)
        self._add_sample_holder()

        # Add fine extension (thin probe extending from holder)
        self._add_fine_extension()

        # Add rotation indicator (extends from sample holder at 0 degrees)
        self._add_rotation_indicator()

        # Add objective position indicator as a flat circle on back wall (Z=0)
        # This shows the detection light path direction
        self._add_objective_indicator()

        # Add XY focus frame showing where the camera is capturing
        self._add_xy_focus_frame()

    def _add_chamber_wireframe(self):
        """Add chamber wireframe as box edges using shapes layer."""
        if not self.viewer:
            return

        dims = self.voxel_storage.display_dims  # (Z, Y, X) order

        # Define the 8 corners of the box in napari (Z, Y, X) order
        # Z = Axis 0 (depth), Y = Axis 1 (height), X = Axis 2 (width)
        corners = np.array([
            [0, 0, 0],                              # Back-bottom-left
            [dims[0]-1, 0, 0],                      # Front-bottom-left
            [dims[0]-1, 0, dims[2]-1],              # Front-bottom-right
            [0, 0, dims[2]-1],                      # Back-bottom-right
            [0, dims[1]-1, 0],                      # Back-top-left
            [dims[0]-1, dims[1]-1, 0],              # Front-top-left
            [dims[0]-1, dims[1]-1, dims[2]-1],      # Front-top-right
            [0, dims[1]-1, dims[2]-1]               # Back-top-right
        ])

        # Define edges by axis for color coding
        # Z edges (Axis 0 - Yellow, dim) - parallel to Z axis
        z_edges = [
            [corners[0], corners[1]],  # Bottom-back
            [corners[3], corners[2]],  # Bottom-front
            [corners[4], corners[5]],  # Top-back
            [corners[7], corners[6]]   # Top-front
        ]

        # Y edges (Axis 1 - Magenta, dim) - parallel to Y axis (vertical)
        y_edges = [
            [corners[0], corners[4]],  # Back-left vertical
            [corners[1], corners[5]],  # Front-left vertical
            [corners[2], corners[6]],  # Front-right vertical
            [corners[3], corners[7]]   # Back-right vertical
        ]

        # X edges (Axis 2 - Cyan, darker) - parallel to X axis
        x_edges = [
            [corners[0], corners[3]],  # Bottom-back
            [corners[1], corners[2]],  # Bottom-front
            [corners[4], corners[7]],  # Top-back
            [corners[5], corners[6]]   # Top-front
        ]

        # Add Z edges (yellow, dim)
        self.viewer.add_shapes(
            data=z_edges,
            shape_type='line',
            name='Chamber Z-edges',
            edge_color='#8B8B00',  # Dim yellow
            edge_width=2,
            opacity=0.6
        )

        # Add Y edges (magenta, dim)
        self.viewer.add_shapes(
            data=y_edges,
            shape_type='line',
            name='Chamber Y-edges',
            edge_color='#8B008B',  # Dim magenta
            edge_width=2,
            opacity=0.6
        )

        # Add X edges (cyan, darker)
        self.viewer.add_shapes(
            data=x_edges,
            shape_type='line',
            name='Chamber X-edges',
            edge_color='#008B8B',  # Darker cyan
            edge_width=2,
            opacity=0.6
        )

    def _generate_chamber_wireframe(self) -> np.ndarray:
        """Generate wireframe voxels for chamber borders."""
        dims = self.voxel_storage.display_dims
        wireframe = np.zeros(dims, dtype=np.uint8)

        thickness = self.config['display'].get('chamber_wireframe_thickness', 2)

        # Draw edges
        for i in range(thickness):
            # Bottom edges
            wireframe[i, :thickness, :thickness] = 1
            wireframe[i, -thickness:, :thickness] = 1
            wireframe[-i-1, :thickness, :thickness] = 1
            wireframe[-i-1, -thickness:, :thickness] = 1

            wireframe[:thickness, i, :thickness] = 1
            wireframe[:thickness, -i-1, :thickness] = 1
            wireframe[-thickness:, i, :thickness] = 1
            wireframe[-thickness:, -i-1, :thickness] = 1

            # Top edges
            wireframe[i, :thickness, -thickness:] = 1
            wireframe[i, -thickness:, -thickness:] = 1
            wireframe[-i-1, :thickness, -thickness:] = 1
            wireframe[-i-1, -thickness:, -thickness:] = 1

            # Vertical edges
            wireframe[:thickness, :thickness, i] = 1
            wireframe[:thickness, -thickness:, i] = 1
            wireframe[-thickness:, :thickness, i] = 1
            wireframe[-thickness:, -thickness:, i] = 1

        return wireframe

    def _add_sample_holder(self):
        """Add sample holder as a cylinder coming down from the top of the chamber."""
        if not self.viewer:
            return

        # Sample holder dimensions (from config)
        holder_diameter_mm = self.config['sample_chamber']['holder_diameter_mm']
        voxel_size_um = self.config['display']['voxel_size_um'][0]  # Isotropic
        holder_radius_voxels = int((holder_diameter_mm * 1000 / 2) / voxel_size_um)

        # Get chamber dimensions (Z, Y, X)
        dims = self.voxel_storage.display_dims

        # Get INITIAL position from sliders
        x_mm = self.position_sliders['x_slider'].value() / 1000.0
        stage_y_mm = self.position_sliders['y_slider'].value() / 1000.0
        z_mm = self.position_sliders['z_slider'].value() / 1000.0

        # Convert stage Y to chamber Y (where extension tip is)
        chamber_y_tip_mm = self._stage_y_to_chamber_y(stage_y_mm)
        chamber_y_base_mm = chamber_y_tip_mm - self.extension_length_mm

        # Convert to napari coordinates
        napari_x, _, napari_z = self.coord_mapper.physical_to_napari(x_mm, 0, z_mm)
        _, napari_y_tip, _ = self.coord_mapper.physical_to_napari(0, chamber_y_tip_mm, 0)
        _, napari_y_base, _ = self.coord_mapper.physical_to_napari(0, chamber_y_base_mm, 0)

        # Store holder TIP position (what matters for data attachment)
        self.holder_position = {
            'x': napari_x,
            'y': napari_y_tip,
            'z': napari_z
        }

        logger.info(f"Initial stage position: ({x_mm:.2f}, {stage_y_mm:.2f}, {z_mm:.2f}) mm")
        logger.info(f"Initial chamber Y: tip={chamber_y_tip_mm:.2f}mm, base={chamber_y_base_mm:.2f}mm")
        logger.info(f"Initial napari position: X={napari_x}, Y_tip={napari_y_tip}, Y_base={napari_y_base}, Z={napari_z}")
        logger.info(f"Chamber dims (Z,Y,X): {dims}")

        # Simplified: Show holder as just a single ball at chamber top
        # This represents the mounting point without cluttering the visualization
        holder_point = np.array([[napari_z, 0, napari_x]])  # Single point at top (Y=0)

        logger.info(f"Created holder indicator at chamber top: (Z,Y,X) = ({napari_z}, 0, {napari_x})")

        self.viewer.add_points(
            holder_point,
            name='Sample Holder',
            size=holder_radius_voxels * 2,  # Diameter for point size
            face_color='gray',
            border_color='darkgray',
            border_width=0.05,
            opacity=0.6,
            shading='spherical'
        )

    def _add_fine_extension(self):
        """
        Add fine extension (thin probe) showing sample position.

        The extension tip is at the imaging position (sample location).
        Extension extends UPWARD from tip by 10mm (reaching toward chamber top).
        """
        if not self.viewer:
            return

        # Extension dimensions
        voxel_size_mm = self.coord_mapper.voxel_size_mm
        extension_radius_voxels = int((self.extension_diameter_mm / 2) / voxel_size_mm)
        extension_length_voxels = int(self.extension_length_mm / voxel_size_mm)

        # Get extension TIP position (stored in holder_position)
        napari_x = self.holder_position['x']
        napari_y_tip = self.holder_position['y']  # Extension tip (where sample is attached)
        napari_z = self.holder_position['z']

        # Extension extends UPWARD from tip by 10mm
        # CRITICAL: Napari Y inverted - upward means DECREASING Y
        # Tip at Y=140 (7mm chamber) → Top at Y=140-200 = -60 (above chamber, OK)
        napari_y_top = napari_y_tip - extension_length_voxels  # Top is ABOVE tip (smaller Y)

        extension_points = []

        # Extension goes from top (smaller Y) to tip (larger Y)
        y_start = max(0, napari_y_top)  # Clamp to chamber top if needed
        y_end = napari_y_tip  # End at tip

        # Create vertical line of points for extension
        # Napari coordinates: (Z, Y, X) order
        for y in range(y_start, y_end + 1, 2):
            extension_points.append([napari_z, y, napari_x])

        logger.info(f"Created {len(extension_points)} extension points (Y from {y_start} to {y_end}, unclamped)")

        if extension_points:
            extension_array = np.array(extension_points)
            self.viewer.add_points(
                extension_array,
                name='Fine Extension',
                size=4,  # Fixed size for visibility (was too small at 2 pixels)
                face_color='#FFFF00',  # Bright yellow for high visibility
                border_color='#FFA500',  # Orange border
                border_width=0.1,
                opacity=0.9,  # High opacity to be clearly visible
                shading='spherical'
            )

    def _add_objective_indicator(self):
        """Add objective position indicator at Z=0 (back wall, in YX plane)."""
        if not self.viewer:
            return

        dims = self.voxel_storage.display_dims  # (Z, Y, X)

        # Objective at Z=0 (back wall, Axis 0)
        # Circle varies in Y and X dimensions (Axes 1 and 2)
        z_objective = 0  # Back wall - OBJECTIVE LOCATION (Axis 0)

        # Objective focal plane is at Y=7mm (where sample holder tip should be centered)
        # This is the physical Y position where the objective focuses
        y_objective_mm = 7.0  # Fixed focal plane position

        # Convert physical Y to napari coordinates
        # Napari Y is inverted: high napari Y = low physical Y
        napari_y_objective = int((self.coord_mapper.y_range_mm[1] - y_objective_mm) /
                                 (self.coord_mapper.voxel_size_mm))

        center_y = napari_y_objective  # Y at objective focal plane (Axis 1)
        center_x = dims[2] // 2  # X center (Axis 2)

        # Circle radius (about 1/6 of the smaller dimension for visibility)
        radius = min(dims[1], dims[2]) // 6

        # Create circle as a series of points in the YX plane at Z=0
        num_points = 36  # Points to form the circle
        angles = np.linspace(0, 2 * np.pi, num_points, endpoint=False)

        circle_points = []
        for angle in angles:
            y = center_y + radius * np.cos(angle)
            x = center_x + radius * np.sin(angle)
            # Napari coordinates in (Z, Y, X) order - circle in YX plane at Z=0
            circle_points.append([z_objective, y, x])

        # Add as thin line circle (less obtrusive than bright points)
        self.viewer.add_shapes(
            data=[[circle_points[i], circle_points[(i+1) % len(circle_points)]]
                  for i in range(len(circle_points))],
            shape_type='line',
            name='Objective',
            edge_color='#666600',  # Dim yellow/olive (less bright)
            edge_width=1,
            opacity=0.3
        )

        logger.info(f"Added objective indicator at Z=0 (back wall), center Y={center_y}, X={center_x}")

    def _add_rotation_indicator(self):
        """Add rotation indicator in ZX plane at Y=0 (follows sample holder XZ position)."""
        if not self.viewer:
            return

        # Indicator dimensions - 1/2 the shortest chamber width
        dims = self.voxel_storage.display_dims  # (Z, Y, X)
        indicator_length = min(dims[0], dims[2]) // 2  # 1/2 shortest dimension

        # Position: extends in ZX plane at Y=0 (top of chamber)
        # Follows the sample holder's X and Z position
        y_position = 0  # TOP OF CHAMBER

        # Start at sample holder's XZ position (in napari coords)
        holder_z = self.holder_position['z']  # Z position (Axis 0)
        holder_x = self.holder_position['x']  # X position (Axis 2)

        # Create indicator points in (Z, Y, X) order
        # At 0 degrees, points along +X axis
        indicator_start = np.array([holder_z, y_position, holder_x])
        indicator_end = np.array([holder_z, y_position, holder_x + indicator_length])

        # Get initial color matching rotation angle
        initial_angle = self.current_rotation.get('ry', 0)
        initial_color = self._get_rotation_gradient_color(initial_angle)

        # Add as a line (using shapes layer for better control)
        self.viewer.add_shapes(
            data=[[indicator_start, indicator_end]],  # 3D line in (Z, Y, X) order
            shape_type='line',
            name='Rotation Indicator',
            edge_color=initial_color,
            edge_width=3,
            opacity=0.8
        )

        # Store indicator length for rotation updates
        self.rotation_indicator_length = indicator_length

        logger.info(f"Added rotation indicator at Y=0 (top), following holder at Z={holder_z}, X={holder_x}")

    def _add_xy_focus_frame(self):
        """
        Add XY focus frame showing where the camera/objective focal plane is.

        The frame is a bright yellow border at the FOCAL PLANE position,
        showing the camera's field of view. This is FIXED - it does not move with the
        sample holder. The objective and camera are stationary; only the sample moves.

        The frame is positioned:
        - Z: At the focal plane (from calibration, or center of Z range ~19mm)
        - Y: At the objective focal plane (Y=7mm in chamber coordinates)
        - X: Centered in the chamber (or at calibrated position)

        Note: The objective housing is at Z=12.5mm (back wall), but the focal plane
        where things are actually in focus is further into the chamber (~19mm).
        """
        if not self.viewer:
            return

        # Get focus frame configuration
        focus_config = self.config.get('focus_frame', {})
        fov_x_mm = focus_config.get('field_of_view_x_mm', 0.52)
        fov_y_mm = focus_config.get('field_of_view_y_mm', 0.52)
        frame_color = focus_config.get('color', '#FFFF00')
        edge_width = focus_config.get('edge_width', 3)
        opacity = focus_config.get('opacity', 0.9)

        # FIXED position at focal plane - does NOT follow sample holder
        # Y = objective focal plane (fixed at 7mm in chamber coordinates)
        chamber_y_mm = self.OBJECTIVE_CHAMBER_Y_MM  # 7.0 mm

        # X and Z from calibration, or use defaults
        if self.objective_xy_calibration:
            # Use calibrated position (from when sample holder tip was in focus)
            x_mm = self.objective_xy_calibration['x']
            z_mm = self.objective_xy_calibration['z']
            logger.info(f"Using calibrated focal plane: X={x_mm:.2f}, Z={z_mm:.2f} mm")
        else:
            # Default to center of X and Z ranges
            x_mm = (self.coord_mapper.x_range_mm[0] + self.coord_mapper.x_range_mm[1]) / 2
            z_mm = (self.coord_mapper.z_range_mm[0] + self.coord_mapper.z_range_mm[1]) / 2
            logger.info(f"Using default focal plane (center): X={x_mm:.2f}, Z={z_mm:.2f} mm")

        # FOV half-widths
        # FOV X maps to physical X, FOV Y maps to chamber Y (vertical in the frame)
        half_fov_x = fov_x_mm / 2
        half_fov_y = fov_y_mm / 2

        # Frame corners in physical mm (X, chamber_Y, Z)
        # The frame is in the XY plane at the focal plane Z depth
        corners_mm = [
            (x_mm - half_fov_x, chamber_y_mm - half_fov_y, z_mm),  # bottom-left
            (x_mm + half_fov_x, chamber_y_mm - half_fov_y, z_mm),  # bottom-right
            (x_mm + half_fov_x, chamber_y_mm + half_fov_y, z_mm),  # top-right
            (x_mm - half_fov_x, chamber_y_mm + half_fov_y, z_mm),  # top-left
        ]

        # Convert to napari coordinates (Z, Y, X) order
        napari_corners = []
        for cx, cy, cz in corners_mm:
            napari_x, napari_y, napari_z = self.coord_mapper.physical_to_napari(cx, cy, cz)
            napari_corners.append([napari_z, napari_y, napari_x])

        # Create frame edges (rectangle outline)
        frame_edges = [
            [napari_corners[0], napari_corners[1]],  # bottom edge
            [napari_corners[1], napari_corners[2]],  # right edge
            [napari_corners[2], napari_corners[3]],  # top edge
            [napari_corners[3], napari_corners[0]],  # left edge
        ]

        self.viewer.add_shapes(
            data=frame_edges,
            shape_type='line',
            name='XY Focus Frame',
            edge_color=frame_color,
            edge_width=edge_width,
            opacity=opacity
        )

        logger.info(f"Added XY focus frame at OBJECTIVE position: X={x_mm:.2f}, Y={chamber_y_mm:.2f}, Z={z_mm:.2f} mm "
                   f"(FOV: {fov_x_mm:.2f}x{fov_y_mm:.2f} mm)")

    def _update_xy_focus_frame(self):
        """
        Update XY focus frame position based on calibration.

        The focus frame is at a FIXED position (focal plane) and only needs
        to be updated when the calibration changes, not when the stage moves.
        """
        if not self.viewer or 'XY Focus Frame' not in self.viewer.layers:
            return

        # Get focus frame configuration
        focus_config = self.config.get('focus_frame', {})
        fov_x_mm = focus_config.get('field_of_view_x_mm', 0.52)
        fov_y_mm = focus_config.get('field_of_view_y_mm', 0.52)

        # FIXED position at focal plane (NOT back wall)
        chamber_y_mm = self.OBJECTIVE_CHAMBER_Y_MM  # 7.0 mm

        # X and Z from calibration or use defaults
        if self.objective_xy_calibration:
            x_mm = self.objective_xy_calibration['x']
            z_mm = self.objective_xy_calibration['z']  # Use calibrated Z (focal plane)
        else:
            x_mm = (self.coord_mapper.x_range_mm[0] + self.coord_mapper.x_range_mm[1]) / 2
            z_mm = (self.coord_mapper.z_range_mm[0] + self.coord_mapper.z_range_mm[1]) / 2  # Center of Z range

        # FOV half-widths
        half_fov_x = fov_x_mm / 2
        half_fov_y = fov_y_mm / 2

        # Frame corners in physical mm
        corners_mm = [
            (x_mm - half_fov_x, chamber_y_mm - half_fov_y, z_mm),
            (x_mm + half_fov_x, chamber_y_mm - half_fov_y, z_mm),
            (x_mm + half_fov_x, chamber_y_mm + half_fov_y, z_mm),
            (x_mm - half_fov_x, chamber_y_mm + half_fov_y, z_mm),
        ]

        # Convert to napari coordinates
        napari_corners = []
        for cx, cy, cz in corners_mm:
            napari_x, napari_y, napari_z = self.coord_mapper.physical_to_napari(cx, cy, cz)
            napari_corners.append([napari_z, napari_y, napari_x])

        # Update frame edges
        frame_edges = [
            [napari_corners[0], napari_corners[1]],
            [napari_corners[1], napari_corners[2]],
            [napari_corners[2], napari_corners[3]],
            [napari_corners[3], napari_corners[0]],
        ]

        self.viewer.layers['XY Focus Frame'].data = frame_edges

        logger.info(f"Updated XY focus frame to X={x_mm:.2f}, Y={chamber_y_mm:.2f}, Z={z_mm:.2f} mm")

    def _stage_y_to_chamber_y(self, stage_y_mm: float) -> float:
        """
        Convert stage Y position to chamber Y coordinate.

        The stage Y is a control parameter. Chamber Y is the actual position
        in the visualization chamber. Reference: at stage Y=7.45mm, the extension
        tip is at the objective focal plane (Y=7.0mm in chamber coordinates).

        Args:
            stage_y_mm: Stage Y position in mm

        Returns:
            Chamber Y position in mm where the extension tip is located
        """
        # Tip position in chamber = objective position + offset from reference
        chamber_y_tip = self.OBJECTIVE_CHAMBER_Y_MM + (stage_y_mm - self.STAGE_Y_AT_OBJECTIVE)
        return chamber_y_tip

    def _update_sample_holder_position(self, x_mm: float, y_mm: float, z_mm: float):
        """
        Update sample holder position when stage moves.

        The stage Y position controls where the sample holder is positioned.
        At stage Y=7.45mm, the extension tip is at the objective (Y=7mm chamber coords).

        Args:
            x_mm, y_mm, z_mm: Physical stage coordinates in mm (y_mm is stage control value)
        """
        if not self.viewer or 'Sample Holder' not in self.viewer.layers:
            return

        # Convert stage Y to chamber Y (where extension tip actually is)
        chamber_y_tip_mm = self._stage_y_to_chamber_y(y_mm)

        # Holder base is above the tip by the extension length
        chamber_y_base_mm = chamber_y_tip_mm - self.extension_length_mm

        # Convert chamber coordinates to napari
        # For X and Z, use stage values directly (they are absolute positions)
        napari_x, _, napari_z = self.coord_mapper.physical_to_napari(x_mm, 0, z_mm)

        # For Y, convert the holder base position
        _, napari_y_base, _ = self.coord_mapper.physical_to_napari(0, chamber_y_base_mm, 0)
        _, napari_y_tip, _ = self.coord_mapper.physical_to_napari(0, chamber_y_tip_mm, 0)

        # Update holder position (store the TIP position, which is what matters)
        self.holder_position = {
            'x': napari_x,
            'y': napari_y_tip,  # Use tip position, not base
            'z': napari_z
        }

        logger.info(f"Stage position: ({x_mm:.2f}, {y_mm:.2f}, {z_mm:.2f}) mm")
        logger.info(f"Chamber Y tip: {chamber_y_tip_mm:.2f} mm")
        logger.info(f"Napari Y tip: {napari_y_tip}")

        # Simplified: Holder shown as single ball at chamber top (just a reference point)
        holder_point = np.array([[napari_z, 0, napari_x]])
        self.viewer.layers['Sample Holder'].data = holder_point

        logger.info(f"Updated holder position indicator at chamber top")

        # Update rotation indicator position (stays at top)
        self._update_rotation_indicator()

        # Update fine extension position
        self._update_fine_extension()

        # NOTE: XY focus frame is NOT updated here - it's FIXED at the objective position
        # and only updates when calibration changes (via set_objective_calibration)

    def _update_fine_extension(self):
        """
        Update fine extension position.

        Extension shows where the sample is positioned. The tip is at the sample location,
        and it extends upward 10mm (toward chamber top) for visibility.
        """
        if not self.viewer or 'Fine Extension' not in self.viewer.layers:
            return

        # Get current TIP position (where sample is)
        napari_x = self.holder_position['x']
        napari_y_tip = self.holder_position['y']  # Extension tip
        napari_z = self.holder_position['z']

        # Extension extends UPWARD from tip by 10mm
        # Napari Y inverted: upward = DECREASING Y values
        voxel_size_mm = self.coord_mapper.voxel_size_mm
        extension_length_voxels = int(self.extension_length_mm / voxel_size_mm)
        napari_y_top = napari_y_tip - extension_length_voxels  # Top is ABOVE tip (smaller Y)

        extension_points = []

        # Extension from top (smaller Y) to tip (larger Y)
        y_start = max(0, napari_y_top)  # Clamp to chamber top
        y_end = napari_y_tip  # End at tip (sample position)

        # Create vertical line of points in (Z, Y, X) order
        for y in range(y_start, y_end + 1, 2):
            extension_points.append([napari_z, y, napari_x])

        # Update the layer
        if extension_points:
            self.viewer.layers['Fine Extension'].data = np.array(extension_points)
        else:
            # If no points, show minimal placeholder
            self.viewer.layers['Fine Extension'].data = np.array([[napari_z, y_start, napari_x]])

    def _update_rotation_indicator(self):
        """Update rotation indicator based on current rotation (follows sample holder XZ position)."""
        if not self.viewer or 'Rotation Indicator' not in self.viewer.layers:
            return

        # Get Y-axis rotation (the physical stage rotation)
        angle_deg = self.current_rotation.get('ry', 0)
        angle_rad = np.radians(angle_deg)

        # Get color matching the rotation slider gradient
        indicator_color = self._get_rotation_gradient_color(angle_deg)

        # Indicator always at Y=0 (top of chamber), extends in ZX plane
        y_position = 0  # TOP OF CHAMBER

        # Start position follows sample holder's X and Z position (in napari coords)
        # But always at Y=0 (top)
        start = np.array([
            self.holder_position['z'],  # Z coordinate (napari index 0)
            y_position,                  # Y coordinate (napari index 1) - always at top
            self.holder_position['x']   # X coordinate (napari index 2)
        ])

        # Calculate end point displacement based on Y rotation
        # Rotation around Y axis (vertical) affects Z and X coordinates
        # At 0°, indicator points in +X direction
        # Napari coords are (Z, Y, X), so rotation affects indices 0 and 2
        dx = self.rotation_indicator_length * np.cos(angle_rad)
        dz = self.rotation_indicator_length * np.sin(angle_rad)

        # End position rotated in ZX plane (indices 0 and 2)
        end = np.array([
            start[0] + dz,   # Z coordinate (index 0)
            y_position,      # Y coordinate (index 1) - always at top
            start[2] + dx    # X coordinate (index 2)
        ])

        # Update the line - provide 3D coordinates in (Z, Y, X) order
        self.viewer.layers['Rotation Indicator'].data = [[start, end]]
        self.viewer.layers['Rotation Indicator'].edge_color = [indicator_color]

    def _setup_data_layers(self):
        """Setup napari layers for multi-channel data."""
        if not self.viewer:
            return

        # Create empty layers for each channel
        for ch_config in self.config['channels']:
            ch_id = ch_config['id']
            ch_name = ch_config['name']

            # Create empty volume
            empty_volume = np.zeros(self.voxel_storage.display_dims, dtype=np.uint16)

            # Add layer with default contrast limits for downsampled data
            layer = self.viewer.add_image(
                empty_volume,
                name=ch_name,
                colormap=ch_config['default_colormap'],
                visible=ch_config.get('default_visible', True),
                blending='additive',
                opacity=0.8,
                rendering='mip',
                contrast_limits=(0, 50)  # Default 0-50 for downsampled display
            )

            # Store layer reference
            if not hasattr(self, 'channel_layers'):
                self.channel_layers = {}
            self.channel_layers[ch_id] = layer

        # Test data generation removed - use real camera data or call _generate_test_sample_data() manually for testing

    def _generate_test_sample_data(self):
        """Generate test sample data for visualization testing."""
        # Calculate sample data size in voxels
        voxel_size_mm = self.coord_mapper.voxel_size_mm
        sample_size_voxels = int(self.test_sample_size_mm / voxel_size_mm)

        # Create 4-channel test data (raw, unrotated)
        # Make Y dimension 2x larger to test out-of-bounds behavior
        self.test_sample_data_raw = {}
        data_shape = (sample_size_voxels * 2, sample_size_voxels, sample_size_voxels)  # (Y, X, Z) - Y doubled

        for ch_id in range(4):
            # Create a 3D volume for this channel
            data = np.zeros(data_shape, dtype=np.uint16)

            if ch_id == 0:  # DAPI - small spheres (nuclei)
                # Create 3-5 nuclei
                for _ in range(4):
                    cx = np.random.randint(10, data_shape[1]-10)
                    cy = np.random.randint(10, data_shape[0]-10)
                    cz = np.random.randint(10, data_shape[2]-10)
                    radius = np.random.randint(3, 6)
                    y, x, z = np.ogrid[:data_shape[0], :data_shape[1], :data_shape[2]]
                    mask = (x-cx)**2 + (y-cy)**2 + (z-cz)**2 <= radius**2
                    data[mask] = np.random.randint(30000, 50000)

            elif ch_id == 1:  # GFP - diffuse signal
                # Create diffuse cloud (elongated in Y)
                center_x = data_shape[1] // 2
                center_y = data_shape[0] // 2
                center_z = data_shape[2] // 2
                y, x, z = np.ogrid[:data_shape[0], :data_shape[1], :data_shape[2]]
                dist = np.sqrt((x-center_x)**2 + ((y-center_y)*0.5)**2 + (z-center_z)**2)
                data = np.clip(20000 * np.exp(-dist/(data_shape[1]/4)), 0, 65535).astype(np.uint16)

            elif ch_id == 2:  # RFP - linear structures
                # Create some "fibers"
                for _ in range(3):
                    start = np.array([np.random.randint(0, data_shape[0]),
                                     np.random.randint(0, data_shape[1]),
                                     np.random.randint(0, data_shape[2])])
                    direction = np.random.randn(3)
                    direction /= np.linalg.norm(direction)
                    for t in range(data_shape[0]//2):
                        pos = start + t * direction
                        py, px, pz = np.clip(pos.astype(int),
                                            [0, 0, 0],
                                            [data_shape[0]-1, data_shape[1]-1, data_shape[2]-1])
                        # Add thickness
                        for dx in range(-1, 2):
                            for dy in range(-1, 2):
                                for dz in range(-1, 2):
                                    x, y, z = px+dx, py+dy, pz+dz
                                    if 0 <= x < data_shape[1] and 0 <= y < data_shape[0] and 0 <= z < data_shape[2]:
                                        data[y, x, z] = max(data[y, x, z], 25000)

            elif ch_id == 3:  # Far-Red - sparse bright spots
                # Random bright spots
                for _ in range(5):
                    cx = np.random.randint(5, data_shape[1]-5)
                    cy = np.random.randint(5, data_shape[0]-5)
                    cz = np.random.randint(5, data_shape[2]-5)
                    radius = np.random.randint(2, 4)
                    y, x, z = np.ogrid[:data_shape[0], :data_shape[1], :data_shape[2]]
                    mask = (x-cx)**2 + (y-cy)**2 + (z-cz)**2 <= radius**2
                    data[mask] = np.random.randint(35000, 55000)

            self.test_sample_data_raw[ch_id] = data

        # Log data statistics
        for ch_id, data in self.test_sample_data_raw.items():
            nonzero_count = np.count_nonzero(data)
            max_val = np.max(data)
            logger.info(f"Channel {ch_id}: {nonzero_count} non-zero voxels, max intensity: {max_val}")

        logger.info(f"Generated test sample data: {data_shape} voxels per channel (Y dimension 2x for testing)")

    def _update_sample_data_visualization(self):
        """Update the sample data visualization with direct dense array updates (optimized)."""
        import time
        t_start = time.time()

        # Early returns
        if not self.viewer or self.test_sample_data_raw is None:
            return

        print(f"PERF: Update started")

        # Get current physical position and rotation
        x_mm = self.position_sliders['x_slider'].value() / 1000.0
        y_mm = self.position_sliders['y_slider'].value() / 1000.0
        z_mm = self.position_sliders['z_slider'].value() / 1000.0
        rotation_deg = self.current_rotation.get('ry', 0)

        t_clear_start = time.time()

        # Clear previous data directly from napari layers (fast!)
        for ch_id in range(4):
            if ch_id in self.previous_sample_bounds and ch_id in self.channel_layers:
                prev_bounds = self.previous_sample_bounds[ch_id]
                pz_start, pz_end, py_start, py_end, px_start, px_end = prev_bounds
                # Direct numpy array clear (< 1ms)
                self.channel_layers[ch_id].data[pz_start:pz_end, py_start:py_end, px_start:px_end] = 0

        print(f"PERF: Clear took {(time.time() - t_clear_start)*1000:.1f}ms")

        # Calculate sample data position (below EXTENSION tip, not holder tip)
        # Extension extends down from holder by extension_length_mm
        extension_tip_y_mm = y_mm - self.extension_length_mm
        sample_center_y_mm = extension_tip_y_mm - self.test_sample_offset_mm - (self.test_sample_size_mm / 2)

        print(f"DEBUG: Holder Y={y_mm:.2f}mm, Extension tip Y={extension_tip_y_mm:.2f}mm, Sample center Y={sample_center_y_mm:.2f}mm")

        # Convert center to napari coordinates
        sample_x, sample_y, sample_z = self.coord_mapper.physical_to_napari(
            x_mm, sample_center_y_mm, z_mm
        )

        # Apply rotation transform to sample data
        voxel_size_mm = self.coord_mapper.voxel_size_mm
        sample_size_voxels = int(self.test_sample_size_mm / voxel_size_mm)

        # Get actual data shape (Y dimension is 2x)
        first_data = next(iter(self.test_sample_data_raw.values()))
        data_y_size, data_x_size, data_z_size = first_data.shape  # (Y, X, Z) order

        print(f"DEBUG: Sample data shape (Y,X,Z): {first_data.shape}")
        print(f"DEBUG: Starting channel updates, {len(self.test_sample_data_raw)} channels, rotation={rotation_deg}°")

        t_rotation_total = 0
        t_sparse_total = 0
        t_dense_total = 0
        t_napari_total = 0

        # Update each channel
        for ch_id, raw_data in self.test_sample_data_raw.items():
            if ch_id not in self.channel_layers:
                print(f"DEBUG: Channel {ch_id} not in channel_layers!")
                logger.warning(f"Channel {ch_id} not in channel_layers, skipping")
                continue

            # Apply rotation to the data
            t_rot_start = time.time()
            rotated_data = self._rotate_sample_data(raw_data, rotation_deg)
            t_rotation_total += (time.time() - t_rot_start)

            # Log rotation results
            nonzero_before = np.count_nonzero(raw_data)
            nonzero_after = np.count_nonzero(rotated_data)
            logger.info(f"Ch{ch_id} rotation: {nonzero_before} → {nonzero_after} non-zero voxels")

            # Transpose to (Z, Y, X) for napari
            rotated_transposed = np.transpose(rotated_data, (2, 0, 1))  # Now (Z, Y, X)

            # Get rotated data dimensions
            rot_z_size, rot_y_size, rot_x_size = rotated_transposed.shape

            # Calculate half sizes for centering
            half_z = rot_z_size // 2
            half_y = rot_y_size // 2
            half_x = rot_x_size // 2

            # Calculate data position in napari space (may be out of bounds!)
            data_z_start = sample_z - half_z
            data_z_end = sample_z + half_z
            data_y_start = sample_y - half_y
            data_y_end = sample_y + half_y
            data_x_start = sample_x - half_x
            data_x_end = sample_x + half_x

            print(f"DEBUG: Data position before clipping: Z=[{data_z_start},{data_z_end}], Y=[{data_y_start},{data_y_end}], X=[{data_x_start},{data_x_end}]")

            # Calculate intersection with visible volume (clip to bounds)
            visible_z_start = max(0, data_z_start)
            visible_z_end = min(self.voxel_storage.display_dims[0], data_z_end)
            visible_y_start = max(0, data_y_start)
            visible_y_end = min(self.voxel_storage.display_dims[1], data_y_end)
            visible_x_start = max(0, data_x_start)
            visible_x_end = min(self.voxel_storage.display_dims[2], data_x_end)

            # Check if any part is visible
            if (visible_z_start >= visible_z_end or
                visible_y_start >= visible_y_end or
                visible_x_start >= visible_x_end):
                print(f"DEBUG: Ch{ch_id} completely outside visible volume")
                # Clear cached bounds (data is now invisible)
                if ch_id in self.previous_sample_bounds:
                    del self.previous_sample_bounds[ch_id]
                continue

            bounds = (visible_z_start, visible_z_end, visible_y_start, visible_y_end, visible_x_start, visible_x_end)

            # Calculate corresponding region in rotated data
            src_z_start = visible_z_start - data_z_start
            src_z_end = src_z_start + (visible_z_end - visible_z_start)
            src_y_start = visible_y_start - data_y_start
            src_y_end = src_y_start + (visible_y_end - visible_y_start)
            src_x_start = visible_x_start - data_x_start
            src_x_end = src_x_start + (visible_x_end - visible_x_start)

            # Extract visible portion
            data_to_place = rotated_transposed[
                src_z_start:src_z_end,
                src_y_start:src_y_end,
                src_x_start:src_x_end
            ]

            print(f"DEBUG: Visible portion: {data_to_place.shape}, non-zero: {np.count_nonzero(data_to_place)}")

            # Direct dense array update (FAST!)
            t_update_start = time.time()
            self.channel_layers[ch_id].data[visible_z_start:visible_z_end,
                                           visible_y_start:visible_y_end,
                                           visible_x_start:visible_x_end] = data_to_place

            # Trigger napari to detect the change (CRITICAL!)
            self.channel_layers[ch_id].refresh()

            t_napari_total += (time.time() - t_update_start)

            # Store bounds for next update
            self.previous_sample_bounds[ch_id] = bounds

        # Calculate memory usage (dense arrays)
        total_voxels = sum(np.count_nonzero(self.channel_layers[ch_id].data)
                          for ch_id in range(4) if ch_id in self.channel_layers)
        memory_mb = (np.prod(self.voxel_storage.display_dims) * 4 * 2) / (1024 * 1024)  # 4 channels, uint16

        self.memory_label.setText(f"Memory: {memory_mb:.1f} MB")
        self.voxel_count_label.setText(f"Voxels: {total_voxels:,}")

        t_total = (time.time() - t_start) * 1000
        print(f"PERF: Total={t_total:.1f}ms | Clear={((time.time()-t_clear_start)*1000 - t_total):.1f}ms | Rotation={t_rotation_total*1000:.1f}ms | Update={t_napari_total*1000:.1f}ms")

        logger.debug(f"Updated sample data at ({x_mm:.2f}, {y_mm:.2f}, {z_mm:.2f}) mm, rotation={rotation_deg}°")

    def _rotate_sample_data(self, data: np.ndarray, rotation_deg: float) -> np.ndarray:
        """
        Apply Y-axis rotation to sample data.

        Args:
            data: 3D array in (Y, X, Z) order
            rotation_deg: Rotation angle in degrees around Y axis

        Returns:
            Rotated 3D array in same order
        """
        if abs(rotation_deg) < 0.1:
            # No rotation needed
            return data

        from scipy.ndimage import rotate

        # Rotate around Y axis (axis=0 in the data array which is Y,X,Z ordered)
        # Y-axis rotation affects X and Z, so we rotate in the XZ plane (axes 1 and 2)
        rotated = rotate(data, rotation_deg, axes=(2, 1), reshape=False,
                        order=1, mode='constant', cval=0)

        return rotated.astype(np.uint16)

    def _on_populate_toggled(self, checked: bool):
        """Handle populate from live view start/stop."""
        self.is_populating = checked

        if checked:
            self.populate_button.setText("Stop Populating")
            self.status_label.setText("Status: Populating from Live View...")

            # Reset frame tracking to allow new frames
            self._last_processed_frame_number = -1
            self._populate_tick_count = 0

            # Reset motion tracking for fresh start
            self._motion_tracking = {
                'in_progress': False,
                'start_position': None,
                'start_time': None,
                'end_position': None,
                'end_time': None,
                'frame_buffer': [],
                'moving_axis': None,
            }

            # Start frame capture timer if camera controller available
            if self.camera_controller:
                self.populate_timer.start()
                # Slow down visualization updates during populate to reduce CPU load
                # Normal: 10 Hz, During populate: 2 Hz (every 500ms)
                self.update_timer.setInterval(500)
                logger.info("Started populating from Live View (10 Hz capture, 2 Hz viz update)")
            else:
                logger.warning("No camera controller - populate disabled")
                self.populate_button.setChecked(False)
                self.status_label.setText("Status: No camera controller")

        else:
            self.populate_button.setText("Populate from Live View")
            self.status_label.setText("Status: Stopped")
            self.populate_timer.stop()
            # Restore normal visualization update rate
            self.update_timer.setInterval(100)  # 10 Hz
            logger.info("Stopped populating (viz update restored to 10 Hz)")

    def _on_clear_data(self):
        """Clear all accumulated data."""
        reply = QMessageBox.question(
            self, "Clear Data",
            "Are you sure you want to clear all accumulated data?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.voxel_storage.clear()
            self._last_processed_frame_number = -1  # Allow new frames after clearing
            # Reset motion tracking
            self._motion_tracking['in_progress'] = False
            self._motion_tracking['frame_buffer'] = []
            self._motion_tracking['moving_axis'] = None
            self._update_visualization()
            self.status_label.setText("Status: Data cleared")
            logger.info("Cleared all visualization data")

    def _on_export_data(self):
        """Export visualization data."""
        # TODO: Implement export functionality
        QMessageBox.information(self, "Export", "Export functionality not yet implemented")

    def _on_x_slider_changed(self, value: int):
        """Handle X slider position changes (local visualization update only)."""
        x_mm = value / 1000.0  # Convert µm to mm
        self.x_position_changed.emit(x_mm)
        # Update sample holder position using coordinate mapper
        y_mm = self.position_sliders['y_slider'].value() / 1000.0
        z_mm = self.position_sliders['z_slider'].value() / 1000.0
        self._update_sample_holder_position(x_mm, y_mm, z_mm)

    def _on_y_slider_changed(self, value: int):
        """Handle Y slider position changes (local visualization update only)."""
        y_mm = value / 1000.0  # Convert µm to mm
        self.y_position_changed.emit(y_mm)
        # Update sample holder position using coordinate mapper
        x_mm = self.position_sliders['x_slider'].value() / 1000.0
        z_mm = self.position_sliders['z_slider'].value() / 1000.0
        self._update_sample_holder_position(x_mm, y_mm, z_mm)

    def _on_z_slider_changed(self, value: int):
        """Handle Z slider position changes (local visualization update only)."""
        z_mm = value / 1000.0  # Convert µm to mm
        self.z_position_changed.emit(z_mm)
        # Update sample holder position using coordinate mapper
        x_mm = self.position_sliders['x_slider'].value() / 1000.0
        y_mm = self.position_sliders['y_slider'].value() / 1000.0
        self._update_sample_holder_position(x_mm, y_mm, z_mm)

    def _on_rotation_slider_changed(self, value: int):
        """Handle rotation slider changes (real-time updates)."""
        # Update spinbox without triggering its signals
        self.rotation_spinbox.blockSignals(True)
        self.rotation_spinbox.setValue(float(value))
        self.rotation_spinbox.blockSignals(False)

        # Apply rotation immediately
        self._apply_rotation(value)

    def _on_rotation_spinbox_changed(self, value: float):
        """Handle rotation spinbox changes (debounced for typing, instant for arrows)."""
        # Update slider without triggering its signals
        self.rotation_slider.blockSignals(True)
        self.rotation_slider.setValue(int(value))
        self.rotation_slider.blockSignals(False)

        # Start/restart debounce timer
        # This makes arrow clicks feel instant (~150ms) but debounces rapid typing
        self.rotation_debounce_timer.stop()
        self.rotation_debounce_timer.start(self.rotation_debounce_delay_ms)

    def _apply_rotation_from_spinbox(self):
        """Apply rotation from spinbox value (called after debounce or Enter)."""
        # Stop debounce timer if running
        self.rotation_debounce_timer.stop()

        # Apply the rotation
        value = self.rotation_spinbox.value()
        self._apply_rotation(int(value))

    def _apply_rotation(self, value: int):
        """Apply rotation update to visualization."""
        # Update current rotation (only Y axis rotation)
        self.current_rotation['ry'] = value

        # Update transformer
        self.transformer.set_rotation(**self.current_rotation)

        # Emit signal
        self.rotation_changed.emit(self.current_rotation)

        # Update rotation indicator (data is rotation-invariant in storage)
        self._update_rotation_indicator()

    def _on_channel_visibility_changed(self, channel_id: int, visible: bool):
        """Handle channel visibility changes."""
        if self.viewer and channel_id in self.channel_layers:
            self.channel_layers[channel_id].visible = visible
        self.channel_visibility_changed.emit(channel_id, visible)

    def _sync_contrast_sliders_with_napari(self):
        """Sync contrast range sliders with napari's actual contrast limits."""
        for ch_id in range(4):
            if ch_id in self.channel_layers and ch_id in self.channel_controls:
                # Get actual contrast limits from napari layer
                actual_limits = self.channel_layers[ch_id].contrast_limits

                # Update range slider to match (without triggering updates)
                controls = self.channel_controls[ch_id]

                controls['contrast_range'].blockSignals(True)
                controls['contrast_range'].setValue((int(actual_limits[0]), int(actual_limits[1])))
                controls['contrast_label'].setText(f"{int(actual_limits[0])} - {int(actual_limits[1])}")
                controls['contrast_range'].blockSignals(False)

                logger.info(f"Synced Ch{ch_id} contrast range to napari: {actual_limits}")

    def _on_colormap_changed(self, channel_id: int, colormap: str):
        """Handle colormap change for a channel."""
        if self.viewer and channel_id in self.channel_layers:
            self.channel_layers[channel_id].colormap = colormap
            logger.debug(f"Changed channel {channel_id} colormap to {colormap}")

    def _on_opacity_changed(self, channel_id: int, opacity: float):
        """Handle opacity change for a channel."""
        if self.viewer and channel_id in self.channel_layers:
            self.channel_layers[channel_id].opacity = opacity
            logger.debug(f"Changed channel {channel_id} opacity to {opacity:.2f}")

    def _on_contrast_range_changed(self, channel_id: int, value: tuple, label: QLabel):
        """Handle contrast range slider change (min and max together)."""
        min_val, max_val = value

        # Update label
        label.setText(f"{min_val} - {max_val}")

        # Update napari layer contrast limits
        if self.viewer and channel_id in self.channel_layers:
            self.channel_layers[channel_id].contrast_limits = (min_val, max_val)
            logger.debug(f"Channel {channel_id} contrast: {min_val} - {max_val}")

    def _update_contrast_slider_range(self, channel_id: int, force_napari_update: bool = False):
        """
        Update contrast slider range based on maximum value recorded.

        Sets the slider max to the current max intensity value for the channel,
        making it easier to adjust contrast for the actual data range.

        PERFORMANCE: Only updates napari layer contrast when slider actually changes,
        to avoid redundant renders. The .data assignment already triggers a render.

        Args:
            channel_id: Channel to update
            force_napari_update: Force napari contrast update even if slider unchanged
        """
        if channel_id not in self.channel_controls:
            return

        # Get max value from storage
        max_value = self.voxel_storage.get_channel_max_value(channel_id)
        if max_value <= 0:
            return

        # Add some headroom (10% above max, minimum 100)
        slider_max = max(int(max_value * 1.1), max_value + 100)

        controls = self.channel_controls[channel_id]
        slider = controls['contrast_range']

        # Only update if max changed significantly (>20% change to reduce UI updates)
        # This prevents cascading updates when max increases slightly with each frame
        current_max = slider.maximum()
        threshold_ratio = 1.2  # 20% change threshold
        if slider_max > current_max * threshold_ratio or slider_max < current_max / threshold_ratio:
            # Update slider range
            slider.blockSignals(True)
            current_value = slider.value()
            slider.setRange(0, slider_max)
            # Adjust current value if needed
            new_max_val = min(current_value[1], slider_max)
            slider.setValue((current_value[0], new_max_val))
            controls['contrast_label'].setText(f"{current_value[0]} - {new_max_val}")
            slider.blockSignals(False)

            # Update napari layer contrast_limits
            # PERFORMANCE: Only do this when slider changes, not on every viz update
            # The .data assignment already triggers a render; avoid double-render
            if channel_id in self.channel_layers:
                self.channel_layers[channel_id].contrast_limits = (current_value[0], new_max_val)

            logger.debug(f"Channel {channel_id} contrast range updated: 0 - {slider_max} (max value: {max_value})")

    def _on_display_settings_changed(self):
        """Handle display setting changes."""
        if not self.viewer:
            return

        # Update chamber wireframe visibility (all edge layers)
        chamber_visible = self.show_chamber_cb.isChecked()
        for layer_name in ['Chamber Z-edges', 'Chamber Y-edges', 'Chamber X-edges']:
            if layer_name in self.viewer.layers:
                self.viewer.layers[layer_name].visible = chamber_visible

        # Legacy chamber layer (if exists)
        if 'Chamber' in self.viewer.layers:
            self.viewer.layers['Chamber'].visible = chamber_visible

        # Update objective visibility
        if 'Objective' in self.viewer.layers:
            self.viewer.layers['Objective'].visible = self.show_objective_cb.isChecked()

    def _on_reset_view(self):
        """Reset camera to default orientation and zoom."""
        if not self.viewer:
            return

        # Reset camera to default view
        self.viewer.camera.angles = (45, 30, 0)
        self.viewer.camera.zoom = 1.57
        self.viewer.reset_view()

    def _on_rendering_mode_changed(self, mode: str):
        """Handle rendering mode changes."""
        if not self.viewer:
            return

        # Update all channel layers to use the new rendering mode
        for layer in self.channel_layers.values():
            layer.rendering = mode
        logger.info(f"Rendering mode changed to: {mode}")

    def _update_visualization(self):
        """Update the visualization with latest data."""
        if not self.viewer:
            return

        # Try to acquire mutex, skip update if already updating
        if not self.update_mutex.tryLock():
            return

        try:
            # Always use transformed volume based on last_stage_position
            # This ensures consistent display regardless of pending_stage_update state
            # The transform accounts for stage movement relative to the reference position

            # Get holder position for rotation center (rotation axis is the sample holder)
            holder_pos_voxels = None
            if hasattr(self, 'holder_position') and self.holder_position:
                holder_pos_voxels = np.array([
                    self.holder_position['x'],
                    self.holder_position['y'],
                    self.holder_position['z']
                ])

            for ch_id in range(self.voxel_storage.num_channels):
                if ch_id in self.channel_layers:
                    # Get transformed display volume (rotation around holder axis)
                    volume = self.voxel_storage.get_display_volume_transformed(
                        ch_id, self.last_stage_position, holder_pos_voxels
                    )

                    # Update layer data
                    self.channel_layers[ch_id].data = volume

                    # Update contrast slider range based on actual display values
                    # PERFORMANCE: This is throttled to only update on significant changes
                    self._update_contrast_slider_range(ch_id)

            # Update memory usage (less frequently - only label updates, cheap)
            memory_stats = self.voxel_storage.get_memory_usage()
            self.memory_label.setText(f"Memory: {memory_stats['total_mb']:.1f} MB")
            self.voxel_count_label.setText(f"Voxels: {memory_stats['storage_voxels']:,}")
        finally:
            self.update_mutex.unlock()

        # Check memory limit
        if self.auto_clear_cb.isChecked():
            limit = self.memory_limit_spin.value()
            if memory_stats['total_mb'] > limit:
                logger.warning(f"Memory limit exceeded ({memory_stats['total_mb']:.1f} > {limit} MB)")
                # Could implement auto-clearing of old data here

    def _handle_stage_update_threadsafe(self, position):
        """
        Handle stage update on main Qt thread (thread-safe).

        Args:
            position: Stage position object or dict with x, y, z, r
        """
        # Store pending update
        self.pending_stage_update = position

        # Start throttle timer if not running
        if not self.update_throttle_timer.isActive():
            self.update_throttle_timer.start()

    def _process_pending_stage_update(self):
        """Process pending stage update with mutex protection."""
        if not self.pending_stage_update:
            self.update_throttle_timer.stop()
            return

        if not self.update_mutex.tryLock():
            return  # Skip if already updating

        try:
            position = self.pending_stage_update
            self.pending_stage_update = None

            # Convert position to dict if needed
            if hasattr(position, 'x'):
                # Position object
                stage_pos = {
                    'x': position.x if hasattr(position, 'x') else 0,
                    'y': position.y if hasattr(position, 'y') else 0,
                    'z': position.z if hasattr(position, 'z') else 0,
                    'r': position.r if hasattr(position, 'r') else 0
                }
            elif isinstance(position, dict):
                # Already a dict
                stage_pos = position
            else:
                # Unknown type - log error and use last known position
                logger.error(f"Unexpected position type: {type(position)} with value: {position}")
                stage_pos = self.last_stage_position

            # Store last stage position
            self.last_stage_position = stage_pos

            # Log at DEBUG level to reduce spam (position updates are frequent)
            logger.debug(f"Processing stage update: X={stage_pos['x']:.3f}, Y={stage_pos['y']:.3f}, "
                       f"Z={stage_pos['z']:.3f}, R={stage_pos.get('r', 0):.1f}°")

            # Update each channel with transformed data
            # Data is stored in SAMPLE coordinates, transform to CHAMBER coordinates for display

            # Get holder position for rotation center (rotation axis is the sample holder)
            holder_pos_voxels = None
            if hasattr(self, 'holder_position') and self.holder_position:
                holder_pos_voxels = np.array([
                    self.holder_position['x'],
                    self.holder_position['y'],
                    self.holder_position['z']
                ])

            for ch_id in range(self.voxel_storage.num_channels):
                if not self.voxel_storage.has_data(ch_id):
                    continue

                # Get transformed volume (sample coords -> chamber coords, rotation around holder)
                volume = self.voxel_storage.get_display_volume_transformed(
                    ch_id, stage_pos, holder_pos_voxels
                )

                # Update napari layer
                if ch_id in self.channel_layers:
                    non_zero_before = np.count_nonzero(self.channel_layers[ch_id].data)
                    self.channel_layers[ch_id].data = volume
                    non_zero_after = np.count_nonzero(volume)

                    logger.debug(f"Stage update: Channel {ch_id} - "
                               f"voxels before: {non_zero_before}, after: {non_zero_after}")

                    # Update contrast slider range based on actual display values
                    self._update_contrast_slider_range(ch_id)

        finally:
            self.update_mutex.unlock()

    def _on_position_changed_for_transform(self, x: float, y: float, z: float, r: float):
        """
        Wrapper to convert movement controller's 4-float signal to dict for transformation.

        Args:
            x, y, z, r: Individual position components from movement controller
        """
        # Convert to dict and call the thread-safe handler
        position_dict = {'x': x, 'y': y, 'z': z, 'r': r}
        self.on_stage_position_changed(position_dict)

    def on_stage_position_changed(self, position):
        """
        Called when stage position changes (from any thread).
        Uses signal to ensure thread safety.

        Args:
            position: New stage position (dict or Position object)
        """
        # Don't process directly - emit signal for thread-safe handling
        self.stage_position_update_signal.emit(position)

    def process_frame(self, frame_data: np.ndarray, metadata: dict):
        """
        Process incoming frame data with rotation transformation.

        Args:
            frame_data: Multi-channel image data (H, W, C)
            metadata: Dictionary with z_position, rotation, timestamp, etc.
        """
        if not self.is_populating:
            return

        # Update current state from metadata (positions in µm from metadata)
        if 'x_position' in metadata:
            self.position_sliders['x_slider'].setValue(int(metadata['x_position']))
            self.position_sliders['x_spinbox'].setValue(metadata['x_position'] / 1000.0)
        if 'y_position' in metadata:
            self.position_sliders['y_slider'].setValue(int(metadata['y_position']))
            self.position_sliders['y_spinbox'].setValue(metadata['y_position'] / 1000.0)
        if 'z_position' in metadata:
            self.current_z = metadata['z_position']
            self.position_sliders['z_slider'].setValue(int(self.current_z))
            self.position_sliders['z_spinbox'].setValue(self.current_z / 1000.0)

        if 'rotation' in metadata:
            self.current_rotation = metadata['rotation']
            # Update Y rotation slider
            if 'ry' in self.current_rotation:
                self.rotation_slider.setValue(int(self.current_rotation['ry']))

        # DO NOT set rotation for data placement - same fix as in _process_camera_frame_to_3d
        # The objective/camera are fixed - only the sample holder rotates
        # Transform coordinates WITHOUT rotation
        self.transformer.set_rotation(rx=0, ry=0, rz=0)  # Reset to no rotation

        # Generate pixel coordinates
        h, w = frame_data.shape[:2]
        y_coords, x_coords = np.meshgrid(np.arange(h), np.arange(w), indexing='ij')
        pixel_coords = np.column_stack([x_coords.ravel(), y_coords.ravel()])

        # Scale to world coordinates (assuming some pixel-to-micron calibration)
        pixel_to_micron = metadata.get('pixel_to_micron', 0.65)  # Example: 0.65 µm/pixel
        world_coords_2d = pixel_coords * pixel_to_micron

        # Transform to 3D world coordinates
        world_coords = self.transformer.camera_to_world(world_coords_2d, self.current_z)

        # Update each channel
        for ch_id in range(min(frame_data.shape[2], self.voxel_storage.num_channels)):
            # Get update strategy from UI
            strategy = 'latest'
            if ch_id in self.channel_controls:
                strategy = self.channel_controls[ch_id]['strategy'].currentText()

            # Extract channel data
            channel_data = frame_data[:, :, ch_id].ravel()

            # Update storage
            self.voxel_storage.update_storage(
                ch_id,
                world_coords,
                channel_data,
                metadata.get('timestamp', 0),
                update_mode=strategy
            )

        # Update status
        self.status_label.setText(f"Status: Processing frame at Z={self.current_z:.1f} µm")

    def _on_x_slider_released(self):
        """Handle X slider release - send move command to stage."""
        if not self.movement_controller or not self._controls_enabled:
            return

        x_mm = self.position_sliders['x_slider'].value() / 1000.0
        try:
            self.movement_controller.move_absolute('x', x_mm, verify=False)
            logger.debug(f"X slider released - moving stage to {x_mm:.3f} mm")
        except Exception as e:
            logger.error(f"Error moving X axis: {e}")

    def _on_y_slider_released(self):
        """Handle Y slider release - send move command to stage."""
        if not self.movement_controller or not self._controls_enabled:
            return

        y_mm = self.position_sliders['y_slider'].value() / 1000.0
        try:
            self.movement_controller.move_absolute('y', y_mm, verify=False)
            logger.debug(f"Y slider released - moving stage to {y_mm:.3f} mm")
        except Exception as e:
            logger.error(f"Error moving Y axis: {e}")

    def _on_z_slider_released(self):
        """Handle Z slider release - send move command to stage."""
        if not self.movement_controller or not self._controls_enabled:
            return

        z_mm = self.position_sliders['z_slider'].value() / 1000.0
        try:
            self.movement_controller.move_absolute('z', z_mm, verify=False)
            logger.debug(f"Z slider released - moving stage to {z_mm:.3f} mm")
        except Exception as e:
            logger.error(f"Error moving Z axis: {e}")

    def _on_rotation_slider_released(self):
        """Handle rotation slider/spinbox release - send rotation command to stage."""
        if not self.movement_controller or not self._controls_enabled:
            return

        rotation_deg = self.rotation_slider.value()
        try:
            self.movement_controller.move_absolute('r', rotation_deg, verify=False)
            logger.debug(f"Rotation released - moving stage to {rotation_deg:.2f}°")
        except Exception as e:
            logger.error(f"Error moving rotation: {e}")

    def _on_position_changed_from_controller(self, x: float, y: float, z: float, r: float):
        """
        Handle position change from movement controller (from ANY window).
        Updates visualization WITHOUT triggering commands (prevents loops).
        """
        # CRITICAL: Validate position is within hardware limits BEFORE updating UI
        # Garbage values during movement (e.g., 7.969mm for Z) could corrupt spinbox
        # and cause subsequent user interactions to send movement to wrong position
        stage_config = self.config['stage_control']
        x_min, x_max = stage_config['x_range_mm']
        z_min, z_max = stage_config['z_range_mm']
        y_min = stage_config.get('y_stage_min_mm', 5.0)
        y_max = stage_config.get('y_stage_max_mm', 25.0)

        # Check if position is valid (with small tolerance for rounding)
        tolerance = 0.01  # 10 µm tolerance
        if not (x_min - tolerance <= x <= x_max + tolerance):
            logger.warning(f"Ignoring invalid X position from controller: {x:.3f}mm (range: {x_min}-{x_max})")
            return
        if not (y_min - tolerance <= y <= y_max + tolerance):
            logger.warning(f"Ignoring invalid Y position from controller: {y:.3f}mm (range: {y_min}-{y_max})")
            return
        if not (z_min - tolerance <= z <= z_max + tolerance):
            logger.warning(f"Ignoring invalid Z position from controller: {z:.3f}mm (range: {z_min}-{z_max})")
            return

        # Block signals to prevent infinite loops
        self.position_sliders['x_slider'].blockSignals(True)
        self.position_sliders['y_slider'].blockSignals(True)
        self.position_sliders['z_slider'].blockSignals(True)
        self.position_sliders['x_spinbox'].blockSignals(True)
        self.position_sliders['y_spinbox'].blockSignals(True)
        self.position_sliders['z_spinbox'].blockSignals(True)
        self.rotation_slider.blockSignals(True)
        self.rotation_spinbox.blockSignals(True)

        try:
            # Update position sliders (convert mm to µm)
            self.position_sliders['x_slider'].setValue(int(x * 1000))
            self.position_sliders['y_slider'].setValue(int(y * 1000))
            self.position_sliders['z_slider'].setValue(int(z * 1000))

            # Update position spinboxes
            self.position_sliders['x_spinbox'].setValue(x)
            self.position_sliders['y_spinbox'].setValue(y)
            self.position_sliders['z_spinbox'].setValue(z)

            # Update rotation controls
            self.rotation_slider.setValue(int(r))
            self.rotation_spinbox.setValue(r)

            # Update current rotation state
            self.current_rotation['ry'] = r

            # Update napari visualization (holder/indicator only, data is in voxel storage)
            self._update_sample_holder_position(x, y, z)
            self._update_rotation_indicator()

            logger.debug(f"Position synced from controller: X={x:.2f}, Y={y:.2f}, Z={z:.2f}, R={r:.2f}°")

        finally:
            # Always restore signals
            self.position_sliders['x_slider'].blockSignals(False)
            self.position_sliders['y_slider'].blockSignals(False)
            self.position_sliders['z_slider'].blockSignals(False)
            self.position_sliders['x_spinbox'].blockSignals(False)
            self.position_sliders['y_spinbox'].blockSignals(False)
            self.position_sliders['z_spinbox'].blockSignals(False)
            self.rotation_slider.blockSignals(False)
            self.rotation_spinbox.blockSignals(False)

    def _on_motion_started(self, axis_name: str):
        """
        Handle motion started signal - start frame buffering.

        This is called by the movement controller when motion begins.
        We record the current position and start buffering all incoming frames.
        Position queries during motion return stale values, so we interpolate
        after motion completes.
        """
        self._set_controls_enabled(False)
        self.status_label.setText(f"Status: Moving {axis_name}...")

        # Get current position BEFORE motion starts
        # This is the last reliable position reading
        start_pos = None
        if self.movement_controller:
            position = self.movement_controller.get_position()
            if position:
                start_pos = (position.x, position.y, position.z, position.r)

        # Start buffering frames
        mt = self._motion_tracking
        mt['in_progress'] = True
        mt['start_position'] = start_pos
        mt['start_time'] = time.time()
        mt['end_position'] = None
        mt['end_time'] = None
        mt['frame_buffer'] = []  # Clear any stale frames
        mt['moving_axis'] = axis_name

        # Speed up frame capture during motion (50 Hz instead of 10 Hz)
        # This ensures we capture enough frames to fill voxels during fast movements
        if self.populate_timer.isActive():
            self.populate_timer.setInterval(20)  # 20ms = 50 Hz during motion
            logger.info(f"Increased populate rate to 50 Hz for motion capture")

        # CRITICAL: Pause visualization updates during motion to free up CPU for frame capture
        # The visualization can catch up after motion completes
        if hasattr(self, 'update_timer') and self.update_timer.isActive():
            self.update_timer.stop()
            logger.info(f"Paused visualization updates during motion for better frame capture")

        logger.info(f"Motion started on {axis_name} - buffering frames. "
                   f"Start position: X={start_pos[0]:.3f}, Y={start_pos[1]:.3f}, "
                   f"Z={start_pos[2]:.3f}, R={start_pos[3]:.1f}°" if start_pos else
                   f"Motion started on {axis_name} - buffering frames (no start position)")

    def _on_motion_stopped(self, axis_name: str):
        """
        Handle motion stopped signal - process buffered frames with interpolation.

        This is called by the movement controller when motion completes.
        We record the final position and process all buffered frames with
        positions interpolated between start and end.
        """
        self._set_controls_enabled(True)
        self.status_label.setText("Status: Ready")

        mt = self._motion_tracking

        # Only process if we were actually tracking motion
        if not mt['in_progress']:
            logger.debug(f"Motion stopped on {axis_name} but no motion was being tracked")
            return

        # Get final position AFTER motion completes
        # This is the new reliable position reading
        end_pos = None
        if self.movement_controller:
            position = self.movement_controller.get_position()
            if position:
                end_pos = (position.x, position.y, position.z, position.r)

        mt['end_position'] = end_pos
        mt['end_time'] = time.time()

        # Log motion summary
        if mt['start_position'] and end_pos:
            delta_z = end_pos[2] - mt['start_position'][2]
            duration = mt['end_time'] - mt['start_time']
            logger.info(f"Motion stopped on {axis_name}. "
                       f"Moved Z from {mt['start_position'][2]:.3f} to {end_pos[2]:.3f}mm "
                       f"(delta={delta_z:.3f}mm) in {duration:.2f}s. "
                       f"Buffered {len(mt['frame_buffer'])} frames for interpolation.")
        else:
            logger.info(f"Motion stopped on {axis_name}. "
                       f"Buffered {len(mt['frame_buffer'])} frames.")

        # Restore normal capture rate (10 Hz)
        if self.populate_timer.isActive():
            self.populate_timer.setInterval(100)  # 100ms = 10 Hz normal rate
            logger.info(f"Restored populate rate to 10 Hz")

        # Resume visualization updates (paused during motion for better frame capture)
        if hasattr(self, 'update_timer') and not self.update_timer.isActive():
            self.update_timer.start()
            logger.info(f"Resumed visualization updates after motion")

        # Process all buffered frames with interpolated positions
        if mt['frame_buffer']:
            self._process_motion_buffer()

        # Reset motion tracking state
        mt['in_progress'] = False
        mt['start_position'] = None
        mt['start_time'] = None
        mt['end_position'] = None
        mt['end_time'] = None
        mt['frame_buffer'] = []
        mt['moving_axis'] = None

    def _set_controls_enabled(self, enabled: bool):
        """Enable or disable movement controls during motion."""
        self._controls_enabled = enabled

        # Enable/disable sliders
        self.position_sliders['x_slider'].setEnabled(enabled)
        self.position_sliders['y_slider'].setEnabled(enabled)
        self.position_sliders['z_slider'].setEnabled(enabled)
        self.rotation_slider.setEnabled(enabled)

        # Enable/disable spinboxes
        self.position_sliders['x_spinbox'].setEnabled(enabled)
        self.position_sliders['y_spinbox'].setEnabled(enabled)
        self.position_sliders['z_spinbox'].setEnabled(enabled)
        self.rotation_spinbox.setEnabled(enabled)

    def _process_motion_buffer(self):
        """
        Process buffered frames after motion completes.

        Selects frames based on voxel size to avoid redundant processing:
        - Calculate how many unique voxel positions need to be filled
        - If more frames than voxels: subsample to pick one frame per voxel
        - If fewer frames than voxels: use all frames (some voxels will be empty)

        Frame 0 (first captured) -> closest to start position
        Frame N (last captured) -> closest to end position
        """
        mt = self._motion_tracking
        buffer = mt['frame_buffer']

        if not buffer:
            logger.debug("Motion buffer empty - nothing to process")
            return

        start = mt['start_position']
        end = mt['end_position']

        if not start or not end:
            logger.warning("Motion buffer has frames but missing start/end position - skipping")
            mt['frame_buffer'] = []
            return

        n_frames = len(buffer)

        # Calculate motion distance along each axis (in mm)
        dx = abs(end[0] - start[0])
        dy = abs(end[1] - start[1])
        dz = abs(end[2] - start[2])

        # Get storage voxel size (in µm) - this determines how many unique positions we need
        storage_voxel_um = self.voxel_storage.config.storage_voxel_size[0]  # Assuming isotropic

        # Calculate how many voxels we need to fill along the primary motion axis
        # Use the largest motion distance
        max_distance_mm = max(dx, dy, dz)
        max_distance_um = max_distance_mm * 1000

        # Number of unique voxel positions needed
        voxels_needed = max(1, int(max_distance_um / storage_voxel_um))

        logger.info(f"Motion buffer: {n_frames} frames, {max_distance_mm:.2f}mm motion, "
                   f"{voxels_needed} voxels needed (at {storage_voxel_um}µm resolution)")

        # Determine which frames to use
        if n_frames <= voxels_needed:
            # Use all frames - we don't have enough to fill every voxel
            frames_to_use = list(range(n_frames))
            logger.info(f"Using all {n_frames} frames (undersampled)")
        else:
            # Subsample: pick frames evenly spaced to fill voxels without redundancy
            frames_to_use = [int(i * (n_frames - 1) / (voxels_needed - 1))
                            for i in range(voxels_needed)] if voxels_needed > 1 else [n_frames - 1]
            logger.info(f"Subsampling: using {len(frames_to_use)} of {n_frames} frames")

        # Create a simple Position-like object
        class DistributedPosition:
            def __init__(self, x, y, z, r):
                self.x = x
                self.y = y
                self.z = z
                self.r = r

        # Process selected frames at their distributed positions
        n_selected = len(frames_to_use)
        for out_idx, frame_idx in enumerate(frames_to_use):
            frame_data = buffer[frame_idx]

            # Calculate position ratio: 0.0 = start, 1.0 = end
            if n_selected > 1:
                ratio = out_idx / (n_selected - 1)
            else:
                ratio = 1.0  # Single frame goes at end position

            # Linear distribution between start and end
            x = start[0] + ratio * (end[0] - start[0])
            y = start[1] + ratio * (end[1] - start[1])
            z = start[2] + ratio * (end[2] - start[2])
            r = start[3] + ratio * (end[3] - start[3])

            position = DistributedPosition(x, y, z, r)

            logger.debug(f"Processing frame {frame_idx} -> voxel {out_idx+1}/{n_selected} at "
                        f"Z={position.z:.3f}mm")

            self._process_camera_frame_to_3d(
                frame_data['frame'],
                frame_data['header'],
                frame_data['channel_id'],
                position
            )

        # Clear buffer after processing
        mt['frame_buffer'] = []
        logger.info(f"Motion buffer processed: {n_selected} frames placed into {voxels_needed} voxel positions")

    def _on_populate_tick(self):
        """
        Capture current frame from Live View and add to 3D volume.

        Motion-aware buffering:
        - When motion_started signal received: buffer all frames
        - When motion_stopped signal received: distribute buffered frames
          evenly across the motion range (start to end position)
        - When stationary: process frames immediately at current position
        """
        if not self.is_populating or not self.camera_controller:
            return

        try:
            # Track tick count for periodic diagnostics
            if not hasattr(self, '_populate_tick_count'):
                self._populate_tick_count = 0
            self._populate_tick_count += 1

            # Check if Live View is actually running
            if not self.camera_controller.is_live_view_active():
                if self._populate_tick_count % 10 == 0:
                    logger.debug(f"Tick {self._populate_tick_count}: Live View not active")
                return

            # Get current stage position
            if not self.movement_controller:
                logger.warning("No movement controller - cannot get position")
                return

            position = self.movement_controller.get_position()
            if position is None:
                logger.warning(f"Tick {self._populate_tick_count}: Position unavailable")
                return

            current_pos = (position.x, position.y, position.z, position.r)
            capture_time = time.time()

            # Get latest camera frame
            frame_data = self.camera_controller.get_latest_frame()
            if frame_data is None:
                if self._populate_tick_count % 10 == 0:
                    logger.warning(f"Tick {self._populate_tick_count}: No frame available")
                return

            # Unpack 3-tuple: (image, header, local_frame_number)
            # local_frame_number is controller's counter (hardware frame_number may be stuck at 0)
            image, header, local_frame_num = frame_data

            # Skip duplicate frames (same frame processed multiple times)
            # Use local_frame_num instead of header.frame_number (hardware may send 0 for all frames)
            if local_frame_num == self._last_processed_frame_number:
                # Log duplicate skips periodically to diagnose stuck frame buffer
                if self._populate_tick_count % 10 == 0:
                    logger.warning(f"Tick {self._populate_tick_count}: Still on frame {local_frame_num} (duplicate)")
                return
            self._last_processed_frame_number = local_frame_num

            logger.debug(f"Populate tick {self._populate_tick_count}: Frame {local_frame_num}")

            # Determine which channel this frame belongs to
            logger.debug("Populate tick: Detecting active channel")
            channel_id = self._detect_active_channel()

            # Skip if LED/brightfield (not appropriate for 3D fluorescence volume)
            if channel_id is None:
                logger.debug("LED/brightfield active - skipping 3D accumulation")
                return

            # Signal-based motion tracking
            # motion_started signal sets in_progress=True, motion_stopped sets it False
            # During motion, buffer frames. After motion stops, frames are interpolated.
            mt = self._motion_tracking

            if mt['in_progress']:
                # Stage is moving (motion_started signal received)
                # Buffer this frame for later processing with interpolated position
                mt['frame_buffer'].append({
                    'frame': image.copy(),  # Copy to avoid reference issues
                    'header': header,
                    'channel_id': channel_id,
                    'capture_time': capture_time
                })
                logger.debug(f"Buffered frame {local_frame_num} during {mt['moving_axis']} motion "
                           f"(buffer size: {len(mt['frame_buffer'])})")
            else:
                # Stage is stationary - process frame immediately at current position
                # Log every 10th frame at INFO level to reduce spam but show progress
                if local_frame_num % 10 == 0:
                    logger.info(f"Processing frame {local_frame_num} (stationary) at "
                               f"X={position.x:.2f}, Y={position.y:.2f}, Z={position.z:.2f}")
                else:
                    logger.debug(f"Populate tick: Processing frame {local_frame_num} for channel {channel_id}")
                self._process_camera_frame_to_3d(image, header, channel_id, position)
                logger.debug(f"Populate tick: Frame {local_frame_num} processed at "
                           f"X={position.x:.2f}, Y={position.y:.2f}, Z={position.z:.2f}, R={position.r:.1f}°")

        except Exception as e:
            logger.error(f"Error capturing frame: {e}", exc_info=True)

    def _process_camera_frame_to_3d(self, image: np.ndarray, header, channel_id: int, position):
        """
        Transform camera frame to 3D world coordinates and accumulate.

        Args:
            image: Camera image (e.g., 2048x2048, uint16)
            header: Image header with metadata
            channel_id: Which channel (0-3)
            position: Position object with x, y, z, r
        """
        try:
            logger.debug("Process 3D: Starting downsample")
            # Downsample camera image to storage resolution
            downsampled = self._downsample_for_storage(image)

            H, W = downsampled.shape
            logger.debug(f"Process 3D: Downsampled from {image.shape} to {downsampled.shape}")

            # Generate pixel coordinate grid
            logger.debug("Process 3D: Creating coordinate grid")
            y_indices, x_indices = np.meshgrid(np.arange(H), np.arange(W), indexing='ij')

            # Convert to camera space (micrometers), centered at (0, 0)
            logger.debug("Process 3D: Converting to camera space")

            # Calculate actual FOV from magnification
            # FOV was calculated as 0.5182mm for 25.69× magnification
            # This should ideally come from camera_controller or configuration
            FOV_mm = 0.5182  # mm (field of view width/height)
            FOV_um = FOV_mm * 1000  # Convert to micrometers

            # Scale camera pixels to physical coordinates based on FOV
            # The camera image spans the full FOV
            pixel_size_um = FOV_um / W  # Micrometers per camera pixel

            camera_x = (x_indices - W/2) * pixel_size_um
            camera_y = (y_indices - H/2) * pixel_size_um

            logger.debug(f"FOV: {FOV_mm:.3f}mm = {FOV_um:.1f}µm, Pixel size: {pixel_size_um:.3f}µm/pixel")

            # Stack into (N, 2) array for transformation
            logger.debug("Process 3D: Stacking coordinates")
            camera_coords_2d = np.column_stack([camera_x.ravel(), camera_y.ravel()])

            # DO NOT set rotation for data placement
            # The objective/camera are fixed - only the sample holder rotates
            # Setting rotation here was causing data to be placed at wrong Z coordinates
            logger.debug(f"Process 3D: Sample rotation (ry={position.r}°) - NOT applied to placement")
            # IMPORTANT: Reset transformer to ensure no rotation is applied to placement
            self.transformer.set_rotation(rx=0, ry=0, rz=0)  # Reset to no rotation

            # Transform 2D camera coords + stage position to 3D world coords
            # CRITICAL: Must convert stage Y to chamber Y using the calibration reference
            # Stage Y is a control parameter; chamber Y is the actual position in the visualization
            # The imaging data is attached to the sample (at extension tip), so it moves with the holder

            logger.debug("Process 3D: Converting stage coords to sample-relative coords")

            # Storage is in SAMPLE coordinates (fixed relative to sample holder)
            # The focal plane position in sample coords changes as stage moves
            #
            # Physical model:
            # - Focal plane is FIXED in chamber space (at objective)
            # - Sample moves with stage
            # - As stage Z increases (away from objective), we image DEEPER into sample
            #
            # Sample coordinate calculation:
            # - Use a reference point (center of storage) as base
            # - Offset by stage delta from reference position
            # - This ensures different stage positions map to different sample positions

            # Get reference position (set on first frame capture)
            if self.voxel_storage.reference_stage_position is None:
                # Will be set below, use current position as temporary reference
                ref_x, ref_y, ref_z = position.x, position.y, position.z
            else:
                ref_x = self.voxel_storage.reference_stage_position['x']
                ref_y = self.voxel_storage.reference_stage_position['y']
                ref_z = self.voxel_storage.reference_stage_position['z']

            # Calculate stage delta from reference
            delta_x = position.x - ref_x
            delta_y = position.y - ref_y
            delta_z = position.z - ref_z

            # Base storage position (center of sample region in storage coords)
            # Config has [X, Y, Z] order, but we need Z, Y, X for napari
            sample_center = self.config['sample_chamber']['sample_region_center_um']
            base_x_um = sample_center[0]  # X is index 0 in config
            base_y_um = sample_center[1]  # Y is index 1 in config
            base_z_um = sample_center[2]  # Z is index 2 in config

            # Storage position = base - delta (in sample coordinates)
            # The negative delta ensures that when the display transform adds +delta,
            # new data appears at the focal plane (base) while old data moves forward
            #
            # Example: reference at Z=21mm, current at Z=22mm (delta=+1mm)
            # - Frame from Z=21 stored at base-0=base, displayed at base+1mm (moved forward)
            # - Frame from Z=22 stored at base-1mm, displayed at base-1+1=base (focal plane)
            #
            # IMPORTANT: Storage expects (Z, Y, X) order for consistency with napari
            world_center_um = np.array([
                base_z_um - delta_z * 1000,  # Z in sample coords (subtract delta)
                base_y_um - delta_y * 1000,  # Y in sample coords (subtract delta)
                base_x_um - delta_x * 1000   # X in sample coords (subtract delta)
            ])

            logger.debug(f"Stage position (mm): X={position.x:.2f}, Y={position.y:.2f}, Z={position.z:.2f}, R={position.r:.1f}°")
            logger.debug(f"Stage delta from ref (mm): dX={delta_x:.2f}, dY={delta_y:.2f}, dZ={delta_z:.2f}")
            logger.debug(f"Sample storage position (µm): Z={world_center_um[0]:.1f}, Y={world_center_um[1]:.1f}, X={world_center_um[2]:.1f}")

            # For 3D visualization, we need to place the 2D camera image as a thin slice
            # The imaging plane has some depth (depth of field ~1.9mm in Z)
            # We'll represent this as a thin slab centered at the focal plane

            # Create 3D coords for camera offsets (relative to imaging plane center)
            logger.debug("Process 3D: Creating 3D coordinate array for camera offsets")

            # The camera captures a 2D image at the focal plane
            # We need to give it some thickness for visualization (e.g., 100µm)
            slice_thickness_um = 100  # Thickness of the imaged slice

            # Create a grid of Z values to give the slice some thickness
            num_pixels = len(camera_coords_2d)
            z_offsets = np.linspace(-slice_thickness_um/2, slice_thickness_um/2, num_pixels)

            # IMPORTANT: Must use (Z, Y, X) order to match storage/napari convention
            camera_offsets_3d = np.column_stack([
                z_offsets,                # Z variation for slice thickness (first)
                camera_coords_2d[:, 1],   # Camera Y offset (second)
                camera_coords_2d[:, 0]    # Camera X offset (third)
            ])

            # For R-axis rotation: The sample holder rotates, but the imaging plane stays fixed
            # The rotation affects how the sample appears in the image, not where the image is placed
            # Therefore, we should NOT rotate the placement coordinates

            logger.debug(f"Process 3D: R-axis rotation = {position.r}° (sample holder orientation)")
            logger.debug(f"Note: R-axis rotation affects sample appearance, not image placement in 3D space")

            # The world coordinates are simply the camera offsets plus the stage position
            # No rotation is applied to the placement (the objective/camera don't rotate)
            world_coords_3d = camera_offsets_3d + world_center_um

            # Debug logging to trace coordinate transformation
            logger.debug(f"World center (sample coords) µm (ZYX order): {world_center_um}")
            logger.debug(f"Camera offset range Z: [{camera_offsets_3d[:, 0].min():.1f}, {camera_offsets_3d[:, 0].max():.1f}] µm")
            logger.debug(f"Camera offset range Y: [{camera_offsets_3d[:, 1].min():.1f}, {camera_offsets_3d[:, 1].max():.1f}] µm")
            logger.debug(f"Camera offset range X: [{camera_offsets_3d[:, 2].min():.1f}, {camera_offsets_3d[:, 2].max():.1f}] µm")
            logger.debug(f"World coord range Z: [{world_coords_3d[:, 0].min():.1f}, {world_coords_3d[:, 0].max():.1f}] µm")
            logger.debug(f"World coord range Y: [{world_coords_3d[:, 1].min():.1f}, {world_coords_3d[:, 1].max():.1f}] µm")
            logger.debug(f"World coord range X: [{world_coords_3d[:, 2].min():.1f}, {world_coords_3d[:, 2].max():.1f}] µm")

            # Extract intensity values
            logger.debug("Process 3D: Extracting intensity values")
            intensity_values = downsampled.ravel()

            # Set reference position for transform calculations if not already set
            # This happens on the FIRST frame captured, establishing the "zero point"
            # for all subsequent stage movements
            if self.voxel_storage.reference_stage_position is None:
                stage_pos_dict = {
                    'x': position.x,
                    'y': position.y,
                    'z': position.z,
                    'r': position.r
                }
                self.voxel_storage.set_reference_position(stage_pos_dict)
                logger.debug(f"First frame captured - reference position set to stage position")

            # Update voxel storage with transformed coordinates
            logger.debug(f"Process 3D: Updating voxel storage for channel {channel_id}")
            self.voxel_storage.update_storage(
                channel_id=channel_id,
                world_coords=world_coords_3d,
                pixel_values=intensity_values,  # Correct parameter name
                timestamp=header.timestamp_ms if hasattr(header, 'timestamp_ms') else 0,
                update_mode='maximum'  # Maximum intensity projection for fluorescence
            )

            logger.debug(f"Added frame to channel {channel_id}: {np.count_nonzero(intensity_values)} non-zero pixels")

            # Update contrast slider range based on actual data values
            self._update_contrast_slider_range(channel_id)

            # Diagnostic: Log world coordinate ranges and rotation for debugging
            logger.debug(f"Stage position: X={position.x:.2f}mm, Y={position.y:.2f}mm, Z={position.z:.2f}mm, R={position.r:.1f}°")
            logger.debug(f"World coordinate ranges (ZYX order, no rotation applied):")
            logger.debug(f"  Z: [{world_coords_3d[:, 0].min():.1f}, {world_coords_3d[:, 0].max():.1f}] µm")
            logger.debug(f"  Y: [{world_coords_3d[:, 1].min():.1f}, {world_coords_3d[:, 1].max():.1f}] µm")
            logger.debug(f"  X: [{world_coords_3d[:, 2].min():.1f}, {world_coords_3d[:, 2].max():.1f}] µm")

        except Exception as e:
            logger.error(f"Error in _process_camera_frame_to_3d: {e}", exc_info=True)
            raise
        logger.debug(f"Sample region center: {self.transformer.sample_center} µm")

    def _detect_active_channel(self) -> Optional[int]:
        """
        Detect which light source channel is currently active.

        Returns:
            Channel ID (0-3) for lasers or LED, None if no light source active.
            LED maps to channel 0 (same as 405nm) to allow brightfield testing.
        """
        # Check if laser/LED controller is available
        if not hasattr(self, 'laser_led_controller') or self.laser_led_controller is None:
            logger.warning("No laser/LED controller - defaulting to channel 0")
            return 0

        try:
            active_source = self.laser_led_controller.get_active_source()

            if active_source is None:
                logger.debug("No light source active")
                return None

            # Map light source to channel
            if active_source == "laser_1":
                return 0  # 405nm (DAPI)
            elif active_source == "laser_2":
                return 1  # 488nm (GFP)
            elif active_source == "laser_3":
                return 2  # 561nm (RFP)
            elif active_source == "laser_4":
                return 3  # 640nm (Far-Red)
            elif active_source and active_source.startswith("led_"):
                # LED/brightfield maps to channel 0 (shared with 405nm)
                # LED active_source is "led_R", "led_G", "led_B", or "led_W"
                # This allows testing with brightfield when no fluorescent sample available
                return 0
            else:
                logger.warning(f"Unknown light source: {active_source}, defaulting to channel 0")
                return 0

        except Exception as e:
            logger.error(f"Error detecting active channel: {e}")
            return 0  # Fallback

    def _downsample_for_storage(self, image: np.ndarray) -> np.ndarray:
        """
        Downsample camera image to storage resolution.

        Args:
            image: Full resolution camera image

        Returns:
            Downsampled image at storage voxel size
        """
        from scipy.ndimage import zoom

        # Camera pixel size (from calibration - TODO: get from config)
        pixel_size_um = 0.65  # micrometers per pixel

        # Target voxel size
        target_voxel_size_um = self.config['storage']['voxel_size_um'][0]

        # Calculate downsample factor
        downsample_factor = target_voxel_size_um / pixel_size_um

        # Downsample with bilinear interpolation
        if downsample_factor > 1.0:
            downsampled = zoom(image, 1.0 / downsample_factor, order=1)
            return downsampled.astype(np.uint16)
        else:
            return image

    def closeEvent(self, event):
        """Handle window close event."""
        # Stop populating
        if self.is_populating:
            self.populate_button.setChecked(False)

        # Stop all timers
        if hasattr(self, 'update_timer') and self.update_timer.isActive():
            self.update_timer.stop()
        if hasattr(self, 'populate_timer') and self.populate_timer.isActive():
            self.populate_timer.stop()
        if hasattr(self, 'rotation_debounce_timer') and self.rotation_debounce_timer.isActive():
            self.rotation_debounce_timer.stop()

        # Close napari viewer
        if self.viewer:
            self.viewer.close()

        event.accept()