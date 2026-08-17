"""An acknowledgment is only an acknowledgment if we read it.

`SCOPE_SETTINGS_SAVE` OVERWRITES the microscope's own settings file, and the
calibrated objective magnification reaches every future acquisition through it.
The reply was previously accepted on its start marker alone — so a scope that
answered with an error status, echoed a different command, or sent a truncated
frame was reported to the user as a successful save.

The protocol puts a status/error code at bytes 8-11 and an end marker at 124-127
(`core/protocol_encoder.py`). Nothing in the codebase read either until
2026-08-17.

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

SCOPE_SETTINGS_SAVE = 4104


def _ack(
    start: int = PositionDebugHelper.START_MARKER,
    code: int = SCOPE_SETTINGS_SAVE,
    status: int = 0,
    end: int = PositionDebugHelper.END_MARKER,
) -> bytes:
    frame = bytearray(128)
    frame[0:4] = struct.pack("<I", start)
    frame[4:8] = struct.pack("<I", code)
    frame[8:12] = struct.pack("<I", status)
    frame[124:128] = struct.pack("<I", end)
    return bytes(frame)


class _Socket:
    def sendall(self, _data):
        pass


class _Connection:
    _command_socket = _Socket()

    def is_connected(self):
        return True

    class encoder:  # noqa: N801 - mirrors the real attribute name
        @staticmethod
        def encode_command(**_kwargs):
            return b"\x00" * 128


class _Logger:
    def info(self, *_a, **_k):
        pass

    def error(self, *_a, **_k):
        pass


def _save(ack: bytes) -> dict:
    helper = PositionDebugHelper.__new__(PositionDebugHelper)
    helper.connection = _Connection()
    helper.logger = _Logger()
    helper._receive_full_bytes = lambda *_a, **_k: ack
    return helper.debug_save_settings(b"settings-bytes")


class TestTheAckIsActuallyChecked:
    def test_a_clean_acknowledgment_succeeds(self):
        assert _save(_ack())["success"] is True

    def test_a_nonzero_status_is_a_failure(self):
        # The field is documented as "Status/error code". Reporting a rejected
        # save as a success is the worst outcome here: the user believes the
        # scope now carries the calibrated magnification when it does not.
        result = _save(_ack(status=7))
        assert result["success"] is False
        assert "status 7" in result["error"]
        assert "may NOT have been saved" in result["error"]

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

    def test_the_failure_message_always_carries_the_raw_fields(self):
        # Whoever reads this next needs the numbers, not an adjective — if this
        # scope uses a non-zero status to mean something benign, the value has
        # to be visible to say so.
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
