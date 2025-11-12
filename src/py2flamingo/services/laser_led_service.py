"""
Laser and LED subsystem service for Flamingo microscope.

Handles laser and LED control including power setting, preview mode,
and switching between light sources for imaging.
"""

from typing import Dict, List
from dataclasses import dataclass

from py2flamingo.services.microscope_command_service import MicroscopeCommandService


@dataclass
class LaserInfo:
    """Information about a laser line."""
    index: int  # 1-based laser index (1, 2, 3, 4)
    wavelength: int  # Wavelength in nm (405, 488, 561, 640)
    max_power: float  # Maximum power in mW
    attached: bool  # Whether laser is physically attached

    @property
    def name(self) -> str:
        """Get display name for laser."""
        return f"Laser {self.index} {self.wavelength} nm"


class LaserLEDCommandCode:
    """Laser and LED command codes from CommandCodes.h."""

    # Laser commands (0x2000 range)
    LASER_LEVEL_SET = 0x2001  # 8193 - Set laser power level
    LASER_LEVEL_GET = 0x2002  # 8194 - Get laser power level
    LASER_ENABLE = 0x2003  # 8195 - Enable laser (emission on)
    LASER_ENABLE_PREVIEW = 0x2004  # 8196 - Enable laser preview mode (external trigger)
    LASER_ENABLE_LINE = 0x2005  # 8197 - Enable laser line
    LASER_DISABLE = 0x2006  # 8198 - Disable laser
    LASER_DISABLE_ALL = 0x2007  # 8199 - Disable all lasers

    # LED commands (0x4000 range)
    LED_SET = 0x4001  # 16385 - Set LED intensity
    LED_PREVIEW_ENABLE = 0x4002  # 16386 - Enable LED preview
    LED_PREVIEW_DISABLE = 0x4003  # 16387 - Disable LED preview

    # Illumination commands (0x7000 range)
    # These control the illumination waveform for synchronized imaging
    ILLUMINATION_LEFT_ENABLE = 0x7004  # 28676 - Enable left illumination
    ILLUMINATION_LEFT_DISABLE = 0x7005  # 28677 - Disable left illumination


