"""
Command Codes for Flamingo Microscope TCP Protocol.

This module defines all command codes used to communicate with the Flamingo
microscope control system. Commands are organized by subsystem for clarity.

Command codes are based on the server-side CommandCodes.h implementation and
verified against actual log files from the microscope system.

Usage Example:
    >>> from command_codes import SystemCommands, StageCommands, LaserCommands
    >>> from protocol_encoder import ProtocolEncoder
    >>>
    >>> encoder = ProtocolEncoder()
    >>>
    >>> # Get system state
    >>> cmd = encoder.encode_command(
    ...     code=SystemCommands.STATE_GET,
    ...     params=[0, 0, 0, 0, 0, 0, encoder.CALLBACK_FLAG]
    ... )
    >>>
    >>> # Move stage to position
    >>> cmd = encoder.encode_command(
    ...     code=StageCommands.POSITION_SET,
    ...     params=[1, 0, 0, 0, 0, 0, encoder.CALLBACK_FLAG],  # axis 1 (X)
    ...     value=7.635  # position in mm
    ... )
"""


class SystemCommands:
    """
    System-level commands for microscope state and configuration.

    These commands control overall system state, initialization, and
    high-level microscope operations.
    """

    # System state commands
    STATE_GET = 0xa007  # 40967 - Get current system state
    STATE_IDLE = 0xa002  # 40962 - Set system to IDLE state

    # System state values (returned in int32Data0)
    STATE_VALUE_IDLE = 0  # System is idle, ready for commands
    STATE_VALUE_BUSY = 1  # System is busy processing
    STATE_VALUE_ERROR = 2  # System has encountered an error

    # Configuration commands
    SCOPE_SETTINGS_LOAD = 0x1009  # 4105 - Load microscope settings


class StageCommands:
    """
    Stage motion and positioning commands.

    The Flamingo uses a 3-axis stage (X, Y, Z) controlled via these commands.
    Positions are in millimeters (mm) and velocities are in mm/s.

    Axis numbering:
        1 = X axis (horizontal)
        2 = Y axis (horizontal)
        3 = Z axis (vertical/focus)
    """

    # Position commands
    POSITION_SET = 0x6005  # 24581 - Set stage position (slide control)
    POSITION_GET = 0x6008  # 24584 - Get current stage position

    # Motion control commands
    HOME = 0x6001  # 24577 - Home stage axes
    HALT = 0x6002  # 24578 - Emergency stop stage motion
    VELOCITY_SET = 0x6006  # 24582 - Set stage velocity

    # Motion status commands
    WAIT_FOR_MOTION = 0x600f  # 24591 - Wait for motion to complete
    MOTION_STOPPED = 0x6010  # 24592 - Motion stopped callback

    # Save location commands
    SAVE_LOCATIONS_GET = 0x6009  # 24585 - Get saved stage positions
    SAVE_LOCATIONS_SET = 0x600a  # 24586 - Save current positions


class LaserCommands:
    """
    Laser control commands.

    The Flamingo supports up to 4 laser lines for fluorescence imaging.
    Laser power is typically specified as a percentage (0-100).

    Laser indices:
        1 = Laser line 1 (e.g., 405nm)
        2 = Laser line 2 (e.g., 488nm)
        3 = Laser line 3 (e.g., 561nm)
        4 = Laser line 4 (e.g., 640nm)
    """

    # Laser power commands
    LEVEL_SET = 0x2001  # 8193 - Set laser power level (GET also uses this)
    LEVEL_GET = 0x2001  # 8193 - Get laser power level (same as SET)

    # Laser enable/disable commands
    PREVIEW_ENABLE = 0x2004  # 8196 - Enable laser preview mode (external trigger)
    PREVIEW_DISABLE = 0x2005  # 8197 - Disable laser preview mode
    ALL_DISABLE = 0x2007  # 8199 - Disable all laser lines

    # Note: Laser level is returned as string in buffer field (e.g., "11.49")
    # int32Data0 contains the laser index (1-4)


class LEDCommands:
    """
    LED illumination commands.

    The Flamingo has LED illumination for brightfield/transmission imaging.
    """

    # LED control commands
    SET_VALUE = 0x4001  # 16385 - Set LED brightness (0-65535)
    ENABLE = 0x4002  # 16386 - Enable LED
    DISABLE = 0x4003  # 16387 - Disable LED
    SELECTION_CHANGE = 0x4006  # 16390 - Change LED selection

    # int32Data0 = LED index
    # int32Data1 = LED brightness value (0-65535)


class CameraCommands:
    """
    Camera control and image acquisition commands.

    The Flamingo uses PCO cameras (e.g., pco.panda 4.2) for image acquisition.
    """

    # Image acquisition commands
    SNAPSHOT = 0x3006  # 12294 - Take a single image
    LIVE_VIEW_START = 0x3007  # 12295 - Start continuous imaging
    LIVE_VIEW_STOP = 0x3008  # 12296 - Stop continuous imaging

    # Camera parameter commands
    EXPOSURE_SET = 0x3001  # 12289 - Set exposure time
    EXPOSURE_GET = 0x3002  # 12290 - Get exposure time
    IMAGE_SIZE_GET = 0x3027  # 12327 - Get image dimensions
    PIXEL_SIZE_GET = 0x3042  # 12354 - Get pixel size in micrometers
    FIELD_OF_VIEW_GET = 0x3043  # 12355 - Get field of view

    # Workflow commands
    WORKFLOW_START = 0x3004  # 12292 - Start image acquisition workflow
    WORKFLOW_STOP = 0x3005  # 12293 - Stop image acquisition workflow

    # Note: Camera dimensions returned in int32Data0/1/2
    # Pixel size and FOV returned in doubleData field


