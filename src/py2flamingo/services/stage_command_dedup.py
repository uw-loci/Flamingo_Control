"""Drop a stage command that repeats one the stage is still executing.

The server log of 2026-09-03 (`OtherDocuments/ControlSystem.txt`) shows **56 of
551 "stage set position" commands (10%) were byte-identical repeats of the
command immediately before them** -- same axis, same value, all from client 24,
which is us. The gaps run 105-372 ms, median 224 ms: far too slow for a signal
loop and far too fast for a person deciding something, which is the signature of
one intent producing two UI events.

The server's reaction is the damage. Each repeat arrives while the axis is still
moving, so `PIStageBase` tears down the in-flight motion thread and starts
another -- 1469 `threadMotionTerminate` / `waitForMotionStopThread started`
pairs for 578 position commands. The stage is repeatedly interrupted before it
can settle.

**Why the existing guards do not catch it.**

`SampleView._send_position_command` already has a no-op guard, added for exactly
this reason. It compares the target against ``last_stage_position`` -- the
*cached* position, which only refreshes when the hardware reports motion
complete. Stage moves are asynchronous. So during an in-flight move, which is
the only window where a duplicate does harm, the cache still holds the old
position and the guard waves the repeat through.

``PositionController._movement_lock`` does not catch it either: ``move_x/y/z``
release it in a ``finally`` as soon as the *send* returns, not when the motion
*completes*. `move_to_position` is the exception -- it holds the lock across its
wait thread.

**Why this lives here rather than in the GUI.** The same two emitters the motion
gate had to cover, for the same reason -- and fixing it in the widgets would mean
one copy per control, which is how the tile-step calculation ended up with five.

* ``PositionController._move_axis`` -- funnel for move_x/y/z/r and
  move_to_position.
* ``StageService.move_to_position`` -- used DIRECTLY by
  ``workflow_queue_service`` and ``led_2d_overview_workflow``, which never touch
  the controllers.

**What it deliberately does not do.** It suppresses a command only while an
identical one for that axis is still in flight. Once the stage reports
STAGE_MOTION_STOPPED the memory is cleared, so re-commanding the same position
later -- after drift, after a failed move, or just because the user asked again
-- always reaches the hardware. Blocking those would be a worse bug than the one
being fixed: a stage that ignores a move request is indistinguishable from a
broken stage.

A different value for the same axis is never suppressed. That is a genuine
change of intent and the server is entitled to interrupt its own motion for it.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Tolerance for "the same position". The stage reports to 3 decimal places
# (micrometres) and the workflow format writes 3, so anything closer than this
# is the same command expressed twice, not a finer move.
POSITION_EPSILON_MM = 1e-4

_lock = threading.Lock()
_in_flight: Dict[int, float] = {}
_suppressed_count = 0


def should_send(axis_code: int, value: float) -> bool:
    """Should this stage command go to the hardware?

    Returns False only when an identical command for the same axis is still in
    flight. Records the command as in flight when it returns True.

    Args:
        axis_code: 1=X, 2=Y, 3=Z, 4=R
        value: target position (mm, or degrees for R)
    """
    global _suppressed_count
    with _lock:
        previous = _in_flight.get(axis_code)
        if previous is not None and abs(previous - value) < POSITION_EPSILON_MM:
            _suppressed_count += 1
            logger.info(
                f"Skipping duplicate stage command: axis {axis_code} is already "
                f"moving to {value:.4f}. The stage is still executing the "
                f"identical command, and re-sending it would make the server "
                f"tear down the in-flight motion and start over "
                f"({_suppressed_count} suppressed this session)."
            )
            return False
        _in_flight[axis_code] = float(value)
        return True


def note_motion_stopped(axis_code: Optional[int] = None) -> None:
    """The stage has stopped, so nothing is in flight any more.

    The server reports STAGE_MOTION_STOPPED with axis 255 when the move was
    asynchronous, meaning "whatever was moving has stopped" rather than naming
    an axis -- so the default clears every axis. That is the safe direction: a
    cleared memory can only allow a command through, never block one.
    """
    with _lock:
        if axis_code is None or axis_code not in _in_flight:
            _in_flight.clear()
        else:
            _in_flight.pop(axis_code, None)


def suppressed_count() -> int:
    """How many duplicates have been dropped, for tests and diagnostics."""
    with _lock:
        return _suppressed_count


def reset() -> None:
    """Forget all state. For tests, and for a fresh connection."""
    global _suppressed_count
    with _lock:
        _in_flight.clear()
        _suppressed_count = 0
