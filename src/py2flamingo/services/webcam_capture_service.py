"""Webcam capture service using OpenCV.

Provides QThread-based live feed and snapshot capture from a USB webcam.
Designed for the ELP 8MP IMX179 camera (UVC-compliant) but works with
any UVC webcam.

Usage:
    service = WebcamCaptureService(device_id=0)
    service.frame_ready.connect(on_frame)
    service.start_capture()
    ...
    snapshot = service.capture_snapshot()
    service.stop_capture()
"""

import logging
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal

logger = logging.getLogger(__name__)

# Default resolutions for live preview vs snapshot
LIVE_RESOLUTION = (1024, 768)
SNAPSHOT_RESOLUTION = (3264, 2448)


class _CaptureWorker(QObject):
    """Worker that reads frames in a background thread."""

    frame_ready = pyqtSignal(object)  # np.ndarray RGB
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._cap: Optional[cv2.VideoCapture] = None
        self._timer: Optional[QTimer] = None
        self._running = False

    def open_device(self, device_id: int, width: int, height: int) -> bool:
        """Open the webcam device. Called from the worker thread."""
        try:
            self._cap = cv2.VideoCapture(device_id)
            if not self._cap.isOpened():
                self.error.emit(
                    f"Could not open webcam device {device_id}.\n\n"
                    "Check that the USB camera is plugged in and not in\n"
                    "use by another application. Try a different device\n"
                    "index if multiple cameras are connected."
                )
                return False

            # Force MJPEG for high-resolution USB 2.0 cameras
            self._cap.set(
                cv2.CAP_PROP_FOURCC,
                cv2.VideoWriter_fourcc("M", "J", "P", "G"),
            )
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

            actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            logger.info(
                f"Webcam opened: device={device_id}, "
                f"requested={width}x{height}, actual={actual_w}x{actual_h}"
            )
            return True

        except Exception as e:
            self.error.emit(f"Error opening webcam: {e}")
            return False

    def start_timer(self, interval_ms: int):
        """Start periodic frame capture."""
        self._running = True
        self._timer = QTimer()
        self._timer.timeout.connect(self._read_frame)
        self._timer.start(interval_ms)

    def stop(self):
        """Stop capture and release device."""
        self._running = False
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def read_single_frame(self) -> Optional[np.ndarray]:
        """Read a single frame (for snapshot). Returns RGB array."""
        if self._cap is None or not self._cap.isOpened():
            return None
        ret, frame = self._cap.read()
        if not ret or frame is None:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def set_resolution(self, width: int, height: int):
        """Change capture resolution on the fly."""
        if self._cap is not None and self._cap.isOpened():
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    def get_resolution(self) -> Tuple[int, int]:
        """Get current capture resolution."""
        if self._cap is not None and self._cap.isOpened():
            w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            return (w, h)
        return (0, 0)

    def _read_frame(self):
        """Read one frame and emit signal."""
        if not self._running or self._cap is None:
            return
        ret, frame = self._cap.read()
        if ret and frame is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            self.frame_ready.emit(rgb)
        else:
            # Transient read failures are common with USB cameras
            pass


class WebcamCaptureService(QObject):
    """Service for capturing frames from a USB webcam.

    Runs capture in a background QThread for smooth UI.

    Signals:
        frame_ready(np.ndarray): New RGB frame available
        capture_error(str): Error message (e.g. device not found)
    """

    frame_ready = pyqtSignal(object)  # np.ndarray RGB
    capture_error = pyqtSignal(str)

    def __init__(self, device_id: int = 0, target_fps: int = 10):
        super().__init__()
        self._device_id = device_id
        self._target_fps = target_fps
        self._thread: Optional[QThread] = None
        self._worker: Optional[_CaptureWorker] = None
        self._capturing = False

    @property
    def device_id(self) -> int:
        return self._device_id

    def set_device_id(self, device_id: int) -> None:
        """Change device index. Must stop/start capture for it to take effect."""
        self._device_id = device_id

    def start_capture(
        self, width: int = LIVE_RESOLUTION[0], height: int = LIVE_RESOLUTION[1]
    ) -> None:
        """Start live capture in a background thread."""
        if self._capturing:
            logger.warning("Capture already running")
            return

        self._thread = QThread()
        self._worker = _CaptureWorker()
        self._worker.moveToThread(self._thread)

        # Wire signals
        self._worker.frame_ready.connect(self.frame_ready)
        self._worker.error.connect(self.capture_error)

        # Use a lambda to chain open + start on the worker thread
        def _on_thread_started():
            if self._worker.open_device(self._device_id, width, height):
                interval = max(1, 1000 // self._target_fps)
                self._worker.start_timer(interval)
                self._capturing = True
            else:
                # Failed to open, clean up
                self._thread.quit()

        self._thread.started.connect(_on_thread_started)
        self._thread.start()

    def stop_capture(self) -> None:
        """Stop live capture and release the device."""
        if self._worker is not None:
            self._worker.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
        self._worker = None
        self._capturing = False

    def capture_snapshot(
        self,
        width: int = SNAPSHOT_RESOLUTION[0],
        height: int = SNAPSHOT_RESOLUTION[1],
    ) -> Optional[np.ndarray]:
        """Capture a single high-resolution frame.

        If live capture is running, temporarily switches resolution,
        grabs a frame, then switches back.

        Returns:
            RGB numpy array, or None on failure.
        """
        if self._worker is None:
            # No live feed; do a quick one-shot capture
            return self._one_shot_capture(width, height)

        # Save current resolution, switch to snapshot res, capture, switch back
        prev_res = self._worker.get_resolution()
        self._worker.set_resolution(width, height)

        # Give the camera a moment to adjust to new resolution
        # Read and discard a few frames
        frame = None
        for _ in range(5):
            frame = self._worker.read_single_frame()

        # Restore live resolution
        if prev_res[0] > 0:
            self._worker.set_resolution(prev_res[0], prev_res[1])

        return frame

    def is_capturing(self) -> bool:
        return self._capturing

    def get_resolution(self) -> Tuple[int, int]:
        """Get current capture resolution."""
        if self._worker is not None:
            return self._worker.get_resolution()
        return (0, 0)

    def _one_shot_capture(self, width: int, height: int) -> Optional[np.ndarray]:
        """Open device, capture one frame, close. For when live feed is off."""
        try:
            cap = cv2.VideoCapture(self._device_id)
            if not cap.isOpened():
                self.capture_error.emit(
                    f"Could not open webcam device {self._device_id}"
                )
                return None

            cap.set(
                cv2.CAP_PROP_FOURCC,
                cv2.VideoWriter_fourcc("M", "J", "P", "G"),
            )
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

            # Read a few frames to let the camera adjust
            frame = None
            for _ in range(5):
                ret, frame = cap.read()
            cap.release()

            if frame is not None:
                return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            return None

        except Exception as e:
            self.capture_error.emit(f"Snapshot capture error: {e}")
            return None

    @staticmethod
    def list_available_devices(max_devices: int = 5) -> List[int]:
        """Probe device indices to find available webcams.

        Returns list of working device indices.
        """
        available = []
        for i in range(max_devices):
            try:
                cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        available.append(i)
                cap.release()
            except Exception:
                pass
        return available
