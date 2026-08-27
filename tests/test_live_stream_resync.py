"""One malformed frame must not end the live stream.

2026-08-27 15:28:25, verbatim:

    First frame received! Size: 1024x1024, 2097152 bytes
    ERROR - cannot reshape array of size 3178545 into shape (2097152,6488170)
    Data receiver thread stopped (received 1 frames)

The stream ran 8 bytes short per frame. Frame 1 read correctly; every header
after it landed two words early, so `image_height` came out holding the previous
frame's `image_size` -- 2097152, which is exactly 1024x1024x2 -- and `image_width`
held two pixel values (106 and 99). The reshape raised, and `except Exception:
break` ended the receiver thread for good.

The consequences were the two symptoms chased across three sessions: live view
frozen on its first image (so no LED change could ever be visible), and an LED
2D Overview that collected nothing for 125 minutes.

Whether those 8 bytes are a longer header or a per-frame trailer cannot be told
apart from the stream, so nothing here assumes either. The reader recovers and
**logs the measured offset**, which is the number that settles it.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_live_stream_resync.py -q
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _header(width, height, frame_number=0):
    """A well-formed 40-byte header."""
    return struct.pack(
        "<10I",
        width * height * 2,  # image_size
        width,
        height,
        0,  # scale_min
        4095,  # scale_max
        1000 + frame_number,  # timestamp_ms
        frame_number,
        10000,  # exposure_us
        0,
        0,
    )


def _frame(width, height, frame_number=0, extra=0, fill=0x0063):
    """A frame, optionally with `extra` unaccounted bytes -- the rig's bug."""
    body = struct.pack("<H", fill) * (width * height)
    return _header(width, height, frame_number) + body + (b"\xaa" * extra)


class _FakeSocket:
    """Hands out a scripted byte stream in whatever sizes are asked for."""

    def __init__(self, data):
        self._data = bytes(data)
        self._pos = 0

    def recv(self, n):
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def gettimeout(self):
        return 5.0


@pytest.fixture
def service():
    from py2flamingo.services.camera_service import CameraService

    svc = CameraService.__new__(CameraService)  # no connection needed
    import logging

    svc.logger = logging.getLogger("test_camera_service")
    svc._rx_pushback = bytearray()
    return svc


# --------------------------------------------------------------------- #
# The header check itself
# --------------------------------------------------------------------- #


class TestAHeaderIsRecognisedOrRejected:
    def _parse(self, raw):
        from py2flamingo.services.camera_service import ImageHeader

        return ImageHeader.from_bytes(raw)

    def test_a_real_header_is_plausible(self):
        assert self._parse(_header(1024, 1024)).is_plausible()

    def test_the_rigs_misread_header_is_not(self):
        # image_height=2097152, image_width=6488170 -- the actual failure.
        raw = struct.pack("<10I", 0, 6488170, 2097152, 0, 0, 0, 0, 0, 0, 0)
        assert not self._parse(raw).is_plausible()

    def test_a_non_square_aoi_is_accepted(self):
        # 1024x2048 and 2048x1024 are as valid as a square AOI. The check keys
        # on image_size == w*h*2, which is shape-agnostic by construction.
        assert self._parse(_header(1024, 2048)).is_plausible()
        assert self._parse(_header(2048, 1024)).is_plausible()

    def test_a_size_that_contradicts_the_dimensions_is_rejected(self):
        # The check that actually does the work: three unrelated words almost
        # never satisfy image_size == width * height * 2.
        raw = struct.pack("<10I", 999, 1024, 1024, 0, 0, 0, 0, 0, 0, 0)
        assert not self._parse(raw).is_plausible()

    def test_image_data_does_not_masquerade_as_a_header(self):
        raw = struct.pack("<H", 106) + struct.pack("<H", 99) * 19
        assert not self._parse(raw).is_plausible()


# --------------------------------------------------------------------- #
# Recovery
# --------------------------------------------------------------------- #


