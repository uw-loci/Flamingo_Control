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
from visualization.coordinate_transforms import CoordinateTransformer

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
    z_position_changed = pyqtSignal(float)
    channel_visibility_changed = pyqtSignal(int, bool)

    def __init__(self, movement_controller=None, camera_controller=None, parent=None):
        super().__init__(parent)

        self.movement_controller = movement_controller
        self.camera_controller = camera_controller

        # Load configuration
        self.config = self._load_config()

        # Initialize storage system
        self._init_storage()

        # Initialize coordinate transformer
        self.transformer = CoordinateTransformer()

        # Current state
        self.current_rotation = {'rx': 0, 'ry': 0, 'rz': 0}
        self.current_z = 0
        self.is_streaming = False

        # Sample holder position (will be initialized in _add_sample_holder)
        self.holder_position = {'x': 0, 'y': 0, 'z': 0}
        self.rotation_indicator_length = 0

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
        """Initialize the dual-resolution storage system."""
        # Convert config to DualResolutionConfig
        chamber_dims_um = tuple(
            d * 1000 for d in self.config['sample_chamber']['inner_dimensions_mm']
        )

        storage_config = DualResolutionConfig(
            storage_voxel_size=tuple(self.config['storage']['voxel_size_um']),
            display_voxel_size=tuple(self.config['display']['voxel_size_um']),
            chamber_dimensions=chamber_dims_um
        )

        self.voxel_storage = DualResolutionVoxelStorage(storage_config)
        logger.info("Initialized dual-resolution voxel storage")

    def _setup_ui(self):
        """Setup the user interface."""
        main_layout = QHBoxLayout(self)

        # Create splitter for resizable panels
        splitter = QSplitter(Qt.Horizontal)

        # Left panel: Controls
        control_panel = self._create_control_panel()
        splitter.addWidget(control_panel)

        # Right panel: Napari viewer placeholder
        viewer_container = QWidget()
        viewer_layout = QVBoxLayout(viewer_container)

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

        # Rotation Controls tab
        rotation_tab = self._create_rotation_controls()
        tabs.addTab(rotation_tab, "Rotation")

        # Data Management tab
        data_tab = self._create_data_controls()
        tabs.addTab(data_tab, "Data")

        # Display Settings tab
        display_tab = self._create_display_controls()
        tabs.addTab(display_tab, "Display")

        layout.addWidget(tabs)

        # Status panel
        status_group = QGroupBox("Status")
        status_layout = QVBoxLayout()

        self.status_label = QLabel("Ready")
        self.memory_label = QLabel("Memory: 0 MB")
        self.voxel_count_label = QLabel("Voxels: 0")

        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.memory_label)
        status_layout.addWidget(self.voxel_count_label)

        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

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

            # Layout channel controls
            ch_layout.addWidget(visible_cb, 0, 0, 1, 2)
            ch_layout.addWidget(QLabel("Color:"), 1, 0)
            ch_layout.addWidget(colormap_combo, 1, 1)
            ch_layout.addWidget(QLabel("Opacity:"), 2, 0)
            ch_layout.addWidget(opacity_slider, 2, 1)
            ch_layout.addWidget(opacity_label, 2, 2)
            ch_layout.addWidget(QLabel("Update:"), 3, 0)
            ch_layout.addWidget(strategy_combo, 3, 1)

            group.setLayout(ch_layout)
            layout.addWidget(group)

            # Store references
            self.channel_controls[ch_id] = {
                'visible': visible_cb,
                'colormap': colormap_combo,
                'opacity': opacity_slider,
                'opacity_label': opacity_label,
                'strategy': strategy_combo
            }

            # Connect opacity slider to label
            opacity_slider.valueChanged.connect(
                lambda v, label=opacity_label: label.setText(f"{v}%")
            )

        layout.addStretch()
        return widget

    def _create_rotation_controls(self) -> QWidget:
        """Create rotation control widgets."""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Add clarification about rotation types
        info_label = QLabel("Note: Only Y-axis rotation is physical (stage rotation).\nX and Z rotations are for visualization only.")
        info_label.setWordWrap(True)
        info_label.setStyleSheet("QLabel { color: #666; font-style: italic; padding: 5px; }")
        layout.addWidget(info_label)

        rotation_group = QGroupBox("View Rotation")
        rot_layout = QGridLayout()

        self.rotation_sliders = {}
        # Y is physical rotation (stage), X and Z are visual only
        axes = [
            ('X (Visual)', 'rx', -180, 180),
            ('Y (Stage)', 'ry', -180, 180),
            ('Z (Visual)', 'rz', 0, 360)
        ]

        for i, (label, key, min_val, max_val) in enumerate(axes):
            rot_layout.addWidget(QLabel(f"{label}:"), i, 0)

            slider = QSlider(Qt.Horizontal)
            slider.setRange(min_val, max_val)
            slider.setValue(0)
            rot_layout.addWidget(slider, i, 1)

            value_label = QLabel("0°")
            rot_layout.addWidget(value_label, i, 2)

            # Reset button
            reset_btn = QPushButton("Reset")
            reset_btn.clicked.connect(lambda checked, s=slider: s.setValue(0))
            rot_layout.addWidget(reset_btn, i, 3)

            self.rotation_sliders[key] = {
                'slider': slider,
                'label': value_label
            }

            # Connect slider to label
            slider.valueChanged.connect(
                lambda v, label=value_label: label.setText(f"{v}°")
            )

        rotation_group.setLayout(rot_layout)
        layout.addWidget(rotation_group)

        # Z Position control
        z_group = QGroupBox("Z Position")
        z_layout = QGridLayout()

        z_layout.addWidget(QLabel("Z:"), 0, 0)
        self.z_spinbox = QSpinBox()
        self.z_spinbox.setRange(-10000, 35000)
        self.z_spinbox.setSuffix(" µm")
        self.z_spinbox.setSingleStep(10)
        z_layout.addWidget(self.z_spinbox, 0, 1)

        z_group.setLayout(z_layout)
        layout.addWidget(z_group)

        layout.addStretch()
        return widget

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

        # Rotation axes
        self.show_axes_cb = QCheckBox("Show Rotation Axes")
        self.show_axes_cb.setChecked(True)

        # Rendering mode
        disp_layout.addWidget(QLabel("Rendering:"), 3, 0)
        self.rendering_combo = QComboBox()
        self.rendering_combo.addItems(['mip', 'minip', 'average', 'iso'])
        disp_layout.addWidget(self.rendering_combo, 3, 1)

        disp_layout.addWidget(self.show_chamber_cb, 0, 0, 1, 2)
        disp_layout.addWidget(self.show_objective_cb, 1, 0, 1, 2)
        disp_layout.addWidget(self.show_axes_cb, 2, 0, 1, 2)

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

        # Rotation sliders
        for key, controls in self.rotation_sliders.items():
            controls['slider'].valueChanged.connect(self._on_rotation_changed)

        # Z position
        self.z_spinbox.valueChanged.connect(self._on_z_changed)

        # Channel visibility
        for ch_id, controls in self.channel_controls.items():
            controls['visible'].toggled.connect(
                lambda checked, cid=ch_id: self._on_channel_visibility_changed(cid, checked)
            )

        # Display settings
        self.show_chamber_cb.toggled.connect(self._on_display_settings_changed)
        self.show_objective_cb.toggled.connect(self._on_display_settings_changed)
        self.show_axes_cb.toggled.connect(self._on_display_settings_changed)

    def _init_napari_viewer(self):
        """Initialize the napari viewer."""
        if not NAPARI_AVAILABLE:
            return

        try:
            # Create napari viewer
            self.viewer = napari.Viewer(ndisplay=3, show=False)

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

        # Add objective position indicator
        # Position it at a reasonable default location
        dims = self.voxel_storage.display_dims
        objective_pos = np.array([[dims[0] // 2, dims[1] // 2, dims[2] // 4]])  # Near bottom
        self.viewer.add_points(
            objective_pos,
            name='Objective',
            size=80,  # Larger size for better visibility
            face_color='yellow',
            border_color='orange',  # napari >= 0.5.0 uses border_* parameters
            border_width=0.15,  # Relative to point size
            opacity=0.9
        )

        # Add rotation axes (will be updated dynamically)
        # Initialize with actual axes data
        self._add_rotation_axes()

    def _add_chamber_wireframe(self):
        """Add chamber wireframe as box edges using shapes layer."""
        if not self.viewer:
            return

        dims = self.voxel_storage.display_dims

        # Define the 8 corners of the box
        corners = np.array([
            [0, 0, 0],
            [dims[0]-1, 0, 0],
            [dims[0]-1, dims[1]-1, 0],
            [0, dims[1]-1, 0],
            [0, 0, dims[2]-1],
            [dims[0]-1, 0, dims[2]-1],
            [dims[0]-1, dims[1]-1, dims[2]-1],
            [0, dims[1]-1, dims[2]-1]
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

        # Sample holder dimensions (in voxels)
        # Convert from mm to voxels based on display resolution
        holder_diameter_mm = 1.0  # 1mm diameter cylinder
        voxel_size_um = self.config['display']['voxel_size_um'][0]  # Assume isotropic
        holder_radius_voxels = int((holder_diameter_mm * 1000 / 2) / voxel_size_um)

        # Get chamber dimensions
        dims = self.voxel_storage.display_dims

        # Sample holder extends from top to current Z position
        # Default to center of chamber in X and Y
        self.holder_position = {
            'x': dims[0] // 2,
            'y': dims[1] // 2,
            'z': dims[2] // 2  # Default Z position
        }

        # Create cylinder as a series of circles (points)
        holder_points = []

        # Generate cylinder from top of chamber down to holder position
        z_top = dims[2] - 1
        z_bottom = self.holder_position['z']

        # Create vertical line of points for cylinder axis
        for z in range(z_bottom, z_top, 2):  # Sample every 2 voxels for performance
            holder_points.append([self.holder_position['x'], self.holder_position['y'], z])

        if holder_points:
            holder_array = np.array(holder_points)
            self.viewer.add_points(
                holder_array,
                name='Sample Holder',
                size=holder_radius_voxels * 2,  # Diameter for point size
                face_color='gray',
                border_color='darkgray',
                border_width=0.05,  # Small border
                opacity=0.6,
                shading='spherical'
            )

    def _add_rotation_indicator(self):
        """Add rotation indicator extending from sample holder at 0 degrees."""
        if not self.viewer:
            return

        # Indicator dimensions
        dims = self.voxel_storage.display_dims
        indicator_length = dims[0] // 10  # 1/10th of chamber width

        # Position: extends from holder position along X-axis (0 degrees)
        # Always at the top of the displayed holder
        z_position = dims[2] - 10  # Near top of chamber

        indicator_start = np.array([
            self.holder_position['x'],
            self.holder_position['y'],
            z_position
        ])

        indicator_end = np.array([
            self.holder_position['x'] + indicator_length,
            self.holder_position['y'],
            z_position
        ])

        # Add as a line (using shapes layer for better control)
        self.viewer.add_shapes(
            data=[[indicator_start, indicator_end]],  # 3D line in 3D viewer
            shape_type='line',
            name='Rotation Indicator',
            edge_color='red',
            edge_width=3,
            opacity=0.8
        )

        # Store for updates during rotation
        self.rotation_indicator_base = indicator_start.copy()
        self.rotation_indicator_length = indicator_length

    def _update_sample_holder_position(self, x_pos=None, y_pos=None, z_pos=None):
        """Update sample holder position when stage moves."""
        if not self.viewer or 'Sample Holder' not in self.viewer.layers:
            return

        # Update position
        if x_pos is not None:
            self.holder_position['x'] = int(x_pos / self.config['display']['voxel_size_um'][0])
        if y_pos is not None:
            self.holder_position['y'] = int(y_pos / self.config['display']['voxel_size_um'][1])
        if z_pos is not None:
            self.holder_position['z'] = int(z_pos / self.config['display']['voxel_size_um'][2])

        # Regenerate holder points
        dims = self.voxel_storage.display_dims
        holder_points = []

        z_top = dims[2] - 1
        z_bottom = max(0, self.holder_position['z'])

        for z in range(z_bottom, z_top, 2):
            holder_points.append([self.holder_position['x'], self.holder_position['y'], z])

        if holder_points:
            self.viewer.layers['Sample Holder'].data = np.array(holder_points)

        # Update rotation indicator position (stays at top)
        self._update_rotation_indicator()

    def _update_rotation_indicator(self):
        """Update rotation indicator based on current rotation and holder position."""
        if not self.viewer or 'Rotation Indicator' not in self.viewer.layers:
            return

        # Calculate rotated position of indicator
        angle_rad = np.radians(self.current_rotation.get('rz', 0))

        # Indicator extends from holder center
        dims = self.voxel_storage.display_dims
        z_position = dims[2] - 10  # Always near top

        start = np.array([
            self.holder_position['x'],
            self.holder_position['y'],
            z_position
        ])

        # Calculate end point based on rotation
        dx = self.rotation_indicator_length * np.cos(angle_rad)
        dy = self.rotation_indicator_length * np.sin(angle_rad)

        end = start + np.array([dx, dy, 0])  # Add 0 for z component

        # Update the line - provide 3D coordinates for 3D viewer
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

    def _on_streaming_toggled(self, checked: bool):
        """Handle streaming start/stop."""
        self.is_streaming = checked

        if checked:
            self.start_button.setText("Stop Streaming")
            self.status_label.setText("Streaming...")
            self.update_timer.start()

            # Start acquiring data if camera controller available
            if self.camera_controller:
                # This would connect to actual camera streaming
                pass

        else:
            self.start_button.setText("Start Streaming")
            self.status_label.setText("Stopped")
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
            self.status_label.setText("Data cleared")
            logger.info("Cleared all visualization data")

    def _on_export_data(self):
        """Export visualization data."""
        # TODO: Implement export functionality
        QMessageBox.information(self, "Export", "Export functionality not yet implemented")

    def _on_rotation_changed(self):
        """Handle rotation slider changes."""
        # Update current rotation
        for key, controls in self.rotation_sliders.items():
            self.current_rotation[key] = controls['slider'].value()

        # Update transformer
        self.transformer.set_rotation(**self.current_rotation)

        # Emit signal
        self.rotation_changed.emit(self.current_rotation)

        # Update visualization
        self._update_rotation_axes()
        self._update_rotation_indicator()  # Update rotation indicator

    def _on_z_changed(self, value: int):
        """Handle Z position changes."""
        self.current_z = value
        self.z_position_changed.emit(value)
        # Update sample holder to show it moving with stage
        self._update_sample_holder_position(z_pos=value)

    def _on_channel_visibility_changed(self, channel_id: int, visible: bool):
        """Handle channel visibility changes."""
        if self.viewer and channel_id in self.channel_layers:
            self.channel_layers[channel_id].visible = visible
        self.channel_visibility_changed.emit(channel_id, visible)

    def _on_display_settings_changed(self):
        """Handle display setting changes."""
        if not self.viewer:
            return

        # Update layer visibility
        if 'Chamber' in self.viewer.layers:
            self.viewer.layers['Chamber'].visible = self.show_chamber_cb.isChecked()

        if 'Objective' in self.viewer.layers:
            self.viewer.layers['Objective'].visible = self.show_objective_cb.isChecked()

        if 'Rotation Axes' in self.viewer.layers:
            self.viewer.layers['Rotation Axes'].visible = self.show_axes_cb.isChecked()

    def _add_rotation_axes(self):
        """Add rotation axes to the viewer."""
        if not self.viewer:
            return

        # Create rotation vectors at origin
        center = np.array([
            self.voxel_storage.display_dims[0] // 2,
            self.voxel_storage.display_dims[1] // 2,
            self.voxel_storage.display_dims[2] // 2
        ])

        # Axis lengths (in voxels)
        axis_length = 30

        # Create axes vectors (start point, direction vector)
        vectors = np.array([
            [[center[0], center[1], center[2]], [axis_length, 0, 0]],  # X axis - red
            [[center[0], center[1], center[2]], [0, axis_length, 0]],  # Y axis - green
            [[center[0], center[1], center[2]], [0, 0, axis_length]]   # Z axis - blue
        ])

        self.viewer.add_vectors(
            vectors,
            name='Rotation Axes',
            edge_color=['red', 'green', 'blue'],
            edge_width=3,
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

        # Update current state
        if 'z_position' in metadata:
            self.current_z = metadata['z_position']
            self.z_spinbox.setValue(int(self.current_z))

        if 'rotation' in metadata:
            self.current_rotation = metadata['rotation']
            # Update sliders
            for key, value in self.current_rotation.items():
                if key in self.rotation_sliders:
                    self.rotation_sliders[key]['slider'].setValue(int(value))

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
        self.status_label.setText(f"Processing frame at Z={self.current_z:.1f} µm")

    def closeEvent(self, event):
        """Handle window close event."""
        # Stop streaming
        if self.is_streaming:
            self.start_button.setChecked(False)

        # Close napari viewer
        if self.viewer:
            self.viewer.close()

        event.accept()