"""Tests for interaction-aware error-notification gating.

Policy: an error push is suppressed ONLY when the error is the immediate result
of the operator interacting with the screen (they see it there). Background
failures and errors during an unattended acquisition still push.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_interaction_notify_gate.py -q
"""

import logging

from py2flamingo.application import _INTERACTION_SUPPRESS_WINDOW_S, FlamingoApplication
from py2flamingo.services.interaction_tracker import InteractionTracker


class _FakeEvent:
    def __init__(self, etype):
        self._t = etype

    def type(self):
        return self._t


class _Tracker:
    """Stand-in exposing a fixed seconds_since_interaction()."""

    def __init__(self, seconds):
        self._seconds = seconds

    def seconds_since_interaction(self):
        return self._seconds


def _record():
    return logging.LogRecord(
        name="py2flamingo.visualization.session_manager",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="boom",
        args=(),
        exc_info=None,
    )


def _gate_with(queue_running=False, executing=False, since=float("inf")):
    app = FlamingoApplication.__new__(FlamingoApplication)
    app.workflow_queue_service = type("Q", (), {"_is_running": queue_running})()
    app.workflow_controller = type("W", (), {"is_executing": executing})()
    app._interaction_tracker = _Tracker(since)
    return app._error_notify_gate(_record())


# ---- gate policy -----------------------------------------------------------


def test_recent_interaction_suppresses_when_idle():
    # Operator clicked 1s ago and no acquisition running -> they see it -> no push.
    assert _gate_with(since=1.0) is False


def test_old_interaction_still_notifies():
    # Last click was long ago -> a background/idle error should push.
    assert _gate_with(since=_INTERACTION_SUPPRESS_WINDOW_S + 5.0) is True


def test_no_interaction_ever_notifies():
    assert _gate_with(since=float("inf")) is True


def test_acquisition_running_always_notifies_even_if_just_clicked():
    assert _gate_with(queue_running=True, since=0.5) is True
    assert _gate_with(executing=True, since=0.5) is True


def test_missing_tracker_notifies():
    app = FlamingoApplication.__new__(FlamingoApplication)
    app.workflow_queue_service = None
    app.workflow_controller = None
    app._interaction_tracker = None
    assert app._error_notify_gate(_record()) is True


# ---- InteractionTracker ----------------------------------------------------


def test_tracker_starts_with_no_interaction():
    t = InteractionTracker()
    assert t.seconds_since_interaction() == float("inf")


def test_tracker_records_press_event():
    from PyQt5.QtCore import QEvent

    t = InteractionTracker()
    t.eventFilter(None, _FakeEvent(QEvent.MouseButtonPress))
    assert t.seconds_since_interaction() < 1.0


def test_tracker_ignores_mouse_move():
    from PyQt5.QtCore import QEvent

    t = InteractionTracker()
    t.eventFilter(None, _FakeEvent(QEvent.MouseMove))
    assert t.seconds_since_interaction() == float("inf")


def test_tracker_never_consumes_events():
    from PyQt5.QtCore import QEvent

    t = InteractionTracker()
    assert t.eventFilter(None, _FakeEvent(QEvent.KeyPress)) is False
    assert t.eventFilter(None, _FakeEvent(QEvent.MouseMove)) is False