class TestTheReaderResynchronises:
    def test_it_finds_the_next_header_past_stray_bytes(self, service):
        sock = _FakeSocket(b"\xaa" * 8 + _frame(1024, 1024, frame_number=7))
        header = service._resync_to_header(sock, b"")

        assert header is not None
        assert (header.image_width, header.image_height) == (1024, 1024)
        assert header.frame_number == 7

    def test_it_reports_how_many_bytes_it_skipped(self, service, caplog):
        # This number is the diagnosis: a steady 8 says each frame occupies 8
        # bytes more than this reader accounts for.
        import logging

        sock = _FakeSocket(b"\xaa" * 8 + _frame(1024, 1024))
        with caplog.at_level(logging.WARNING):
            service._resync_to_header(sock, b"")

        assert "skipping 8 byte" in caplog.text

    def test_the_recovered_frame_body_is_not_lost(self, service):
        # Bytes read past the header during the scan are pushed back, so the
        # frame we resynchronised onto is still readable in full.
        sock = _FakeSocket(b"\xaa" * 8 + _frame(64, 64))
        header = service._resync_to_header(sock, b"")
        body = service._receive_exact(sock, header.image_size)

        assert len(body) == 64 * 64 * 2

    def test_it_gives_up_rather_than_scanning_forever(self, service):
        from py2flamingo.services.camera_service import CameraService

        sock = _FakeSocket(b"\xaa" * (CameraService.MAX_RESYNC_BYTES * 2))
        assert service._resync_to_header(sock, b"") is None

    def test_a_closed_connection_ends_the_scan(self, service):
        assert service._resync_to_header(_FakeSocket(b""), b"") is None


class TestThePushbackBuffer:
    def test_pushed_back_bytes_are_served_before_the_socket(self, service):
        service._rx_pushback = bytearray(b"abc")
        assert service._receive_exact(_FakeSocket(b"def"), 6) == b"abcdef"

    def test_it_is_consumed_not_replayed(self, service):
        service._rx_pushback = bytearray(b"abc")
        sock = _FakeSocket(b"defghi")
        service._receive_exact(sock, 6)

        assert service._receive_exact(sock, 3) == b"ghi"


# --------------------------------------------------------------------- #
# End to end: the rig's stream through the real receiver loop
# --------------------------------------------------------------------- #


@pytest.fixture
def streaming_service(service):
    """A CameraService wired up enough to run `_data_receiver_loop`."""
    import threading
    from collections import deque

    service._streaming = True
    service._frame_buffer = deque(maxlen=100)
    service._frame_buffer_lock = threading.Lock()
    service._image_callback = None
    service._frame_times = []
    service._max_frame_history = 30
    service._dropped_frame_count = 0
    service._note_image_size = lambda w, h: None
    return service


def _run(service, stream):
    service._data_socket = _FakeSocket(stream)
    service._data_receiver_loop()
    return list(service._frame_buffer)


class TestTheRigsBrokenStreamKeepsFlowing:
    def _short_stream(self, n, width=64, height=64, extra=8):
        """n frames, each `extra` bytes longer than the reader accounts for."""
        return b"".join(
            _frame(width, height, frame_number=i, extra=extra) for i in range(n)
        )

    def test_it_survives_past_the_frame_that_used_to_kill_it(self, streaming_service):
        # Before: "Data receiver thread stopped (received 1 frames)".
        frames = _run(streaming_service, self._short_stream(5))
        assert len(frames) >= 4

    def test_every_recovered_frame_has_the_right_shape(self, streaming_service):
        frames = _run(streaming_service, self._short_stream(5))
        assert all(image.shape == (64, 64) for image, _ in frames)

    def test_a_non_square_aoi_survives_the_same_stream(self, streaming_service):
        # The shape the reader is about to meet at 1024x2048 / 2048x1024.
        frames = _run(streaming_service, self._short_stream(4, width=32, height=64))
        assert frames and all(image.shape == (64, 32) for image, _ in frames)

    def test_frame_numbers_keep_advancing(self, streaming_service):
        # Proof it is resynchronising onto real frames, not re-reading one.
        frames = _run(streaming_service, self._short_stream(5))
        numbers = [header.frame_number for _, header in frames]
        assert numbers == sorted(numbers) and len(set(numbers)) == len(numbers)

    def test_a_clean_stream_is_untouched(self, streaming_service):
        # No extra bytes: the resync path must never engage.
        frames = _run(streaming_service, self._short_stream(5, extra=0))
        assert len(frames) == 5

    def test_it_still_stops_when_the_connection_closes(self, streaming_service):
        # Recovery must not turn a closed socket into an infinite loop.
        frames = _run(streaming_service, self._short_stream(2))
        assert streaming_service._streaming  # loop exited on its own, not a flag
        assert len(frames) >= 1
