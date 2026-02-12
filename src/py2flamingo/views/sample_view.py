"""
Sample View - Integrated Sample Interaction Window.

Combines all elements needed for sample viewing and interaction:
- Live camera feed with embedded display controls
- 3D volume visualization (napari)
- Position sliders for stage control
- Illumination controls (always visible)
- MIP plane views with click-to-move
- Workflow progress placeholder
- Dialog launcher buttons
"""

import logging
import time
import numpy as np
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QSlider, QComboBox, QCheckBox, QProgressBar,
    QSplitter, QSizePolicy, QFrame, QSpinBox,
    QGridLayout, QLineEdit, QTabWidget
)
from PyQt5.QtCore import Qt, pyqtSlot, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage, QFont, QDoubleValidator, QShowEvent, QCloseEvent, QHideEvent, QIcon

from py2flamingo.services.window_geometry_manager import PersistentDialog
from py2flamingo.resources import get_app_icon

if TYPE_CHECKING:
    from py2flamingo.services.window_geometry_manager import WindowGeometryManager
from superqt import QRangeSlider

from py2flamingo.views.laser_led_control_panel import LaserLEDControlPanel
from py2flamingo.views.colors import SUCCESS_COLOR, ERROR_COLOR, WARNING_BG
from py2flamingo.services.position_preset_service import PositionPresetService
from py2flamingo.views.widgets.slice_plane_viewer import SlicePlaneViewer, AXIS_COLORS
from py2flamingo.views.dialogs.viewer_controls_dialog import ViewerControlsDialog
from py2flamingo.views.chamber_visualization_manager import ChamberVisualizationManager

# Import camera state for live view control
from py2flamingo.controllers.camera_controller import CameraState



