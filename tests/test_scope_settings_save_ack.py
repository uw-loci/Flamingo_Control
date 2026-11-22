"""An acknowledgment is only an acknowledgment if it is the server's own.

`SCOPE_SETTINGS_SAVE` replaces the settings the microscope works with, and
the calibrated objective magnification reaches every future acquisition
through it. The 3.0.0 server answers it with COMMON_SCOPE_SETTINGS (0x1007),
status 1, echoing the file, or with MESSAGE_FROM_CONTROLLER when it refuses
the file (ControlServer::saveScopeSettings); it never answers with
SCOPE_SETTINGS_SAVE itself. Until 2026-11-22 the client waited for exactly
that impossible ack (code 0x1008 and status 0), on the raw socket.

Every field is checked: markers, the reply code, the status.

Run: python3 -m pytest tests/test_scope_settings_save_ack.py -q
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.controllers.position_debug import (  # noqa: E402
    PositionDebugHelper,
)
from py2flamingo.core.frames import frame_fields, parse_frame  # noqa: E402

SCOPE_SETTINGS = 0x1007
MESSAGE_FROM_CONTROLLER = 0x1006


def _ack(
    start: int = PositionDebugHelper.START_MARKER,
    code: int = SCOPE_SETTINGS,
    status: int = 1,
    end: int = PositionDebugHelper.END_MARKER,
    additional: bytes = b"",
) -> dict:
    frame = bytearray(128)
    frame[0:4] = struct.pack("<I", start)
    frame[4:8] = struct.pack("<I", code)
    frame[8:12] = struct.pack("<I", status)
    frame[124:128] = struct.pack("<I", end)
    fields = frame_fields(parse_frame(bytes(frame)))
    fields["raw"] = bytes(frame)
    fields["additional_data"] = additional
    return fields


class _Connection:
    def is_connected(self):
        return True


class _Logger:
    def info(self, *_a, **_k):
        pass

    def error(self, *_a, **_k):
        pass


def _save(ack: dict) -> dict:
    helper = PositionDebugHelper.__new__(PositionDebugHelper)
    helper.connection = _Connection()
    helper.logger = _Logger()
    helper._exchange = lambda *_a, **_k: ack
    return helper.debug_save_settings(b"settings-bytes")


class TestTheAckIsActuallyChecked:
    def test_the_settings_echo_with_status_1_succeeds(self):
        assert _save(_ack())["success"] is True

    def test_another_status_is_a_failure(self):
        # Reporting a rejected save as a success is the worst outcome here:
        # the user believes the scope now carries the calibrated
        # magnification when it does not.
        result = _save(_ack(status=0))
        assert result["success"] is False
        assert "status 0" in result["error"]
        assert "may NOT have been saved" in result["error"]

    def test_a_refusal_is_a_failure_and_says_why(self):
        result = _save(
            _ack(
                code=MESSAGE_FROM_CONTROLLER,
                additional=b"Save scope settings error - invalid scope settings file",
            )
        )
        assert result["success"] is False
        assert "invalid scope settings file" in result["error"]

    def test_a_wrong_command_echo_is_a_failure(self):
        result = _save(_ack(code=9999))
        assert result["success"] is False
        assert "different command" in result["error"]

    def test_a_bad_end_marker_is_a_failure(self):
        # A good start with a bad end means the frame is truncated or
        # misaligned, so the status and command fields between them cannot be
        # trusted either.
        result = _save(_ack(end=0xDEADBEEF))
        assert result["success"] is False
        assert "end marker" in result["error"]

    def test_a_bad_start_marker_is_a_failure(self):
        assert _save(_ack(start=0xDEADBEEF))["success"] is False

    def test_no_reply_is_a_failure(self):
        result = _save(None)
        assert result["success"] is False
        assert "Timeout" in result["error"]

    def test_the_failure_message_always_carries_the_raw_fields(self):
        # Whoever reads this next needs the numbers, not an adjective.
        for ack in (
            _ack(status=7),
            _ack(code=9999),
            _ack(end=0xDEADBEEF),
            _ack(start=0xDEADBEEF),
        ):
            error = _save(ack)["error"]
            assert "marker=0x" in error and "status=" in error


class TestDisconnected:
    def test_it_refuses_rather_than_pretending(self):
        helper = PositionDebugHelper.__new__(PositionDebugHelper)
        helper.logger = _Logger()

        class _Offline(_Connection):
            def is_connected(self):
                return False

        helper.connection = _Offline()
        result = helper.debug_save_settings(b"x")
        assert result["success"] is False
        assert "Not connected" in result["error"]


class TestTheProtocolConstants:
    @pytest.mark.parametrize(
        "name,value",
        [("START_MARKER", 0xF321E654), ("END_MARKER", 0xFEDC4321)],
    )
    def test_markers_match_the_protocol(self, name, value):
        assert getattr(PositionDebugHelper, name) == value

    def test_position_set_and_get_do_not_share_a_command_code(self):
        """A comment in the LED overview claimed they collide. They do not.

        `_capture_plane`'s docstring attributed the "Overwriting pending
        request for 0x6008" warnings to POSITION_SET and POSITION_GET sharing
        code 24584. They are distinct (24580 / 24581 vs 24584), so the real
        cause was back-to-back POSITION_GET queries overwriting each other in
        the single-slot pending map. The fix — wait on STAGE_MOTION_STOPPED
        instead of polling — was right either way, but a wrong mechanism in a
        docstring sends the next person after the wrong bug.
        """
        from py2flamingo.services.stage_service import StageCommandCode

        assert StageCommandCode.POSITION_GET == 24584
        assert StageCommandCode.POSITION_SET == 24580
        assert StageCommandCode.POSITION_SET_SLIDER == 24581
        assert StageCommandCode.POSITION_SET != StageCommandCode.POSITION_GET
