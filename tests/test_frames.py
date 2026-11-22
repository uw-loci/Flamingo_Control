"""One frame parser and one exact-length receive (core/frames.py).

Until 2026-11-22 the client had a parser per module, and they disagreed:
``tcp_protocol.ProtocolDecoder`` read every field unsigned, so an int32Data0
of -1 came back as 4294967295, while the background reader read it signed.
Every reader now goes through ``parse_frame``; these tests pin what it
returns and that the readers built on it agree.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
import unittest

from py2flamingo.core.frames import (
    END_MARKER,
    START_MARKER,
    frame_fields,
    parse_frame,
    recv_exactly,
)


def _frame(code=0x6008, status=1, ints=(0, 0, 0, -1, -2, 3), bits=0x80000000):
    return struct.pack(
        "<IIIiiiiiiIdI72sI",
        START_MARKER,
        code,
        status,
        *ints,
        bits,
        12.5,
        7,
        b"hello",
        END_MARKER,
    )


class TestParseFrame(unittest.TestCase):
    def test_data_fields_are_signed_and_bits_unsigned(self):
        msg = parse_frame(_frame())
        self.assertTrue(msg.is_valid)
        self.assertEqual(msg.command_code, 0x6008)
        self.assertEqual(
            (msg.int32_data0, msg.int32_data1, msg.int32_data2), (-1, -2, 3)
        )
        self.assertEqual(msg.cmd_data_bits, 0x80000000)
        self.assertEqual(msg.value, 12.5)
        self.assertEqual(msg.additional_data_size, 7)
        self.assertEqual(msg.data_field[:5], b"hello")

    def test_wrong_length_raises(self):
        with self.assertRaises(ValueError):
            parse_frame(b"\x00" * 127)

    def test_bad_markers_parse_but_are_not_valid(self):
        bad = b"\x00" * 128
        self.assertFalse(parse_frame(bad).is_valid)


class TestEveryReaderAgrees(unittest.TestCase):
    def test_protocol_decoder_reads_a_negative_as_negative(self):
        from py2flamingo.core.tcp_protocol import ProtocolDecoder

        decoded = ProtocolDecoder().decode_command(_frame())
        self.assertEqual(decoded["params"][3], -1)
        self.assertEqual(decoded["params"][6], 0x80000000)
        self.assertEqual(decoded["reserved"], 7)
        self.assertTrue(decoded["valid"])

    def test_the_service_dicts_match_the_reader(self):
        from py2flamingo.services.microscope_command_service import (
            MicroscopeCommandService,
        )
        from py2flamingo.services.motion_tracker import MotionTracker

        raw = _frame()
        expected = frame_fields(parse_frame(raw))
        service = MicroscopeCommandService(connection=None)
        self.assertEqual(service._parse_response(raw), expected)
        self.assertEqual(service._convert_parsed_message(parse_frame(raw)), expected)
        self.assertEqual(MotionTracker()._parse_response(raw), expected)

    def test_the_verification_tap_matches_the_reader(self):
        from py2flamingo.verification.context import decode_packet

        msg = decode_packet(_frame() + b"extra", "rx")
        self.assertEqual((msg.int0, msg.int1, msg.int2), (-1, -2, 3))
        self.assertEqual(msg.bits, 0x80000000)
        self.assertEqual(msg.additional, b"extra")


class TestRecvExactly(unittest.TestCase):
    def setUp(self):
        self.a, self.b = socket.socketpair()
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)

    def test_short_reads_are_joined(self):
        def send():
            for part in (b"ab", b"cd", b"ef"):
                self.b.sendall(part)
                time.sleep(0.02)

        threading.Thread(target=send, daemon=True).start()
        self.assertEqual(recv_exactly(self.a, 6, timeout=2.0), b"abcdef")

    def test_timeout_raises_and_restores_the_socket_timeout(self):
        self.a.settimeout(7.0)
        self.b.sendall(b"ab")
        with self.assertRaises(socket.timeout):
            recv_exactly(self.a, 4, timeout=0.2)
        self.assertEqual(self.a.gettimeout(), 7.0)

    def test_close_raises_connection_error(self):
        self.b.sendall(b"ab")
        self.b.close()
        with self.assertRaises(ConnectionError):
            recv_exactly(self.a, 4, timeout=1.0)


if __name__ == "__main__":
    unittest.main()
