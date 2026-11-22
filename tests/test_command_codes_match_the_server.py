"""Client command codes must agree with the server's own header.

Found 2026-08-28 by cross-checking every constant in the client against
`server-api/codes-tcp-server.json`. The codes that are exercised on every run
were all correct; the ones nothing ever sent were wrong, which is exactly how
they stayed wrong. Three of them mattered:

* ``StageCommands.HALT`` was 0x6002, which is the server's **STAGE_HOME**. The
  emergency stop -- the one control whose entire job is to stop motion -- would
  have commanded a long one instead.
* ``CAMERA_STACK_COMPLETE`` was 0x3011, the server's **CAMERA_ROI_TOP_SET**, and
  the workflow queue registered a callback on it. The backup completion signal
  could never fire from a stack finishing, and a ROI_TOP_SET response would have
  looked like one. Only the primary signal (SYSTEM_STATE_IDLE) held the queue
  together, which is why nobody noticed.
* ``UI_IMAGES_SAVED_TO_STORAGE`` was 0x9008, the server's **UI_END**.

The expected values are written out here rather than read from
``~/LSControl/server-api/codes-tcp-server.json`` on purpose: that baseline lives
outside this repo (deliberately -- this repo is public), so a test that depended
on it would silently pass in CI by skipping. Copying the numbers in makes the
contract reviewable in the diff, and updating them a conscious act.

Only codes this client actually sends or dispatches on are listed. The full
202-entry table is the baseline's job.

Run: .venv/bin/python -m pytest tests/test_command_codes_match_the_server.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# name -> (value, server's name for that value). Transcribed from
# server-api/codes-tcp-server.json, resolved from oldcodereference/CommandCodes.h.
SERVER = {
    "STAGE_FIND_REFERENCE": 0x6001,
    "STAGE_HOME": 0x6002,
    "STAGE_HALT": 0x6003,
    "STAGE_POSITION_SET": 0x6004,
    "STAGE_POSITION_SET_SLIDER": 0x6005,
    "STAGE_POSITION_GET": 0x6008,
    "STAGE_VELOCITY_SET": 0x6009,
    "STAGE_WAIT_FOR_MOTION_TO_STOP": 0x600F,
    "STAGE_MOTION_STOPPED": 0x6010,
    "CAMERA_ALL_SETTINGS_GET": 0x3001,
    "CAMERA_WORK_FLOW_START": 0x3004,
    "CAMERA_WORK_FLOW_STOP": 0x3005,
    "CAMERA_SNAPSHOT_GET": 0x3006,
    "CAMERA_LIVE_VIEW_START": 0x3007,
    "CAMERA_LIVE_VIEW_STOP": 0x3008,
    "CAMERA_EXPOSURE_SET": 0x3009,
    "CAMERA_EXPOSURE_GET": 0x300A,
    "CAMERA_ROI_LEFT_SET": 0x300F,
    "CAMERA_ROI_TOP_SET": 0x3011,
    "CAMERA_STACK_COMPLETE": 0x3014,
    "CAMERA_IMAGE_SIZE_GET": 0x3027,
    "CAMERA_PIXEL_FIELD_OF_VIEW_GET": 0x3037,
    "LED_SET": 0x4001,
    "LED_PREVIEW_ENABLE": 0x4002,
    "LED_PREVIEW_DISABLE": 0x4003,
    "LASER_LEVEL_SET": 0x2001,
    "LASER_ENABLE_PREVIEW": 0x2004,
    "LASER_DISABLE_ALL": 0x2007,
    "UI_SET_GAUGE_SIZE": 0x9003,
    "UI_SET_GAUGE_VALUE": 0x9004,
    "UI_IMAGES_SAVED_TO_STORAGE": 0x9007,
    "UI_END": 0x9008,
    "SYSTEM_STATE_IDLE": 0xA002,
}


def _stage():
    from py2flamingo.core.command_codes import StageCommands

    return StageCommands


class TestTheCodesThatMoveOrStopTheStage:
    def test_halt_is_the_servers_halt_not_its_home(self):
        # The regression this file exists for. At 0x6002 the emergency stop sent
        # STAGE_HOME: a long motion, from the control meant to end one.
        assert _stage().HALT == SERVER["STAGE_HALT"]

    def test_halt_is_not_home(self):
        assert _stage().HALT != SERVER["STAGE_HOME"]

    def test_home_is_not_find_reference(self):
        assert _stage().HOME == SERVER["STAGE_HOME"]

    def test_the_move_command_is_unchanged(self):
        # Proven on every run; if this ever fails, suspect the test, not the code.
        assert _stage().POSITION_SET_MOVE == SERVER["STAGE_POSITION_SET"]

    def test_position_get_is_unchanged(self):
        assert _stage().POSITION_GET == SERVER["STAGE_POSITION_GET"]

    def test_motion_stopped_is_unchanged(self):
        assert _stage().MOTION_STOPPED == SERVER["STAGE_MOTION_STOPPED"]

    def test_velocity_set_is_not_a_position_command(self):
        assert _stage().VELOCITY_SET == SERVER["STAGE_VELOCITY_SET"]

    def test_no_stage_command_collides_with_another(self):
        # 0x6002 meaning both HOME and HALT is how the emergency stop broke.
        # POSITION_SET_MOVE is the old name of POSITION_SET, on purpose.
        values = [
            v
            for k, v in vars(_stage()).items()
            if not k.startswith("_") and isinstance(v, int) and k != "POSITION_SET_MOVE"
        ]
        assert len(values) == len(set(values))


class TestTheWorkflowCompletionCallbacks:
    def test_stack_complete_is_not_the_roi_command(self):
        from py2flamingo.services.workflow_queue_service import CAMERA_STACK_COMPLETE

        assert CAMERA_STACK_COMPLETE == SERVER["CAMERA_STACK_COMPLETE"]
        assert CAMERA_STACK_COMPLETE != SERVER["CAMERA_ROI_TOP_SET"]

    def test_images_saved_is_not_ui_end(self):
        from py2flamingo.services.workflow_queue_service import UI_IMAGES_SAVED

        assert UI_IMAGES_SAVED == SERVER["UI_IMAGES_SAVED_TO_STORAGE"]
        assert UI_IMAGES_SAVED != SERVER["UI_END"]

    def test_the_primary_completion_signal_is_unchanged(self):
        # This one was always right, and is the only reason the queue worked.
        from py2flamingo.services.workflow_queue_service import SYSTEM_STATE_IDLE

        assert SYSTEM_STATE_IDLE == SERVER["SYSTEM_STATE_IDLE"]

    def test_the_command_codes_table_agrees_with_the_queue(self):
        from py2flamingo.core.command_codes import CameraCommands, UICommands
        from py2flamingo.services.workflow_queue_service import (
            CAMERA_STACK_COMPLETE,
            UI_IMAGES_SAVED,
        )

        assert CameraCommands.STACK_COMPLETE == CAMERA_STACK_COMPLETE
        assert UICommands.IMAGES_SAVED_TO_STORAGE == UI_IMAGES_SAVED


class TestTheUnsolicitedCallbackSet:
    def test_it_lists_the_servers_codes(self):
        from py2flamingo.core.socket_reader import UNSOLICITED_COMMANDS

        assert SERVER["CAMERA_STACK_COMPLETE"] in UNSOLICITED_COMMANDS
        assert SERVER["UI_IMAGES_SAVED_TO_STORAGE"] in UNSOLICITED_COMMANDS

    def test_it_no_longer_lists_the_wrong_ones(self):
        from py2flamingo.core.socket_reader import UNSOLICITED_COMMANDS

        assert SERVER["CAMERA_ROI_TOP_SET"] not in UNSOLICITED_COMMANDS
        assert SERVER["UI_END"] not in UNSOLICITED_COMMANDS


class TestTheLogLabelTable:
    """`ProtocolCommands` names received codes in the log.

    A wrong entry does not merely fail to label a line -- it labels it
    *plausibly*, and three sessions were just spent reading these logs.
    """

    @pytest.mark.parametrize(
        "member,server_name",
        [
            ("STAGE_HOME", "STAGE_HOME"),
            ("STAGE_HALT", "STAGE_HALT"),
            ("STAGE_POSITION_SET_MOVE", "STAGE_POSITION_SET"),
            ("STAGE_POSITION_GET", "STAGE_POSITION_GET"),
            ("STAGE_VELOCITY_SET", "STAGE_VELOCITY_SET"),
            ("STAGE_MOTION_STOPPED", "STAGE_MOTION_STOPPED"),
            ("CAMERA_EXPOSURE_SET", "CAMERA_EXPOSURE_SET"),
            ("CAMERA_EXPOSURE_GET", "CAMERA_EXPOSURE_GET"),
            ("CAMERA_STACK_COMPLETE", "CAMERA_STACK_COMPLETE"),
            ("CAMERA_LIVE_VIEW_START", "CAMERA_LIVE_VIEW_START"),
            ("CAMERA_IMAGE_SIZE_GET", "CAMERA_IMAGE_SIZE_GET"),
            ("LED_SET", "LED_SET"),
            ("LED_ENABLE", "LED_PREVIEW_ENABLE"),
            ("LED_DISABLE", "LED_PREVIEW_DISABLE"),
            ("SYSTEM_STATE_IDLE", "SYSTEM_STATE_IDLE"),
        ],
    )
    def test_the_label_resolves_to_the_right_command(self, member, server_name):
        from py2flamingo.core.socket_reader import ProtocolCommands

        assert getattr(ProtocolCommands, member).value == SERVER[server_name]

    def test_no_two_labels_share_a_value(self):
        # IntEnum silently aliases duplicates, so a collision would not raise --
        # it would just make one of the two names unreachable in the log.
        from py2flamingo.core.socket_reader import ProtocolCommands

        assert len(set(ProtocolCommands)) == len(list(ProtocolCommands))


# --------------------------------------------------------------------------
# One table: every code table in the client is an alias of
# core/command_codes.FIXED (fixed blocks) or the 3.0.0 server layout (COMMON).
# Each entry below names the server command its attribute must equal; an
# attribute that is not listed fails too, so a new constant has to be mapped.
# --------------------------------------------------------------------------


def _tables():
    from py2flamingo.controllers.position_controller import PositionController
    from py2flamingo.core import command_codes as cc
    from py2flamingo.core.socket_reader import ProtocolCommands
    from py2flamingo.services import safe_state
    from py2flamingo.services.camera_service import CameraCommandCode
    from py2flamingo.services.laser_led_service import LaserLEDCommandCode
    from py2flamingo.services.motion_tracker import MotionTracker
    from py2flamingo.services.position_debug import PositionDebugHelper
    from py2flamingo.services.stage_service import StageCommandCode

    return {
        "SystemCommands": (
            cc.SystemCommands,
            {
                "STATE_GET": "SYSTEM_STATE_GET",
                "STATE_IDLE": "SYSTEM_STATE_IDLE",
                "SCOPE_SETTINGS_LOAD": "COMMON_SCOPE_SETTINGS_LOAD",
                "STORAGE_PATH_GET": "COMMON_STORAGE_PATH_SET",
                "STORAGE_PATH_UNMOUNT": "COMMON_STORAGE_PATH_UNMOUNT",
                "STORAGE_PATH_TEST": "COMMON_STORAGE_PATH_TEST",
            },
            {"STATE_VALUE_IDLE", "STATE_VALUE_BUSY", "STATE_VALUE_ERROR"},
        ),
        "StageCommands": (
            cc.StageCommands,
            {
                "POSITION_SET": "STAGE_POSITION_SET",
                "POSITION_SET_MOVE": "STAGE_POSITION_SET",
                "POSITION_SET_SLIDER": "STAGE_POSITION_SET_SLIDER",
                "POSITION_SET_TEXT_CTRL": "STAGE_POSITION_SET_TEXT_CTRL",
                "POSITION_GET": "STAGE_POSITION_GET",
                "HOME": "STAGE_HOME",
                "HALT": "STAGE_HALT",
                "VELOCITY_SET": "STAGE_VELOCITY_SET",
                "VELOCITY_GET": "STAGE_VELOCITY_GET",
                "WAIT_FOR_MOTION": "STAGE_WAIT_FOR_MOTION_TO_STOP",
                "MOTION_STOPPED": "STAGE_MOTION_STOPPED",
            },
            set(),
        ),
        "LaserCommands": (
            cc.LaserCommands,
            {
                "LEVEL_SET": "LASER_LEVEL_SET",
                "LEVEL_GET": "LASER_LEVEL_GET",
                "ENABLE": "LASER_ENABLE",
                "PREVIEW_ENABLE": "LASER_ENABLE_PREVIEW",
                "ENABLE_LINE": "LASER_ENABLE_LINE",
                "DISABLE": "LASER_DISABLE",
                "ALL_DISABLE": "LASER_DISABLE_ALL",
            },
            set(),
        ),
        "LEDCommands": (
            cc.LEDCommands,
            {
                "SET_VALUE": "LED_SET",
                "ENABLE": "LED_PREVIEW_ENABLE",
                "DISABLE": "LED_PREVIEW_DISABLE",
                "SELECTION_CHANGE": "LED_SELECTION_CHANGE",
            },
            set(),
        ),
        "CameraCommands": (
            cc.CameraCommands,
            {
                "SNAPSHOT": "CAMERA_SNAPSHOT_GET",
                "LIVE_VIEW_START": "CAMERA_LIVE_VIEW_START",
                "LIVE_VIEW_STOP": "CAMERA_LIVE_VIEW_STOP",
                "EXPOSURE_SET": "CAMERA_EXPOSURE_SET",
                "EXPOSURE_GET": "CAMERA_EXPOSURE_GET",
                "IMAGE_SIZE_GET": "CAMERA_IMAGE_SIZE_GET",
                "PIXEL_FIELD_OF_VIEW_GET": "CAMERA_PIXEL_FIELD_OF_VIEW_GET",
                "WORKFLOW_START": "CAMERA_WORK_FLOW_START",
                "WORKFLOW_STOP": "CAMERA_WORK_FLOW_STOP",
                "STACK_COMPLETE": "CAMERA_STACK_COMPLETE",
            },
            set(),
        ),
        "IlluminationCommands": (
            cc.IlluminationCommands,
            {
                "LEFT_ENABLE": "ILLUMINATION_CONTROL_LEFT_ENABLED",
                "LEFT_DISABLE": "ILLUMINATION_CONTROL_LEFT_DISABLED",
                "RIGHT_ENABLE": "ILLUMINATION_CONTROL_RIGHT_ENABLED",
                "RIGHT_DISABLE": "ILLUMINATION_CONTROL_RIGHT_DISABLED",
            },
            set(),
        ),
        "FilterCommands": (
            cc.FilterCommands,
            {
                "POSITION_SET": "SERVO_FILTER_WHEEL_POSITION_SET",
                "MOTION_STOPPED": "SERVO_FILTER_WHEEL_MOTION_STOPPED",
            },
            set(),
        ),
        "UICommands": (
            cc.UICommands,
            {
                "SET_GAUGE_SIZE": "UI_SET_GAUGE_SIZE",
                "SET_GAUGE_VALUE": "UI_SET_GAUGE_VALUE",
                "IMAGES_SAVED_TO_STORAGE": "UI_IMAGES_SAVED_TO_STORAGE",
            },
            set(),
        ),
        "CommandCode": (
            cc.CommandCode,
            {
                "CMD_SCOPE_SETTINGS_LOAD": "COMMON_SCOPE_SETTINGS_LOAD",
                "CMD_WORKFLOW_START": "CAMERA_WORK_FLOW_START",
                "CMD_WORKFLOW_STOP": "CAMERA_WORK_FLOW_STOP",
                "CMD_STAGE_POSITION_GET": "STAGE_POSITION_GET",
                "CMD_STAGE_POSITION_SET": "STAGE_POSITION_SET",
                "CMD_SYSTEM_STATE_GET": "SYSTEM_STATE_GET",
                "CMD_SYSTEM_STATE_IDLE": "SYSTEM_STATE_IDLE",
            },
            set(),
        ),
        "StageCommandCode": (
            StageCommandCode,
            {
                "POSITION_GET": "STAGE_POSITION_GET",
                "MOTION_STOPPED": "STAGE_MOTION_STOPPED",
                "POSITION_SET": "STAGE_POSITION_SET",
                "POSITION_SET_SLIDER": "STAGE_POSITION_SET_SLIDER",
                "POSITION_SET_TEXT": "STAGE_POSITION_SET_TEXT_CTRL",
            },
            set(),
        ),
        "CameraCommandCode": (
            CameraCommandCode,
            {
                "IMAGE_SIZE_GET": "CAMERA_IMAGE_SIZE_GET",
                "PIXEL_FIELD_OF_VIEW_GET": "CAMERA_PIXEL_FIELD_OF_VIEW_GET",
                "EXPOSURE_GET": "CAMERA_EXPOSURE_GET",
                "READ_OUT_TIME_GET": "CAMERA_READ_OUT_TIME_GET",
                "FPS_GET": "CAMERA_FPS_GET",
                "SNAPSHOT": "CAMERA_SNAPSHOT_GET",
                "LIVE_VIEW_START": "CAMERA_LIVE_VIEW_START",
                "LIVE_VIEW_STOP": "CAMERA_LIVE_VIEW_STOP",
                "WORKFLOW_START": "CAMERA_WORK_FLOW_START",
                "WORKFLOW_STOP": "CAMERA_WORK_FLOW_STOP",
                "LIGHT_SHEET_MODE_ON_OFF": "CAMERA_LIGHT_SHEET_MODE_ON_OFF",
                "LIGHT_SHEET_MODE_LINES": "CAMERA_LIGHT_SHEET_MODE_LINES",
                "LIGHT_SHEET_MODE_DELAY_LINES": "CAMERA_LIGHT_SHEET_MODE_DELAY_LINES",
                "LIGHT_SHEET_MODE_EXPOSURE_TIME": "CAMERA_LIGHT_SHEET_MODE_EXPOSURE_TIME",
                "TRIGGER_MODE_SET": "CAMERA_TRIGGER_MODE_SET",
                "TRIGGER_MODE_GET": "CAMERA_TRIGGER_MODE_GET",
                "SHUTTER_MODE_GET": "CAMERA_SHUTTER_MODE",
                "READOUT_FORMAT_GET": "CAMERA_READOUT_FORMAT",
                "ROI_LEFT_SET": "CAMERA_ROI_LEFT_SET",
                "ROI_LEFT_GET": "CAMERA_ROI_LEFT_GET",
                "ROI_TOP_SET": "CAMERA_ROI_TOP_SET",
                "ROI_TOP_GET": "CAMERA_ROI_TOP_GET",
            },
            set(),
        ),
        "LaserLEDCommandCode": (
            LaserLEDCommandCode,
            {
                "LASER_LEVEL_SET": "LASER_LEVEL_SET",
                "LASER_LEVEL_GET": "LASER_LEVEL_GET",
                "LASER_ENABLE": "LASER_ENABLE",
                "LASER_ENABLE_PREVIEW": "LASER_ENABLE_PREVIEW",
                "LASER_ENABLE_LINE": "LASER_ENABLE_LINE",
                "LASER_DISABLE": "LASER_DISABLE",
                "LASER_DISABLE_ALL": "LASER_DISABLE_ALL",
                "LED_SET": "LED_SET",
                "LED_PREVIEW_ENABLE": "LED_PREVIEW_ENABLE",
                "LED_PREVIEW_DISABLE": "LED_PREVIEW_DISABLE",
                "ILLUMINATION_LEFT_ENABLE": "ILLUMINATION_CONTROL_LEFT_ENABLED",
                "ILLUMINATION_LEFT_DISABLE": "ILLUMINATION_CONTROL_LEFT_DISABLED",
                "ILLUMINATION_RIGHT_ENABLE": "ILLUMINATION_CONTROL_RIGHT_ENABLED",
                "ILLUMINATION_RIGHT_DISABLE": "ILLUMINATION_CONTROL_RIGHT_DISABLED",
            },
            set(),
        ),
        "ProtocolCommands": (
            ProtocolCommands,
            {
                "STAGE_HOME": "STAGE_HOME",
                "STAGE_HALT": "STAGE_HALT",
                "STAGE_POSITION_SET_MOVE": "STAGE_POSITION_SET",
                "STAGE_POSITION_SET_SLIDER": "STAGE_POSITION_SET_SLIDER",
                "STAGE_VELOCITY_SET": "STAGE_VELOCITY_SET",
                "STAGE_POSITION_GET": "STAGE_POSITION_GET",
                "STAGE_VELOCITY_GET": "STAGE_VELOCITY_GET",
                "STAGE_WAIT_FOR_MOTION": "STAGE_WAIT_FOR_MOTION_TO_STOP",
                "STAGE_MOTION_STOPPED": "STAGE_MOTION_STOPPED",
                "SYSTEM_STATE_GET": "SYSTEM_STATE_GET",
                "SYSTEM_STATE_IDLE": "SYSTEM_STATE_IDLE",
                "CAMERA_EXPOSURE_SET": "CAMERA_EXPOSURE_SET",
                "CAMERA_STACK_COMPLETE": "CAMERA_STACK_COMPLETE",
                "CAMERA_EXPOSURE_GET": "CAMERA_EXPOSURE_GET",
                "CAMERA_WORKFLOW_START": "CAMERA_WORK_FLOW_START",
                "CAMERA_WORKFLOW_STOP": "CAMERA_WORK_FLOW_STOP",
                "CAMERA_SNAPSHOT": "CAMERA_SNAPSHOT_GET",
                "CAMERA_LIVE_VIEW_START": "CAMERA_LIVE_VIEW_START",
                "CAMERA_LIVE_VIEW_STOP": "CAMERA_LIVE_VIEW_STOP",
                "CAMERA_IMAGE_SIZE_GET": "CAMERA_IMAGE_SIZE_GET",
                "CAMERA_PIXEL_FIELD_OF_VIEW_GET": "CAMERA_PIXEL_FIELD_OF_VIEW_GET",
                "LASER_LEVEL_SET": "LASER_LEVEL_SET",
                "LASER_LEVEL_GET": "LASER_LEVEL_GET",
                "LASER_PREVIEW_ENABLE": "LASER_ENABLE_PREVIEW",
                "LASER_ENABLE_LINE": "LASER_ENABLE_LINE",
                "LASER_ALL_DISABLE": "LASER_DISABLE_ALL",
                "LED_SET": "LED_SET",
                "LED_ENABLE": "LED_PREVIEW_ENABLE",
                "LED_DISABLE": "LED_PREVIEW_DISABLE",
                "FILTER_POSITION_SET": "SERVO_FILTER_WHEEL_POSITION_SET",
                "FILTER_MOTION_STOPPED": "SERVO_FILTER_WHEEL_MOTION_STOPPED",
                "ILLUMINATION_LEFT_ENABLE": "ILLUMINATION_CONTROL_LEFT_ENABLED",
                "ILLUMINATION_LEFT_DISABLE": "ILLUMINATION_CONTROL_LEFT_DISABLED",
                "ILLUMINATION_RIGHT_ENABLE": "ILLUMINATION_CONTROL_RIGHT_ENABLED",
                "ILLUMINATION_RIGHT_DISABLE": "ILLUMINATION_CONTROL_RIGHT_DISABLED",
            },
            set(),
        ),
        "PositionController": (
            PositionController,
            {
                "COMMAND_CODES_STAGE_POSITION_SET": "STAGE_POSITION_SET",
                "COMMAND_CODES_STAGE_POSITION_GET": "STAGE_POSITION_GET",
            },
            None,
        ),
        "PositionDebugHelper": (
            PositionDebugHelper,
            {"COMMAND_CODES_STAGE_POSITION_GET": "STAGE_POSITION_GET"},
            None,
        ),
        "MotionTracker": (
            MotionTracker,
            {"STAGE_MOTION_STOPPED": "STAGE_MOTION_STOPPED"},
            None,
        ),
        "safe_state": (
            safe_state,
            {
                "STAGE_HALT": "STAGE_HALT",
                "CAMERA_WORK_FLOW_STOP": "CAMERA_WORK_FLOW_STOP",
                "CAMERA_LIVE_VIEW_STOP": "CAMERA_LIVE_VIEW_STOP",
                "LASER_DISABLE_ALL": "LASER_DISABLE_ALL",
                "LED_PREVIEW_DISABLE": "LED_PREVIEW_DISABLE",
            },
            None,
        ),
    }


def _authoritative(name):
    from py2flamingo.core import command_codes as cc
    from py2flamingo.core import server_layout

    if name in cc.FIXED:
        return cc.FIXED[name]
    return server_layout.REFERENCE.codes[name]


class TestEveryTableIsTheOneTable:
    @pytest.mark.parametrize("table", sorted(_tables()))
    def test_each_entry_equals_the_servers_code(self, table):
        owner, mapping, not_codes = _tables()[table]
        for attr, server_name in mapping.items():
            assert getattr(owner, attr) == _authoritative(
                server_name
            ), f"{table}.{attr} != {server_name}"
        if not_codes is None:
            return  # a class with other attributes: only the mapped ones
        names = {
            k
            for k, v in vars(owner).items()
            if not k.startswith("_") and isinstance(v, int)
        }
        if hasattr(owner, "__members__"):
            names = set(owner.__members__)
        assert (
            names - set(mapping) - not_codes == set()
        ), f"{table} has codes no test maps to a server name"

    def test_the_fixed_table_has_one_name_per_code(self):
        from py2flamingo.core import command_codes as cc

        assert len(set(cc.FIXED.values())) == len(cc.FIXED)

    def test_verification_uses_the_core_table(self):
        from py2flamingo.core import command_codes as cc
        from py2flamingo.verification import codes

        assert codes.FIXED is cc.FIXED

    def test_tcp_protocol_names_are_the_core_ones(self):
        from py2flamingo.core import command_codes as cc
        from py2flamingo.core import tcp_protocol

        assert tcp_protocol.CommandCode is cc.CommandCode
        assert tcp_protocol.CommandDataBits is cc.CommandDataBits

    def test_core_does_not_import_verification(self):
        import ast

        core = Path(__file__).resolve().parents[1] / "src" / "py2flamingo" / "core"
        for path in core.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "verification" not in node.module, path.name


class TestOneMoveCodeAndOneAxisCode:
    def test_position_set_is_0x6004_everywhere(self):
        from py2flamingo.core import command_codes as cc
        from py2flamingo.core import tcp_protocol
        from py2flamingo.services.stage_service import StageCommandCode

        assert (
            cc.StageCommands.POSITION_SET
            == StageCommandCode.POSITION_SET
            == tcp_protocol.CommandCode.CMD_STAGE_POSITION_SET
            == 0x6004
        )
        assert cc.StageCommands.POSITION_SET_SLIDER == 0x6005

    def test_there_is_one_axis_code(self):
        from py2flamingo.controllers import position_controller
        from py2flamingo.core.command_codes import AxisCode
        from py2flamingo.services import stage_service

        assert stage_service.AxisCode is AxisCode
        assert position_controller.AxisCode is AxisCode
        assert (AxisCode.X, AxisCode.Y, AxisCode.Z, AxisCode.R) == (1, 2, 3, 4)
        assert AxisCode.ALL == 0xFF
        assert (AxisCode.X_AXIS, AxisCode.ROTATION) == (AxisCode.X, AxisCode.R)


class TestLogNamesAreTheServers:
    def test_0x2005_is_enable_line_not_a_disable(self):
        from py2flamingo.core.tcp_protocol import get_command_name

        assert get_command_name(0x2005) == "LASER_ENABLE_LINE"

    def test_the_filter_wheel_is_in_the_servo_block(self):
        from py2flamingo.core.tcp_protocol import get_command_name

        assert get_command_name(0x8001) == "SERVO_FILTER_WHEEL_POSITION_SET"
        assert get_command_name(0x5001) == "TRIGGER_EXE_CMD"

    def test_codes_the_server_does_not_have_are_unknown(self):
        from py2flamingo.core.tcp_protocol import get_command_name

        assert get_command_name(0x3042).startswith("UNKNOWN")
        assert get_command_name(0x3043).startswith("UNKNOWN")

    def test_common_names_follow_the_connected_build(self):
        from py2flamingo.core import server_layout
        from py2flamingo.core.frames import parse_frame
        from py2flamingo.core.tcp_protocol import get_command_name

        try:
            server_layout.set_active(server_layout.REFERENCE)
            assert get_command_name(0x1007) == "COMMON_SCOPE_SETTINGS"
            server_layout.set_active(server_layout.LAYOUT_2026_11_13)
            assert get_command_name(0x1007) == "COMMON_DEVICE_RESET"
            frame = bytearray(128)
            frame[4:8] = (0x1007).to_bytes(4, "little")
            assert parse_frame(bytes(frame)).command_name == "COMMON_DEVICE_RESET"
        finally:
            server_layout.clear_active()
