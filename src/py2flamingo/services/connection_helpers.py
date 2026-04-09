"""Shared helpers for ensuring connection health before sending commands.

The Flamingo Control app connects to the microscope over TCP. Connections
can silently die during idle periods (server timeout, other client taking
over, network blip). To handle this reactively — without an always-on
heartbeat that would hog the connection from other clients — every
hardware command is wrapped in an ``ensure_connected()`` check and an
OSError-catching retry.

Typical use inside a controller or service::

    from py2flamingo.services.connection_helpers import with_connection_retry

    def move_x(self, delta: float):
        def _do_move():
            return self._position_controller.move_axis("x", delta)

        with_connection_retry(
            self._connection_service,
            _do_move,
            action_description=f"move X by {delta}",
        )

If the connection is already dead when the command is about to run,
``ensure_connected()`` attempts a single reconnect. If the command
itself fails with a socket error (OSError / BrokenPipeError /
ConnectionResetError), ``with_connection_retry`` attempts one reconnect
and retries the command once. A final failure is raised as
``ConnectionError`` so callers can distinguish connectivity problems
from other exceptions.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def ensure_connected(
    connection_service: Any, action_description: str = "command"
) -> bool:
    """Verify the connection is alive; reconnect once if not.

    Args:
        connection_service: MVCConnectionService instance exposing
            ``is_connected()`` and ``reconnect_last()``.
        action_description: Short human-readable description of the
            pending action, used in log messages.

    Returns:
        True if the connection is usable (possibly after a successful
        reconnect), False if reconnect failed.
    """
    try:
        if connection_service.is_connected():
            return True
    except Exception as e:
        logger.warning(
            f"ensure_connected: is_connected() raised ({e}); "
            f"treating as disconnected"
        )

    logger.warning(
        f"Connection down before {action_description} — attempting reconnect..."
    )
    try:
        if connection_service.reconnect_last():
            logger.info(f"Reconnect successful, proceeding with {action_description}")
            return True
    except Exception as e:
        logger.error(
            f"reconnect_last() raised while preparing for {action_description}: {e}",
            exc_info=True,
        )
        return False

    logger.error(
        f"Reconnect failed — cannot execute {action_description}. "
        f"User must reconnect manually."
    )
    return False


def with_connection_retry(
    connection_service: Any,
    action: Callable[[], T],
    action_description: str = "command",
    retry_on_error: bool = True,
) -> Optional[T]:
    """Run ``action()`` with a pre-flight check and single post-error retry.

    Behavior:
        1. Pre-flight: call ``ensure_connected()``. If it returns False,
           raise ``ConnectionError`` without invoking ``action``.
        2. Call ``action()``. If it succeeds, return the result.
        3. If ``action()`` raises a socket-related error (OSError,
           BrokenPipeError, ConnectionResetError) and ``retry_on_error``
           is True: mark the connection lost, attempt a single reconnect,
           and retry ``action()`` once.
        4. A final failure raises ``ConnectionError`` (chained from the
           original exception) so callers can distinguish connectivity
           problems from other failures.

    Args:
        connection_service: MVCConnectionService instance.
        action: Zero-arg callable that sends the command. Should either
            return normally on success or raise on failure.
        action_description: Short description for logs and error messages.
        retry_on_error: If True, attempt reconnect + retry on socket errors.

    Returns:
        The return value of ``action()`` on success, or None if the action
        itself returns None.

    Raises:
        ConnectionError: If the pre-flight check fails or the post-error
            retry (and reconnect) fails.
        Exception: Any non-socket exception raised by ``action`` is
            propagated unchanged.
    """
    if not ensure_connected(connection_service, action_description):
        raise ConnectionError(
            f"Cannot execute {action_description}: connection is down and "
            f"reconnect failed. Click Reconnect or check the microscope."
        )

    try:
        return action()
    except (OSError, BrokenPipeError, ConnectionResetError) as e:
        if not retry_on_error:
            raise

        logger.warning(
            f"{action_description} failed with socket error ({type(e).__name__}: "
            f"{e}) — attempting reconnect + retry"
        )

        # Mark connection as lost so reconnect_last() starts from a clean
        # state. We route through _on_tcp_connection_lost if the service
        # exposes it, otherwise just call reconnect_last() directly.
        handler = getattr(connection_service, "_on_tcp_connection_lost", None)
        if callable(handler):
            try:
                handler(f"caught {type(e).__name__}")
            except Exception as handler_e:
                logger.debug(
                    f"Ignoring error from _on_tcp_connection_lost handler: "
                    f"{handler_e}"
                )

        try:
            if not connection_service.reconnect_last():
                raise ConnectionError(
                    f"Reconnect failed after {action_description} socket error: {e}"
                ) from e
        except ConnectionError:
            raise
        except Exception as reconnect_e:
            raise ConnectionError(
                f"reconnect_last() raised while recovering from "
                f"{action_description}: {reconnect_e}"
            ) from e

        logger.info(f"Reconnect successful, retrying {action_description}")
        try:
            return action()
        except Exception as retry_e:
            raise ConnectionError(
                f"{action_description} failed again after reconnect: {retry_e}"
            ) from retry_e
