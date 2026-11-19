"""Command names to wire codes, for whichever server build is connected.

COMMON and META_DATA have been renumbered between builds; those come from
``server_layout`` (and raise ``CommandUnavailable`` when the build lacks the
command or the client does not know the build). Every other block has kept
its numbers in all known builds, so those are listed here once, from the
resolved server header (``server-api/codes-*.json``), and still pass the
layout guard on the way out.
"""

from __future__ import annotations

from typing import Dict

from py2flamingo.core import server_layout

FIXED: Dict[str, int] = {
    # LASER
    "LASER_LEVEL_SET": 0x2001,
    "LASER_LEVEL_GET": 0x2002,
    "LASER_ENABLE": 0x2003,
    "LASER_ENABLE_PREVIEW": 0x2004,
    "LASER_ENABLE_LINE": 0x2005,
    "LASER_DISABLE": 0x2006,
    "LASER_DISABLE_ALL": 0x2007,
    # CAMERA (appended to, never renumbered)
    "CAMERA_WORK_FLOW_START": 0x3004,
    "CAMERA_WORK_FLOW_STOP": 0x3005,
    "CAMERA_SNAPSHOT_GET": 0x3006,
    "CAMERA_LIVE_VIEW_START": 0x3007,
    "CAMERA_LIVE_VIEW_STOP": 0x3008,
    "CAMERA_EXPOSURE_SET": 0x3009,
    "CAMERA_EXPOSURE_GET": 0x300A,
    "CAMERA_TRIGGER_MODE_SET": 0x300D,
    "CAMERA_TRIGGER_MODE_GET": 0x300E,
    "CAMERA_ROI_LEFT_SET": 0x300F,
    "CAMERA_ROI_LEFT_GET": 0x3010,
    "CAMERA_ROI_TOP_SET": 0x3011,
    "CAMERA_ROI_TOP_GET": 0x3012,
    "CAMERA_STACK_COMPLETE": 0x3014,
    "CAMERA_FPS_GET": 0x3018,
    "CAMERA_IMAGE_SIZE_GET": 0x3027,
    "CAMERA_CHECK_STACK": 0x302B,
    "CAMERA_READ_OUT_TIME_GET": 0x302C,
    "CAMERA_SHUTTER_MODE": 0x302E,
    "CAMERA_READOUT_FORMAT": 0x302F,
    "CAMERA_LIGHT_SHEET_MODE_ON_OFF": 0x3030,
    "CAMERA_LIGHT_SHEET_MODE_LINES": 0x3031,
    "CAMERA_LIGHT_SHEET_MODE_DELAY_LINES": 0x3032,
    "CAMERA_LIGHT_SHEET_MODE_EXPOSURE_TIME": 0x3033,
    "CAMERA_PIXEL_FIELD_OF_VIEW_GET": 0x3037,
    # LED
    "LED_SET": 0x4001,
    "LED_PREVIEW_ENABLE": 0x4002,
    "LED_PREVIEW_DISABLE": 0x4003,
    "LED_SELECTION_CHANGE": 0x4006,
    # STAGE
    "STAGE_HOME": 0x6002,
    "STAGE_HALT": 0x6003,
    "STAGE_POSITION_SET": 0x6004,
    "STAGE_POSITION_SET_SLIDER": 0x6005,
    "STAGE_POSITION_GET": 0x6008,
    "STAGE_VELOCITY_SET": 0x6009,
    "STAGE_VELOCITY_GET": 0x600A,
    "STAGE_WAIT_FOR_MOTION_TO_STOP": 0x600F,
    "STAGE_MOTION_STOPPED": 0x6010,
    # ILLUMINATION_CONTROL
    "ILLUMINATION_LEFT_ENABLED": 0x7004,
    "ILLUMINATION_LEFT_DISABLED": 0x7005,
    "ILLUMINATION_RIGHT_ENABLED": 0x7006,
    "ILLUMINATION_RIGHT_DISABLED": 0x7007,
    # SERVO
    "SERVO_FILTER_WHEEL_POSITION_SET": 0x8001,
    "SERVO_FILTER_WHEEL_MOTION_STOPPED": 0x8002,
    # UI
    "UI_SET_GAUGE_SIZE": 0x9003,
    "UI_SET_GAUGE_VALUE": 0x9004,
    "UI_IMAGES_SAVED_TO_STORAGE": 0x9007,
    # SYSTEM_STATE
    "SYSTEM_STATE_IDLE": 0xA002,
    "SYSTEM_STATE_GET": 0xA007,
}

_LAYOUT_BLOCKS = ("COMMON_", "META_DATA_")

# Axis numbers the server uses in int32Data0 for stage commands.
AXIS = {"x": 1, "y": 2, "z": 3, "r": 4}


def resolve(name: str) -> int:
    """Wire code for ``name`` on the connected build.

    Raises ``server_layout.CommandUnavailable`` when this build does not have
    the command, numbers it in a way the client does not know, or the layout
    guard would refuse it.
    """
    key = name.removeprefix("COMMAND_CODES_")
    if key.startswith(_LAYOUT_BLOCKS):
        code = server_layout.code(key)
    elif key in FIXED:
        code = FIXED[key]
    else:
        code = server_layout.code(key)  # e.g. CAMERA_BINNING_GET
    server_layout.check_sendable(code)
    return code


def name_of(code: int) -> str:
    """Name of ``code`` on the connected build (for logs and the report)."""
    layout = server_layout.active() or server_layout.REFERENCE
    for name, value in layout.codes.items():
        if value == code:
            return name
    for name, value in FIXED.items():
        if value == code:
            return name
    return f"0x{code:04X}"
