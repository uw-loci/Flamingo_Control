"""Command codes for the Flamingo TCP protocol: the one table.

Two sources, both resolved from the server's ``CommandCodes.h`` with the
flamingo-server-api skill:

* ``FIXED`` below: every block that has kept its numbers in every known
  server build (LASER, CAMERA, LED, TRIGGER, STAGE, ILLUMINATION_CONTROL,
  SERVO, UI, SYSTEM_STATE, DISPLAY), by the server's name without the
  ``COMMAND_CODES_`` prefix. CAMERA only grew at its end (the 2026-11-13
  binning codes, which are in ``server_layout`` because older builds lack
  them).
* ``core/server_layout.py``: COMMON and META_DATA, which builds renumber.
  Those are sent through ``server_layout.code(name)``, which knows the
  connected build. 0x1007 is SCOPE_SETTINGS on 3.0.0 and DEVICE_RESET on the
  2026-11-13 build, so a name for a COMMON code exists only for a layout.

The classes further down (SystemCommands, StageCommands, ...) and the
per-service tables (StageCommandCode, CameraCommandCode, LaserLEDCommandCode,
socket_reader.ProtocolCommands, tcp_protocol.CommandCode, ...) are aliases
of these values for the code that names them that way;
``tests/test_command_codes_match_the_server.py`` checks every one.

``command_name(code)`` is the one name lookup, for logs.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Optional

from py2flamingo.core import server_layout

#: Wire codes by server name, for the blocks no server build has renumbered.
#: Generated from CommandCodes.h (3.0.0, checked against 2026-11-13); block
#: start/end markers left out. Names are upper case: the header spells one
#: CAMERA_PIXEL_FIELD_Of_VIEW_GET.
FIXED: Dict[str, int] = {
    # LASER
    "LASER_LEVEL_SET": 0x2001,
    "LASER_LEVEL_GET": 0x2002,
    "LASER_ENABLE": 0x2003,
    "LASER_ENABLE_PREVIEW": 0x2004,
    "LASER_ENABLE_LINE": 0x2005,
    "LASER_DISABLE": 0x2006,
    "LASER_DISABLE_ALL": 0x2007,
    "LASER_RECORDING_ENABLE": 0x2008,
    "LASER_RECORDING_DISABLE": 0x2009,
    # CAMERA
    "CAMERA_ALL_SETTINGS_GET": 0x3001,
    "CAMERA_WORK_FLOW_SET": 0x3002,
    "CAMERA_WORK_FLOW_GET": 0x3003,
    "CAMERA_WORK_FLOW_START": 0x3004,
    "CAMERA_WORK_FLOW_STOP": 0x3005,
    "CAMERA_SNAPSHOT_GET": 0x3006,
    "CAMERA_LIVE_VIEW_START": 0x3007,
    "CAMERA_LIVE_VIEW_STOP": 0x3008,
    "CAMERA_EXPOSURE_SET": 0x3009,
    "CAMERA_EXPOSURE_GET": 0x300A,
    "CAMERA_DELAY_SET": 0x300B,
    "CAMERA_DELAY_GET": 0x300C,
    "CAMERA_TRIGGER_MODE_SET": 0x300D,
    "CAMERA_TRIGGER_MODE_GET": 0x300E,
    "CAMERA_ROI_LEFT_SET": 0x300F,
    "CAMERA_ROI_LEFT_GET": 0x3010,
    "CAMERA_ROI_TOP_SET": 0x3011,
    "CAMERA_ROI_TOP_GET": 0x3012,
    "CAMERA_TAKE_STACK": 0x3013,
    "CAMERA_STACK_COMPLETE": 0x3014,
    "CAMERA_SET_IMAGE_MIN_VALUE": 0x3015,
    "CAMERA_SET_IMAGE_MAX_VALUE": 0x3016,
    "CAMERA_FPS_SET": 0x3017,
    "CAMERA_FPS_GET": 0x3018,
    "CAMERA_STORAGE_DISK_FAULT": 0x3019,
    "CAMERA_MAX_PROJECTION_SAVE_ENABLED": 0x301A,
    "CAMERA_MAX_PROJECTION_SAVE_DISABLED": 0x301B,
    "CAMERA_MAX_PROJECTION_DISPLAY_ENABLED": 0x301C,
    "CAMERA_MAX_PROJECTION_DISPLAY_DISABLED": 0x301D,
    "CAMERA_SAVE_IMAGE_DATA_TIFF_ENABLED": 0x301E,
    "CAMERA_SAVE_IMAGE_DATA_TIFF_DISABLED": 0x301F,
    "CAMERA_SAVE_IMAGE_DATA_RAW_ENABLED": 0x3020,
    "CAMERA_SAVE_IMAGE_DATA_RAW_DISABLED": 0x3021,
    "CAMERA_SAVE_TO_SUBFOLDERS_ENABLED": 0x3022,
    "CAMERA_SAVE_TO_SUBFOLDERS_DISABLED": 0x3023,
    "CAMERA_SET_CAMERA1AND2_OPTIONS": 0x3024,
    "CAMERA_1_OVERLAP_SETTINGS": 0x3025,
    "CAMERA_2_OVERLAP_SETTINGS": 0x3026,
    "CAMERA_IMAGE_SIZE_GET": 0x3027,
    "CAMERA_RESET_SETTINGS": 0x3028,
    "CAMERA_RESET_ENABLED": 0x3029,
    "CAMERA_RESET_DISABLED": 0x302A,
    "CAMERA_CHECK_STACK": 0x302B,
    "CAMERA_READ_OUT_TIME_GET": 0x302C,
    "CAMERA_STACK_OPTIONS": 0x302D,
    "CAMERA_SHUTTER_MODE": 0x302E,
    "CAMERA_READOUT_FORMAT": 0x302F,
    "CAMERA_LIGHT_SHEET_MODE_ON_OFF": 0x3030,
    "CAMERA_LIGHT_SHEET_MODE_LINES": 0x3031,
    "CAMERA_LIGHT_SHEET_MODE_DELAY_LINES": 0x3032,
    "CAMERA_LIGHT_SHEET_MODE_EXPOSURE_TIME": 0x3033,
    "CAMERA_SAVE_IMAGE_DATA_BIG_TIFF_ENABLED": 0x3034,
    "CAMERA_SAVE_IMAGE_DATA_BIG_TIFF_DISABLED": 0x3035,
    "CAMERA_CHECK_STACK_UPDATES": 0x3036,
    "CAMERA_PIXEL_FIELD_OF_VIEW_GET": 0x3037,
    "CAMERA_MAX_HORZ_VERT_RES_STD_DESC": 0x3038,
    "CAMERA_MIN_SIZE_HORZ_VERT_DESC": 0x3039,
    # LED
    "LED_SET": 0x4001,
    "LED_PREVIEW_ENABLE": 0x4002,
    "LED_PREVIEW_DISABLE": 0x4003,
    "LED_RECORDING_ENABLE": 0x4004,
    "LED_RECORDING_DISABLE": 0x4005,
    "LED_SELECTION_CHANGE": 0x4006,
    # TRIGGER
    "TRIGGER_EXE_CMD": 0x5001,
    # STAGE
    "STAGE_FIND_REFERENCE": 0x6001,
    "STAGE_HOME": 0x6002,
    "STAGE_HALT": 0x6003,
    "STAGE_POSITION_SET": 0x6004,
    "STAGE_POSITION_SET_SLIDER": 0x6005,
    "STAGE_POSITION_SET_TEXT_CTRL": 0x6006,
    "STAGE_POSITION_SET_UPDATE": 0x6007,
    "STAGE_POSITION_GET": 0x6008,
    "STAGE_VELOCITY_SET": 0x6009,
    "STAGE_VELOCITY_GET": 0x600A,
    "STAGE_VELOCITY_SAVE": 0x600B,
    "STAGE_VELOCITY_RESTORE": 0x600C,
    "STAGE_EXE_CMD_STR": 0x600D,
    "STAGE_EXE_CMD_STR_NO_RESPONSE": 0x600E,
    "STAGE_WAIT_FOR_MOTION_TO_STOP": 0x600F,
    "STAGE_MOTION_STOPPED": 0x6010,
    "STAGE_MOTION_STATUS": 0x6011,
    "STAGE_MOVE_TO_END_ON_TRIGGER": 0x6012,
    "STAGE_CANCEL_MOVE_TO_END_ON_TRIGGER": 0x6013,
    # ILLUMINATION_CONTROL
    "ILLUMINATION_CONTROL_SET": 0x7001,
    "ILLUMINATION_CONTROL_SET_BIAS": 0x7002,
    "ILLUMINATION_CONTROL_WAVE_FORM_SET": 0x7003,
    "ILLUMINATION_CONTROL_LEFT_ENABLED": 0x7004,
    "ILLUMINATION_CONTROL_LEFT_DISABLED": 0x7005,
    "ILLUMINATION_CONTROL_RIGHT_ENABLED": 0x7006,
    "ILLUMINATION_CONTROL_RIGHT_DISABLED": 0x7007,
    "ILLUMINATION_CONTROL_RECORDING_LEFT_ENABLE": 0x7008,
    "ILLUMINATION_CONTROL_RECORDING_LEFT_DISABLE": 0x7009,
    "ILLUMINATION_CONTROL_RECORDING_RIGHT_ENABLE": 0x700A,
    "ILLUMINATION_CONTROL_RECORDING_RIGHT_DISABLE": 0x700B,
    "ILLUMINATION_CONTROL_RESET_FREQUENCY_CONTROLS": 0x700C,
    "ILLUMINATION_CONTROL_BEAM_CONTROL_UI_FREQUENCY_SET": 0x700D,
    "ILLUMINATION_CONTROL_BEAM_CONTROL_UI_BUTTON_START": 0x700E,
    "ILLUMINATION_CONTROL_BEAM_CONTROL_UI_BUTTON_STOP": 0x700F,
    "ILLUMINATION_CONTROL_BEAM_CONTROL_WAVE_FORM_SET": 0x7010,
    "ILLUMINATION_CONTROL_BEAM_CONTROL_DEVICE_SET": 0x7011,
    "ILLUMINATION_CONTROL_MULTI_LASERS_ON": 0x7012,
    # SERVO
    "SERVO_FILTER_WHEEL_POSITION_SET": 0x8001,
    "SERVO_FILTER_WHEEL_MOTION_STOPPED": 0x8002,
    "SERVO_ID_FIND": 0x8003,
    "SERVO_ID_SET": 0x8004,
    "SERVO_ID_IFF": 0x8005,
    "SERVO_POSITION_SET": 0x8006,
    "SERVO_POSITION_GET": 0x8007,
    "SERVO_TEST": 0x8008,
    # UI
    "UI_SET_WORK_FLOW": 0x9001,
    "UI_SET_WORK_FLOW_INDEX": 0x9002,
    "UI_SET_GAUGE_SIZE": 0x9003,
    "UI_SET_GAUGE_VALUE": 0x9004,
    "UI_LIVE_VIEW_UPDATE_ON_CHANGE_ENABLED": 0x9005,
    "UI_LIVE_VIEW_UPDATE_ON_CHANGE_DISABLED": 0x9006,
    "UI_IMAGES_SAVED_TO_STORAGE": 0x9007,
    # SYSTEM_STATE
    "SYSTEM_STATE_DISCONNECTED": 0xA001,
    "SYSTEM_STATE_IDLE": 0xA002,
    "SYSTEM_STATE_SNAP_SHOT": 0xA003,
    "SYSTEM_STATE_LIVE_VIEW": 0xA004,
    "SYSTEM_STATE_WORK_FLOW_RUNNING": 0xA005,
    "SYSTEM_STATE_SYSTEM_UPDATE": 0xA006,
    "SYSTEM_STATE_GET": 0xA007,
    # DISPLAY
    "DISPLAY_SET_STATE": 0xB001,
    "DISPLAY_SET_TOTAL_FRAMES": 0xB002,
    "DISPLAY_SET_FRAME_INDEX": 0xB003,
    "DISPLAY_SET_WAIT_TIME": 0xB004,
    "DISPLAY_SET_WAIT_INDEX": 0xB005,
    "DISPLAY_SET_WHO_AM_I": 0xB006,
}

_NAME_BY_CODE: Dict[int, str] = {v: k for k, v in FIXED.items()}
assert len(_NAME_BY_CODE) == len(FIXED), "two names for one fixed code"


def code(name: str) -> int:
    """Wire code for ``name`` (server name, ``COMMAND_CODES_`` prefix optional).

    Fixed blocks from ``FIXED``; COMMON, META_DATA and the layout-specific
    CAMERA codes from the connected build (``server_layout.code``, which
    raises ``CommandUnavailable`` when the build lacks the command).
    """
    key = name.removeprefix("COMMAND_CODES_")
    if key in FIXED:
        return FIXED[key]
    return server_layout.code(key)


def command_name(code_value: int) -> str:
    """The server's name for ``code_value`` on the connected build.

    COMMON and META_DATA names come from the active layout (3.0.0 before a
    server is identified). ``UNKNOWN_0x....`` for a code no table has.
    """
    layout = server_layout.active() or server_layout.REFERENCE
    for name, value in layout.codes.items():
        if value == code_value:
            return name
    name = _NAME_BY_CODE.get(code_value)
    if name is not None:
        return name
    return f"UNKNOWN_0x{int(code_value):04X}"


def _ref(name: str) -> int:
    """A COMMON code in the 3.0.0 numbering (send it via server_layout.code)."""
    return server_layout.REFERENCE.codes[name]


class AxisCode:
    """Stage axes, as the server numbers them in int32Data0 (SystemIDs.h)."""

    X = 1
    Y = 2
    Z = 3
    R = 4
    # STAGE_AXIS_ALL. HALT and HOME act only on a valid axis (1-4) or on this;
    # axis 0 is STAGE_AXIS_NONE, and the server drops a stage command
    # carrying it before any stage sees it.
    ALL = 0xFF
    # Longer names, used by StageService callers.
    X_AXIS = X
    Y_AXIS = Y
    Z_AXIS = Z
    ROTATION = R


class SystemCommands:
    """System state, and the 3.0.0 COMMON codes this client names."""

    STATE_GET = FIXED["SYSTEM_STATE_GET"]
    STATE_IDLE = FIXED["SYSTEM_STATE_IDLE"]

    # System state values (returned in int32Data0)
    STATE_VALUE_IDLE = 0  # System is idle, ready for commands
    STATE_VALUE_BUSY = 1  # System is busy processing
    STATE_VALUE_ERROR = 2  # System has encountered an error

    # COMMON block, 3.0.0 numbering. Later builds renumber it: send these
    # through server_layout.code(name), which the encoder's guard enforces.
    SCOPE_SETTINGS_LOAD = _ref("COMMON_SCOPE_SETTINGS_LOAD")
    # The server's STORAGE_PATH_SET; with int32Data0 = 0 it lists the drives.
    STORAGE_PATH_GET = _ref("COMMON_STORAGE_PATH_SET")
    STORAGE_PATH_UNMOUNT = _ref("COMMON_STORAGE_PATH_UNMOUNT")
    STORAGE_PATH_TEST = _ref("COMMON_STORAGE_PATH_TEST")


class StageCommands:
    """Stage commands. Positions in mm, rotation in degrees."""

    # The move. One name, one code: the server's STAGE_POSITION_SET.
    POSITION_SET = FIXED["STAGE_POSITION_SET"]
    # Old name for the same code, kept for its callers.
    POSITION_SET_MOVE = POSITION_SET
    # The vendor GUI's slider and text-box variants; the server moves for
    # these too, but this client sends only POSITION_SET.
    POSITION_SET_SLIDER = FIXED["STAGE_POSITION_SET_SLIDER"]
    POSITION_SET_TEXT_CTRL = FIXED["STAGE_POSITION_SET_TEXT_CTRL"]
    POSITION_GET = FIXED["STAGE_POSITION_GET"]

    HOME = FIXED["STAGE_HOME"]
    # HALT, not HOME: at 0x6002 the emergency stop once sent STAGE_HOME.
    HALT = FIXED["STAGE_HALT"]
    VELOCITY_SET = FIXED["STAGE_VELOCITY_SET"]
    VELOCITY_GET = FIXED["STAGE_VELOCITY_GET"]

    WAIT_FOR_MOTION = FIXED["STAGE_WAIT_FOR_MOTION_TO_STOP"]
    MOTION_STOPPED = FIXED["STAGE_MOTION_STOPPED"]

    # The server header has no SAVE_LOCATIONS command at any value. Nothing
    # sends these, and inventing a number would be worse than saying so.
    SAVE_LOCATIONS_GET = None  # no server equivalent - do not send
    SAVE_LOCATIONS_SET = None  # no server equivalent - do not send


class LaserCommands:
    """Laser commands. int32Data0 is the laser line (1-based)."""

    LEVEL_SET = FIXED["LASER_LEVEL_SET"]
    LEVEL_GET = FIXED["LASER_LEVEL_GET"]
    ENABLE = FIXED["LASER_ENABLE"]
    PREVIEW_ENABLE = FIXED["LASER_ENABLE_PREVIEW"]
    # 0x2005 is LASER_ENABLE_LINE. It was named PREVIEW_DISABLE here, so a
    # received 0x2005 was logged as a disable.
    ENABLE_LINE = FIXED["LASER_ENABLE_LINE"]
    DISABLE = FIXED["LASER_DISABLE"]
    ALL_DISABLE = FIXED["LASER_DISABLE_ALL"]

    # Laser level is returned as string in buffer field (e.g., "11.49")


class LEDCommands:
    """LED commands. LED_SET: int32Data0 = color, int32Data1 = percent."""

    SET_VALUE = FIXED["LED_SET"]
    ENABLE = FIXED["LED_PREVIEW_ENABLE"]
    DISABLE = FIXED["LED_PREVIEW_DISABLE"]
    SELECTION_CHANGE = FIXED["LED_SELECTION_CHANGE"]


class CameraCommands:
    """Camera and acquisition commands."""

    SNAPSHOT = FIXED["CAMERA_SNAPSHOT_GET"]
    LIVE_VIEW_START = FIXED["CAMERA_LIVE_VIEW_START"]
    LIVE_VIEW_STOP = FIXED["CAMERA_LIVE_VIEW_STOP"]

    EXPOSURE_SET = FIXED["CAMERA_EXPOSURE_SET"]
    # int32Data0 in MILLISECONDS
    EXPOSURE_GET = FIXED["CAMERA_EXPOSURE_GET"]
    IMAGE_SIZE_GET = FIXED["CAMERA_IMAGE_SIZE_GET"]
    # Per-pixel field of view (mm/px) in doubleData. (The 0x3042/0x3043
    # "PIXEL_SIZE_GET"/"FIELD_OF_VIEW_GET" once listed here do not exist.)
    PIXEL_FIELD_OF_VIEW_GET = FIXED["CAMERA_PIXEL_FIELD_OF_VIEW_GET"]

    WORKFLOW_START = FIXED["CAMERA_WORK_FLOW_START"]
    WORKFLOW_STOP = FIXED["CAMERA_WORK_FLOW_STOP"]

    # Sent by the server when a stack completes:
    #   int32Data0 = images acquired, int32Data1 = images expected,
    #   int32Data2 = error count, doubleData = acquisition time (us)
    STACK_COMPLETE = FIXED["CAMERA_STACK_COMPLETE"]


class IlluminationCommands:
    """Illumination path commands (TSPIM left/right)."""

    LEFT_ENABLE = FIXED["ILLUMINATION_CONTROL_LEFT_ENABLED"]
    LEFT_DISABLE = FIXED["ILLUMINATION_CONTROL_LEFT_DISABLED"]
    RIGHT_ENABLE = FIXED["ILLUMINATION_CONTROL_RIGHT_ENABLED"]
    RIGHT_DISABLE = FIXED["ILLUMINATION_CONTROL_RIGHT_DISABLED"]


class FilterCommands:
    """Filter wheel: the server's SERVO block.

    These were 0x5001/0x5002, which are TRIGGER_EXE_CMD and the TRIGGER
    block's end. Nothing sends them; the names are for the log.
    """

    POSITION_SET = FIXED["SERVO_FILTER_WHEEL_POSITION_SET"]
    MOTION_STOPPED = FIXED["SERVO_FILTER_WHEEL_MOTION_STOPPED"]


class UICommands:
    """Progress messages the server sends during a workflow."""

    SET_GAUGE_SIZE = FIXED["UI_SET_GAUGE_SIZE"]  # progress bar maximum
    SET_GAUGE_VALUE = FIXED["UI_SET_GAUGE_VALUE"]  # int32Data0 = current count
    IMAGES_SAVED_TO_STORAGE = FIXED["UI_IMAGES_SAVED_TO_STORAGE"]


#: Messages the server sends without being asked.
UNSOLICITED_COMMANDS: FrozenSet[int] = frozenset(
    {
        StageCommands.MOTION_STOPPED,
        SystemCommands.STATE_IDLE,  # system became idle (workflow complete)
        CameraCommands.STACK_COMPLETE,
        UICommands.SET_GAUGE_SIZE,
        UICommands.SET_GAUGE_VALUE,
        UICommands.IMAGES_SAVED_TO_STORAGE,
    }
)


class CommandDataBits:
    """
    Command data bits flags for the cmdDataBits0 (params[6]) parameter field.

    These are bit flags that can be combined using bitwise OR (|) operations.
    From CommandCodes.h enum COMMAND_DATA_BITS.

    CRITICAL: The TRIGGER_CALL_BACK flag MUST be set for all GET/query commands,
    otherwise the microscope will not send a response and the command will timeout.

    Usage Examples:
        >>> from py2flamingo.core.tcp_protocol import ProtocolEncoder
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
    """Legacy command code names (tcp_protocol.CommandCode is this class)."""

    CMD_SCOPE_SETTINGS_LOAD = SystemCommands.SCOPE_SETTINGS_LOAD
    CMD_WORKFLOW_START = CameraCommands.WORKFLOW_START
    CMD_WORKFLOW_STOP = CameraCommands.WORKFLOW_STOP
    CMD_STAGE_POSITION_GET = StageCommands.POSITION_GET
    CMD_STAGE_POSITION_SET = StageCommands.POSITION_SET
    CMD_SYSTEM_STATE_GET = SystemCommands.STATE_GET
    CMD_SYSTEM_STATE_IDLE = SystemCommands.STATE_IDLE
