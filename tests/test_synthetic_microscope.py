"""
The synthetic microscope has to be a faithful peer, or it hides faults.

These tests are about the properties that make it usable as a stand-in: it
holds frame sync, a move takes time, HALT and HOME stay distinct, and the
command history is trustworthy enough to assert on.
"""

import socket
import time
import unittest

import numpy as np

from py2flamingo.core.command_codes import (
    CameraCommands,
    LEDCommands,
    StageCommands,
    SystemCommands,
)
from py2flamingo.core.tcp_protocol import ProtocolDecoder, ProtocolEncoder
from py2flamingo.testing.synthetic_microscope import (
    AXIS_X,
    AXIS_Z,
    HOST,
    SyntheticMicroscope,
)


class SyntheticScopeTestCase(unittest.TestCase):
    """Starts a scope on an ephemeral port and talks to it as the client does."""

    def setUp(self):
        self.scope = SyntheticMicroscope(command_port=0, live_port=0)
        self.scope.start()
        self.addCleanup(self.scope.stop)

        self.encoder = ProtocolEncoder()
        self.decoder = ProtocolDecoder()
        self.sock = socket.create_connection((HOST, self.scope.command_port), timeout=5)
        self.sock.settimeout(5)
        self.addCleanup(self.sock.close)

    def send(self, code, params=None, value=0.0, data=b"", additional=b""):
        """Send one command the way the client does, and read the reply."""
        packet = self.encoder.encode_command(
            code=code,
            params=params or [0, 0, 0, 0, 0, 0, 0],
            value=value,
            data=data,
            additional_data_size=len(additional),
        )
        self.sock.sendall(packet)
        if additional:
            self.sock.sendall(additional)
        return self.decoder.decode_command(self._read_exactly(128))

    def _read_exactly(self, count):
        chunks = b""
        while len(chunks) < count:
            chunk = self.sock.recv(count - len(chunks))
            self.assertTrue(chunk, "scope closed the connection mid-message")
            chunks += chunk
        return chunks


class TestItHoldsFrameSync(SyntheticScopeTestCase):
    """Every reply is exactly 128 bytes, which is what the reader requires."""

    def test_reply_is_exactly_128_bytes(self):
        self.sock.sendall(self.encoder.encode_command(code=SystemCommands.STATE_GET))
        reply = self._read_exactly(128)
        self.assertEqual(len(reply), 128)
        self.assertTrue(self.decoder.decode_command(reply)["valid"])

    def test_many_commands_stay_in_sync(self):
        """Ten commands in a row each yield one aligned, valid reply.

        This is the property the old 4-byte "OKAY" mock could not provide: a
        short reply leaves the reader straddling message boundaries, and every
        later frame decodes as garbage.
        """
        for _ in range(10):
            decoded = self.send(SystemCommands.STATE_GET)
            self.assertTrue(decoded["valid"])
            self.assertEqual(decoded["code"], SystemCommands.STATE_GET)

    def test_a_malformed_frame_does_not_kill_the_session(self):
        """Garbage in does not end the connection; the next good frame works."""
        self.sock.sendall(b"\x00" * 128)
        decoded = self.send(SystemCommands.STATE_GET)
        self.assertTrue(decoded["valid"])


class TestMotionTakesTime(SyntheticScopeTestCase):
    """An ack is not an arrival."""

    def test_the_ack_arrives_before_the_axis_does(self):
        start = self.scope.stage_position()["x"]
        target = start + 3.0
        self.send(
            StageCommands.POSITION_SET_MOVE,
            params=[0, 0, 0, AXIS_X, 0, 0, 0],
            value=target,
        )
        # Acked already, but the axis is still in transit.
        self.assertTrue(self.scope.stage.moving)
        self.assertLess(self.scope.stage_position()["x"], target)

    def test_the_axis_arrives_eventually(self):
        self.send(
            StageCommands.POSITION_SET_MOVE,
            params=[0, 0, 0, AXIS_Z, 0, 0, 0],
            value=self.scope.stage_position()["z"] + 0.1,
        )
        deadline = time.monotonic() + 5.0
        while self.scope.stage.moving and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.scope.stage.moving, "axis never arrived")

    def test_state_reports_busy_while_moving(self):
        self.send(
            StageCommands.POSITION_SET_MOVE,
            params=[0, 0, 0, AXIS_X, 0, 0, 0],
            value=self.scope.stage_position()["x"] + 4.0,
        )
        decoded = self.send(SystemCommands.STATE_GET)
        self.assertEqual(decoded["value"], float(SystemCommands.STATE_VALUE_BUSY))

    def test_position_get_reports_the_queried_axis(self):
        self.scope.stage.position["z"] = 17.25
        decoded = self.send(
            StageCommands.POSITION_GET, params=[0, 0, 0, AXIS_Z, 0, 0, 0]
        )
        self.assertAlmostEqual(decoded["value"], 17.25, places=4)


