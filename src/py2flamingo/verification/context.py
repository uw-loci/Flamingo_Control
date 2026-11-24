"""What a check works with: the connection, the services, a tap and a probe.

The tap records every command the client encodes and every message the
background reader receives, so the report can show the raw exchange behind
each result and a check can wait for an unsolicited message (MOTION_STOPPED,
STACK_COMPLETE) without registering its own handler. The probe sends one
command and returns the parsed reply, for checks that test a single command
directly rather than through a service.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from py2flamingo.core import server_layout
from py2flamingo.core.errors import WorkflowStartRefusedError
from py2flamingo.core.frames import parse_frame
from py2flamingo.verification import codes
from py2flamingo.verification.model import Aborted, Failed, Skipped

logger = logging.getLogger(__name__)

TRIGGER_CALL_BACK = 0x80000000


@dataclass
class Message:
    """One 128-byte message, either direction, decoded."""

    t: float
    direction: str  # "tx" or "rx"
    code: int
    status: int = 0
    hardware_id: int = 0
    subsystem_id: int = 0
    client_id: int = 0
    int0: int = 0
    int1: int = 0
    int2: int = 0
    bits: int = 0
    value: float = 0.0
    buffer: bytes = b""
    additional: bytes = b""

    @property
    def name(self) -> str:
        return codes.name_of(self.code)

    @property
    def text(self) -> str:
        """The 72-byte buffer as text, up to its first NUL."""
        return self.buffer.split(b"\x00", 1)[0].decode("utf-8", errors="replace")

    @property
    def additional_text(self) -> str:
        return self.additional.replace(b"\x00", b"").decode("utf-8", errors="replace")

    def line(self, t0: float) -> str:
        extra = ""
        if self.text:
            extra += f" buf={self.text!r}"
        if self.additional:
            shown = self.additional_text[:120].replace("\n", "\\n")
            extra += f" add[{len(self.additional)}]={shown!r}"
        return (
            f"{self.t - t0:9.3f} {self.direction} 0x{self.code:04X} {self.name:<40s} "
            f"st={self.status} hw={self.hardware_id} sub={self.subsystem_id} "
            f"i0={self.int0} i1={self.int1} i2={self.int2} "
            f"bits=0x{self.bits & 0xFFFFFFFF:08X} v={self.value:g}{extra}"
        )


def decode_packet(packet: bytes, direction: str) -> Optional[Message]:
    if len(packet) < 128:
        return None
    f = parse_frame(packet[:128])
    if not f.is_valid:
        return None
    return Message(
        t=time.time(),
        direction=direction,
        code=f.command_code,
        status=f.status_code,
        hardware_id=f.hardware_id,
        subsystem_id=f.subsystem_id,
        client_id=f.client_id,
        int0=f.int32_data0,
        int1=f.int32_data1,
        int2=f.int32_data2,
        bits=f.cmd_data_bits,
        value=f.value,
        buffer=f.data_field,
        additional=packet[128:],
    )


class Tap:
    """Records what goes out (encoder) and what comes in (reader dispatch)."""

    def __init__(self) -> None:
        self.messages: List[Message] = []
        self._cond = threading.Condition()
        self._undo: List[Callable[[], None]] = []

    # -------------------------------------------------------------- install

    def install(self, connection: Any) -> None:
        encoder = getattr(connection, "encoder", None)
        if encoder is not None:
            original = encoder.encode_command

            def encode(*args, **kwargs):
                packet = original(*args, **kwargs)
                msg = decode_packet(packet, "tx")
                if msg is not None:
                    self._add(msg)
                return packet

            encoder.encode_command = encode
            self._undo.append(lambda: delattr(encoder, "encode_command"))

        client = getattr(
            getattr(connection, "tcp_connection", None), "_command_client", None
        )
        dispatcher = getattr(client, "_dispatcher", None)
        if dispatcher is not None:
            original_dispatch = dispatcher.dispatch

            def dispatch(message):
                self._add(
                    Message(
                        t=time.time(),
                        direction="rx",
                        code=message.command_code,
                        status=message.status_code,
                        hardware_id=message.hardware_id,
                        subsystem_id=message.subsystem_id,
                        client_id=message.client_id,
                        int0=message.int32_data0,
                        int1=message.int32_data1,
                        int2=message.int32_data2,
                        bits=message.cmd_data_bits,
                        value=message.value,
                        buffer=message.data_field or b"",
                        additional=message.additional_data or b"",
                    )
                )
                return original_dispatch(message)

            dispatcher.dispatch = dispatch
            self._undo.append(lambda: delattr(dispatcher, "dispatch"))
        else:
            logger.warning(
                "Verification tap: no background reader; replies not recorded"
            )

    def remove(self) -> None:
        while self._undo:
            try:
                self._undo.pop()()
            except AttributeError:
                pass

    # -------------------------------------------------------------- reading

    def _add(self, msg: Message) -> None:
        with self._cond:
            self.messages.append(msg)
            self._cond.notify_all()

    def mark(self) -> int:
        with self._cond:
            return len(self.messages)

    def since(
        self, mark: int, direction: Optional[str] = None, code: Optional[int] = None
    ) -> List[Message]:
        with self._cond:
            found = self.messages[mark:]
        return [
            m
            for m in found
            if (direction is None or m.direction == direction)
            and (code is None or m.code == code)
        ]

    def wait_for(
        self,
        code: int,
        mark: int,
        timeout: float,
        abort: Optional[threading.Event] = None,
        predicate: Optional[Callable[[Message], bool]] = None,
    ) -> Optional[Message]:
        """First received message with ``code`` at or after ``mark``."""
        deadline = time.monotonic() + timeout
        with self._cond:
            index = mark
            while True:
                while index < len(self.messages):
                    m = self.messages[index]
                    index += 1
                    if (
                        m.direction == "rx"
                        and m.code == code
                        and (predicate is None or predicate(m))
                    ):
                        return m
                remaining = deadline - time.monotonic()
                if remaining <= 0 or (abort is not None and abort.is_set()):
                    return None
                self._cond.wait(min(remaining, 0.2))


@dataclass
class Target:
    """Where checks that move or image work. From the rig's settings json."""

    name: str
    position: Dict[str, float]  # x, y, z in mm, r in degrees
    reference_dir: Optional[Path] = None
    defined: bool = True  # False: the current position stood in


