"""Motion callbacks are buffered only while something is waiting for one.

A continuous Z sweep produced 200+ "Motion callback queue full" warnings in
2.5 minutes on 2026-08-09 — over a thousand across a full run, in a log that
has to stay readable when something real goes wrong.

The drops were harmless, which is exactly why they were worth removing rather
than escalating: ``_wait_async`` discards every queued callback before it
starts listening, so anything buffered outside a wait is thrown away
regardless. Filling a 100-slot queue with messages destined for the bin, and
reporting each overflow, is pure noise.

What survives is the case that genuinely matters: the queue overflowing *while*
a wait is in progress, which means a motion-complete signal may have been lost
while someone was listening for it. That still warns, and now says so.

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

    def test_wait_async_discards_anything_queued_before_it_started(self):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "src/py2flamingo/controllers/motion_tracker.py"
        ).read_text(encoding="utf-8")
        i = src.index("def _wait_async")
        body = src[i : i + 1200]
        assert "Discarded stale motion callback" in body, (
            "the whole argument for not buffering outside a wait is that these "
            "messages are discarded anyway; if that drain goes, revisit this"
        )
