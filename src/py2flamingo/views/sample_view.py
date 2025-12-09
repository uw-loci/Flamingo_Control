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
from typing import Optional, Dict, Any
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QSlider, QComboBox, QCheckBox, QProgressBar,
    QSplitter, QSizePolicy, QFrame
)
from PyQt5.QtCore import Qt, pyqtSlot, QTimer
from PyQt5.QtGui import QPixmap, QImage, QFont

from py2flamingo.views.laser_led_control_panel import LaserLEDControlPanel
from py2flamingo.views.colors import SUCCESS_COLOR, ERROR_COLOR, WARNING_BG


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
            parent: Parent widget
        """
        super().__init__(parent)

        self.camera_controller = camera_controller
        self.movement_controller = movement_controller
        self.laser_led_controller = laser_led_controller
        self.voxel_storage = voxel_storage
        self.image_controls_window = image_controls_window
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

        # Setup window
        self.setWindowTitle("Sample View")
        self.setMinimumSize(1200, 850)
        self.resize(1400, 950)

        # Setup UI
        self._setup_ui()

        # Connect signals
        self._connect_signals()

        # Initialize stage limits
        self._init_stage_limits()

        self.logger.info("SampleView initialized")

    def _setup_ui(self) -> None:
        """Create and layout all UI components."""
        main_layout = QVBoxLayout()
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # ========== TOP SECTION: Live + 3D + Controls ==========
        top_section = QHBoxLayout()

        # ----- Left Column: Live Camera + Display Controls + Illumination -----
        left_column = QVBoxLayout()
        left_column.setSpacing(6)

        # Live Camera Feed
        left_column.addWidget(self._create_live_feed_section())

        # Display Controls (embedded)
        left_column.addWidget(self._create_display_controls())

        # Illumination Controls
        left_column.addWidget(self._create_illumination_section())

        left_widget = QWidget()
        left_widget.setLayout(left_column)
        left_widget.setMaximumWidth(680)
        top_section.addWidget(left_widget)

        # ----- Right Column: 3D View + Position Sliders -----
        right_column = QVBoxLayout()
        right_column.setSpacing(6)

        # 3D Volume View (placeholder for now)
        right_column.addWidget(self._create_3d_view_section(), stretch=1)

        # Position Sliders
        right_column.addWidget(self._create_position_sliders())

        right_widget = QWidget()
        right_widget.setLayout(right_column)
        top_section.addWidget(right_widget, stretch=1)

        main_layout.addLayout(top_section, stretch=1)

        # ========== MIDDLE SECTION: Plane Views ==========
        main_layout.addWidget(self._create_plane_views_section())

        # ========== BOTTOM SECTION: Workflow + Buttons ==========
        main_layout.addWidget(self._create_workflow_progress())
        main_layout.addWidget(self._create_button_bar())

        self.setLayout(main_layout)

    def _create_live_feed_section(self) -> QGroupBox:
        """Create the live camera feed display section."""
        group = QGroupBox("Live Camera Feed")
        layout = QVBoxLayout()
        layout.setSpacing(4)

        # Image display label
        self.live_image_label = QLabel("No image - Start live view from main window")
        self.live_image_label.setAlignment(Qt.AlignCenter)
        self.live_image_label.setMinimumSize(640, 480)
        self.live_image_label.setMaximumSize(640, 480)
        self.live_image_label.setStyleSheet(
            "QLabel { background-color: black; color: gray; border: 1px solid #444; }"
        )
        self.live_image_label.setScaledContents(False)
        layout.addWidget(self.live_image_label)

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

    def _create_display_controls(self) -> QWidget:
        """Create embedded display controls for live view."""
        widget = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)

        # Colormap selector
        layout.addWidget(QLabel("Display:"))
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(["Grayscale", "Hot", "Viridis", "Plasma", "Inferno"])
        self.colormap_combo.setCurrentText("Grayscale")
        self.colormap_combo.currentTextChanged.connect(self._on_colormap_changed)
        self.colormap_combo.setMaximumWidth(100)
        layout.addWidget(self.colormap_combo)

        # Auto-scale checkbox
        self.auto_scale_checkbox = QCheckBox("Auto")
        self.auto_scale_checkbox.setChecked(True)
        self.auto_scale_checkbox.stateChanged.connect(self._on_auto_scale_changed)
        layout.addWidget(self.auto_scale_checkbox)

        # Min intensity
        layout.addWidget(QLabel("Min:"))
        self.min_intensity_slider = QSlider(Qt.Horizontal)
        self.min_intensity_slider.setRange(0, 65535)
        self.min_intensity_slider.setValue(0)
        self.min_intensity_slider.setMaximumWidth(80)
        self.min_intensity_slider.valueChanged.connect(self._on_min_intensity_changed)
        self.min_intensity_slider.setEnabled(False)
        layout.addWidget(self.min_intensity_slider)

        # Max intensity
        layout.addWidget(QLabel("Max:"))
        self.max_intensity_slider = QSlider(Qt.Horizontal)
        self.max_intensity_slider.setRange(0, 65535)
        self.max_intensity_slider.setValue(65535)
        self.max_intensity_slider.setMaximumWidth(80)
        self.max_intensity_slider.valueChanged.connect(self._on_max_intensity_changed)
        self.max_intensity_slider.setEnabled(False)
        layout.addWidget(self.max_intensity_slider)

        layout.addStretch()

        widget.setLayout(layout)
        widget.setStyleSheet("background-color: #f0f0f0; border-radius: 4px;")
        return widget

    def _create_illumination_section(self) -> QGroupBox:
        """Create illumination controls section."""
        group = QGroupBox("Illumination")

        # Use the existing LaserLEDControlPanel
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)

        self.laser_led_panel = LaserLEDControlPanel(self.laser_led_controller)
        layout.addWidget(self.laser_led_panel)

        group.setLayout(layout)
        return group

    def _create_3d_view_section(self) -> QGroupBox:
        """Create 3D volume view section (placeholder for napari)."""
        group = QGroupBox("3D Volume View")
        layout = QVBoxLayout()

        # Placeholder for napari viewer
        self.viewer_placeholder = QLabel("3D Napari Viewer\n(Will be integrated)")
        self.viewer_placeholder.setAlignment(Qt.AlignCenter)
        self.viewer_placeholder.setStyleSheet(
            "QLabel { background-color: #1a1a2e; color: #888; "
            "border: 2px dashed #444; font-size: 14pt; }"
        )
        self.viewer_placeholder.setMinimumHeight(400)
        layout.addWidget(self.viewer_placeholder)

        group.setLayout(layout)
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
        """Create the three MIP plane views section."""
        widget = QWidget()
        layout = QHBoxLayout()
        layout.setSpacing(8)

        # XZ Plane (Top-Down)
        xz_group = QGroupBox("XZ Plane (Top-Down)")
        xz_layout = QVBoxLayout()
        self.xz_plane_label = QLabel("MIP View\n(Click to move X,Z)")
        self.xz_plane_label.setAlignment(Qt.AlignCenter)
        self.xz_plane_label.setMinimumSize(400, 180)
        self.xz_plane_label.setStyleSheet(
            "QLabel { background-color: #1a1a1a; color: #666; border: 1px solid #444; }"
        )
        xz_layout.addWidget(self.xz_plane_label)
        xz_group.setLayout(xz_layout)
        layout.addWidget(xz_group)

        # XY Plane (Side View)
        xy_group = QGroupBox("XY Plane (Side View)")
        xy_layout = QVBoxLayout()
        self.xy_plane_label = QLabel("MIP View\n(Click to move X,Y)")
        self.xy_plane_label.setAlignment(Qt.AlignCenter)
        self.xy_plane_label.setMinimumSize(400, 180)
        self.xy_plane_label.setStyleSheet(
            "QLabel { background-color: #1a1a1a; color: #666; border: 1px solid #444; }"
        )
        xy_layout.addWidget(self.xy_plane_label)
        xy_group.setLayout(xy_layout)
        layout.addWidget(xy_group)

        # YZ Plane (End View)
        yz_group = QGroupBox("YZ Plane (End View)")
        yz_layout = QVBoxLayout()
        self.yz_plane_label = QLabel("MIP View\n(Click to move Y,Z)")
        self.yz_plane_label.setAlignment(Qt.AlignCenter)
        self.yz_plane_label.setMinimumSize(280, 180)
        self.yz_plane_label.setStyleSheet(
            "QLabel { background-color: #1a1a1a; color: #666; border: 1px solid #444; }"
        )
        yz_layout.addWidget(self.yz_plane_label)
        yz_group.setLayout(yz_layout)
        layout.addWidget(yz_group)

        widget.setLayout(layout)
        return widget

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
        layout.setSpacing(12)

        # Saved Positions button
        self.saved_positions_btn = QPushButton("Saved Positions")
        self.saved_positions_btn.clicked.connect(self._on_saved_positions_clicked)
        layout.addWidget(self.saved_positions_btn)

        # Image Settings button (advanced controls)
        self.image_settings_btn = QPushButton("Image Settings")
        self.image_settings_btn.clicked.connect(self._on_image_settings_clicked)
        layout.addWidget(self.image_settings_btn)

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

                    if min_label:
                        min_label.setText(f"{min_val:.{decimals}f}")
                    if max_label:
                        max_label.setText(f"{max_val:.{decimals}f}")

            self.logger.info("Stage limits initialized for position sliders")

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
        self.min_intensity_slider.setEnabled(not self._auto_scale)
        self.max_intensity_slider.setEnabled(not self._auto_scale)
        self._update_live_display()

    def _on_min_intensity_changed(self, value: int) -> None:
        """Handle min intensity slider change."""
        self._intensity_min = value
        self._update_live_display()

    def _on_max_intensity_changed(self, value: int) -> None:
        """Handle max intensity slider change."""
        self._intensity_max = value
        self._update_live_display()

    # ========== Dialog Launchers ==========

    def _on_saved_positions_clicked(self) -> None:
        """Open saved positions dialog."""
        self.logger.info("Saved Positions clicked (not yet implemented)")
        # TODO: Open saved positions dialog

    def _on_image_settings_clicked(self) -> None:
        """Open image controls window for advanced settings."""
        if self.image_controls_window:
            self.image_controls_window.show()
            self.image_controls_window.raise_()
        else:
            self.logger.info("Image Settings clicked (window not available)")

    def _on_stage_control_clicked(self) -> None:
        """Focus the main window Stage Control tab."""
        self.logger.info("Stage Control clicked (not yet implemented)")
        # TODO: Focus main window Stage Control tab

    def _on_export_data_clicked(self) -> None:
        """Open export data dialog."""
        self.logger.info("Export Data clicked (not yet implemented)")
        # TODO: Open export dialog

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
