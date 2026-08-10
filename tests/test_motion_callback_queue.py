"""Motion callbacks are buffered only while something is waiting for one.

A continuous Z sweep produced 200+ "Motion callback queue full" warnings in
2.5 minutes on 2026-08-09 — over a thousand across a full run, in a log that
has to stay readable when something real goes wrong.

The drops were harmless, which is exactly why they were worth removing rather
than escalating: an unarmed ``_wait_async`` discards every queued callback
before it starts listening, so anything buffered outside a wait is thrown away
regardless. Filling a 100-slot queue with messages destined for the bin, and
reporting each overflow, is pure noise.

What survives is the case that genuinely matters: the queue overflowing *while*
a wait is in progress, which means a motion-complete signal may have been lost
while someone was listening for it. That still warns, and now says so.

Then those same callbacks turned out to be the fix for something much larger.
The LED Overview's Z sweep was polling STAGE_POSITION_GET every 10 ms to learn
that the stage had arrived — flooding the command socket, timing out, and
burning a full settle timeout on every plane (~25 s/tile) — while the stage was
announcing each of those arrivals on this very queue. So the sweep now waits on
the callback, which requires ``arm()``: a short Z step can complete before the
caller reaches the wait, and an unarmed queue would drop the completion and then
wait out the timeout anyway. Arm, move, wait.

Run: QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest \
        tests/test_motion_callback_queue.py -q
"""

import queue
import threading

import pytest


class _Msg:
    def __init__(self, status_code=1, int32_data0=255):
        self.status_code = status_code
        self.int32_data0 = int32_data0


def _tracker():
    pytest.importorskip("PyQt5")
    from py2flamingo.controllers.motion_tracker import MotionTracker

    t = MotionTracker.__new__(MotionTracker)
    t._callback_queue = queue.Queue(maxsize=100)
    t._queue_full_count = 0
    t._lock = threading.Lock()
    t._is_moving = False
    t._wait_active = False
    import logging

    t.logger = logging.getLogger("test.motion_tracker")
    return t


class TestCallbacksAreIgnoredOutsideAWait:
    def test_nothing_is_queued_when_no_one_is_waiting(self):
        t = _tracker()
        for _ in range(500):
            t._on_motion_stopped_callback(_Msg())
        assert t._callback_queue.qsize() == 0

    def test_no_overflow_warning_is_produced_outside_a_wait(self):
        """This is the 200-warnings-in-2.5-minutes case."""
        t = _tracker()
        for _ in range(500):
            t._on_motion_stopped_callback(_Msg())
        assert t._queue_full_count == 0

    def test_callbacks_are_queued_while_a_wait_is_active(self):
        t = _tracker()
        with t._lock:
            t._wait_active = True
        t._on_motion_stopped_callback(_Msg())
        assert t._callback_queue.qsize() == 1

    def test_overflow_during_a_wait_still_warns(self):
        """A lost signal while someone is listening is a real problem."""
        t = _tracker()
        with t._lock:
            t._wait_active = True
        for _ in range(150):
            t._on_motion_stopped_callback(_Msg())
        assert t._callback_queue.qsize() == 100
        assert t._queue_full_count == 50


class TestTheWaitWindowIsClosedOnEveryPath:
    """A flag left set would keep queueing; left clear would hang every wait."""

    def _source(self):
        from pathlib import Path

        return (
            Path(__file__).resolve().parents[1]
            / "src/py2flamingo/controllers/motion_tracker.py"
        ).read_text(encoding="utf-8")

    def test_the_flag_is_cleared_in_a_finally_block(self):
        src = self._source()
        i = src.index("finally:")
        tail = src[i : i + 200]
        assert "_wait_active = False" in tail, (
            "clearing the flag anywhere but a finally means a timeout or an "
            "exception leaves the queue permanently armed"
        )

    def test_the_flag_starts_false(self):
        assert _tracker()._wait_active is False

    def test_the_flag_is_set_before_the_wait_loop(self):
        src = self._source()
        assert "_wait_active = True" in src


class TestTheStaleDrainIsWhyThisIsSafe:
    """If a wait ever consumed pre-wait callbacks, dropping them would matter."""

    def test_an_unarmed_wait_discards_anything_queued_before_it_started(self):
        """The premise of not buffering outside a wait: it gets binned anyway."""
        t = _tracker()
        t._setup_async_callback = lambda: None
        with t._lock:
            t._wait_active = False
        t._callback_queue.put_nowait(_Msg(status_code=1))
        t._callback_queue.put_nowait(_Msg(status_code=1))

        t._stop_waiting = False
        # No fresh callback will arrive, so a wait that honoured the two stale
        # messages would return True immediately. It must time out instead.
        assert t._wait_async(timeout=0.05, allow_cancel=False) is False
        assert t._callback_queue.qsize() == 0


