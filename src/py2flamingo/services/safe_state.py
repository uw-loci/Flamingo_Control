"""Stop commands that must go out at once, and the safe state built from them.

Every command here is sent fire-and-forget on the command socket that is open
now. Nothing here reconnects, retries or waits for a reply: an emergency stop
that first spends seconds reconnecting, or waits 3 s for an ack the server may
never send, is not an emergency stop. Each send reports whether its bytes left
this computer, which is all a client can know without a reply.

``safe_state()`` is the one place that turns the instrument's emitters off
(and, when asked, stops the stage and any server workflow). ``leave_safe()``
decides how much of it to send when the client lets go of the instrument
(window close, application quit, disconnect, an uncaught exception); see its
docstring for the rule. The verification runner has its own copy
(``verification/runner.py``), which resolves codes by name for the build
under test.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional

logger = logging.getLogger(__name__)

# Codes outside the COMMON block keep their numbers in every known server
# build (see verification/codes.py FIXED).
STAGE_HALT = 0x6003
CAMERA_WORK_FLOW_STOP = 0x3005
CAMERA_LIVE_VIEW_STOP = 0x3008
LASER_DISABLE_ALL = 0x2007
LED_PREVIEW_DISABLE = 0x4003

#: int32Data0 for HALT: every axis. 0 is STAGE_AXIS_NONE and the server drops
#: it (StageControl::exeCommandValidAxis).
STAGE_AXIS_ALL = 0xFF
#: The most laser lines any Flamingo server knows.
ALL_LASER_LINES = tuple(range(1, 8))
TRIGGER_CALL_BACK = 0x80000000
#: How long a stop waits for another thread's 128-byte send to finish before
#: sending anyway. The lock is held only for one sendall.
SEND_LOCK_WAIT_S = 0.5


def _send_lock(connection):
    tcp = getattr(connection, "tcp_connection", None)
    client = getattr(tcp, "_command_client", None)
    return getattr(client, "_send_lock", None)


def send_now(connection, code: int, int0: int = 0, name: str = "") -> bool:
    """Send one command on the open command socket. True if it was sent.

    Never reconnects and never waits for a reply. Returns False (and logs)
    when there is no open socket or the send raised.
    """
    label = name or f"0x{code:04X}"
    if connection is None:
        logger.error(f"{label} NOT sent: no connection")
        return False
    sock = getattr(connection, "_command_socket", None)
    if sock is None:
        logger.error(f"{label} NOT sent: the command socket is not open")
        return False
    try:
        packet = connection.encoder.encode_command(
            code=code,
            status=0,
            params=[0, 0, 0, int0, 0, 0, TRIGGER_CALL_BACK],
            value=0.0,
            data=b"",
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"{label} NOT sent: could not encode it: {e}")
        return False
    lock = _send_lock(connection)
    locked = False
    try:
        if lock is not None:
            locked = lock.acquire(timeout=SEND_LOCK_WAIT_S)
            if not locked:
                logger.warning(
                    f"{label}: another send held the socket for "
                    f"{SEND_LOCK_WAIT_S} s; sending anyway"
                )
        sock.sendall(packet)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(f"{label} NOT sent: {type(e).__name__}: {e}")
        return False
    finally:
        if locked:
            lock.release()


def halt_all_axes(connection) -> bool:
    """Send STAGE_HALT for every axis. True if it was sent."""
    return send_now(connection, STAGE_HALT, STAGE_AXIS_ALL, "STAGE_HALT(all axes)")


def stop_workflow(connection) -> bool:
    """Send CAMERA_WORK_FLOW_STOP. True if it was sent."""
    return send_now(connection, CAMERA_WORK_FLOW_STOP, 0, "WORKFLOW_STOP")


@dataclass
class SafeStateResult:
    """What ``safe_state`` sent and what it could not send."""

    reason: str
    sent: List[str] = field(default_factory=list)
    not_sent: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.not_sent


def safe_state(
    connection,
    reason: str,
    halt: bool = False,
    workflow_stop: bool = False,
    laser_lines: Optional[Iterable[int]] = None,
) -> SafeStateResult:
    """Turn every emitter off, and optionally stop the stage and the workflow.

    Order: HALT (if ``halt``), WORKFLOW_STOP (if ``workflow_stop``), live view
    stop, LASER_DISABLE_ALL once per line in ``laser_lines`` (default 1-7;
    servers before the 2026-11-20 build act only when int32Data0 names a line
    of that engine), LED off. Each command is sent on its own, fire-and-forget,
    so one failure does not stop the rest. Never raises.
    """
    result = SafeStateResult(reason=reason)
    lines = sorted(set(laser_lines or ())) or list(ALL_LASER_LINES)
    sends = []
    if halt:
        sends.append(("STAGE_HALT(all axes)", STAGE_HALT, STAGE_AXIS_ALL))
    if workflow_stop:
        sends.append(("WORKFLOW_STOP", CAMERA_WORK_FLOW_STOP, 0))
    sends.append(("LIVE_VIEW_STOP", CAMERA_LIVE_VIEW_STOP, 0))
    sends += [(f"LASER_DISABLE_ALL({n})", LASER_DISABLE_ALL, n) for n in lines]
    sends.append(("LED_PREVIEW_DISABLE", LED_PREVIEW_DISABLE, 0))
    for name, code, int0 in sends:
        try:
            ok = send_now(connection, code, int0, name)
        except Exception as e:  # noqa: BLE001 - must keep going
            logger.error(f"safe state: {name} raised {e}")
            ok = False
        (result.sent if ok else result.not_sent).append(name)
    if result.not_sent:
        logger.error(
            f"Safe state ({reason}): NOT sent {', '.join(result.not_sent)}"
            + (f"; sent {', '.join(result.sent)}" if result.sent else "")
        )
    else:
        logger.warning(f"Safe state ({reason}): sent {', '.join(result.sent)}")
    return result


# --------------------------------------------------------------------------
# Leaving the instrument: what to send on close, quit, disconnect or crash
# --------------------------------------------------------------------------


@dataclass
class Activity:
    """What the client knows is running when it lets go of the instrument."""

    #: A client-side acquisition (LED overview, tile collection, the workflow
    #: queue): the client is driving the stage and the light.
    client_acquisition: bool = False
    #: A server workflow this client started and has not seen finish.
    server_workflow: bool = False
    #: The laser lines this scope has (empty: unknown, 1-7 is used).
    laser_lines: List[int] = field(default_factory=list)


_activity_probe: Optional[Callable[[], Activity]] = None


def set_activity_probe(probe: Optional[Callable[[], Activity]]) -> None:
    """Register how ``leave_safe`` learns what is running (the application)."""
    global _activity_probe
    _activity_probe = probe


def _activity() -> Activity:
    if _activity_probe is None:
        return Activity()
    try:
        return _activity_probe()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Could not tell what is running ({e}); turning lights off")
        return Activity()


def leave_safe(connection, reason: str, once: bool = True) -> Optional[SafeStateResult]:
    """Put the instrument in a safe state as this client lets go of it.

    * A client acquisition is running: HALT, WORKFLOW_STOP and lights off.
      Nobody will be driving the stage or the light any more.
    * Only a server workflow is running (no client acquisition): nothing is
      sent. The workflow owns the stage and the lasers and may rightly keep
      running after the client closes; turning its light off would ruin it.
    * Otherwise: lights off (live view stop, every laser line, LED). No HALT:
      a move the client sent was checked against the limits and may finish.

    With ``once`` (the close, quit and disconnect paths, which follow one
    another on exit) it is sent once per open socket. Returns None when
    nothing was sent.
    """
    sock = getattr(connection, "_command_socket", None) if connection else None
    if sock is None:
        logger.info(f"Leaving ({reason}): no open command socket, nothing to send")
        return None
    if once and getattr(connection, "_left_safe_socket", None) is sock:
        return None
    activity = _activity()
    if activity.server_workflow and not activity.client_acquisition:
        logger.warning(
            f"Leaving ({reason}) while a server workflow runs: stage and lights "
            "are left to the workflow (no HALT, no light off)."
        )
        result = None
    else:
        full = activity.client_acquisition
        result = safe_state(
            connection,
            reason,
            halt=full,
            workflow_stop=full,
            laser_lines=activity.laser_lines,
        )
    if once:
        try:
            connection._left_safe_socket = sock
        except Exception:  # noqa: BLE001 - a connection that takes no attributes
            pass
    return result


def install_excepthook(get_connection: Callable[[], object]) -> Callable:
    """Install a ``sys.excepthook`` that logs, leaves the instrument safe, chains.

    Under PyQt5 an exception that escapes a slot aborts the process with no
    cleanup when no hook is installed. This hook logs the exception, runs
    :func:`leave_safe` (with the same rule as a close; a running client
    acquisition is also cancelled through the emergency-stop hooks), then
    calls the hook that was installed before. Because a hook is installed,
    PyQt no longer aborts: the application keeps running with the light off.

    Returns the previous hook.
    """
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        try:
            logging.getLogger("py2flamingo").critical(
                "Uncaught exception; putting the instrument in a safe state",
                exc_info=(exc_type, exc, tb),
            )
            if _activity().client_acquisition:
                from py2flamingo.services import stage_motion_gate

                stage_motion_gate.run_stop_hooks()
            connection = get_connection()
            if connection is not None:
                # The application keeps running, so this is not the last word
                # on this socket: a later close must still send its own.
                leave_safe(connection, f"uncaught {exc_type.__name__}", once=False)
        except Exception as e:  # noqa: BLE001 - the hook must not raise
            logger.error(f"Safe state from the exception hook failed: {e}")
        finally:
            previous(exc_type, exc, tb)

    sys.excepthook = hook
    return previous
