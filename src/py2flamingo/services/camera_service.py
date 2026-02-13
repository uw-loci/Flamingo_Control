"""
Camera subsystem service for Flamingo microscope.

Handles all camera-related commands including image size queries,
field of view, snapshots, and live view control with live data streaming.
"""

import struct
import socket
import threading
import numpy as np
from collections import deque
from typing import Tuple, Optional, Callable
from dataclasses import dataclass

from py2flamingo.services.microscope_command_service import MicroscopeCommandService


@dataclass
class ImageHeader:
    """
    Image header structure (40 bytes total).

    Received before each image frame with metadata about the image.
    Structure: 10 x uint32 (4 bytes each) = 40 bytes
    """
    image_size: int       # Total size of image data in bytes
    image_width: int      # Width in pixels
    image_height: int     # Height in pixels
    image_scale_min: int  # Minimum intensity value in image (for display scaling)
    image_scale_max: int  # Maximum intensity value in image (for display scaling)
    timestamp_ms: int     # Timestamp in milliseconds
    frame_number: int     # Sequential frame number
    exposure_us: int      # Exposure time in microseconds
    reserved1: int        # Reserved field
    reserved2: int        # Reserved field

    @classmethod
    def from_bytes(cls, data: bytes) -> 'ImageHeader':
        """
        Parse 40-byte header from binary data.

        Args:
            data: 40 bytes of header data

        Returns:
            ImageHeader instance

        Raises:
            ValueError: If data is not 40 bytes
        """
        if len(data) != 40:
            raise ValueError(f"Header must be 40 bytes, got {len(data)}")

        # Unpack 10 uint32 values (little-endian)
        values = struct.unpack('<10I', data)

        return cls(
            image_size=values[0],
            image_width=values[1],
            image_height=values[2],
            image_scale_min=values[3],
            image_scale_max=values[4],
            timestamp_ms=values[5],
            frame_number=values[6],
            exposure_us=values[7],
            reserved1=values[8],
            reserved2=values[9]
        )


class CameraCommandCode:
    """Camera subsystem command codes from CommandCodes.h (0x3000 range)."""

    # Query commands
    IMAGE_SIZE_GET = 12327  # 0x3027
    PIXEL_FIELD_OF_VIEW_GET = 12343  # 0x3037
    EXPOSURE_GET = 12298  # 0x300A - get exposure time (returns int32Data0 in microseconds)

    # Action commands
    SNAPSHOT = 12294  # 0x3006 - take single image
    LIVE_VIEW_START = 12295  # 0x3007 - start continuous imaging
    LIVE_VIEW_STOP = 12296  # 0x3008 - stop continuous imaging
    WORKFLOW_START = 12292  # 0x3004
    WORKFLOW_STOP = 12293  # 0x3005


