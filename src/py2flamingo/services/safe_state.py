"""Stop commands that must go out at once, and the safe state built from them.

Every command here is sent fire-and-forget on the command socket that is open
now. Nothing here reconnects, retries or waits for a reply: an emergency stop
that first spends seconds reconnecting, or waits 3 s for an ack the server may
never send, is not an emergency stop. Each send reports whether its bytes left
this computer, which is all a client can know without a reply.

``safe_state()`` is the one place that turns the instrument's emitters off
(and, when asked, stops the stage and any server workflow). It is called on
exit, on disconnect, from the exception hook and by the emergency stop. The
verification runner has its own copy (``verification/runner.py``), which
resolves codes by name for the build under test.
"""

from __future__ import annotations

import logging

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
