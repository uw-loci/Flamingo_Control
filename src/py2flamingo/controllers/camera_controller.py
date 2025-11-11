"""
Camera controller for managing live feed and image acquisition.

Coordinates between CameraService (hardware interface) and LiveFeedView (UI),
managing state, buffering, and display parameters.
"""

import logging
import numpy as np
from collections import deque
from typing import Optional, Callable
from enum import Enum
from pathlib import Path
from datetime import datetime
from PyQt5.QtCore import QObject, pyqtSignal

from py2flamingo.services.camera_service import CameraService, ImageHeader


class CameraState(Enum):
    """Camera operation states."""
    IDLE = "idle"
    LIVE_VIEW = "live_view"
    ACQUIRING = "acquiring"
    ERROR = "error"


class CameraController(QObject):
    """
    Controller for camera operations and live feed management.

    Manages camera state, image buffering, and display parameters.
    Provides thread-safe interface between service and view.

    Signals:
        new_image: Emitted when new image received (image, header)
        state_changed: Emitted when camera state changes (CameraState)
        error_occurred: Emitted on errors (error_message)
        frame_rate_updated: Emitted with current FPS (float)
    """

    # Qt signals for thread-safe communication
    new_image = pyqtSignal(np.ndarray, object)  # (image_array, ImageHeader)
    state_changed = pyqtSignal(object)  # CameraState
    error_occurred = pyqtSignal(str)  # error message
    frame_rate_updated = pyqtSignal(float)  # FPS

    def __init__(self, camera_service: CameraService, laser_led_controller=None):
        """
        Initialize camera controller.

        Args:
            camera_service: CameraService instance for hardware communication
            laser_led_controller: Optional LaserLEDController for coordinating light sources
        """
        super().__init__()

        self.camera_service = camera_service
        self.laser_led_controller = laser_led_controller
        self.logger = logging.getLogger(__name__)

        # State
        self._state = CameraState.IDLE

        # Image buffering
        self._max_buffer_frames = 10
        self._frame_buffer = deque(maxlen=self._max_buffer_frames)

        # Display parameters
        self._display_min = 0
        self._display_max = 65535
        self._auto_scale = True

        # Exposure time (microseconds)
        self._exposure_us = 10000  # 10ms default

        # Frame rate limiting
        self._max_display_fps = 30.0
        self._last_display_time = 0
        self._min_display_interval = 1.0 / self._max_display_fps

        # Snapshot capture
        self._capture_next_frame = False
        self._captured_snapshot: Optional[tuple] = None  # (image, header)

        # Connect camera service callback
        self.camera_service.set_image_callback(self._on_image_received)

    @property
    def state(self) -> CameraState:
        """Get current camera state."""
        return self._state

    def set_state(self, new_state: CameraState) -> None:
        """
        Set camera state and emit signal.

        Args:
            new_state: New camera state
        """
        if new_state != self._state:
            self._state = new_state
            self.state_changed.emit(new_state)
            self.logger.info(f"Camera state changed to: {new_state.value}")

    def start_live_view(self) -> bool:
        """
        Start live view mode.

        Returns:
            True if started successfully, False otherwise
        """
        if self._state == CameraState.LIVE_VIEW:
            self.logger.warning("Live view already active")
            return True

        try:
            self.logger.info("Starting live view...")
            self.camera_service.start_live_view_streaming()
            self.set_state(CameraState.LIVE_VIEW)
            return True

        except Exception as e:
            error_msg = f"Failed to start live view: {e}"
            self.logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            self.set_state(CameraState.ERROR)
            return False

    def stop_live_view(self) -> bool:
        """
        Stop live view mode.

        Returns:
            True if stopped successfully, False otherwise
        """
        if self._state != CameraState.LIVE_VIEW:
            self.logger.warning("Live view not active")
            return True

        try:
            self.logger.info("Stopping live view...")
            self.camera_service.stop_live_view_streaming()
            self.set_state(CameraState.IDLE)
            return True

        except Exception as e:
            error_msg = f"Failed to stop live view: {e}"
            self.logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            return False

    def set_exposure_time(self, exposure_us: int) -> None:
        """
        Set camera exposure time.

        Args:
            exposure_us: Exposure time in microseconds

        Note:
            This sets the desired exposure time. Actual exposure time
            depends on camera hardware capabilities.
        """
        if exposure_us < 1:
            exposure_us = 1
        elif exposure_us > 1000000:  # 1 second max
            exposure_us = 1000000

        self._exposure_us = exposure_us
        self.logger.info(f"Exposure time set to {exposure_us} µs ({exposure_us/1000:.2f} ms)")

    def get_exposure_time(self) -> int:
        """
        Get current exposure time setting.

        Returns:
            Exposure time in microseconds
        """
        return self._exposure_us

    def set_display_range(self, min_val: int, max_val: int) -> None:
        """
        Set display intensity range for scaling.

        Args:
            min_val: Minimum intensity value
            max_val: Maximum intensity value
        """
        self._display_min = max(0, min_val)
        self._display_max = min(65535, max_val)
        self._auto_scale = False

        self.logger.debug(f"Display range set to [{self._display_min}, {self._display_max}]")

    def set_auto_scale(self, enabled: bool) -> None:
        """
        Enable or disable auto-scaling of display intensity.

        Args:
            enabled: True to auto-scale, False to use fixed range
        """
        self._auto_scale = enabled
        self.logger.debug(f"Auto-scale: {'enabled' if enabled else 'disabled'}")

    def get_display_range(self) -> tuple:
        """
        Get current display range.

        Returns:
            Tuple of (min_val, max_val)
        """
        return (self._display_min, self._display_max)

    def is_auto_scale(self) -> bool:
        """Check if auto-scaling is enabled."""
        return self._auto_scale

    def set_max_display_fps(self, fps: float) -> None:
        """
        Set maximum display frame rate.

        Args:
            fps: Maximum frames per second for display updates
        """
        if fps < 1:
            fps = 1
        elif fps > 60:
            fps = 60

        self._max_display_fps = fps
        self._min_display_interval = 1.0 / fps
        self.logger.info(f"Max display FPS set to {fps}")

    def get_buffered_frames(self) -> list:
        """
        Get list of buffered frames.

        Returns:
            List of (image_array, header) tuples
        """
        return list(self._frame_buffer)

    def clear_buffer(self) -> None:
        """Clear the frame buffer."""
        self._frame_buffer.clear()
        self.logger.debug("Frame buffer cleared")

    def get_frame_rate(self) -> float:
        """
        Get current frame rate from camera service.

        Returns:
            Frame rate in FPS
        """
        return self.camera_service.get_frame_rate()

    def _on_image_received(self, image: np.ndarray, header: ImageHeader) -> None:
        """
        Callback for when camera service receives a new image.

        Handles buffering, scaling, and rate limiting before emitting to view.
        Also captures snapshots when requested.

        Args:
            image: Image array (uint16)
            header: Image metadata
        """
        import time

        # Check if we're capturing a snapshot
        if self._capture_next_frame:
            self._captured_snapshot = (image.copy(), header)
            self._capture_next_frame = False
            self.logger.info("Snapshot captured from data stream")

        # Add to buffer
        self._frame_buffer.append((image.copy(), header))

        # Apply frame rate limiting for display
        current_time = time.time()
        if current_time - self._last_display_time < self._min_display_interval:
            return  # Skip this frame to maintain target FPS

        self._last_display_time = current_time

        # Apply display scaling if auto-scale enabled
        if self._auto_scale and header.image_scale_min != header.image_scale_max:
            # Use header-provided scale values
            self._display_min = header.image_scale_min
            self._display_max = header.image_scale_max

        # Emit to UI (Qt signal handles thread safety)
        try:
            self.new_image.emit(image, header)

            # Update frame rate every 10 frames
            if header.frame_number % 10 == 0:
                fps = self.get_frame_rate()
                self.frame_rate_updated.emit(fps)

        except Exception as e:
            self.logger.error(f"Error emitting image: {e}")

    def get_image_dimensions(self) -> Optional[tuple]:
        """
        Get camera image dimensions.

        Returns:
            Tuple of (width, height) or None if not available
        """
        try:
            return self.camera_service.get_image_size()
        except Exception as e:
            self.logger.error(f"Failed to get image dimensions: {e}")
            return None

    def get_latest_frame(self) -> Optional[tuple]:
        """
        Get the most recent buffered frame.

        Returns:
            Tuple of (image_array, header) or None if no frames available
        """
        if len(self._frame_buffer) > 0:
            return self._frame_buffer[-1]
        return None

    def take_snapshot_and_save(self, sample_name: str, save_directory: str) -> Optional[str]:
        """
        Take a snapshot and save it with auto-incrementing filename.

        This method reuses the existing data socket connection (if live view is active)
        or temporarily connects to capture the snapshot. It does NOT duplicate any
        communication infrastructure.

        Args:
            sample_name: Sample name for filename
            save_directory: Directory to save snapshot

        Returns:
            Path to saved file, or None if failed

        Example:
            >>> path = controller.take_snapshot_and_save("sample_01", "/data/snapshots")
            >>> print(f"Saved to {path}")
        """
        try:
            # Check if we need to temporarily connect data socket
            was_streaming = self._state == CameraState.LIVE_VIEW

            if not was_streaming:
                # Not in live view, need to connect data socket temporarily
                self.logger.info("Connecting data socket for snapshot...")
                self.camera_service.start_live_view_streaming()
                import time
                time.sleep(0.5)  # Give socket time to connect

            # Set flag to capture next frame
            self._capture_next_frame = True
            self._captured_snapshot = None

            # Send snapshot command (reuses existing communication)
            self.logger.info("Sending snapshot command...")
            self.camera_service.take_snapshot()

            # Wait for image to arrive (via existing callback mechanism)
            import time
            timeout = 5.0  # 5 second timeout
            start_time = time.time()

            while self._captured_snapshot is None and (time.time() - start_time) < timeout:
                time.sleep(0.1)

            if self._captured_snapshot is None:
                raise RuntimeError("Timeout waiting for snapshot image")

            # Save the captured image
            image, header = self._captured_snapshot
            filename = self._generate_snapshot_filename(sample_name, save_directory)

            self._save_image(image, filename)
            self.logger.info(f"Snapshot saved to {filename}")

            # Clean up temporary connection if needed
            if not was_streaming:
                self.logger.info("Disconnecting temporary data socket...")
                self.camera_service.stop_live_view_streaming()

            return filename

        except Exception as e:
            error_msg = f"Failed to capture snapshot: {e}"
            self.logger.error(error_msg)
            self.error_occurred.emit(error_msg)

            # Clean up on error
            if not was_streaming and self.camera_service._streaming:
                try:
                    self.camera_service.stop_live_view_streaming()
                except:
                    pass

            return None

    def _generate_snapshot_filename(self, sample_name: str, save_directory: str) -> str:
        """
        Generate snapshot filename with auto-incrementing number.

        Format: {sample_name}_{YYYYMMDD}_{NNN}.tif
        Where NNN is auto-incremented based on existing files.

        Args:
            sample_name: Sample name
            save_directory: Directory to save in

        Returns:
            Full path to snapshot file
        """
        # Create directory if it doesn't exist
        save_path = Path(save_directory)
        save_path.mkdir(parents=True, exist_ok=True)

        # Get current date
        date_str = datetime.now().strftime("%Y%m%d")

        # Find existing snapshots for this sample and date
        pattern = f"{sample_name}_{date_str}_*.tif"
        existing_files = list(save_path.glob(pattern))

        # Determine next number
        if not existing_files:
            next_num = 1
        else:
            # Extract numbers from existing files
            numbers = []
            for file in existing_files:
                try:
                    # Extract number from filename: sample_20231115_005.tif -> 5
                    parts = file.stem.split('_')
                    if len(parts) >= 3:
                        num = int(parts[-1])
                        numbers.append(num)
                except ValueError:
                    continue

            next_num = max(numbers) + 1 if numbers else 1

        # Generate filename with zero-padded number
        filename = f"{sample_name}_{date_str}_{next_num:03d}.tif"
        full_path = save_path / filename

        return str(full_path)

    def _save_image(self, image: np.ndarray, filename: str) -> None:
        """
        Save image to TIFF file.

        Args:
            image: Image array (uint16)
            filename: Path to save file
        """
        try:
            from PIL import Image

            # Convert to PIL Image and save as 16-bit TIFF
            pil_image = Image.fromarray(image.astype(np.uint16), mode='I;16')
            pil_image.save(filename, format='TIFF')

            self.logger.info(f"Image saved: {filename}")

        except ImportError:
            # Fallback to numpy save if PIL not available
            self.logger.warning("PIL not available, saving as numpy array")
            np.save(filename.replace('.tif', '.npy'), image)