class TestHaltIsNotHome(SyntheticScopeTestCase):
    """The two were once confused, and an emergency stop homed the stage."""

    def test_halt_stops_where_it_is(self):
        self.scope.stage.position["x"] = 6.0
        self.send(
            StageCommands.POSITION_SET_MOVE,
            params=[0, 0, 0, AXIS_X, 0, 0, 0],
            value=12.0,
        )
        self.send(StageCommands.HALT)
        self.assertFalse(self.scope.stage.moving)
        stopped_at = self.scope.stage_position()["x"]
        self.assertLess(stopped_at, 12.0, "HALT must not complete the move")
        self.assertGreaterEqual(stopped_at, 6.0)
        self.assertTrue(self.scope.stage.halted)

    def test_home_drives_to_the_reference_and_is_not_a_stop(self):
        self.send(StageCommands.HOME)
        self.assertTrue(self.scope.stage.moving, "HOME commands motion")
        self.assertFalse(self.scope.stage.halted, "HOME is not a halt")

    def test_the_history_distinguishes_them(self):
        self.send(StageCommands.HOME)
        self.send(StageCommands.HALT)
        self.assertEqual(len(self.scope.received(StageCommands.HALT)), 1)
        self.assertEqual(len(self.scope.received(StageCommands.HOME)), 1)
        self.assertNotEqual(StageCommands.HALT, StageCommands.HOME)


class TestTheHistoryIsTheOracle(SyntheticScopeTestCase):
    """What the scope was asked to do, not what the client believes."""

    def test_it_records_code_axis_and_value(self):
        self.send(
            StageCommands.POSITION_SET_MOVE,
            params=[0, 0, 0, AXIS_Z, 0, 0, 0],
            value=19.5,
        )
        moves = self.scope.received(StageCommands.POSITION_SET_MOVE)
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0].axis, AXIS_Z)
        self.assertAlmostEqual(moves[0].value, 19.5, places=4)

    def test_clear_received_scopes_an_assertion(self):
        self.send(SystemCommands.STATE_GET)
        self.scope.clear_received()
        self.assertEqual(self.scope.received(), [])
        self.send(SystemCommands.STATE_GET)
        self.assertEqual(len(self.scope.received()), 1)

    def test_an_unknown_command_is_still_acked_and_recorded(self):
        """Unmodelled commands must not stall the client or vanish."""
        decoded = self.send(0x7FFF)
        self.assertTrue(decoded["valid"])
        self.assertEqual(len(self.scope.received(0x7FFF)), 1)


class TestWorkflowUpload(SyntheticScopeTestCase):
    """Two-part messages: a 128-byte command then the workflow text."""

    def test_the_workflow_text_arrives_intact(self):
        text = "<Workflow Settings>\nfoo = bar\n</Workflow Settings>\n"
        self.send(CameraCommands.WORKFLOW_START, additional=text.encode("utf-8"))
        self.assertEqual(self.scope.workflows, [text])
        self.assertTrue(self.scope.workflow_running)

    def test_additional_data_is_kept_on_the_record(self):
        text = "x" * 300
        self.send(CameraCommands.WORKFLOW_START, additional=text.encode("utf-8"))
        starts = self.scope.received(CameraCommands.WORKFLOW_START)
        self.assertEqual(starts[0].additional_data, text.encode("utf-8"))

    def test_sync_survives_a_two_part_message(self):
        """The reply after an upload must still be aligned.

        Reading the trailing text is where a peer loses sync, and the symptom
        shows up on the *next* command rather than this one.
        """
        self.send(CameraCommands.WORKFLOW_START, additional=b"body text here")
        decoded = self.send(SystemCommands.STATE_GET)
        self.assertTrue(decoded["valid"])
        self.assertEqual(decoded["code"], SystemCommands.STATE_GET)

    def test_stop_clears_the_running_flag(self):
        self.send(CameraCommands.WORKFLOW_START, additional=b"body")
        self.send(CameraCommands.WORKFLOW_STOP)
        self.assertFalse(self.scope.workflow_running)


