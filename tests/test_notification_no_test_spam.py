"""The ntfy push-log handler must not spam a real topic during tests.

Two guarantees, after the fix in application._wire_notification_hooks:

  1. Under pytest, no NtfyLogHandler is attached to the root logger — so the
     errors the suite deliberately logs (mock objects, "Test error", missing
     events) never reach a configured ntfy topic.
  2. Installation is idempotent: any NtfyLogHandler left on the root logger by a
     previous app construction is removed first, so a single logger.error()
     can't fire N accumulated handlers → N duplicate phone pushes.
"""

import logging
from unittest.mock import MagicMock

import pytest

from py2flamingo.application import FlamingoApplication


class _FakeNtfyHandler(logging.Handler):
    """Stand-in whose class name matches the real handler for the dedup check."""

    def emit(self, record):  # pragma: no cover - never emits in the test
        pass


# The dedup matches on class name, so present as "NtfyLogHandler".
_FakeNtfyHandler.__name__ = "NtfyLogHandler"


def _count_ntfy_handlers(root):
    return sum(1 for h in root.handlers if h.__class__.__name__ == "NtfyLogHandler")


@pytest.fixture
def app(qapp):
    return FlamingoApplication()


def test_no_ntfy_handler_attached_under_pytest_and_dedup(app):
    root = logging.getLogger()
    # Simulate handlers left over by earlier app constructions.
    leftovers = [_FakeNtfyHandler() for _ in range(3)]
    for h in leftovers:
        root.addHandler(h)
    assert _count_ntfy_handlers(root) >= 3

    # Force a notification service so the wire path runs past the None guard,
    # and make it hand back a handler that WOULD be counted if wrongly attached.
    svc = MagicMock()
    svc.make_log_handler.return_value = _FakeNtfyHandler()
    app._notification_service = svc

    app._wire_notification_hooks()

    # Dedup removed the 3 leftovers; the pytest guard attached none.
    assert _count_ntfy_handlers(root) == 0
    assert app._notification_log_handler is None
    svc.make_log_handler.assert_not_called()

    # Clean up any stray fakes so we don't affect other tests.
    for h in list(root.handlers):
        if isinstance(h, _FakeNtfyHandler):
            root.removeHandler(h)
