"""Every command the client sends keeps its exact bytes on the wire.

A characterization test: the client is driven through one of each command it
sends (connect and settings read, position reads, a move, HALT, laser, LED,
camera, workflow start and stop, drives) against the synthetic microscope,
which records every byte it receives. Those bytes are compared with
``tests/fixtures/wire_bytes_golden.json``, recorded from the client before the
command socket was given one writer, one reader and one code table
(2026-11-22). A refactor of the send path that changes any byte fails here.

To re-record after an intended change of what goes on the wire:

    FLAMINGO_WRITE_WIRE_GOLDEN=1 QT_QPA_PLATFORM=offscreen .venv/bin/python \
        -m pytest tests/test_wire_bytes_unchanged.py -q

and say in the commit which command changed and why.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import py2flamingo
from py2flamingo.core import server_layout as sl
from py2flamingo.core.tcp_connection import TCPConnection
from py2flamingo.core.tcp_protocol import ProtocolEncoder
from py2flamingo.models.command import Command, WorkflowCommand
from py2flamingo.models.connection import ConnectionConfig
from py2flamingo.services import safe_state, stage_command_dedup
from py2flamingo.services.camera_service import CameraService
from py2flamingo.services.connection_service import MVCConnectionService
from py2flamingo.services.laser_led_service import LaserLEDService
from py2flamingo.services.stage_service import StageService
from py2flamingo.testing import synthetic_microscope as synth
from py2flamingo.verification import context as vctx
from tests.synthetic_scope_case import adjacent_ports

GOLDEN = Path(__file__).parent / "fixtures" / "wire_bytes_golden.json"
WRITE = os.environ.get("FLAMINGO_WRITE_WIRE_GOLDEN") == "1"

WORKFLOW_TEXT = (
    b"<Workflow Settings>\r\n"
    b"  <Experiment Settings>\r\n"
    b"    Plane spacing (um) = 2.5\r\n"
    b"    Frame rate (f/s) = 40\r\n"
    b"    Exposure time (us) = 10000\r\n"
    b"    Save image drive = /media/deploy/drive1\r\n"
    b"    Save image directory = wire\r\n"
    b"    Save image data = NotSaved\r\n"
    b"  </Experiment Settings>\r\n"
    b"  <Stack Settings>\r\n"
    b"    Stack option = ZStack\r\n"
    b"    Number of planes = 3\r\n"
    b"  </Stack Settings>\r\n"
    b"</Workflow Settings>\r\n"
)


class RecordingScope(synth.SyntheticMicroscope):
    """Records every chunk read from a client: each 128-byte frame and any
    additional data that followed it, in the order received."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.chunks = []
        self._chunks_lock = threading.Lock()

    def _recv_exactly(self, client, count):
        data = super()._recv_exactly(client, count)
        if data is not None:
            with self._chunks_lock:
                self.chunks.append(data.hex())
        return data

    def take(self):
        with self._chunks_lock:
            out, self.chunks = self.chunks, []
        return out


def _settle(scope: RecordingScope, quiet: float = 0.25, limit: float = 5.0):
    """Wait until the scope has received nothing new for ``quiet`` s."""
    end = time.monotonic() + limit
    seen = len(scope.chunks)
    still_since = time.monotonic()
    while time.monotonic() < end:
        time.sleep(0.02)
        n = len(scope.chunks)
        if n != seen:
            seen, still_since = n, time.monotonic()
        elif time.monotonic() - still_since >= quiet:
            return


