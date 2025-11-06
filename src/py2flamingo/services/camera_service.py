"""
Camera subsystem service for Flamingo microscope.

Handles all camera-related commands including image size queries,
field of view, snapshots, and live view control.
"""

from typing import Tuple

from py2flamingo.services.microscope_command_service import MicroscopeCommandService


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

    Example:
        >>> camera = CameraService(connection)
        >>> width, height = camera.get_image_size()
        >>> print(f"Camera resolution: {width}x{height}")
        Camera resolution: 2048x2048
    """

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