class LaserLEDService(MicroscopeCommandService):
    """
    Service for laser and LED operations on Flamingo microscope.

    This service manages light sources required for camera operation.
    Before starting live view or taking snapshots, a laser or LED must
    be enabled in preview mode.

    Example:
        >>> laser_led = LaserLEDService(connection, config_service)
        >>> # Enable laser 3 (488nm) for preview
        >>> laser_led.set_laser_power(3, 5.0)  # 5% power
        >>> laser_led.enable_laser_preview(3)
        >>> # Now camera can start live view
        >>> camera.start_live_view()
        >>> # Disable when done
        >>> laser_led.disable_all_lasers()
    """

    def __init__(self, connection, config_service):
        """
        Initialize laser/LED service.

        Args:
            connection: MVCConnectionService instance
            config_service: ConfigurationService for reading laser parameters
        """
        super().__init__(connection)
        self._config = config_service
        self._lasers: List[LaserInfo] = []
        self._led_available = False
        self._load_laser_configuration()

    def _load_laser_configuration(self) -> None:
        """Load laser configuration from ControlSettings."""
        try:
            # Parse ControlSettings.txt for laser parameters
            from py2flamingo.utils.file_handlers import text_to_dict
            from pathlib import Path

            base_path = self._config.base_path
            control_settings_path = base_path / 'microscope_settings' / 'ControlSettings.txt'

            if not control_settings_path.exists():
                self.logger.warning(f"ControlSettings.txt not found at {control_settings_path}")
                return

            settings = text_to_dict(str(control_settings_path))

            # Extract laser parameters
            laser_params = settings.get('Laser parameters', {})
            attached_devices = settings.get('Attached devices', {})

            # Get number of laser lines
            num_lasers = int(laser_params.get('Laser lines', 4))

            # Load each laser
            for i in range(1, num_lasers + 1):
                wavelength = int(laser_params.get(f'Laser {i} wave length (nm)', 0))
                max_power = float(laser_params.get(f'Laser {i} max power (mw)', 0))
                attached = int(attached_devices.get(f'Attached laser line {i}', 0)) == 1

                if wavelength > 0:  # Valid laser
                    laser = LaserInfo(
                        index=i,
                        wavelength=wavelength,
                        max_power=max_power,
                        attached=attached
                    )
                    self._lasers.append(laser)
                    self.logger.info(f"Loaded {laser.name}, max power: {max_power} mW, attached: {attached}")

            # Check if LED is attached
            self._led_available = int(attached_devices.get('Attached LED', 0)) == 1
            self.logger.info(f"LED available: {self._led_available}")

        except Exception as e:
            self.logger.error(f"Failed to load laser configuration: {e}")

    def get_available_lasers(self) -> List[LaserInfo]:
        """
        Get list of available lasers.

        Returns:
            List of LaserInfo objects for attached lasers
        """
        return [laser for laser in self._lasers if laser.attached]

    def is_led_available(self) -> bool:
        """Check if LED is available."""
        return self._led_available

    def set_laser_power(self, laser_index: int, power_percent: float) -> bool:
        """
        Set laser power level as percentage.

        Args:
            laser_index: Laser index (1-4)
            power_percent: Power as percentage (0.0 - 100.0)

        Returns:
            True if successful, False otherwise

        Example:
            >>> laser_led.set_laser_power(3, 5.0)  # Set laser 3 to 5% power
        """
        if not (1 <= laser_index <= len(self._lasers)):
            self.logger.error(f"Invalid laser index: {laser_index}")
            return False

        if not (0.0 <= power_percent <= 100.0):
            self.logger.error(f"Power must be 0-100%, got {power_percent}")
            return False

        self.logger.info(f"Setting laser {laser_index} power to {power_percent:.1f}%")

        # Convert percentage to DAC value (0-65535)
        dac_value = int(655.35 * power_percent)

        result = self._send_command(
            LaserLEDCommandCode.LASER_LEVEL_SET,
            f"LASER_{laser_index}_LEVEL_SET",
            params=[0, 0, 0, laser_index, dac_value, 0, 0]
        )

        return result['success']

    def enable_laser_preview(self, laser_index: int) -> bool:
        """
        Enable laser in preview mode (external trigger).

        This disables all other lasers and enables the specified laser
        with external triggering for live view/snapshot operation.

        Args:
            laser_index: Laser index (1-4)

        Returns:
            True if successful, False otherwise

        Example:
            >>> laser_led.enable_laser_preview(3)  # Enable laser 3 for preview
        """
        if not (1 <= laser_index <= len(self._lasers)):
            self.logger.error(f"Invalid laser index: {laser_index}")
            return False

        self.logger.info(f"Enabling laser {laser_index} preview mode")

        result = self._send_command(
            LaserLEDCommandCode.LASER_ENABLE_PREVIEW,
            f"LASER_{laser_index}_ENABLE_PREVIEW",
            params=[0, 0, 0, laser_index, 0, 0, 0]
        )

        return result['success']

    def disable_all_lasers(self) -> bool:
        """
        Disable all laser lines.

        Returns:
            True if successful, False otherwise
        """
        self.logger.info("Disabling all lasers")

        result = self._send_command(
            LaserLEDCommandCode.LASER_DISABLE_ALL,
            "LASER_DISABLE_ALL",
            params=[0, 0, 0, 0, 0, 0, 0]
        )

        return result['success']

    def set_led_intensity(self, led_color: int, intensity_percent: float) -> bool:
        """
        Set LED intensity as percentage for specified color.

        Args:
            led_color: LED color (0=Red, 1=Green, 2=Blue, 3=White)
            intensity_percent: Intensity as percentage (0.0 - 100.0)

        Returns:
            True if successful, False otherwise

        Example:
            >>> laser_led.set_led_intensity(1, 50.0)  # Set Green LED to 50% intensity
        """
        if not self._led_available:
            self.logger.error("LED not available on this microscope")
            return False

        if not (0 <= led_color <= 3):
            self.logger.error(f"LED color must be 0-3 (Red/Green/Blue/White), got {led_color}")
            return False

        if not (0.0 <= intensity_percent <= 100.0):
            self.logger.error(f"Intensity must be 0-100%, got {intensity_percent}")
            return False

        color_names = ["Red", "Green", "Blue", "White"]
        self.logger.debug(f"Setting {color_names[led_color]} LED intensity to {intensity_percent:.1f}%")

        # Convert percentage to DAC value range
        # Based on ScopeSettings.txt: min=3200000, max=6553500
        min_dac = 3200000
        max_dac = 6553500
        dac_value = int(min_dac + (max_dac - min_dac) * (intensity_percent / 100.0))

        # LED_SET command:
        # int32Data0 = led_color (0=Red, 1=Green, 2=Blue, 3=White)
        # int32Data1 = dac_value (intensity)
        result = self._send_command(
            LaserLEDCommandCode.LED_SET,
            "LED_SET",
            params=[0, 0, 0, led_color, dac_value, 0, 0]
        )

        return result['success']

    def enable_led_preview(self) -> bool:
        """
        Enable LED in preview mode.

        Returns:
            True if successful, False otherwise
        """
        if not self._led_available:
            self.logger.error("LED not available on this microscope")
            return False

        self.logger.info("Enabling LED preview mode")

        result = self._send_command(
            LaserLEDCommandCode.LED_PREVIEW_ENABLE,
            "LED_PREVIEW_ENABLE",
            params=[0, 0, 0, 0, 0, 0, 0]
        )

        return result['success']

    def disable_led_preview(self) -> bool:
        """
        Disable LED preview mode.

        Returns:
            True if successful, False otherwise
        """
        if not self._led_available:
            self.logger.error("LED not available on this microscope")
            return False

        self.logger.info("Disabling LED preview mode")

        result = self._send_command(
            LaserLEDCommandCode.LED_PREVIEW_DISABLE,
            "LED_PREVIEW_DISABLE",
            params=[0, 0, 0, 0, 0, 0, 0]
        )

        return result['success']

    def enable_illumination(self) -> bool:
        """
        Enable illumination waveform for synchronized imaging.

        This must be called after enabling laser preview to configure
        the illumination timing that coordinates with camera exposure.

        From analysis of working system, this command:
        - Sets illumination waveform (e.g., ILWAVE 200 1500 290 -1 -1)
        - Coordinates camera exposure timing with light source

        Returns:
            True if successful, False otherwise

        Example:
            >>> # Proper sequence for laser live view:
            >>> laser_led.disable_led_preview()  # 1. Disable LED
            >>> laser_led.enable_laser_preview(2)  # 2. Enable laser 2
            >>> laser_led.enable_illumination()  # 3. Enable illumination (CRITICAL!)
            >>> camera.start_live_view()  # 4. Start imaging
        """
        self.logger.info("Enabling illumination for synchronized imaging")

        result = self._send_command(
            LaserLEDCommandCode.ILLUMINATION_LEFT_ENABLE,
            "ILLUMINATION_LEFT_ENABLE",
            params=[0, 0, 0, 0, 0, 0, 0]
        )

        return result['success']

    def disable_illumination(self) -> bool:
        """
        Disable illumination waveform.

        Returns:
            True if successful, False otherwise
        """
        self.logger.info("Disabling illumination")

        result = self._send_command(
            LaserLEDCommandCode.ILLUMINATION_LEFT_DISABLE,
            "ILLUMINATION_LEFT_DISABLE",
            params=[0, 0, 0, 0, 0, 0, 0]
        )

        return result['success']
