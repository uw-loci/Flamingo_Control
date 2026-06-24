"""Tests for NotificationService.notify_recovery.

A recovery/all-clear (e.g. successful reconnect after a connection loss) must be
gated by the same "errors" checkbox as the error alerts — so a user who gets the
error also gets the recovery — and tagged with a check mark.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_notification_recovery.py -q
"""

from py2flamingo.services.notification_service import NotificationService


class _Settings:
    def __init__(self, d):
        self._d = d

    def get_setting(self, key, default):
        return self._d.get(key, default)


def test_recovery_gated_off_when_notifications_disabled():
    svc = NotificationService(_Settings({"notifications.enabled": False}))
    assert svc.notify_recovery("Reconnected", "all good") is False


def test_recovery_gated_off_when_errors_event_disabled():
    svc = NotificationService(
        _Settings(
            {
                "notifications.enabled": True,
                "notifications.events.errors": False,
                "notifications.ntfy_url": "https://ntfy.sh/topic",
            }
        )
    )
    assert svc.notify_recovery("Reconnected", "all good") is False


def test_recovery_sends_under_errors_event_with_check_tag(monkeypatch):
    svc = NotificationService(
        _Settings(
            {
                "notifications.enabled": True,
                "notifications.events.errors": True,
                "notifications.ntfy_url": "https://ntfy.sh/topic",
            }
        )
    )
    captured = {}
    monkeypatch.setattr(
        svc,
        "_dispatch",
        lambda url, message, title, priority, tags: captured.update(
            url=url, message=message, title=title, tags=tags
        ),
    )
    assert svc.notify_recovery("Flamingo: reconnected", "restored") is True
    assert captured["title"] == "Flamingo: reconnected"
    assert captured["message"] == "restored"
    assert captured["tags"] == "white_check_mark"
