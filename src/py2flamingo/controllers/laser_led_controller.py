"""
Laser and LED Controller - manages light source state and camera coordination.

This controller handles:
- Light source selection (laser or LED)
- Power/intensity control
- Enabling preview mode before camera operations
- Tracking current light source state
"""

import logging
from typing import Optional, List
from PyQt5.QtCore import QObject, pyqtSignal

from py2flamingo.services.laser_led_service import LaserLEDService, LaserInfo


class LaserLEDController(QObject):
    """
    Controller for laser and LED operations.

    Manages light source state and coordinates with camera for proper
    preview mode activation before imaging.

    Signals:
        laser_power_changed: Emitted when laser power changes (laser_index, power_percent)
        led_intensity_changed: Emitted when LED intensity changes (intensity_percent)
        preview_enabled: Emitted when preview mode is enabled (source_name)
        preview_disabled: Emitted when preview mode is disabled
        error_occurred: Emitted when an error occurs (error_message)
    """

    # Signals
    laser_power_changed = pyqtSignal(int, float)  # laser_index, power_percent
    led_intensity_changed = pyqtSignal(float)  # intensity_percent
    preview_enabled = pyqtSignal(str)  # source_name
    preview_disabled = pyqtSignal()
    error_occurred = pyqtSignal(str)  # error_message

    def __init__(self, laser_led_service: LaserLEDService):
        """
        Initialize laser/LED controller.

        Args:
            laser_led_service: LaserLEDService instance
        """
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.laser_led_service = laser_led_service

        # State tracking
        self._active_source: Optional[str] = None  # "laser_N" or "led" or None
        self._active_laser_index: Optional[int] = None
        self._laser_powers: dict = {}  # laser_index -> power_percent
        self._led_intensity: float = 50.0  # Default LED intensity

        # Initialize laser powers to 5%
        for laser in self.get_available_lasers():
            self._laser_powers[laser.index] = 5.0

        self.logger.info("LaserLEDController initialized")

    def get_available_lasers(self) -> List[LaserInfo]:
        """Get list of available lasers."""
        return self.laser_led_service.get_available_lasers()

    def is_led_available(self) -> bool:
        """Check if LED is available."""
        return self.laser_led_service.is_led_available()

    def get_laser_power(self, laser_index: int) -> float:
        """Get current laser power setting."""
        return self._laser_powers.get(laser_index, 5.0)

    def get_led_intensity(self) -> float:
        """Get current LED intensity setting."""
        return self._led_intensity

    def get_active_source(self) -> Optional[str]:
        """Get currently active light source ("laser_N" or "led" or None)."""
        return self._active_source

    def set_laser_power(self, laser_index: int, power_percent: float) -> bool:
        """
        Set laser power level.

        Args:
            laser_index: Laser index (1-4)
            power_percent: Power as percentage (0.0 - 100.0)

        Returns:
            True if successful
        """
        try:
            success = self.laser_led_service.set_laser_power(laser_index, power_percent)

            if success:
                self._laser_powers[laser_index] = power_percent
                self.laser_power_changed.emit(laser_index, power_percent)
                self.logger.info(f"Laser {laser_index} power set to {power_percent:.1f}%")
            else:
                error_msg = f"Failed to set laser {laser_index} power"
                self.logger.error(error_msg)
                self.error_occurred.emit(error_msg)

            return success

        except Exception as e:
            error_msg = f"Error setting laser {laser_index} power: {e}"
            self.logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            return False

    def set_led_intensity(self, intensity_percent: float) -> bool:
        """
        Set LED intensity level.

        Args:
            intensity_percent: Intensity as percentage (0.0 - 100.0)

        Returns:
            True if successful
        """
        try:
            success = self.laser_led_service.set_led_intensity(intensity_percent)

            if success:
                self._led_intensity = intensity_percent
                self.led_intensity_changed.emit(intensity_percent)
                self.logger.info(f"LED intensity set to {intensity_percent:.1f}%")
            else:
                error_msg = "Failed to set LED intensity"
                self.logger.error(error_msg)
                self.error_occurred.emit(error_msg)

            return success

        except Exception as e:
            error_msg = f"Error setting LED intensity: {e}"
            self.logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            return False

    def enable_laser_for_preview(self, laser_index: int) -> bool:
        """
        Enable specific laser for preview/imaging.

        This method:
        1. Sets the laser power (if not already set)
        2. Disables all other light sources
        3. Enables the specified laser in preview mode

        Args:
            laser_index: Laser index (1-4)

        Returns:
            True if successful
        """
        try:
            self.logger.info(f"Enabling laser {laser_index} for preview")

            # Disable LED if it was active
            if self._active_source == "led":
                self.laser_led_service.disable_led_preview()

            # Set power if not already set
            power = self._laser_powers.get(laser_index, 5.0)
            if not self.laser_led_service.set_laser_power(laser_index, power):
                raise RuntimeError(f"Failed to set laser {laser_index} power")

            # Enable laser preview (this automatically disables other lasers)
            if not self.laser_led_service.enable_laser_preview(laser_index):
                raise RuntimeError(f"Failed to enable laser {laser_index} preview")

            # Update state
            self._active_source = f"laser_{laser_index}"
            self._active_laser_index = laser_index

            laser_name = f"Laser {laser_index}"
            for laser in self.get_available_lasers():
                if laser.index == laser_index:
                    laser_name = laser.name
                    break

            self.preview_enabled.emit(laser_name)
            self.logger.info(f"Laser {laser_index} enabled for preview")
            return True

        except Exception as e:
            error_msg = f"Error enabling laser {laser_index} for preview: {e}"
            self.logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            return False

    def enable_led_for_preview(self) -> bool:
        """
        Enable LED for preview/imaging.

        This method:
        1. Sets the LED intensity (if not already set)
        2. Disables all lasers
        3. Enables LED in preview mode

        Returns:
            True if successful
        """
        try:
            self.logger.info("Enabling LED for preview")

            # Disable all lasers
            self.laser_led_service.disable_all_lasers()

            # Set intensity
            if not self.laser_led_service.set_led_intensity(self._led_intensity):
                raise RuntimeError("Failed to set LED intensity")

            # Enable LED preview
            if not self.laser_led_service.enable_led_preview():
                raise RuntimeError("Failed to enable LED preview")

            # Update state
            self._active_source = "led"
            self._active_laser_index = None

            self.preview_enabled.emit("LED")
            self.logger.info("LED enabled for preview")
            return True

        except Exception as e:
            error_msg = f"Error enabling LED for preview: {e}"
            self.logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            return False

    def disable_all_light_sources(self) -> bool:
        """
        Disable all light sources (lasers and LED).

        Returns:
            True if successful
        """
        try:
            self.logger.info("Disabling all light sources")

            # Disable lasers
            self.laser_led_service.disable_all_lasers()

            # Disable LED if available
            if self.is_led_available():
                self.laser_led_service.disable_led_preview()

            # Update state
            self._active_source = None
            self._active_laser_index = None

            self.preview_disabled.emit()
            self.logger.info("All light sources disabled")
            return True

        except Exception as e:
            error_msg = f"Error disabling light sources: {e}"
            self.logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            return False

    def is_preview_active(self) -> bool:
        """Check if any light source is currently in preview mode."""
        return self._active_source is not None
