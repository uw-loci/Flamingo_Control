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

* **Motion takes time.** ``POSITION_SET`` returns immediately and the
  axis travels at a finite speed, so a caller that assumes a move is finished
  when the ack arrives sees the same stale position it would see on the rig.
* **Three codes command a move.** ``STAGE_POSITION_SET`` (0x6004, what the
  client sends) and the vendor GUI's ``_SLIDER`` (0x6005) and ``_TEXT_CTRL``
  (0x6006) all move an axis, as on the server.

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
  frame format (48-byte header, 16-bit pixels) rendered at the current stage
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
import re
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


#: Laser lines the synthetic scope has (UI number: wavelength in nm).
SYNTHETIC_LASERS = {1: 405, 2: 488, 3: 561, 4: 640}


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
        f"          <Laser {i}>\n          Attached = true\n"
        f"          Wavelength (nm) = {wave}\n"
        f"          Filter wheel position = {i - 1}\n"
        f"          </Laser {i}>\n"
        for i, wave in SYNTHETIC_LASERS.items()
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


def _workflow_value(text: str, key: str) -> Optional[float]:
    """First ``key = number`` in a workflow text, or None."""
    m = re.search(rf"^\s*{re.escape(key)}\s*=\s*([-0-9.,]+)", text, re.M)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


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
        data_root: Optional[str] = None,
    ) -> None:
        self._requested_command_port = command_port
        self._requested_live_port = live_port
        self.scope_name = scope_name
        self.layout = layout or layouts.REFERENCE
        #: When set, a workflow writes its files here as the server does
        #: (the folder the save drive appears as on the client computer).
        self.data_root = data_root
        #: Set when a client hits SYSTEM_SLEEP, which on the 2026-10-06 layout
        #: is the code the 3.0.0 "list storage drives" used to be.
        self.sleep_minutes: Optional[int] = None

        self.stage = StageState()
        self.system_state = SystemCommands.STATE_VALUE_IDLE
        #: Exposure in integer ms, as the server's camera reports it.
        self.exposure_ms = 10
        self.trigger_mode = 0
        self.image_shape = (2048, 2048)
        self.sensor_shape = (2048, 2048)
        self.binning = (1, 1)
        self.light_sheet = {"mode": 0, "line_time_ns": 44364, "lines": 40, "delay": 0}
        #: Lasers present (UI numbers) and their power in percent.
        self.lasers = dict(SYNTHETIC_LASERS)
        self.lasers_on: set = set()
        #: Laser engines, each the set of UI laser numbers it drives; None is
        #: one engine with every laser. The server keeps a laser assignment
        #: list per engine, and before the 2026-11-20 build LASER_DISABLE_ALL
        #: ran only in the engine that owns the laser int32Data0 names
        #: (LaserControl::exeCommand), so a line in another engine stayed on.
        self.laser_engines: Optional[List[set]] = None
        #: The 2026-11-20 build turns every line of every engine off on
        #: LASER_DISABLE_ALL, whatever int32Data0 holds.
        self.disable_all_ignores_index = False
        #: Snapshots requested so far; each live connection sends one frame
        #: per new snapshot (the server sends to every data client it has).
        self._snapshot_seq = 0
        self._send_locks: Dict[int, threading.Lock] = {}
        self._workflow_stop = threading.Event()
        self.led_percent = 0.0
        self.led_enabled = False
        #: Below this level the LED gives no light. Many of the rigs' LEDs stay
        #: dark below about 25%; 0 models an LED that lights at any level.
        self.led_min_percent = 0.0
        self.live_view = False
        #: Frames sent on the live port, all connections together.
        self.frames_sent = 0
        self.laser_levels: Dict[int, float] = {i: 5.0 for i in self.lasers}
        self.workflow_running = False
        #: Workflow text the client uploaded, newest last.
        self.workflows: List[str] = []
        #: Settings files sent with SCOPE_SETTINGS_SAVE (3.0.0), newest last.
        self.saved_scope_settings: List[bytes] = []

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

        self._last_buffer = decoded.get("data") or b""
        if isinstance(self._last_buffer, str):
            self._last_buffer = self._last_buffer.encode("utf-8", "replace")
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
            "COMMON_SCOPE_SETTINGS_SAVE": self._on_scope_settings_save,
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
            # The server moves for all three position-set codes: the client
            # sends POSITION_SET (0x6004); the vendor GUI's slider and text
            # box send 0x6005 and 0x6006. A client once sent the slider code,
            # and a scope that modelled only one would have acked its moves
            # and moved nothing.
            StageCommands.POSITION_SET: self._on_move,
            StageCommands.POSITION_SET_SLIDER: self._on_move,
            StageCommands.POSITION_SET_TEXT_CTRL: self._on_move,
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
            # Modelled on the server source (claude-reports design
            # server-command-reply-reference.md): which code answers, what is
            # in which field, and what is never answered at all.
            0x3006: self._on_snapshot,
            0x300D: self._on_trigger_mode_set,
            0x300E: self._on_trigger_mode_get,
            0x300F: self._on_roi_set,
            0x3011: self._on_roi_set,
            0x3010: self._on_roi_get,
            0x3012: self._on_roi_get,
            0x3018: self._on_fps_get,
            0x302B: self._on_check_stack,
            0x302C: self._on_frame_period_get,
            0x3030: self._on_light_sheet,
            0x3031: self._on_light_sheet,
            0x3032: self._on_light_sheet,
            0x3033: self._on_light_sheet,
            0x3037: self._on_pixel_fov_get,
            0x8002: self._on_filter_wheel_stopped,
            0x2001: self._on_laser_level_set,
            0x2002: self._on_laser_level_get,
            0x2003: self._on_laser_enable,
            0x2004: self._on_laser_enable,
            0x2005: self._on_laser_enable,
            0x2006: self._on_laser_disable,
            0x2007: self._on_laser_disable_all,
            **(
                {
                    self.layout.codes["CAMERA_BINNING_GET"]: self._on_binning_get,
                    self.layout.codes["CAMERA_BINNING_SET"]: self._on_binning_set,
                }
                if "CAMERA_BINNING_GET" in self.layout.codes
                else {}
            ),
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
        status: int = 1,
    ) -> None:
        """Send one well-formed 128-byte reply, plus ``additional`` after it.

        Always 128 bytes, with addDataBytes giving the length of what follows.
        The client reads fixed-size messages, so a reply of any other length
        costs it frame sync. The layout guard is off: this is the server.
        """
        packet = self._encoder.encode_command(
            code=code,
            status=status,
            params=params or [0, 0, 0, 0, 0, 0, 0],
            value=value,
            data=data,
            additional_data_size=len(additional),
            check_server_layout=False,
        )
        assert len(packet) == 128, f"reply was {len(packet)} bytes, not 128"
        lock = self._send_locks.setdefault(id(client), threading.Lock())
        try:
            with lock:
                client.sendall(packet + additional)
        except OSError:
            pass

    def _later(self, delay: float, fn, *args) -> None:
        """Run ``fn`` after ``delay`` s on a daemon thread (unsolicited frames)."""
        timer = threading.Timer(delay, fn, args=args)
        timer.daemon = True
        timer.start()

    def _state_code(self) -> int:
        """The state's own command code, as SYSTEM_STATE_GET's int32Data0."""
        if self.workflow_running:
            return 0xA005
        if self.live_view:
            return 0xA004
        return SystemCommands.STATE_IDLE

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
        # The 3.0.0 server answers with COMMON_SCOPE_SETTINGS, not with the
        # code that was sent (ControlServer::loadScopeSettings).
        self._reply(
            client,
            self.layout.codes["COMMON_SCOPE_SETTINGS"],
            additional=(text + _LEGACY_EXTRA_SECTIONS).encode("utf-8"),
        )

    def _on_scope_settings_save(self, client, code, params, value, additional) -> None:
        """3.0.0 ControlServer::saveScopeSettings: an empty file gets no reply;
        a file the server cannot read gets MESSAGE_FROM_CONTROLLER; otherwise
        the settings are taken (in memory, as no SAVE_TO_DISK bit is set) and
        echoed back with COMMON_SCOPE_SETTINGS, status 1."""
        self.saved_scope_settings.append(additional or b"")
        if not additional:
            return
        if b"<" not in additional:
            self._reply(
                client,
                self.layout.codes["COMMON_MESSAGE_FROM_CONTROLLER"],
                additional=b"Save scope settings error - invalid scope settings file",
            )
            return
        self._reply(
            client, self.layout.codes["COMMON_SCOPE_SETTINGS"], additional=additional
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
        self._reply(
            client,
            code,
            params=[0, 0, 0, self._state_code(), 0, 0, 0],
            value=float(state),
        )

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
        # The server sends no reply of its own: what arrives is the stage
        # monitor's position frames. 3.0.0 coded them with the code that was
        # sent; later builds code every one 0x6004 (StageControl.cpp,
        # threadMotionWaitForStop), so a client waiting for 0x6005 times out.
        frame_code = (
            code if self.layout is layouts.REFERENCE else StageCommands.POSITION_SET
        )
        self._reply(client, frame_code, value=float(value))
        arrival = self.stage.arrives_at.get(axis, time.monotonic())
        self._later(
            max(0.0, arrival - time.monotonic()) + 0.02,
            self._motion_stopped,
            client,
            axis,
            arrival,
        )

    def _motion_stopped(self, client, axis: str, arrival: float) -> None:
        """MOTION_STOPPED (int32Data0 0xFF) once this move ended, unless a
        later move of the same axis superseded it."""
        if self.stage.arrives_at.get(axis) not in (None, arrival):
            return
        self.stage.settle()
        self._reply(
            client, StageCommands.MOTION_STOPPED, params=[0, 0, 0, 0xFF, 0, 0, 0]
        )

    def _on_position_get(self, client, code, params, value, additional) -> None:
        axis = AXIS_NAMES.get(params[3] if len(params) > 3 else None)
        position = self.stage_position()
        if axis is None:
            self._reply(client, code, value=0.0)
            return
        self._reply(client, code, params=list(params), value=position[axis])

    def _on_halt(self, client, code, params, value, additional) -> None:
        # The server runs a stage command only for axis 1-4 or 0xFF (all);
        # axis 0 is STAGE_AXIS_NONE and is dropped with no reply at all.
        axis = params[3] if len(params) > 3 else 0
        if axis not in (1, 2, 3, 4, 0xFF):
            logger.info(
                "Synthetic scope: HALT for axis %s ignored, as the server does", axis
            )
            return
        was_moving = self.stage.moving
        self.stage.halt()
        self._reply(client, code, params=[0, 0, 0, axis, 0, 0, 0])
        if was_moving:
            self._reply(
                client, StageCommands.MOTION_STOPPED, params=[0, 0, 0, 0xFF, 0, 0, 0]
            )

    def _on_home(self, client, code, params, value, additional) -> None:
        # HOME drives to the reference; it is not a stop. Kept distinct from
        # HALT so a test can tell which one an emergency stop really sent.
        for axis in self.stage.position:
            self.stage.start_move(axis, 0.0)
        self._reply(client, code)

    def _on_exposure_set(self, client, code, params, value, additional) -> None:
        # int32Data0 in integer ms; answered with FPS_GET then EXPOSURE_GET.
        self.exposure_ms = int(params[3]) if len(params) > 3 else int(value)
        self._reply(client, 0x3018, value=LIVE_FPS)
        self._reply(client, 0x300A, params=[0, 0, 0, self.exposure_ms, 0, 0, 0])

    def _on_exposure_get(self, client, code, params, value, additional) -> None:
        self._reply(client, code, params=[0, 0, 0, self.exposure_ms, 0, 0, 0])

    def _on_image_size_get(self, client, code, params, value, additional) -> None:
        rows, cols = self.image_shape
        self._reply(
            client,
            code,
            params=[0, 0, 0, cols, rows, 0, 0],
            value=6.5 * self.binning[0],
        )

    def _on_workflow_start(self, client, code, params, value, additional) -> None:
        """Never answered under 0x3004: the workflow-running state (0xA005),
        then per stack gauge size, images saved, STACK_COMPLETE, then idle."""
        text = (additional or b"").decode("utf-8", errors="replace")
        if text:
            self.workflows.append(text)
        if self.workflow_running:
            return
        planes = _workflow_value(text, "Number of planes")
        exposure = None
        # 3.0.0 reads only "Exposure time (ns)"; later builds read "(ms)" and
        # still accept "(ns)". The value is ms either way. "(us)", the key the
        # app keeps internally, is read by no build.
        labels = (
            ("Exposure time (ns)",)
            if self.layout is layouts.REFERENCE
            else ("Exposure time (ms)", "Exposure time (ns)")
        )
        for label in labels:
            exposure = exposure or _workflow_value(text, label)
        if exposure:
            self.exposure_ms = int(float(str(exposure).replace(",", "")))
        self.workflow_running = True
        self.system_state = SystemCommands.STATE_VALUE_BUSY
        self._workflow_stop.clear()
        self._reply(client, 0xA005)
        if planes and self.data_root:
            try:
                self._write_acquisition(text, int(planes))
            except OSError as exc:
                logger.warning(
                    "Synthetic scope could not write the acquisition: %s", exc
                )
        self._workflow_thread = None
        if planes:
            self._workflow_thread = threading.Thread(
                target=self._run_workflow, args=(client, int(planes)), daemon=True
            )
            self._workflow_thread.start()

    def _write_acquisition(self, text: str, planes: int) -> None:
        """The experiment folder the server writes: Workflow.txt and, per
        stack, the image file and its _Settings.txt sidecar; the metadata
        copy this build writes (ScopeSettings.txt up to 3.0.0,
        SystemMetaData.txt from 10-06 on). From 10-06 on the workflow keys
        are the server's new ones (ROI, bit binning, exposure in ms,
        "Left path recording = true")."""
        import os

        m = re.search(r"Save image directory\s*=\s*(.+)", text)
        # The server names the folder {drive}/{YYYYmmdd_HHMMSS}_{directory}
        # (WFSettings::createExperimentDirectory, 3.0.0 and later).
        folder = os.path.join(
            self.data_root,
            time.strftime("%Y%m%d_%H%M%S_")
            + (m.group(1).strip() if m else "experiment"),
        )
        os.makedirs(folder, exist_ok=True)
        out = text
        if self.layout.has_metadata_document:

            def path_line(match):
                v = match.group(2).strip().upper()
                on = v.startswith("ON") or v.endswith("1")
                return f"{match.group(1)} path recording = {'true' if on else 'false'}"

            out = re.sub(r"(Left|Right) path\s*=\s*([^\n]*)", path_line, out)
            out = out.replace("AOI width", "ROI width").replace(
                "AOI height", "ROI height"
            )
            out = re.sub(
                r"Exposure time \(us\)\s*=\s*[^\n]*",
                f"Exposure time (ms) = {self.exposure_ms}",
                out,
            )
            out = out.replace(
                "  </Camera Settings>",
                f"    Bit binning width = {self.binning[0]}\n"
                f"    Bit binning height = {self.binning[1]}\n  </Camera Settings>",
            )
        with open(os.path.join(folder, "Workflow.txt"), "w", encoding="utf-8") as f:
            f.write(out)
        doc = metadata_document(self.scope_name, self.command_port)
        if self.layout.has_metadata_document:
            with open(
                os.path.join(folder, "SystemMetaData.txt"), "w", encoding="utf-8"
            ) as f:
                f.write(doc)
        else:
            legacy, _ = legacy_scope_settings_text(parse(doc))
            with open(
                os.path.join(folder, "ScopeSettings.txt"), "w", encoding="utf-8"
            ) as f:
                f.write(legacy)
        rows, cols = self.image_shape
        stem = os.path.join(
            folder, f"S000_t000000_V000_R0000_X000_Y000_C00_I0_D0_P{planes:05d}"
        )
        with open(stem + ".raw", "wb") as f:
            f.truncate(planes * rows * cols * 2)
        with open(stem + "_Settings.txt", "w", encoding="utf-8") as f:
            f.write(out)

    def _run_workflow(self, client, planes: int) -> None:
        self._reply(client, 0x9003, params=[0, 0, 0, planes, 0, 0, 0])
        saved = 0
        while saved < planes and not self._workflow_stop.is_set():
            time.sleep(1.0 / (4 * LIVE_FPS))
            saved += 1
            if saved % 10 == 0 or saved == planes:
                self._reply(client, 0x9007, params=[0, 0, 0, saved, 0, 0, 0])
        if not self._workflow_stop.is_set():
            self._reply(client, 0x3014)
        self.workflow_running = False
        self.system_state = SystemCommands.STATE_VALUE_IDLE
        self._reply(client, SystemCommands.STATE_IDLE)

    def _on_workflow_stop(self, client, code, params, value, additional) -> None:
        running = self.workflow_running
        self._workflow_stop.set()
        text = b"Work flow terminated" if running else b"No active work flow running"
        self.stage.halt()
        self._reply(client, code, data=text)
        thread = getattr(self, "_workflow_thread", None)
        if thread is not None and thread.is_alive():
            return  # it sends idle itself once it sees the stop
        self.workflow_running = False
        self.system_state = SystemCommands.STATE_VALUE_IDLE
        if running:
            self._reply(client, SystemCommands.STATE_IDLE)

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
        self._reply(client, code, data=b"Live view thread started")
        self._reply(client, 0xA004)

    def _on_live_stop(self, client, code, params, value, additional) -> None:
        running = self.live_view
        self.live_view = False
        if running:
            self._reply(client, code, data=b"Live view thread terminated")
        else:
            self._reply(client, code, data=b"Live view thread not running", status=0)
        self._reply(client, SystemCommands.STATE_IDLE)

    # --------------------------------------------------- more camera, lasers

    def _on_snapshot(self, client, code, params, value, additional) -> None:
        rows, cols = self.image_shape
        self._reply(client, code, data=b"Live view thread started")
        self._reply(client, 0xA003)
        buffer = struct.pack("<3i", 0, cols, rows)
        self._reply(
            client,
            code,
            params=[0, 0, 0, rows * cols * 2, 0, 0, 0],
            data=buffer,
            status=3,
        )
        self._snapshot_seq += 1
        self._reply(client, SystemCommands.STATE_IDLE)

    def _on_trigger_mode_set(self, client, code, params, value, additional) -> None:
        self.trigger_mode = int(params[3]) if len(params) > 3 else 0
        self._on_trigger_mode_get(client, 0x300E, params, value, additional)

    def _on_trigger_mode_get(self, client, code, params, value, additional) -> None:
        names = {0: b"Auto", 1: b"SW-Trig", 2: b"Ext-Exp. Start", 3: b"Ext-Exp. Ctrl"}
        self._reply(
            client,
            0x300E,
            params=[0, 0, 0, self.trigger_mode, 0, 0, 0],
            data=names.get(self.trigger_mode, b""),
        )

    def _binned_sensor(self) -> Tuple[int, int]:
        return (
            self.sensor_shape[0] // self.binning[1],
            self.sensor_shape[1] // self.binning[0],
        )

    def _roi_edges(self) -> Tuple[int, int, int, int]:
        rows, cols = self.image_shape
        b_rows, b_cols = self._binned_sensor()
        s_rows, s_cols = max(b_rows, rows), max(b_cols, cols)
        left = (s_cols - cols) // 2 + 1
        top = (s_rows - rows) // 2 + 1
        return left, left + cols - 1, top, top + rows - 1

    def _on_roi_get(self, client, code, params, value, additional) -> None:
        left, right, top, bottom = self._roi_edges()
        lo, hi = (left, right) if code == 0x3010 else (top, bottom)
        self._reply(client, code, params=[0, 0, 0, lo, hi, 0, 0])

    def _on_roi_set(self, client, code, params, value, additional) -> None:
        """int32Data0 = left (top) edge, int32Data1 = right (bottom) edge,
        1-based. An edge pair the camera cannot use is reverted (status 2),
        as the 2026-11-13 server does."""
        lo = int(params[3]) if len(params) > 3 else 0
        hi = int(params[4]) if len(params) > 4 else 0
        b_rows, b_cols = self._binned_sensor()
        limit = b_cols if code == 0x300F else b_rows
        rows, cols = self.image_shape
        if 1 <= lo < hi <= limit:
            if code == 0x300F:
                cols = hi - lo + 1
            else:
                rows = hi - lo + 1
            self.image_shape = (rows, cols)
            status = 1
        else:
            status = 2
        self._reply(client, code, params=[0, 0, 0, lo, hi, 0, 0], status=status)
        self._reply(client, 0x3018, value=LIVE_FPS)
        self._on_roi_get(client, 0x3010, params, value, additional)
        self._on_roi_get(client, 0x3012, params, value, additional)

    def _on_fps_get(self, client, code, params, value, additional) -> None:
        self._reply(client, code, value=LIVE_FPS)

    def _on_frame_period_get(self, client, code, params, value, additional) -> None:
        self._reply(client, code, value=1000.0 / LIVE_FPS)

    def _on_pixel_fov_get(self, client, code, params, value, additional) -> None:
        self._reply(client, code, value=PIXEL_SIZE_MM * self.binning[0])

    def _on_binning_set(self, client, code, params, value, additional) -> None:
        """Powers of two up to 4 are taken and reset the ROI to the full frame
        in binned pixels; anything else is reverted (status 2)."""
        h = int(params[3]) if len(params) > 3 else 1
        v = int(params[4]) if len(params) > 4 else 1
        if h in (1, 2, 4) and v in (1, 2, 4):
            self.binning = (h, v)
            self.image_shape = self._binned_sensor()
            status = 1
        else:
            status = 2
        self._reply(client, code, params=[0, 0, 0, h, v, 0, 0], status=status)
        self._on_binning_get(
            client, self.layout.codes["CAMERA_BINNING_GET"], params, value, additional
        )
        self._reply(client, 0x3018, value=LIVE_FPS)

    def _on_filter_wheel_stopped(self, client, code, params, value, additional) -> None:
        # The Scope board answers the query; data0 1 = stopped.
        self._reply(client, code, params=[0, 0, 0, 1, 0, 0, 0])

    def _on_binning_get(self, client, code, params, value, additional) -> None:
        self._reply(
            client, code, params=[0, 0, 0, self.binning[0], self.binning[1], 0, 0]
        )

    def _on_check_stack(self, client, code, params, value, additional) -> None:
        planes = _workflow_value(
            (additional or b"").decode("utf-8", "replace"), "Number of planes"
        )
        seconds = int((planes or 0) / LIVE_FPS) + 1
        report = f"Stack 1 OK\nExperiment run time 00:00:00:{seconds:02d}\n"
        status = params[0] if params else 0  # the server echoes the request's status
        self._reply(client, code, additional=report.encode("utf-8"), status=status)

    def _on_light_sheet(self, client, code, params, value, additional) -> None:
        v = int(params[3]) if len(params) > 3 else 0
        key = {
            0x3030: "mode",
            0x3031: "lines",
            0x3032: "delay",
            0x3033: "line_time_ns",
        }[code]
        self.light_sheet[key] = (
            1 if (key == "mode" and v == 1) else (0 if key == "mode" else v)
        )
        ls = self.light_sheet
        self._reply(client, 0x1006, data=b"Light sheet mode settings updated")
        for c, k in (
            (0x3030, "mode"),
            (0x3033, "line_time_ns"),
            (0x3031, "lines"),
            (0x3032, "delay"),
        ):
            self._reply(client, c, params=[0, 0, 0, ls[k], 0, 0, 0])

    def _laser_reply(self, client, code, laser: int, **kw) -> None:
        self._reply(client, code, params=[0, 0, 0, laser, laser, 0, 0], **kw)

    def _on_laser_level_get(self, client, code, params, value, additional) -> None:
        laser = int(params[3]) if len(params) > 3 else 0
        if laser not in self.lasers:
            return
        level = self.laser_levels.get(laser, 0.0)
        self._laser_reply(
            client, 0x2002, laser, value=level, data=f"{level:.2f}".encode()
        )

    def _on_laser_level_set(self, client, code, params, value, additional) -> None:
        """Percent as text in the buffer; answered with LEVEL_GET only."""
        laser = int(params[3]) if len(params) > 3 else 0
        if laser not in self.lasers:
            return
        text = self._last_buffer.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()
        try:
            self.laser_levels[laser] = float(text)
        except ValueError:
            pass
        self._on_laser_level_get(client, 0x2002, params, value, additional)

    def _on_laser_enable(self, client, code, params, value, additional) -> None:
        laser = int(params[3]) if len(params) > 3 else 0
        if laser not in self.lasers:
            return
        for other in sorted(self.lasers_on - {laser}):
            self._laser_reply(client, 0x2006, other)
        self.lasers_on = {laser}
        self._laser_reply(client, 0x2003, laser)

    def _on_laser_disable(self, client, code, params, value, additional) -> None:
        laser = int(params[3]) if len(params) > 3 else 0
        if laser not in self.lasers:
            return
        self.lasers_on.discard(laser)
        self._laser_reply(client, 0x2006, laser)

    def _on_laser_disable_all(self, client, code, params, value, additional) -> None:
        # Before 2026-11-20: acts only when int32Data0 names a real laser (0
        # does nothing), and only on the lines of that laser's engine.
        laser = int(params[3]) if len(params) > 3 else 0
        if self.disable_all_ignores_index:
            targets = set(self.lasers)
        else:
            if laser not in self.lasers:
                return
            engines = self.laser_engines or [set(self.lasers)]
            targets = set().union(*(e for e in engines if laser in e))
        for each in sorted(targets):
            self.lasers_on.discard(each)
            self._laser_reply(client, 0x2006, each)

    def frame_now(self) -> np.ndarray:
        """The frame the camera would see at this instant."""
        pos = self.stage_position()
        lit = self.led_enabled and self.led_percent >= self.led_min_percent
        led = (self.led_percent / 100.0) if lit else 0.0
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
        seen_snapshot = self._snapshot_seq
        try:
            while self._running:
                if not self.live_view and self._snapshot_seq == seen_snapshot:
                    time.sleep(0.02)
                    continue
                seen_snapshot = self._snapshot_seq
                started = time.monotonic()
                frame = self.frame_now()
                rows, cols = frame.shape
                with self._lock:
                    self.frames_sent += 1
                    number = self.frames_sent
                # The server's struct ImageHeader, 48 bytes: size, width,
                # height, scale min/max, device index, two option words, then
                # image index start/stop, image index and total (int32).
                header = struct.pack(
                    "<8I4i",
                    frame.nbytes,
                    cols,
                    rows,
                    int(frame.min()),
                    int(frame.max()),
                    0,
                    0,
                    0,
                    0,
                    0,
                    number,
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
