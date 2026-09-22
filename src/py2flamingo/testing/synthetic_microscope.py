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
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from py2flamingo.core.command_codes import (
    CameraCommands,
    LEDCommands,
    StageCommands,
    SystemCommands,
)
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
        live_port: Live-image port to bind. 0 picks a free port.
        scope_name: Reported in ``SCOPE_SETTINGS_LOAD`` text.
    """

    def __init__(
        self,
        command_port: int = DEFAULT_COMMAND_PORT,
        live_port: int = DEFAULT_LIVE_PORT,
        scope_name: str = "synthetic",
    ) -> None:
        self._requested_command_port = command_port
        self._requested_live_port = live_port
        self.scope_name = scope_name

        self.stage = StageState()
        self.system_state = SystemCommands.STATE_VALUE_IDLE
        self.exposure_us = 10_000
        self.image_shape = (2048, 2048)
        self.led_percent = 0.0
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

    def _handlers(self) -> Dict:
        return {
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
        }

    # -------------------------------------------------------------- behaviours

    def _reply(
        self,
        client: socket.socket,
        code: int,
        params: Optional[List[int]] = None,
        value: float = 0.0,
        data: bytes = b"",
    ) -> None:
        """Send one well-formed 128-byte reply.

        Always 128 bytes. The client reads fixed-size messages, so a reply of
        any other length costs it frame sync.
        """
        packet = self._encoder.encode_command(
            code=code,
            status=self.system_state,
            params=params or [0, 0, 0, 0, 0, 0, 0],
            value=value,
            data=data,
        )
        assert len(packet) == 128, f"reply was {len(packet)} bytes, not 128"
        try:
            client.sendall(packet)
        except OSError:
            pass

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

    def _serve_live(self, listener: socket.socket) -> None:
        while self._running:
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                client.close()
            except OSError:
                pass
