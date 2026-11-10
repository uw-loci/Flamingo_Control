"""
A synthetic Flamingo microscope that speaks the real wire protocol.

This exists so the application can be driven end to end with no instrument
attached: the connection sequence, the 128-byte protocol, the async socket
reader and the command path all run for real, against a server that keeps
plausible stage and camera state.

Why not ``tests/mock_server.py``: that one replies with a 4-byte ``b"OKAY"``
while :class:`~py2flamingo.core.socket_reader.SocketReader` reads exactly
``MESSAGE_SIZE = 128`` bytes per message. A short reply desynchronizes the
reader, which is the same shape as the live-stream fault that once froze the
viewer after one frame. A mock that cannot hold frame sync cannot test the
code that has to hold frame sync, so this module encodes and decodes with
:class:`~py2flamingo.core.tcp_protocol.ProtocolEncoder` and
:class:`~py2flamingo.core.tcp_protocol.ProtocolDecoder` -- the same classes
the client uses, so the synthetic scope cannot drift from the real one.

**It binds loopback only.** ``host`` is not a parameter. Nothing here should
ever be reachable from another machine, and nothing here should ever be
mistaken for an instrument.

What it models, because these are the behaviours that have actually caused
faults:

* **Motion takes time.** ``POSITION_SET_MOVE`` returns immediately and the
  axis travels at a finite speed, so a caller that assumes a move is finished
  when the ack arrives sees the same stale position it would see on the rig.
* **Two codes command a move.** ``POSITION_SET_MOVE`` (0x6004) and
  ``POSITION_SET`` (0x6005) both move an axis here, because the application
  sends the second one from :mod:`py2flamingo.services.stage_service` while
  the command-code module documents the first as the client's.

* **HALT and HOME are different commands.** ``HALT`` (0x6003) stops motion
  where it is; ``HOME`` (0x6002) drives to the reference. They are recorded
  separately so a test can prove which one an emergency stop actually sent.
* **Server builds number commands differently.** ``layout`` picks the 3.0.0
  numbering (scope settings as ``SCOPE_SETTINGS_LOAD`` text) or the
  2026-10-06 one (settings in a metadata document, fourteen COMMON codes
  moved down by six). The scope answers ``UI_CONTROLLER_ID_GET`` with the
  matching version string, and on the newer layout the old codes reach the
  commands now at those numbers: 0x1013 sets ``sleep_minutes``, as it would
  on the rig.
* **Live view streams frames of a synthetic sample.** Between
  ``LIVE_VIEW_START`` and ``LIVE_VIEW_STOP`` the live port sends the real
  frame format (40-byte header, 16-bit pixels) rendered at the current stage
  position: a textured ellipse around the home position, bright only while
  the LED is enabled and sharp only near ``SAMPLE_FOCUS_Z_MM``. An overview
  scan therefore finds the sample, and a Z sweep finds its focus.
* **Every command is recorded.** :meth:`received` is the oracle. An ack is not
  an effect, so a test asserts on what the scope was asked to do, not on what
  the client believes it did.

Usage::

    scope = SyntheticMicroscope()
    scope.start()
    try:
        ...  # point the application at 127.0.0.1:scope.command_port
        assert scope.received(StageCommands.HALT)
    finally:
        scope.stop()
"""

import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from py2flamingo.core import server_layout as layouts
from py2flamingo.core.command_codes import (
    CameraCommands,
    LEDCommands,
    StageCommands,
    SystemCommands,
)
from py2flamingo.core.metadata_document import legacy_scope_settings_text, parse
from py2flamingo.core.tcp_protocol import ProtocolDecoder, ProtocolEncoder

logger = logging.getLogger(__name__)

#: Only ever loopback. See the module docstring.
HOST = "127.0.0.1"

#: Default ports, matching the real instrument's command and live-image ports.
DEFAULT_COMMAND_PORT = 53717
DEFAULT_LIVE_PORT = 53718

#: Axis indices as the protocol numbers them, in ``int32Data0`` (params[3]).
AXIS_X, AXIS_Y, AXIS_Z, AXIS_R = 1, 2, 3, 4
AXIS_NAMES = {AXIS_X: "x", AXIS_Y: "y", AXIS_Z: "z", AXIS_R: "r"}

#: How long an accept() waits before re-checking whether we are still running.
ACCEPT_POLL_S = 0.1

