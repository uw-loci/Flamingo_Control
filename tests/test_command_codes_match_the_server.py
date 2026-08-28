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
        values = [
            v
            for k, v in vars(_stage()).items()
            if not k.startswith("_") and isinstance(v, int)
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
