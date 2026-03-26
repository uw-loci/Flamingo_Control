"""Webcam Overview Dialog.

Main dialog for the webcam-based sample overview extension. Provides
live webcam feed, calibration point marking, multi-angle capture,
and launches the result viewer for tile selection.
"""

import logging
from datetime import datetime
from typing import Optional

import numpy as np
from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from py2flamingo.models.data.webcam_models import (
    WebcamAngleView,
    WebcamCalibrationPoint,
    WebcamSession,
)
from py2flamingo.services.webcam_calibration_service import (
    WebcamCalibrationService,
)
from py2flamingo.services.webcam_capture_service import WebcamCaptureService
from py2flamingo.services.window_geometry_manager import PersistentDialog

logger = logging.getLogger(__name__)


class ClickableImageLabel(QLabel):
    """QLabel that emits click coordinates as a signal."""

    clicked = pyqtSignal(float, float)  # (x_fraction, y_fraction) in [0,1]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("QLabel { background-color: #1a1a2e; }")
        self._pixmap: Optional[QPixmap] = None

    def set_image(self, pixmap: QPixmap) -> None:
        """Set the displayed pixmap, scaled to fit."""
        self._pixmap = pixmap
        self._update_display()

    def _update_display(self):
        if self._pixmap is not None:
            scaled = self._pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            super().setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_display()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._pixmap is not None:
            # Compute click position relative to the displayed image
            pixmap = self.pixmap()
            if pixmap is None or pixmap.isNull():
                return

            # Find the offset of the scaled pixmap within the label
            label_size = self.size()
            pixmap_size = pixmap.size()
            x_offset = (label_size.width() - pixmap_size.width()) / 2
            y_offset = (label_size.height() - pixmap_size.height()) / 2

            # Click position relative to the pixmap
            px = event.x() - x_offset
            py = event.y() - y_offset

            if 0 <= px <= pixmap_size.width() and 0 <= py <= pixmap_size.height():
                # Convert to fraction of original image
                x_frac = px / pixmap_size.width()
                y_frac = py / pixmap_size.height()
                self.clicked.emit(x_frac, y_frac)
        super().mousePressEvent(event)


