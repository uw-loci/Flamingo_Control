"""Every write on the command socket takes one lock (one writer).

Until 2026-11-22 the workflow header and its text were two ``sendall`` calls
with no lock, and fire-and-forget sends bypassed the reader's send lock, so
a frame from another thread (a position poll, a laser command) could land
between the header and the text the server counts as the workflow, or in
the middle of it. Here every write is slowed to small chunks so that any
interleaving would show, and a workflow is sent while another thread polls.
"""

from __future__ import annotations

import threading
import time

from py2flamingo.core.workflow_exposure import to_server_exposure
from py2flamingo.models.command import WorkflowCommand
from py2flamingo.services import safe_state
from py2flamingo.services.stage_service import StageService
from tests.synthetic_scope_case import SyntheticScopeCase, wait_until

WORKFLOW = (
    b"<Workflow Settings>\r\n  <Experiment Settings>\r\n"
    + b"".join(b"    Comment %04d = padding the payload\r\n" % i for i in range(120))
    + b"    Exposure time (us) = 10000\r\n  </Experiment Settings>\r\n"
    b"  <Stack Settings>\r\n    Stack option = ZStack\r\n"
    b"    Number of planes = 2\r\n  </Stack Settings>\r\n</Workflow Settings>\r\n"
)


class SlowSocket:
    """A socket whose sendall writes 16 bytes at a time, with a pause."""

    def __init__(self, sock):
        self._sock = sock

    def sendall(self, data):
        for i in range(0, len(data), 16):
            self._sock.sendall(data[i : i + 16])
            time.sleep(0.0005)

    def __getattr__(self, name):
        return getattr(self._sock, name)


class TestOneWriter(SyntheticScopeCase):
    def _slow_down_every_writer(self):
        tcp = self.connection.tcp_connection
        slow = SlowSocket(tcp._command_socket)
        tcp._command_socket = slow
        self.connection._command_socket = slow
        tcp._command_client._socket = slow

    def test_a_workflow_payload_is_never_split(self):
        self._slow_down_every_writer()
        stage = StageService(self.connection)
        stop = threading.Event()

        def poll():
            while not stop.is_set():
                stage.get_axis_position(1)
                safe_state.send_now(self.connection, 0x4003, 0, "LED off")

        pollers = [threading.Thread(target=poll, daemon=True) for _ in range(2)]
        for t in pollers:
            t.start()
        time.sleep(0.05)
        try:
            self.connection.send_command(
                WorkflowCommand(code=0x3004, workflow_data=WORKFLOW)
            )
        finally:
            stop.set()
            for t in pollers:
                t.join(timeout=5)
        self.assertTrue(wait_until(lambda: self.scope.received(0x3004)))
        starts = self.scope.received(0x3004)
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0].additional_data, to_server_exposure(WORKFLOW))
        # Nothing the scope could not read as a frame: every code it saw is
        # one the client sent.
        codes = {c.code for c in self.scope.received()}
        self.assertLessEqual(codes, {0x1002, 0x6008, 0x4003, 0x3004})

    def test_every_writer_shares_the_reader_lock(self):
        tcp = self.connection.tcp_connection
        self.assertIs(tcp._command_client._send_lock, tcp.send_lock)
        self.assertIs(safe_state._send_lock(self.connection), tcp.send_lock)

    def test_a_stop_does_not_wait_behind_a_held_lock(self):
        lock = self.connection.tcp_connection.send_lock
        lock.acquire()
        try:
            t0 = time.monotonic()
            self.assertTrue(safe_state.halt_all_axes(self.connection))
            self.assertLess(time.monotonic() - t0, safe_state.SEND_LOCK_WAIT_S + 0.5)
        finally:
            lock.release()
        self.assertTrue(wait_until(lambda: self.scope.received(0x6003)))