@dataclass
class RunOptions:
    rig: str = ""
    sample: str = ""  # what the operator says is mounted
    report_dir: Optional[Path] = None
    # Server-side folder name for T4 data, under the drive the workflow uses.
    server_save_drive: str = ""
    server_save_subdir: str = "verification"
    # Where the server's save drive appears on this computer (e.g. D:\\CTLSM1 for
    # /media/deploy/ctlsm1), for reading back what a workflow wrote.
    local_data_root: str = ""
    # The app's server-path -> local-path mappings (drive_mappings.json), used
    # when local_data_root is empty or does not hold the workflow's folder.
    drive_mappings: Dict[str, str] = field(default_factory=dict)
    motion_step_mm: float = 0.1
    rotation_step_deg: float = 1.0
    halt_test_distance_mm: float = 1.0
    workflow_planes: int = 20
    laser_test_percent: float = 1.0
    # Many of the LEDs on these rigs stay dark below about 25%, so the default
    # sits above that. The brightness check also uses twice this level, so the
    # operator's choice is limited to LED_TEST_PERCENT_MAX.
    led_test_percent: float = 30.0
    live_frames: int = 20
    position_tolerance_mm: float = 0.005
    rotation_tolerance_deg: float = 0.05


LED_TEST_PERCENT_DEFAULT = 30.0
LED_TEST_PERCENT_MAX = 50.0


