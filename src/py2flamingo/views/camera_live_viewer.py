"""
Camera Live Viewer widget for real-time camera feed display.

Provides dedicated UI for camera live view with exposure control,
intensity scaling, and performance monitoring.
"""

import logging
import numpy as np
from typing import Optional
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QSlider, QSpinBox, QCheckBox, QSizePolicy
)
from PyQt5.QtCore import Qt, pyqtSlot, QTimer
from PyQt5.QtGui import QPixmap, QImage

from py2flamingo.controllers.camera_controller import CameraController, CameraState
from py2flamingo.services.camera_service import ImageHeader


class CameraLiveViewer(QWidget):
    """
    Widget for displaying live camera feed with controls.

    Connects to CameraController and provides UI for:
    - Start/Stop live view
    - Exposure time control
    - Display intensity scaling
    - Frame rate monitoring
    - Image information overlay
    """

    def __init__(self, camera_controller: CameraController, laser_led_controller=None, parent=None):
        """
        Initialize camera live viewer.

        Args:
            camera_controller: CameraController instance
            laser_led_controller: Optional LaserLEDController for light source control
            parent: Parent widget
        """
        super().__init__(parent)

        self.camera_controller = camera_controller
        self.laser_led_controller = laser_led_controller
        self.logger = logging.getLogger(__name__)

        # Display state
        self._current_image: Optional[np.ndarray] = None
        self._current_header: Optional[ImageHeader] = None
        self._display_scale = 1.0

        # Setup UI
        self._setup_ui()

        # Connect controller signals
        self._connect_signals()

        # Update UI with initial state
        self._update_ui_state()

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        main_layout = QVBoxLayout()

        # ===== Laser/LED Control Panel =====
        if self.laser_led_controller:
            from py2flamingo.views.laser_led_control_panel import LaserLEDControlPanel
            self.laser_led_panel = LaserLEDControlPanel(self.laser_led_controller)
            main_layout.addWidget(self.laser_led_panel)

        # ===== Top: Controls Group =====
        controls_group = QGroupBox("Camera Controls")
        controls_layout = QVBoxLayout()

        # Live view control
        lv_layout = QHBoxLayout()
        lv_layout.addWidget(QLabel("Live View:"))

        self.start_btn = QPushButton("Start Live View")
        self.start_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 8px;")
        self.start_btn.clicked.connect(self._on_start_clicked)
        lv_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("Stop Live View")
        self.stop_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 8px;")
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.stop_btn.setEnabled(False)
        lv_layout.addWidget(self.stop_btn)

        lv_layout.addStretch()
        controls_layout.addLayout(lv_layout)

        # Exposure time control
        exp_layout = QHBoxLayout()
        exp_layout.addWidget(QLabel("Exposure (µs):"))

        self.exposure_spinbox = QSpinBox()
        self.exposure_spinbox.setRange(100, 1000000)  # 100µs to 1s
        self.exposure_spinbox.setValue(10000)  # 10ms default
        self.exposure_spinbox.setSingleStep(1000)
        self.exposure_spinbox.valueChanged.connect(self._on_exposure_changed)
        exp_layout.addWidget(self.exposure_spinbox)

        exp_layout.addWidget(QLabel("="))
        self.exposure_ms_label = QLabel("10.0 ms")
        exp_layout.addWidget(self.exposure_ms_label)

        exp_layout.addStretch()
        controls_layout.addLayout(exp_layout)

        # Display scaling controls
        scale_layout = QVBoxLayout()

        # Auto-scale checkbox
        autoscale_layout = QHBoxLayout()
        self.autoscale_checkbox = QCheckBox("Auto-scale Intensity")
        self.autoscale_checkbox.setChecked(True)
        self.autoscale_checkbox.stateChanged.connect(self._on_autoscale_changed)
        autoscale_layout.addWidget(self.autoscale_checkbox)
        autoscale_layout.addStretch()
        scale_layout.addLayout(autoscale_layout)

        # Min intensity slider
        min_layout = QHBoxLayout()
        min_layout.addWidget(QLabel("Min Intensity:"))
        self.min_slider = QSlider(Qt.Horizontal)
        self.min_slider.setRange(0, 65535)
        self.min_slider.setValue(0)
        self.min_slider.valueChanged.connect(self._on_min_changed)
        self.min_slider.setEnabled(False)
        min_layout.addWidget(self.min_slider)
        self.min_label = QLabel("0")
        self.min_label.setMinimumWidth(50)
        min_layout.addWidget(self.min_label)
        scale_layout.addLayout(min_layout)

        # Max intensity slider
        max_layout = QHBoxLayout()
        max_layout.addWidget(QLabel("Max Intensity:"))
        self.max_slider = QSlider(Qt.Horizontal)
        self.max_slider.setRange(0, 65535)
        self.max_slider.setValue(65535)
        self.max_slider.valueChanged.connect(self._on_max_changed)
        self.max_slider.setEnabled(False)
        max_layout.addWidget(self.max_slider)
        self.max_label = QLabel("65535")
        self.max_label.setMinimumWidth(50)
        max_layout.addWidget(self.max_label)
        scale_layout.addLayout(max_layout)

        controls_layout.addLayout(scale_layout)

        # Crosshair and zoom controls
        overlay_layout = QHBoxLayout()
        self.crosshair_checkbox = QCheckBox("Show Crosshair")
        self.crosshair_checkbox.stateChanged.connect(self._on_crosshair_changed)
        overlay_layout.addWidget(self.crosshair_checkbox)

        overlay_layout.addStretch()
        overlay_layout.addWidget(QLabel("Zoom:"))
        self.zoom_spinbox = QSpinBox()
        self.zoom_spinbox.setRange(100, 400)  # 100% to 400%
        self.zoom_spinbox.setValue(100)
        self.zoom_spinbox.setSuffix("%")
        self.zoom_spinbox.setSingleStep(25)
        self.zoom_spinbox.valueChanged.connect(self._on_zoom_changed)
        overlay_layout.addWidget(self.zoom_spinbox)

        controls_layout.addLayout(overlay_layout)

        controls_group.setLayout(controls_layout)
        main_layout.addWidget(controls_group)

        # ===== Middle: Image Display =====
        display_group = QGroupBox("Live Image")
        display_layout = QVBoxLayout()

        self.image_label = QLabel("No image - Click 'Start Live View'")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(640, 480)
        self.image_label.setStyleSheet("QLabel { background-color: black; color: gray; border: 2px solid gray; }")
        self.image_label.setScaledContents(False)
        display_layout.addWidget(self.image_label)

        display_group.setLayout(display_layout)
        main_layout.addWidget(display_group, stretch=1)

        # ===== Bottom: Info Display =====
        info_group = QGroupBox("Image Information")
        info_layout = QVBoxLayout()

        # Status line
        status_layout = QHBoxLayout()
        status_layout.addWidget(QLabel("Status:"))
        self.status_label = QLabel("Idle")
        self.status_label.setStyleSheet("color: gray; font-weight: bold;")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        info_layout.addLayout(status_layout)

        # Image info line
        img_info_layout = QHBoxLayout()
        img_info_layout.addWidget(QLabel("Image:"))
        self.img_info_label = QLabel("--")
        img_info_layout.addWidget(self.img_info_label)
        img_info_layout.addStretch()
        info_layout.addLayout(img_info_layout)

        # Frame rate line
        fps_layout = QHBoxLayout()
        fps_layout.addWidget(QLabel("Frame Rate:"))
        self.fps_label = QLabel("-- FPS")
        self.fps_label.setStyleSheet("font-weight: bold;")
        fps_layout.addWidget(self.fps_label)
        fps_layout.addStretch()

        fps_layout.addWidget(QLabel("Exposure:"))
        self.actual_exposure_label = QLabel("--")
        fps_layout.addWidget(self.actual_exposure_label)
        fps_layout.addStretch()

        info_layout.addLayout(fps_layout)

        # Intensity range line
        intensity_layout = QHBoxLayout()
        intensity_layout.addWidget(QLabel("Intensity Range:"))
        self.intensity_label = QLabel("--")
        intensity_layout.addWidget(self.intensity_label)
        intensity_layout.addStretch()
        info_layout.addLayout(intensity_layout)

        info_group.setLayout(info_layout)
        main_layout.addWidget(info_group)

        self.setLayout(main_layout)

    def _connect_signals(self) -> None:
        """Connect controller signals to UI slots."""
        self.camera_controller.new_image.connect(self._on_new_image)
        self.camera_controller.state_changed.connect(self._on_state_changed)
        self.camera_controller.error_occurred.connect(self._on_error)
        self.camera_controller.frame_rate_updated.connect(self._on_frame_rate_updated)

    def _update_ui_state(self) -> None:
        """Update UI based on controller state."""
        state = self.camera_controller.state

        if state == CameraState.IDLE:
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.status_label.setText("Idle")
            self.status_label.setStyleSheet("color: gray; font-weight: bold;")

        elif state == CameraState.LIVE_VIEW:
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.status_label.setText("Live View Active")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")

        elif state == CameraState.ACQUIRING:
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.status_label.setText("Acquiring...")
            self.status_label.setStyleSheet("color: orange; font-weight: bold;")

        elif state == CameraState.ERROR:
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.status_label.setText("Error")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")

    # ===== Slot implementations =====

    @pyqtSlot(np.ndarray, object)
    def _on_new_image(self, image: np.ndarray, header: ImageHeader) -> None:
        """Handle new image from controller."""
        self._current_image = image
        self._current_header = header

        # Update info display
        self.img_info_label.setText(
            f"{header.image_width}x{header.image_height}, "
            f"Frame #{header.frame_number}"
        )

        self.actual_exposure_label.setText(f"{header.exposure_us/1000:.2f} ms")

        self.intensity_label.setText(
            f"[{header.image_scale_min} - {header.image_scale_max}]"
        )

        # Convert and display image
        self._display_image(image, header)

    @pyqtSlot(object)
    def _on_state_changed(self, state: CameraState) -> None:
        """Handle camera state change."""
        self._update_ui_state()

    @pyqtSlot(str)
    def _on_error(self, error_msg: str) -> None:
        """Handle error from controller."""
        self.logger.error(f"Camera error: {error_msg}")
        self.status_label.setText(f"Error: {error_msg[:30]}")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")

    @pyqtSlot(float)
    def _on_frame_rate_updated(self, fps: float) -> None:
        """Handle frame rate update."""
        self.fps_label.setText(f"{fps:.1f} FPS")

    def _on_start_clicked(self) -> None:
        """Handle start button click."""
        self.camera_controller.start_live_view()

    def _on_stop_clicked(self) -> None:
        """Handle stop button click."""
        self.camera_controller.stop_live_view()

    def _on_exposure_changed(self, value: int) -> None:
        """Handle exposure time change."""
        self.camera_controller.set_exposure_time(value)
        self.exposure_ms_label.setText(f"{value/1000:.2f} ms")

    def _on_autoscale_changed(self, state: int) -> None:
        """Handle auto-scale checkbox change."""
        enabled = state == Qt.Checked
        self.camera_controller.set_auto_scale(enabled)

        # Enable/disable manual sliders
        self.min_slider.setEnabled(not enabled)
        self.max_slider.setEnabled(not enabled)

    def _on_min_changed(self, value: int) -> None:
        """Handle min intensity slider change."""
        self.min_label.setText(str(value))
        if not self.camera_controller.is_auto_scale():
            max_val = self.max_slider.value()
            self.camera_controller.set_display_range(value, max_val)
            # Redisplay current image if available
            if self._current_image is not None:
                self._display_image(self._current_image, self._current_header)

    def _on_max_changed(self, value: int) -> None:
        """Handle max intensity slider change."""
        self.max_label.setText(str(value))
        if not self.camera_controller.is_auto_scale():
            min_val = self.min_slider.value()
            self.camera_controller.set_display_range(min_val, value)
            # Redisplay current image if available
            if self._current_image is not None:
                self._display_image(self._current_image, self._current_header)

    def _on_crosshair_changed(self, state: int) -> None:
        """Handle crosshair checkbox change."""
        # Redisplay with/without crosshair
        if self._current_image is not None:
            self._display_image(self._current_image, self._current_header)

    def _on_zoom_changed(self, value: int) -> None:
        """Handle zoom spinbox change."""
        self._display_scale = value / 100.0
        # Redisplay at new scale
        if self._current_image is not None:
            self._display_image(self._current_image, self._current_header)

    def _display_image(self, image: np.ndarray, header: ImageHeader) -> None:
        """
        Convert image to QPixmap and display.

        Args:
            image: Image array (uint16)
            header: Image metadata
        """
        try:
            # Get display range
            if self.camera_controller.is_auto_scale():
                min_val = header.image_scale_min
                max_val = header.image_scale_max
            else:
                min_val, max_val = self.camera_controller.get_display_range()

            # Normalize to 8-bit for display
            if max_val > min_val:
                normalized = ((image.astype(np.float32) - min_val) /
                            (max_val - min_val) * 255.0)
                normalized = np.clip(normalized, 0, 255).astype(np.uint8)
            else:
                normalized = np.zeros_like(image, dtype=np.uint8)

            # Convert to QImage
            height, width = normalized.shape
            bytes_per_line = width
            qimage = QImage(normalized.data, width, height, bytes_per_line,
                          QImage.Format_Grayscale8)

            # Add crosshair if enabled
            if self.crosshair_checkbox.isChecked():
                qimage = self._add_crosshair(qimage)

            # Convert to pixmap
            pixmap = QPixmap.fromImage(qimage)

            # Apply zoom
            if self._display_scale != 1.0:
                new_size = pixmap.size() * self._display_scale
                pixmap = pixmap.scaled(new_size, Qt.KeepAspectRatio,
                                      Qt.SmoothTransformation)

            # Scale to fit label while maintaining aspect ratio
            scaled_pixmap = pixmap.scaled(
                self.image_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )

            self.image_label.setPixmap(scaled_pixmap)

        except Exception as e:
            self.logger.error(f"Error displaying image: {e}")

    def _add_crosshair(self, qimage: QImage) -> QImage:
        """
        Add crosshair overlay to image.

        Args:
            qimage: Input QImage

        Returns:
            QImage with crosshair
        """
        from PyQt5.QtGui import QPainter, QPen
        from PyQt5.QtCore import QPoint

        # Create a copy to draw on
        result = qimage.copy()

        painter = QPainter(result)
        pen = QPen(Qt.red)
        pen.setWidth(2)
        painter.setPen(pen)

        # Draw crosshair at center
        center_x = result.width() // 2
        center_y = result.height() // 2
        size = 20

        # Vertical line
        painter.drawLine(center_x, center_y - size, center_x, center_y + size)
        # Horizontal line
        painter.drawLine(center_x - size, center_y, center_x + size, center_y)

        painter.end()

        return result

    def cleanup(self) -> None:
        """Cleanup resources when widget is closed."""
        if self.camera_controller.state == CameraState.LIVE_VIEW:
            self.camera_controller.stop_live_view()
