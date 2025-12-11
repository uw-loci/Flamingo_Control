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
import numpy as np
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QSlider, QComboBox, QCheckBox, QProgressBar,
    QSplitter, QSizePolicy, QFrame, QSpinBox, QDialog,
    QDialogButtonBox, QGridLayout
)
from PyQt5.QtCore import Qt, pyqtSlot, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage, QFont

from py2flamingo.views.laser_led_control_panel import LaserLEDControlPanel
from py2flamingo.views.colors import SUCCESS_COLOR, ERROR_COLOR, WARNING_BG

# Import camera state for live view control
from py2flamingo.controllers.camera_controller import CameraState

# Axis colors matching napari 3D viewer
AXIS_COLORS = {
    'x': '#008B8B',  # Cyan
    'y': '#8B008B',  # Magenta
    'z': '#8B8B00',  # Yellow/Olive
}


class SlicePlaneViewer(QFrame):
    """2D slice plane viewer with colored borders and overlays.

    Shows MIP projection with sample holder, objective, and viewing frame positions.
    Border colors match the napari 3D viewer axis colors.
    """

    # Signal emitted when user clicks to move (axis1_value, axis2_value)
    position_clicked = pyqtSignal(float, float)

    def __init__(self, plane: str, h_axis: str, v_axis: str,
                 width: int, height: int, parent=None):
        """
        Initialize slice plane viewer.

        Args:
            plane: Plane identifier ('xz', 'xy', 'yz')
            h_axis: Horizontal axis ('x', 'y', or 'z')
            v_axis: Vertical axis ('x', 'y', or 'z')
            width: Widget width in pixels
            height: Widget height in pixels
            parent: Parent widget
        """
        super().__init__(parent)

        self.plane = plane
        self.h_axis = h_axis
        self.v_axis = v_axis
        self._width = width
        self._height = height

        # Physical coordinate ranges (will be set from config)
        self.h_range = (0.0, 1.0)  # (min, max) in mm
        self.v_range = (0.0, 1.0)  # (min, max) in mm

        # Current MIP data
        self._mip_data: Optional[np.ndarray] = None
        self._contrast_limits = (0, 65535)

        # Overlay positions (in physical coordinates, mm)
        self._holder_pos: Optional[Tuple[float, float]] = None  # (h, v)
        self._objective_pos: Optional[Tuple[float, float]] = None
        self._frame_pos: Optional[Tuple[float, float, float, float]] = None  # (h1, v1, h2, v2)

        # Setup UI
        self._setup_ui()

    def _setup_ui(self):
        """Setup the viewer UI with colored borders."""
        self.setFixedSize(self._width, self._height)

        # Get border colors from axis
        h_color = AXIS_COLORS.get(self.h_axis, '#444')
        v_color = AXIS_COLORS.get(self.v_axis, '#444')

        # Create colored border using stylesheet
        # Left/Right borders use horizontal axis color, Top/Bottom use vertical axis color
        self.setStyleSheet(f"""
            SlicePlaneViewer {{
                background-color: #1a1a1a;
                border-left: 3px solid {h_color};
                border-right: 3px solid {h_color};
                border-top: 3px solid {v_color};
                border-bottom: 3px solid {v_color};
            }}
        """)

        # Image label for MIP display
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background-color: transparent; border: none;")
        self.image_label.setText(f"{self.plane.upper()}\n(Click to move)")
        self.image_label.setStyleSheet("color: #666; background-color: transparent;")
        layout.addWidget(self.image_label)

        self.setLayout(layout)

        # Enable mouse tracking for click-to-move
        self.setMouseTracking(True)
        self.image_label.setMouseTracking(True)

    def set_ranges(self, h_range: Tuple[float, float], v_range: Tuple[float, float]):
        """Set the physical coordinate ranges for the axes."""
        self.h_range = h_range
        self.v_range = v_range

    def set_contrast_limits(self, limits: Tuple[int, int]):
        """Set contrast limits for MIP display."""
        self._contrast_limits = limits
        self._update_display()

    def set_mip_data(self, data: np.ndarray):
        """Set the MIP data to display."""
        self._mip_data = data
        self._update_display()

    def set_holder_position(self, h: float, v: float):
        """Set the sample holder position (in physical coordinates)."""
        self._holder_pos = (h, v)
        self._update_display()

    def set_objective_position(self, h: float, v: float):
        """Set the objective position (in physical coordinates)."""
        self._objective_pos = (h, v)
        self._update_display()

    def set_frame_position(self, h1: float, v1: float, h2: float, v2: float):
        """Set the viewing frame position (rectangle in physical coordinates)."""
        self._frame_pos = (h1, v1, h2, v2)
        self._update_display()

    def _update_display(self):
        """Update the display with current MIP data and overlays."""
        # Create image from MIP data
        display_width = self._width - 6  # Account for borders
        display_height = self._height - 6

        if self._mip_data is not None and self._mip_data.size > 0:
            # Apply contrast limits
            data = self._mip_data.astype(np.float32)
            min_val, max_val = self._contrast_limits
            if max_val > min_val:
                data = np.clip((data - min_val) / (max_val - min_val), 0, 1)
            else:
                data = np.zeros_like(data)

            # Convert to 8-bit
            data_8bit = (data * 255).astype(np.uint8)

            # Create QImage
            h, w = data_8bit.shape
            qimage = QImage(data_8bit.data, w, h, w, QImage.Format_Grayscale8)
            pixmap = QPixmap.fromImage(qimage)

            # Scale to fit
            scaled = pixmap.scaled(display_width, display_height,
                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            # Create empty pixmap
            scaled = QPixmap(display_width, display_height)
            scaled.fill(Qt.black)

        # Draw overlays on the pixmap
        from PyQt5.QtGui import QPainter, QPen, QColor
        painter = QPainter(scaled)
        painter.setRenderHint(QPainter.Antialiasing)

        # Calculate scale factors
        img_w = scaled.width()
        img_h = scaled.height()
        h_scale = img_w / (self.h_range[1] - self.h_range[0]) if self.h_range[1] != self.h_range[0] else 1
        v_scale = img_h / (self.v_range[1] - self.v_range[0]) if self.v_range[1] != self.v_range[0] else 1

        def to_pixel(h_coord, v_coord):
            """Convert physical coordinates to pixel coordinates."""
            px = int((h_coord - self.h_range[0]) * h_scale)
            py = int((v_coord - self.v_range[0]) * v_scale)
            return px, py

        # Draw objective (green circle)
        if self._objective_pos:
            pen = QPen(QColor('#00FF00'))
            pen.setWidth(2)
            painter.setPen(pen)
            px, py = to_pixel(*self._objective_pos)
            painter.drawEllipse(px - 8, py - 8, 16, 16)

        # Draw sample holder (white rectangle outline)
        if self._holder_pos:
            pen = QPen(QColor('#FFFFFF'))
            pen.setWidth(1)
            painter.setPen(pen)
            px, py = to_pixel(*self._holder_pos)
            # Draw as small cross
            painter.drawLine(px - 5, py, px + 5, py)
            painter.drawLine(px, py - 5, px, py + 5)

        # Draw viewing frame (cyan dashed rectangle)
        if self._frame_pos:
            pen = QPen(QColor('#00FFFF'))
            pen.setWidth(1)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            px1, py1 = to_pixel(self._frame_pos[0], self._frame_pos[1])
            px2, py2 = to_pixel(self._frame_pos[2], self._frame_pos[3])
            painter.drawRect(min(px1, px2), min(py1, py2),
                           abs(px2 - px1), abs(py2 - py1))

        painter.end()

        self.image_label.setPixmap(scaled)

    def mousePressEvent(self, event):
        """Handle mouse click for click-to-move."""
        if event.button() == Qt.LeftButton:
            # Convert click position to physical coordinates
            pos = event.pos()

            # Account for border
            x = pos.x() - 3
            y = pos.y() - 3

            # Get image dimensions
            pixmap = self.image_label.pixmap()
            if pixmap:
                img_w = pixmap.width()
                img_h = pixmap.height()

                # Calculate physical coordinates
                h_coord = self.h_range[0] + (x / img_w) * (self.h_range[1] - self.h_range[0])
                v_coord = self.v_range[0] + (y / img_h) * (self.v_range[1] - self.v_range[0])

                # Emit signal with coordinates
                self.position_clicked.emit(h_coord, v_coord)

        super().mousePressEvent(event)


class ViewerControlsDialog(QDialog):
    """Placeholder dialog for advanced viewer controls.

    Will be implemented later with full viewer settings including:
    - Opacity per channel
    - Rendering mode (MIP, additive, etc.)
    - 3D visualization settings
    - Export options
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Viewer Controls")
        self.setMinimumSize(400, 300)

        layout = QVBoxLayout()

        # Placeholder content
        placeholder = QLabel(
            "Viewer Controls\n\n"
            "This dialog will provide advanced controls for:\n"
            "• Channel opacity adjustments\n"
            "• Rendering modes (MIP, Volume, etc.)\n"
            "• 3D visualization settings\n"
            "• Camera/view angle controls\n"
            "• Export and snapshot options\n\n"
            "(Coming soon)"
        )
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet(
            "QLabel { background-color: #f5f5f5; padding: 20px; "
            "border: 2px dashed #ccc; border-radius: 8px; }"
        )
        layout.addWidget(placeholder)

        # Button box
        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)


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
        sample_3d_window=None,
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
            sample_3d_window: Optional Sample3DVisualizationWindow for sharing visualization
            parent: Parent widget
        """
        super().__init__(parent)

        self.camera_controller = camera_controller
        self.movement_controller = movement_controller
        self.laser_led_controller = laser_led_controller
        self.voxel_storage = voxel_storage
        self.image_controls_window = image_controls_window
        self.sample_3d_window = sample_3d_window
        self.logger = logging.getLogger(__name__)

        # Display state
        self._current_image: Optional[np.ndarray] = None
        self._colormap = "Grayscale"
        self._auto_scale = True
        self._intensity_min = 0
        self._intensity_max = 65535

        # Stage limits (will be populated from movement controller)
        self._stage_limits = None

        # Position slider scale factors (for int conversion)
        self._slider_scale = 1000  # 3 decimal places

        # Load visualization config for axis inversion settings
        self._config = self._load_visualization_config()
        self._invert_x = self._config.get('stage_control', {}).get('invert_x_default', False)

        # Channel visibility/contrast state for 4 viewers
        self._channel_states = {
            i: {'visible': True, 'contrast_min': 0, 'contrast_max': 65535}
            for i in range(4)
        }

        # Live view state
        self._live_view_active = False

        # Setup window - sized for 3-column layout
        self.setWindowTitle("Sample View")
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

        # Update live view button state
        self._update_live_view_state()

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

        # Live View toggle button (red when stopped, blue when active)
        self.live_view_toggle_btn = QPushButton("Start Live View")
        self.live_view_toggle_btn.setCheckable(True)
        self.live_view_toggle_btn.clicked.connect(self._on_live_view_toggle)
        self.live_view_toggle_btn.setStyleSheet(
            f"QPushButton {{ background-color: {ERROR_COLOR}; color: white; "
            f"font-weight: bold; padding: 8px 16px; }}"
            f"QPushButton:checked {{ background-color: #2196F3; }}"
        )
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
        """Create Min-Max range control with editable text fields and combined slider."""
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

        # Row 2: Min spinbox + range slider + Max spinbox
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

        # Combined range slider (shows the range visually)
        self.range_slider = QSlider(Qt.Horizontal)
        self.range_slider.setRange(0, 65535)
        self.range_slider.setValue(32767)  # Middle position
        self.range_slider.setEnabled(False)
        self.range_slider.setToolTip("Drag to adjust range center")
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
        self.position_labels: Dict[str, QLabel] = {}

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

            # Axis label
            axis_label = QLabel(f"<b>{axis_name}:</b>")
            axis_label.setMinimumWidth(25)
            row.addWidget(axis_label)

            # Min value label
            min_label = QLabel("0.0")
            min_label.setStyleSheet("color: #666; font-size: 9pt;")
            min_label.setMinimumWidth(50)
            min_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(min_label)

            # Slider
            slider = QSlider(Qt.Horizontal)
            slider.setMinimum(0)
            slider.setMaximum(100000)  # Will be updated with real limits
            slider.setValue(50000)
            slider.setTickPosition(QSlider.TicksBelow)
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

            # Current value label
            value_label = QLabel(f"50.000 {unit}")
            value_label.setStyleSheet(
                "background-color: #e3f2fd; padding: 4px; "
                "border: 1px solid #2196f3; border-radius: 3px; "
                "font-weight: bold; min-width: 80px;"
            )
            value_label.setAlignment(Qt.AlignCenter)
            self.position_labels[axis_id] = value_label
            row.addWidget(value_label)

            # Store min/max labels for later updates
            slider.setProperty('min_label', min_label)
            slider.setProperty('max_label', max_label)
            slider.setProperty('unit', unit)
            slider.setProperty('decimals', decimals)

            layout.addLayout(row)

        group.setLayout(layout)
        return group

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

        # Get stage ranges from config
        stage_config = self._config.get('stage_control', {})
        x_range = tuple(stage_config.get('x_range_mm', [1.0, 12.31]))
        y_range = tuple(stage_config.get('y_range_mm', [5.0, 25.0]))
        z_range = tuple(stage_config.get('z_range_mm', [12.5, 26.0]))

        # XZ Plane (Top-Down) - X horizontal, Z vertical
        xz_group = QGroupBox("XZ Plane (Top-Down)")
        xz_layout = QVBoxLayout()
        xz_layout.setContentsMargins(4, 4, 4, 4)
        # Aspect ~11:13.5 ≈ 0.81, use 180x220
        self.xz_plane_viewer = SlicePlaneViewer('xz', 'x', 'z', 180, 220)
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
        # Aspect ~11:20 ≈ 0.55, use 130x240
        self.xy_plane_viewer = SlicePlaneViewer('xy', 'x', 'y', 130, 240)
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
        # Aspect ~13.5:20 ≈ 0.675, use 160x240
        self.yz_plane_viewer = SlicePlaneViewer('yz', 'z', 'y', 160, 240)
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

        # Channel names/colors matching the laser wavelengths
        channel_info = [
            ("Ch1 (405nm)", "#9370DB"),  # Violet
            ("Ch2 (488nm)", "#00CED1"),  # Cyan
            ("Ch3 (561nm)", "#32CD32"),  # Green
            ("Ch4 (640nm)", "#DC143C"),  # Red
        ]

        # Store widget references
        self.channel_checkboxes: Dict[int, QCheckBox] = {}
        self.channel_contrast_sliders: Dict[int, QSlider] = {}

        for i, (name, color) in enumerate(channel_info):
            row_layout = QHBoxLayout()
            row_layout.setSpacing(4)

            # Visibility checkbox with channel name (compact)
            checkbox = QCheckBox(name)
            checkbox.setChecked(True)
            checkbox.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: bold; }}")
            checkbox.stateChanged.connect(
                lambda state, ch=i: self._on_channel_visibility_changed(ch, state)
            )
            checkbox.setMinimumWidth(100)
            self.channel_checkboxes[i] = checkbox
            row_layout.addWidget(checkbox)

            # Contrast slider (takes remaining space)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(50)  # Default contrast
            slider.setToolTip(f"Adjust contrast for {name}")
            slider.valueChanged.connect(
                lambda val, ch=i: self._on_channel_contrast_changed(ch, val)
            )
            self.channel_contrast_sliders[i] = slider
            row_layout.addWidget(slider, stretch=1)

            layout.addLayout(row_layout)

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
        """Create dialog launcher button bar."""
        widget = QWidget()
        layout = QHBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(0, 4, 0, 4)

        # Saved Positions button
        self.saved_positions_btn = QPushButton("Saved Positions")
        self.saved_positions_btn.clicked.connect(self._on_saved_positions_clicked)
        layout.addWidget(self.saved_positions_btn)

        # Stage Control button
        self.stage_control_btn = QPushButton("Stage Control")
        self.stage_control_btn.clicked.connect(self._on_stage_control_clicked)
        layout.addWidget(self.stage_control_btn)

        # Export Data button
        self.export_data_btn = QPushButton("Export Data")
        self.export_data_btn.clicked.connect(self._on_export_data_clicked)
        layout.addWidget(self.export_data_btn)

        layout.addStretch()

        widget.setLayout(layout)
        return widget

    def _connect_signals(self) -> None:
        """Connect controller signals."""
        # Camera signals
        if self.camera_controller:
            self.camera_controller.new_image.connect(self._on_frame_received)
            self.camera_controller.state_changed.connect(self._on_camera_state_changed)

        # Movement signals
        if self.movement_controller:
            self.movement_controller.position_changed.connect(self._on_position_changed)

        self.logger.info("SampleView signals connected")

    def _init_stage_limits(self) -> None:
        """Initialize stage limits from movement controller."""
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

        except Exception as e:
            self.logger.error(f"Error initializing stage limits: {e}")

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
                img_min = np.min(image)
                img_max = np.max(image)
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

    @pyqtSlot(object)
    def _on_camera_state_changed(self, state) -> None:
        """Handle camera state change."""
        state_names = {0: "Idle", 1: "Starting", 2: "Running", 3: "Stopping"}
        state_name = state_names.get(state.value if hasattr(state, 'value') else state, "Unknown")
        self.live_status_label.setText(f"Status: {state_name}")

    @pyqtSlot(float, float, float, float)
    def _on_position_changed(self, x: float, y: float, z: float, r: float) -> None:
        """Handle position change from movement controller."""
        positions = {'x': x, 'y': y, 'z': z, 'r': r}

        for axis_id, value in positions.items():
            if axis_id in self.position_sliders:
                slider = self.position_sliders[axis_id]
                label = self.position_labels[axis_id]

                # Block signals to prevent feedback loop
                slider.blockSignals(True)
                slider.setValue(int(value * self._slider_scale))
                slider.blockSignals(False)

                # Update value label
                unit = slider.property('unit')
                decimals = slider.property('decimals')
                label.setText(f"{value:.{decimals}f} {unit}")

    def _on_position_slider_changed(self, axis: str, value: int) -> None:
        """Handle position slider value change (during drag)."""
        if axis in self.position_sliders:
            slider = self.position_sliders[axis]
            label = self.position_labels[axis]

            real_value = value / self._slider_scale
            unit = slider.property('unit')
            decimals = slider.property('decimals')
            label.setText(f"{real_value:.{decimals}f} {unit}")

    def _on_position_slider_released(self, axis: str) -> None:
        """Handle position slider release - send move command."""
        if not self.movement_controller:
            return

        if axis in self.position_sliders:
            slider = self.position_sliders[axis]
            real_value = slider.value() / self._slider_scale

            try:
                self.movement_controller.move_absolute(axis, real_value, verify=False)
                self.logger.info(f"Moving {axis.upper()} to {real_value:.3f}")
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
        """Handle min intensity spinbox change."""
        self._intensity_min = value
        # Ensure min doesn't exceed max
        if value > self._intensity_max:
            self.max_intensity_spinbox.setValue(value)
        self._update_live_display()

    def _on_max_spinbox_changed(self, value: int) -> None:
        """Handle max intensity spinbox change."""
        self._intensity_max = value
        # Ensure max doesn't go below min
        if value < self._intensity_min:
            self.min_intensity_spinbox.setValue(value)
        self._update_live_display()

    def _on_channel_visibility_changed(self, channel: int, state: int) -> None:
        """Handle channel visibility checkbox change."""
        visible = (state == Qt.Checked)
        self._channel_states[channel]['visible'] = visible
        self.logger.debug(f"Channel {channel} visibility: {visible}")

        # Update napari layer visibility via the 3D window's viewer
        viewer = self._get_viewer()
        if viewer:
            layer_name = f"Channel {channel + 1}"
            if layer_name in viewer.layers:
                viewer.layers[layer_name].visible = visible

    def _on_channel_contrast_changed(self, channel: int, value: int) -> None:
        """Handle channel contrast slider change."""
        # Map 0-100 slider to contrast range
        # At 50 (default), use full range [0, 65535]
        # Lower values compress the range (increase contrast)
        # Higher values expand the range (decrease contrast)
        self._channel_states[channel]['contrast'] = value

        # Calculate contrast limits based on slider value
        # value 0 = very high contrast (narrow range)
        # value 50 = normal (full range)
        # value 100 = low contrast (expanded range)
        if value <= 50:
            # Compress range: at 0, range is [0, 6553], at 50, range is [0, 65535]
            max_val = int(65535 * (value / 50.0)) if value > 0 else 6553
            contrast_limits = [0, max_val]
        else:
            # For values > 50, keep full range but could adjust brightness
            contrast_limits = [0, 65535]

        # Update napari layer contrast via the 3D window's viewer
        viewer = self._get_viewer()
        if viewer:
            layer_name = f"Channel {channel + 1}"
            if layer_name in viewer.layers:
                viewer.layers[layer_name].contrast_limits = contrast_limits

        self.logger.debug(f"Channel {channel} contrast: {value}, limits: {contrast_limits}")

    def _get_viewer(self):
        """Get the napari viewer from the 3D window."""
        if self.sample_3d_window:
            return getattr(self.sample_3d_window, 'viewer', None)
        return None

    # ========== Dialog Launchers ==========

    def _on_saved_positions_clicked(self) -> None:
        """Open saved positions dialog."""
        self.logger.info("Saved Positions clicked (not yet implemented)")
        # TODO: Open saved positions dialog

    def _on_viewer_controls_clicked(self) -> None:
        """Open viewer controls dialog (placeholder)."""
        dialog = ViewerControlsDialog(self)
        dialog.exec_()

    def _on_stage_control_clicked(self) -> None:
        """Focus the main window Stage Control tab."""
        self.logger.info("Stage Control clicked (not yet implemented)")
        # TODO: Focus main window Stage Control tab

    def _on_export_data_clicked(self) -> None:
        """Open export data dialog."""
        self.logger.info("Export Data clicked (not yet implemented)")
        # TODO: Open export dialog

    # ========== 3D Viewer Integration (reuses existing Sample3DVisualizationWindow) ==========

    def _embed_3d_viewer(self) -> None:
        """Embed the napari viewer from the existing Sample3DVisualizationWindow."""
        if not self.sample_3d_window:
            self.logger.warning("No sample_3d_window available - 3D viewer not embedded")
            return

        try:
            # Get the viewer from the existing 3D window
            viewer = getattr(self.sample_3d_window, 'viewer', None)
            if not viewer:
                self.logger.warning("Sample3DVisualizationWindow has no viewer")
                return

            # Get the napari Qt widget
            napari_window = viewer.window
            viewer_widget = napari_window._qt_viewer

            # Replace placeholder with actual viewer
            if hasattr(self, 'viewer_placeholder') and self.viewer_placeholder:
                parent_widget = self.viewer_placeholder.parent()
                if parent_widget:
                    layout = parent_widget.layout()
                    if layout:
                        layout.replaceWidget(self.viewer_placeholder, viewer_widget)
                        self.viewer_placeholder.deleteLater()
                        self.viewer_placeholder = None

            self.logger.info("Embedded existing 3D viewer from Sample3DVisualizationWindow")

        except Exception as e:
            self.logger.error(f"Failed to embed 3D viewer: {e}")
            import traceback
            traceback.print_exc()

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
                self.live_view_toggle_btn.setText("Start Live View")
                self.live_view_toggle_btn.setStyleSheet(
                    f"QPushButton {{ background-color: {ERROR_COLOR}; color: white; "
                    f"font-weight: bold; padding: 8px 16px; }}"
                )
                self.logger.info("Live view stopped")
            else:
                # Start live view
                self.camera_controller.start_live_view()
                self._live_view_active = True
                self.live_view_toggle_btn.setChecked(True)
                self.live_view_toggle_btn.setText("Stop Live View")
                self.live_view_toggle_btn.setStyleSheet(
                    f"QPushButton {{ background-color: #2196F3; color: white; "
                    f"font-weight: bold; padding: 8px 16px; }}"
                )
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
                self.live_view_toggle_btn.setText("Stop Live View")
                self.live_view_toggle_btn.setStyleSheet(
                    f"QPushButton {{ background-color: #2196F3; color: white; "
                    f"font-weight: bold; padding: 8px 16px; }}"
                )
            else:
                self.live_view_toggle_btn.setChecked(False)
                self.live_view_toggle_btn.setText("Start Live View")
                self.live_view_toggle_btn.setStyleSheet(
                    f"QPushButton {{ background-color: {ERROR_COLOR}; color: white; "
                    f"font-weight: bold; padding: 8px 16px; }}"
                )
        except Exception as e:
            self.logger.error(f"Error updating live view state: {e}")

    def _on_live_settings_clicked(self) -> None:
        """Open Live Display (image controls) window for advanced settings."""
        if self.image_controls_window:
            self.image_controls_window.show()
            self.image_controls_window.raise_()
        else:
            self.logger.info("Live View Settings clicked (window not available)")

    def _on_plane_click(self, plane: str, h_coord: float, v_coord: float) -> None:
        """Handle click-to-move from plane viewers."""
        if not self.movement_controller:
            return

        try:
            # Map plane coordinates to axis movements
            if plane == 'xz':
                # XZ plane: h=X, v=Z
                self.movement_controller.move_absolute('x', h_coord, verify=False)
                self.movement_controller.move_absolute('z', v_coord, verify=False)
                self.logger.info(f"Moving to X={h_coord:.3f}, Z={v_coord:.3f}")
            elif plane == 'xy':
                # XY plane: h=X, v=Y
                self.movement_controller.move_absolute('x', h_coord, verify=False)
                self.movement_controller.move_absolute('y', v_coord, verify=False)
                self.logger.info(f"Moving to X={h_coord:.3f}, Y={v_coord:.3f}")
            elif plane == 'yz':
                # YZ plane: h=Z, v=Y
                self.movement_controller.move_absolute('z', h_coord, verify=False)
                self.movement_controller.move_absolute('y', v_coord, verify=False)
                self.logger.info(f"Moving to Z={h_coord:.3f}, Y={v_coord:.3f}")
        except Exception as e:
            self.logger.error(f"Error moving from plane click: {e}")

    def _update_plane_views(self) -> None:
        """Update the MIP (Maximum Intensity Projection) plane views from voxel data."""
        if not self.voxel_storage:
            return

        try:
            # Get current data from voxel storage (if available)
            data = self.voxel_storage.get_display_data() if hasattr(self.voxel_storage, 'get_display_data') else None

            if data is None or data.size == 0:
                return

            # Get contrast limits from 3D viewer (use first channel as reference)
            contrast_limits = (0, 65535)
            viewer = self._get_viewer()
            if viewer and len(viewer.layers) > 0:
                for layer in viewer.layers:
                    if hasattr(layer, 'contrast_limits'):
                        contrast_limits = tuple(layer.contrast_limits)
                        break

            # Generate MIP projections and update viewers
            # Data is in (Z, Y, X) order

            # XZ plane (top-down) - project along Y axis (axis 1)
            xz_mip = np.max(data, axis=1)
            self.xz_plane_viewer.set_contrast_limits(contrast_limits)
            self.xz_plane_viewer.set_mip_data(xz_mip)

            # XY plane (front view) - project along Z axis (axis 0)
            xy_mip = np.max(data, axis=0)
            self.xy_plane_viewer.set_contrast_limits(contrast_limits)
            self.xy_plane_viewer.set_mip_data(xy_mip)

            # YZ plane (side view) - project along X axis (axis 2)
            yz_mip = np.max(data, axis=2)
            self.yz_plane_viewer.set_contrast_limits(contrast_limits)
            self.yz_plane_viewer.set_mip_data(yz_mip)

        except Exception as e:
            self.logger.error(f"Error updating plane views: {e}")

    def _update_plane_overlays(self) -> None:
        """Update overlay positions on all plane viewers."""
        if not self.sample_3d_window:
            return

        try:
            # Get current stage position from movement controller
            if self.movement_controller:
                pos = self.movement_controller.get_current_position()
                if pos:
                    x, y, z = pos.get('x', 0), pos.get('y', 0), pos.get('z', 0)

                    # Update holder position on each plane
                    self.xz_plane_viewer.set_holder_position(x, z)
                    self.xy_plane_viewer.set_holder_position(x, y)
                    self.yz_plane_viewer.set_holder_position(z, y)

            # Get objective position from config
            stage_config = self._config.get('stage_control', {})
            obj_x = (stage_config.get('x_range_mm', [1, 12.31])[0] +
                     stage_config.get('x_range_mm', [1, 12.31])[1]) / 2
            obj_y = (stage_config.get('y_range_mm', [5, 25])[0] +
                     stage_config.get('y_range_mm', [5, 25])[1]) / 2
            obj_z = stage_config.get('z_range_mm', [12.5, 26])[0]  # Objective at back

            # Update objective position on each plane
            self.xz_plane_viewer.set_objective_position(obj_x, obj_z)
            self.xy_plane_viewer.set_objective_position(obj_x, obj_y)
            self.yz_plane_viewer.set_objective_position(obj_z, obj_y)

        except Exception as e:
            self.logger.error(f"Error updating plane overlays: {e}")

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