class IlluminationCommands:
    """
    Illumination control commands.

    Controls the illumination waveform and timing for synchronized imaging.
    """

    # Illumination control
    LEFT_ENABLE = 0x7004  # 28676 - Enable left illumination
    LEFT_DISABLE = 0x7005  # 28677 - Disable left illumination


class FilterCommands:
    """
    Filter wheel commands.

    Controls the motorized filter wheel positions.
    """

    # Filter wheel control
    POSITION_SET = 0x5001  # 20481 - Set filter wheel position
    POSITION_GET = 0x5002  # 20482 - Get current filter wheel position


class CommandDataBits:
    """
    Command data bits flags for the cmdDataBits0 (params[6]) parameter field.

    These are bit flags that can be combined using bitwise OR (|) operations.
    From CommandCodes.h enum COMMAND_DATA_BITS.

    CRITICAL: The TRIGGER_CALL_BACK flag MUST be set for all GET/query commands,
    otherwise the microscope will not send a response and the command will timeout.

    Usage Examples:
        >>> from protocol_encoder import ProtocolEncoder
        >>> from command_codes import StageCommands, CommandDataBits
        >>>
        >>> encoder = ProtocolEncoder()
        >>>
        >>> # Example 1: Query command - MUST have TRIGGER_CALL_BACK
        >>> cmd = encoder.encode_command(
        ...     code=StageCommands.POSITION_GET,
        ...     params=[1, 0, 0, 0, 0, 0, CommandDataBits.TRIGGER_CALL_BACK]
        ... )
        >>>
        >>> # Example 2: Z-stack workflow with max projection saved to disk
        >>> flags = (CommandDataBits.STAGE_ZSWEEP |
        ...          CommandDataBits.MAX_PROJECTION |
        ...          CommandDataBits.SAVE_TO_DISK)
        >>> cmd = encoder.encode_command(
        ...     code=CameraCommands.WORKFLOW_START,
        ...     params=[0, 0, 0, 0, 0, 0, flags]
        ... )
        >>>
        >>> # Example 3: Multi-position timelapse
        >>> flags = (CommandDataBits.STAGE_POSITIONS_IN_BUFFER |
        ...          CommandDataBits.SAVE_TO_DISK |
        ...          CommandDataBits.EXPERIMENT_TIME_REMAINING)
        >>> cmd = encoder.encode_command(
        ...     code=CameraCommands.WORKFLOW_START,
        ...     params=[0, 0, 0, 0, 0, 0, flags]
        ... )
    """

    # === Response Control ===
    # Trigger callback/response from microscope (CRITICAL for query/GET commands)
    # Without this flag, query commands receive no response and timeout
    # USE FOR: All *_GET commands (STAGE_POSITION_GET, CAMERA_IMAGE_SIZE_GET, etc.)
    TRIGGER_CALL_BACK = 0x80000000

    # === Workflow/Experiment Flags ===
    # Request/indicate experiment time remaining information
    # USE FOR: Long-running workflows, timelapse experiments
    EXPERIMENT_TIME_REMAINING = 0x00000001

    # Stage positions are buffered for multi-position acquisition
    # USE FOR: Workflows with multiple XYZ positions (multi-well plates, tiles, etc.)
    STAGE_POSITIONS_IN_BUFFER = 0x00000002

    # Compute Maximum Intensity Projection from Z-stack
    # USE FOR: Z-stack workflows when you want MIP instead of full stack
    # NOTE: Old code used this extensively for sample finding
    MAX_PROJECTION = 0x00000004

    # Save acquired images to disk (vs. only sending to live view)
    # USE FOR: Actual experiments/acquisitions (not live preview)
    SAVE_TO_DISK = 0x00000008

    # Don't send stage position updates to client during movement
    # USE FOR: Rapid multi-position movements to reduce network traffic
    STAGE_NOT_UPDATE_CLIENT = 0x00000010

    # Indicates a Z-sweep/Z-stack operation
    # USE FOR: Workflows that acquire multiple Z planes
    # COMBINE WITH: MAX_PROJECTION for MIP, SAVE_TO_DISK for saving
    STAGE_ZSWEEP = 0x00000020


# Convenience aliases for backward compatibility
class CommandCode:
    """Legacy command code class for backward compatibility."""

    CMD_SCOPE_SETTINGS_LOAD = SystemCommands.SCOPE_SETTINGS_LOAD
    CMD_WORKFLOW_START = CameraCommands.WORKFLOW_START
    CMD_WORKFLOW_STOP = CameraCommands.WORKFLOW_STOP
    CMD_STAGE_POSITION_GET = StageCommands.POSITION_GET
    CMD_STAGE_POSITION_SET = StageCommands.POSITION_SET
    CMD_SYSTEM_STATE_GET = SystemCommands.STATE_GET
    CMD_SYSTEM_STATE_IDLE = SystemCommands.STATE_IDLE