@dataclass
class RunContext:
    """Everything a check may use. Built by the runner."""

    connection: Any
    services: Dict[str, Any]
    tap: Tap
    options: RunOptions
    abort: threading.Event
    envelope: Any = None
    target: Optional[Target] = None
    # Filled in by checks for later checks (e.g. what T0 read).
    facts: Dict[str, Any] = field(default_factory=dict)
    # Per-check scratch, reset by the runner before each check.
    notes: List[str] = field(default_factory=list)
    values: Dict[str, Any] = field(default_factory=dict)
    _restores: List[Tuple[str, Callable[[], None], bool, bool]] = field(
        default_factory=list
    )
    progress: Optional[Callable[[str], None]] = None

    # -------------------------------------------------------------- helpers

    @property
    def layout(self) -> Optional[server_layout.ServerLayout]:
        return server_layout.active()

    def service(self, name: str) -> Any:
        svc = self.services.get(name)
        if svc is None:
            raise Skipped(f"no {name} service in this run")
        return svc

    def note(self, text: str) -> None:
        self.notes.append(text)

    def record(self, key: str, value: Any) -> None:
        self.values[key] = value

    def step(self, text: str) -> None:
        """Say what is happening now (progress line in the GUI and CLI)."""
        if self.progress is not None:
            self.progress(text)
        self.check_abort()

    def check_abort(self) -> None:
        if self.abort.is_set():
            raise Aborted("run aborted")

    def on_restore(
        self,
        label: str,
        fn: Callable[[], None],
        moves: bool = False,
        at_end: bool = False,
    ) -> None:
        """Register how to put something back, newest first.

        Restores run as soon as the check that registered them ends, so a
        setting changed by one check never carries into the next. ``at_end``
        keeps it to the end of the run instead (the stage returning from the
        target to where the run started).

        ``moves``: the restore drives the stage. After an abort those are
        reported and not run: an operator who pressed Abort wants the stage
        to stay where it stopped.
        """
        self._restores.append((label, fn, moves, at_end))

    def restores(self, at_end: bool) -> List[Tuple[str, Callable[[], None], bool]]:
        """Take the restores of one kind (all of them when ``at_end``)."""
        take = [r for r in self._restores if at_end or not r[3]]
        self._restores = [r for r in self._restores if r not in take]
        return [(label, fn, moves) for label, fn, moves, _ in reversed(take)]

    def code(self, name: str) -> int:
        """Wire code for this server build; Skipped if the build lacks it."""
        try:
            return codes.resolve(name)
        except server_layout.CommandUnavailable as e:
            raise Skipped(str(e)) from e

    def wait_for(
        self, name_or_code, mark: int, timeout: float, predicate=None
    ) -> Optional[Message]:
        code = (
            name_or_code if isinstance(name_or_code, int) else self.code(name_or_code)
        )
        return self.tap.wait_for(code, mark, timeout, self.abort, predicate)

    def sleep(self, seconds: float) -> None:
        """Wait, but return at once on abort."""
        if self.abort.wait(seconds):
            raise Aborted("run aborted")

    # -------------------------------------------------------------- probe

    def exchange(
        self,
        name_or_code,
        *,
        int0: int = 0,
        int1: int = 0,
        int2: int = 0,
        value: float = 0.0,
        hardware_id: int = 0,
        subsystem_id: int = 0,
        reply: Optional[str] = "same",
        timeout: float = 5.0,
        additional: bytes = b"",
        callback: bool = True,
    ) -> Optional[Message]:
        """Send one command; return the reply the server sends back.

        ``reply="same"`` waits for a message with the same code (how the
        server answers queries); ``reply=None`` sends and returns None; any
        other name waits for that code instead.
        """
        code = (
            name_or_code if isinstance(name_or_code, int) else self.code(name_or_code)
        )
        self.check_abort()
        # Marked before encoding: the tap records the command as it is encoded,
        # and only received messages after this point can be its reply.
        mark = self.tap.mark()
        params = [hardware_id, subsystem_id, 0, int0, int1, int2, 0]
        # The server answers subsystem commands only when the callback bit is
        # set, so it is on by default even where no reply is awaited.
        if callback:
            params[6] = TRIGGER_CALL_BACK
        packet = self.connection.encoder.encode_command(
            code=code,
            status=0,
            params=params,
            value=value,
            data=b"",
            additional_data_size=len(additional),
        )
        if additional:
            packet = packet + additional
        want = code if reply == "same" else (self.code(reply) if reply else None)
        # The connection's one locked writer, so the frame and any payload
        # go out together (MVCConnectionService.send_raw_bytes).
        send = getattr(self.connection, "send_raw_bytes", None)
        if not callable(send):
            raise Failed("the connection has no command-socket writer")
        try:
            send(packet)
        except ConnectionError as e:
            raise Failed(f"command socket not available ({e})")
        except WorkflowStartRefusedError as e:
            # The app's emergency-stop latch (or unknown limits) refuses a
            # server workflow on every path, this one included.
            raise Failed(e.message)
        if want is None:
            return None
        reply_msg = self.tap.wait_for(want, mark, timeout, self.abort)
        if reply_msg is None:
            self.check_abort()
            raise Failed(
                f"no reply to {codes.name_of(code)} within {timeout:g} s",
                expected=f"reply 0x{want:04X}",
                actual="nothing",
            )
        return reply_msg
