# ============================================================================
# src/py2flamingo/services/initialization_service.py
"""
MVC-compliant microscope initialization service.

This service handles the initial setup and configuration of the microscope system,
replacing the initial_setup() function from microscope_interactions.py.
"""

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional

from py2flamingo.core.events import EventManager
from py2flamingo.core.queue_manager import QueueManager
from py2flamingo.utils.file_handlers import text_to_dict


@dataclass
class InitializationData:
    """
    Container for microscope initialization data.

    This dataclass holds all the values returned from the initialization process,
    making it easier to pass around and access initialization parameters.

    Attributes:
        command_codes: Dictionary of command codes loaded from command_list.txt.
            Includes both named keys (e.g., 'COMMAND_CODES_CAMERA_IMAGE_SIZE_GET')
            and a 'command_labels' key containing a list for backward compatibility.
        stage_limits: Dictionary of stage boundary limits (including ymax)
        fov_parameters: Dictionary of field-of-view parameters (y_move, frame_size, FOV)
        pixel_size_mm: Image pixel size in mm (not camera pixel size)
    """
    command_codes: Dict[str, Any]  # Changed from Dict[str, int] to include the list
    stage_limits: Dict[str, float]
    fov_parameters: Dict[str, Any]
    pixel_size_mm: float


class MicroscopeInitializationService:
    """
    Service for microscope initialization and setup.

    This service handles the initial setup process that was previously done by
    initial_setup() in microscope_interactions.py. It:
    - Clears events and queues
    - Loads command codes
    - Gets microscope settings and pixel size
    - Calculates field of view and movement parameters
    - Gets stage limits

    Attributes:
        connection_service: ConnectionService for microscope communication
        event_manager: EventManager for synchronization
        queue_manager: QueueManager for data flow
        logger: Logger instance
    """

    def __init__(
        self,
        connection_service: 'ConnectionService',
        event_manager: EventManager,
        queue_manager: QueueManager
    ):
        """
        Initialize the microscope initialization service.

        Args:
            connection_service: ConnectionService instance for microscope communication
            event_manager: EventManager instance for event synchronization
            queue_manager: QueueManager instance for data flow
        """
        self.connection_service = connection_service
        self.event_manager = event_manager
        self.queue_manager = queue_manager
        self.logger = logging.getLogger(__name__)

    def initial_setup(self) -> InitializationData:
        """
        Perform initial microscope setup and return initialization data.

        This method replicates the functionality of the original initial_setup()
        function (lines 14-77 of microscope_interactions.py). It:
        1. Clears all events and queues
        2. Loads command codes from command_list.txt
        3. Gets microscope settings and pixel size
        4. Gets frame size from microscope
        5. Calculates field of view and y_move parameters
        6. Gets stage limits (ymax)

        Returns:
            InitializationData: Container with all initialization parameters

        Raises:
            FileNotFoundError: If command_list.txt or settings files not found
            RuntimeError: If microscope communication fails
            ValueError: If received data is invalid

        Example:
            >>> service = MicroscopeInitializationService(conn, events, queues)
            >>> init_data = service.initial_setup()
            >>> print(f"FOV: {init_data.fov_parameters['FOV']}mm")
            >>> print(f"Y max: {init_data.stage_limits['ymax']}mm")
        """
        self.logger.info("Starting microscope initialization...")

        # Step 1: Clear all events and queues (replaces clear_all_events_queues())
        self._clear_events_and_queues()

        # Step 2: Load command codes
        command_codes = self.load_command_codes()

        # Step 3: Get microscope settings and pixel size
        pixel_size_mm, scope_settings = self.connection_service.get_microscope_settings()

        # Step 4: Get frame size and calculate FOV parameters
        fov_parameters = self.calculate_fov_parameters(pixel_size_mm, command_codes)

        # Step 5: Get stage limits
        stage_limits = self.get_stage_limits(scope_settings)

        # Log the initialization results
        self.logger.info(
            f"Initialization complete: "
            f"pixel_size={pixel_size_mm:.6f}mm, "
            f"frame_size={fov_parameters['frame_size']}px, "
            f"FOV={fov_parameters['FOV']:.4f}mm, "
            f"y_move={fov_parameters['y_move']:.4f}mm, "
            f"ymax={stage_limits['ymax']:.4f}mm"
        )

        # Return all initialization data
        return InitializationData(
            command_codes=command_codes,
            stage_limits=stage_limits,
            fov_parameters=fov_parameters,
            pixel_size_mm=pixel_size_mm
        )

    def load_command_codes(self) -> Dict[str, int]:
        """
        Load command codes from command_list.txt.

        This method reads the command_list.txt file and extracts the command
        codes that are used throughout the application. The file is located
        relative to the functions directory.

        Returns:
            Dictionary mapping command code names to their integer values.
            Keys include:
            - 'COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD'
            - 'COMMAND_CODES_CAMERA_WORK_FLOW_START'
            - 'COMMAND_CODES_STAGE_POSITION_SET'
            - 'COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET'
            - 'COMMAND_CODES_CAMERA_IMAGE_SIZE_GET'
            - 'COMMAND_CODES_CAMERA_CHECK_STACK'

        Raises:
            FileNotFoundError: If command_list.txt is not found
            KeyError: If expected command codes are not in the file

        Example:
            >>> codes = service.load_command_codes()
            >>> print(codes['COMMAND_CODES_CAMERA_IMAGE_SIZE_GET'])
            12331
        """
        try:
            # Get path to command_list.txt (in functions directory)
            command_file = Path(__file__).parent.parent / "functions" / "command_list.txt"

            if not command_file.exists():
                raise FileNotFoundError(
                    f"Command list file not found: {command_file}"
                )

            # Parse the command file
            commands = text_to_dict(command_file)

            # Extract the specific command codes we need
            command_section = commands.get("CommandCodes.h", {})

            # Extract individual command codes (matching original code structure)
            COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD = int(
                command_section["COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD"]
            )
            COMMAND_CODES_CAMERA_WORK_FLOW_START = int(
                command_section["COMMAND_CODES_CAMERA_WORK_FLOW_START"]
            )
            COMMAND_CODES_STAGE_POSITION_SET = int(
                command_section["COMMAND_CODES_STAGE_POSITION_SET"]
            )
            COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET = int(
                command_section["COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET"]
            )
            COMMAND_CODES_CAMERA_IMAGE_SIZE_GET = int(
                command_section["COMMAND_CODES_CAMERA_IMAGE_SIZE_GET"]
            )
            COMMAND_CODES_CAMERA_CHECK_STACK = int(
                command_section["COMMAND_CODES_CAMERA_CHECK_STACK"]
            )

            # Create both dictionary (for named access) and list (for compatibility)
            command_codes = {
                'COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD': COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD,
                'COMMAND_CODES_CAMERA_WORK_FLOW_START': COMMAND_CODES_CAMERA_WORK_FLOW_START,
                'COMMAND_CODES_STAGE_POSITION_SET': COMMAND_CODES_STAGE_POSITION_SET,
                'COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET': COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET,
                'COMMAND_CODES_CAMERA_IMAGE_SIZE_GET': COMMAND_CODES_CAMERA_IMAGE_SIZE_GET,
                'COMMAND_CODES_CAMERA_CHECK_STACK': COMMAND_CODES_CAMERA_CHECK_STACK,
                # Also include the list version for backward compatibility (unpacking)
                'command_labels': [
                    COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD,
                    COMMAND_CODES_CAMERA_WORK_FLOW_START,
                    COMMAND_CODES_STAGE_POSITION_SET,
                    COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET,
                    COMMAND_CODES_CAMERA_IMAGE_SIZE_GET,
                    COMMAND_CODES_CAMERA_CHECK_STACK,
                ],
            }

            self.logger.debug(f"Loaded {len(command_codes) - 1} command codes")  # -1 for the list
            return command_codes

        except KeyError as e:
            self.logger.error(f"Missing expected command code: {e}")
            raise KeyError(f"Command code not found in command_list.txt: {e}")
        except Exception as e:
            self.logger.error(f"Failed to load command codes: {e}")
            raise

    def calculate_fov_parameters(
        self,
        pixel_size_mm: float,
        command_codes: Dict[str, int]
    ) -> Dict[str, Any]:
        """
        Calculate field of view and movement parameters.

        This method:
        1. Sends CAMERA_IMAGE_SIZE_GET command to microscope
        2. Retrieves the frame size from the response queue
        3. Calculates the field of view (FOV) = pixel_size * frame_size
        4. Calculates y_move step size (currently equal to FOV)

        Args:
            pixel_size_mm: Image pixel size in mm
            command_codes: Dictionary of command codes

        Returns:
            Dictionary containing:
            - 'frame_size': Number of pixels per frame (assumes square)
            - 'FOV': Field of view in mm
            - 'y_move': Y-axis movement step size in mm

        Raises:
            RuntimeError: If microscope communication fails
            ValueError: If frame_size is invalid

        Example:
            >>> fov = service.calculate_fov_parameters(0.0016, codes)
            >>> print(f"Frame: {fov['frame_size']}px, FOV: {fov['FOV']}mm")
        """
        try:
            # Send command to get image size
            COMMAND_CODES_CAMERA_IMAGE_SIZE_GET = command_codes[
                'COMMAND_CODES_CAMERA_IMAGE_SIZE_GET'
            ]

            self.queue_manager.put_nowait('command', COMMAND_CODES_CAMERA_IMAGE_SIZE_GET)
            self.event_manager.set_event('send')

            # Wait for response (same timing as original code)
            time.sleep(0.1)

            # Get frame size from queue
            frame_size = self.queue_manager.get_nowait('other_data')

            if not frame_size or not isinstance(frame_size, (int, float)):
                raise ValueError(
                    f"Invalid frame_size received: {frame_size}"
                )

            frame_size = int(frame_size)

            # Calculate field of view
            FOV = pixel_size_mm * frame_size

            # Calculate y_move (same as FOV in original code)
            # Note: Original had comment about multiplying by 1.3 for large samples
            y_move = FOV

            self.logger.info(f"y_move search step size is currently {y_move}mm")

            return {
                'frame_size': frame_size,
                'FOV': FOV,
                'y_move': y_move,
            }

        except Exception as e:
            self.logger.error(f"Failed to calculate FOV parameters: {e}")
            raise RuntimeError(
                f"Could not calculate FOV parameters: {e}"
            )

    def get_stage_limits(self, scope_settings: Dict[str, Any]) -> Dict[str, float]:
        """
        Get stage boundary limits from microscope settings.

        This method extracts the stage limits from the scope settings dictionary,
        particularly the ymax value which is the soft limit max y-axis.

        Args:
            scope_settings: Microscope settings dictionary from get_microscope_settings()

        Returns:
            Dictionary containing stage limits, including:
            - 'ymax': Maximum Y-axis position in mm

        Raises:
            KeyError: If expected settings are not found
            ValueError: If ymax value is invalid

        Example:
            >>> limits = service.get_stage_limits(settings)
            >>> print(f"Y max: {limits['ymax']}mm")
        """
        try:
            # Extract ymax from settings
            ymax = float(
                scope_settings["Stage limits"]["Soft limit max y-axis"]
            )

            self.logger.info(f"ymax is {ymax}mm")

            return {
                'ymax': ymax,
            }

        except KeyError as e:
            self.logger.error(f"Missing stage limit setting: {e}")
            raise KeyError(
                f"Stage limit not found in settings: {e}. "
                f"Available sections: {list(scope_settings.keys())}"
            )
        except (ValueError, TypeError) as e:
            self.logger.error(f"Invalid ymax value: {e}")
            raise ValueError(f"Invalid ymax value in settings: {e}")

    def _clear_events_and_queues(self) -> None:
        """
        Clear all events and queues to ensure clean state.

        This method replaces the clear_all_events_queues() call from the
        original code. It clears all events and queues to ensure a clean
        state before initialization.
        """
        self.logger.debug("Clearing all events and queues")

        # Clear all events
        self.event_manager.clear_all()

        # Clear all queues
        self.queue_manager.clear_all()

        self.logger.debug("Events and queues cleared")