class TestArmingClosesTheShortMoveRace:
    """A 250 um Z step can finish before the caller reaches the wait.

    The queue only buffers while armed, so without arm() that completion is
    dropped and the wait sits out its whole timeout — turning the callback path
    into a guaranteed 2 s stall per plane, i.e. worse than the polling it
    replaced. Arm first, then move, then wait.
    """

    def test_arm_buffers_a_callback_that_lands_before_the_wait(self):
        t = _tracker()
        t._setup_async_callback = lambda: None

        t.arm()
        # Stage arrives here — after the move was sent, before the wait begins.
        t._on_motion_stopped_callback(_Msg(status_code=1))
        assert t._callback_queue.qsize() == 1, "arm() must buffer"

        t._stop_waiting = False
        assert t._wait_async(timeout=0.05, allow_cancel=False) is True

    def test_an_armed_wait_does_not_bin_the_event_it_is_waiting_for(self):
        """The regression this guards: draining unconditionally after arming."""
        t = _tracker()
        t._setup_async_callback = lambda: None
        t.arm()
        t._on_motion_stopped_callback(_Msg(status_code=1))

        t._stop_waiting = False
        # Timeout far shorter than any real move: only the already-buffered
        # callback can satisfy this.
        assert t._wait_async(timeout=0.05, allow_cancel=False) is True

    def test_arm_is_idempotent_and_keeps_what_is_buffered(self):
        t = _tracker()
        t._setup_async_callback = lambda: None
        t.arm()
        t._on_motion_stopped_callback(_Msg(status_code=1))
        t.arm()
        assert t._callback_queue.qsize() == 1

    def test_arm_drains_callbacks_from_a_previous_plane(self):
        """Each plane re-arms; the previous plane's arrival must not satisfy it."""
        t = _tracker()
        t._setup_async_callback = lambda: None
        t.arm()
        t._on_motion_stopped_callback(_Msg(status_code=1))
        t.disarm()  # plane done

        t.arm()  # next plane
        assert t._callback_queue.qsize() == 0, (
            "a stale arrival left in the queue would let the next plane's wait "
            "return before the stage has moved, restoring the motion blur that "
            "the settle wait exists to prevent"
        )

    def test_disarm_stops_buffering(self):
        t = _tracker()
        t._setup_async_callback = lambda: None
        t.arm()
        t.disarm()
        for _ in range(200):
            t._on_motion_stopped_callback(_Msg())
        assert t._callback_queue.qsize() == 0

    def test_disarm_is_safe_when_never_armed(self):
        t = _tracker()
        t.disarm()
        assert t._wait_active is False


class TestTheZSweepUsesCallbacksNotPolling:
    """~25 s/tile on 2026-08-09 was per-plane STAGE_POSITION_GET polling.

    Each poll is a round-trip on the command socket the LED preview is already
    saturating; the polls timed out, the tolerance was never confirmed, and every
    plane burned its full settle timeout. The stage announces arrival on its own
    (0x6010) — those callbacks were the thing flooding the queue. Listen instead
    of asking.
    """

    def _source(self):
        from pathlib import Path

        return (
            Path(__file__).resolve().parents[1]
            / "src/py2flamingo/workflows/led_2d_overview_workflow.py"
        ).read_text(encoding="utf-8")

    def test_capture_plane_no_longer_polls_position_every_10ms(self):
        src = self._source()
        i = src.index("def _capture_plane")
        body = src[i : src.index("def ", i + 10)]
        assert "poll_interval_s=0.01" not in body, (
            "a 10 ms poll interval on a network round-trip is not a poll "
            "interval, it is a flood"
        )

    def test_capture_plane_arms_before_it_moves(self):
        """Parsed, not grepped — the docstring quotes the old code verbatim."""
        import ast

        tree = ast.parse(self._source())
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_capture_plane"
        )
        arm_lines = [
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "arm"
        ]
        move_lines = [
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "move_to_position"
        ]
        assert arm_lines, "_capture_plane never arms the tracker"
        assert move_lines, "_capture_plane never moves Z"
        assert min(arm_lines) < min(
            move_lines
        ), "arming after the move re-opens the short-move race"

    def test_the_fallback_tolerance_is_not_finer_than_the_readback(self):
        src = self._source()
        i = src.index("def _wait_for_z_arrival")
        body = src[i : src.index("def ", i + 10)]
        assert "tolerance_mm=0.002" not in body, (
            "2 um was below what the position readback resolves, so the poll "
            "loop could never confirm arrival and always ran to timeout"
        )
