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
        self._active_source: Optional[str] = None  # "laser_N" or "led_R/G/B/W" or None
        self._active_laser_index: Optional[int] = None
        self._laser_powers: dict = {}  # laser_index -> power_percent

        # LED state - separate tracking for each color
        self._led_color: int = 1  # Default to Green (0=Red, 1=Green, 2=Blue, 3=White)
        self._led_intensities: dict = {  # color -> intensity_percent
            0: 50.0,  # Red
            1: 50.0,  # Green
            2: 50.0,  # Blue
            3: 50.0,  # White
        }

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

    def get_led_color(self) -> int:
        """Get current LED color selection (0=Red, 1=Green, 2=Blue, 3=White)."""
        return self._led_color

    def get_led_intensity(self, led_color: Optional[int] = None) -> float:
        """
        Get LED intensity setting for specified color.

        Args:
            led_color: LED color (0=Red, 1=Green, 2=Blue, 3=White). If None, uses current selection.

        Returns:
            Intensity as percentage (0.0 - 100.0)
        """
        if led_color is None:
            led_color = self._led_color
        return self._led_intensities.get(led_color, 50.0)

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

    def set_led_color(self, led_color: int) -> None:
        """
        Set LED color selection.

        Args:
            led_color: LED color (0=Red, 1=Green, 2=Blue, 3=White)
        """
        if 0 <= led_color <= 3:
            self._led_color = led_color
            self.logger.info(f"LED color set to {led_color}")

    def set_led_intensity(self, led_color: int, intensity_percent: float) -> bool:
        """
        Set LED intensity level for specified color.

        Args:
            led_color: LED color (0=Red, 1=Green, 2=Blue, 3=White)
            intensity_percent: Intensity as percentage (0.0 - 100.0)

        Returns:
            True if successful
        """
        try:
            success = self.laser_led_service.set_led_intensity(led_color, intensity_percent)

            if success:
                self._led_intensities[led_color] = intensity_percent
                self.led_intensity_changed.emit(intensity_percent)
                color_names = ["Red", "Green", "Blue", "White"]
                self.logger.debug(f"{color_names[led_color]} LED intensity set to {intensity_percent:.1f}%")
            else:
                error_msg = f"Failed to set LED intensity for color {led_color}"
                self.logger.error(error_msg)
                self.error_occurred.emit(error_msg)

            return success

        except Exception as e:
            error_msg = f"Error setting LED intensity: {e}"
            self.logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            return False

    def enable_laser_for_preview(self, laser_index: int, path: str = "left") -> bool:
        """
        Enable specific laser for preview/imaging.

        CRITICAL: Follows exact command sequence from working C++ implementation:
        1. Disable LED if active (0x4003)
        2. Enable laser preview mode (0x2004 with laser_index) - MUST come first!
        3. Set laser power (0x2001) - server only responds if laser enabled first
        4. Enable illumination on selected path (0x7004 or 0x7006) - coordinates exposure timing
        5. Ready for snapshot/live view

        Args:
            laser_index: Laser index (1-4)
            path: Light path selection - "left" or "right" (TSPIM only, default: "left")

        Returns:
            True if successful
        """
        try:
            path_display = path.upper()
            self.logger.info(f"Enabling laser {laser_index} for preview on {path_display} path (full sequence)")

            # Step 1: Disable LED if it was active
            if self._active_source and self._active_source.startswith("led"):
                self.logger.info("Step 1: Disabling LED")
                self.laser_led_service.disable_led_preview()

            # Step 2: Enable laser preview FIRST (prerequisite for power setting)
            # Server requires laser to be enabled before it responds to power commands
            self.logger.info(f"Step 2: Enabling laser {laser_index} preview mode")
            if not self.laser_led_service.enable_laser_preview(laser_index):
                raise RuntimeError(f"Failed to enable laser {laser_index} preview")

            # Step 3: Set laser power (must come AFTER enable_laser_preview)
            power = self._laser_powers.get(laser_index, 5.0)
            self.logger.info(f"Step 3: Setting laser {laser_index} power to {power:.1f}%")
            if not self.laser_led_service.set_laser_power(laser_index, power):
                raise RuntimeError(f"Failed to set laser {laser_index} power")

            # Step 4: Enable illumination on selected path (CRITICAL - coordinates exposure timing)
            left_enabled = (path == "left")
            right_enabled = (path == "right")
            self.logger.info(f"Step 4: Enabling {path_display} illumination path for synchronized imaging")
            if not self.laser_led_service.enable_illumination(left=left_enabled, right=right_enabled):
                raise RuntimeError(f"Failed to enable {path_display} illumination")

            # Update state
            self._active_source = f"laser_{laser_index}"
            self._active_laser_index = laser_index

            laser_name = f"Laser {laser_index}"
            for laser in self.get_available_lasers():
                if laser.index == laser_index:
                    laser_name = laser.name
                    break

            self.preview_enabled.emit(f"{laser_name} ({path_display} path)")
            self.logger.info(f"Laser {laser_index} enabled on {path_display} path - ready for imaging")
            return True

        except Exception as e:
            error_msg = f"Error enabling laser {laser_index} for preview: {e}"
            self.logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            return False

    def enable_led_for_preview(self, led_color: Optional[int] = None) -> bool:
        """
        Enable LED for preview/imaging.

        Command sequence for LED:
        1. Disable all lasers
        2. Set LED intensity for selected color
        3. Enable LED preview mode
        4. Enable illumination (for synchronized imaging)

        Args:
            led_color: LED color (0=Red, 1=Green, 2=Blue, 3=White). If None, uses current selection.

        Returns:
            True if successful
        """
        try:
            # Use specified color or current selection
            if led_color is not None:
                if not (0 <= led_color <= 3):
                    raise ValueError(f"Invalid LED color: {led_color} (must be 0-3)")
                self._led_color = led_color

            color_names = ["Red", "Green", "Blue", "White"]
            color_name = color_names[self._led_color]

            self.logger.info(f"Enabling {color_name} LED for preview (full sequence)")

            # Step 1: Disable all lasers (non-fatal if fails)
            self.logger.info("Step 1: Disabling all lasers")
            try:
                self.laser_led_service.disable_all_lasers()
            except Exception as e:
                # Non-fatal: laser disable may timeout if no lasers attached
                self.logger.debug(f"Laser disable had error (non-fatal): {e}")

            # Step 2: Set intensity for selected color
            intensity = self._led_intensities.get(self._led_color, 50.0)
            self.logger.info(f"Step 2: Setting {color_name} LED intensity to {intensity:.1f}%")
            if not self.laser_led_service.set_led_intensity(self._led_color, intensity):
                raise RuntimeError(f"Failed to set {color_name} LED intensity")

            # Step 3: Enable LED preview
            self.logger.info("Step 3: Enabling LED preview mode")
            if not self.laser_led_service.enable_led_preview():
                raise RuntimeError("Failed to enable LED preview")

            # Step 4: Enable illumination (for synchronized imaging)
            self.logger.info("Step 4: Enabling illumination for synchronized imaging")
            if not self.laser_led_service.enable_illumination():
                raise RuntimeError("Failed to enable illumination")

            # Update state
            self._active_source = f"led_{color_name[0]}"  # "led_R", "led_G", "led_B", or "led_W"
            self._active_laser_index = None

            self.preview_enabled.emit(f"{color_name} LED")
            self.logger.info(f"{color_name} LED enabled for preview - ready for imaging")
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
