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
    # For TSPIM systems, controls left and right light paths
    ILLUMINATION_LEFT_ENABLE = 0x7004  # 28676 - Enable left illumination path
    ILLUMINATION_LEFT_DISABLE = 0x7005  # 28677 - Disable left illumination path
    ILLUMINATION_RIGHT_ENABLE = 0x7006  # 28678 - Enable right illumination path
    ILLUMINATION_RIGHT_DISABLE = 0x7007  # 28679 - Disable right illumination path


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

    def query_laser_power(self, laser_index: int) -> float:
        """
        Query actual laser power level from hardware.

        The laser hardware (Coherent OBIS) quantizes power based on DAC resolution,
        so the actual power may differ slightly from the requested power.
        This method reads back the actual power the laser is set to.

        Args:
            laser_index: Laser index (1-4)

        Returns:
            Actual laser power as percentage (0.0 - 100.0), or -1.0 on error

        Example:
            >>> laser_led.set_laser_power(3, 10.0)
            >>> actual = laser_led.query_laser_power(3)  # Might return 10.7%
        """
        if not (1 <= laser_index <= len(self._lasers)):
            self.logger.error(f"Invalid laser index: {laser_index}")
            return -1.0

        self.logger.debug(f"Querying laser {laser_index} actual power")

        # Send LASER_LEVEL_GET query command
        result = self._query_command(
            LaserLEDCommandCode.LASER_LEVEL_GET,
            f"LASER_{laser_index}_LEVEL_GET",
            params=[laser_index, 0, 0, 0, 0, 0, 0]  # params[6] will be set to TRIGGER_CALL_BACK
        )

        if not result['success']:
            self.logger.error(f"Failed to query laser {laser_index} power: {result.get('error', 'unknown')}")
            return -1.0

        # Parse response - power is returned as string in buffer field
        parsed = result.get('parsed', {})
        buffer_data = parsed.get('additional_data', b'')

        if buffer_data:
            # Power is in additional data as string
            power_str = buffer_data.decode('utf-8', errors='ignore').strip().rstrip('\x00')
        else:
            # Try parsing from main buffer field (72 bytes in response)
            # Note: The response structure might have power in the data field
            self.logger.warning(f"No additional data in LASER_LEVEL_GET response for laser {laser_index}")
            return -1.0

        try:
            actual_power = float(power_str)
            self.logger.debug(f"Laser {laser_index} actual power: {actual_power:.2f}%")
            return actual_power
        except (ValueError, AttributeError) as e:
            self.logger.error(f"Failed to parse laser power from response: {power_str}, error: {e}")
            return -1.0

    def set_laser_power(self, laser_index: int, power_percent: float, verify: bool = True) -> tuple[bool, float]:
        """
        Set laser power level as percentage and optionally verify actual power.

        The laser hardware (Coherent OBIS) quantizes power based on DAC resolution,
        so the actual power may differ slightly from requested power (e.g., 10.0% → 10.7%).
        This method can automatically query the actual power after setting.

        Args:
            laser_index: Laser index (1-4)
            power_percent: Power as percentage (0.0 - 100.0)
            verify: If True, query actual power after setting (default: True)

        Returns:
            Tuple of (success: bool, actual_power: float)
            - success: True if set command succeeded
            - actual_power: Actual power from hardware (if verify=True), otherwise requested power

        Example:
            >>> success, actual = laser_led.set_laser_power(3, 10.0)
            >>> print(f"Requested 10.0%, actual: {actual:.2f}%")  # Might print "actual: 10.7%"
        """
        if not (1 <= laser_index <= len(self._lasers)):
            self.logger.error(f"Invalid laser index: {laser_index}")
            return False, power_percent

        if not (0.0 <= power_percent <= 100.0):
            self.logger.error(f"Power must be 0-100%, got {power_percent}")
            return False, power_percent

        self.logger.info(f"Setting laser {laser_index} power to {power_percent:.1f}%")

        # CRITICAL: Server expects percentage as STRING in buffer field
        # NOT as DAC value in params!
        # From logs: buffer = "5.00", "11.49", etc.
        power_str = f"{power_percent:.2f}"

        self.logger.info(f"DEBUG: Sending LASER_LEVEL_SET with int32Data0={laser_index}, buffer='{power_str}'")

        # Send command and wait for response
        result = self._send_command(
            LaserLEDCommandCode.LASER_LEVEL_SET,
            f"LASER_{laser_index}_LEVEL_SET",
            params=[laser_index, 0, 0, 0, 0, 0, 0],  # params[6] will be set to CALLBACK_FLAG by _send_command
            data=power_str
        )

        if not result['success']:
            self.logger.error(f"DEBUG: Laser {laser_index} power set FAILED: {result.get('error', 'unknown error')}")
            return False, power_percent

        self.logger.info(f"DEBUG: Laser {laser_index} power set to {power_str}% - SUCCESS")

        # Query actual power from hardware (mimics C++ GUI behavior)
        if verify:
            actual_power = self.query_laser_power(laser_index)
            if actual_power >= 0:
                if abs(actual_power - power_percent) > 0.5:
                    self.logger.info(f"Laser {laser_index}: Requested {power_percent:.2f}%, "
                                   f"actual {actual_power:.2f}% (hardware quantization)")
                return True, actual_power
            else:
                # Query failed, return requested power
                self.logger.warning(f"Failed to verify laser {laser_index} power, using requested value")
                return True, power_percent
        else:
            return True, power_percent

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
        self.logger.info(f"DEBUG: Sending LASER_ENABLE_PREVIEW command with int32Data0={laser_index}")

        result = self._send_command(
            LaserLEDCommandCode.LASER_ENABLE_PREVIEW,
            f"LASER_{laser_index}_ENABLE_PREVIEW",
            params=[laser_index, 0, 0, 0, 0, 0, 0]
        )

        self.logger.info(f"DEBUG: LASER_ENABLE_PREVIEW result: success={result.get('success')}, error={result.get('error', 'none')}")

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

        # LED RANGE FIX: Map UI 0-100% to second half of 16-bit range (positive percentages)
        # LED uses signed percentage system: -100% to +100% mapped to unsigned 16-bit
        # From C++ GUI logs: 0% → 32000, 50% → 48767, 100% → 65534
        # First half (0-31999): -100% to ~0% (negative percentages, LED off/dark)
        # Second half (32000-65534): 0% to +100% (positive percentages, LED brightness)
        # UI  0% → 32000 (server 0%, LED off)
        # UI 50% → 48767 (server +50%, half brightness)
        # UI 100% → 65534 (server +100%, full brightness)
        LED_MIN = 32000
        LED_MAX = 65534
        led_value = LED_MIN + int((LED_MAX - LED_MIN) * (intensity_percent / 100.0))

        self.logger.debug(f"LED value mapping: UI {intensity_percent:.1f}% → LED value {led_value} (server +{intensity_percent:.1f}%)")

        # LED_SET command (0x4001):
        # int32Data0 = led_color (0=Red, 1=Green, 2=Blue, 3=White)
        # int32Data1 = led_value (32000-65534 range, positive half of signed percentage)
        result = self._send_command(
            LaserLEDCommandCode.LED_SET,
            "LED_SET",
            params=[0, 0, 0, led_color, led_value, 0, 0]
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

    def enable_illumination(self, left: bool = True, right: bool = False) -> bool:
        """
        Enable illumination waveform for synchronized imaging.

        For TSPIM systems, this controls which light path(s) are active.
        This must be called after enabling laser/LED preview to configure
        the illumination timing that coordinates with camera exposure.

        Args:
            left: Enable left illumination path (default: True)
            right: Enable right illumination path (default: False)

        Returns:
            True if successful, False otherwise

        Example:
            >>> # Proper sequence for laser live view on left path:
            >>> laser_led.disable_led_preview()  # 1. Disable LED
            >>> laser_led.enable_laser_preview(2)  # 2. Enable laser 2
            >>> laser_led.enable_illumination(left=True)  # 3. Enable left path
            >>> camera.start_live_view()  # 4. Start imaging
        """
        success = True

        if left:
            self.logger.info("Enabling LEFT illumination path for synchronized imaging")
            result = self._send_command(
                LaserLEDCommandCode.ILLUMINATION_LEFT_ENABLE,
                "ILLUMINATION_LEFT_ENABLE",
                params=[0, 0, 0, 0, 0, 0, 0]
            )
            success = success and result['success']

        if right:
            self.logger.info("Enabling RIGHT illumination path for synchronized imaging")
            result = self._send_command(
                LaserLEDCommandCode.ILLUMINATION_RIGHT_ENABLE,
                "ILLUMINATION_RIGHT_ENABLE",
                params=[0, 0, 0, 0, 0, 0, 0]
            )
            success = success and result['success']

        if not left and not right:
            self.logger.warning("enable_illumination called with both paths disabled")

        return success

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