#: Travel speeds, deliberately slow enough that a move is observably in
#: progress. Real speeds depend on the stage and the commanded velocity; these
#: only have to make "the ack arrived but the axis has not got there" happen.
SPEED_MM_PER_S = 2.0
SPEED_DEG_PER_S = 45.0


#: Image pixel size the settings imply (6.5 um sensor / 6.205x), and the
#: synthetic sample in stage millimetres: an ellipse around the home position,
#: in focus at one depth.
PIXEL_SIZE_MM = 6.5e-3 / 6.205
SAMPLE_CENTER_MM = (6.0, 12.0)
SAMPLE_RADII_MM = (1.6, 1.2)
SAMPLE_FOCUS_Z_MM = 19.0
SAMPLE_DEPTH_OF_FIELD_MM = 0.4
LIVE_FPS = 10.0


def render_frame(
    x_mm: float, y_mm: float, z_mm: float, led_fraction: float, shape: Tuple[int, int]
) -> np.ndarray:
    """One 16-bit camera frame of the synthetic sample at this stage position.

    Rendered on a grid 8x coarser than the sensor and enlarged, which keeps a
    2048 x 2048 frame cheap enough to stream at 10 frames a second. Columns
    follow stage X and rows stage Y."""
    rows, cols = int(shape[0]), int(shape[1])
    step = 8
    r, c = max(1, rows // step), max(1, cols // step)
    span = PIXEL_SIZE_MM * step
    ys = y_mm + (np.arange(r) - r / 2 + 0.5) * span
    xs = x_mm + (np.arange(c) - c / 2 + 0.5) * span
    yy, xx = np.meshgrid(ys, xs, indexing="ij")
    (cx, cy), (rx, ry) = SAMPLE_CENTER_MM, SAMPLE_RADII_MM
    inside = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 <= 1.0
    focus = float(
        np.exp(-(((z_mm - SAMPLE_FOCUS_Z_MM) / SAMPLE_DEPTH_OF_FIELD_MM) ** 2))
    )
    texture = 0.5 + 0.5 * np.sin(xx * 40.0) * np.cos(yy * 35.0)
    signal = np.where(inside, 0.35 + 0.65 * (focus * texture + (1 - focus) * 0.5), 0.05)
    frame = 100.0 + led_fraction * 3000.0 * signal
    frame = np.repeat(np.repeat(frame, step, axis=0), step, axis=1)[:rows, :cols]
    if frame.shape != (rows, cols):
        frame = np.pad(
            frame, ((0, rows - frame.shape[0]), (0, cols - frame.shape[1])), mode="edge"
        )
    return frame.astype(np.uint16)


#: Travel limits reported in the settings (mm, and degrees for r). The
#: flamingo-mcp safety envelope uses the same x maximum.
STAGE_LIMITS = {
    "x": (1.0, 12.31),
    "y": (5.0, 25.0),
    "z": (12.5, 26.0),
    "r": (-720.0, 720.0),
}
STAGE_HOME = {"x": 6.0, "y": 12.0, "z": 19.0, "r": 0.0}


def metadata_document(scope_name: str, command_port: int) -> str:
    """A system metadata document like a 2026-10-06 server's, for this scope.

    Only the parts this client reads, in the server template's layout
    (MetaDataTemplate.cpp). The address is loopback, never an instrument's."""
    stages = "".join(
        f"        <Stage {axis}>\n"
        f"          Hard limit min = {lo:.3f}\n"
        f"          Hard limit max = {hi:.3f}\n"
        f"          Soft limit min = {lo:.3f}\n"
        f"          Soft limit max = {hi:.3f}\n"
        f"          Home = {STAGE_HOME[axis]:.3f}\n"
        f"          Unload = {lo:.3f}\n"
        f"          Default velocity = {50.0 if axis == 'r' else 1.0:.3f}\n"
        f"        </Stage {axis}>\n"
        for axis, (lo, hi) in STAGE_LIMITS.items()
    )
    lasers = "".join(
        f"          <Laser {i}>\n          Filter wheel position = {i - 1}\n"
        f"          </Laser {i}>\n"
        for i in range(1, 5)
    )
    wheel = "".join(
        f"          Filter wheel position {i} = {i * 500} {8 - i}\n"
        for i in range(1, 9)
    ) + "".join(
        f"          Filter wheel description {i} = filter {i}\n" for i in range(1, 9)
    )
    return (
        "<MetaData>\n  Version = 1.1\n  <Instrument>\n"
        "    <Microscope characteristics>\n"
        f"      Name = {scope_name}\n      Type = synthetic\n"
        f"      <Address>\n        IP = {HOST}\n        Port = {command_port}\n"
        "      </Address>\n"
        "      <Builders>\n        <Index 1>\n          Name = Flamingo_Control tests\n"
        "        </Index 1>\n      </Builders>\n"
        "    </Microscope characteristics>\n"
        "    <Electronic box hardware>\n      <Stage motors and controllers>\n"
        f"{stages}"
        "      </Stage motors and controllers>\n    </Electronic box hardware>\n"
        "    <Microscope Hardware>\n      <Illumination setting>\n"
        "        <Illumination source>\n"
        f"{lasers}"
        "          <LED setting>\n          Filter wheel position = 4\n"
        "          </LED setting>\n        </Illumination source>\n"
        "      </Illumination setting>\n      <Detection>\n        <Detection 1>\n"
        "          Tube lens focal length (mm) = 200\n"
        "          Tube lens length (mm) = 200\n"
        "          <Detection lens>\n            Magnification = 6.205\n"
        "          </Detection lens>\n          <Filter wheel 1>\n"
        f"{wheel}"
        "          </Filter wheel 1>\n        </Detection 1>\n      </Detection>\n"
        "    </Microscope Hardware>\n  </Instrument>\n</MetaData>\n"
    )


#: The sections a 3.0.0 server's ScopeSettings.txt has beyond the ones this
#: client reads; they bring the text to its real size (the client expects at
#: least 2000 bytes from SCOPE_SETTINGS_LOAD).
_LEGACY_EXTRA_SECTIONS = (
    "<Illumination settings>\n"
    + "".join(
        f"  {kind} {side} {ax}-axis = 0\n"
        for kind in ("Offset", "Amplitude")
        for side in ("left", "right")
        for ax in ("x", "y")
    )
    + "  Alignment center x-axis = 2000\n  Alignment center y-axis = -1\n"
    "  Sample scan frequency (Hz) = 5000\n</Illumination settings>\n"
    "<Camera overlap settings>\n"
    "  Camera 1 overlap percent = 100\n"
    "  Camera 1 capture mode (0: full, 1: from front, 2: from back) = 0\n"
    "  Camera 2 overlap percent = 100\n"
    "  Camera 2 capture mode (0: full, 1: from front, 2: from back) = 0\n"
    "</Camera overlap settings>\n"
    "<LED settings>\n  LED selection (0 red, 1 green, 2 blue, 3 white) = 0\n"
    "  Preview on = 0\n</LED settings>\n"
)


@dataclass
class ReceivedCommand:
    """One command the synthetic scope was asked to perform."""

    code: int
    params: Tuple[int, ...]
    value: float
    at: float
    #: Bytes that followed the 128-byte message, when the command carried any.
    additional_data: Optional[bytes] = None

    @property
    def axis(self) -> Optional[int]:
        """``int32Data0``, which carries the axis or laser index."""
        return self.params[3] if len(self.params) > 3 else None


@dataclass
class StageState:
    """Where each axis is, and where it is going."""

    position: Dict[str, float] = field(
        default_factory=lambda: {"x": 6.0, "y": 12.0, "z": 19.0, "r": 0.0}
    )
    target: Dict[str, float] = field(default_factory=dict)
    #: Wall-clock time each moving axis is expected to arrive.
    arrives_at: Dict[str, float] = field(default_factory=dict)
    halted: bool = False

    def settle(self, now: Optional[float] = None) -> None:
        """Advance every moving axis to where it should be by ``now``."""
        now = time.monotonic() if now is None else now
        for axis, target in list(self.target.items()):
            start_pos = self.position[axis]
            arrival = self.arrives_at.get(axis, now)
            started = self._started_at.get(axis, now)
            if now >= arrival:
                self.position[axis] = target
                self.target.pop(axis, None)
                self.arrives_at.pop(axis, None)
                self._started_at.pop(axis, None)
            elif arrival > started:
                fraction = (now - started) / (arrival - started)
                self.position[axis] = start_pos + (target - start_pos) * fraction
                self._started_at[axis] = now

    def start_move(self, axis: str, target: float) -> None:
        """Begin a move. Returns at once; the axis arrives later."""
        self.settle()
        self.halted = False
        distance = abs(target - self.position[axis])
        speed = SPEED_DEG_PER_S if axis == "r" else SPEED_MM_PER_S
        now = time.monotonic()
        self.target[axis] = target
        self._started_at[axis] = now
        self.arrives_at[axis] = now + (distance / speed if speed else 0.0)

    def halt(self) -> None:
        """Stop every axis where it currently is."""
        self.settle()
        self.target.clear()
        self.arrives_at.clear()
        self._started_at.clear()
        self.halted = True

    @property
    def moving(self) -> bool:
        self.settle()
        return bool(self.target)

    _started_at: Dict[str, float] = field(default_factory=dict)


class SyntheticMicroscope:
    """A loopback server that answers the Flamingo command protocol.

    Args:
        command_port: Command port to bind. 0 picks a free port, which is what
            concurrent tests should use; read it back from
            :attr:`command_port` once started.
        live_port: Live-image port to bind. 0 picks a free port. The
            application opens the live port at the command port + 1, as on
            the instrument (53717 / 53718), so a scope the app connects to
            needs ``live_port = command_port + 1``.
        scope_name: Reported in the settings, whichever form they take.
        layout: A :class:`~py2flamingo.core.server_layout.ServerLayout` to
            number commands by: ``REFERENCE`` (3.0.0, the default) or
            ``LAYOUT_2026_10_06``. Any other counts can be passed as a layout
            too, to play a build this client does not know.
    """

    def __init__(
        self,
        command_port: int = DEFAULT_COMMAND_PORT,
        live_port: int = DEFAULT_LIVE_PORT,
        scope_name: str = "synthetic",
        layout: Optional["layouts.ServerLayout"] = None,
    ) -> None:
        self._requested_command_port = command_port
        self._requested_live_port = live_port
        self.scope_name = scope_name
        self.layout = layout or layouts.REFERENCE
        #: Set when a client hits SYSTEM_SLEEP, which on the 2026-10-06 layout
        #: is the code the 3.0.0 "list storage drives" used to be.
        self.sleep_minutes: Optional[int] = None

        self.stage = StageState()
        self.system_state = SystemCommands.STATE_VALUE_IDLE
        self.exposure_us = 10_000
        self.image_shape = (2048, 2048)
        self.led_percent = 0.0
        self.led_enabled = False
        self.live_view = False
        #: Frames sent on the live port, all connections together.
        self.frames_sent = 0
        self.laser_levels: Dict[int, float] = {}
        self.workflow_running = False
        #: Workflow text the client uploaded, newest last.
        self.workflows: List[str] = []

        self._received: List[ReceivedCommand] = []
        self._lock = threading.Lock()
        self._encoder = ProtocolEncoder()
        self._decoder = ProtocolDecoder()

        self._running = False
        self._command_sock: Optional[socket.socket] = None
        self._live_sock: Optional[socket.socket] = None
        self._threads: List[threading.Thread] = []
        self.command_port = command_port
        self.live_port = live_port

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Bind both ports and begin serving. Returns once bound."""
        self._running = True
        self._command_sock = self._bind(self._requested_command_port)
        self.command_port = self._command_sock.getsockname()[1]
        self._live_sock = self._bind(self._requested_live_port)
        self.live_port = self._live_sock.getsockname()[1]

        for name, sock, handler in (
            ("command", self._command_sock, self._serve_commands),
            ("live", self._live_sock, self._serve_live),
        ):
            thread = threading.Thread(
                target=handler, args=(sock,), name=f"synthetic-{name}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

        logger.info(
            "Synthetic microscope on %s: command %d, live %d",
            HOST,
            self.command_port,
            self.live_port,
        )

    def stop(self) -> None:
        """Stop serving and close both ports."""
        self._running = False
        for sock in (self._command_sock, self._live_sock):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        with self._lock:
            threads = list(self._threads)
            self._threads.clear()
        for thread in threads:
            thread.join(timeout=2.0)

    def __enter__(self) -> "SyntheticMicroscope":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    @staticmethod
    def _bind(port: int) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, port))
        sock.listen(4)
        # Closing a listening socket does not reliably wake a thread blocked
        # in accept(), so the accept loops poll instead and check _running.
        sock.settimeout(ACCEPT_POLL_S)
        return sock

    # ------------------------------------------------------------- the oracle

    def received(self, code: Optional[int] = None) -> List[ReceivedCommand]:
        """Commands received, oldest first, optionally filtered by code.

        This is the oracle a test should assert on. An acknowledgement proves
        a packet was well formed, not that the right thing was asked for.
        """
        with self._lock:
            commands = list(self._received)
        if code is None:
            return commands
        return [c for c in commands if c.code == code]

    def clear_received(self) -> None:
        """Forget the command history, so a test can scope its assertions."""
        with self._lock:
            self._received.clear()

    def stage_position(self) -> Dict[str, float]:
        """Current position of every axis, advancing any move in progress."""
        self.stage.settle()
        return dict(self.stage.position)

    # ----------------------------------------------------------- command loop

    def _serve_commands(self, listener: socket.socket) -> None:
        while self._running:
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            thread = threading.Thread(
                target=self._handle_client, args=(client,), daemon=True
            )
            thread.start()
            with self._lock:
                self._threads = [t for t in self._threads if t.is_alive()]
                self._threads.append(thread)

    def _handle_client(self, client: socket.socket) -> None:
        client.settimeout(0.5)
        try:
            while self._running:
                message = self._recv_exactly(client, 128)
                if message is None:
                    return
                try:
                    decoded = self._decoder.decode_command(message)
                except Exception as exc:  # malformed frame
                    logger.warning("Synthetic scope rejected a frame: %s", exc)
                    continue
                self._dispatch(client, decoded)
        finally:
            try:
                client.close()
            except OSError:
                pass

    def _recv_exactly(self, client: socket.socket, count: int) -> Optional[bytes]:
        """Read exactly ``count`` bytes, or None if the peer went away.

        Short reads are joined rather than treated as a message, because that
        is what the client does and a synthetic peer that is sloppier than the
        client under test hides faults instead of finding them.
        """
        chunks = b""
        while len(chunks) < count and self._running:
            try:
                chunk = client.recv(count - len(chunks))
            except socket.timeout:
                continue
            except OSError:
                return None
            if not chunk:
                return None
            chunks += chunk
        return chunks if len(chunks) == count else None

    def _dispatch(self, client: socket.socket, decoded: Dict) -> None:
        code = decoded["code"]
        params = tuple(decoded.get("params") or ())
        value = decoded.get("value", 0.0)
        additional = None

        # ProtocolDecoder calls the addDataBytes field "reserved" (see
        # tcp_protocol.py:482 and its own [RX] log line, which reads
        # "Reserved Field (addDataBytes)"). It is the size of whatever follows
        # the 128-byte message, not a reserved field.
        size = decoded.get("reserved") or 0
        if size:
            additional = self._recv_exactly(client, size)

        with self._lock:
            self._received.append(
                ReceivedCommand(
                    code=code,
                    params=params,
                    value=value,
                    at=time.monotonic(),
                    additional_data=additional,
                )
            )

        handler = self._handlers().get(code)
        if handler is None:
            logger.debug("Synthetic scope: no behaviour for 0x%04X, acking", code)
            self._reply(client, code)
            return
        handler(client, code, params, value, additional)

    def version_string(self) -> str:
        """What this scope answers UI_CONTROLLER_ID_GET with."""
        return (
            f"ControlSystem - Oct  6 2026 - 14:03:00 - 3.0.0 - "
            f"{self.layout.counts_text}"
        )

    def _layout_handlers(self) -> Dict:
        """Handlers for the commands whose numbers depend on the layout."""
        named = {
            "COMMON_UI_CONTROLLER_ID_GET": self._on_controller_id,
            "COMMON_SUBSYSTEM_CONFIGURATION_GET": self._on_configuration_get,
            "COMMON_SCOPE_SETTINGS_LOAD": self._on_scope_settings_load,
            "COMMON_STORAGE_PATH_SET": self._on_storage_path,
            "COMMON_SYSTEM_SYSTEM_SLEEP": self._on_sleep,
        }
        return {
            self.layout.codes[name]: handler
            for name, handler in named.items()
            if name in self.layout.codes
        }

    def _handlers(self) -> Dict:
        return {
            **self._layout_handlers(),
            SystemCommands.STATE_GET: self._on_state_get,
            SystemCommands.STATE_IDLE: self._on_state_idle,
            # Both move codes. command_codes.py documents POSITION_SET_MOVE
            # (0x6004) as "the code the Python GUI uses", but
            # StageService.move_to_position sends POSITION_SET_SLIDER (0x6005)
            # from its own local enum, with the comment "Use slider variant
            # from logs" (stage_service.py:390). A synthetic scope that
            # modelled only the documented one would silently ack every real
            # move and move nothing, which is how this was found.
            StageCommands.POSITION_SET_MOVE: self._on_move,
            StageCommands.POSITION_SET: self._on_move,
            StageCommands.POSITION_GET: self._on_position_get,
            StageCommands.HALT: self._on_halt,
            StageCommands.HOME: self._on_home,
            CameraCommands.EXPOSURE_SET: self._on_exposure_set,
            CameraCommands.EXPOSURE_GET: self._on_exposure_get,
            CameraCommands.IMAGE_SIZE_GET: self._on_image_size_get,
            CameraCommands.WORKFLOW_START: self._on_workflow_start,
            CameraCommands.WORKFLOW_STOP: self._on_workflow_stop,
            LEDCommands.SET_VALUE: self._on_led_set,
            LEDCommands.ENABLE: self._on_led_enable,
            LEDCommands.DISABLE: self._on_led_disable,
            CameraCommands.LIVE_VIEW_START: self._on_live_start,
            CameraCommands.LIVE_VIEW_STOP: self._on_live_stop,
        }

    # -------------------------------------------------------------- behaviours

    def _reply(
        self,
        client: socket.socket,
        code: int,
        params: Optional[List[int]] = None,
        value: float = 0.0,
        data: bytes = b"",
        additional: bytes = b"",
    ) -> None:
        """Send one well-formed 128-byte reply, plus ``additional`` after it.

        Always 128 bytes, with addDataBytes giving the length of what follows.
        The client reads fixed-size messages, so a reply of any other length
        costs it frame sync. The layout guard is off: this is the server.
        """
        packet = self._encoder.encode_command(
            code=code,
            status=self.system_state,
            params=params or [0, 0, 0, 0, 0, 0, 0],
            value=value,
            data=data,
            additional_data_size=len(additional),
            check_server_layout=False,
        )
        assert len(packet) == 128, f"reply was {len(packet)} bytes, not 128"
        try:
            client.sendall(packet + additional)
        except OSError:
            pass

    def _on_controller_id(self, client, code, params, value, additional) -> None:
        # int32Data0 (params[3]) carries the server's verdict on the client's
        # version; 1 = failed. Ours never matches, as on the rig.
        self._reply(
            client,
            code,
            params=[0, 0, 0, 1, 0, 0, 0],
            additional=self.version_string().encode("utf-8"),
        )

    def _on_configuration_get(self, client, code, params, value, additional) -> None:
        """2026-10-06 on: the document, then other settings frames after it,
        as ControlServer::getMicroscopeConfiguration sends them."""
        if self.layout.has_metadata_document:
            doc = metadata_document(self.scope_name, self.command_port)
            self._reply(
                client,
                self.layout.codes["META_DATA_DOCUMENT"],
                additional=doc.encode("utf-8"),
            )
        # Stands in for the camera/stage/laser settings that follow.
        self._reply(client, SystemCommands.STATE_GET)

    def _on_scope_settings_load(self, client, code, params, value, additional) -> None:
        text, _ = legacy_scope_settings_text(
            parse(metadata_document(self.scope_name, self.command_port))
        )
        # A 3.0.0 server sends its own file: no "rendered from" comment.
        text = "".join(
            line + "\n" for line in text.splitlines() if not line.startswith("#")
        )
        self._reply(
            client, code, additional=(text + _LEGACY_EXTRA_SECTIONS).encode("utf-8")
        )

    def _on_storage_path(self, client, code, params, value, additional) -> None:
        self._reply(client, code, additional=b"/media/synthetic/drive1\n")

    def _on_sleep(self, client, code, params, value, additional) -> None:
        # ControlServer clamps to SLEEP_TIME_MIN, which is one minute.
        self.sleep_minutes = max(1, int(params[3]) if len(params) > 3 else 1)
        self._reply(client, code)

    def _on_state_get(self, client, code, params, value, additional) -> None:
        state = (
            SystemCommands.STATE_VALUE_BUSY
            if (self.stage.moving or self.workflow_running)
            else SystemCommands.STATE_VALUE_IDLE
        )
        self.system_state = state
        self._reply(client, code, value=float(state))

    def _on_state_idle(self, client, code, params, value, additional) -> None:
        self.system_state = SystemCommands.STATE_VALUE_IDLE
        self._reply(client, code)

    def _on_move(self, client, code, params, value, additional) -> None:
        axis = AXIS_NAMES.get(params[3] if len(params) > 3 else None)
        if axis is None:
            logger.warning("Synthetic scope: move with no axis in int32Data0")
            self._reply(client, code)
            return
        self.stage.start_move(axis, float(value))
        # Acknowledge now, arrive later. A caller that treats this ack as
        # arrival reads a stale position, exactly as it would on the rig.
        self._reply(client, code, value=float(value))

    def _on_position_get(self, client, code, params, value, additional) -> None:
        axis = AXIS_NAMES.get(params[3] if len(params) > 3 else None)
        position = self.stage_position()
        if axis is None:
            self._reply(client, code, value=0.0)
            return
        self._reply(client, code, params=list(params), value=position[axis])

    def _on_halt(self, client, code, params, value, additional) -> None:
        self.stage.halt()
        self._reply(client, code)

    def _on_home(self, client, code, params, value, additional) -> None:
        # HOME drives to the reference; it is not a stop. Kept distinct from
        # HALT so a test can tell which one an emergency stop really sent.
        for axis in self.stage.position:
            self.stage.start_move(axis, 0.0)
        self._reply(client, code)

    def _on_exposure_set(self, client, code, params, value, additional) -> None:
        self.exposure_us = int(params[3]) if len(params) > 3 else int(value)
        self._reply(client, code)

    def _on_exposure_get(self, client, code, params, value, additional) -> None:
        self._reply(client, code, params=[0, 0, 0, self.exposure_us, 0, 0, 0])

    def _on_image_size_get(self, client, code, params, value, additional) -> None:
        rows, cols = self.image_shape
        self._reply(client, code, params=[0, 0, 0, rows, cols, 0, 0])

    def _on_workflow_start(self, client, code, params, value, additional) -> None:
        if additional:
            try:
                self.workflows.append(additional.decode("utf-8", errors="replace"))
            except Exception:
                pass
        self.workflow_running = True
        self.system_state = SystemCommands.STATE_VALUE_BUSY
        self._reply(client, code)

    def _on_workflow_stop(self, client, code, params, value, additional) -> None:
        self.workflow_running = False
        self.system_state = SystemCommands.STATE_VALUE_IDLE
        self._reply(client, code)

    def _on_led_set(self, client, code, params, value, additional) -> None:
        # int32Data1 carries the percentage; 27 means 27%, not a scaled level.
        self.led_percent = float(params[4]) if len(params) > 4 else float(value)
        self._reply(client, code)

    # --------------------------------------------------------------- live port

    def _on_led_enable(self, client, code, params, value, additional) -> None:
        self.led_enabled = True
        self._reply(client, code)

    def _on_led_disable(self, client, code, params, value, additional) -> None:
        self.led_enabled = False
        self._reply(client, code)

    def _on_live_start(self, client, code, params, value, additional) -> None:
        self.live_view = True
        self._reply(client, code)

    def _on_live_stop(self, client, code, params, value, additional) -> None:
        self.live_view = False
        self._reply(client, code)

    def frame_now(self) -> np.ndarray:
        """The frame the camera would see at this instant."""
        pos = self.stage_position()
        led = (self.led_percent / 100.0) if self.led_enabled else 0.0
        return render_frame(pos["x"], pos["y"], pos["z"], led, self.image_shape)

    def _serve_live(self, listener: socket.socket) -> None:
        while self._running:
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            thread = threading.Thread(
                target=self._stream_live, args=(client,), daemon=True
            )
            thread.start()
            with self._lock:
                self._threads.append(thread)

    def _stream_live(self, client: socket.socket) -> None:
        """Send frames while live view is on, in the server's frame format."""
        period = 1.0 / LIVE_FPS
        try:
            while self._running:
                if not self.live_view:
                    time.sleep(0.02)
                    continue
                started = time.monotonic()
                frame = self.frame_now()
                rows, cols = frame.shape
                with self._lock:
                    self.frames_sent += 1
                    number = self.frames_sent
                header = struct.pack(
                    "<10I",
                    frame.nbytes,
                    cols,
                    rows,
                    int(frame.min()),
                    int(frame.max()),
                    int(time.time() * 1000) & 0xFFFFFFFF,
                    number,
                    int(self.exposure_us),
                    0,
                    0,
                )
                client.sendall(header + frame.tobytes())
                time.sleep(max(0.0, period - (time.monotonic() - started)))
        except OSError:
            pass
        finally:
            try:
                client.close()
            except OSError:
                pass
