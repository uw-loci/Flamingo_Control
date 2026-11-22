"""Replies reach the request that asked for them.

Until 2026-11-22 the dispatcher kept ONE pending slot per command code: a
second request for the same code overwrote the first ("Overwriting pending
request"), and the first's cleanup then removed the second's slot, so one of
them always timed out. Two threads reading the X and Y positions could also
be handed each other's reply. Now each code has a FIFO of requests, and a
request may carry a predicate (POSITION_GET takes only the reply that echoes
its axis in int32Data0, as the server does: PIStageBase::positionGet).

Also here: ``send_command(cmd, timeout)`` honors its timeout (it was fixed at
3 s), and an automatic reconnect resends only queries, never a move, an
enable or a workflow start.
"""

from __future__ import annotations

import struct
import threading
import time
import unittest

from py2flamingo.core.frames import END_MARKER, START_MARKER, parse_frame
from py2flamingo.core.socket_reader import MessageDispatcher
from py2flamingo.core.tcp_protocol import ProtocolEncoder
from py2flamingo.models.command import Command
from py2flamingo.services.microscope_command_service import (
    RESEND_AFTER_RECONNECT,
    MicroscopeCommandService,
)
from py2flamingo.services.stage_service import StageService
from tests.synthetic_scope_case import SyntheticScopeCase


def _reply(code, int0=0, value=0.0):
    return parse_frame(
        struct.pack(
            "<IIIIIIiiiIdI72sI",
            START_MARKER,
            code,
            1,
            0,
            0,
            0,
            int0,
            0,
            0,
            0,
            value,
            0,
            b"",
            END_MARKER,
        )
    )


class TestDispatcherFifo(unittest.TestCase):
    def test_two_requests_for_one_code_both_get_a_reply(self):
        d = MessageDispatcher()
        first = d.register_pending_request(0x300A)
        second = d.register_pending_request(0x300A)
        d.dispatch(_reply(0x300A, int0=1))
        d.dispatch(_reply(0x300A, int0=2))
        self.assertEqual(first.get(timeout=0.1).int32_data0, 1)
        self.assertEqual(second.get(timeout=0.1).int32_data0, 2)

    def test_one_requests_cleanup_leaves_the_other_waiting(self):
        d = MessageDispatcher()
        first = d.register_pending_request(0x300A)
        second = d.register_pending_request(0x300A)
        d.unregister_pending_request(first)  # first timed out
        self.assertEqual(d.pending_count(0x300A), 1)
        d.dispatch(_reply(0x300A, int0=7))
        self.assertEqual(second.get(timeout=0.1).int32_data0, 7)
        self.assertEqual(d.pending_count(0x300A), 0)

    def test_a_predicate_picks_the_reply_for_its_axis(self):
        d = MessageDispatcher()
        x = d.register_pending_request(0x6008, lambda m: m.int32_data0 == 1)
        y = d.register_pending_request(0x6008, lambda m: m.int32_data0 == 2)
        d.dispatch(_reply(0x6008, int0=2, value=12.0))  # Y answers first
        d.dispatch(_reply(0x6008, int0=1, value=6.0))
        self.assertEqual(x.get(timeout=0.1).value, 6.0)
        self.assertEqual(y.get(timeout=0.1).value, 12.0)

    def test_a_reply_nobody_accepts_goes_to_the_callbacks(self):
        d = MessageDispatcher()
        seen = []
        d.register_callback_handler(0x6008, seen.append)
        waiting = d.register_pending_request(0x6008, lambda m: m.int32_data0 == 1)
        d.dispatch(_reply(0x6008, int0=0))  # an unsolicited frame, no axis
        self.assertEqual(len(seen), 1)
        self.assertEqual(d.pending_count(0x6008), 1)
        d.unregister_pending_request(waiting)

    def test_one_request_may_wait_on_several_codes(self):
        d = MessageDispatcher()
        r = d.register_pending_request((0x1007, 0x1000))
        d.dispatch(_reply(0x1000))
        self.assertEqual(r.get(timeout=0.1).command_code, 0x1000)
        self.assertEqual(d.pending_count(0x1007), 0)


class TestConcurrentPositionReads(SyntheticScopeCase):
    def test_two_threads_reading_two_axes_get_their_own_positions(self):
        stage = StageService(self.connection)
        expected = {1: 6.0, 2: 12.0}
        results = {1: [], 2: []}

        def read(axis):
            for _ in range(15):
                results[axis].append(stage.get_axis_position(axis))

        threads = [threading.Thread(target=read, args=(a,)) for a in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        for axis, values in results.items():
            self.assertEqual(values, [expected[axis]] * 15, f"axis {axis}")


class TestSendCommandHonorsItsTimeout(SyntheticScopeCase):
    def test_a_short_timeout_is_used(self):
        # HALT with axis 0 is dropped by the server with no reply at all.
        cmd = Command(code=0x6003, parameters={"params": [0, 0, 0, 0, 0, 0, 0]})
        t0 = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.connection.send_command(cmd, timeout=0.3)
        self.assertLess(time.monotonic() - t0, 1.5)


class _BrokenOnce:
    """A connection (no background reader) whose first write fails and whose
    reconnect succeeds. The background-reader path never resends at all: a
    send error there is reported as a failure."""

    def __init__(self):
        self.encoder = ProtocolEncoder()
        self.sent = []
        self.has_async_reader = False
        self._command_socket = object()

    def is_connected(self):
        return True

    def reconnect_last(self):
        return True

    def send_raw_bytes(self, data, lock_wait=None):
        self.sent.append(parse_frame(data[:128]).command_code)
        if len(self.sent) == 1:
            raise BrokenPipeError("gone")


def _service(conn):
    service = MicroscopeCommandService(conn)
    service._receive_full_bytes = lambda sock, n, timeout=3.0: _reply(
        conn.sent[-1]
    ).raw_data
    return service


class TestReconnectResendsOnlyQueries(unittest.TestCase):
    def test_a_query_is_resent(self):
        conn = _BrokenOnce()
        result = _service(conn)._query_command(0x6008, "POS", [0] * 7)
        self.assertTrue(result["success"])
        self.assertEqual(conn.sent, [0x6008, 0x6008])

    def test_a_move_is_not_resent(self):
        conn = _BrokenOnce()
        result = _service(conn)._send_command(
            0x6004, "MOVE", [0, 0, 0, 1, 0, 0, 0], value=5.0
        )
        self.assertFalse(result["success"])
        self.assertIn("not resent", result["error"])
        self.assertEqual(conn.sent, [0x6004])

    def test_no_action_is_on_the_resend_list(self):
        for code in (0x3004, 0x6004, 0x6005, 0x2004, 0x2001, 0x4001, 0x4002, 0x3009):
            self.assertNotIn(code, RESEND_AFTER_RECONNECT)


if __name__ == "__main__":
    unittest.main()
