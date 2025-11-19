"""
3D Sample Chamber Visualization Window with rotation-aware data accumulation.
"""

import numpy as np
import yaml
from pathlib import Path
from typing import Optional, Dict, Tuple
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QGroupBox, QSlider, QCheckBox, QComboBox, QSpinBox,
    QSplitter, QTabWidget, QGridLayout, QMessageBox
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
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

    def __init__(self, movement_controller=None, camera_controller=None, parent=None):
        super().__init__(parent)

        self.movement_controller = movement_controller
        self.camera_controller = camera_controller

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

        # Initialize storage system using coord_mapper dimensions
        self._init_storage_with_mapper()

        # Initialize coordinate transformers (for rotation)
        self.transformer = CoordinateTransformer()

        # Current state
        self.current_rotation = {'rx': 0, 'ry': 0, 'rz': 0}
        self.current_z = 0
        self.is_streaming = False

        # Sample holder position (will be initialized in _add_sample_holder)
        self.holder_position = {'x': 0, 'y': 0, 'z': 0}
        self.rotation_indicator_length = 0

        # Test sample data (raw, unrotated)
        self.test_sample_data_raw = None
        self.test_sample_size_mm = 2.0  # 2mm cube of sample data
        self.test_sample_offset_mm = 0.5  # 0.5mm below holder tip

        # Sparse volume renderer for efficient display
        self.sparse_renderer = None  # Initialized after voxel_storage

        # Setup UI
        self._setup_ui()
        self._connect_signals()

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
                    'voxel_size_um': [15, 15, 15],
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
                    'inner_dimensions_mm': [10, 10, 43]
                },
                'channels': [
                    {'id': 0, 'name': 'DAPI', 'default_colormap': 'cyan'},
                    {'id': 1, 'name': 'GFP', 'default_colormap': 'green'},
                    {'id': 2, 'name': 'RFP', 'default_colormap': 'red'},
                    {'id': 3, 'name': 'BF', 'default_colormap': 'gray'}
                ]
            }
            logger.warning("Using default 3D visualization config")

        return config

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

        storage_config = DualResolutionConfig(
            storage_voxel_size=tuple(self.config['storage']['voxel_size_um']),
            display_voxel_size=tuple(self.config['display']['voxel_size_um']),
            chamber_dimensions=chamber_dims_um
        )

        self.voxel_storage = DualResolutionVoxelStorage(storage_config)
        logger.info(f"Initialized dual-resolution voxel storage")
        logger.info(f"  Napari dimensions (Z, Y, X): {napari_dims}")
        logger.info(f"  Chamber dimensions (µm): {chamber_dims_um}")
        logger.info(f"  Display dimensions (voxels): {self.voxel_storage.display_dims}")
        logger.info(f"  Voxel size (µm): {self.config['display']['voxel_size_um']}")

        # Initialize sparse volume renderer
        self.sparse_renderer = SparseVolumeRenderer(
            dims=self.voxel_storage.display_dims,
            num_channels=4,
            block_size=32,
            use_sparse=True
        )

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
        splitter.setSizes([400, 800])

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

        self.start_button = QPushButton("Start Streaming")
        self.start_button.setCheckable(True)
        self.clear_button = QPushButton("Clear Data")
        self.export_button = QPushButton("Export...")

        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.clear_button)
        button_layout.addWidget(self.export_button)

        layout.addLayout(button_layout)

        return control_widget

    def _create_channel_controls(self) -> QWidget:
        """Create channel control widgets."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.channel_controls = {}

        for ch_config in self.config['channels']:
            ch_id = ch_config['id']
            ch_name = ch_config['name']

            group = QGroupBox(f"Channel {ch_id}: {ch_name}")
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

            # Update strategy
            strategy_combo = QComboBox()
            strategy_combo.addItems(['latest', 'maximum', 'average', 'additive'])
            strategy_combo.setCurrentText(ch_config.get('update_strategy', 'latest'))

            # Contrast limits (min/max intensity)
            contrast_min_slider = QSlider(Qt.Horizontal)
            contrast_min_slider.setRange(0, 65535)
            contrast_min_slider.setValue(0)
            contrast_min_label = QLabel("0")

            contrast_max_slider = QSlider(Qt.Horizontal)
            contrast_max_slider.setRange(0, 65535)
            contrast_max_slider.setValue(65535)
            contrast_max_label = QLabel("65535")

            # Layout channel controls
            ch_layout.addWidget(visible_cb, 0, 0, 1, 3)
            ch_layout.addWidget(QLabel("Color:"), 1, 0)
            ch_layout.addWidget(colormap_combo, 1, 1, 1, 2)
            ch_layout.addWidget(QLabel("Opacity:"), 2, 0)
            ch_layout.addWidget(opacity_slider, 2, 1)
            ch_layout.addWidget(opacity_label, 2, 2)
            ch_layout.addWidget(QLabel("Contrast Min:"), 3, 0)
            ch_layout.addWidget(contrast_min_slider, 3, 1)
            ch_layout.addWidget(contrast_min_label, 3, 2)
            ch_layout.addWidget(QLabel("Contrast Max:"), 4, 0)
            ch_layout.addWidget(contrast_max_slider, 4, 1)
            ch_layout.addWidget(contrast_max_label, 4, 2)
            ch_layout.addWidget(QLabel("Update:"), 5, 0)
            ch_layout.addWidget(strategy_combo, 5, 1, 1, 2)

            group.setLayout(ch_layout)
            layout.addWidget(group)

            # Store references
            self.channel_controls[ch_id] = {
                'visible': visible_cb,
                'colormap': colormap_combo,
                'opacity': opacity_slider,
                'opacity_label': opacity_label,
                'strategy': strategy_combo,
                'contrast_min': contrast_min_slider,
                'contrast_min_label': contrast_min_label,
                'contrast_max': contrast_max_slider,
                'contrast_max_label': contrast_max_label
            }

            # Connect sliders to labels
            opacity_slider.valueChanged.connect(
                lambda v, label=opacity_label: label.setText(f"{v}%")
            )
            contrast_min_slider.valueChanged.connect(
                lambda v, label=contrast_min_label, cid=ch_id: self._on_contrast_changed(cid, v, 'min', label)
            )
            contrast_max_slider.valueChanged.connect(
                lambda v, label=contrast_max_label, cid=ch_id: self._on_contrast_changed(cid, v, 'max', label)
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

        position_group.setLayout(pos_layout)
        layout.addWidget(position_group)

        # Rotation control
        rotation_group = QGroupBox("Stage Rotation (Y-Axis)")
        rot_layout = QVBoxLayout()

        # Rotation header
        rot_header = QHBoxLayout()
        rot_header.addWidget(QLabel("Rotation Angle:"))
        rot_header.addStretch()
        self.rotation_value_label = QLabel(f"{stage_config['rotation_default_deg']:.1f}°")
        self.rotation_value_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        rot_header.addWidget(self.rotation_value_label)
        rot_layout.addLayout(rot_header)

        # Rotation slider
        self.rotation_slider = QSlider(Qt.Horizontal)
        rot_range = stage_config['rotation_range_deg']
        self.rotation_slider.setRange(int(rot_range[0]), int(rot_range[1]))
        self.rotation_slider.setValue(int(stage_config['rotation_default_deg']))
        self.rotation_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                border: 1px solid #999999;
                height: 8px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #FF0000, stop:0.5 #888888, stop:1 #00FF00);
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
        range_layout.addWidget(QLabel(f"{rot_range[0]}°"))
        range_layout.addStretch()
        range_layout.addWidget(QLabel(f"{rot_range[1]}°"))
        rot_layout.addLayout(range_layout)

        # Reset button
        reset_btn = QPushButton("Reset to 0°")
        reset_btn.clicked.connect(lambda: self.rotation_slider.setValue(0))
        rot_layout.addWidget(reset_btn)

        # Connect slider to label
        self.rotation_slider.valueChanged.connect(
            lambda v: self.rotation_value_label.setText(f"{v:.1f}°")
        )

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

        # Connect slider to update spinbox
        slider.valueChanged.connect(
            lambda v, sb=value_spinbox: sb.setValue(v/1000.0)
        )

        # Connect spinbox to update slider
        value_spinbox.valueChanged.connect(
            lambda v, sl=slider: sl.setValue(int(v * 1000))
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
        # Start/stop streaming
        self.start_button.toggled.connect(self._on_streaming_toggled)

        # Clear data
        self.clear_button.clicked.connect(self._on_clear_data)

        # Export
        self.export_button.clicked.connect(self._on_export_data)

        # Position sliders
        self.position_sliders['x_slider'].valueChanged.connect(self._on_x_slider_changed)
        self.position_sliders['y_slider'].valueChanged.connect(self._on_y_slider_changed)
        self.position_sliders['z_slider'].valueChanged.connect(self._on_z_slider_changed)

        # Rotation slider
        self.rotation_slider.valueChanged.connect(self._on_rotation_changed)

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

    def _init_napari_viewer(self):
        """Initialize the napari viewer."""
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
            self.viewer.camera.zoom = 2.0  # Start with a reasonable zoom

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

            # Now that viewer is fully initialized, update sample data visualization
            try:
                self._update_sample_data_visualization()
                logger.info("Initial sample data visualization complete")
            except Exception as e:
                logger.error(f"Failed to update sample data visualization: {e}")

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

        # Add rotation indicator (extends from sample holder at 0 degrees)
        self._add_rotation_indicator()

        # Add objective position indicator as a flat circle on back wall (Z=0)
        # This shows the detection light path direction
        self._add_objective_indicator()

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

        # Define edges connecting corners (12 edges of a box)
        edges = [
            # Bottom face
            [corners[0], corners[1]],
            [corners[1], corners[2]],
            [corners[2], corners[3]],
            [corners[3], corners[0]],
            # Top face
            [corners[4], corners[5]],
            [corners[5], corners[6]],
            [corners[6], corners[7]],
            [corners[7], corners[4]],
            # Vertical edges
            [corners[0], corners[4]],
            [corners[1], corners[5]],
            [corners[2], corners[6]],
            [corners[3], corners[7]]
        ]

        # Add as shapes layer with lines
        self.viewer.add_shapes(
            data=edges,
            shape_type='line',
            name='Chamber',
            edge_color='cyan',
            edge_width=2,
            opacity=0.7
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
        y_mm = self.position_sliders['y_slider'].value() / 1000.0
        z_mm = self.position_sliders['z_slider'].value() / 1000.0

        # Convert to napari coordinates (returns X, Y, Z conceptually)
        napari_x, napari_y, napari_z = self.coord_mapper.physical_to_napari(x_mm, y_mm, z_mm)

        # Store holder position
        self.holder_position = {
            'x': napari_x,
            'y': napari_y,
            'z': napari_z
        }

        logger.info(f"Initial physical position: ({x_mm:.2f}, {y_mm:.2f}, {z_mm:.2f}) mm")
        logger.info(f"Initial holder position (voxels): X={napari_x}, Y={napari_y}, Z={napari_z}")
        logger.info(f"Chamber dims (Z,Y,X): {dims}")

        # Create holder points
        # Holder extends from current Y position to top (Y=0 in napari coords)
        holder_points = []

        y_top = 0  # Top of chamber (Y=0)
        y_bottom = napari_y

        # Create vertical line of points
        # Napari coordinates: (Z, Y, X) order!
        for y in range(y_top, y_bottom + 1, 2):  # Sample every 2 voxels
            # Points in (Z, Y, X) order
            holder_points.append([napari_z, y, napari_x])

        logger.info(f"Created {len(holder_points)} holder points (Y from {y_top} to {y_bottom})")
        if holder_points:
            logger.info(f"First point (Z,Y,X): {holder_points[0]}, Last point: {holder_points[-1]}")

        if holder_points:
            holder_array = np.array(holder_points)
            self.viewer.add_points(
                holder_array,
                name='Sample Holder',
                size=holder_radius_voxels * 2,  # Diameter for point size
                face_color='gray',
                border_color='darkgray',
                border_width=0.05,
                opacity=0.6,
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

        # Objective centered in the chamber (middle of Y range)
        # Chamber extends from y_range_mm[0] to y_range_mm[1]
        y_chamber_center_mm = (self.coord_mapper.y_range_mm[0] + self.coord_mapper.y_range_mm[1]) / 2
        napari_y_center = int((self.coord_mapper.y_range_mm[1] - y_chamber_center_mm) /
                              (self.coord_mapper.voxel_size_mm))

        center_y = napari_y_center  # Y at chamber center (Axis 1)
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

        self.viewer.add_points(
            np.array(circle_points),
            name='Objective',
            size=15,
            face_color='yellow',
            border_color='orange',
            border_width=0.2,
            opacity=0.9
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

        # Add as a line (using shapes layer for better control)
        self.viewer.add_shapes(
            data=[[indicator_start, indicator_end]],  # 3D line in (Z, Y, X) order
            shape_type='line',
            name='Rotation Indicator',
            edge_color='red',
            edge_width=3,
            opacity=0.8
        )

        # Store indicator length for rotation updates
        self.rotation_indicator_length = indicator_length

        logger.info(f"Added rotation indicator at Y=0 (top), following holder at Z={holder_z}, X={holder_x}")

    def _update_sample_holder_position(self, x_mm: float, y_mm: float, z_mm: float):
        """
        Update sample holder position when stage moves.

        Args:
            x_mm, y_mm, z_mm: Physical stage coordinates in mm
        """
        if not self.viewer or 'Sample Holder' not in self.viewer.layers:
            return

        # Convert physical mm to napari pixel coordinates
        napari_x, napari_y, napari_z = self.coord_mapper.physical_to_napari(x_mm, y_mm, z_mm)

        # Update holder position
        self.holder_position = {
            'x': napari_x,
            'y': napari_y,
            'z': napari_z
        }

        logger.info(f"Physical position: ({x_mm:.2f}, {y_mm:.2f}, {z_mm:.2f}) mm")
        logger.info(f"Napari position: ({napari_x}, {napari_y}, {napari_z}) pixels")

        # Regenerate holder points
        # Holder extends from current Y position (napari_y) to top (Y=0)
        # Note: Y=0 is top, Y increases downward in napari coords (inverted from physical)
        holder_points = []

        y_top = 0  # Top of chamber (Y=0 in napari coords)
        y_bottom = napari_y

        # Napari coordinates: (Z, Y, X) order!
        for y in range(y_top, y_bottom + 1, 2):  # Sample every 2 voxels
            holder_points.append([napari_z, y, napari_x])

        logger.info(f"Regenerated {len(holder_points)} holder points (y_top={y_top}, y_bottom={y_bottom})")

        # Update the layer data
        if holder_points:
            self.viewer.layers['Sample Holder'].data = np.array(holder_points)
        else:
            # If no points (holder at very top), show a minimal placeholder
            self.viewer.layers['Sample Holder'].data = np.array([[napari_z, y_top, napari_x]])

        # Update rotation indicator position (stays at top)
        self._update_rotation_indicator()

    def _update_rotation_indicator(self):
        """Update rotation indicator based on current rotation (follows sample holder XZ position)."""
        if not self.viewer or 'Rotation Indicator' not in self.viewer.layers:
            return

        # Get Y-axis rotation (the physical stage rotation)
        angle_rad = np.radians(self.current_rotation.get('ry', 0))

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

            # Add layer
            layer = self.viewer.add_image(
                empty_volume,
                name=ch_name,
                colormap=ch_config['default_colormap'],
                visible=ch_config.get('default_visible', True),
                blending='additive',
                opacity=0.8,
                rendering='mip'
            )

            # Store layer reference
            if not hasattr(self, 'channel_layers'):
                self.channel_layers = {}
            self.channel_layers[ch_id] = layer

        # Generate test sample data (but don't visualize yet - viewer not ready)
        self._generate_test_sample_data()

    def _generate_test_sample_data(self):
        """Generate test sample data for visualization testing."""
        # Calculate sample data size in voxels
        voxel_size_mm = self.coord_mapper.voxel_size_mm
        sample_size_voxels = int(self.test_sample_size_mm / voxel_size_mm)

        # Create 4-channel test data (raw, unrotated)
        self.test_sample_data_raw = {}

        for ch_id in range(4):
            # Create a 3D volume for this channel
            data = np.zeros((sample_size_voxels, sample_size_voxels, sample_size_voxels), dtype=np.uint16)

            if ch_id == 0:  # DAPI - small spheres (nuclei)
                # Create 3-5 nuclei
                for _ in range(4):
                    cx, cy, cz = np.random.randint(10, sample_size_voxels-10, 3)
                    radius = np.random.randint(3, 6)
                    y, x, z = np.ogrid[:sample_size_voxels, :sample_size_voxels, :sample_size_voxels]
                    mask = (x-cx)**2 + (y-cy)**2 + (z-cz)**2 <= radius**2
                    data[mask] = np.random.randint(30000, 50000)

            elif ch_id == 1:  # GFP - diffuse signal
                # Create diffuse cloud
                center = sample_size_voxels // 2
                y, x, z = np.ogrid[:sample_size_voxels, :sample_size_voxels, :sample_size_voxels]
                dist = np.sqrt((x-center)**2 + (y-center)**2 + (z-center)**2)
                data = np.clip(20000 * np.exp(-dist/(sample_size_voxels/4)), 0, 65535).astype(np.uint16)

            elif ch_id == 2:  # RFP - linear structures
                # Create some "fibers"
                for _ in range(3):
                    start = np.random.randint(0, sample_size_voxels, 3)
                    direction = np.random.randn(3)
                    direction /= np.linalg.norm(direction)
                    for t in range(sample_size_voxels//2):
                        pos = start + t * direction
                        px, py, pz = np.clip(pos.astype(int), 0, sample_size_voxels-1)
                        # Add thickness
                        for dx in range(-1, 2):
                            for dy in range(-1, 2):
                                for dz in range(-1, 2):
                                    x, y, z = px+dx, py+dy, pz+dz
                                    if 0 <= x < sample_size_voxels and 0 <= y < sample_size_voxels and 0 <= z < sample_size_voxels:
                                        data[y, x, z] = max(data[y, x, z], 25000)

            elif ch_id == 3:  # Far-Red - sparse bright spots
                # Random bright spots
                for _ in range(5):
                    cx, cy, cz = np.random.randint(5, sample_size_voxels-5, 3)
                    radius = np.random.randint(2, 4)
                    y, x, z = np.ogrid[:sample_size_voxels, :sample_size_voxels, :sample_size_voxels]
                    mask = (x-cx)**2 + (y-cy)**2 + (z-cz)**2 <= radius**2
                    data[mask] = np.random.randint(35000, 55000)

            self.test_sample_data_raw[ch_id] = data

        # Log data statistics
        for ch_id, data in self.test_sample_data_raw.items():
            nonzero_count = np.count_nonzero(data)
            max_val = np.max(data)
            logger.info(f"Channel {ch_id}: {nonzero_count} non-zero voxels, max intensity: {max_val}")

        logger.info(f"Generated test sample data: {sample_size_voxels}x{sample_size_voxels}x{sample_size_voxels} voxels per channel")

    def _update_sample_data_visualization(self):
        """Update the sample data visualization with position and rotation transforms."""
        if not self.viewer or self.test_sample_data_raw is None or self.sparse_renderer is None:
            return

        # Get current physical position and rotation
        x_mm = self.position_sliders['x_slider'].value() / 1000.0
        y_mm = self.position_sliders['y_slider'].value() / 1000.0
        z_mm = self.position_sliders['z_slider'].value() / 1000.0
        rotation_deg = self.current_rotation.get('ry', 0)

        # Clear all previous data from sparse renderer
        for ch_id in range(4):
            # Get previous active bounds and clear them
            prev_bounds = self.sparse_renderer.get_active_bounds(ch_id)
            if prev_bounds:
                self.sparse_renderer.clear_region(ch_id, prev_bounds)

        # Calculate sample data position (below holder tip)
        sample_center_y_mm = y_mm - self.test_sample_offset_mm - (self.test_sample_size_mm / 2)

        # Convert center to napari coordinates
        sample_x, sample_y, sample_z = self.coord_mapper.physical_to_napari(
            x_mm, sample_center_y_mm, z_mm
        )

        # Apply rotation transform to sample data
        voxel_size_mm = self.coord_mapper.voxel_size_mm
        sample_size_voxels = int(self.test_sample_size_mm / voxel_size_mm)

        # Update each channel
        for ch_id, raw_data in self.test_sample_data_raw.items():
            if ch_id not in self.channel_layers:
                logger.warning(f"Channel {ch_id} not in channel_layers, skipping")
                continue

            # Apply rotation to the data
            rotated_data = self._rotate_sample_data(raw_data, rotation_deg)

            # Log rotation results
            nonzero_before = np.count_nonzero(raw_data)
            nonzero_after = np.count_nonzero(rotated_data)
            logger.info(f"Ch{ch_id} rotation: {nonzero_before} → {nonzero_after} non-zero voxels")

            # Transpose to (Z, Y, X) for napari
            rotated_transposed = np.transpose(rotated_data, (2, 0, 1))

            # Calculate bounds in napari coordinates
            half_size = sample_size_voxels // 2
            z_start = max(0, sample_z - half_size)
            z_end = min(self.voxel_storage.display_dims[0], sample_z + half_size)
            y_start = max(0, sample_y - half_size)
            y_end = min(self.voxel_storage.display_dims[1], sample_y + half_size)
            x_start = max(0, sample_x - half_size)
            x_end = min(self.voxel_storage.display_dims[2], sample_x + half_size)

            bounds = (z_start, z_end, y_start, y_end, x_start, x_end)

            # Extract the portion that fits in bounds
            data_z_size = z_end - z_start
            data_y_size = y_end - y_start
            data_x_size = x_end - x_start

            # Get corresponding region from rotated data
            src_z_start = max(0, half_size - (sample_z - z_start))
            src_y_start = max(0, half_size - (sample_y - y_start))
            src_x_start = max(0, half_size - (sample_x - x_start))

            data_to_place = rotated_transposed[
                src_z_start:src_z_start + data_z_size,
                src_y_start:src_y_start + data_y_size,
                src_x_start:src_x_start + data_x_size
            ]

            # Log what we're placing
            logger.info(f"Ch{ch_id}: placing {data_to_place.shape} at bounds {bounds}")
            logger.info(f"  Data range: {data_to_place.min()}-{data_to_place.max()}, non-zero: {np.count_nonzero(data_to_place)}")

            # Update sparse renderer
            self.sparse_renderer.update_region(ch_id, bounds, data_to_place)

            # Get dense volume from sparse renderer and update napari layer
            dense_volume = self.sparse_renderer.get_dense_volume(ch_id)

            # Log dense volume stats
            logger.info(f"Ch{ch_id}: dense volume shape {dense_volume.shape}, non-zero: {np.count_nonzero(dense_volume)}")

            # Update napari layer
            self.channel_layers[ch_id].data = dense_volume
            self.channel_layers[ch_id].contrast_limits = (0, 65535)  # Reset contrast

        # Update memory display
        mem_stats = self.sparse_renderer.get_memory_usage()
        self.memory_label.setText(f"Memory: {mem_stats['total_mb']:.1f} MB")
        self.voxel_count_label.setText(f"Voxels: {mem_stats['total_voxels']:,}")

        logger.info(f"Updated sample data at ({x_mm:.2f}, {y_mm:.2f}, {z_mm:.2f}) mm, rotation={rotation_deg}°")

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

    def _on_streaming_toggled(self, checked: bool):
        """Handle streaming start/stop."""
        self.is_streaming = checked

        if checked:
            self.start_button.setText("Stop Streaming")
            self.status_label.setText("Status: Streaming...")
            self.update_timer.start()

            # Start acquiring data if camera controller available
            if self.camera_controller:
                # This would connect to actual camera streaming
                pass

        else:
            self.start_button.setText("Start Streaming")
            self.status_label.setText("Status: Stopped")
            self.update_timer.stop()

    def _on_clear_data(self):
        """Clear all accumulated data."""
        reply = QMessageBox.question(
            self, "Clear Data",
            "Are you sure you want to clear all accumulated data?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.voxel_storage.clear()
            self._update_visualization()
            self.status_label.setText("Status: Data cleared")
            logger.info("Cleared all visualization data")

    def _on_export_data(self):
        """Export visualization data."""
        # TODO: Implement export functionality
        QMessageBox.information(self, "Export", "Export functionality not yet implemented")

    def _on_x_slider_changed(self, value: int):
        """Handle X slider position changes."""
        x_mm = value / 1000.0  # Convert µm to mm
        self.x_position_changed.emit(x_mm)
        # Update sample holder position using coordinate mapper
        y_mm = self.position_sliders['y_slider'].value() / 1000.0
        z_mm = self.position_sliders['z_slider'].value() / 1000.0
        self._update_sample_holder_position(x_mm, y_mm, z_mm)
        # Update sample data position
        self._update_sample_data_visualization()

    def _on_y_slider_changed(self, value: int):
        """Handle Y slider position changes (vertical)."""
        y_mm = value / 1000.0  # Convert µm to mm
        self.y_position_changed.emit(y_mm)
        # Update sample holder position using coordinate mapper
        x_mm = self.position_sliders['x_slider'].value() / 1000.0
        z_mm = self.position_sliders['z_slider'].value() / 1000.0
        self._update_sample_holder_position(x_mm, y_mm, z_mm)
        # Update sample data position
        self._update_sample_data_visualization()

    def _on_z_slider_changed(self, value: int):
        """Handle Z slider position changes (depth)."""
        z_mm = value / 1000.0  # Convert µm to mm
        self.z_position_changed.emit(z_mm)
        # Update sample holder position using coordinate mapper
        x_mm = self.position_sliders['x_slider'].value() / 1000.0
        y_mm = self.position_sliders['y_slider'].value() / 1000.0
        self._update_sample_holder_position(x_mm, y_mm, z_mm)
        # Update sample data position
        self._update_sample_data_visualization()

    def _on_rotation_changed(self, value: int):
        """Handle rotation slider changes."""
        # Update current rotation (only Y axis rotation)
        self.current_rotation['ry'] = value

        # Update transformer
        self.transformer.set_rotation(**self.current_rotation)

        # Emit signal
        self.rotation_changed.emit(self.current_rotation)

        # Update visualization - both indicator and sample data
        self._update_rotation_indicator()
        self._update_sample_data_visualization()

    def _on_channel_visibility_changed(self, channel_id: int, visible: bool):
        """Handle channel visibility changes."""
        if self.viewer and channel_id in self.channel_layers:
            self.channel_layers[channel_id].visible = visible
        self.channel_visibility_changed.emit(channel_id, visible)

    def _on_contrast_changed(self, channel_id: int, value: int, limit_type: str, label: QLabel):
        """Handle contrast limit changes."""
        # Update label
        label.setText(str(value))

        # Update napari layer contrast limits
        if self.viewer and channel_id in self.channel_layers:
            layer = self.channel_layers[channel_id]
            current_limits = layer.contrast_limits

            if limit_type == 'min':
                new_limits = (value, current_limits[1])
            else:  # 'max'
                new_limits = (current_limits[0], value)

            # Ensure min < max
            if new_limits[0] < new_limits[1]:
                layer.contrast_limits = new_limits
            else:
                logger.warning(f"Invalid contrast limits for channel {channel_id}: {new_limits}")

    def _on_display_settings_changed(self):
        """Handle display setting changes."""
        if not self.viewer:
            return

        # Update layer visibility
        if 'Chamber' in self.viewer.layers:
            self.viewer.layers['Chamber'].visible = self.show_chamber_cb.isChecked()

        if 'Objective' in self.viewer.layers:
            self.viewer.layers['Objective'].visible = self.show_objective_cb.isChecked()

    def _on_reset_view(self):
        """Reset camera to default orientation and zoom."""
        if not self.viewer:
            return

        # Reset camera to default view
        self.viewer.camera.angles = (45, 30, 0)
        self.viewer.camera.zoom = 2.0
        self.viewer.reset_view()

    def _on_rendering_mode_changed(self, mode: str):
        """Handle rendering mode changes."""
        if not self.viewer:
            return

        # Update all channel layers to use the new rendering mode
        for layer in self.channel_layers.values():
            layer.rendering = mode
        logger.info(f"Rendering mode changed to: {mode}")

    def _add_rotation_axes(self):
        """Add rotation axes to the viewer."""
        if not self.viewer:
            return

        # Create rotation axis at center of chamber
        dims = self.voxel_storage.display_dims
        center = np.array([
            dims[0] // 2,  # X center (axis 0)
            dims[1] // 2,  # Y center (axis 1, vertical)
            dims[2] // 2   # Z center (axis 2)
        ])

        # Y-axis length (the physical rotation axis) - make it prominent
        axis_length = dims[1] // 2  # Half chamber height in Y

        # Create Y-axis vector (the physical stage rotation axis)
        # Format for napari vectors in (X, Y, Z) order: array of [position, direction] pairs
        vectors = np.array([
            [[center[0], center[1], center[2]], [0, axis_length, 0]]  # Y axis - green, vertical
        ])

        self.viewer.add_vectors(
            vectors,
            name='Rotation Axes',
            edge_color='green',
            edge_width=5,
            length=1.0,
            visible=self.show_axes_cb.isChecked()
        )

    def _update_rotation_axes(self):
        """Update rotation axis indicators."""
        if not self.viewer or 'Rotation Axes' not in self.viewer.layers:
            return

        # Create rotation vectors
        axes = np.array([
            [30, 0, 0],  # X axis
            [0, 30, 0],  # Y axis
            [0, 0, 30]   # Z axis
        ])

        # Apply rotation
        rotated_axes = axes @ self.transformer.rotation_matrix.T

        # Create vector data for napari (n, 2, d) format
        # n=3 vectors, 2 components (position, direction), d=3 dimensions
        origin = np.array([100, 100, 50])
        vectors = np.zeros((3, 2, 3))
        for i in range(3):
            vectors[i, 0, :] = origin  # Starting position
            vectors[i, 1, :] = rotated_axes[i]  # Direction vector

        self.viewer.layers['Rotation Axes'].data = vectors
        self.viewer.layers['Rotation Axes'].visible = True  # Make visible

    def _update_visualization(self):
        """Update the visualization with latest data."""
        if not self.viewer:
            return

        # Update each channel
        for ch_id in range(self.voxel_storage.num_channels):
            if ch_id in self.channel_layers:
                # Get display volume
                volume = self.voxel_storage.get_display_volume(ch_id)

                # Update layer data
                self.channel_layers[ch_id].data = volume

        # Update memory usage
        memory_stats = self.voxel_storage.get_memory_usage()
        self.memory_label.setText(f"Memory: {memory_stats['total_mb']:.1f} MB")
        self.voxel_count_label.setText(f"Voxels: {memory_stats['storage_voxels']:,}")

        # Check memory limit
        if self.auto_clear_cb.isChecked():
            limit = self.memory_limit_spin.value()
            if memory_stats['total_mb'] > limit:
                logger.warning(f"Memory limit exceeded ({memory_stats['total_mb']:.1f} > {limit} MB)")
                # Could implement auto-clearing of old data here

    def process_frame(self, frame_data: np.ndarray, metadata: dict):
        """
        Process incoming frame data with rotation transformation.

        Args:
            frame_data: Multi-channel image data (H, W, C)
            metadata: Dictionary with z_position, rotation, timestamp, etc.
        """
        if not self.is_streaming:
            return

        # Update current state from metadata
        if 'x_position' in metadata:
            self.stage_position_inputs['x'].setValue(int(metadata['x_position']))
        if 'y_position' in metadata:
            self.stage_position_inputs['y'].setValue(int(metadata['y_position']))
        if 'z_position' in metadata:
            self.current_z = metadata['z_position']
            self.stage_position_inputs['z'].setValue(int(self.current_z))

        if 'rotation' in metadata:
            self.current_rotation = metadata['rotation']
            # Update Y rotation slider
            if 'ry' in self.current_rotation:
                self.rotation_slider.setValue(int(self.current_rotation['ry']))

        # Transform coordinates
        self.transformer.set_rotation(**self.current_rotation)

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

    def closeEvent(self, event):
        """Handle window close event."""
        # Stop streaming
        if self.is_streaming:
            self.start_button.setChecked(False)

        # Close napari viewer
        if self.viewer:
            self.viewer.close()

        event.accept()