"""
Live feed view for displaying microscope images.

This module provides the LiveFeedView widget that displays live images from
the microscope with user-configurable transformations. Updates are suppressed
during workflow execution.
"""
import logging
from typing import Optional
from queue import Queue, Empty

import numpy as np
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QComboBox, QGroupBox, QSlider, QSizePolicy,
    QDoubleSpinBox, QSpinBox, QLineEdit, QScrollArea
)
from PyQt5.QtCore import QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QPixmap

from ..models import ImageDisplayModel
from ..models.microscope import Position
from ..controllers import WorkflowController
from ..utils.image_transforms import Rotation, Colormap, apply_transforms
from ..utils.image_processing import convert_to_qimage


class LiveFeedView(QWidget):
    """
    Widget for displaying live microscope feed with image transformations.

    This view polls the visualize_queue for new images, applies user-configured
    transformations, and displays them. Display is suppressed when a workflow
    is running to avoid interfering with acquisition.

    Attributes:
        workflow_controller: Controller to check if workflow is running
        visualize_queue: Queue containing images to display
        display_model: Model tracking display transformation settings
        update_interval_ms: How often to poll for new images (default: 500ms)
    """

    # Signal emitted when new image is ready for display (thread-safe)
    image_ready = pyqtSignal(object)  # numpy array

    # Signals for stage control
    move_position_requested = pyqtSignal(Position)  # Move to absolute position
    move_relative_requested = pyqtSignal(str, float)  # Move axis by relative amount

    # Signals for laser control
    laser_changed = pyqtSignal(str)  # Laser channel name
    laser_power_changed = pyqtSignal(float)  # Laser power percentage

    # Signals for image acquisition
    snapshot_requested = pyqtSignal()
    brightfield_requested = pyqtSignal()

    # Signal for settings sync
    sync_settings_requested = pyqtSignal()

    def __init__(self,
                 workflow_controller: WorkflowController,
                 visualize_queue: Queue,
                 display_model: Optional[ImageDisplayModel] = None,
                 update_interval_ms: int = 500,
                 position_controller=None,
                 image_acquisition_service=None,
                 initialization_service=None):
        """
        Initialize live feed view.

        Args:
            workflow_controller: Controller to check workflow state
            visualize_queue: Queue with images from microscope
            display_model: Display settings model (creates default if None)
            update_interval_ms: Poll interval in milliseconds
            position_controller: Controller for stage movement (optional)
            image_acquisition_service: Service for image acquisition (optional)
            initialization_service: Service for settings initialization (optional)
        """
        super().__init__()

        self.workflow_controller = workflow_controller
        self.visualize_queue = visualize_queue
        self.display_model = display_model or ImageDisplayModel()
        self.update_interval_ms = update_interval_ms
        self.position_controller = position_controller
        self.image_acquisition_service = image_acquisition_service
        self.initialization_service = initialization_service

        self._logger = logging.getLogger(__name__)
        self._last_image: Optional[np.ndarray] = None
        self._frame_count = 0
        self._current_position = Position(x=0.0, y=0.0, z=0.0, r=0.0)

        # Available laser channels (will be populated from microscope settings)
        self._laser_channels = [
            "Laser 1 405 nm",
            "Laser 2 445 nm",
            "Laser 3 488 nm",
            "Laser 4 561 nm",
            "Laser 5 638 nm"
        ]

        # Setup UI
        self.setup_ui()

        # Connect signal for thread-safe updates
        self.image_ready.connect(self._display_image)

        # Start timer to poll for images
        self.timer = QTimer()
        self.timer.timeout.connect(self._check_for_image)
        self.timer.start(self.update_interval_ms)

        self._logger.info(f"LiveFeedView initialized (poll interval: {update_interval_ms}ms)")

    def setup_ui(self) -> None:
        """Create and layout UI components."""
        # Main layout for the widget
        main_layout = QVBoxLayout()

        # Create scroll area to handle overflow
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # Create content widget for scroll area
        content_widget = QWidget()
        content_layout = QHBoxLayout()

        # LEFT SIDE: Image display area
        left_layout = QVBoxLayout()

        display_group = QGroupBox("Live Feed")
        display_layout = QVBoxLayout()

        self.image_label = QLabel("No image")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(512, 512)
        self.image_label.setMaximumSize(800, 800)
        self.image_label.setScaledContents(False)
        self.image_label.setStyleSheet("QLabel { background-color: black; color: gray; }")
        display_layout.addWidget(self.image_label)

        # Status label
        self.status_label = QLabel("Status: Waiting for images...")
        self.status_label.setStyleSheet("color: gray; font-style: italic;")
        display_layout.addWidget(self.status_label)

        display_group.setLayout(display_layout)
        left_layout.addWidget(display_group)
        left_layout.addStretch()

        # RIGHT SIDE: All controls
        right_layout = QVBoxLayout()

        # Transform controls
        controls_group = QGroupBox("Image Transformations")
        controls_layout = QVBoxLayout()

        # Rotation controls
        rotation_layout = QHBoxLayout()
        rotation_layout.addWidget(QLabel("Rotation:"))

        self.rotate_0_btn = QPushButton("0°")
        self.rotate_0_btn.setCheckable(True)
        self.rotate_0_btn.setChecked(True)
        self.rotate_0_btn.clicked.connect(lambda: self._set_rotation(0))
        rotation_layout.addWidget(self.rotate_0_btn)

        self.rotate_90_btn = QPushButton("90°")
        self.rotate_90_btn.setCheckable(True)
        self.rotate_90_btn.clicked.connect(lambda: self._set_rotation(90))
        rotation_layout.addWidget(self.rotate_90_btn)

        self.rotate_180_btn = QPushButton("180°")
        self.rotate_180_btn.setCheckable(True)
        self.rotate_180_btn.clicked.connect(lambda: self._set_rotation(180))
        rotation_layout.addWidget(self.rotate_180_btn)

        self.rotate_270_btn = QPushButton("270°")
        self.rotate_270_btn.setCheckable(True)
        self.rotate_270_btn.clicked.connect(lambda: self._set_rotation(270))
        rotation_layout.addWidget(self.rotate_270_btn)

        rotation_layout.addStretch()
        controls_layout.addLayout(rotation_layout)

        # Flip controls
        flip_layout = QHBoxLayout()
        flip_layout.addWidget(QLabel("Flip:"))

        self.flip_h_check = QCheckBox("Horizontal")
        self.flip_h_check.stateChanged.connect(self._on_flip_changed)
        flip_layout.addWidget(self.flip_h_check)

        self.flip_v_check = QCheckBox("Vertical")
        self.flip_v_check.stateChanged.connect(self._on_flip_changed)
        flip_layout.addWidget(self.flip_v_check)

        flip_layout.addStretch()
        controls_layout.addLayout(flip_layout)

        # Downsample controls
        downsample_layout = QHBoxLayout()
        downsample_layout.addWidget(QLabel("Downsample:"))

        self.downsample_slider = QSlider(Qt.Horizontal)
        self.downsample_slider.setMinimum(0)  # 2^0 = 1
        self.downsample_slider.setMaximum(3)  # 2^3 = 8
        self.downsample_slider.setValue(0)
        self.downsample_slider.setTickPosition(QSlider.TicksBelow)
        self.downsample_slider.setTickInterval(1)
        self.downsample_slider.valueChanged.connect(self._on_downsample_changed)
        downsample_layout.addWidget(self.downsample_slider)

        self.downsample_label = QLabel("1x")
        self.downsample_label.setMinimumWidth(40)
        downsample_layout.addWidget(self.downsample_label)

        controls_layout.addLayout(downsample_layout)

        # Colormap controls
        colormap_layout = QHBoxLayout()
        colormap_layout.addWidget(QLabel("Colormap:"))

        self.colormap_combo = QComboBox()
        for cmap in Colormap:
            self.colormap_combo.addItem(cmap.value.capitalize(), cmap)
        self.colormap_combo.currentIndexChanged.connect(self._on_colormap_changed)
        colormap_layout.addWidget(self.colormap_combo)

        colormap_layout.addStretch()
        controls_layout.addLayout(colormap_layout)

        # Reset button
        reset_layout = QHBoxLayout()
        reset_layout.addStretch()

        reset_btn = QPushButton("Reset All")
        reset_btn.clicked.connect(self._reset_transforms)
        reset_layout.addWidget(reset_btn)

        controls_layout.addLayout(reset_layout)

        controls_group.setLayout(controls_layout)
        right_layout.addWidget(controls_group)

        # Stage control group
        stage_group = QGroupBox("Stage Control")
        stage_layout = QVBoxLayout()

        # Current position display
        position_display_layout = QHBoxLayout()
        position_display_layout.addWidget(QLabel("Current Position:"))
        self.position_label = QLabel("X: 0.00 Y: 0.00 Z: 0.00 R: 0.00°")
        self.position_label.setStyleSheet("font-weight: bold; color: blue;")
        position_display_layout.addWidget(self.position_label)
        position_display_layout.addStretch()
        stage_layout.addLayout(position_display_layout)

        # X-axis control
        x_layout = QHBoxLayout()
        x_layout.addWidget(QLabel("X (mm):"))
        self.x_spinbox = QDoubleSpinBox()
        self.x_spinbox.setRange(-100.0, 100.0)
        self.x_spinbox.setDecimals(3)
        self.x_spinbox.setSingleStep(0.1)
        self.x_spinbox.setValue(0.0)
        x_layout.addWidget(self.x_spinbox)
        self.x_minus_btn = QPushButton("-0.1")
        self.x_minus_btn.clicked.connect(lambda: self._move_relative('X', -0.1))
        x_layout.addWidget(self.x_minus_btn)
        self.x_plus_btn = QPushButton("+0.1")
        self.x_plus_btn.clicked.connect(lambda: self._move_relative('X', 0.1))
        x_layout.addWidget(self.x_plus_btn)
        stage_layout.addLayout(x_layout)

        # Y-axis control
        y_layout = QHBoxLayout()
        y_layout.addWidget(QLabel("Y (mm):"))
        self.y_spinbox = QDoubleSpinBox()
        self.y_spinbox.setRange(-100.0, 100.0)
        self.y_spinbox.setDecimals(3)
        self.y_spinbox.setSingleStep(0.1)
        self.y_spinbox.setValue(0.0)
        y_layout.addWidget(self.y_spinbox)
        self.y_minus_btn = QPushButton("-0.1")
        self.y_minus_btn.clicked.connect(lambda: self._move_relative('Y', -0.1))
        y_layout.addWidget(self.y_minus_btn)
        self.y_plus_btn = QPushButton("+0.1")
        self.y_plus_btn.clicked.connect(lambda: self._move_relative('Y', 0.1))
        y_layout.addWidget(self.y_plus_btn)
        stage_layout.addLayout(y_layout)

        # Z-axis control
        z_layout = QHBoxLayout()
        z_layout.addWidget(QLabel("Z (mm):"))
        self.z_spinbox = QDoubleSpinBox()
        self.z_spinbox.setRange(-100.0, 100.0)
        self.z_spinbox.setDecimals(3)
        self.z_spinbox.setSingleStep(0.01)
        self.z_spinbox.setValue(0.0)
        z_layout.addWidget(self.z_spinbox)
        self.z_minus_btn = QPushButton("-0.01")
        self.z_minus_btn.clicked.connect(lambda: self._move_relative('Z', -0.01))
        z_layout.addWidget(self.z_minus_btn)
        self.z_plus_btn = QPushButton("+0.01")
        self.z_plus_btn.clicked.connect(lambda: self._move_relative('Z', 0.01))
        z_layout.addWidget(self.z_plus_btn)
        stage_layout.addLayout(z_layout)

        # R-axis (rotation) control
        r_layout = QHBoxLayout()
        r_layout.addWidget(QLabel("R (deg):"))
        self.r_spinbox = QDoubleSpinBox()
        self.r_spinbox.setRange(-720.0, 720.0)
        self.r_spinbox.setDecimals(1)
        self.r_spinbox.setSingleStep(1.0)
        self.r_spinbox.setValue(0.0)
        r_layout.addWidget(self.r_spinbox)
        self.r_minus_btn = QPushButton("-1°")
        self.r_minus_btn.clicked.connect(lambda: self._move_relative('R', -1.0))
        r_layout.addWidget(self.r_minus_btn)
        self.r_plus_btn = QPushButton("+1°")
        self.r_plus_btn.clicked.connect(lambda: self._move_relative('R', 1.0))
        r_layout.addWidget(self.r_plus_btn)
        stage_layout.addLayout(r_layout)

        # Move to position button
        move_btn_layout = QHBoxLayout()
        self.move_to_position_btn = QPushButton("Move to Position")
        self.move_to_position_btn.clicked.connect(self._on_move_to_position)
        self.move_to_position_btn.setStyleSheet("font-weight: bold;")
        move_btn_layout.addWidget(self.move_to_position_btn)
        stage_layout.addLayout(move_btn_layout)

        stage_group.setLayout(stage_layout)
        right_layout.addWidget(stage_group)

        # Laser control group
        laser_group = QGroupBox("Laser Control")
        laser_layout = QVBoxLayout()

        # Laser selection
        laser_select_layout = QHBoxLayout()
        laser_select_layout.addWidget(QLabel("Laser Channel:"))
        self.laser_combo = QComboBox()
        for laser in self._laser_channels:
            self.laser_combo.addItem(laser)
        self.laser_combo.setCurrentIndex(2)  # Default to Laser 3 488 nm
        self.laser_combo.currentTextChanged.connect(self._on_laser_changed)
        laser_select_layout.addWidget(self.laser_combo)
        laser_layout.addLayout(laser_select_layout)

        # Laser power control
        power_layout = QHBoxLayout()
        power_layout.addWidget(QLabel("Power (%):"))
        self.power_spinbox = QDoubleSpinBox()
        self.power_spinbox.setRange(0.0, 100.0)
        self.power_spinbox.setDecimals(2)
        self.power_spinbox.setSingleStep(0.5)
        self.power_spinbox.setValue(5.0)
        self.power_spinbox.valueChanged.connect(self._on_laser_power_changed)
        power_layout.addWidget(self.power_spinbox)

        self.power_slider = QSlider(Qt.Horizontal)
        self.power_slider.setRange(0, 100)
        self.power_slider.setValue(5)
        self.power_slider.valueChanged.connect(lambda v: self.power_spinbox.setValue(v))
        power_layout.addWidget(self.power_slider)
        laser_layout.addLayout(power_layout)

        laser_group.setLayout(laser_layout)
        right_layout.addWidget(laser_group)

        # Image acquisition group
        acquisition_group = QGroupBox("Image Acquisition")
        acquisition_layout = QVBoxLayout()

        # Acquisition buttons
        acq_btn_layout = QHBoxLayout()

        self.snapshot_btn = QPushButton("Take Snapshot")
        self.snapshot_btn.clicked.connect(self._on_snapshot_clicked)
        self.snapshot_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        acq_btn_layout.addWidget(self.snapshot_btn)

        self.brightfield_btn = QPushButton("Acquire Brightfield")
        self.brightfield_btn.clicked.connect(self._on_brightfield_clicked)
        self.brightfield_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        acq_btn_layout.addWidget(self.brightfield_btn)

        acquisition_layout.addLayout(acq_btn_layout)

        # Sync settings button
        sync_layout = QHBoxLayout()
        self.sync_settings_btn = QPushButton("Sync Settings from Microscope")
        self.sync_settings_btn.clicked.connect(self._on_sync_settings)
        self.sync_settings_btn.setToolTip("Pull current settings from microscope and update GUI")
        sync_layout.addWidget(self.sync_settings_btn)
        acquisition_layout.addLayout(sync_layout)

        acquisition_group.setLayout(acquisition_layout)
        right_layout.addWidget(acquisition_group)

        # Add stretch to push controls to top on right side
        right_layout.addStretch()

        # Combine left and right layouts
        content_layout.addLayout(left_layout)
        content_layout.addLayout(right_layout)

        # Set up scroll area
        content_widget.setLayout(content_layout)
        scroll_area.setWidget(content_widget)

        # Add scroll area to main layout
        main_layout.addWidget(scroll_area)

        # Set main layout on the widget
        self.setLayout(main_layout)

    def _check_for_image(self) -> None:
        """
        Poll visualize_queue for new images.

        Called by timer every update_interval_ms.
        Suppresses display if workflow is running.
        """
        # Check if workflow is running - suppress display during acquisition
        if self.workflow_controller.is_workflow_running():
            self.status_label.setText("Status: Workflow running (display suppressed)")
            self.status_label.setStyleSheet("color: orange; font-style: italic;")
            return

        # Try to get image from queue
        try:
            image = self.visualize_queue.get_nowait()

            if image is not None:
                # Emit signal for thread-safe display
                self.image_ready.emit(image)

        except Empty:
            # No image available - this is normal
            pass

    def _display_image(self, image: np.ndarray) -> None:
        """
        Display image with transformations applied.

        This is a Qt slot called via signal for thread-safety.

        Args:
            image: Raw image from microscope (typically uint16, 512x512)
        """
        try:
            # Store raw image
            self._last_image = image
            self._frame_count += 1

            # Apply transformations
            transformed = apply_transforms(
                image,
                rotation=self.display_model.rotation,
                flip_horizontal=self.display_model.flip_horizontal,
                flip_vertical=self.display_model.flip_vertical,
                downsample_factor=self.display_model.downsample_factor,
                colormap=self.display_model.colormap,
                normalize=True
            )

            # Convert to QImage for display
            if transformed.ndim == 2:
                # Grayscale uint8
                qimage = convert_to_qimage(transformed)
            elif transformed.ndim == 3 and transformed.shape[2] == 3:
                # RGB uint8 from colormap
                from PyQt5.QtGui import QImage
                h, w, c = transformed.shape
                bytes_per_line = w * 3
                qimage = QImage(transformed.data, w, h, bytes_per_line,
                              QImage.Format_RGB888).copy()
            else:
                self._logger.error(f"Unexpected image shape: {transformed.shape}")
                return

            # Convert to pixmap and display
            pixmap = QPixmap.fromImage(qimage)

            # Scale to fit label while maintaining aspect ratio
            scaled_pixmap = pixmap.scaled(
                self.image_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )

            self.image_label.setPixmap(scaled_pixmap)

            # Update status
            self.status_label.setText(f"Status: Live ({self._frame_count} frames)")
            self.status_label.setStyleSheet("color: green; font-style: italic;")

        except Exception as e:
            self._logger.exception(f"Error displaying image: {e}")
            self.status_label.setText(f"Status: Error - {str(e)}")
            self.status_label.setStyleSheet("color: red; font-style: italic;")

    def _set_rotation(self, degrees: int) -> None:
        """
        Set rotation angle and update display.

        Args:
            degrees: Rotation in degrees (0, 90, 180, 270)
        """
        # Update model
        self.display_model.set_rotation(degrees)

        # Update button states
        self.rotate_0_btn.setChecked(degrees == 0)
        self.rotate_90_btn.setChecked(degrees == 90)
        self.rotate_180_btn.setChecked(degrees == 180)
        self.rotate_270_btn.setChecked(degrees == 270)

        # Redisplay last image with new rotation
        if self._last_image is not None:
            self._display_image(self._last_image)

    def _on_flip_changed(self) -> None:
        """Handle flip checkbox changes."""
        self.display_model.flip_horizontal = self.flip_h_check.isChecked()
        self.display_model.flip_vertical = self.flip_v_check.isChecked()

        # Redisplay last image
        if self._last_image is not None:
            self._display_image(self._last_image)

    def _on_downsample_changed(self, value: int) -> None:
        """
        Handle downsample slider changes.

        Args:
            value: Slider value (0-3, representing 2^value downsampling)
        """
        # Convert slider value to downsample factor: 2^value
        factor = 2 ** value
        self.display_model.downsample_factor = factor
        self.downsample_label.setText(f"{factor}x")

        # Redisplay last image
        if self._last_image is not None:
            self._display_image(self._last_image)

    def _on_colormap_changed(self, index: int) -> None:
        """Handle colormap selection change."""
        colormap = self.colormap_combo.itemData(index)
        self.display_model.colormap = colormap

        # Redisplay last image
        if self._last_image is not None:
            self._display_image(self._last_image)

    def _reset_transforms(self) -> None:
        """Reset all transformations to defaults."""
        self.display_model.reset()

        # Update UI to match
        self.rotate_0_btn.setChecked(True)
        self.rotate_90_btn.setChecked(False)
        self.rotate_180_btn.setChecked(False)
        self.rotate_270_btn.setChecked(False)
        self.flip_h_check.setChecked(False)
        self.flip_v_check.setChecked(False)
        self.downsample_slider.setValue(0)
        self.colormap_combo.setCurrentIndex(0)  # Gray

        # Redisplay last image
        if self._last_image is not None:
            self._display_image(self._last_image)

    def start_updates(self) -> None:
        """Start polling for images (if not already started)."""
        if not self.timer.isActive():
            self.timer.start(self.update_interval_ms)
            self._logger.info("LiveFeedView updates started")

    def stop_updates(self) -> None:
        """Stop polling for images."""
        if self.timer.isActive():
            self.timer.stop()
            self._logger.info("LiveFeedView updates stopped")

    def set_update_interval(self, interval_ms: int) -> None:
        """
        Change the update polling interval.

        Args:
            interval_ms: New interval in milliseconds
        """
        self.update_interval_ms = interval_ms
        if self.timer.isActive():
            self.timer.stop()
            self.timer.start(interval_ms)
            self._logger.info(f"Update interval changed to {interval_ms}ms")

    # Stage control methods
    def _move_relative(self, axis: str, delta: float) -> None:
        """
        Move stage by relative amount on specified axis.

        Args:
            axis: Axis name ('X', 'Y', 'Z', 'R')
            delta: Amount to move (mm or degrees)
        """
        try:
            # Update current position
            if axis == 'X':
                self._current_position.x += delta
                self.x_spinbox.setValue(self._current_position.x)
            elif axis == 'Y':
                self._current_position.y += delta
                self.y_spinbox.setValue(self._current_position.y)
            elif axis == 'Z':
                self._current_position.z += delta
                self.z_spinbox.setValue(self._current_position.z)
            elif axis == 'R':
                self._current_position.r += delta
                self.r_spinbox.setValue(self._current_position.r)

            # Emit signal for relative movement
            self.move_relative_requested.emit(axis, delta)

            # Update position display
            self._update_position_display()

            self._logger.info(f"Moved {axis} by {delta}")

        except Exception as e:
            self._logger.error(f"Error moving {axis}: {e}")
            self.status_label.setText(f"Status: Error moving stage - {str(e)}")
            self.status_label.setStyleSheet("color: red; font-style: italic;")

    def _on_move_to_position(self) -> None:
        """Handle move to absolute position button click."""
        try:
            # Get target position from spinboxes
            target = Position(
                x=self.x_spinbox.value(),
                y=self.y_spinbox.value(),
                z=self.z_spinbox.value(),
                r=self.r_spinbox.value()
            )

            # Emit signal for absolute movement
            self.move_position_requested.emit(target)

            # Update current position
            self._current_position = target
            self._update_position_display()

            self._logger.info(f"Moving to position: {target}")
            self.status_label.setText(f"Status: Moving to position...")
            self.status_label.setStyleSheet("color: orange; font-style: italic;")

        except Exception as e:
            self._logger.error(f"Error moving to position: {e}")
            self.status_label.setText(f"Status: Error - {str(e)}")
            self.status_label.setStyleSheet("color: red; font-style: italic;")

    def _update_position_display(self) -> None:
        """Update the position label with current position."""
        self.position_label.setText(
            f"X: {self._current_position.x:.3f} "
            f"Y: {self._current_position.y:.3f} "
            f"Z: {self._current_position.z:.3f} "
            f"R: {self._current_position.r:.1f}°"
        )

    def update_position(self, position: Position) -> None:
        """
        Update displayed position (called from controller).

        Args:
            position: New position from microscope
        """
        self._current_position = position
        self.x_spinbox.setValue(position.x)
        self.y_spinbox.setValue(position.y)
        self.z_spinbox.setValue(position.z)
        self.r_spinbox.setValue(position.r)
        self._update_position_display()

    def request_position_update(self) -> None:
        """
        Request position update from microscope.

        This method should be called after connection is established
        to initialize the position display with actual microscope position.
        """
        if self.position_controller is None:
            self._logger.warning("Cannot request position: position_controller not available")
            return

        try:
            position = self.position_controller.get_current_position()
            if position:
                self.update_position(position)
                self._logger.info(f"Position updated from microscope: {position}")
            else:
                self._logger.warning("Could not retrieve position from microscope")
        except Exception as e:
            self._logger.error(f"Error requesting position update: {e}")

    # Laser control methods
    def _on_laser_changed(self, laser_channel: str) -> None:
        """
        Handle laser channel selection change.

        Args:
            laser_channel: Selected laser channel name
        """
        self.laser_changed.emit(laser_channel)
        self._logger.info(f"Laser changed to: {laser_channel}")

    def _on_laser_power_changed(self, power: float) -> None:
        """
        Handle laser power change.

        Args:
            power: Laser power percentage (0-100)
        """
        # Update slider to match spinbox
        self.power_slider.setValue(int(power))

        # Emit signal
        self.laser_power_changed.emit(power)
        self._logger.debug(f"Laser power changed to: {power}%")

    def get_laser_settings(self) -> tuple:
        """
        Get current laser settings from UI.

        Returns:
            Tuple of (laser_channel, laser_power)
        """
        return (
            self.laser_combo.currentText(),
            self.power_spinbox.value()
        )

    # Image acquisition methods
    def _on_snapshot_clicked(self) -> None:
        """Handle snapshot button click."""
        try:
            self._logger.info("Snapshot requested")
            self.status_label.setText("Status: Taking snapshot...")
            self.status_label.setStyleSheet("color: orange; font-style: italic;")

            # Disable button during acquisition
            self.snapshot_btn.setEnabled(False)

            # Emit signal
            self.snapshot_requested.emit()

            # Re-enable after short delay (actual re-enable should come from controller)
            QTimer.singleShot(1000, lambda: self.snapshot_btn.setEnabled(True))

        except Exception as e:
            self._logger.error(f"Error requesting snapshot: {e}")
            self.status_label.setText(f"Status: Snapshot error - {str(e)}")
            self.status_label.setStyleSheet("color: red; font-style: italic;")
            self.snapshot_btn.setEnabled(True)

    def _on_brightfield_clicked(self) -> None:
        """Handle brightfield acquisition button click."""
        try:
            self._logger.info("Brightfield acquisition requested")
            self.status_label.setText("Status: Acquiring brightfield image...")
            self.status_label.setStyleSheet("color: orange; font-style: italic;")

            # Disable button during acquisition
            self.brightfield_btn.setEnabled(False)

            # Emit signal
            self.brightfield_requested.emit()

            # Re-enable after short delay
            QTimer.singleShot(1000, lambda: self.brightfield_btn.setEnabled(True))

        except Exception as e:
            self._logger.error(f"Error requesting brightfield: {e}")
            self.status_label.setText(f"Status: Brightfield error - {str(e)}")
            self.status_label.setStyleSheet("color: red; font-style: italic;")
            self.brightfield_btn.setEnabled(True)

    def _on_sync_settings(self) -> None:
        """Handle sync settings button click."""
        try:
            self._logger.info("Settings sync requested")
            self.status_label.setText("Status: Syncing settings from microscope...")
            self.status_label.setStyleSheet("color: orange; font-style: italic;")

            # Disable button during sync
            self.sync_settings_btn.setEnabled(False)

            # Emit signal
            self.sync_settings_requested.emit()

            # Re-enable after short delay
            QTimer.singleShot(2000, lambda: self.sync_settings_btn.setEnabled(True))

        except Exception as e:
            self._logger.error(f"Error syncing settings: {e}")
            self.status_label.setText(f"Status: Sync error - {str(e)}")
            self.status_label.setStyleSheet("color: red; font-style: italic;")
            self.sync_settings_btn.setEnabled(True)

    def set_controls_enabled(self, enabled: bool) -> None:
        """
        Enable or disable all control widgets.

        Args:
            enabled: True to enable controls, False to disable
        """
        # Stage controls
        self.x_spinbox.setEnabled(enabled)
        self.y_spinbox.setEnabled(enabled)
        self.z_spinbox.setEnabled(enabled)
        self.r_spinbox.setEnabled(enabled)
        self.x_minus_btn.setEnabled(enabled)
        self.x_plus_btn.setEnabled(enabled)
        self.y_minus_btn.setEnabled(enabled)
        self.y_plus_btn.setEnabled(enabled)
        self.z_minus_btn.setEnabled(enabled)
        self.z_plus_btn.setEnabled(enabled)
        self.r_minus_btn.setEnabled(enabled)
        self.r_plus_btn.setEnabled(enabled)
        self.move_to_position_btn.setEnabled(enabled)

        # Laser controls
        self.laser_combo.setEnabled(enabled)
        self.power_spinbox.setEnabled(enabled)
        self.power_slider.setEnabled(enabled)

        # Acquisition controls
        self.snapshot_btn.setEnabled(enabled)
        self.brightfield_btn.setEnabled(enabled)
        self.sync_settings_btn.setEnabled(enabled)
