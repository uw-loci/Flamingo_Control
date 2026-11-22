"""Only the background reader reads the command socket (one reader).

Until 2026-11-22 several paths called ``recv`` on the command socket while
the reader was reading it too, so whichever thread got a frame first had it:

* ``_send_workflow_stop_command`` read its own ack: when the reader won, the
  stop "timed out", and the timeout put the connection in the ERROR state.
* ``position_debug`` (the Connection tab's debug query and settings save,
  and the Pixel Calibrator's "push magnification") read acks on the raw
  socket without pausing the reader, and waited for an ack code (0x1008) the
  server never sends: a 3.0.0 server answers SCOPE_SETTINGS_SAVE with
  COMMON_SCOPE_SETTINGS (ControlServer::saveScopeSettings).
* The 3.0.0 settings read paused the reader and read the text itself; the
  pause raced a read already in progress, so the second settings read of a
  session failed ("Text response too small: 0 bytes").

Every one of these now waits for its reply through the reader. The command
socket is wrapped here so that any ``recv`` from another thread is caught.
"""

from __future__ import annotations

import threading

from py2flamingo.controllers.position_debug import PositionDebugHelper
from py2flamingo.models.command import Command
from py2flamingo.services.stage_service import StageService
from tests.synthetic_scope_case import SyntheticScopeCase, wait_until


class RecvWatch:
    """A socket proxy that records which threads call recv on it."""

    def __init__(self, sock):
        self._sock = sock
        self.readers = []

    def recv(self, *args, **kwargs):
        self.readers.append(threading.current_thread().name)
        return self._sock.recv(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._sock, name)


class OneReaderCase(SyntheticScopeCase):
    def setUp(self):
        super().setUp()
        # Every reference the client code reads the socket through, except
        # the reader's own.
        tcp = self.connection.tcp_connection
        self.watch = RecvWatch(tcp._command_socket)
        tcp._command_socket = self.watch
        self.connection._command_socket = self.watch
        tcp._command_client._socket = self.watch  # the sender's reference

    def assert_nobody_else_read(self):
        self.assertEqual(self.watch.readers, [])


class TestWorkflowStopAck(OneReaderCase):
    def test_the_stop_ack_comes_through_the_reader(self):
        for _ in range(5):
            response = self.connection.send_command(Command(code=0x3005), timeout=5)
            self.assertEqual(int.from_bytes(response[4:8], "little"), 0x3005)
            self.assertEqual(int.from_bytes(response[8:12], "little"), 1)
        self.assertTrue(self.connection.is_connected())
        self.assert_nobody_else_read()


class TestSettingsRead(OneReaderCase):
    def test_the_settings_can_be_read_again_and_again(self):
        for _ in range(3):
            settings = self.connection.get_microscope_settings()
            self.assertEqual(settings["Type"]["Microscope name"], "synthetic")
        self.assert_nobody_else_read()


class TestDebugHelper(OneReaderCase):
    def test_a_debug_query_is_answered(self):
        result = PositionDebugHelper(self.connection).debug_query_command(
            0xA007, "SYSTEM_STATE_GET"
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(result["parsed"]["command_code"], 0xA007)
        self.assert_nobody_else_read()

    def test_a_settings_save_is_acknowledged_and_the_stream_stays_in_sync(self):
        data = b"<Type>\n  Objective lens magnification = 16.3\n</Type>\n"
        result = PositionDebugHelper(self.connection).debug_save_settings(data)
        self.assertTrue(result["success"], result)
        self.assertTrue(wait_until(lambda: self.scope.saved_scope_settings))
        self.assertEqual(self.scope.saved_scope_settings[-1], data)
        # The echoed file was consumed whole: the next reply is still found.
        self.assertEqual(StageService(self.connection).get_axis_position(2), 12.0)
        self.assert_nobody_else_read()

    def test_a_refused_settings_file_is_reported(self):
        result = PositionDebugHelper(self.connection).debug_save_settings(b"junk")
        self.assertFalse(result["success"])
        self.assertIn("invalid scope settings file", result["error"])
