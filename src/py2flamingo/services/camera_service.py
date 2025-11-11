"""
Camera subsystem service for Flamingo microscope.

Handles all camera-related commands including image size queries,
field of view, snapshots, and live view control with live data streaming.
"""

import struct
import socket
import threading
import numpy as np
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

    def get_image_size(self) -> Tuple[int, int]:
        """
        Get camera image dimensions in pixels.

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

        Opens connection to data port, sends LIVE_VIEW_START command,
        and begins receiving image frames in background thread.

        Args:
            data_port: Port for image data (default: 53718)

        Raises:
            RuntimeError: If already streaming or connection fails

        Example:
            >>> camera.set_image_callback(my_callback)
            >>> camera.start_live_view_streaming()
            >>> # Images will stream to callback
        """
        with self._streaming_lock:
            if self._streaming:
                raise RuntimeError("Live view streaming already active")

            # Connect to data port
            try:
                self._data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._data_socket.settimeout(5.0)

                # Get IP from connection service
                ip = self.connection.ip if hasattr(self.connection, 'ip') else '127.0.0.1'
                self._data_socket.connect((ip, data_port))

                self.logger.info(f"Connected to image data port {ip}:{data_port}")

            except Exception as e:
                self.logger.error(f"Failed to connect to data port: {e}")
                if self._data_socket:
                    self._data_socket.close()
                    self._data_socket = None
                raise RuntimeError(f"Failed to connect to data port: {e}")

            # Send START command on control port
            try:
                self.start_live_view()
            except Exception as e:
                self.logger.error(f"Failed to start live view: {e}")
                self._data_socket.close()
                self._data_socket = None
                raise

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
        Stop live view streaming and close data connection.

        Sends LIVE_VIEW_STOP command and stops background thread.

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

        # Close data socket
        if self._data_socket:
            try:
                self._data_socket.close()
            except:
                pass
            self._data_socket = None

        self.logger.info("Live view streaming stopped")

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

    def _data_receiver_loop(self) -> None:
        """
        Background thread that receives image data from data socket.

        Continuously reads header + image data and delivers to callback.
        """
        import time

        self.logger.info("Data receiver thread started")

        while self._streaming:
            try:
                # Read 40-byte header
                header_bytes = self._receive_exact(self._data_socket, 40)
                if not header_bytes:
                    self.logger.warning("Connection closed by server")
                    break

                # Parse header
                header = ImageHeader.from_bytes(header_bytes)

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

                # Deliver to callback
                if self._image_callback:
                    try:
                        self._image_callback(image_array, header)
                    except Exception as e:
                        self.logger.error(f"Error in image callback: {e}")

            except socket.timeout:
                # Normal timeout, continue loop
                continue

            except Exception as e:
                if self._streaming:  # Only log if we're supposed to be streaming
                    self.logger.error(f"Error in data receiver: {e}")
                break

        self.logger.info("Data receiver thread stopped")

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
