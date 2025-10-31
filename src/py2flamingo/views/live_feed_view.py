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
    QCheckBox, QComboBox, QGroupBox, QSlider, QSizePolicy
)
from PyQt5.QtCore import QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QPixmap

from ..models import ImageDisplayModel
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

    def __init__(self,
                 workflow_controller: WorkflowController,
                 visualize_queue: Queue,
                 display_model: Optional[ImageDisplayModel] = None,
                 update_interval_ms: int = 500):
        """
        Initialize live feed view.

        Args:
            workflow_controller: Controller to check workflow state
            visualize_queue: Queue with images from microscope
            display_model: Display settings model (creates default if None)
            update_interval_ms: Poll interval in milliseconds
        """
        super().__init__()

        self.workflow_controller = workflow_controller
        self.visualize_queue = visualize_queue
        self.display_model = display_model or ImageDisplayModel()
        self.update_interval_ms = update_interval_ms

        self._logger = logging.getLogger(__name__)
        self._last_image: Optional[np.ndarray] = None
        self._frame_count = 0

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
        layout = QVBoxLayout()

        # Image display area
        display_group = QGroupBox("Live Feed")
        display_layout = QVBoxLayout()

        self.image_label = QLabel("No image")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(512, 512)
        self.image_label.setScaledContents(False)
        self.image_label.setStyleSheet("QLabel { background-color: black; color: gray; }")
        display_layout.addWidget(self.image_label)

        # Status label
        self.status_label = QLabel("Status: Waiting for images...")
        self.status_label.setStyleSheet("color: gray; font-style: italic;")
        display_layout.addWidget(self.status_label)

        display_group.setLayout(display_layout)
        layout.addWidget(display_group)

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
        layout.addWidget(controls_group)

        # Add stretch to push everything to top
        layout.addStretch()

        self.setLayout(layout)

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
