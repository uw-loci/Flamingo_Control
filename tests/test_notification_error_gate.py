"""Tests for the NtfyLogHandler error-notify gate.

The root-logger ntfy handler captures every logger.error in the app. To avoid
push noise from interactive errors the operator can already see on screen (e.g.
"failed to load stitched data"), a gate predicate restricts pushes to times when
an acquisition is actually running. These tests verify the gate is honoured.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_notification_error_gate.py -q
"""

import logging

from py2flamingo.services.notification_service import NotificationService


class _Settings:
    def __init__(self, d):
        self._d = d

    def get_setting(self, key, default):
        return self._d.get(key, default)


def _service(monkeypatch):
    svc = NotificationService(
        _Settings(
            {
                "notifications.enabled": True,
                "notifications.events.errors": True,
                "notifications.ntfy_url": "https://ntfy.sh/topic",
            }
        )
    )
    sent = []
    monkeypatch.setattr(
        svc,
        "notify",
        lambda *a, **k: sent.append((a, k)) or True,
    )
    return svc, sent


def _record(msg="boom"):
    return logging.LogRecord(
        name="py2flamingo.visualization.session_manager",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_gate_false_suppresses_notification(monkeypatch):
    svc, sent = _service(monkeypatch)
    handler = svc.make_log_handler(gate=lambda record: False)
    handler.emit(_record())
    assert sent == []  # gated off -> nothing sent


def test_gate_true_allows_notification(monkeypatch):
    svc, sent = _service(monkeypatch)
    handler = svc.make_log_handler(gate=lambda record: True)
    handler.emit(_record())
    assert len(sent) == 1
    assert sent[0][0][0] == "errors"  # event_key


def test_no_gate_still_notifies(monkeypatch):
    svc, sent = _service(monkeypatch)
    handler = svc.make_log_handler()  # no gate -> unchanged behaviour
    handler.emit(_record())
    assert len(sent) == 1


def test_gate_exception_suppresses_safely(monkeypatch):
    svc, sent = _service(monkeypatch)

    def _boom(record):
        raise RuntimeError("bad gate")

    handler = svc.make_log_handler(gate=_boom)
    handler.emit(_record())  # must not raise
    assert sent == []


def test_gate_not_consulted_for_ignored_loggers(monkeypatch):
    svc, sent = _service(monkeypatch)
    calls = []
    handler = svc.make_log_handler(gate=lambda record: calls.append(1) or True)
    rec = logging.LogRecord(
        name="urllib3.connectionpool",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="noise",
        args=(),
        exc_info=None,
    )
    handler.emit(rec)
    assert sent == []  # ignored logger -> never sent
    assert calls == []  # and the gate isn't even consulted