class WebcamOverviewDialog(PersistentDialog):
    """Main dialog for webcam overview capture and calibration.

    Provides:
    - Live webcam feed display
    - Calibration point marking (click webcam + read stage position)
    - Affine calibration computation and validation
    - Multi-angle snapshot capture (current R, rotate +90)
    - Session save/load
    - Launch result viewer for tile selection

    Works without microscope connection for basic webcam viewing.
    Stage-aware features (calibration, rotate-and-capture) require connection.
    """

    session_captured = pyqtSignal(object)  # WebcamSession

    def __init__(self, app=None, parent=None):
        super().__init__(parent=parent, window_id="WebcamOverview")
        self.app = app
        self.setWindowTitle("Webcam Overview")
        self.setMinimumSize(900, 650)

        # Services (owned by this dialog)
        self._capture_service = WebcamCaptureService()
        self._calibration_service = WebcamCalibrationService()

        # Current session
        self._session = WebcamSession()
        self._current_frame: Optional[np.ndarray] = None
        self._calibration_click_pending = False
        self._current_cal_angle = 0.0

        # Result viewer windows (prevent garbage collection)
        self._result_windows = []

        self._setup_ui()
        self._connect_signals()
        self._update_button_states()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # -- Device controls --
        device_layout = QHBoxLayout()
        device_layout.addWidget(QLabel("Device:"))
        self._device_combo = QComboBox()
        self._device_combo.addItem("0 (Default)", 0)
        for i in range(1, 5):
            self._device_combo.addItem(str(i), i)
        self._device_combo.setFixedWidth(100)
        device_layout.addWidget(self._device_combo)

        self._start_stop_btn = QPushButton("Start Live")
        self._start_stop_btn.setFixedWidth(120)
        device_layout.addWidget(self._start_stop_btn)

        self._resolution_label = QLabel("")
        device_layout.addWidget(self._resolution_label)
        device_layout.addStretch()
        layout.addLayout(device_layout)

        # -- Main splitter: image | calibration --
        splitter = QSplitter(Qt.Horizontal)

        # Left: webcam image
        self._image_label = ClickableImageLabel()
        self._image_label.setText(
            "Click 'Start Live' to begin webcam feed\n\n"
            "If no camera is detected, check that the USB\n"
            "camera is plugged in and not in use by another app"
        )
        splitter.addWidget(self._image_label)

        # Right: calibration panel
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Calibration group
        cal_group = QGroupBox("Calibration")
        cal_layout = QVBoxLayout(cal_group)

        # Angle selector
        angle_layout = QHBoxLayout()
        angle_layout.addWidget(QLabel("Angle:"))
        self._cal_angle_spin = QDoubleSpinBox()
        self._cal_angle_spin.setRange(-360, 360)
        self._cal_angle_spin.setDecimals(1)
        self._cal_angle_spin.setSuffix("°")
        self._cal_angle_spin.setValue(0.0)
        angle_layout.addWidget(self._cal_angle_spin)
        self._use_current_r_btn = QPushButton("Use Current R")
        self._use_current_r_btn.setToolTip("Read current stage rotation angle")
        angle_layout.addWidget(self._use_current_r_btn)
        cal_layout.addLayout(angle_layout)

        # Point marking button
        self._mark_point_btn = QPushButton("Mark Calibration Point")
        self._mark_point_btn.setToolTip(
            "Click this, then click the webcam image at a feature whose\n"
            "stage position you know. The current stage position will be\n"
            "recorded along with the clicked pixel position."
        )
        cal_layout.addWidget(self._mark_point_btn)

        # Calibration points list
        self._cal_points_list = QListWidget()
        self._cal_points_list.setMaximumHeight(150)
        cal_layout.addWidget(self._cal_points_list)

        # Point management buttons
        point_btn_layout = QHBoxLayout()
        self._delete_point_btn = QPushButton("Delete")
        self._clear_points_btn = QPushButton("Clear All")
        point_btn_layout.addWidget(self._delete_point_btn)
        point_btn_layout.addWidget(self._clear_points_btn)
        cal_layout.addLayout(point_btn_layout)

        # Compute calibration
        self._compute_cal_btn = QPushButton("Compute Calibration")
        self._compute_cal_btn.setStyleSheet("QPushButton { font-weight: bold; }")
        cal_layout.addWidget(self._compute_cal_btn)

        self._cal_status_label = QLabel("Not calibrated")
        self._cal_status_label.setWordWrap(True)
        cal_layout.addWidget(self._cal_status_label)

        right_layout.addWidget(cal_group)

        # Multi-angle capture group
        capture_group = QGroupBox("Multi-Angle Capture")
        capture_layout = QVBoxLayout(capture_group)

        # Grid size
        grid_layout = QHBoxLayout()
        grid_layout.addWidget(QLabel("Grid:"))
        self._grid_rows_spin = QSpinBox()
        self._grid_rows_spin.setRange(2, 100)
        self._grid_rows_spin.setValue(20)
        self._grid_rows_spin.setPrefix("Rows: ")
        grid_layout.addWidget(self._grid_rows_spin)
        self._grid_cols_spin = QSpinBox()
        self._grid_cols_spin.setRange(2, 100)
        self._grid_cols_spin.setValue(20)
        self._grid_cols_spin.setPrefix("Cols: ")
        grid_layout.addWidget(self._grid_cols_spin)
        capture_layout.addLayout(grid_layout)

        # Captured views list
        self._views_label = QLabel("Captured views: (none)")
        capture_layout.addWidget(self._views_label)

        # Capture buttons
        self._capture_btn = QPushButton("Capture at Current R")
        self._capture_btn.setToolTip("Take a snapshot at the current rotation angle")
        capture_layout.addWidget(self._capture_btn)

        self._rotate_capture_btn = QPushButton("Rotate +90° && Capture")
        self._rotate_capture_btn.setToolTip(
            "Rotate the stage 90° and capture a second view.\n"
            "Requires microscope connection."
        )
        capture_layout.addWidget(self._rotate_capture_btn)

        right_layout.addWidget(capture_group)
        right_layout.addStretch()

        splitter.addWidget(right_panel)
        splitter.setSizes([600, 300])
        layout.addWidget(splitter, stretch=1)

        # -- Bottom action bar --
        action_layout = QHBoxLayout()

        self._open_result_btn = QPushButton("Open Result Viewer")
        self._open_result_btn.setToolTip("Open the captured views for tile selection")
        action_layout.addWidget(self._open_result_btn)

        self._save_session_btn = QPushButton("Save Session")
        action_layout.addWidget(self._save_session_btn)

        self._load_session_btn = QPushButton("Load Session")
        action_layout.addWidget(self._load_session_btn)

        action_layout.addStretch()

        self._close_btn = QPushButton("Close")
        action_layout.addWidget(self._close_btn)

        layout.addLayout(action_layout)

    def _connect_signals(self):
        # Device controls
        self._start_stop_btn.clicked.connect(self._on_start_stop_capture)
        self._capture_service.frame_ready.connect(self._on_frame_ready)
        self._capture_service.capture_error.connect(self._on_capture_error)

        # Calibration
        self._use_current_r_btn.clicked.connect(self._on_use_current_r)
        self._mark_point_btn.clicked.connect(self._on_mark_point)
        self._image_label.clicked.connect(self._on_image_clicked)
        self._delete_point_btn.clicked.connect(self._on_delete_point)
        self._clear_points_btn.clicked.connect(self._on_clear_points)
        self._compute_cal_btn.clicked.connect(self._on_compute_calibration)
        self._cal_angle_spin.valueChanged.connect(self._on_cal_angle_changed)

        # Multi-angle capture
        self._capture_btn.clicked.connect(self._on_capture_this_angle)
        self._rotate_capture_btn.clicked.connect(self._on_rotate_and_capture)

        # Actions
        self._open_result_btn.clicked.connect(self._on_open_result_viewer)
        self._save_session_btn.clicked.connect(self._on_save_session)
        self._load_session_btn.clicked.connect(self._on_load_session)
        self._close_btn.clicked.connect(self.close)

    # ========== Connection Helpers ==========

    def _is_connected(self) -> bool:
        """Check if microscope is connected."""
        if self.app is None:
            return False
        try:
            cs = getattr(self.app, "connection_service", None)
            if cs and hasattr(cs, "is_connected"):
                return cs.is_connected()
            # Fallback: check if connection model has a flag
            cm = getattr(self.app, "connection_model", None)
            if cm and hasattr(cm, "connected"):
                return cm.connected
        except Exception:
            pass
        return False

    def _get_current_position(self):
        """Get current stage position (x, y, z, r) or None."""
        if not self._is_connected():
            return None
        try:
            pc = getattr(self.app, "position_controller", None)
            if pc and hasattr(pc, "get_current_position"):
                pos = pc.get_current_position()
                if pos:
                    return (pos.x, pos.y, pos.z, pos.r)
        except Exception as e:
            logger.warning(f"Could not read stage position: {e}")
        return None

    def _update_button_states(self):
        """Enable/disable buttons based on connection state."""
        connected = self._is_connected()
        capturing = self._capture_service.is_capturing()
        has_views = len(self._session.views) > 0

        # Calibration needs connection (to read stage pos)
        self._use_current_r_btn.setEnabled(connected)
        self._mark_point_btn.setEnabled(connected and capturing)
        if not connected:
            self._mark_point_btn.setToolTip(
                "Connect to microscope to enable calibration"
            )
        else:
            self._mark_point_btn.setToolTip("Click this, then click the webcam image")

        # Point management
        angle = self._cal_angle_spin.value()
        points = self._calibration_service.get_points(angle)
        self._delete_point_btn.setEnabled(self._cal_points_list.currentRow() >= 0)
        self._clear_points_btn.setEnabled(len(points) > 0)
        self._compute_cal_btn.setEnabled(len(points) >= 3)

        # Capture buttons
        self._capture_btn.setEnabled(capturing)
        self._rotate_capture_btn.setEnabled(connected and capturing)
        if not connected:
            self._rotate_capture_btn.setToolTip(
                "Connect to microscope to enable rotation"
            )

        # Result viewer needs captured views
        self._open_result_btn.setEnabled(has_views)
        self._save_session_btn.setEnabled(has_views)

        # Start/stop label
        self._start_stop_btn.setText("Stop Live" if capturing else "Start Live")

    # ========== Webcam Control ==========

    def _on_start_stop_capture(self):
        if self._capture_service.is_capturing():
            self._capture_service.stop_capture()
            self._image_label.setText("Webcam stopped")
            self._resolution_label.setText("")
        else:
            device_id = self._device_combo.currentData()
            self._capture_service.set_device_id(device_id)
            self._capture_service.start_capture()

        # Delay state update to let capture start
        QTimer.singleShot(500, self._update_button_states)

    def _on_frame_ready(self, frame: np.ndarray):
        """Handle new webcam frame."""
        self._current_frame = frame
        h, w = frame.shape[:2]

        # Update resolution label (only occasionally to avoid flicker)
        if self._resolution_label.text() == "":
            self._resolution_label.setText(f"{w}x{h}")

        # Convert numpy RGB to QPixmap
        if frame.ndim == 3:
            bytes_per_line = 3 * w
            qimg = QImage(frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
        else:
            # Grayscale
            qimg = QImage(frame.data, w, h, w, QImage.Format_Grayscale8)

        pixmap = QPixmap.fromImage(qimg)

        # Draw calibration overlay if calibrated at current angle
        pixmap = self._draw_calibration_overlay(pixmap)

        # Draw click-mode indicator
        if self._calibration_click_pending:
            pixmap = self._draw_click_indicator(pixmap)

        self._image_label.set_image(pixmap)

    def _draw_calibration_overlay(self, pixmap: QPixmap) -> QPixmap:
        """Draw calibration point markers and stage position crosshair."""
        angle = self._cal_angle_spin.value()
        points = self._calibration_service.get_points(angle)
        cal = self._calibration_service.get_calibration(angle)

        if not points and cal is None:
            return pixmap

        result = QPixmap(pixmap)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw calibration points as circles
        pen = QPen(QColor(255, 100, 100), 2)
        painter.setPen(pen)
        for i, p in enumerate(points):
            x, y = int(p.pixel_u), int(p.pixel_v)
            painter.drawEllipse(x - 8, y - 8, 16, 16)
            painter.drawText(x + 12, y + 4, str(i + 1))

        # If calibrated, draw current stage position as green crosshair
        if cal is not None and self._is_connected():
            pos = self._get_current_position()
            if pos is not None:
                stage_x, stage_y, stage_z, stage_r = pos
                # Get the horizontal stage coord visible at this angle
                import math

                r_rad = math.radians(angle)
                h = stage_x * math.cos(r_rad) + stage_z * math.sin(r_rad)
                v = stage_y
                try:
                    px, py = cal.stage_to_pixel(h, v)
                    pen = QPen(QColor(0, 255, 0), 2)
                    painter.setPen(pen)
                    px, py = int(px), int(py)
                    painter.drawLine(px - 20, py, px + 20, py)
                    painter.drawLine(px, py - 20, px, py + 20)
                except Exception:
                    pass

        painter.end()
        return result

    def _draw_click_indicator(self, pixmap: QPixmap) -> QPixmap:
        """Draw a border indicating click mode is active."""
        result = QPixmap(pixmap)
        painter = QPainter(result)
        pen = QPen(QColor(255, 200, 0), 4)
        painter.setPen(pen)
        painter.drawRect(2, 2, result.width() - 4, result.height() - 4)
        painter.setPen(QPen(QColor(255, 200, 0)))
        painter.drawText(10, 20, "Click a feature in the image...")
        painter.end()
        return result

    def _on_capture_error(self, error_msg: str):
        logger.error(f"Webcam error: {error_msg}")
        QMessageBox.warning(self, "Webcam Error", error_msg)
        self._update_button_states()

    # ========== Calibration ==========

    def _on_use_current_r(self):
        """Read current stage R and set calibration angle."""
        pos = self._get_current_position()
        if pos:
            self._cal_angle_spin.setValue(pos[3])
        else:
            QMessageBox.information(
                self,
                "Position Unavailable",
                "Could not read stage position. Is the microscope connected?",
            )

    def _on_cal_angle_changed(self, angle: float):
        """Update points list when calibration angle changes."""
        self._current_cal_angle = angle
        self._refresh_cal_points_list()
        self._update_cal_status()
        self._update_button_states()

    def _on_mark_point(self):
        """Enter click mode to mark a calibration point."""
        if not self._is_connected():
            QMessageBox.information(
                self,
                "Connection Required",
                "Connect to the microscope first.\n"
                "The current stage position will be recorded when you "
                "click the webcam image.",
            )
            return

        self._calibration_click_pending = True
        self._mark_point_btn.setText("Click the webcam image...")
        self._mark_point_btn.setEnabled(False)

    def _on_image_clicked(self, x_frac: float, y_frac: float):
        """Handle click on webcam image."""
        if not self._calibration_click_pending:
            return

        self._calibration_click_pending = False
        self._mark_point_btn.setText("Mark Calibration Point")

        # Get pixel coordinates in original image space
        if self._current_frame is None:
            return
        h, w = self._current_frame.shape[:2]
        pixel_u = x_frac * w
        pixel_v = y_frac * h

        # Read current stage position
        pos = self._get_current_position()
        if pos is None:
            QMessageBox.warning(
                self,
                "Position Error",
                "Could not read stage position. Point not added.",
            )
            self._update_button_states()
            return

        stage_x, stage_y, stage_z, stage_r = pos
        angle = self._cal_angle_spin.value()

        point = WebcamCalibrationPoint(
            pixel_u=pixel_u,
            pixel_v=pixel_v,
            stage_x_mm=stage_x,
            stage_y_mm=stage_y,
            stage_z_mm=stage_z,
            stage_r_deg=stage_r,
        )
        self._calibration_service.add_point(angle, point)
        self._refresh_cal_points_list()
        self._update_button_states()

    def _on_delete_point(self):
        """Delete selected calibration point."""
        row = self._cal_points_list.currentRow()
        if row >= 0:
            angle = self._cal_angle_spin.value()
            self._calibration_service.remove_point(angle, row)
            self._refresh_cal_points_list()
            self._update_button_states()

    def _on_clear_points(self):
        """Clear all calibration points for current angle."""
        angle = self._cal_angle_spin.value()
        reply = QMessageBox.question(
            self,
            "Clear Points",
            f"Clear all calibration points for R={angle:.1f}°?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._calibration_service.clear_points(angle)
            self._refresh_cal_points_list()
            self._update_cal_status()
            self._update_button_states()

    def _on_compute_calibration(self):
        """Compute affine calibration from marked points."""
        angle = self._cal_angle_spin.value()
        points = self._calibration_service.get_points(angle)

        if len(points) < 3:
            QMessageBox.warning(
                self,
                "Not Enough Points",
                f"Need at least 3 calibration points, have {len(points)}.\n"
                "Mark more points by clicking 'Mark Calibration Point' "
                "and then clicking the webcam image.",
            )
            return

        # Get image dimensions for metadata
        img_w, img_h = 0, 0
        if self._current_frame is not None:
            img_h, img_w = self._current_frame.shape[:2]

        try:
            cal = self._calibration_service.compute_calibration(
                angle, image_width=img_w, image_height=img_h
            )
            is_good, rms, msg = self._calibration_service.validate(angle)
            self._update_cal_status()

            if is_good:
                QMessageBox.information(self, "Calibration Computed", msg)
            else:
                QMessageBox.warning(self, "Calibration Quality", msg)

        except ValueError as e:
            QMessageBox.warning(self, "Calibration Error", str(e))
        except Exception as e:
            logger.error(f"Calibration computation error: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Calibration failed:\n{e}")

    def _refresh_cal_points_list(self):
        """Refresh the calibration points list widget."""
        self._cal_points_list.clear()
        angle = self._cal_angle_spin.value()
        points = self._calibration_service.get_points(angle)
        for i, p in enumerate(points):
            text = (
                f"{i+1}. pixel=({p.pixel_u:.0f}, {p.pixel_v:.0f}) → "
                f"X={p.stage_x_mm:.3f} Y={p.stage_y_mm:.3f} Z={p.stage_z_mm:.3f}"
            )
            self._cal_points_list.addItem(text)

    def _update_cal_status(self):
        """Update calibration status label."""
        angle = self._cal_angle_spin.value()
        if self._calibration_service.is_calibrated(angle):
            is_good, rms, msg = self._calibration_service.validate(angle)
            color = "green" if is_good else "orange"
            self._cal_status_label.setText(f'<span style="color:{color}">{msg}</span>')
        else:
            points = self._calibration_service.get_points(angle)
            if points:
                self._cal_status_label.setText(
                    f"{len(points)} points marked. Need at least 3 to calibrate."
                )
            else:
                self._cal_status_label.setText("Not calibrated")

    # ========== Multi-Angle Capture ==========

    def _on_capture_this_angle(self):
        """Capture a snapshot at the current rotation angle."""
        if self._current_frame is None:
            QMessageBox.information(
                self,
                "No Frame",
                "Start the webcam live feed first.",
            )
            return

        # Try to capture a high-res snapshot
        snapshot = self._capture_service.capture_snapshot()
        if snapshot is None:
            # Fall back to current live frame
            snapshot = self._current_frame.copy()

        # Determine rotation angle
        angle = 0.0
        pos = self._get_current_position()
        if pos is not None:
            angle = pos[3]

        # Get calibration for this angle if available
        cal = self._calibration_service.get_calibration(angle)

        view = WebcamAngleView(
            rotation_angle=angle,
            image=snapshot,
            calibration=cal,
            timestamp=datetime.now().isoformat(),
            grid_rows=self._grid_rows_spin.value(),
            grid_cols=self._grid_cols_spin.value(),
        )

        self._session.add_view(view)
        self._update_views_label()
        self._update_button_states()

        logger.info(f"Captured webcam view at R={angle:.1f}°")

    def _on_rotate_and_capture(self):
        """Rotate stage +90° and capture a second view."""
        if not self._is_connected():
            QMessageBox.information(
                self,
                "Connection Required",
                "Connect to the microscope to use rotate-and-capture.",
            )
            return

        pos = self._get_current_position()
        if pos is None:
            QMessageBox.warning(
                self,
                "Position Error",
                "Could not read current stage position.",
            )
            return

        current_r = pos[3]
        target_r = current_r + 90.0

        reply = QMessageBox.question(
            self,
            "Rotate and Capture",
            f"This will rotate the stage from R={current_r:.1f}° "
            f"to R={target_r:.1f}° and capture a snapshot.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._rotate_capture_btn.setEnabled(False)
        self._rotate_capture_btn.setText("Rotating...")

        try:
            # Send rotation command
            mc = getattr(self.app, "movement_controller", None)
            if mc and hasattr(mc, "move_r"):
                mc.move_r(target_r)
            elif mc and hasattr(mc, "move_to_r"):
                mc.move_to_r(target_r)
            else:
                logger.warning("No suitable move method found on movement_controller")
                QMessageBox.warning(
                    self,
                    "Move Error",
                    "Could not find rotation move method.",
                )
                return

            # Wait for move to complete, then capture
            # Use a timer to check position and capture after settling
            self._rotation_target = target_r
            self._rotation_check_timer = QTimer()
            self._rotation_check_timer.timeout.connect(self._check_rotation_complete)
            self._rotation_check_count = 0
            self._rotation_check_timer.start(500)  # Check every 500ms

        except Exception as e:
            logger.error(f"Rotation error: {e}", exc_info=True)
            QMessageBox.warning(self, "Rotation Error", str(e))
            self._rotate_capture_btn.setEnabled(True)
            self._rotate_capture_btn.setText("Rotate +90° & Capture")

    def _check_rotation_complete(self):
        """Check if rotation move is complete, then capture."""
        self._rotation_check_count += 1

        # Timeout after 30 seconds
        if self._rotation_check_count > 60:
            self._rotation_check_timer.stop()
            self._rotate_capture_btn.setEnabled(True)
            self._rotate_capture_btn.setText("Rotate +90° & Capture")
            QMessageBox.warning(
                self,
                "Timeout",
                "Rotation did not complete within 30 seconds.",
            )
            return

        pos = self._get_current_position()
        if pos is not None:
            current_r = pos[3]
            if abs(current_r - self._rotation_target) < 1.0:
                # Close enough — wait one more interval for settling
                self._rotation_check_timer.stop()
                QTimer.singleShot(500, self._capture_after_rotation)
                return

    def _capture_after_rotation(self):
        """Capture snapshot after rotation is complete."""
        self._on_capture_this_angle()
        self._rotate_capture_btn.setEnabled(True)
        self._rotate_capture_btn.setText("Rotate +90° & Capture")
        logger.info("Captured view after rotation")

    def _update_views_label(self):
        """Update the captured views label."""
        if not self._session.views:
            self._views_label.setText("Captured views: (none)")
        else:
            angles = [f"{v.rotation_angle:.1f}°" for v in self._session.views]
            self._views_label.setText(f"Captured views: {', '.join(angles)}")

    # ========== Session Management ==========

    def _on_open_result_viewer(self):
        """Open the result viewer with captured views."""
        if not self._session.views:
            QMessageBox.information(
                self,
                "No Views",
                "Capture at least one view first.",
            )
            return

        try:
            from py2flamingo.views.dialogs.webcam_overview_result import (
                WebcamOverviewResultWindow,
            )

            window = WebcamOverviewResultWindow(
                session=self._session,
                calibration_service=self._calibration_service,
                app=self.app,
            )
            window.show()
            self._result_windows.append(window)

        except Exception as e:
            logger.error(f"Error opening result viewer: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Failed to open result viewer:\n{e}")

    def _on_save_session(self):
        """Save current session to a Zarr folder."""
        if not self._session.views:
            QMessageBox.information(
                self, "Nothing to Save", "Capture some views first."
            )
            return

        # Get save path
        start_path = str(self._get_session_browse_path())
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Folder for Webcam Session",
            start_path,
            QFileDialog.ShowDirsOnly,
        )
        if not folder:
            return

        try:
            from pathlib import Path

            from py2flamingo.visualization.webcam_session_io import (
                save_webcam_session,
            )

            save_path = save_webcam_session(self._session, Path(folder))
            self._set_session_browse_path(str(Path(folder)))

            QMessageBox.information(
                self,
                "Session Saved",
                f"Session saved to:\n{save_path}",
            )
            logger.info(f"Webcam session saved to {save_path}")

        except Exception as e:
            logger.error(f"Error saving session: {e}", exc_info=True)
            QMessageBox.critical(self, "Save Error", f"Failed to save session:\n{e}")

    def _on_load_session(self):
        """Load a saved session from a Zarr folder."""
        start_path = str(self._get_session_browse_path())
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Webcam Session Folder",
            start_path,
            QFileDialog.ShowDirsOnly,
        )
        if not folder:
            return

        try:
            from pathlib import Path

            from py2flamingo.visualization.webcam_session_io import (
                load_webcam_session,
            )

            session = load_webcam_session(Path(folder))
            self._session = session
            self._set_session_browse_path(str(Path(folder).parent))
            self._update_views_label()
            self._update_button_states()

            QMessageBox.information(
                self,
                "Session Loaded",
                f"Loaded {len(session.views)} views from:\n{folder}",
            )
            logger.info(f"Webcam session loaded from {folder}")

        except Exception as e:
            logger.error(f"Error loading session: {e}", exc_info=True)
            QMessageBox.critical(self, "Load Error", f"Failed to load session:\n{e}")

    def _get_session_browse_path(self) -> str:
        """Get last-used session folder path."""
        from pathlib import Path

        if self.app and hasattr(self.app, "config_service") and self.app.config_service:
            path = self.app.config_service.get_webcam_session_path()
            if path:
                return path
        return str(Path.home())

    def _set_session_browse_path(self, path: str):
        """Save last-used session folder path."""
        if self.app and hasattr(self.app, "config_service") and self.app.config_service:
            try:
                self.app.config_service.set_webcam_session_path(path)
            except AttributeError:
                # Method not yet added to config service
                pass

    # ========== Cleanup ==========

    def closeEvent(self, event):
        """Stop capture and clean up when dialog closes."""
        self._capture_service.stop_capture()
        super().closeEvent(event)
