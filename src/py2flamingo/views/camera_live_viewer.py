"""
Camera Live Viewer widget for real-time camera feed display.

Provides dedicated UI for camera live view with exposure control,
intensity scaling, and performance monitoring.
"""

import logging
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Optional
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QSlider, QSpinBox, QCheckBox, QSizePolicy, QMessageBox, QFileDialog
)
from PyQt5.QtCore import Qt, pyqtSlot, QTimer, QEvent
from PyQt5.QtGui import QPixmap, QImage, QCloseEvent, QShowEvent, QHideEvent

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

    # Class variable to remember last snapshot directory across instances
    _last_snapshot_directory: Optional[str] = None

    def __init__(self, camera_controller: CameraController, laser_led_controller=None, image_controls_window=None, parent=None):
        """
        Initialize camera live viewer.

        Args:
            camera_controller: CameraController instance
            laser_led_controller: Optional LaserLEDController for light source control
            image_controls_window: Optional ImageControlsWindow for slider feedback
            parent: Parent widget
        """
        super().__init__(parent)

        self.camera_controller = camera_controller
        self.laser_led_controller = laser_led_controller
        self.image_controls_window = image_controls_window
        self.logger = logging.getLogger(__name__)

        # Display state
        self._current_image: Optional[np.ndarray] = None
        self._current_header: Optional[ImageHeader] = None
        self._display_scale = 1.0

        # Image transformation state (controlled by Image Controls window)
        self._rotation = 0  # 0, 90, 180, 270
        self._flip_horizontal = False
        self._flip_vertical = False
        self._colormap = "Grayscale"
        self._show_crosshair = False

        # Setup UI
        self._setup_ui()

        # Connect controller signals
        self._connect_signals()

        # Update UI with initial state
        self._update_ui_state()

        # Configure as independent window
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Camera Live Viewer")
        self.setMinimumSize(1000, 600)  # Wider window for horizontal layout

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        # Main horizontal layout: Image on left, controls on right
        main_layout = QHBoxLayout()

        # ===== LEFT SIDE: Image Display =====
        left_layout = QVBoxLayout()

        display_group = QGroupBox("Live Image")
        display_layout = QVBoxLayout()

        self.image_label = QLabel("No image - Click 'Start Live View'")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(640, 480)
        self.image_label.setStyleSheet("QLabel { background-color: black; color: gray; border: 2px solid gray; }")
        self.image_label.setScaledContents(False)
        display_layout.addWidget(self.image_label)

        display_group.setLayout(display_layout)
        left_layout.addWidget(display_group, stretch=1)

        # Add left side to main layout
        main_layout.addLayout(left_layout, stretch=2)

        # ===== RIGHT SIDE: Controls =====
        right_layout = QVBoxLayout()

        # ===== Laser/LED Control Panel =====
        if self.laser_led_controller:
            from py2flamingo.views.laser_led_control_panel import LaserLEDControlPanel
            self.laser_led_panel = LaserLEDControlPanel(self.laser_led_controller)
            right_layout.addWidget(self.laser_led_panel)

        # ===== Camera Controls Group =====
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

        # Snapshot button
        self.snapshot_btn = QPushButton("Take Snapshot")
        self.snapshot_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 8px;")
        self.snapshot_btn.clicked.connect(self._on_snapshot_clicked)
        lv_layout.addWidget(self.snapshot_btn)

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

        # Image Controls button
        image_controls_layout = QVBoxLayout()

        self.image_controls_btn = QPushButton("Open Image Controls")
        self.image_controls_btn.setStyleSheet(
            "background-color: #9C27B0; color: white; font-weight: bold; padding: 8px;"
        )
        self.image_controls_btn.clicked.connect(self._on_image_controls_clicked)
        image_controls_layout.addWidget(self.image_controls_btn)

        # Short description
        desc_label = QLabel("<i>For image transformations, color, and display options</i>")
        desc_label.setStyleSheet("color: #666; font-size: 9pt;")
        desc_label.setWordWrap(True)
        desc_label.setAlignment(Qt.AlignCenter)
        image_controls_layout.addWidget(desc_label)

        controls_layout.addLayout(image_controls_layout)

        controls_group.setLayout(controls_layout)
        right_layout.addWidget(controls_group)

        # ===== Info Display =====
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
        right_layout.addWidget(info_group)

        # Add stretch to push everything to top
        right_layout.addStretch()

        # Add right side to main layout
        main_layout.addLayout(right_layout, stretch=1)

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

        # Update Image Controls Window sliders with auto-scale feedback
        if self.image_controls_window and self.camera_controller.is_auto_scale():
            self.image_controls_window.update_auto_scale_feedback(
                header.image_scale_min,
                header.image_scale_max
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

    def _on_snapshot_clicked(self) -> None:
        """Handle snapshot button click with file save dialog."""
        try:
            # Ensure laser/LED is active
            if self.laser_led_controller and not self.laser_led_controller.is_preview_active():
                QMessageBox.warning(
                    self,
                    "No Light Source",
                    "Please select and enable a laser or LED before taking a snapshot."
                )
                return

            # Determine default save directory
            if CameraLiveViewer._last_snapshot_directory:
                default_dir = CameraLiveViewer._last_snapshot_directory
            else:
                # Try to get from config, otherwise use home directory
                try:
                    from py2flamingo.services.configuration_service import ConfigurationService
                    config = ConfigurationService()
                    default_dir = config.get_data_storage_location()
                except:
                    default_dir = str(Path.home())

            # Generate suggested filename with auto-increment
            suggested_filename = self._generate_snapshot_filename(default_dir)

            # Show save file dialog
            filename, _ = QFileDialog.getSaveFileName(
                self,
                "Save Snapshot",
                suggested_filename,
                "TIFF Images (*.tif *.tiff);;PNG Images (*.png);;All Files (*.*)",
                options=QFileDialog.DontConfirmOverwrite  # We handle auto-increment, so no overwrite prompt
            )

            # User cancelled
            if not filename:
                self.logger.info("Snapshot cancelled by user")
                return

            # Remember directory for next time
            CameraLiveViewer._last_snapshot_directory = str(Path(filename).parent)

            # Disable button during capture
            self.snapshot_btn.setEnabled(False)
            self.snapshot_btn.setText("Capturing...")

            # Take snapshot and save (runs in background, reuses existing data socket)
            def do_snapshot():
                success = self._capture_and_save_snapshot(filename)

                if success:
                    self.status_label.setText(f"Snapshot saved: {Path(filename).name}")
                    self.status_label.setStyleSheet("color: green; font-weight: bold;")
                    self.logger.info(f"Snapshot saved to {filename}")
                else:
                    self.status_label.setText("Snapshot failed")
                    self.status_label.setStyleSheet("color: red; font-weight: bold;")

                # Re-enable button
                self.snapshot_btn.setEnabled(True)
                self.snapshot_btn.setText("Take Snapshot")

            # Run snapshot in timer to avoid blocking UI
            QTimer.singleShot(100, do_snapshot)

        except Exception as e:
            self.logger.error(f"Snapshot error: {e}")
            QMessageBox.critical(self, "Snapshot Error", str(e))
            self.snapshot_btn.setEnabled(True)
            self.snapshot_btn.setText("Take Snapshot")

    def _generate_snapshot_filename(self, directory: str) -> str:
        """
        Generate snapshot filename with auto-incrementing number.

        Format: snapshot_{YYYYMMDD}_{NNN}.tif
        Where NNN is auto-incremented based on existing files.

        Args:
            directory: Directory to save in

        Returns:
            Full path to suggested snapshot filename
        """
        save_path = Path(directory)
        save_path.mkdir(parents=True, exist_ok=True)

        # Get current date
        date_str = datetime.now().strftime("%Y%m%d")

        # Find existing snapshots for today
        pattern = f"snapshot_{date_str}_*.tif"
        existing_files = list(save_path.glob(pattern))

        # Determine next number
        if not existing_files:
            next_num = 1
        else:
            # Extract numbers from existing files
            numbers = []
            for file in existing_files:
                try:
                    # Extract number from filename: snapshot_20231115_005.tif -> 5
                    parts = file.stem.split('_')
                    if len(parts) >= 3:
                        num = int(parts[-1])
                        numbers.append(num)
                except ValueError:
                    continue

            next_num = max(numbers) + 1 if numbers else 1

        # Generate filename with zero-padded number
        filename = f"snapshot_{date_str}_{next_num:03d}.tif"
        full_path = save_path / filename

        return str(full_path)

    def _capture_and_save_snapshot(self, filename: str) -> bool:
        """
        Capture snapshot and save to specified file.

        Args:
            filename: Full path to save file

        Returns:
            True if successful, False otherwise
        """
        try:
            # Check if we need to temporarily connect data socket
            was_streaming = self.camera_controller._state == CameraState.LIVE_VIEW

            if not was_streaming:
                # Not in live view, need to connect data socket temporarily
                self.logger.info("Connecting data socket for snapshot...")
                self.camera_controller.camera_service.start_live_view_streaming()
                import time
                time.sleep(0.5)  # Give socket time to connect

            # Set flag to capture next frame
            self.camera_controller._capture_next_frame = True
            self.camera_controller._captured_snapshot = None

            # Send snapshot command (reuses existing communication)
            self.logger.info("Sending snapshot command...")
            self.camera_controller.camera_service.take_snapshot()

            # Wait for image to arrive (via existing callback mechanism)
            import time
            timeout = 5.0  # 5 second timeout
            start_time = time.time()

            while self.camera_controller._captured_snapshot is None and (time.time() - start_time) < timeout:
                time.sleep(0.1)

            if self.camera_controller._captured_snapshot is None:
                raise RuntimeError("Timeout waiting for snapshot image")

            # Save the captured image
            image, header = self.camera_controller._captured_snapshot
            self._save_image_to_file(image, filename)
            self.logger.info(f"Snapshot saved to {filename}")

            # Clean up temporary connection if needed
            if not was_streaming:
                self.logger.info("Disconnecting temporary data socket...")
                self.camera_controller.camera_service.stop_live_view_streaming()

            return True

        except Exception as e:
            error_msg = f"Failed to capture snapshot: {e}"
            self.logger.error(error_msg)

            # Clean up on error
            if not was_streaming and self.camera_controller.camera_service._streaming:
                try:
                    self.camera_controller.camera_service.stop_live_view_streaming()
                except:
                    pass

            return False

    def _save_image_to_file(self, image: np.ndarray, filename: str) -> None:
        """
        Save image to file (TIFF or PNG).

        Args:
            image: Image array (uint16)
            filename: Path to save file
        """
        try:
            from PIL import Image

            file_path = Path(filename)

            # Save based on extension
            if file_path.suffix.lower() in ['.tif', '.tiff']:
                # Save as 16-bit TIFF
                pil_image = Image.fromarray(image.astype(np.uint16), mode='I;16')
                pil_image.save(filename, format='TIFF')
            elif file_path.suffix.lower() == '.png':
                # Save as 16-bit PNG
                pil_image = Image.fromarray(image.astype(np.uint16), mode='I;16')
                pil_image.save(filename, format='PNG')
            else:
                # Default to TIFF
                pil_image = Image.fromarray(image.astype(np.uint16), mode='I;16')
                pil_image.save(filename, format='TIFF')

            self.logger.info(f"Image saved: {filename}")

        except ImportError:
            # Fallback to numpy save if PIL not available
            self.logger.warning("PIL not available, saving as numpy array")
            np.save(filename.replace('.tif', '.npy').replace('.png', '.npy'), image)

    def _on_exposure_changed(self, value: int) -> None:
        """Handle exposure time change."""
        self.camera_controller.set_exposure_time(value)
        self.exposure_ms_label.setText(f"{value/1000:.2f} ms")

    def _on_image_controls_clicked(self) -> None:
        """Handle image controls button click - show the Image Controls window."""
        if self.image_controls_window:
            self.image_controls_window.show()
            self.image_controls_window.raise_()  # Bring to front
            self.image_controls_window.activateWindow()  # Give focus
            self.logger.info("Image Controls window opened")
        else:
            self.logger.warning("Image Controls window not available")

    def _on_crosshair_changed(self, state: int) -> None:
        """Handle crosshair checkbox change."""
        # Update internal state
        self._show_crosshair = (state == Qt.Checked)
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
        Convert image to QPixmap and display with transformations.

        Args:
            image: Image array (uint16)
            header: Image metadata
        """
        try:
            # Apply flips to raw image first (before intensity scaling)
            transformed = image.copy()
            if self._flip_horizontal:
                transformed = np.fliplr(transformed)
            if self._flip_vertical:
                transformed = np.flipud(transformed)

            # Apply rotation to raw image
            if self._rotation == 90:
                transformed = np.rot90(transformed, k=1)  # 90° counter-clockwise
            elif self._rotation == 180:
                transformed = np.rot90(transformed, k=2)
            elif self._rotation == 270:
                transformed = np.rot90(transformed, k=3)  # 270° = -90°

            # Get display range
            if self.camera_controller.is_auto_scale():
                min_val = header.image_scale_min
                max_val = header.image_scale_max
            else:
                min_val, max_val = self.camera_controller.get_display_range()

            # Normalize to 8-bit for display
            if max_val > min_val:
                normalized = ((transformed.astype(np.float32) - min_val) /
                            (max_val - min_val) * 255.0)
                normalized = np.clip(normalized, 0, 255).astype(np.uint8)
            else:
                normalized = np.zeros_like(transformed, dtype=np.uint8)

            # Apply color map
            if self._colormap != "Grayscale":
                normalized = self._apply_colormap(normalized, self._colormap)
                # Ensure array is C-contiguous for QImage
                if not normalized.flags['C_CONTIGUOUS']:
                    normalized = np.ascontiguousarray(normalized)
                # Convert to QImage (RGB format)
                height, width, channels = normalized.shape
                bytes_per_line = width * channels
                qimage = QImage(normalized.data, width, height, bytes_per_line,
                              QImage.Format_RGB888)
            else:
                # Ensure array is C-contiguous for QImage
                if not normalized.flags['C_CONTIGUOUS']:
                    normalized = np.ascontiguousarray(normalized)
                # Convert to QImage (grayscale)
                height, width = normalized.shape
                bytes_per_line = width
                qimage = QImage(normalized.data, width, height, bytes_per_line,
                              QImage.Format_Grayscale8)

            # Add crosshair if enabled (use internal state from Image Controls)
            if self._show_crosshair:
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

    def _apply_colormap(self, grayscale: np.ndarray, colormap_name: str) -> np.ndarray:
        """
        Apply color map to grayscale image.

        Args:
            grayscale: Grayscale image (uint8)
            colormap_name: Name of color map to apply

        Returns:
            RGB image (uint8) with shape (H, W, 3)
        """
        # Simple built-in color maps without matplotlib dependency
        # Each is a 256x3 lookup table

        if colormap_name == "Hot":
            # Hot colormap: black -> red -> yellow -> white
            lut = np.zeros((256, 3), dtype=np.uint8)
            for i in range(256):
                if i < 85:
                    lut[i] = [i * 3, 0, 0]
                elif i < 170:
                    lut[i] = [255, (i - 85) * 3, 0]
                else:
                    lut[i] = [255, 255, (i - 170) * 3]

        elif colormap_name == "Jet":
            # Jet colormap: blue -> cyan -> green -> yellow -> red
            lut = np.zeros((256, 3), dtype=np.uint8)
            for i in range(256):
                if i < 32:
                    lut[i] = [0, 0, 128 + i * 4]
                elif i < 96:
                    lut[i] = [0, (i - 32) * 4, 255]
                elif i < 160:
                    lut[i] = [(i - 96) * 4, 255, 255 - (i - 96) * 4]
                elif i < 224:
                    lut[i] = [255, 255 - (i - 160) * 4, 0]
                else:
                    lut[i] = [255 - (i - 224) * 4, 0, 0]

        elif colormap_name == "Viridis":
            # Simplified Viridis: purple -> blue -> green -> yellow
            lut = np.zeros((256, 3), dtype=np.uint8)
            for i in range(256):
                t = i / 255.0
                lut[i] = [
                    int(255 * (0.267 + 0.529 * t)),
                    int(255 * (0.005 + 0.839 * t - 0.135 * t * t)),
                    int(255 * (0.329 - 0.329 * t))
                ]

        elif colormap_name == "Plasma":
            # Simplified Plasma: purple -> pink -> orange -> yellow
            lut = np.zeros((256, 3), dtype=np.uint8)
            for i in range(256):
                t = i / 255.0
                lut[i] = [
                    int(255 * (0.5 + 0.5 * t)),
                    int(255 * (0.0 + 0.8 * t * t)),
                    int(255 * (0.8 - 0.8 * t))
                ]

        elif colormap_name == "Inferno":
            # Simplified Inferno: black -> purple -> red -> yellow
            lut = np.zeros((256, 3), dtype=np.uint8)
            for i in range(256):
                t = i / 255.0
                lut[i] = [
                    int(255 * t),
                    int(255 * (t * t)),
                    int(255 * max(0, 3 * t - 2))
                ]

        elif colormap_name == "Magma":
            # Simplified Magma: black -> purple -> pink -> white
            lut = np.zeros((256, 3), dtype=np.uint8)
            for i in range(256):
                t = i / 255.0
                lut[i] = [
                    int(255 * t),
                    int(255 * (t * t * t)),
                    int(255 * max(0, 4 * t - 3))
                ]

        elif colormap_name == "Turbo":
            # Simplified Turbo: blue -> cyan -> green -> yellow -> red
            lut = np.zeros((256, 3), dtype=np.uint8)
            for i in range(256):
                t = i / 255.0
                lut[i] = [
                    int(255 * min(1.0, max(0.0, 1.5 * t - 0.25))),
                    int(255 * min(1.0, max(0.0, -abs(2 * t - 1) + 1))),
                    int(255 * min(1.0, max(0.0, -1.5 * t + 1.25)))
                ]

        else:
            # Default: grayscale
            lut = np.stack([np.arange(256)] * 3, axis=1).astype(np.uint8)

        # Apply LUT to image
        rgb = lut[grayscale]

        return rgb

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

    # ===== Image transformation setters (called by Image Controls window) =====

    def set_rotation(self, angle: int) -> None:
        """Set image rotation angle (0, 90, 180, 270)."""
        if angle in [0, 90, 180, 270]:
            self._rotation = angle
            self.logger.info(f"Image rotation set to {angle}°")
            # Redisplay current image with new rotation
            if self._current_image is not None:
                self._display_image(self._current_image, self._current_header)

    def set_flip_horizontal(self, enabled: bool) -> None:
        """Set horizontal flip state."""
        self._flip_horizontal = enabled
        self.logger.info(f"Horizontal flip: {enabled}")
        # Redisplay current image
        if self._current_image is not None:
            self._display_image(self._current_image, self._current_header)

    def set_flip_vertical(self, enabled: bool) -> None:
        """Set vertical flip state."""
        self._flip_vertical = enabled
        self.logger.info(f"Vertical flip: {enabled}")
        # Redisplay current image
        if self._current_image is not None:
            self._display_image(self._current_image, self._current_header)

    def set_colormap(self, colormap: str) -> None:
        """Set color map for image display."""
        self._colormap = colormap
        self.logger.info(f"Color map set to: {colormap}")
        # Redisplay current image with new colormap
        if self._current_image is not None:
            self._display_image(self._current_image, self._current_header)

    def set_intensity_range(self, min_val: int, max_val: int) -> None:
        """Set manual intensity range (pass through to camera controller)."""
        self.camera_controller.set_display_range(min_val, max_val)
        # Redisplay current image
        if self._current_image is not None:
            self._display_image(self._current_image, self._current_header)

    def set_auto_scale(self, enabled: bool) -> None:
        """Set auto-scale state (pass through to camera controller)."""
        self.camera_controller.set_auto_scale(enabled)

    def set_zoom(self, zoom_percentage: float) -> None:
        """Set zoom as percentage (1.0 = 100%)."""
        self._display_scale = zoom_percentage
        self.logger.debug(f"Zoom set to {zoom_percentage * 100:.0f}%")
        # Redisplay current image with new zoom
        if self._current_image is not None:
            self._display_image(self._current_image, self._current_header)

    def set_crosshair(self, enabled: bool) -> None:
        """Set crosshair visibility."""
        self._show_crosshair = enabled
        self.logger.debug(f"Crosshair: {enabled}")
        # Redisplay current image
        if self._current_image is not None:
            self._display_image(self._current_image, self._current_header)

    def cleanup(self) -> None:
        """Cleanup resources when widget is closed."""
        if self.camera_controller.state == CameraState.LIVE_VIEW:
            self.camera_controller.stop_live_view()

    def showEvent(self, event: QShowEvent) -> None:
        """
        Handle window show event.

        Unblock laser/LED signals and load actual powers from hardware.
        """
        super().showEvent(event)

        # Unblock signals that may have been blocked during hide
        if hasattr(self, 'laser_led_panel') and self.laser_led_panel:
            # Unblock LED slider and spinbox signals
            if hasattr(self.laser_led_panel, '_led_slider') and self.laser_led_panel._led_slider:
                self.laser_led_panel._led_slider.blockSignals(False)
            if hasattr(self.laser_led_panel, '_led_spinbox') and self.laser_led_panel._led_spinbox:
                self.laser_led_panel._led_spinbox.blockSignals(False)

            # Unblock all laser sliders
            if hasattr(self.laser_led_panel, '_laser_sliders'):
                for slider in self.laser_led_panel._laser_sliders.values():
                    slider.blockSignals(False)

            # Unblock all laser spinboxes
            if hasattr(self.laser_led_panel, '_laser_spinboxes'):
                for spinbox in self.laser_led_panel._laser_spinboxes.values():
                    spinbox.blockSignals(False)

        self.logger.info("Camera Live Viewer window opened (signals unblocked)")

        # Load actual laser powers from hardware (now that connection is established)
        if self.laser_led_controller:
            self.laser_led_controller.load_laser_powers_from_hardware()

    def hideEvent(self, event: QHideEvent) -> None:
        """
        Handle window hide event.

        Block all laser/LED control signals to prevent spurious commands
        during Qt widget cleanup/state management.
        """
        # Block ALL signals from laser/LED control panel widgets to prevent
        # Qt widget state changes from triggering unwanted LED_SET commands
        if hasattr(self, 'laser_led_panel') and self.laser_led_panel:
            # Block LED slider and spinbox signals
            if hasattr(self.laser_led_panel, '_led_slider') and self.laser_led_panel._led_slider:
                self.laser_led_panel._led_slider.blockSignals(True)
            if hasattr(self.laser_led_panel, '_led_spinbox') and self.laser_led_panel._led_spinbox:
                self.laser_led_panel._led_spinbox.blockSignals(True)

            # Block all laser sliders
            if hasattr(self.laser_led_panel, '_laser_sliders'):
                for slider in self.laser_led_panel._laser_sliders.values():
                    slider.blockSignals(True)

            # Block all laser spinboxes
            if hasattr(self.laser_led_panel, '_laser_spinboxes'):
                for spinbox in self.laser_led_panel._laser_spinboxes.values():
                    spinbox.blockSignals(True)

        super().hideEvent(event)
        self.logger.info("Camera Live Viewer window hidden (laser/LED signals blocked)")

    def changeEvent(self, event: QEvent) -> None:
        """Handle window state changes - log when window is activated."""
        super().changeEvent(event)
        if event.type() == QEvent.WindowActivate:
            self.logger.info("Camera Live Viewer window activated (user clicked into window)")
        elif event.type() == QEvent.WindowDeactivate:
            self.logger.debug("Camera Live Viewer window deactivated")

    def closeEvent(self, event: QCloseEvent) -> None:
        """
        Handle window close event.

        Hide the window instead of closing it so it can be reopened.

        Args:
            event: Close event
        """
        # hideEvent will log this, so no need to log here again
        self.hide()
        event.ignore()  # Don't actually close, just hide