class TestCameraAndLed(SyntheticScopeTestCase):
    def test_exposure_round_trips_through_int32data0(self):
        self.send(CameraCommands.EXPOSURE_SET, params=[0, 0, 0, 25_000, 0, 0, 0])
        self.assertEqual(self.scope.exposure_us, 25_000)
        decoded = self.send(CameraCommands.EXPOSURE_GET)
        self.assertEqual(decoded["params"][3], 25_000)

    def test_image_size_reports_rows_and_columns(self):
        decoded = self.send(CameraCommands.IMAGE_SIZE_GET)
        self.assertEqual(
            (decoded["params"][3], decoded["params"][4]), self.scope.image_shape
        )

    def test_led_is_a_percentage_not_a_scaled_level(self):
        """27 means 27%. It used to be a scaled 16-bit level."""
        self.send(LEDCommands.SET_VALUE, params=[0, 0, 0, 0, 27, 0, 0])
        self.assertEqual(self.scope.led_percent, 27.0)


class TestLiveView(SyntheticScopeTestCase):
    """Frames in the server's format, of a sample the LED lights."""

    def _live_socket(self):
        live = socket.create_connection((HOST, self.scope.live_port), timeout=5)
        self.addCleanup(live.close)
        return live

    @staticmethod
    def _frame(live):
        from py2flamingo.services.camera_service import ImageHeader

        def exactly(n):
            buf = b""
            while len(buf) < n:
                chunk = live.recv(n - len(buf))
                if not chunk:
                    raise AssertionError("live socket closed")
                buf += chunk
            return buf

        header = ImageHeader.from_bytes(exactly(40))
        assert header.is_plausible(), header
        pixels = np.frombuffer(exactly(header.image_size), dtype="<u2")
        return header, pixels.reshape(header.image_height, header.image_width)

    def test_frames_arrive_in_the_client_format_and_count_up(self):
        self.scope.image_shape = (256, 320)
        live = self._live_socket()
        self.send(CameraCommands.LIVE_VIEW_START)
        first, image = self._frame(live)
        second, _ = self._frame(live)
        self.assertEqual(image.shape, (256, 320))
        self.assertGreater(second.frame_number, first.frame_number)

    def test_nothing_is_sent_without_live_view(self):
        live = self._live_socket()
        live.settimeout(0.4)
        with self.assertRaises(socket.timeout):
            live.recv(1)

    def test_the_sample_shows_only_with_the_led_on(self):
        from py2flamingo.testing import synthetic_microscope as synth

        self.scope.image_shape = (256, 256)
        self.scope.stage.position.update(
            x=synth.SAMPLE_CENTER_MM[0],
            y=synth.SAMPLE_CENTER_MM[1],
            z=synth.SAMPLE_FOCUS_Z_MM,
        )
        dark = self.scope.frame_now()
        self.send(LEDCommands.SET_VALUE, params=[0, 0, 0, 0, 50, 0, 0])
        self.send(LEDCommands.ENABLE)
        lit = self.scope.frame_now()
        self.assertLess(dark.mean(), 200)
        self.assertGreater(lit.mean(), 600)

    def test_focus_sharpens_the_texture(self):
        from py2flamingo.testing import synthetic_microscope as synth

        cx, cy = synth.SAMPLE_CENTER_MM
        focused = synth.render_frame(cx, cy, synth.SAMPLE_FOCUS_Z_MM, 1.0, (256, 256))
        blurred = synth.render_frame(
            cx, cy, synth.SAMPLE_FOCUS_Z_MM + 2.0, 1.0, (256, 256)
        )
        self.assertGreater(focused.std(), 3 * blurred.std())


class TestItIsLoopbackOnly(unittest.TestCase):
    """The one property that matters most: it is not reachable as an instrument."""

    def test_host_is_loopback_and_not_configurable(self):
        self.assertEqual(HOST, "127.0.0.1")
        with SyntheticMicroscope(command_port=0, live_port=0) as scope:
            bound_host = scope._command_sock.getsockname()[0]
            self.assertEqual(bound_host, "127.0.0.1")

    def test_constructor_takes_no_host(self):
        """A host parameter is the beginning of pointing this at a real scope."""
        import inspect

        signature = inspect.signature(SyntheticMicroscope.__init__)
        self.assertNotIn("host", signature.parameters)


if __name__ == "__main__":
    unittest.main()