class WireBytesCase(unittest.TestCase):
    layout = sl.REFERENCE

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, cwd)
        self.addCleanup(sl.clear_active)
        Path("microscope_settings").mkdir()
        Path("microscope_settings/ControlSettings.txt").write_text(
            "<Laser parameters>\n"
            "  Laser 1 wave length (nm) = 488\n  Laser 1 max power (mw) = 20\n"
            "  Laser 2 wave length (nm) = 561\n  Laser 2 max power (mw) = 20\n"
            "  Laser lines = 2\n</Laser parameters>\n<Attached devices>\n"
            "  Attached laser line 1 = 1\n  Attached laser line 2 = 1\n"
            "  Attached LED = 1\n</Attached devices>\n"
        )
        stage_command_dedup.reset()
        self.addCleanup(stage_command_dedup.reset)
        # The client sends its version and the time on connect; pin both.
        fixed = time.struct_time((2026, 1, 1, 0, 0, 0, 3, 1, 0))
        patcher = mock.patch.object(sl.time, "localtime", lambda *a: fixed)
        patcher.start()
        self.addCleanup(patcher.stop)
        version = py2flamingo.__version__
        py2flamingo.__version__ = "0.0.0"
        self.addCleanup(setattr, py2flamingo, "__version__", version)
        port = adjacent_ports()
        self.scope = RecordingScope(
            command_port=port, live_port=port + 1, layout=self.layout
        )
        self.scope.start()
        self.addCleanup(self.scope.stop)
        self.port = port
        self.connection = MVCConnectionService(TCPConnection(), ProtocolEncoder())
        self.addCleanup(
            lambda: self.connection.is_connected() and self.connection.disconnect()
        )
        self.steps = []

    def step(self, name, action):
        """Run ``action`` and keep the bytes it put on the wire.

        Whether the action succeeded is not part of the comparison: some of
        these replies are raced for (the workflow-stop ack, the debug reads),
        and the bytes sent are the same either way."""
        self.scope.take()
        try:
            action()
        except Exception:  # noqa: BLE001 - the bytes are what is tested
            pass
        _settle(self.scope)
        self.steps.append((name, self.scope.take()))

    def exchange(self, name, **kw):
        tap = vctx.Tap()
        tap.install(self.connection)
        try:
            ctx = vctx.RunContext(
                connection=self.connection,
                services={},
                tap=tap,
                options=vctx.RunOptions(),
                abort=threading.Event(),
            )
            ctx.exchange(name, **kw)
        finally:
            tap.remove()

    def check(self, key):
        recorded = {name: chunks for name, chunks in self.steps}
        golden = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
        if WRITE:
            golden[key] = recorded
            GOLDEN.parent.mkdir(exist_ok=True)
            GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")
            return
        self.assertIn(key, golden, "no golden recording; see the module docstring")
        for name, chunks in golden[key].items():
            self.assertEqual(
                recorded.get(name), chunks, f"bytes sent for '{name}' changed"
            )
        self.assertEqual(sorted(recorded), sorted(golden[key]))


class TestReferenceLayout(WireBytesCase):
    def test_every_command_keeps_its_bytes(self):
        c = self.connection
        self.step(
            "connect",
            lambda: c.connect(ConnectionConfig("127.0.0.1", self.port, self.port + 1)),
        )
        self.step("settings read", c.get_microscope_settings)
        stage = StageService(c)
        self.step("position get x4", stage.get_position)
        self.step("move", lambda: stage.move_to_position(1, 6.5))
        self.step("halt", lambda: safe_state.halt_all_axes(c))
        lasers = LaserLEDService(c, SimpleNamespace(base_path=Path(".")))
        self.step("laser level set", lambda: lasers.set_laser_power(1, 12.5))
        self.step("laser level get", lambda: lasers.query_laser_power(1))
        self.step("laser preview enable", lambda: lasers.enable_laser_preview(1))
        self.step("laser disable all", lasers.disable_all_lasers)
        self.step("led set", lambda: lasers.set_led_intensity(0, 30.0))
        self.step("led enable", lasers.enable_led_preview)
        self.step("led disable", lasers.disable_led_preview)
        camera = CameraService(c)
        self.step(
            "exposure set",
            lambda: self.exchange(
                "CAMERA_EXPOSURE_SET", int0=12, reply="CAMERA_EXPOSURE_GET"
            ),
        )
        self.step("exposure get", camera.get_exposure)
        self.step("roi set", lambda: camera.set_centered_aoi(1024, 512))
        self.step("snapshot", camera.take_snapshot)
        self.step("live view start", camera.start_live_view)
        self.step("live view stop", camera.stop_live_view)
        self.step("system state", c.query_system_state)
        self.step("drives query", c.query_available_drives)
        from py2flamingo.controllers.position_debug import PositionDebugHelper

        helper = PositionDebugHelper(c)
        self.step(
            "debug query", lambda: helper.debug_query_command(0xA007, "STATE_GET")
        )
        self.step(
            "debug save settings",
            lambda: helper.debug_save_settings(b"<Type>\n</Type>\n"),
        )
        self.step(
            "workflow start",
            lambda: c.send_command(
                WorkflowCommand(code=0x3004, workflow_data=WORKFLOW_TEXT)
            ),
        )
        self.step("workflow stop", lambda: c.send_command(Command(code=0x3005)))
        self.step("safe state", lambda: safe_state.safe_state(c, "test", halt=True))
        self.check("3.0.0")


class TestLayout20261113(WireBytesCase):
    layout = sl.LAYOUT_2026_11_13

    def test_document_read_and_binning_keep_their_bytes(self):
        c = self.connection
        self.step(
            "connect",
            lambda: c.connect(ConnectionConfig("127.0.0.1", self.port, self.port + 1)),
        )
        self.step("settings read", c.get_microscope_settings)
        self.step("binning get", lambda: self.exchange("CAMERA_BINNING_GET"))
        self.step(
            "binning set",
            lambda: self.exchange(
                "CAMERA_BINNING_SET", int0=1, int1=1, reply="CAMERA_BINNING_SET"
            ),
        )
        self.step("drives query", c.query_available_drives)
        self.check("2026-11-13")


if __name__ == "__main__":
    unittest.main()
