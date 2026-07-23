"""Regression test: a stage position-poll timeout must not escalate to ntfy.

During an acquisition (e.g. an LED 2D overview) the client polls the hardware
stage position while the server is busy servicing the workflow. Those
``STAGE_POSITION_GET`` queries legitimately time out, and the caller already
treats a ``None`` return as harmless. The bug was that the underlying async
send logged the timeout at ERROR level, and the app promotes ERROR log records
to high-priority ntfy push notifications — so every poll interval produced a
"Flamingo: Error in microscope_command_service" push during a normal overview.

The fix threads a ``benign_timeout`` flag from ``get_axis_position`` down to the
send path so an expected poll miss logs at WARNING (below the ntfy threshold)
instead of ERROR. This test locks that behaviour in.
"""

import logging
from unittest.mock import Mock

from py2flamingo.services.stage_service import StageService


def _make_stage_service_that_times_out() -> StageService:
    """A StageService whose async send always reports a timeout (None)."""
    connection = Mock()
    connection.is_connected.return_value = True
    connection.has_async_reader = True
    connection.encoder.encode_command.return_value = b"\x00" * 128
    # Async reader returns None => timeout in _send_via_async_reader.
    connection.send_command_async.return_value = None
    return StageService(connection)


def test_position_poll_timeout_logs_warning_not_error(caplog):
    """A position query timeout is logged at WARNING, never ERROR."""
    service = _make_stage_service_that_times_out()

    with caplog.at_level(logging.WARNING):
        result = service.get_axis_position(1)  # X axis

    # Benign: caller gets None back, not an exception.
    assert result is None

    # The async-timeout record must be WARNING (so the ntfy ERROR-gate skips it).
    timeout_records = [
        r
        for r in caplog.records
        if "Timeout waiting for" in r.getMessage() and "response" in r.getMessage()
    ]
    assert timeout_records, "expected a timeout log record"
    levels = [r.levelname for r in timeout_records]
    assert all(
        r.levelno == logging.WARNING for r in timeout_records
    ), f"position timeout must be WARNING, got {levels}"
    assert not any(
        r.levelno >= logging.ERROR for r in timeout_records
    ), "position poll timeout must not log at ERROR (would push to ntfy)"