class CameraService(MicroscopeCommandService):
    """
    Service for camera operations on Flamingo microscope.

    Provides high-level methods for camera control that handle
    command encoding, socket communication, and response parsing.
    Also manages live data streaming from the image data port (53718).

    Example:
        >>> camera = CameraService(connection)
        >>> width, height = camera.get_image_size()
        >>> print(f"Camera resolution: {width}x{height}")
        Camera resolution: 2048x2048
        >>>
        >>> # Start live view with callback
        >>> def on_image(image, header):
        ...     print(f"Received {header.image_width}x{header.image_height} image")
        >>> camera.set_image_callback(on_image)
        >>> camera.start_live_view_streaming()
        >>> # Images will be delivered to callback
        >>> camera.stop_live_view_streaming()
    """

    def __init__(self, connection):
        """
        Initialize camera service with live streaming support.

        Args:
            connection: MVCConnectionService instance
        """
        super().__init__(connection)

        # Live data streaming
        self._data_socket: Optional[socket.socket] = None
        self._data_thread: Optional[threading.Thread] = None
        self._streaming = False
        self._streaming_lock = threading.Lock()

        # Image callback
        self._image_callback: Optional[Callable] = None

        # Frame rate calculation
        self._last_frame_time: float = 0
        self._frame_times: list = []
        self._max_frame_history = 30

        # Fast frame buffer (thread-safe queue)
        # Camera sends frames fast -> buffer them ALL
        # Downstream processing pulls and drops as needed
        self._frame_buffer_lock = threading.Lock()
        self._frame_buffer = deque(maxlen=20)  # Keep last 20 frames max
        self._dropped_frame_count = 0

        # Cached image size from live streaming (when camera query returns 0x0)
        self._cached_image_size: Optional[Tuple[int, int]] = None

    def get_image_size(self) -> Tuple[int, int]:
        """
        Get camera image dimensions in pixels.

        First queries the camera directly. If the camera returns 0x0
        (which can happen when not streaming), uses cached size from
        previous live streaming session if available.

        Returns:
            Tuple of (width, height) in pixels, e.g., (2048, 2048)

        Raises:
            RuntimeError: If command fails or microscope not connected

        Example:
            >>> width, height = camera_service.get_image_size()
            >>> print(f"Setting up buffer for {width}x{height} image")
        """
        self.logger.info("Getting camera image size...")

        result = self._query_command(
            CameraCommandCode.IMAGE_SIZE_GET,
            "CAMERA_IMAGE_SIZE_GET"
        )

        if not result['success']:
            raise RuntimeError(f"Failed to get image size: {result.get('error', 'Unknown error')}")

        params = result['parsed']['params']
        width = params[3]   # X dimension in Param[3]
        height = params[4]  # Y dimension in Param[4]

        # If camera returns 0x0, try cached value from live streaming
        if width == 0 or height == 0:
            if self._cached_image_size:
                width, height = self._cached_image_size
                self.logger.info(f"Camera returned 0x0, using cached size: {width}x{height} pixels")
            else:
                self.logger.warning("Camera returned 0x0 and no cached size available")
        else:
            # Update cache with fresh value
            self._cached_image_size = (width, height)

        self.logger.info(f"Camera image size: {width}x{height} pixels")
        return (width, height)

    def get_pixel_field_of_view(self) -> float:
        """
        Get pixel field of view (physical size per pixel).

        Returns:
            Pixel size in millimeters per pixel

        Raises:
            RuntimeError: If command fails or microscope not connected

        Example:
            >>> pixel_size_mm = camera_service.get_pixel_field_of_view()
            >>> print(f"Each pixel = {pixel_size_mm * 1000:.3f} micrometers")
            Each pixel = 0.253 micrometers
        """
        self.logger.info("Getting pixel field of view...")

        result = self._query_command(
            CameraCommandCode.PIXEL_FIELD_OF_VIEW_GET,
            "CAMERA_PIXEL_FIELD_OF_VIEW_GET"
        )

        if not result['success']:
            raise RuntimeError(f"Failed to get pixel FOV: {result.get('error', 'Unknown error')}")

        # Pixel FOV returned in Value field (double)
        pixel_fov = result['parsed']['value']

        self.logger.info(f"Pixel field of view: {pixel_fov} mm/pixel ({pixel_fov * 1000:.3f} µm/pixel)")
        return pixel_fov

    def get_exposure(self) -> float:
        """
        Get current camera exposure time in microseconds.

        Queries the camera via CAMERA_EXPOSURE_GET (0x300A) command.

        Returns:
            Exposure time in microseconds

        Raises:
            RuntimeError: If command fails or microscope not connected

        Example:
            >>> exposure_us = camera_service.get_exposure()
            >>> print(f"Exposure: {exposure_us} us ({exposure_us/1000:.2f} ms)")
            Exposure: 25000.0 us (25.00 ms)
        """
        self.logger.info("Getting camera exposure time...")

        result = self._query_command(
            CameraCommandCode.EXPOSURE_GET,
            "CAMERA_EXPOSURE_GET"
        )

        if not result['success']:
            raise RuntimeError(f"Failed to get exposure: {result.get('error', 'Unknown error')}")

        parsed = result['parsed']

        # Exposure is returned in int32Data0 (params[3]) in microseconds
        # Reference: PCOBase.cpp getExposureTime() sets pscmd->int32Data0 = exposure
        params = parsed['params']
        exposure_us = float(params[3]) if len(params) > 3 else 0.0

        self.logger.info(f"Camera exposure: {exposure_us} us ({exposure_us/1000:.2f} ms)")
        return exposure_us

    def take_snapshot(self) -> None:
        """
        Take a single snapshot image.

        Triggers camera to capture one frame. The image data is typically
        sent via the live view socket (separate from command socket).

        Raises:
            RuntimeError: If command fails or microscope not connected

        Example:
            >>> camera_service.take_snapshot()
            >>> # Wait for image data on live view socket
        """
        self.logger.info("Taking snapshot...")

        result = self._send_command(
            CameraCommandCode.SNAPSHOT,
            "CAMERA_SNAPSHOT"
        )

        if not result['success']:
            raise RuntimeError(f"Failed to take snapshot: {result.get('error', 'Unknown error')}")

        self.logger.info("Snapshot command sent successfully")

    def start_live_view(self) -> None:
        """
        Start continuous image acquisition (live view mode).

        Camera will continuously capture and stream images via the
        live view socket until stop_live_view() is called.

        Raises:
            RuntimeError: If command fails or microscope not connected

        Example:
            >>> camera_service.start_live_view()
            >>> # Process images from live view socket
            >>> camera_service.stop_live_view()
        """
        self.logger.info("Starting live view...")

        result = self._send_command(
            CameraCommandCode.LIVE_VIEW_START,
            "CAMERA_LIVE_VIEW_START"
        )

        if not result['success']:
            raise RuntimeError(f"Failed to start live view: {result.get('error', 'Unknown error')}")

        self.logger.info("Live view started successfully")

    def stop_live_view(self) -> None:
        """
        Stop continuous image acquisition.

        Stops the live view mode started by start_live_view().

        Raises:
            RuntimeError: If command fails or microscope not connected

        Example:
            >>> camera_service.stop_live_view()
        """
        self.logger.info("Stopping live view...")

        result = self._send_command(
            CameraCommandCode.LIVE_VIEW_STOP,
            "CAMERA_LIVE_VIEW_STOP"
        )

        if not result['success']:
            raise RuntimeError(f"Failed to stop live view: {result.get('error', 'Unknown error')}")

        self.logger.info("Live view stopped successfully")

    # ========================================================================
    # Live Data Streaming Methods
    # ========================================================================

    def set_image_callback(self, callback: Optional[Callable[[np.ndarray, ImageHeader], None]]) -> None:
        """
        Set callback function for received images.

        Args:
            callback: Function called with (image_array, header) for each frame,
                     or None to clear callback

        Example:
            >>> def my_callback(image, header):
            ...     print(f"Frame {header.frame_number}: {image.shape}")
            >>> camera.set_image_callback(my_callback)
        """
        self._image_callback = callback

    def start_live_view_streaming(self, data_port: int = 53718) -> None:
        """
        Start live view with image data streaming.

        Uses the existing live socket (already connected to port 53718)
        instead of creating a new connection.

        Args:
            data_port: Port for image data (default: 53718) - kept for compatibility but unused

        Raises:
            RuntimeError: If already streaming or connection fails

        Example:
            >>> camera.set_image_callback(my_callback)
            >>> camera.start_live_view_streaming()
            >>> # Images will stream to callback
        """
        import time

        with self._streaming_lock:
            if self._streaming:
                raise RuntimeError("Live view streaming already active")

            # Send START command on control port FIRST
            # This tells the microscope to start streaming on the live socket
            try:
                self.logger.info("Sending LIVE_VIEW_START command...")
                self.start_live_view()
            except Exception as e:
                self.logger.error(f"Failed to start live view: {e}")
                raise

            # Wait for microscope to start streaming (give it time to initialize camera)
            self.logger.info("Waiting for microscope to start streaming...")
            time.sleep(0.5)

            # Use the EXISTING live socket (already connected during initial connection)
            try:
                # Get the live socket from the connection service
                if hasattr(self.connection, 'tcp_connection') and hasattr(self.connection.tcp_connection, '_live_socket'):
                    # MVCConnectionService
                    self._data_socket = self.connection.tcp_connection._live_socket
                    ip = self.connection.tcp_connection._ip
                    port = 53718
                elif hasattr(self.connection, 'live_client'):
                    # ConnectionService
                    self._data_socket = self.connection.live_client
                    ip = self.connection.ip
                    port = 53718
                else:
                    raise RuntimeError("Cannot access live socket from connection service")

                if self._data_socket is None:
                    raise RuntimeError("Live socket is not connected")

                self._data_socket.settimeout(5.0)
                self.logger.info(f"Using existing live socket for image data ({ip}:{port})")

            except Exception as e:
                self.logger.error(f"Failed to access live socket: {e}")
                self._data_socket = None
                # Try to stop live view on microscope since we can't receive data
                try:
                    self.stop_live_view()
                except:
                    pass
                raise RuntimeError(f"Failed to access live socket: {e}")

            # Start streaming thread
            self._streaming = True
            self._data_thread = threading.Thread(
                target=self._data_receiver_loop,
                daemon=True,
                name="CameraDataReceiver"
            )
            self._data_thread.start()

            self.logger.info("Live view streaming started")

    def stop_live_view_streaming(self) -> None:
        """
        Stop live view streaming.

        Sends LIVE_VIEW_STOP command and stops background thread.
        Note: Does NOT close the live socket as it's owned by tcp_connection.

        Example:
            >>> camera.stop_live_view_streaming()
        """
        with self._streaming_lock:
            if not self._streaming:
                self.logger.warning("Live view streaming not active")
                return

            # Signal thread to stop
            self._streaming = False

        # Send STOP command
        try:
            self.stop_live_view()
        except Exception as e:
            self.logger.error(f"Error stopping live view: {e}")

        # Wait for thread to finish
        if self._data_thread and self._data_thread.is_alive():
            self._data_thread.join(timeout=2.0)

        # Clear socket reference (but don't close it - tcp_connection owns it)
        self._data_socket = None

        self.logger.info("Live view streaming stopped")

    def ensure_data_receiver_running(self) -> None:
        """
        Start the data receiver thread without sending LIVE_VIEW_START.

        This is a "listen-only" mode for use during tile workflows where
        the workflow handles its own camera acquisition. Sending LIVE_VIEW_START
        between queued workflows crashes the server, so this method only sets up
        the socket reader thread to capture frames already being sent.

        No-op if already streaming.
        """
        with self._streaming_lock:
            if self._streaming:
                self.logger.debug("Data receiver already running, skipping")
                return

            # Use the EXISTING live socket (already connected during initial connection)
            try:
                if hasattr(self.connection, 'tcp_connection') and hasattr(self.connection.tcp_connection, '_live_socket'):
                    self._data_socket = self.connection.tcp_connection._live_socket
                    ip = self.connection.tcp_connection._ip
                    port = 53718
                elif hasattr(self.connection, 'live_client'):
                    self._data_socket = self.connection.live_client
                    ip = self.connection.ip
                    port = 53718
                else:
                    raise RuntimeError("Cannot access live socket from connection service")

                if self._data_socket is None:
                    raise RuntimeError("Live socket is not connected")

                self._data_socket.settimeout(5.0)
                self.logger.info(f"Data receiver using existing live socket ({ip}:{port})")

            except Exception as e:
                self.logger.error(f"Failed to access live socket for data receiver: {e}")
                self._data_socket = None
                raise RuntimeError(f"Failed to access live socket: {e}")

            # Start streaming thread
            self._streaming = True
            self._data_thread = threading.Thread(
                target=self._data_receiver_loop,
                daemon=True,
                name="CameraDataReceiver"
            )
            self._data_thread.start()

            self.logger.info("Data receiver started (no LIVE_VIEW_START sent)")

    def stop_data_receiver(self) -> None:
        """
        Stop the data receiver thread without sending LIVE_VIEW_STOP.

        Counterpart to ensure_data_receiver_running(). Tears down the
        receiver thread but does not send any commands to the server.
        """
        with self._streaming_lock:
            if not self._streaming:
                self.logger.warning("Data receiver not active")
                return

            # Signal thread to stop
            self._streaming = False

        # Wait for thread to finish
        if self._data_thread and self._data_thread.is_alive():
            self._data_thread.join(timeout=2.0)
            if self._data_thread.is_alive():
                self.logger.warning("Data receiver thread did not stop within 2s timeout")

        # Clear socket reference (but don't close it - tcp_connection owns it)
        self._data_socket = None

        self.logger.info("Stopped data receiver (no LIVE_VIEW_STOP sent)")

    def is_streaming(self) -> bool:
        """
        Check if live view streaming is active.

        Returns:
            True if streaming, False otherwise
        """
        return self._streaming

    def get_frame_rate(self) -> float:
        """
        Get current frame rate in FPS.

        Returns:
            Frame rate in frames per second, or 0 if not streaming

        Example:
            >>> fps = camera.get_frame_rate()
            >>> print(f"Current FPS: {fps:.1f}")
        """
        if len(self._frame_times) < 2:
            return 0.0

        # Calculate average from recent frame times
        time_diffs = [self._frame_times[i] - self._frame_times[i-1]
                     for i in range(1, len(self._frame_times))]
        avg_diff = sum(time_diffs) / len(time_diffs)

        if avg_diff > 0:
            return 1.0 / avg_diff
        return 0.0

    def get_latest_frame(self, clear_buffer: bool = True) -> Optional[tuple]:
        """
        Get the latest frame from buffer.

        FRAME DROPPING STRATEGY:
        When clear_buffer=True (default), this method:
        1. Gets the most recent frame
        2. Clears all older frames from buffer
        3. Returns the latest frame for processing

        This ensures processing always works on fresh data and never
        gets stuck processing a backlog of old frames.

        Args:
            clear_buffer: If True, clear all accumulated frames after getting latest.
                         This prevents processing backlog and ensures display updates.

        Returns:
            Tuple of (image_array, header) or None if buffer empty

        Example:
            >>> # Get latest and drop accumulated frames
            >>> frame = camera.get_latest_frame(clear_buffer=True)
            >>> if frame:
            ...     image, header = frame
            ...     # Process this frame
            ...     # All older frames are now discarded
        """
        with self._frame_buffer_lock:
            if len(self._frame_buffer) == 0:
                return None

            # Get the newest frame (rightmost in deque)
            latest = self._frame_buffer[-1]

            if clear_buffer:
                # Clear the buffer to drop accumulated frames
                # This prevents processing backlog
                dropped_count = len(self._frame_buffer) - 1
                self._frame_buffer.clear()
                if dropped_count > 0:
                    self.logger.debug(f"Dropped {dropped_count} accumulated frames")

            return latest

    def drain_all_frames(self) -> list:
        """Get ALL buffered frames and clear buffer.

        Unlike get_latest_frame() which keeps only the newest,
        this returns every frame in the buffer. Used for tile
        workflow mode where each frame is a unique Z-plane.

        Returns:
            List of (image, header) tuples, oldest first
        """
        with self._frame_buffer_lock:
            frames = list(self._frame_buffer)
            self._frame_buffer.clear()
            return frames

    def get_buffer_size(self) -> int:
        """
        Get current number of frames in buffer.

        Returns:
            Number of buffered frames
        """
        with self._frame_buffer_lock:
            return len(self._frame_buffer)

    def clear_frame_buffer(self) -> None:
        """Clear all buffered frames."""
        with self._frame_buffer_lock:
            count = len(self._frame_buffer)
            self._frame_buffer.clear()
            if count > 0:
                self.logger.info(f"Cleared {count} frames from buffer")

    def set_tile_mode_buffer(self, enabled: bool) -> None:
        """Switch to larger buffer for tile workflows where every frame matters.

        During tile workflows, the GUI thread may block on visualization transforms,
        preventing drain_all_frames() from being called. A larger buffer prevents
        frame loss during these stalls.

        Args:
            enabled: True to use large buffer (500 frames), False to restore default (20)
        """
        with self._frame_buffer_lock:
            frames = list(self._frame_buffer)
            new_maxlen = 500 if enabled else 20
            self._frame_buffer = deque(frames, maxlen=new_maxlen)
            if enabled:
                self._dropped_frame_count = 0
            self.logger.info(f"Frame buffer resized to maxlen={new_maxlen} "
                           f"(tile_mode={'ON' if enabled else 'OFF'}, "
                           f"preserved {len(frames)} frames)")

    def _data_receiver_loop(self) -> None:
        """
        Background thread that receives image data from data socket.

        CRITICAL DESIGN: This thread ONLY receives and buffers frames.
        No processing, no callbacks, no delays - just fast acquisition.
        Downstream consumers pull from buffer and drop frames as needed.

        This ensures camera data is never blocked, preventing socket overflow.
        """
        import time

        self.logger.info("Data receiver thread started")
        self.logger.info(f"Waiting for image data on socket (timeout={self._data_socket.gettimeout()}s)...")
        frames_received = 0
        last_log_time = time.time()

        while self._streaming:
            try:
                # Log every 2 seconds while waiting
                current_time = time.time()
                if current_time - last_log_time > 2.0:
                    self.logger.debug(f"Still waiting for frames... (received {frames_received} so far)")
                    last_log_time = current_time

                # Read 40-byte header
                header_bytes = self._receive_exact(self._data_socket, 40)
                if not header_bytes:
                    self.logger.warning("Connection closed by server")
                    break

                # Parse header
                header = ImageHeader.from_bytes(header_bytes)

                if frames_received == 0:
                    self.logger.info(f"First frame received! Size: {header.image_width}x{header.image_height}, {header.image_size} bytes")
                    # Cache the image size for later use when query returns 0x0
                    if header.image_width > 0 and header.image_height > 0:
                        self._cached_image_size = (header.image_width, header.image_height)
                        self.logger.debug(f"Cached image size: {header.image_width}x{header.image_height}")

                # Read image data (16-bit pixels)
                image_data_bytes = self._receive_exact(self._data_socket, header.image_size)
                if not image_data_bytes:
                    self.logger.warning("Connection closed while reading image data")
                    break

                # Convert to numpy array (16-bit unsigned)
                image_array = np.frombuffer(image_data_bytes, dtype=np.uint16)
                image_array = image_array.reshape((header.image_height, header.image_width))

                # Update frame rate tracking
                current_time = time.time()
                self._frame_times.append(current_time)
                if len(self._frame_times) > self._max_frame_history:
                    self._frame_times.pop(0)

                frames_received += 1

                if frames_received % 10 == 0:
                    self.logger.debug(f"Received {frames_received} frames")


                # Fast buffering: Just add to queue (deque handles overflow by dropping oldest)
                with self._frame_buffer_lock:
                    if len(self._frame_buffer) >= self._frame_buffer.maxlen:
                        self._dropped_frame_count += 1
                        if self._dropped_frame_count % 50 == 1:
                            self.logger.warning(
                                f"Frame buffer full (maxlen={self._frame_buffer.maxlen}), "
                                f"dropping oldest frame ({self._dropped_frame_count} total dropped)")
                    self._frame_buffer.append((image_array, header))

                # Optional: Trigger callback for notification (but don't do work in it!)
                # Callback should just signal that data is available, not process it
                if self._image_callback:
                    try:
                        # Pass notification that frame is ready
                        # Controller should pull from buffer, not process here
                        self._image_callback(image_array, header)
                    except Exception as e:
                        self.logger.error(f"Error in image callback: {e}")

            except socket.timeout:
                # Socket timeout - no data received within timeout period
                # This is normal if server isn't sending data yet
                self.logger.debug(f"Socket timeout while waiting for data (received {frames_received} frames so far)")
                continue

            except Exception as e:
                if self._streaming:  # Only log if we're supposed to be streaming
                    self.logger.error(f"Error in data receiver: {e}")
                    import traceback
                    self.logger.error(f"Traceback: {traceback.format_exc()}")
                break

        self.logger.info(f"Data receiver thread stopped (received {frames_received} frames)")

    def _receive_exact(self, sock: socket.socket, num_bytes: int) -> Optional[bytes]:
        """
        Receive exactly num_bytes from socket.

        Args:
            sock: Socket to read from
            num_bytes: Number of bytes to receive

        Returns:
            Bytes received, or None if connection closed

        Raises:
            socket.timeout: If receive times out
        """
        data = bytearray()
        while len(data) < num_bytes:
            try:
                chunk = sock.recv(num_bytes - len(data))
                if not chunk:
                    return None  # Connection closed
                data.extend(chunk)
            except socket.timeout:
                raise  # Let caller handle timeout

        return bytes(data)