class SampleView(QWidget):
    """
    Integrated sample viewing and interaction window.

    Combines live camera, 3D visualization, MIP plane views,
    position sliders, and illumination controls in a single interface.
    """

    def __init__(
        self,
        camera_controller,
        movement_controller,
        laser_led_controller,
        voxel_storage=None,
        image_controls_window=None,
        geometry_manager: 'WindowGeometryManager' = None,
        configuration_service=None,
        parent=None
    ):
        """
        Initialize Sample View.

        Args:
            camera_controller: CameraController instance
            movement_controller: MovementController instance
            laser_led_controller: LaserLEDController instance
            voxel_storage: Optional DualResolutionVoxelStorage instance
            image_controls_window: Optional ImageControlsWindow for advanced settings
            geometry_manager: Optional WindowGeometryManager for saving/restoring geometry
            configuration_service: Optional ConfigurationService for path persistence
            parent: Parent widget
        """
        super().__init__(parent)

        self.camera_controller = camera_controller
        self.movement_controller = movement_controller
        self.laser_led_controller = laser_led_controller
        self.voxel_storage = voxel_storage
        self.image_controls_window = image_controls_window
        self._geometry_manager = geometry_manager
        self._configuration_service = configuration_service
        self._geometry_restored = False
        self._dialog_state_restored = False
        self.logger = logging.getLogger(__name__)

        # napari viewer and channel layers (owned by Sample View)
        self.viewer = None
        self.channel_layers = {}

        # Display state
        self._current_image: Optional[np.ndarray] = None
        self._colormap = "Grayscale"
        self._auto_scale = True
        self._intensity_min = 0
        self._intensity_max = 65535

        # Auto-contrast algorithm parameters
        self._auto_contrast_interval = 1.0  # seconds between adjustments
        self._saturation_threshold = 0.20  # 20% of pixels saturated triggers raise
        self._low_brightness_threshold = 0.05  # <5% bright pixels triggers lower
        self._brightness_reference = 0.70  # 70% of max is "bright"
        self._saturation_percentile = 0.95  # pixels >= 95% of max are "saturated"
        self._last_contrast_adjustment = 0.0  # timestamp of last adjustment
        self._auto_contrast_max = 65535  # current auto-determined max (min stays 0)

        # Stage limits (will be populated from movement controller)
        self._stage_limits = None

        # Position slider scale factors (for int conversion)
        self._slider_scale = 1000  # 3 decimal places

        # Load visualization config for axis inversion settings
        self._config = self._load_visualization_config()
        self._invert_x = self._config.get('stage_control', {}).get('invert_x_default', False)

        # Channel visibility/contrast state for 4 viewers - load from config
        self._channel_states = self._load_channel_settings_from_config()

        # Live view state
        self._live_view_active = False

        # Tile workflow integration state
        self._tile_workflow_active = False
        self._expected_tiles = []  # List of tile positions
        self._accumulated_zstacks = {}  # (x,y) -> frame count
        self._current_channel = 0  # Default to channel 0 (405nm)

        # Chamber visualization manager (3D viewer, chamber geometry, data layers)
        self._chamber_viz = ChamberVisualizationManager(
            voxel_storage=voxel_storage,
            config=self._config,
            invert_x=self._invert_x,
            slider_scale=self._slider_scale,
        )
        # Backward-compat references (set properly after embed_viewer)
        self.viewer = None
        self.channel_layers = {}

        # Expose manager state that other methods still read directly
        self.coord_mapper = None  # Will be set from voxel_storage

        # Stage position tracking for dynamic 3D updates
        self.last_stage_position = {'x': 0, 'y': 0, 'z': 0, 'r': 0}
        self._pending_stage_update = None

        # Load objective calibration from presets
        self._chamber_viz.load_objective_calibration()

        # Setup window - sized for 3-column layout
        self.setWindowTitle("Sample View")
        self.setWindowIcon(get_app_icon())  # Use flamingo icon
        self.setMinimumSize(1000, 800)
        self.resize(1200, 900)

        # Setup UI
        self._setup_ui()

        # Connect signals
        self._connect_signals()

        # Initialize stage limits
        self._init_stage_limits()

        # Embed 3D viewer from existing window (if available)
        self._embed_3d_viewer()

        # Initialize 2D plane overlays now that self.viewer is set
        # (position_changed signal won't fire until stage actually moves)
        self._update_plane_overlays()

        # Update live view button state
        self._update_live_view_state()

        # Timer to update zoom display and other info
        self._info_timer = QTimer(self)
        self._info_timer.timeout.connect(self._update_info_displays)
        self._info_timer.start(500)  # Update every 500ms

        # Debounced timer for channel availability checks
        self._channel_availability_timer = QTimer(self)
        self._channel_availability_timer.setSingleShot(True)
        self._channel_availability_timer.setInterval(500)
        self._channel_availability_timer.timeout.connect(self._update_channel_availability)

        # Debounced timer for visualization updates during acquisition
        self._visualization_update_timer = QTimer(self)
        self._visualization_update_timer.setSingleShot(True)
        self._visualization_update_timer.setInterval(500)  # Update 500ms after last frame
        self._visualization_update_timer.timeout.connect(self._update_visualization)

        # Throttled timer for stage position → 3D visualization updates (20 FPS max)
        self._stage_update_timer = QTimer(self)
        self._stage_update_timer.setInterval(50)
        self._stage_update_timer.timeout.connect(self._process_pending_stage_update)

        self.logger.info("SampleView initialized")

    def _load_visualization_config(self) -> Dict[str, Any]:
        """Load visualization config from YAML file."""
        config_path = Path(__file__).parent.parent / "configs" / "visualization_3d_config.yaml"
        try:
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config = yaml.safe_load(f)
                self.logger.info(f"Loaded visualization config from {config_path}")
                return config
        except Exception as e:
            self.logger.warning(f"Could not load visualization config: {e}")

        # Return default config if file not found
        return {
            'stage_control': {
                'invert_x_default': False,
                'invert_z_default': False,
            }
        }

    def set_objective_calibration(self, x: float, y: float, z: float, r: float = 0):
        """Set and save the objective XY calibration point.

        Args:
            x, y, z: Stage position in mm when sample holder tip is centered in live view
            r: Rotation angle (stored but not critical for calibration)
        """
        self._chamber_viz.set_objective_calibration(x, y, z, r)

    def _load_channel_settings_from_config(self) -> Dict[int, Dict[str, Any]]:
        """Load channel settings (contrast, visibility) from visualization config.

        Returns:
            Dictionary mapping channel index to settings dict with:
            - visible: bool
            - contrast_min: int
            - contrast_max: int
        """
        channel_states = {}
        channels_config = self._config.get('channels', [])

        for i in range(4):
            # Find channel config by id
            channel_config = None
            for ch in channels_config:
                if ch.get('id') == i:
                    channel_config = ch
                    break

            if channel_config:
                channel_states[i] = {
                    'visible': channel_config.get('default_visible', True),
                    'contrast_min': channel_config.get('default_contrast_min', 0),
                    'contrast_max': channel_config.get('default_contrast_max', 65535),
                }
                self.logger.debug(f"Loaded channel {i} settings from config: {channel_states[i]}")
            else:
                # Default if not in config
                channel_states[i] = {
                    'visible': True,
                    'contrast_min': 0,
                    'contrast_max': 65535,
                }

        # Also load live display settings for the main display
        live_config = self._config.get('live_display', {})
        self._intensity_min = live_config.get('default_contrast_min', 0)
        self._intensity_max = live_config.get('default_contrast_max', 65535)
        self._auto_scale = live_config.get('auto_scale', True)

        self.logger.info(f"Loaded channel and live display settings from config")

        return channel_states

    def _setup_ui(self) -> None:
        """Create and layout all UI components."""
        main_layout = QHBoxLayout()
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # ========== LEFT COLUMN: Live Camera + Display + Illumination ==========
        left_column = QVBoxLayout()
        left_column.setSpacing(6)

        # Live Camera Feed (4:3 aspect ratio)
        left_column.addWidget(self._create_live_feed_section())

        # Display controls row: Range controls + Settings button
        display_row = QHBoxLayout()
        display_row.addWidget(self._create_range_controls(), stretch=1)

        # Small "Live View Settings" button next to display controls
        self.live_settings_btn = QPushButton("Settings")
        self.live_settings_btn.setToolTip("Open Live View Settings dialog")
        self.live_settings_btn.clicked.connect(self._on_live_settings_clicked)
        self.live_settings_btn.setMaximumWidth(70)
        self.live_settings_btn.setStyleSheet("QPushButton { padding: 4px 8px; font-size: 9pt; }")
        display_row.addWidget(self.live_settings_btn)

        left_column.addLayout(display_row)

        # Illumination Controls
        left_column.addWidget(self._create_illumination_section())

        # Live View toggle button (green when stopped, red when active) - compact
        self.live_view_toggle_btn = QPushButton("Start Live")
        self.live_view_toggle_btn.setCheckable(True)
        self.live_view_toggle_btn.clicked.connect(self._on_live_view_toggle)
        self.live_view_toggle_btn.setStyleSheet(
            f"QPushButton {{ background-color: {SUCCESS_COLOR}; color: white; "
            f"font-weight: bold; padding: 6px 12px; font-size: 10pt; }}"
            f"QPushButton:checked {{ background-color: {ERROR_COLOR}; }}"
        )
        self.live_view_toggle_btn.setMaximumWidth(120)
        left_column.addWidget(self.live_view_toggle_btn)

        left_column.addStretch()

        left_widget = QWidget()
        left_widget.setLayout(left_column)
        left_widget.setMinimumWidth(380)
        left_widget.setMaximumWidth(450)
        main_layout.addWidget(left_widget)

        # ========== CENTER COLUMN: 3D View (tall/vertical) ==========
        center_column = QVBoxLayout()
        center_column.setSpacing(6)

        # 3D Volume View (tall for vertical chamber)
        center_column.addWidget(self._create_3d_view_section(), stretch=1)

        # Position Sliders below 3D view
        center_column.addWidget(self._create_position_sliders())

        center_widget = QWidget()
        center_widget.setLayout(center_column)
        main_layout.addWidget(center_widget, stretch=1)

        # ========== RIGHT COLUMN: Plane Views + Channel Controls ==========
        right_column = QVBoxLayout()
        right_column.setSpacing(6)

        # Plane views with XY and YZ side by side (XZ on top)
        right_column.addWidget(self._create_plane_views_section())

        # Channel controls for 4 viewers (contrast + visibility)
        right_column.addWidget(self._create_channel_controls())

        # Viewer Controls button
        self.viewer_controls_btn = QPushButton("Viewer Controls")
        self.viewer_controls_btn.clicked.connect(self._on_viewer_controls_clicked)
        right_column.addWidget(self.viewer_controls_btn)

        # Workflow Progress
        right_column.addWidget(self._create_workflow_progress())

        # Button bar
        right_column.addWidget(self._create_button_bar())

        right_widget = QWidget()
        right_widget.setLayout(right_column)
        right_widget.setMinimumWidth(340)
        right_widget.setMaximumWidth(500)
        main_layout.addWidget(right_widget)

        self.setLayout(main_layout)

    def _create_live_feed_section(self) -> QGroupBox:
        """Create the live camera feed display section with 4:3 aspect ratio."""
        group = QGroupBox("Live Camera Feed")
        layout = QVBoxLayout()
        layout.setSpacing(4)

        # Image display label - constrained to 4:3 aspect ratio
        # Using 320x240 as base size that scales up to fit available space
        self.live_image_label = QLabel("No image - Start live view from main window")
        self.live_image_label.setAlignment(Qt.AlignCenter)
        self.live_image_label.setMinimumSize(320, 240)  # 4:3 minimum
        self.live_image_label.setFixedSize(360, 270)    # 4:3 fixed size for compact layout
        self.live_image_label.setStyleSheet(
            "QLabel { background-color: black; color: gray; border: 1px solid #444; }"
        )
        self.live_image_label.setScaledContents(False)
        layout.addWidget(self.live_image_label, alignment=Qt.AlignCenter)

        # Status row
        status_layout = QHBoxLayout()
        self.live_status_label = QLabel("Status: Idle")
        self.live_status_label.setStyleSheet("color: #888; font-size: 9pt;")
        status_layout.addWidget(self.live_status_label)

        status_layout.addStretch()

        self.fps_label = QLabel("FPS: --")
        self.fps_label.setStyleSheet("color: #888; font-size: 9pt;")
        status_layout.addWidget(self.fps_label)

        layout.addLayout(status_layout)

        group.setLayout(layout)
        return group

    def _create_range_controls(self) -> QWidget:
        """Create Min-Max range control with dual-handle slider and editable spinboxes."""
        widget = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Row 1: Colormap + Auto checkbox
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        row1.addWidget(QLabel("Display:"))
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(["Grayscale", "Hot", "Viridis", "Plasma", "Inferno"])
        self.colormap_combo.setCurrentText("Grayscale")
        self.colormap_combo.currentTextChanged.connect(self._on_colormap_changed)
        self.colormap_combo.setMaximumWidth(90)
        row1.addWidget(self.colormap_combo)

        self.auto_scale_checkbox = QCheckBox("Auto")
        self.auto_scale_checkbox.setChecked(True)
        self.auto_scale_checkbox.stateChanged.connect(self._on_auto_scale_changed)
        row1.addWidget(self.auto_scale_checkbox)

        row1.addStretch()
        main_layout.addLayout(row1)

        # Row 2: Min spinbox + dual-handle range slider + Max spinbox
        row2 = QHBoxLayout()
        row2.setSpacing(4)

        # Min value spinbox (editable)
        self.min_intensity_spinbox = QSpinBox()
        self.min_intensity_spinbox.setRange(0, 65535)
        self.min_intensity_spinbox.setValue(0)
        self.min_intensity_spinbox.setMaximumWidth(70)
        self.min_intensity_spinbox.setEnabled(False)
        self.min_intensity_spinbox.valueChanged.connect(self._on_min_spinbox_changed)
        row2.addWidget(self.min_intensity_spinbox)

        # Dual-handle range slider (from superqt)
        self.range_slider = QRangeSlider(Qt.Horizontal)
        self.range_slider.setRange(0, 65535)
        self.range_slider.setValue((0, 65535))  # (min, max) tuple
        self.range_slider.setEnabled(False)
        self.range_slider.setToolTip("Drag handles to adjust contrast range")
        # Style the range slider handles to be visible
        self.range_slider.setStyleSheet("""
            QRangeSlider {
                qproperty-barColor: #2196F3;
            }
            QRangeSlider::handle {
                background: #1976D2;
                border: 2px solid #0D47A1;
                border-radius: 6px;
                width: 12px;
                height: 12px;
            }
            QRangeSlider::handle:hover {
                background: #1565C0;
            }
        """)
        self.range_slider.valueChanged.connect(self._on_range_slider_changed)
        row2.addWidget(self.range_slider, stretch=1)

        # Max value spinbox (editable)
        self.max_intensity_spinbox = QSpinBox()
        self.max_intensity_spinbox.setRange(0, 65535)
        self.max_intensity_spinbox.setValue(65535)
        self.max_intensity_spinbox.setMaximumWidth(70)
        self.max_intensity_spinbox.setEnabled(False)
        self.max_intensity_spinbox.valueChanged.connect(self._on_max_spinbox_changed)
        row2.addWidget(self.max_intensity_spinbox)

        main_layout.addLayout(row2)

        widget.setLayout(main_layout)
        widget.setStyleSheet("background-color: #f0f0f0; border-radius: 4px;")
        return widget

    def _create_illumination_section(self) -> QGroupBox:
        """Create illumination controls section with minimum width to prevent squishing."""
        group = QGroupBox("Illumination")

        # Use the existing LaserLEDControlPanel
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)

        self.laser_led_panel = LaserLEDControlPanel(self.laser_led_controller)
        self.laser_led_panel.setMinimumWidth(320)  # Prevent squishing
        layout.addWidget(self.laser_led_panel)

        group.setLayout(layout)
        group.setMinimumWidth(340)  # Ensure group doesn't squish
        return group

    def _create_3d_view_section(self) -> QGroupBox:
        """Create 3D volume view section (placeholder for napari) - tall/vertical."""
        group = QGroupBox("3D Volume View")
        layout = QVBoxLayout()

        # Placeholder for napari viewer - tall layout for vertical sample chamber
        # Chamber dimensions: X ~11mm, Y ~20mm (vertical), Z ~13.5mm
        self.viewer_placeholder = QLabel("3D Napari Viewer\n(Will be integrated)")
        self.viewer_placeholder.setAlignment(Qt.AlignCenter)
        self.viewer_placeholder.setStyleSheet(
            "QLabel { background-color: #1a1a2e; color: #888; "
            "border: 2px dashed #444; font-size: 14pt; }"
        )
        self.viewer_placeholder.setMinimumSize(250, 450)  # Tall/vertical orientation
        self.viewer_placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.viewer_placeholder)

        # Info row: Navigation help, Memory/Voxels stats, Zoom, Reset button
        info_row = QHBoxLayout()

        # Navigation help button (left side)
        self.nav_help_btn = QPushButton("?")
        self.nav_help_btn.setFixedSize(24, 24)
        self.nav_help_btn.setStyleSheet("""
            QPushButton {
                background-color: #555;
                color: white;
                border-radius: 12px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #777; }
        """)
        self.nav_help_btn.setToolTip(
            "3D Navigation Controls:\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Left drag        → Rotate view\n"
            "Shift+Left drag  → Pan/translate\n"
            "Scroll wheel     → Zoom in/out\n"
            "Right drag       → Zoom in/out\n"
            "Double-click     → Zoom in 2x"
        )
        info_row.addWidget(self.nav_help_btn)

        info_row.addStretch()

        # Memory usage label
        self.memory_label = QLabel("Memory: -- MB")
        self.memory_label.setStyleSheet("color: #888; font-size: 9pt;")
        info_row.addWidget(self.memory_label)

        info_row.addSpacing(10)

        # Voxel count label
        self.voxel_label = QLabel("Voxels: --")
        self.voxel_label.setStyleSheet("color: #888; font-size: 9pt;")
        info_row.addWidget(self.voxel_label)

        info_row.addStretch()

        # Zoom display
        self.zoom_label = QLabel("Zoom: --")
        self.zoom_label.setStyleSheet("color: #888; font-size: 9pt;")
        info_row.addWidget(self.zoom_label)

        # Reset view button next to zoom
        self.reset_view_btn = QPushButton("⟲ Reset")
        self.reset_view_btn.setToolTip("Reset camera view to defaults (orientation and zoom)")
        self.reset_view_btn.setMaximumWidth(70)
        self.reset_view_btn.setStyleSheet("""
            QPushButton {
                font-size: 9pt;
                padding: 2px 6px;
                border: 1px solid #666;
                border-radius: 3px;
                background: #3a3a5a;
                color: #ccc;
            }
            QPushButton:hover {
                background: #4a4a7a;
                color: #fff;
            }
        """)
        self.reset_view_btn.clicked.connect(self._on_reset_zoom_clicked)
        info_row.addWidget(self.reset_view_btn)

        layout.addLayout(info_row)

        # Quality row: Fast Transform checkbox
        quality_row = QHBoxLayout()
        quality_row.addStretch()

        self.fast_transform_cb = QCheckBox("Fast Transform")
        self.fast_transform_cb.setChecked(True)
        self.fast_transform_cb.setToolTip(
            "Checked: Faster rendering (nearest-neighbor)\n"
            "Unchecked: Smoother rendering (linear interpolation)"
        )
        self.fast_transform_cb.toggled.connect(self._on_transform_quality_changed)
        quality_row.addWidget(self.fast_transform_cb)

        quality_row.addStretch()
        layout.addLayout(quality_row)

        group.setLayout(layout)
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        return group

    def _create_position_sliders(self) -> QGroupBox:
        """Create position control sliders for all axes."""
        group = QGroupBox("Position Sliders")
        layout = QVBoxLayout()
        layout.setSpacing(6)

        # Store slider references
        self.position_sliders: Dict[str, QSlider] = {}
        self.position_edits: Dict[str, QLineEdit] = {}

        # Create slider for each axis
        axes = [
            ('x', 'X', 'mm', 3),
            ('y', 'Y', 'mm', 3),
            ('z', 'Z', 'mm', 3),
            ('r', 'R', '°', 2),
        ]

        for axis_id, axis_name, unit, decimals in axes:
            row = QHBoxLayout()
            row.setSpacing(8)

            # Get axis color (XYZ have colors, R doesn't)
            axis_color = AXIS_COLORS.get(axis_id, '#666666')

            # Axis label with color
            axis_label = QLabel(f"<b>{axis_name}:</b>")
            axis_label.setMinimumWidth(25)
            axis_label.setStyleSheet(f"color: {axis_color}; font-size: 11pt;")
            row.addWidget(axis_label)

            # Min value label
            min_label = QLabel("0.0")
            min_label.setStyleSheet("color: #666; font-size: 9pt;")
            min_label.setMinimumWidth(50)
            min_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(min_label)

            # Slider with colored groove
            slider = QSlider(Qt.Horizontal)
            slider.setMinimum(0)
            slider.setMaximum(100000)  # Will be updated with real limits
            slider.setValue(50000)
            slider.setTickPosition(QSlider.TicksBelow)
            # Style the slider with axis color
            slider.setStyleSheet(f"""
                QSlider::groove:horizontal {{
                    border: 1px solid {axis_color};
                    height: 6px;
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #333, stop:1 {axis_color});
                    margin: 2px 0;
                    border-radius: 3px;
                }}
                QSlider::handle:horizontal {{
                    background: {axis_color};
                    border: 1px solid #333;
                    width: 14px;
                    margin: -5px 0;
                    border-radius: 7px;
                }}
                QSlider::handle:horizontal:hover {{
                    background: white;
                    border: 2px solid {axis_color};
                }}
            """)
            slider.valueChanged.connect(
                lambda val, a=axis_id: self._on_position_slider_changed(a, val)
            )
            slider.sliderReleased.connect(
                lambda a=axis_id: self._on_position_slider_released(a)
            )
            self.position_sliders[axis_id] = slider
            row.addWidget(slider, stretch=1)

            # Max value label
            max_label = QLabel("100.0")
            max_label.setStyleSheet("color: #666; font-size: 9pt;")
            max_label.setMinimumWidth(50)
            row.addWidget(max_label)

            # Current value - editable field with validation
            value_edit = QLineEdit(f"50.000")
            value_edit.setStyleSheet(
                "background-color: #e3f2fd; padding: 4px; "
                "border: 1px solid #2196f3; border-radius: 3px; "
                "font-weight: bold; min-width: 70px; max-width: 80px;"
            )
            value_edit.setAlignment(Qt.AlignCenter)
            # Validator will be set when limits are known
            validator = QDoubleValidator(0.0, 100.0, decimals)
            validator.setNotation(QDoubleValidator.StandardNotation)
            value_edit.setValidator(validator)
            value_edit.editingFinished.connect(
                lambda a=axis_id: self._on_position_edit_finished(a)
            )
            self.position_edits[axis_id] = value_edit
            row.addWidget(value_edit)

            # Unit label
            unit_label = QLabel(unit)
            unit_label.setStyleSheet("font-weight: bold; min-width: 20px;")
            row.addWidget(unit_label)

            # Store min/max labels for later updates
            slider.setProperty('min_label', min_label)
            slider.setProperty('max_label', max_label)
            slider.setProperty('unit', unit)
            slider.setProperty('decimals', decimals)
            slider.setProperty('value_edit', value_edit)

            layout.addLayout(row)

        group.setLayout(layout)
        return group

    def _on_position_edit_finished(self, axis: str) -> None:
        """Handle position edit field value change (when user presses Enter or focus leaves)."""
        if axis not in self.position_edits:
            return

        edit = self.position_edits[axis]
        slider = self.position_sliders[axis]

        try:
            # Parse the entered value
            value_text = edit.text().strip()
            value = float(value_text)

            # Clamp to valid range
            min_val = slider.minimum() / self._slider_scale
            max_val = slider.maximum() / self._slider_scale
            clamped_value = max(min_val, min(max_val, value))

            # Update the edit field if value was clamped
            decimals = slider.property('decimals')
            if clamped_value != value:
                edit.setText(f"{clamped_value:.{decimals}f}")

            # Update slider (without triggering movement yet)
            slider.blockSignals(True)
            slider.setValue(int(clamped_value * self._slider_scale))
            slider.blockSignals(False)

            # Send movement command
            self._send_position_command(axis, clamped_value)

        except ValueError:
            # Invalid input - restore from slider
            current_value = slider.value() / self._slider_scale
            decimals = slider.property('decimals')
            edit.setText(f"{current_value:.{decimals}f}")

    def _create_plane_views_section(self) -> QWidget:
        """Create the three MIP plane views section with proportions based on stage dimensions.

        Stage dimensions: X ~11mm, Y ~20mm (vertical), Z ~13.5mm
        - XZ (Top-Down): ~square (11:13.5) - on top, X horizontal, Z vertical
        - XY (Front View): tall (11:20) - bottom left, X horizontal, Y vertical
        - YZ (Side View): tall (13.5:20) - bottom right, Z horizontal, Y vertical

        Borders are colored to match napari axis colors:
        - X: Cyan (#008B8B)
        - Y: Magenta (#8B008B)
        - Z: Yellow (#8B8B00)
        """
        widget = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(4)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header row with help button
        header_row = QHBoxLayout()
        header_row.addStretch()
        plane_help_btn = QPushButton("?")
        plane_help_btn.setFixedSize(24, 24)
        plane_help_btn.setStyleSheet("""
            QPushButton {
                background-color: #555;
                color: white;
                border-radius: 12px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #777; }
        """)
        plane_help_btn.setToolTip(
            "2D MIP Plane View Controls:\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            "\n"
            "Overlay Markers:\n"
            "  \u25cb Yellow circle    \u2192 Objective focal point\n"
            "  + White cross      \u2192 Current stage position\n"
            "  \u2295 Orange crosshair \u2192 Move target (active)\n"
            "  \u2295 Purple crosshair \u2192 Move target (reached)\n"
            "  \u2508 Cyan dashed box  \u2192 3D viewing frame\n"
            "  \u2508 Cyan dashed line \u2192 Focal plane\n"
            "\n"
            "Mouse Controls:\n"
            "  Double-click \u2192 Move stage (2 axes)\n"
            "  Left drag    \u2192 Pan view\n"
            "  Scroll wheel \u2192 Zoom in/out\n"
            "\n"
            "Coordinate readout shown at top-right."
        )
        header_row.addWidget(plane_help_btn)
        layout.addLayout(header_row)

        # Get ranges from config
        # Y range is in chamber/visualization coordinates (0-14mm)
        stage_config = self._config.get('stage_control', {})
        x_range = tuple(stage_config.get('x_range_mm', [1.0, 12.31]))
        y_range = tuple(stage_config.get('y_range_mm', [0.0, 14.0]))
        z_range = tuple(stage_config.get('z_range_mm', [12.5, 26.0]))

        # XZ Plane (Top-Down) - X horizontal, Z vertical
        # X axis inverted to match 3D view (high X on left when invert_x is True)
        xz_group = QGroupBox("XZ Plane (Top-Down)")
        xz_layout = QVBoxLayout()
        xz_layout.setContentsMargins(4, 4, 4, 4)
        # Aspect ~11:13.5 ≈ 0.81, use 180x220
        self.xz_plane_viewer = SlicePlaneViewer('xz', 'x', 'z', 180, 220,
                                                 h_axis_inverted=self._invert_x, v_axis_inverted=False)
        self.xz_plane_viewer.set_ranges(x_range, z_range)
        self.xz_plane_viewer.position_clicked.connect(
            lambda h, v: self._on_plane_click('xz', h, v)
        )
        xz_layout.addWidget(self.xz_plane_viewer, alignment=Qt.AlignCenter)
        xz_group.setLayout(xz_layout)
        layout.addWidget(xz_group)

        # Bottom row: XY and YZ side by side (both tall)
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(4)

        # XY Plane (Front View) - X horizontal, Y vertical
        xy_group = QGroupBox("XY Plane (Front)")
        xy_layout = QVBoxLayout()
        xy_layout.setContentsMargins(4, 4, 4, 4)
        # Aspect ~11:14 ≈ 0.79, use 130x240
        self.xy_plane_viewer = SlicePlaneViewer('xy', 'x', 'y', 130, 240,
                                                 h_axis_inverted=self._invert_x, v_axis_inverted=False)
        self.xy_plane_viewer.set_ranges(x_range, y_range)
        self.xy_plane_viewer.position_clicked.connect(
            lambda h, v: self._on_plane_click('xy', h, v)
        )
        xy_layout.addWidget(self.xy_plane_viewer, alignment=Qt.AlignCenter)
        xy_group.setLayout(xy_layout)
        bottom_row.addWidget(xy_group)

        # YZ Plane (Side View) - Z horizontal, Y vertical
        yz_group = QGroupBox("YZ Plane (Side)")
        yz_layout = QVBoxLayout()
        yz_layout.setContentsMargins(4, 4, 4, 4)
        # Aspect ~13.5:14 ≈ 0.96, use 160x240
        self.yz_plane_viewer = SlicePlaneViewer('yz', 'z', 'y', 160, 240,
                                                 h_axis_inverted=False, v_axis_inverted=False)
        self.yz_plane_viewer.set_ranges(z_range, y_range)
        self.yz_plane_viewer.position_clicked.connect(
            lambda h, v: self._on_plane_click('yz', h, v)
        )
        yz_layout.addWidget(self.yz_plane_viewer, alignment=Qt.AlignCenter)
        yz_group.setLayout(yz_layout)
        bottom_row.addWidget(yz_group)

        layout.addLayout(bottom_row)

        widget.setLayout(layout)
        return widget

    def _create_channel_controls(self) -> QGroupBox:
        """Create channel visibility and contrast controls for 4 viewer channels."""
        group = QGroupBox("Viewer Channels")
        layout = QVBoxLayout()
        layout.setSpacing(2)
        layout.setContentsMargins(6, 6, 6, 6)

        # Get channel names from visualization config (wavelengths)
        # Falls back to default names if config not available
        channels_config = self._config.get('channels', [])
        default_channel_info = [
            {"name": "405nm", "color": "#9370DB"},  # Violet
            {"name": "488nm", "color": "#00CED1"},  # Cyan
            {"name": "561nm", "color": "#32CD32"},  # Green
            {"name": "640nm", "color": "#DC143C"},  # Red
        ]

        # Store widget references
        self.channel_checkboxes: Dict[int, QCheckBox] = {}
        self.channel_contrast_sliders: Dict[int, QRangeSlider] = {}
        self.channel_min_labels: Dict[int, QLabel] = {}
        self.channel_max_labels: Dict[int, QLabel] = {}

        for i in range(4):
            # Get channel config or use default
            if i < len(channels_config):
                ch_config = channels_config[i]
                # Extract wavelength from name like "405nm (DAPI)" -> "405nm"
                name = ch_config.get('name', default_channel_info[i]['name'])
                if '(' in name:
                    name = name.split('(')[0].strip()
            else:
                name = default_channel_info[i]['name']

            # Get colormap color for the channel
            colormap = channels_config[i].get('default_colormap', 'gray') if i < len(channels_config) else 'gray'
            colormap_colors = {
                'blue': '#9370DB', 'green': '#32CD32', 'red': '#DC143C',
                'magenta': '#FF00FF', 'cyan': '#00CED1', 'gray': '#808080'
            }
            color = colormap_colors.get(colormap, default_channel_info[i]['color'])

            row_layout = QHBoxLayout()
            row_layout.setSpacing(4)

            # Visibility checkbox with wavelength name
            checkbox = QCheckBox(name)
            checkbox.setChecked(self._channel_states[i].get('visible', True))
            checkbox.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: bold; }}")
            checkbox.stateChanged.connect(
                lambda state, ch=i: self._on_channel_visibility_changed(ch, state)
            )
            checkbox.setMinimumWidth(70)
            self.channel_checkboxes[i] = checkbox
            row_layout.addWidget(checkbox)

            # Dual-handle contrast range slider
            # Range is 0-500 for typical fluorescence/brightfield (not full 16-bit)
            slider = QRangeSlider(Qt.Horizontal)
            slider.setRange(0, 500)
            # Load initial values from channel state (clamped to slider range)
            min_val = min(self._channel_states[i].get('contrast_min', 0), 500)
            max_val = min(self._channel_states[i].get('contrast_max', 500), 500)
            slider.setValue((min_val, max_val))
            slider.setToolTip(f"Adjust contrast range for {name}")
            # Style the range slider handles to be visible
            slider.setStyleSheet("""
                QRangeSlider {
                    qproperty-barColor: #2196F3;
                }
                QRangeSlider::handle {
                    background: #1976D2;
                    border: 2px solid #0D47A1;
                    border-radius: 6px;
                    width: 12px;
                    height: 12px;
                }
                QRangeSlider::handle:hover {
                    background: #1565C0;
                }
            """)
            slider.valueChanged.connect(
                lambda val, ch=i: self._on_channel_contrast_changed(ch, val)
            )
            self.channel_contrast_sliders[i] = slider

            # Min/max value labels flanking the slider
            min_label = QLabel(str(min_val))
            min_label.setFixedWidth(28)
            min_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            min_label.setStyleSheet("color: #888; font-size: 9pt;")
            self.channel_min_labels[i] = min_label

            max_label = QLabel(str(max_val))
            max_label.setFixedWidth(28)
            max_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            max_label.setStyleSheet("color: #888; font-size: 9pt;")
            self.channel_max_labels[i] = max_label

            row_layout.addWidget(min_label)
            row_layout.addWidget(slider, stretch=1)
            row_layout.addWidget(max_label)

            # Start channels disabled until data arrives
            checkbox.setEnabled(False)
            checkbox.setChecked(False)
            checkbox.setToolTip(
                f"{name} channel — No data loaded.\n"
                "This channel will activate automatically when 3D volume data is received."
            )
            slider.setEnabled(False)
            min_label.setEnabled(False)
            max_label.setEnabled(False)

            layout.addLayout(row_layout)

        # Auto Contrast button
        auto_contrast_btn = QPushButton("Auto Contrast")
        auto_contrast_btn.setToolTip("Calculate contrast from actual data (2nd-98th percentile)")
        auto_contrast_btn.clicked.connect(self._auto_contrast_channels)
        layout.addWidget(auto_contrast_btn)

        group.setLayout(layout)
        return group

    def _create_workflow_progress(self) -> QWidget:
        """Create workflow progress bar (placeholder - not connected)."""
        widget = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # Status label
        self.workflow_status_label = QLabel("Workflow: Not Running")
        self.workflow_status_label.setStyleSheet("font-weight: bold;")
        self.workflow_status_label.setMinimumWidth(350)
        layout.addWidget(self.workflow_status_label)

        # Progress bar
        self.workflow_progress_bar = QProgressBar()
        self.workflow_progress_bar.setRange(0, 100)
        self.workflow_progress_bar.setValue(0)
        self.workflow_progress_bar.setTextVisible(True)
        self.workflow_progress_bar.setFormat("%p%")
        layout.addWidget(self.workflow_progress_bar, stretch=1)

        # Time remaining
        self.time_remaining_label = QLabel("--:--")
        self.time_remaining_label.setStyleSheet("color: #666;")
        layout.addWidget(self.time_remaining_label)

        widget.setLayout(layout)
        widget.setStyleSheet(
            f"background-color: {WARNING_BG}; border: 1px solid #ffc107; border-radius: 4px;"
        )
        return widget

    def _create_button_bar(self) -> QWidget:
        """Create dialog launcher button bar with data collection controls."""
        widget = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(4)
        layout.setContentsMargins(0, 4, 0, 4)

        # Row 1: Data collection buttons (most important - prominent styling)
        data_row = QHBoxLayout()
        data_row.setSpacing(8)

        # Populate from Live View toggle button
        self.populate_btn = QPushButton("Populate from Live")
        self.populate_btn.setCheckable(True)
        self.populate_btn.setToolTip("Capture frames from Live View and accumulate into 3D volume")
        self.populate_btn.clicked.connect(self._on_populate_toggled)
        self.populate_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 16px; }"
            "QPushButton:checked { background-color: #f44336; }"
            "QPushButton:hover { background-color: #45a049; }"
            "QPushButton:checked:hover { background-color: #da190b; }"
        )
        data_row.addWidget(self.populate_btn)

        # Clear Data button
        self.clear_data_btn = QPushButton("Clear Data")
        self.clear_data_btn.setToolTip("Clear all accumulated 3D volume data")
        self.clear_data_btn.clicked.connect(self._on_clear_data_clicked)
        self.clear_data_btn.setStyleSheet(
            "QPushButton { background-color: #ff9800; color: white; font-weight: bold; padding: 8px 16px; }"
            "QPushButton:hover { background-color: #f57c00; }"
        )
        data_row.addWidget(self.clear_data_btn)

        data_row.addStretch()
        layout.addLayout(data_row)

        # Row 2: Navigation buttons
        nav_row = QHBoxLayout()
        nav_row.setSpacing(8)

        # Saved Positions button
        self.saved_positions_btn = QPushButton("Saved Positions")
        self.saved_positions_btn.clicked.connect(self._on_saved_positions_clicked)
        nav_row.addWidget(self.saved_positions_btn)

        # Stage Control button
        self.stage_control_btn = QPushButton("Stage Control")
        self.stage_control_btn.clicked.connect(self._on_stage_control_clicked)
        nav_row.addWidget(self.stage_control_btn)

        # Export Data button
        self.export_data_btn = QPushButton("Export Data")
        self.export_data_btn.clicked.connect(self._on_export_data_clicked)
        nav_row.addWidget(self.export_data_btn)

        nav_row.addStretch()
        layout.addLayout(nav_row)

        # Row 3: Performance & Session buttons
        perf_row = QHBoxLayout()
        perf_row.setSpacing(8)

        # Load Test Data button
        self.load_test_data_btn = QPushButton("Load Test Data")
        self.load_test_data_btn.setToolTip("Load .zarr, .tif, or .npy test data into viewer")
        self.load_test_data_btn.clicked.connect(self._on_load_test_data_clicked)
        perf_row.addWidget(self.load_test_data_btn)

        # Save Session button
        self.save_session_btn = QPushButton("Save Session")
        self.save_session_btn.setToolTip("Save current 3D data and settings to OME-Zarr session")
        self.save_session_btn.clicked.connect(self._on_save_session_clicked)
        perf_row.addWidget(self.save_session_btn)

        # Load Session button
        self.load_session_btn = QPushButton("Load Session")
        self.load_session_btn.setToolTip("Load a saved OME-Zarr session")
        self.load_session_btn.clicked.connect(self._on_load_session_clicked)
        perf_row.addWidget(self.load_session_btn)

        # Benchmark button
        self.benchmark_btn = QPushButton("Benchmark")
        self.benchmark_btn.setToolTip("Run performance benchmarks on 3D transforms")
        self.benchmark_btn.clicked.connect(self._on_benchmark_clicked)
        self.benchmark_btn.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; }"
            "QPushButton:hover { background-color: #1976D2; }"
        )
        perf_row.addWidget(self.benchmark_btn)

        # Settings button
        self.settings_btn = QPushButton("Settings")
        self.settings_btn.setToolTip("Open application settings dialog")
        self.settings_btn.clicked.connect(self._on_settings_clicked)
        self.settings_btn.setStyleSheet(
            "QPushButton { background-color: #607D8B; color: white; }"
            "QPushButton:hover { background-color: #455A64; }"
        )
        perf_row.addWidget(self.settings_btn)

        perf_row.addStretch()
        layout.addLayout(perf_row)

        widget.setLayout(layout)
        return widget

    def _connect_signals(self) -> None:
        """Connect controller signals."""
        # Camera signals
        if self.camera_controller:
            self.camera_controller.new_image.connect(self._on_frame_received)
            self.camera_controller.state_changed.connect(self._on_camera_state_changed)

            # Connect tile Z-stack frame signal for Sample View integration
            if hasattr(self.camera_controller, 'tile_zstack_frame'):
                self.camera_controller.tile_zstack_frame.connect(self._on_tile_zstack_frame)
                self.logger.info("Connected tile Z-stack frame signal for Sample View integration")

        # Movement signals
        if self.movement_controller:
            self.movement_controller.position_changed.connect(self._on_position_changed)

        self.logger.info("SampleView signals connected")

    def _init_stage_limits(self) -> None:
        """Initialize stage limits from movement controller and set current positions."""
        if not self.movement_controller:
            return

        try:
            self._stage_limits = self.movement_controller.get_stage_limits()

            # Update sliders with actual limits
            for axis_id in ['x', 'y', 'z', 'r']:
                if axis_id in self._stage_limits and axis_id in self.position_sliders:
                    limits = self._stage_limits[axis_id]
                    slider = self.position_sliders[axis_id]

                    min_val = limits['min']
                    max_val = limits['max']

                    # Update slider range (scaled to integers)
                    slider.setMinimum(int(min_val * self._slider_scale))
                    slider.setMaximum(int(max_val * self._slider_scale))

                    # Update edit field validator with actual limits
                    if axis_id in self.position_edits:
                        edit = self.position_edits[axis_id]
                        validator = edit.validator()
                        if validator:
                            validator.setRange(min_val, max_val, validator.decimals())

                    # Update min/max labels
                    min_label = slider.property('min_label')
                    max_label = slider.property('max_label')
                    decimals = slider.property('decimals')

                    # For X axis: if inverted, swap the displayed labels (high on left, low on right)
                    if axis_id == 'x' and self._invert_x:
                        # Inverted: show max on left, min on right
                        if min_label:
                            min_label.setText(f"{max_val:.{decimals}f}")
                        if max_label:
                            max_label.setText(f"{min_val:.{decimals}f}")
                        # Mark slider as inverted for value display
                        slider.setProperty('inverted', True)
                        slider.setInvertedAppearance(True)
                    else:
                        # Normal: show min on left, max on right
                        if min_label:
                            min_label.setText(f"{min_val:.{decimals}f}")
                        if max_label:
                            max_label.setText(f"{max_val:.{decimals}f}")
                        slider.setProperty('inverted', False)

            self.logger.info(f"Stage limits initialized (X inverted: {self._invert_x})")

            # Query and set CURRENT stage position (critical for safety!)
            self._load_current_positions()

        except Exception as e:
            self.logger.error(f"Error initializing stage limits: {e}")

    def _load_current_positions(self) -> None:
        """Load current stage positions from movement controller and update sliders.

        This is critical for safety - sliders must reflect actual stage position,
        not default values that could cause dangerous movements.
        """
        if not self.movement_controller:
            self.logger.warning("No movement controller - cannot load current positions")
            return

        try:
            # Get current position from controller (returns Position object)
            current_pos = self.movement_controller.get_position()

            if current_pos is None:
                self.logger.warning("Could not retrieve current position from controller")
                return

            # Extract positions (current_pos is a Position object when axis=None)
            positions = {
                'x': current_pos.x,
                'y': current_pos.y,
                'z': current_pos.z,
                'r': current_pos.r
            }

            self.logger.info(f"Loading current positions: X={positions['x']:.3f}, "
                           f"Y={positions['y']:.3f}, Z={positions['z']:.3f}, R={positions['r']:.2f}")

            # Update internal position tracking (critical for 3D viewer and transforms!)
            self.last_stage_position = positions.copy()
            self.current_rotation['ry'] = positions['r']
            self.logger.info(f"Updated last_stage_position and current_rotation from hardware")

            # Update each slider to current position
            for axis_id, value in positions.items():
                if axis_id in self.position_sliders and value is not None:
                    slider = self.position_sliders[axis_id]
                    edit = self.position_edits[axis_id]

                    # Block signals to prevent triggering movement commands
                    slider.blockSignals(True)
                    slider.setValue(int(value * self._slider_scale))
                    slider.blockSignals(False)

                    # Update value edit field (unit is now a separate label)
                    decimals = slider.property('decimals')
                    edit.blockSignals(True)
                    edit.setText(f"{value:.{decimals}f}")
                    edit.blockSignals(False)

            self.logger.info("Slider positions initialized from current stage position")

        except Exception as e:
            self.logger.error(f"Error loading current positions: {e}")
            import traceback
            traceback.print_exc()

    # ========== Slot Handlers ==========

    @pyqtSlot(object, object)
    def _on_frame_received(self, image: np.ndarray, header) -> None:
        """Handle received camera frame."""
        self._current_image = image
        self._update_live_display()

    def _update_live_display(self) -> None:
        """Update the live image display."""
        if self._current_image is None:
            return

        try:
            image = self._current_image

            # Apply intensity scaling
            if self._auto_scale:
                # Use stabilized auto-contrast (adjusts at most once per interval)
                img_min = 0  # Always use 0 as min for consistency
                img_max = self._calculate_auto_contrast(image)
            else:
                img_min = self._intensity_min
                img_max = self._intensity_max

            # Normalize to 0-255
            if img_max > img_min:
                normalized = ((image.astype(np.float32) - img_min) /
                             (img_max - img_min) * 255).clip(0, 255).astype(np.uint8)
            else:
                normalized = np.zeros_like(image, dtype=np.uint8)

            # Convert to QImage and display
            height, width = normalized.shape
            bytes_per_line = width
            qimage = QImage(normalized.data, width, height, bytes_per_line, QImage.Format_Grayscale8)

            # Scale to fit label while maintaining aspect ratio
            pixmap = QPixmap.fromImage(qimage)
            scaled_pixmap = pixmap.scaled(
                self.live_image_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            self.live_image_label.setPixmap(scaled_pixmap)

        except Exception as e:
            self.logger.error(f"Error updating live display: {e}")

    def _calculate_auto_contrast(self, image: np.ndarray) -> int:
        """Calculate auto-contrast max value with stabilization.

        Uses a percentage-based algorithm that only adjusts once per interval:
        - If image max is much lower than current max: jump directly to data-based value
        - If >20% pixels are saturated (>=95% of current max): raise max to 95% of top 5% mean
        - If <5% pixels are above 70% of current max: lower max by 10%
        - Otherwise: keep current max (stable)

        Args:
            image: The current camera frame

        Returns:
            The contrast max value to use for display
        """
        current_time = time.time()
        time_since_last = current_time - self._last_contrast_adjustment

        # Only recalculate if enough time has passed
        if time_since_last < self._auto_contrast_interval:
            return self._auto_contrast_max

        # Calculate pixel statistics
        total_pixels = image.size
        current_max = self._auto_contrast_max

        # Quick check: if image is very dark compared to current_max, jump directly
        # This handles the case where we start at 65535 but sample is dim
        image_actual_max = int(np.max(image))
        if image_actual_max < current_max * 0.1:  # Actual max is less than 10% of display max
            # Image is very dark - set max based on actual data
            # Use 99th percentile for robustness against hot pixels
            p99 = np.percentile(image, 99)
            new_max = int(p99 / 0.85)  # Set so 99th percentile is at 85% brightness
            new_max = max(100, min(65535, new_max))  # Clamp to reasonable range

            if new_max < current_max * 0.5:  # Only jump if it's a significant change
                self.logger.info(f"Auto-contrast: quick adjustment {current_max} -> {new_max} "
                                f"(image max={image_actual_max}, p99={p99:.0f})")
                self._auto_contrast_max = new_max
                self._last_contrast_adjustment = current_time
                return self._auto_contrast_max

        # Count saturated pixels (>= 95% of current max)
        saturation_level = current_max * self._saturation_percentile
        saturated_count = np.sum(image >= saturation_level)
        saturated_ratio = saturated_count / total_pixels

        if saturated_ratio > self._saturation_threshold:
            # Too many saturated pixels - raise max to 95% of top 5% mean
            # This allows large jumps when transitioning to heavily stained areas
            top_5_percent_count = max(1, int(total_pixels * 0.05))
            # Use partition for efficiency (faster than full sort)
            top_values = np.partition(image.ravel(), -top_5_percent_count)[-top_5_percent_count:]
            top_5_mean = np.mean(top_values)
            new_max = int(top_5_mean / 0.95)  # Set so top 5% mean is at 95%
            new_max = min(65535, max(1000, new_max))  # Clamp to reasonable range

            if new_max != self._auto_contrast_max:
                self.logger.debug(f"Auto-contrast: raising max {self._auto_contrast_max} -> {new_max} "
                                 f"(saturated: {saturated_ratio:.1%}, top 5% mean: {top_5_mean:.0f})")
                self._auto_contrast_max = new_max
                self._last_contrast_adjustment = current_time
        else:
            # Check if we should lower the max (image is too dark)
            brightness_level = current_max * self._brightness_reference
            bright_count = np.sum(image > brightness_level)
            bright_ratio = bright_count / total_pixels

            if bright_ratio < self._low_brightness_threshold:
                # Too few bright pixels - lower max by 10%
                new_max = int(current_max * 0.90)
                new_max = max(1000, new_max)  # Don't go below 1000

                if new_max != self._auto_contrast_max:
                    self.logger.debug(f"Auto-contrast: lowering max {self._auto_contrast_max} -> {new_max} "
                                     f"(bright pixels: {bright_ratio:.1%})")
                    self._auto_contrast_max = new_max
                    self._last_contrast_adjustment = current_time

        return self._auto_contrast_max

    @pyqtSlot(object)
    def _on_camera_state_changed(self, state) -> None:
        """Handle camera state change.

        Updates both the status label and the live view button to reflect
        the actual camera state. This ensures the GUI stays in sync when
        the camera is controlled externally (e.g., by workflows).
        """
        state_names = {0: "Idle", 1: "Starting", 2: "Running", 3: "Stopping"}
        state_name = state_names.get(state.value if hasattr(state, 'value') else state, "Unknown")
        self.live_status_label.setText(f"Status: {state_name}")

        # Also update the live view button to match actual camera state
        self._update_live_view_state()

    @pyqtSlot(float, float, float, float)
    def _on_position_changed(self, x: float, y: float, z: float, r: float) -> None:
        """Handle position change from movement controller."""
        self.logger.debug(f"Position changed signal received: X={x:.3f}, Y={y:.3f}, Z={z:.3f}, R={r:.1f}")
        positions = {'x': x, 'y': y, 'z': z, 'r': r}

        for axis_id, value in positions.items():
            if axis_id in self.position_sliders:
                slider = self.position_sliders[axis_id]
                edit = self.position_edits[axis_id]

                # Block signals to prevent feedback loop
                slider.blockSignals(True)
                slider.setValue(int(value * self._slider_scale))
                slider.blockSignals(False)

                # Update value edit field
                decimals = slider.property('decimals')
                edit.blockSignals(True)
                edit.setText(f"{value:.{decimals}f}")
                edit.blockSignals(False)

        # Queue 3D visualization update (throttled to 20 FPS max)
        self._pending_stage_update = {'x': x, 'y': y, 'z': z, 'r': r}
        if not self._stage_update_timer.isActive():
            self._stage_update_timer.start()

        # Update plane view overlays with current position
        self._update_plane_overlays()

        # Mark target markers as stale when stage reaches target position
        self._check_and_mark_targets_stale(x, y, z)

    def _on_position_slider_changed(self, axis: str, value: int) -> None:
        """Handle position slider value change (during drag)."""
        if axis in self.position_sliders:
            slider = self.position_sliders[axis]
            edit = self.position_edits[axis]

            real_value = value / self._slider_scale
            decimals = slider.property('decimals')
            edit.blockSignals(True)
            edit.setText(f"{real_value:.{decimals}f}")
            edit.blockSignals(False)

    def _on_position_slider_released(self, axis: str) -> None:
        """Handle position slider release - send move command."""
        if axis in self.position_sliders:
            slider = self.position_sliders[axis]
            real_value = slider.value() / self._slider_scale
            self._send_position_command(axis, real_value)

    def _send_position_command(self, axis: str, value: float) -> None:
        """Send a movement command to the specified axis.

        Args:
            axis: The axis to move ('x', 'y', 'z', or 'r')
            value: The target position value
        """
        if not self.movement_controller:
            return

        try:
            self.movement_controller.move_absolute(axis, value, verify=False)
            self.logger.info(f"Moving {axis.upper()} to {value:.3f}")
        except Exception as e:
            self.logger.error(f"Error moving {axis}: {e}")

    def _on_colormap_changed(self, colormap: str) -> None:
        """Handle colormap selection change."""
        self._colormap = colormap
        self._update_live_display()

    def _on_auto_scale_changed(self, state: int) -> None:
        """Handle auto-scale checkbox change."""
        self._auto_scale = (state == Qt.Checked)
        enabled = not self._auto_scale
        self.min_intensity_spinbox.setEnabled(enabled)
        self.max_intensity_spinbox.setEnabled(enabled)
        self.range_slider.setEnabled(enabled)
        self._update_live_display()

    def _on_min_spinbox_changed(self, value: int) -> None:
        """Handle min intensity spinbox change - update slider and display."""
        self._intensity_min = value
        # Ensure min doesn't exceed max
        if value > self._intensity_max:
            self.max_intensity_spinbox.setValue(value)
        # Sync range slider with spinboxes
        self.range_slider.blockSignals(True)
        self.range_slider.setValue((self._intensity_min, self._intensity_max))
        self.range_slider.blockSignals(False)
        self._update_live_display()

    def _on_max_spinbox_changed(self, value: int) -> None:
        """Handle max intensity spinbox change - update slider and display."""
        self._intensity_max = value
        # Ensure max doesn't go below min
        if value < self._intensity_min:
            self.min_intensity_spinbox.setValue(value)
        # Sync range slider with spinboxes
        self.range_slider.blockSignals(True)
        self.range_slider.setValue((self._intensity_min, self._intensity_max))
        self.range_slider.blockSignals(False)
        self._update_live_display()

    def _on_range_slider_changed(self, value: tuple) -> None:
        """Handle range slider change - update spinboxes and display."""
        min_val, max_val = value
        self._intensity_min = min_val
        self._intensity_max = max_val
        # Sync spinboxes with slider
        self.min_intensity_spinbox.blockSignals(True)
        self.max_intensity_spinbox.blockSignals(True)
        self.min_intensity_spinbox.setValue(min_val)
        self.max_intensity_spinbox.setValue(max_val)
        self.min_intensity_spinbox.blockSignals(False)
        self.max_intensity_spinbox.blockSignals(False)
        self._update_live_display()

    def _on_channel_visibility_changed(self, channel: int, state: int) -> None:
        """Handle channel visibility checkbox change."""
        visible = (state == Qt.Checked)
        self._channel_states[channel]['visible'] = visible
        self.logger.debug(f"Channel {channel} visibility: {visible}")

        # Toggle visibility on the actual napari layer
        if self.channel_layers:
            layer = self.channel_layers.get(channel)
            if layer is not None:
                layer.visible = visible

        # Update 2D plane views with new visibility settings
        self._update_plane_views()

    def _on_channel_contrast_changed(self, channel: int, value: tuple) -> None:
        """Handle channel contrast range slider change.

        Args:
            channel: Channel index (0-3)
            value: Tuple of (min, max) contrast values from QRangeSlider
        """
        min_val, max_val = value
        self._channel_states[channel]['contrast_min'] = min_val
        self._channel_states[channel]['contrast_max'] = max_val

        # Update min/max labels
        if channel in self.channel_min_labels:
            self.channel_min_labels[channel].setText(str(min_val))
        if channel in self.channel_max_labels:
            self.channel_max_labels[channel].setText(str(max_val))

        # Update contrast on the actual napari layer
        if self.channel_layers:
            layer = self.channel_layers.get(channel)
            if layer is not None:
                layer.contrast_limits = [min_val, max_val]

        self.logger.debug(f"Channel {channel} contrast range: [{min_val}, {max_val}]")

        # Update 2D plane views with new contrast settings
        self._update_plane_views()

    def _auto_contrast_channels(self) -> None:
        """Calculate and apply contrast based on actual data statistics."""
        if not self.voxel_storage or not self.channel_layers:
            return

        for ch_id, layer in self.channel_layers.items():
            if not self.voxel_storage.has_data(ch_id):
                continue

            volume = self.voxel_storage.get_display_volume(ch_id)
            if volume is None or volume.size == 0:
                continue

            # Calculate percentile-based contrast (2nd to 98th percentile)
            non_zero = volume[volume > 0]
            if len(non_zero) == 0:
                continue

            min_val = int(np.percentile(non_zero, 2))
            max_val = int(np.percentile(non_zero, 98))

            # Ensure min < max
            if max_val <= min_val:
                max_val = min_val + 10

            # Update layer contrast
            layer.contrast_limits = (min_val, max_val)

            # Update UI slider and labels
            if ch_id in self.channel_contrast_sliders:
                slider = self.channel_contrast_sliders[ch_id]
                # Expand slider range if needed
                current_max = slider.maximum()
                if max_val > current_max:
                    slider.setRange(0, max(max_val + 100, 1000))
                slider.blockSignals(True)
                slider.setValue((min_val, max_val))
                slider.blockSignals(False)

            if ch_id in self.channel_min_labels:
                self.channel_min_labels[ch_id].setText(str(min_val))
            if ch_id in self.channel_max_labels:
                self.channel_max_labels[ch_id].setText(str(max_val))

            # Update channel state
            self._channel_states[ch_id]['contrast_min'] = min_val
            self._channel_states[ch_id]['contrast_max'] = max_val

            self.logger.debug(f"Auto-contrast channel {ch_id}: [{min_val}, {max_val}]")

    def _update_channel_availability(self) -> None:
        """Enable/disable channel controls based on whether data exists."""
        if not self.voxel_storage:
            return

        channels_config = self._config.get('channels', [])

        for ch_id in range(4):
            has_data = self.voxel_storage.has_data(ch_id)
            checkbox = self.channel_checkboxes.get(ch_id)
            slider = self.channel_contrast_sliders.get(ch_id)
            min_lbl = self.channel_min_labels.get(ch_id)
            max_lbl = self.channel_max_labels.get(ch_id)

            if checkbox and has_data and not checkbox.isEnabled():
                # Auto-enable on first data arrival
                checkbox.setEnabled(True)
                checkbox.setChecked(True)
                if slider:
                    slider.setEnabled(True)
                if min_lbl:
                    min_lbl.setEnabled(True)
                if max_lbl:
                    max_lbl.setEnabled(True)

                # Explicitly set layer visibility (don't rely only on signal)
                if ch_id in self.channel_layers:
                    self.channel_layers[ch_id].visible = True
                    self._channel_states[ch_id]['visible'] = True

                name = channels_config[ch_id].get('name', f'Ch {ch_id}') if ch_id < len(channels_config) else f'Ch {ch_id}'
                checkbox.setToolTip(f"{name} — Data available. Click to toggle visibility.")
                self.logger.info(f"Channel {ch_id} auto-enabled (data received)")

    def _get_viewer(self):
        """Get the napari viewer owned by this Sample View."""
        return self.viewer

    # ========== Dialog Launchers ==========

    def _on_saved_positions_clicked(self) -> None:
        """Open saved positions dialog."""
        from py2flamingo.views.position_history_dialog import PositionHistoryDialog

        if not self.movement_controller:
            self.logger.warning("No movement controller - cannot open position history")
            return

        try:
            dialog = PositionHistoryDialog(self.movement_controller, parent=self)
            dialog.exec_()
        except Exception as e:
            self.logger.error(f"Error opening position history: {e}")

    def _on_viewer_controls_clicked(self) -> None:
        """Open viewer controls dialog for napari layer settings."""
        dialog = ViewerControlsDialog(
            viewer_container=self,  # SampleView now owns viewer and channel_layers
            config=self._config,
            parent=self
        )
        # Connect signal to update plane views when channel settings change
        dialog.plane_views_update_requested.connect(self._update_plane_views)
        dialog.exec_()

    def _on_stage_control_clicked(self) -> None:
        """Open the Stage Chamber Visualization window."""
        # Try to find and show the stage chamber visualization window
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            for widget in app.topLevelWidgets():
                if widget.__class__.__name__ == 'StageChamberVisualizationWindow':
                    widget.show()
                    widget.raise_()
                    widget.activateWindow()
                    self.logger.info("Opened Stage Chamber Visualization window")
                    return

        self.logger.info("Stage Chamber Visualization window not available")

    def _on_export_data_clicked(self) -> None:
        """Export accumulated 3D data to file."""
        if not self.voxel_storage:
            self.logger.warning("No voxel storage - cannot export data")
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Not Ready",
                              "Voxel storage not initialized.")
            return

        from PyQt5.QtWidgets import QFileDialog, QMessageBox
        from pathlib import Path

        # Get last-used path from configuration service
        default_path = ""
        if self._configuration_service:
            saved_path = self._configuration_service.get_sample_3d_data_path()
            if saved_path and Path(saved_path).exists():
                default_path = saved_path

        # Basic export dialog
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export 3D Data", default_path,
            "TIFF Stack (*.tif);;NumPy Array (*.npy);;All Files (*)"
        )
        if file_path:
            # Remember the directory for next time
            if self._configuration_service:
                self._configuration_service.set_sample_3d_data_path(str(Path(file_path).parent))

            try:
                if self.voxel_storage:
                    data = self.voxel_storage.get_display_data()
                    if data is not None and data.size > 0:
                        if file_path.endswith('.npy'):
                            np.save(file_path, data)
                        else:
                            import tifffile
                            tifffile.imwrite(file_path, data)
                        self.logger.info(f"Exported data to {file_path}")
                        QMessageBox.information(self, "Export Complete",
                                              f"Data exported to:\n{file_path}")
                    else:
                        QMessageBox.warning(self, "No Data",
                                          "No data to export. Use 'Populate from Live' first.")
            except Exception as e:
                self.logger.error(f"Export failed: {e}")
                QMessageBox.critical(self, "Export Error", f"Failed to export: {e}")

    def _on_load_test_data_clicked(self) -> None:
        """Load test data from file for benchmarking and testing."""
        from PyQt5.QtWidgets import QFileDialog, QMessageBox
        from pathlib import Path

        if not self.voxel_storage:
            QMessageBox.warning(self, "Not Ready",
                              "Voxel storage not initialized. Open 3D visualization first.")
            return

        # Get last-used path from configuration service
        default_path = ""
        if self._configuration_service:
            saved_path = self._configuration_service.get_sample_3d_data_path()
            if saved_path and Path(saved_path).exists():
                default_path = saved_path

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Load Test Data", default_path,
            "All Supported (*.zarr *.tif *.tiff *.npy);;Zarr Sessions (*.zarr);;TIFF Files (*.tif *.tiff);;NumPy Arrays (*.npy)"
        )

        if file_path:
            # Remember the directory for next time
            if self._configuration_service:
                self._configuration_service.set_sample_3d_data_path(str(Path(file_path).parent))

            try:
                from py2flamingo.visualization.session_manager import load_test_data

                success = load_test_data(Path(file_path), self.voxel_storage)

                if success:
                    self.logger.info(f"Loaded test data from {file_path}")
                    QMessageBox.information(self, "Data Loaded",
                                          f"Test data loaded successfully from:\n{file_path}")
                    # Update visualization
                    self._update_visualization()
                else:
                    QMessageBox.warning(self, "Load Failed",
                                      f"Failed to load data from:\n{file_path}")
            except Exception as e:
                self.logger.exception(f"Load test data failed: {e}")
                QMessageBox.critical(self, "Load Error", f"Error loading data: {e}")

    def _on_save_session_clicked(self) -> None:
        """Save current 3D data to an OME-Zarr session."""
        from PyQt5.QtWidgets import QInputDialog, QMessageBox

        if not self.voxel_storage:
            QMessageBox.warning(self, "Not Ready",
                              "Voxel storage not initialized. Capture some data first.")
            return

        try:
            from py2flamingo.visualization.session_manager import SessionManager

            if not SessionManager.is_available():
                QMessageBox.warning(self, "Zarr Not Available",
                                  "zarr library not installed.\nInstall with: pip install zarr")
                return

            # Prompt for session name
            session_name, ok = QInputDialog.getText(
                self, "Save Session",
                "Enter session name:",
                text=f"session_{time.strftime('%Y%m%d_%H%M')}"
            )

            if ok and session_name:
                manager = SessionManager()
                session_path = manager.save_session(
                    self.voxel_storage,
                    session_name,
                    description="Saved from Sample View"
                )

                self.logger.info(f"Session saved to {session_path}")
                QMessageBox.information(self, "Session Saved",
                                      f"Session saved to:\n{session_path}")
        except Exception as e:
            self.logger.exception(f"Save session failed: {e}")
            QMessageBox.critical(self, "Save Error", f"Error saving session: {e}")

    def _on_load_session_clicked(self) -> None:
        """Load a saved OME-Zarr session."""
        from PyQt5.QtWidgets import QFileDialog, QMessageBox

        if not self.voxel_storage:
            QMessageBox.warning(self, "Not Ready",
                              "Voxel storage not initialized.")
            return

        try:
            from py2flamingo.visualization.session_manager import SessionManager

            if not SessionManager.is_available():
                QMessageBox.warning(self, "Zarr Not Available",
                                  "zarr library not installed.\nInstall with: pip install zarr")
                return

            # Get last-used path from configuration service (independent of other dialogs)
            start_path = str(SessionManager().session_dir)
            if self._configuration_service:
                saved_path = self._configuration_service.get_zarr_session_path()
                if saved_path:
                    start_path = saved_path

            # Open file dialog for .zarr directory
            file_path = QFileDialog.getExistingDirectory(
                self, "Select Session (.zarr folder)",
                start_path
            )

            if file_path and file_path.endswith('.zarr'):
                from pathlib import Path

                # Save the parent directory for next time
                if self._configuration_service:
                    self._configuration_service.set_zarr_session_path(str(Path(file_path).parent))

                manager = SessionManager()
                metadata = manager.restore_to_storage(self.voxel_storage, Path(file_path))

                self.logger.info(f"Session loaded: {metadata.session_name}")
                QMessageBox.information(self, "Session Loaded",
                                      f"Loaded session: {metadata.session_name}\n"
                                      f"Total voxels: {metadata.total_voxels:,}")

                # Update visualization
                self._update_visualization()
            elif file_path:
                QMessageBox.warning(self, "Invalid Selection",
                                  "Please select a .zarr folder")
        except Exception as e:
            self.logger.exception(f"Load session failed: {e}")
            QMessageBox.critical(self, "Load Error", f"Error loading session: {e}")

    def _on_benchmark_clicked(self) -> None:
        """Open the performance benchmark dialog."""
        try:
            from py2flamingo.views.dialogs.performance_benchmark_dialog import PerformanceBenchmarkDialog

            dialog = PerformanceBenchmarkDialog(
                voxel_storage=self.voxel_storage,
                parent=self
            )
            dialog.exec_()
        except Exception as e:
            self.logger.exception(f"Error opening benchmark dialog: {e}")
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error",
                               f"Could not open benchmark dialog: {e}")

    def _on_settings_clicked(self) -> None:
        """Open the application settings dialog."""
        try:
            from py2flamingo.views.dialogs.settings_dialog import SettingsDialog
            from py2flamingo.services.microscope_settings_service import MicroscopeSettingsService

            # Get or create the settings service
            settings_service = getattr(self, '_settings_service', None)
            if settings_service is None:
                # Try to get microscope name from configuration service
                microscope_name = "n7"  # Default
                if self._configuration_service:
                    config = self._configuration_service.get_current_configuration()
                    if config:
                        microscope_name = config.get('name', 'n7')

                # Create settings service (will use the microscope_settings directory)
                from pathlib import Path
                base_path = Path(__file__).parent.parent.parent.parent  # Go up to project root
                settings_service = MicroscopeSettingsService(microscope_name, base_path)
                self._settings_service = settings_service

            dialog = SettingsDialog(
                settings_service=settings_service,
                parent=self
            )

            if dialog.exec_():
                self.logger.info("Settings dialog accepted - settings saved")
                # Notify user if display settings changed (requires restart)
                settings = dialog.get_settings()
                if 'display' in settings:
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.information(
                        self, "Settings Saved",
                        "Display settings have been saved.\n\n"
                        "Note: Changes to storage voxel size or downsample factor "
                        "will take effect after restarting the application."
                    )
        except Exception as e:
            self.logger.exception(f"Error opening settings dialog: {e}")
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error",
                               f"Could not open settings dialog: {e}")

    # ========== Data Collection Controls ==========

    def _on_populate_toggled(self, checked: bool) -> None:
        """Handle populate from live view start/stop."""
        if not self.voxel_storage:
            self.logger.warning("No voxel storage - cannot populate")
            self.populate_btn.setChecked(False)
            return

        self._is_populating = checked
        if checked:
            self.populate_btn.setText("Stop Populating")
            self.logger.info("Started populating from live view")
            # Start populate timer if not running
            if not hasattr(self, '_populate_timer'):
                self._populate_timer = QTimer()
                self._populate_timer.timeout.connect(self._on_populate_tick)
                self._populate_timer.setInterval(100)  # 10 Hz
            self._populate_timer.start()
        else:
            self.populate_btn.setText("Populate from Live")
            self.logger.info("Stopped populating from live view")
            if hasattr(self, '_populate_timer'):
                self._populate_timer.stop()

    def _on_populate_tick(self) -> None:
        """Capture current frame and add to 3D volume."""
        if not getattr(self, '_is_populating', False) or not self.camera_controller:
            return

        # Don't populate during tile workflows - they write their own data
        if getattr(self, '_tile_workflow_active', False):
            return

        try:
            if not self.camera_controller.is_live_view_active():
                return

            if not self.movement_controller:
                return

            position = self.movement_controller.get_position()
            if position is None:
                return

            frame_data = self.camera_controller.get_latest_frame()
            if frame_data is None:
                return

            image, header, frame_num = frame_data

            # Detect active channel
            channel_id = self._detect_active_channel()
            if channel_id is None:
                return

            # Add frame to volume
            stage_pos = {'x': position.x, 'y': position.y, 'z': position.z}
            self.add_frame_to_volume(image, stage_pos, channel_id)

        except Exception as e:
            self.logger.debug(f"Populate tick error: {e}")

    def _detect_active_channel(self) -> Optional[int]:
        """Detect which channel is currently active based on laser state."""
        if not self.laser_led_controller:
            return 0

        try:
            laser_states = self.laser_led_controller.get_laser_states()
            for ch_id, is_on in enumerate(laser_states[:4]):
                if is_on:
                    return ch_id
            return None  # No laser on (probably LED)
        except:
            return 0

    def _on_clear_data_clicked(self) -> None:
        """Clear all accumulated 3D data."""
        if not self.voxel_storage:
            self.logger.warning("No voxel storage - cannot clear data")
            return

        from PyQt5.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Clear Data",
            "Are you sure you want to clear all accumulated data?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.voxel_storage.clear()
            self._update_visualization()
            self.logger.info("Cleared all visualization data")

    def add_frame_to_volume(self, image: np.ndarray, stage_position_mm: dict,
                            channel_id: int, timestamp: float = None,
                            reference_position: dict = None) -> None:
        """
        Place a camera frame into the 3D voxel storage.

        All three axes (X, Y, Z) use stage position deltas from the reference
        position consistently. The focal plane is a 2D XY region at fixed
        chamber coordinates - no axis receives special treatment.

        Args:
            image: Camera image
            stage_position_mm: {'x': float, 'y': float, 'z': float} in mm
            channel_id: Channel index (0-3)
            timestamp: Optional timestamp in ms
            reference_position: Optional reference position for delta calculation.
                If provided, uses this instead of voxel_storage.reference_stage_position.
                This allows tile workflows to use deltas without enabling display transform.
        """
        if not self.voxel_storage:
            return

        try:
            import time as time_module
            if timestamp is None:
                timestamp = time_module.time() * 1000

            # Downsample image to storage resolution
            downsampled = self._downsample_for_storage(image)
            H, W = downsampled.shape

            # Generate pixel coordinate grid
            y_indices, x_indices = np.meshgrid(np.arange(H), np.arange(W), indexing='ij')

            # Calculate FOV from magnification
            FOV_mm = 0.5182  # Field of view in mm
            FOV_um = FOV_mm * 1000
            pixel_size_um = FOV_um / W

            # Convert to camera space (micrometers)
            camera_x = (x_indices - W/2) * pixel_size_um
            camera_y = (y_indices - H/2) * pixel_size_um

            # Stack into (N, 2) array
            camera_coords_2d = np.column_stack([camera_x.ravel(), camera_y.ravel()])

            # Get sample region center from config
            sample_center = self._config.get('sample_chamber', {}).get(
                'sample_region_center_um', [6655, 7000, 19250]
            )

            # Get reference position
            pos_x = stage_position_mm['x']
            pos_y = stage_position_mm['y']
            pos_z = stage_position_mm['z']

            # Get reference position for delta calculation
            if reference_position is not None:
                # Use explicitly provided reference (for tile workflows)
                # This avoids setting voxel_storage.reference_stage_position,
                # which would enable display transform (causing double-delta)
                ref_x = reference_position['x']
                ref_y = reference_position['y']
                ref_z = reference_position['z']
            elif self.voxel_storage.reference_stage_position is not None:
                # Use stored reference (for live view)
                ref_x = self.voxel_storage.reference_stage_position['x']
                ref_y = self.voxel_storage.reference_stage_position['y']
                ref_z = self.voxel_storage.reference_stage_position['z']
            else:
                # First frame in live view - set reference in voxel_storage
                self.voxel_storage.set_reference_position(stage_position_mm)
                self.logger.info(f"First frame - set reference position to ({pos_x:.3f}, {pos_y:.3f}, {pos_z:.3f})")
                ref_x = pos_x
                ref_y = pos_y
                ref_z = pos_z

            # Calculate stage delta from reference
            delta_x = pos_x - ref_x
            delta_y = pos_y - ref_y
            delta_z = pos_z - ref_z

            # Log delta values for debugging 3D distribution
            self.logger.debug(f"add_frame_to_volume: pos=({pos_x:.3f}, {pos_y:.3f}, {pos_z:.3f}), "
                             f"ref=({ref_x:.3f}, {ref_y:.3f}, {ref_z:.3f}), "
                             f"delta=({delta_x:.3f}, {delta_y:.3f}, {delta_z:.3f})")

            # Storage position (ZYX order)
            base_z_um = sample_center[2]
            base_y_um = sample_center[1]
            base_x_um = sample_center[0]

            # X-axis storage handling:
            # - When invert_x=True: use -delta_x (storage inverts, display inverts -> correct)
            # - When invert_x=False: use +delta_x (storage normal, display normal -> correct)
            delta_x_storage = -delta_x if self._invert_x else delta_x

            # World coordinates for this frame based on stage position deltas
            # All three axes (X, Y, Z) use deltas consistently - no axis is special.
            # The focal plane is a 2D XY region at fixed X, Y, Z in chamber coordinates.
            # Data position = base_position + delta from reference position.
            #
            # Z sign convention: smaller stage Z = closer to objective = smaller napari Z (back wall at Z=0)
            # So we use +delta_z to maintain the same orientation in napari as physical space.
            world_center_um = np.array([
                base_z_um + delta_z * 1000,         # Z varies with stage movement
                base_y_um + delta_y * 1000,         # Y varies with stage movement
                base_x_um + delta_x_storage * 1000  # X varies with stage movement
            ])

            # Log world center for debugging 3D placement
            self.logger.info(f"Frame placed at world_center_um (Z,Y,X): ({world_center_um[0]:.1f}, {world_center_um[1]:.1f}, {world_center_um[2]:.1f})")

            # Create 3D coords
            slice_thickness_um = 100
            num_pixels = len(camera_coords_2d)
            z_offsets = np.linspace(-slice_thickness_um/2, slice_thickness_um/2, num_pixels)

            camera_offsets_3d = np.column_stack([
                z_offsets,
                camera_coords_2d[:, 1],
                camera_coords_2d[:, 0]
            ])

            world_coords_3d = camera_offsets_3d + world_center_um
            values = downsampled.ravel()

            # Update voxel storage
            self.voxel_storage.update_storage(
                channel_id=channel_id,
                world_coords=world_coords_3d,
                pixel_values=values,
                timestamp=timestamp,
                update_mode='maximum'
            )

            # Trigger channel availability check
            if hasattr(self, '_channel_availability_timer'):
                self._channel_availability_timer.start()

            # Trigger debounced visualization update
            if hasattr(self, '_visualization_update_timer'):
                self._visualization_update_timer.start()

        except Exception as e:
            self.logger.error(f"Error in add_frame_to_volume: {e}", exc_info=True)

    def _downsample_for_storage(self, image: np.ndarray) -> np.ndarray:
        """Downsample camera image to storage resolution."""
        from scipy.ndimage import zoom

        if image.ndim == 3:
            image = image[:, :, 0]

        # Calculate downsample factor (camera ~2000px to storage ~100px)
        target_size = 100
        current_size = max(image.shape)
        factor = target_size / current_size

        if factor < 1:
            return zoom(image, factor, order=1).astype(np.uint16)
        return image.astype(np.uint16)

        # Reset channel controls to disabled
        channels_config = self._config.get('channels', [])
        for ch_id in range(4):
            cb = self.channel_checkboxes.get(ch_id)
            sl = self.channel_contrast_sliders.get(ch_id)
            ml = self.channel_min_labels.get(ch_id)
            xl = self.channel_max_labels.get(ch_id)
            if cb:
                cb.setEnabled(False)
                cb.setChecked(False)
                name = channels_config[ch_id].get('name', f'Ch {ch_id}') if ch_id < len(channels_config) else f'Ch {ch_id}'
                cb.setToolTip(
                    f"{name} channel — No data loaded.\n"
                    "This channel will activate automatically when 3D volume data is received."
                )
            if sl:
                sl.setEnabled(False)
            if ml:
                ml.setEnabled(False)
            if xl:
                xl.setEnabled(False)

    # ========== 3D Viewer Integration ==========

    @property
    def holder_position(self):
        """Holder position in napari voxel coordinates (delegated to manager)."""
        return self._chamber_viz.holder_position

    @holder_position.setter
    def holder_position(self, value):
        self._chamber_viz.holder_position = value

    @property
    def current_rotation(self):
        """Current rotation state (delegated to manager)."""
        return self._chamber_viz.current_rotation

    @current_rotation.setter
    def current_rotation(self, value):
        self._chamber_viz.current_rotation = value

    @property
    def objective_xy_calibration(self):
        """Objective XY calibration (delegated to manager)."""
        return self._chamber_viz.objective_xy_calibration

    @objective_xy_calibration.setter
    def objective_xy_calibration(self, value):
        self._chamber_viz.objective_xy_calibration = value

    def _embed_3d_viewer(self) -> None:
        """Create and embed the napari 3D viewer (delegated to manager)."""
        self._chamber_viz._position_sliders = getattr(self, 'position_sliders', None)
        placeholder = getattr(self, 'viewer_placeholder', None)
        self._chamber_viz.embed_viewer(placeholder)
        self.viewer = self._chamber_viz.viewer
        self.channel_layers = self._chamber_viz.channel_layers
        if placeholder:
            self.viewer_placeholder = None

    def _update_sample_holder_position(self, x_mm: float, y_mm: float, z_mm: float):
        """Update sample holder position (delegated to manager)."""
        self._chamber_viz.update_stage_geometry(x_mm, y_mm, z_mm)

    def _update_xy_focus_frame(self):
        """Update XY focus frame (delegated to manager)."""
        self._chamber_viz.update_focus_frame()

    def _process_pending_stage_update(self):
        """Process pending stage position update for 3D visualization.

        Called by the throttle timer (50ms interval / 20 FPS max) to avoid
        overwhelming the GUI with rapid position updates.
        """
        if self._pending_stage_update is None:
            self._stage_update_timer.stop()
            return

        # Pop pending position
        stage_pos = self._pending_stage_update
        self._pending_stage_update = None

        self.logger.info(f"Stage update received: X={stage_pos['x']:.3f}, Y={stage_pos['y']:.3f}, "
                        f"Z={stage_pos['z']:.3f}, R={stage_pos['r']:.1f}")

        # During tile workflows, don't update last_stage_position from hardware -
        # _on_tile_zstack_frame() manages it based on workflow positions.
        # Also skip data layer updates since _update_visualization() handles that.
        if getattr(self, '_tile_workflow_active', False):
            # Still update geometry (holder position) but not data transforms
            self.logger.info("  Skipping data transform (tile workflow active)")
            self.current_rotation['ry'] = stage_pos.get('r', 0)
            self._update_sample_holder_position(
                stage_pos['x'], stage_pos['y'], stage_pos['z']
            )
            return

        # Store last stage position (only when NOT in tile workflow)
        self.last_stage_position = stage_pos

        # Update rotation tracking
        self.current_rotation['ry'] = stage_pos.get('r', 0)

        # Update reference geometry (holder, extension, rotation indicator)
        self._update_sample_holder_position(
            stage_pos['x'], stage_pos['y'], stage_pos['z']
        )

        # Update data layers with transformed volumes (data moves with stage)
        if not self.voxel_storage:
            self.logger.info("  Skipping data transform (no voxel_storage)")
            return

        # Check if reference position is set (required for transform)
        if self.voxel_storage.reference_stage_position is None:
            self.logger.info("  Skipping data transform (reference_stage_position not set)")
            return

        # Get holder position for rotation center
        holder_pos_voxels = np.array([
            self.holder_position['x'],
            self.holder_position['y'],
            self.holder_position['z']
        ])

        channels_updated = 0
        for ch_id in range(self.voxel_storage.num_channels):
            if not self.voxel_storage.has_data(ch_id):
                continue

            volume = self.voxel_storage.get_display_volume_transformed(
                ch_id, stage_pos, holder_pos_voxels
            )

            if ch_id in self.channel_layers:
                self.channel_layers[ch_id].data = volume
                channels_updated += 1

                self.logger.debug(f"Stage update: Channel {ch_id} - "
                                 f"non-zero voxels: {np.count_nonzero(volume)}")

        if channels_updated > 0:
            self.logger.info(f"  Updated {channels_updated} channel(s) with transformed data")
            # Also update 2D plane views with the same transformed data
            self._update_plane_views()

    def _update_visualization(self) -> None:
        """Update the 3D visualization with latest data from voxel storage."""
        if not self.viewer or not self.voxel_storage:
            return

        try:
            for ch_id in range(self.voxel_storage.num_channels):
                if ch_id in self.channel_layers:
                    if not self.voxel_storage.has_data(ch_id):
                        # Clear the layer if storage has no data (e.g., after Clear Data)
                        layer = self.channel_layers[ch_id]
                        if layer.data is not None and np.any(layer.data):
                            layer.data = np.zeros_like(layer.data)
                            self.logger.info(f"Cleared display for channel {ch_id}")
                        continue

                    # Use transformed volume if stage has moved from origin
                    if self.last_stage_position and any(
                        v != 0 for v in self.last_stage_position.values()
                    ):
                        holder_pos = np.array([
                            self.holder_position['x'],
                            self.holder_position['y'],
                            self.holder_position['z']
                        ])
                        volume = self.voxel_storage.get_display_volume_transformed(
                            ch_id, self.last_stage_position, holder_pos
                        )
                    else:
                        volume = self.voxel_storage.get_display_volume(ch_id)

                    # Diagnostic logging to help debug data display issues
                    self.logger.info(
                        f"Channel {ch_id}: volume shape={volume.shape}, "
                        f"non-zero={np.count_nonzero(volume)}, "
                        f"max={volume.max()}"
                    )

                    self.channel_layers[ch_id].data = volume

                    # Auto-contrast if this is first data for channel
                    layer = self.channel_layers[ch_id]
                    if not getattr(layer, '_auto_contrast_applied', False):
                        self._auto_contrast_channels()
                        layer._auto_contrast_applied = True

            # Update 2D plane views with MIP projections
            self._update_plane_views()

        except Exception as e:
            self.logger.error(f"Error updating visualization: {e}", exc_info=True)

    def _reset_viewer_camera(self) -> None:
        """Reset the napari viewer camera zoom (delegated to manager)."""
        self._chamber_viz.reset_camera()

    # ========== Live View Control ==========

    def _on_live_view_toggle(self) -> None:
        """Toggle live view on/off."""
        if not self.camera_controller:
            self.logger.warning("No camera controller available")
            return

        try:
            if self._live_view_active:
                # Stop live view
                self.camera_controller.stop_live_view()
                self._live_view_active = False
                self.live_view_toggle_btn.setChecked(False)
                self.live_view_toggle_btn.setText("Start Live")
                self.live_view_toggle_btn.setStyleSheet(
                    f"QPushButton {{ background-color: {SUCCESS_COLOR}; color: white; "
                    f"font-weight: bold; padding: 6px 12px; font-size: 10pt; }}"
                )
                self.live_status_label.setText("Status: Idle")
                self.logger.info("Live view stopped")
            else:
                # Re-enable the selected light source before starting camera
                # (it was disabled when live view stopped previously)
                if hasattr(self, 'laser_led_panel') and self.laser_led_panel:
                    self.laser_led_panel.restore_checked_illumination()

                # Start live view
                self.camera_controller.start_live_view()
                self._live_view_active = True
                self.live_view_toggle_btn.setChecked(True)
                self.live_view_toggle_btn.setText("Stop Live")
                self.live_view_toggle_btn.setStyleSheet(
                    f"QPushButton {{ background-color: {ERROR_COLOR}; color: white; "
                    f"font-weight: bold; padding: 6px 12px; font-size: 10pt; }}"
                )
                self.live_status_label.setText("Status: Streaming")
                self.logger.info("Live view started")
        except Exception as e:
            self.logger.error(f"Error toggling live view: {e}")

    def _update_live_view_state(self) -> None:
        """Update the live view button state based on camera controller state."""
        if not self.camera_controller:
            return

        try:
            is_live = self.camera_controller.state == CameraState.LIVE_VIEW
            self._live_view_active = is_live

            if is_live:
                self.live_view_toggle_btn.setChecked(True)
                self.live_view_toggle_btn.setText("Stop Live")
                self.live_view_toggle_btn.setStyleSheet(
                    f"QPushButton {{ background-color: {ERROR_COLOR}; color: white; "
                    f"font-weight: bold; padding: 6px 12px; font-size: 10pt; }}"
                )
                self.live_status_label.setText("Status: Streaming")
            else:
                self.live_view_toggle_btn.setChecked(False)
                self.live_view_toggle_btn.setText("Start Live")
                self.live_view_toggle_btn.setStyleSheet(
                    f"QPushButton {{ background-color: {SUCCESS_COLOR}; color: white; "
                    f"font-weight: bold; padding: 6px 12px; font-size: 10pt; }}"
                )
                self.live_status_label.setText("Status: Idle")
        except Exception as e:
            self.logger.error(f"Error updating live view state: {e}")

    def _update_zoom_display(self) -> None:
        """Update the zoom level display from napari viewer."""
        viewer = self._get_viewer()
        if viewer and hasattr(viewer, 'camera'):
            zoom = viewer.camera.zoom
            self.zoom_label.setText(f"Zoom: {zoom:.2f}")
        else:
            self.zoom_label.setText("Zoom: --")

    def _on_reset_zoom_clicked(self) -> None:
        """Reset camera view to defaults (orientation and zoom)."""
        viewer = self._get_viewer()
        if viewer and hasattr(viewer, 'camera'):
            viewer.reset_view()  # Reset orientation to napari defaults
            viewer.camera.zoom = 1.57  # Set zoom after reset
            self._update_zoom_display()
            self.logger.info("Reset camera view to defaults (orientation + zoom=1.57)")

    def _update_info_displays(self) -> None:
        """Periodically update zoom, FPS, and data stats displays."""
        # Update zoom
        self._update_zoom_display()

        # Update data stats (memory/voxels)
        self._update_data_stats()

        # Update FPS from camera controller if live
        if self._live_view_active and self.camera_controller:
            fps = getattr(self.camera_controller, '_current_fps', None)
            if fps is not None:
                self.fps_label.setText(f"FPS: {fps:.1f}")
            else:
                self.fps_label.setText("FPS: --")
        elif not self._live_view_active:
            self.fps_label.setText("FPS: --")

    def _update_data_stats(self) -> None:
        """Update memory and voxel count labels from voxel storage."""
        if not self.voxel_storage:
            return

        try:
            stats = self.voxel_storage.get_memory_usage()
            self.memory_label.setText(f"Memory: {stats['total_mb']:.1f} MB")
            voxels = stats['storage_voxels']
            if voxels >= 1_000_000:
                self.voxel_label.setText(f"Voxels: {voxels/1_000_000:.1f}M")
            elif voxels >= 1_000:
                self.voxel_label.setText(f"Voxels: {voxels/1_000:.1f}K")
            else:
                self.voxel_label.setText(f"Voxels: {voxels:,}")
        except Exception as e:
            self.logger.debug(f"Error updating data stats: {e}")

    def _on_transform_quality_changed(self, fast_mode: bool) -> None:
        """Handle Fast Transform checkbox toggle."""
        try:
            from py2flamingo.visualization.coordinate_transforms import TransformQuality
            quality = TransformQuality.FAST if fast_mode else TransformQuality.QUALITY
            if self.voxel_storage:
                self.voxel_storage.transform_quality = quality
                # Trigger visualization update
                self._update_visualization()
            self.logger.info(f"Transform quality changed to: {quality.name}")
        except Exception as e:
            self.logger.error(f"Error changing transform quality: {e}")

    def _on_live_settings_clicked(self) -> None:
        """Open Live Display (image controls) window for advanced settings."""
        if self.image_controls_window:
            self.image_controls_window.show()
            self.image_controls_window.raise_()
        else:
            self.logger.info("Live View Settings clicked (window not available)")

    def _on_plane_click(self, plane: str, h_coord: float, v_coord: float) -> None:
        """Handle click-to-move from plane viewers.

        Uses move_to_position for multi-axis moves to avoid movement lock conflicts.
        """
        if not self.movement_controller:
            return

        try:
            # Get current position to preserve unchanged axes
            current_pos = self.movement_controller.get_position()
            if not current_pos:
                self.logger.warning("Cannot get current position for plane click move")
                return

            # Import Position class
            from py2flamingo.models.hardware.stage import Position

            # Map plane coordinates to target position
            if plane == 'xz':
                # XZ plane: h=X, v=Z, keep Y and R
                target = Position(x=h_coord, y=current_pos.y, z=v_coord, r=current_pos.r)
                self.logger.info(f"Moving to X={h_coord:.3f}, Z={v_coord:.3f} (Y={current_pos.y:.3f} unchanged)")
            elif plane == 'xy':
                # XY plane: h=X, v=Y (viz coords), keep Z and R
                stage_y = self._viz_y_to_stage_y(v_coord)
                target = Position(x=h_coord, y=stage_y, z=current_pos.z, r=current_pos.r)
                self.logger.info(f"Moving to X={h_coord:.3f}, Y={stage_y:.3f} (Z={current_pos.z:.3f} unchanged)")
            elif plane == 'yz':
                # YZ plane: h=Z, v=Y (viz coords), keep X and R
                stage_y = self._viz_y_to_stage_y(v_coord)
                target = Position(x=current_pos.x, y=stage_y, z=h_coord, r=current_pos.r)
                self.logger.info(f"Moving to Z={h_coord:.3f}, Y={stage_y:.3f} (X={current_pos.x:.3f} unchanged)")
            else:
                self.logger.warning(f"Unknown plane: {plane}")
                return

            # Use move_to_position for multi-axis move (handles lock properly)
            self.movement_controller.position_controller.move_to_position(target, validate=True)

        except Exception as e:
            self.logger.error(f"Error moving from plane click: {e}")

    def _update_plane_views(self) -> None:
        """Update the MIP (Maximum Intensity Projection) plane views from voxel data.

        Supports multi-channel display with colormaps from Viewer Controls settings.
        Uses the transformed data from napari layers (if available) so that 2D planes
        show the same data position as the 3D viewer when the stage moves.
        """
        if not self.voxel_storage:
            return

        try:
            # Collect MIP data and settings for each channel
            xz_channel_mips: Dict[int, np.ndarray] = {}
            xy_channel_mips: Dict[int, np.ndarray] = {}
            yz_channel_mips: Dict[int, np.ndarray] = {}
            channel_settings: Dict[int, dict] = {}

            # Get channel settings from napari layers (if available)
            viewer = self._get_viewer()

            for ch_id in range(4):
                # Check if channel has data
                if not self.voxel_storage.has_data(ch_id):
                    continue

                # Prefer using napari layer data (already transformed with stage position)
                # over raw voxel storage data, so 2D planes match 3D viewer position
                if ch_id in self.channel_layers and self.channel_layers[ch_id].data is not None:
                    volume = self.channel_layers[ch_id].data
                else:
                    # Fallback to raw storage data
                    volume = self.voxel_storage.get_display_volume(ch_id)

                if volume is None or volume.size == 0:
                    continue

                # Data is in (Z, Y, X) order - generate MIP projections
                # napari volume has Y inverted (row 0 = Y_max = chamber bottom)
                # flipud on Y-containing planes so row 0 = Y_min (chamber top)

                # XZ plane (top-down) - project along Y axis (axis 1)
                # Result shape: (Z, X) where Z=rows(vertical), X=cols(horizontal)
                xz_channel_mips[ch_id] = np.max(volume, axis=1)

                # XY plane (front view) - project along Z axis (axis 0)
                # Result shape: (Y, X) - flipud so row 0 = Y_min (chamber top)
                xy_channel_mips[ch_id] = np.flipud(np.max(volume, axis=0))

                # YZ plane (side view) - project along X axis (axis 2)
                # Result shape: (Z, Y) -> transpose to (Y, Z) - flipud so row 0 = Y_min
                yz_channel_mips[ch_id] = np.flipud(np.max(volume, axis=2).T)

                # Get channel settings from napari layer or use defaults
                settings = {
                    'visible': True,
                    'colormap': 'gray',
                    'contrast_min': 0,
                    'contrast_max': 65535
                }

                # Try to get settings from napari layer
                if ch_id in self.channel_layers:
                    layer = self.channel_layers[ch_id]
                    if hasattr(layer, 'visible'):
                        settings['visible'] = layer.visible
                    if hasattr(layer, 'colormap') and hasattr(layer.colormap, 'name'):
                        settings['colormap'] = layer.colormap.name
                    if hasattr(layer, 'contrast_limits'):
                        limits = layer.contrast_limits
                        settings['contrast_min'] = int(limits[0])
                        settings['contrast_max'] = int(limits[1])

                channel_settings[ch_id] = settings

            # Update plane viewers with multi-channel data
            if xz_channel_mips:
                self.xz_plane_viewer.set_multi_channel_mip(xz_channel_mips, channel_settings)
            if xy_channel_mips:
                self.xy_plane_viewer.set_multi_channel_mip(xy_channel_mips, channel_settings)
            if yz_channel_mips:
                self.yz_plane_viewer.set_multi_channel_mip(yz_channel_mips, channel_settings)

        except Exception as e:
            self.logger.error(f"Error updating plane views: {e}")

    def _stage_y_to_viz_y(self, stage_y: float) -> float:
        """Convert stage Y coordinate to visualization/chamber Y coordinate."""
        return stage_y - self._chamber_viz.STAGE_Y_AT_OBJECTIVE + self._chamber_viz.OBJECTIVE_CHAMBER_Y_MM

    def _viz_y_to_stage_y(self, viz_y: float) -> float:
        """Convert visualization/chamber Y coordinate to stage Y coordinate."""
        return viz_y + self._chamber_viz.STAGE_Y_AT_OBJECTIVE - self._chamber_viz.OBJECTIVE_CHAMBER_Y_MM

    def _update_plane_overlays(self) -> None:
        """Update overlay positions on all plane viewers."""
        if not self.viewer:
            return

        try:
            # Get current stage position from movement controller
            if self.movement_controller:
                pos = self.movement_controller.get_position()
                if pos:
                    x, y, z = pos.x, pos.y, pos.z
                    viz_y = self._stage_y_to_viz_y(y)

                    # Update holder position on each plane
                    self.xz_plane_viewer.set_holder_position(x, z)
                    self.xy_plane_viewer.set_holder_position(x, viz_y)
                    self.yz_plane_viewer.set_holder_position(z, viz_y)

            # Get objective focal point position from calibration
            cal = self.objective_xy_calibration
            if cal:
                obj_x, obj_y, obj_z = cal['x'], cal['y'], cal['z']
            else:
                # Fallback to defaults
                stage_config = self._config.get('stage_control', {})
                obj_x = (stage_config.get('x_range_mm', [1, 12.31])[0] +
                         stage_config.get('x_range_mm', [1, 12.31])[1]) / 2
                obj_y = self._chamber_viz.STAGE_Y_AT_OBJECTIVE  # 7.45mm
                obj_z = stage_config.get('z_range_mm', [12.5, 26])[0]

            # Convert objective Y from stage coords to viz coords
            obj_y_viz = self._stage_y_to_viz_y(obj_y)

            # Update objective circle (focal point) on each plane
            self.xz_plane_viewer.set_objective_position(obj_x, obj_z)
            self.xy_plane_viewer.set_objective_position(obj_x, obj_y_viz)
            self.yz_plane_viewer.set_objective_position(obj_z, obj_y_viz)

            # Update focal plane lines (cyan dashed) through objective position
            self.xz_plane_viewer.set_focal_plane_position(obj_z)   # horizontal at obj Z
            self.xy_plane_viewer.set_focal_plane_position(obj_y_viz)  # horizontal at obj Y
            self.yz_plane_viewer.set_focal_plane_position(obj_z)   # vertical at obj Z

        except Exception as e:
            self.logger.error(f"Error updating plane overlays: {e}")

    def _check_and_mark_targets_stale(self, x: float, y: float, z: float) -> None:
        """Check if stage has reached target positions and mark targets as stale.

        Args:
            x: Current X position in mm
            y: Current Y position in mm (stage coords)
            z: Current Z position in mm
        """
        threshold = 0.05  # 50 microns tolerance
        viz_y = self._stage_y_to_viz_y(y)

        # Check XZ plane target
        target = self.xz_plane_viewer._target_pos
        if target and self.xz_plane_viewer._target_active:
            target_x, target_z = target
            if abs(x - target_x) < threshold and abs(z - target_z) < threshold:
                self.xz_plane_viewer.set_target_stale()

        # Check XY plane target (target Y is in viz coords)
        target = self.xy_plane_viewer._target_pos
        if target and self.xy_plane_viewer._target_active:
            target_x, target_y = target
            if abs(x - target_x) < threshold and abs(viz_y - target_y) < threshold:
                self.xy_plane_viewer.set_target_stale()

        # Check YZ plane target (note: h=Z, v=Y in YZ plane, target Y is in viz coords)
        target = self.yz_plane_viewer._target_pos
        if target and self.yz_plane_viewer._target_active:
            target_z, target_y = target
            if abs(z - target_z) < threshold and abs(viz_y - target_y) < threshold:
                self.yz_plane_viewer.set_target_stale()

    # ========== Public Methods ==========

    def update_workflow_progress(self, status: str, progress: int, time_remaining: str) -> None:
        """
        Update workflow progress display.

        Args:
            status: Status text (e.g., "Running Step 3 of 10")
            progress: Progress percentage (0-100)
            time_remaining: Time remaining string (e.g., "02:30")
        """
        self.workflow_status_label.setText(f"Workflow: {status}")
        self.workflow_progress_bar.setValue(progress)
        self.time_remaining_label.setText(time_remaining)

    # ========== Window Events ==========

    def showEvent(self, event: QShowEvent) -> None:
        """Handle window show event - restore geometry and dialog state on first show."""
        super().showEvent(event)

        # Restore geometry on first show
        if not self._geometry_restored and self._geometry_manager:
            self._geometry_manager.restore_geometry("SampleView", self)
            self._geometry_restored = True
            self.logger.info("Restored SampleView geometry")

        # Restore dialog state on first show
        if not self._dialog_state_restored and self._geometry_manager:
            self._restore_dialog_state()
            self._dialog_state_restored = True

        # Load laser powers from hardware (every time window is shown)
        if self.laser_led_controller:
            self.logger.info("Loading laser powers from hardware...")
            self.laser_led_controller.load_laser_powers_from_hardware()

    def hideEvent(self, event: QHideEvent) -> None:
        """Handle window hide event - save geometry and dialog state when hidden."""
        # Save geometry and dialog state when hiding
        if self._geometry_manager:
            self._geometry_manager.save_geometry("SampleView", self)
            self._save_dialog_state()
            self._geometry_manager.save_all()
            self.logger.debug("Saved SampleView geometry and dialog state on hide")

        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Handle window close event - save geometry and dialog state."""
        # Save geometry and dialog state
        if self._geometry_manager:
            self._geometry_manager.save_geometry("SampleView", self)
            self._save_dialog_state()
            self._geometry_manager.save_all()
            self.logger.info("Saved SampleView geometry and dialog state")

        event.accept()

    # ========== Dialog State Persistence ==========

    def _save_dialog_state(self) -> None:
        """Save dialog state (display settings and illumination selections) for persistence.

        Saves:
        - Display settings: colormap, auto-scale, intensity min/max
        - Illumination selections: laser/LED checkboxes, LED color, LED intensity, light path

        Does NOT save (these are reset or loaded from hardware):
        - Stage positions (loaded from current hardware state)
        - Laser power values (loaded from hardware)
        - "Populate from live" checkbox (always starts unchecked)
        - 3D view camera position (always resets)
        """
        if not self._geometry_manager:
            return

        state = {}

        # Display settings
        state["colormap"] = self.colormap_combo.currentText()
        state["auto_scale"] = self.auto_scale_checkbox.isChecked()
        state["intensity_min"] = self.min_intensity_spinbox.value()
        state["intensity_max"] = self.max_intensity_spinbox.value()

        # Illumination selections from the laser/LED panel
        if hasattr(self, 'laser_led_panel') and self.laser_led_panel:
            state["illumination"] = self.laser_led_panel.get_illumination_selection_state()

        self._geometry_manager.save_dialog_state("SampleView", state)
        self.logger.debug(f"Saved dialog state: colormap={state['colormap']}, "
                         f"auto_scale={state['auto_scale']}, "
                         f"intensity={state['intensity_min']}-{state['intensity_max']}")

    def _restore_dialog_state(self) -> None:
        """Restore dialog state (display settings and illumination selections) from persistence.

        Restores:
        - Display settings: colormap, auto-scale, intensity min/max
        - Illumination selections: laser/LED checkboxes, LED color, LED intensity, light path

        Does NOT restore (intentionally):
        - Stage positions (current hardware state is used)
        - Laser power values (loaded from hardware separately)
        - "Populate from live" checkbox (always starts unchecked)
        - 3D view camera position (always starts in reset position)
        """
        if not self._geometry_manager:
            return

        state = self._geometry_manager.restore_dialog_state("SampleView")
        if not state:
            self.logger.debug("No saved dialog state to restore")
            return

        # Restore display settings (block signals to prevent side effects)
        if "colormap" in state:
            self.colormap_combo.blockSignals(True)
            self.colormap_combo.setCurrentText(state["colormap"])
            self._colormap = state["colormap"]
            self.colormap_combo.blockSignals(False)

        if "auto_scale" in state:
            self.auto_scale_checkbox.blockSignals(True)
            self.auto_scale_checkbox.setChecked(state["auto_scale"])
            self._auto_scale = state["auto_scale"]
            self.auto_scale_checkbox.blockSignals(False)

            # Enable/disable intensity controls based on auto-scale
            manual_enabled = not state["auto_scale"]
            self.min_intensity_spinbox.setEnabled(manual_enabled)
            self.max_intensity_spinbox.setEnabled(manual_enabled)
            self.range_slider.setEnabled(manual_enabled)

        if "intensity_min" in state and "intensity_max" in state:
            self.min_intensity_spinbox.blockSignals(True)
            self.max_intensity_spinbox.blockSignals(True)
            self.range_slider.blockSignals(True)

            self._intensity_min = state["intensity_min"]
            self._intensity_max = state["intensity_max"]
            self.min_intensity_spinbox.setValue(state["intensity_min"])
            self.max_intensity_spinbox.setValue(state["intensity_max"])
            self.range_slider.setValue((state["intensity_min"], state["intensity_max"]))

            self.min_intensity_spinbox.blockSignals(False)
            self.max_intensity_spinbox.blockSignals(False)
            self.range_slider.blockSignals(False)

        # Restore illumination selections
        if "illumination" in state and hasattr(self, 'laser_led_panel') and self.laser_led_panel:
            self.laser_led_panel.restore_illumination_selection_state(state["illumination"])

        self.logger.info(f"Restored dialog state: colormap={state.get('colormap')}, "
                        f"auto_scale={state.get('auto_scale')}, "
                        f"intensity={state.get('intensity_min')}-{state.get('intensity_max')}")

    # ========== Acquisition Lock Controls ==========

    def set_stage_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable stage movement controls.

        Called during acquisition processes (e.g., LED 2D Overview scan) to prevent
        accidental stage movements that could interfere with the acquisition.

        This only affects stage position controls - visualization and display
        controls remain enabled.

        Args:
            enabled: True to enable controls, False to disable
        """
        self.logger.info(f"Stage controls {'enabled' if enabled else 'disabled'} (acquisition lock)")

        # Disable/enable position sliders
        if hasattr(self, 'position_sliders'):
            for slider in self.position_sliders.values():
                slider.setEnabled(enabled)

        # Disable/enable position edit fields
        if hasattr(self, 'position_edits'):
            for edit in self.position_edits.values():
                edit.setEnabled(enabled)

        # Disable/enable illumination controls during acquisition
        # (acquisition controls the LED, user shouldn't change it)
        if hasattr(self, 'laser_led_panel') and self.laser_led_panel:
            self.laser_led_panel.setEnabled(enabled)

    # ========== Tile Workflow Integration ==========

    def prepare_for_tile_workflows(self, tile_info: list):
        """Prepare Sample View to receive tile workflow Z-stacks.

        Args:
            tile_info: List of dicts with tile positions and Z-ranges
                      Each dict has keys: x, y, z_min, z_max, filename
        """
        self._tile_workflow_active = True
        self._expected_tiles = tile_info
        self._accumulated_zstacks = {}
        self._tile_reference_set = False  # Set reference on first tile frame
        self._tile_reference_position = None  # Local reference (not in voxel_storage to avoid transform)
        self._learned_frames_per_tile = None  # Learn from first tile for channel detection

        # Disable Populate from Live during tile workflows to prevent interference
        if getattr(self, '_is_populating', False):
            self._was_populating_before_workflow = True
            self.populate_btn.setChecked(False)  # This triggers _on_populate_toggled
            self.logger.info("Disabled Populate from Live for tile workflow")
        else:
            self._was_populating_before_workflow = False

        # Disable the populate button during tile workflows
        if hasattr(self, 'populate_btn'):
            self.populate_btn.setEnabled(False)
            self.populate_btn.setToolTip("Disabled during tile workflow")

        # Cache camera FPS for channel detection
        self._tile_camera_fps = 40.0  # Default
        if self.camera_controller and self.camera_controller.camera_service:
            try:
                fps = getattr(self.camera_controller, '_max_display_fps', 40.0)
                if fps and fps > 0:
                    self._tile_camera_fps = fps
                self.logger.info(f"Sample View: Camera FPS for channel detection: {self._tile_camera_fps}")
            except Exception:
                pass

        self.logger.info(f"Sample View: Prepared to receive {len(tile_info)} tile workflows")

    def finish_tile_workflows(self):
        """Mark tile workflows as complete and restore UI state.

        Call this when all tile workflows have finished to:
        - Re-enable Populate from Live button
        - Allow other 3D Volume View interactions
        - Trigger final visualization update to show last tile
        """
        self._tile_workflow_active = False
        self.logger.info(f"Sample View: Tile workflows finished. "
                        f"Processed {len(self._accumulated_zstacks)} tiles.")

        # Trigger visualization update to show the final tile(s)
        # During acquisition, visualization only updates when a NEW tile starts,
        # so we need this final update to show the last tile.
        self._visualization_update_timer.start()

        # Re-enable the populate button
        if hasattr(self, 'populate_btn'):
            self.populate_btn.setEnabled(True)
            self.populate_btn.setToolTip("Capture frames from Live View and accumulate into 3D volume")

        # Optionally restore populate state if it was active before
        # (commented out - user should manually re-enable if desired)
        # if getattr(self, '_was_populating_before_workflow', False):
        #     self.populate_btn.setChecked(True)

    def _on_tile_zstack_frame(self, image: np.ndarray, position: dict,
                              z_index: int, frame_num: int):
        """Handle incoming Z-stack frame from tile workflow.

        Args:
            image: Frame data (H, W) uint16 array
            position: Tile position dict with x, y, z_min, z_max, filename
            z_index: Z-plane index (0-based)
            frame_num: Global frame number
        """
        if not self._tile_workflow_active:
            return

        # Calculate actual Z position from index
        z_min = position['z_min']
        z_max = position['z_max']
        z_range = z_max - z_min

        # Determine laser channel from z_index and channel list
        channels = position.get('channels', [0])
        num_channels = len(channels)

        # Track frames per tile to determine channel boundaries dynamically.
        # The firmware acquires channels sequentially, so we need to detect
        # when we've passed the midpoint of the total frames.
        tile_key = (position['x'], position['y'])
        is_new_tile = tile_key not in self._accumulated_zstacks
        if is_new_tile:
            self._accumulated_zstacks[tile_key] = 0

            # CRITICAL: Force position update at start of each new tile
            # This ensures the display transform is current before new data arrives,
            # so existing data shifts correctly and new data is stored with proper deltas.
            if self.movement_controller:
                try:
                    pos = self.movement_controller.get_position()
                    if pos:
                        self.logger.info(f"New tile starting - forcing position update: "
                                        f"X={pos.x:.3f}, Y={pos.y:.3f}, Z={pos.z:.3f}")
                        self._on_position_changed(pos.x, pos.y, pos.z, pos.r)
                except Exception as e:
                    self.logger.warning(f"Could not force position update for new tile: {e}")

        frame_count = self._accumulated_zstacks[tile_key]

        # Calculate frames_per_channel for proper Z distribution
        # For first tile: estimate from z_velocity and z_range (use for ENTIRE first tile)
        # For subsequent tiles: use learned value from completed first tile
        is_first_tile = len(self._accumulated_zstacks) == 1
        frames_per_tile = getattr(self, '_learned_frames_per_tile', None)

        if is_first_tile or frames_per_tile is None:
            # First tile (or no learned value yet): calculate from z_velocity and z_range
            # IMPORTANT: Use this calculation for the ENTIRE first tile, even after
            # _learned_frames_per_tile starts being updated, to ensure consistent
            # Z distribution throughout the tile.
            z_velocity = position.get('z_velocity', 0)
            camera_fps = getattr(self, '_tile_camera_fps', 40.0)
            if z_velocity > 0 and z_range > 0:
                sweep_duration = z_range / z_velocity  # seconds
                expected_frames = int(sweep_duration * camera_fps)
                frames_per_channel = max(1, expected_frames // max(1, num_channels))
                if frame_count == 0:  # Only log once per tile
                    self.logger.info(f"First tile: estimated {expected_frames} frames "
                                    f"({frames_per_channel}/channel) from z_vel={z_velocity:.3f}, "
                                    f"z_range={z_range:.3f}, fps={camera_fps}")
            else:
                # Fallback if z_velocity not available
                frames_per_channel = 100  # Conservative high value to avoid wrap
                if frame_count == 0:
                    self.logger.warning(f"First tile: no z_velocity, using fallback frames_per_channel={frames_per_channel}")
        else:
            # Subsequent tiles: use learned value from completed first tile
            frames_per_channel = max(1, frames_per_tile // max(1, num_channels))

        # Which channel does this z_index belong to?
        # Channels are acquired sequentially, so divide frame count by frames_per_channel
        channel_idx = min(z_index // frames_per_channel, num_channels - 1)
        self._current_channel = channels[channel_idx]

        # For tile workflows, ALWAYS calculate Z from z_index
        # Hardware position doesn't update mid-Z-sweep, so querying returns
        # the same Z for all frames. Use the calculated position instead.
        z_within_channel = z_index % max(1, frames_per_channel)
        z_fraction = z_within_channel / max(1, frames_per_channel - 1) if frames_per_channel > 1 else 0.5
        z_position = z_min + z_fraction * z_range

        # Increment frame count (tracking was initialized above for channel detection)
        self._accumulated_zstacks[tile_key] += 1
        frame_count = self._accumulated_zstacks[tile_key]

        # Learn the actual frames per tile from the first tile when it completes
        # This improves channel routing for subsequent tiles
        if len(self._accumulated_zstacks) == 1 and frame_count > 5:
            # Update estimate as we go - will settle on final value
            self._learned_frames_per_tile = frame_count

        # Update workflow progress directly (PyQt signals are starved during frame processing)
        total_expected = num_channels * frames_per_channel
        total_tiles = max(1, len(self._expected_tiles))
        tile_idx = len(self._accumulated_zstacks)  # Current tile number
        if total_expected > 0 and frame_count % 5 == 0:
            tile_pct = min(1.0, frame_count / total_expected)
            overall_pct = min(100, int(((tile_idx - 1 + tile_pct) / total_tiles) * 100))
            ch_name = channels[channel_idx] if channel_idx < len(channels) else '?'
            status = f"Tile {tile_idx}/{total_tiles}: {frame_count} frames (Ch {ch_name})"
            self.update_workflow_progress(status, overall_pct, "--:--")

        # Set reference on first frame of acquisition
        # Query actual stage position synchronously to avoid timing issues
        if not self._tile_reference_set and self.voxel_storage:
            # Query actual position from hardware (synchronous call)
            actual_pos = None
            if self.movement_controller:
                try:
                    actual_pos = self.movement_controller.get_position()
                    if actual_pos is None:
                        self.logger.warning("Sample View: movement_controller.get_position() returned None")
                except Exception as e:
                    self.logger.warning(f"Sample View: Failed to query stage position: {e}")
            else:
                self.logger.warning("Sample View: No movement_controller available for position query")

            # Log position comparison for debugging
            self.logger.info(f"Sample View: Position comparison on first frame:")
            self.logger.info(f"  Workflow target: X={position['x']:.3f}, Y={position['y']:.3f}, Z={z_position:.3f}")
            if actual_pos:
                self.logger.info(f"  Queried actual:  X={actual_pos.x:.3f}, Y={actual_pos.y:.3f}, "
                                f"Z={actual_pos.z:.3f}, R={actual_pos.r:.1f}°")
            else:
                self.logger.info(f"  Cached stage:    X={self.last_stage_position.get('x', 0):.3f}, "
                                f"Y={self.last_stage_position.get('y', 0):.3f}, "
                                f"Z={self.last_stage_position.get('z', 0):.3f}, "
                                f"R={self.last_stage_position.get('r', 0):.1f}°")

            # CRITICAL: Use WORKFLOW position for reference, not actual stage position.
            # Data position also comes from workflow position, so using the same source
            # ensures delta = 0 for the first tile (data appears at base position).
            # If we used actual stage position for reference but workflow position for data,
            # any difference would cause an offset in the first tile.
            ref_x = position['x']
            ref_y = position['y']
            ref_z = z_position
            ref_r = position.get('r', self.last_stage_position.get('r', 0))
            self.logger.info(f"Sample View: Using WORKFLOW position for reference "
                            f"(ensures delta=0 for first tile)")

            # Store reference both locally (for storage delta) and in voxel_storage (for display transform).
            # The storage delta places each tile at its relative position: base + (tile_pos - reference).
            # The display transform shifts the volume by -(current - reference) to show the correct
            # focal plane relationship: whichever tile is at the current stage position appears at the focal plane.
            #
            # This works because:
            # - Storage: base + delta (where delta = tile_pos - reference)
            # - Display: shifts by -delta (where delta = current - reference)
            # - When current = tile_pos (at capture position): combined = base + delta - delta = base (focal plane)
            # - When current != tile_pos: tile appears offset from focal plane (as expected)
            self._tile_reference_position = {
                'x': ref_x,
                'y': ref_y,
                'z': ref_z,
                'r': ref_r
            }
            self._tile_reference_set = True

            # Also set voxel_storage reference so display transform is applied
            # The display transform uses NEGATIVE delta for all axes, which cancels the storage delta
            # when viewing from the tile's capture position.
            self.voxel_storage.set_reference_position(self._tile_reference_position)
            self.logger.info(f"Sample View: Tile reference set to "
                            f"X={ref_x:.3f}, Y={ref_y:.3f}, Z={ref_z:.3f}, R={ref_r:.1f}° "
                            f"(both local and voxel_storage)")

        # Update last_stage_position only on FIRST FRAME of each new tile
        # This makes the display transform shift once per tile (not every Z frame),
        # showing the correct focal plane relationship: currently captured tile at focal plane.
        # Using z_min as the representative Z position for the tile.
        if frame_count == 1:
            self.last_stage_position = {
                'x': position['x'],
                'y': position['y'],
                'z': z_min,  # Use tile's starting Z, not varying z_position
                'r': position.get('r', self.last_stage_position.get('r', 0))
            }
            self.logger.info(f"Sample View: New tile at ({position['x']:.3f}, {position['y']:.3f}), "
                            f"updated last_stage_position for display transform")

        # Add frame to volume - pass reference explicitly for storage delta calculation
        self.add_frame_to_volume(
            image=image,
            stage_position_mm={'x': position['x'], 'y': position['y'], 'z': z_position},
            channel_id=self._current_channel,
            reference_position=self._tile_reference_position
        )

        # Kick the debounced channel-availability check so checkboxes
        # get enabled once storage reports has_data()==True.
        # The timer is single-shot, so repeated .start() calls just reset it.
        self._channel_availability_timer.start()

        # Only kick visualization timer on FIRST frame of each tile.
        # This ensures the previous tile is complete before we refresh the display.
        # The current tile will show on the NEXT tile's first frame (or workflow completion).
        if frame_count == 1:
            self._visualization_update_timer.start()

        self.logger.debug(f"Sample View: Accumulated Z-plane {z_index} for tile "
                         f"({position['x']:.2f}, {position['y']:.2f})")

