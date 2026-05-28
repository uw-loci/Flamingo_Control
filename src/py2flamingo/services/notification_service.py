"""ntfy.sh push-notification service.

Sends notifications to a user-configured ntfy topic URL when long-running
operations finish. Each event type (workflow queue, stitching item, stitching
batch, tile collection, benchmark, errors) is gated by its own checkbox in the
Settings → Notifications tab.

Settings shape (under the microscope settings JSON):

    "notifications": {
        "enabled": true,
        "ntfy_url": "https://ntfy.sh/your-topic",
        "events": {
            "workflow_queue_completed": true,
            "stitching_item_completed":  false,
            "stitching_batch_completed": true,
            "tile_collection_completed": true,
            "benchmark_completed":       false,
            "errors":                    true
        }
    }

Sends run on a background QThread so a slow network never stalls the UI.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from PyQt5.QtCore import QObject, QThread, pyqtSignal

DEFAULT_EVENTS = {
    "workflow_queue_completed": True,
    "stitching_item_completed": False,
    "stitching_batch_completed": True,
    "tile_collection_completed": True,
    "benchmark_completed": False,
    "errors": True,
}


class _NtfyPostThread(QThread):
    """One-shot background thread that POSTs a single notification."""

    sent = pyqtSignal(bool, str)  # success, message_or_error

    def __init__(
        self,
        url: str,
        message: str,
        title: Optional[str],
        priority: Optional[str],
        tags: Optional[str],
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._url = url
        self._message = message
        self._title = title
        self._priority = priority
        self._tags = tags

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                self._url,
                data=self._message.encode("utf-8"),
                method="POST",
            )
            if self._title:
                req.add_header("Title", self._title)
            if self._priority:
                req.add_header("Priority", self._priority)
            if self._tags:
                req.add_header("Tags", self._tags)
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            self.sent.emit(True, "ok")
        except urllib.error.URLError as e:
            self.sent.emit(False, f"network error: {e.reason}")
        except Exception as e:
            self.sent.emit(False, f"{type(e).__name__}: {e}")


class NotificationService(QObject):
    """Routes completion events to ntfy.sh, gated by per-event checkboxes.

    Read settings on every send so the user's checkbox/URL edits take effect
    immediately without restart.
    """

    def __init__(self, settings_service, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self._settings_service = settings_service
        self._threads: list[_NtfyPostThread] = []
        self._threads_lock = threading.Lock()

    def _get(self, key_path: str, default):
        if self._settings_service is None:
            return default
        return self._settings_service.get_setting(f"notifications.{key_path}", default)

    def is_event_enabled(self, event_key: str) -> bool:
        if not self._get("enabled", True):
            return False
        return bool(
            self._get(f"events.{event_key}", DEFAULT_EVENTS.get(event_key, False))
        )

    def get_ntfy_url(self) -> str:
        return (self._get("ntfy_url", "") or "").strip()

    def notify(
        self,
        event_key: str,
        title: str,
        message: str,
        priority: Optional[str] = None,
        tags: Optional[str] = None,
    ) -> bool:
        """Send a notification if the matching event checkbox is enabled.

        Returns True if a send was dispatched; False if gated off or no URL.
        """
        if not self.is_event_enabled(event_key):
            return False
        url = self.get_ntfy_url()
        if not url:
            self._logger.debug("ntfy URL not configured; skipping notification")
            return False
        self._dispatch(url, message, title, priority, tags)
        return True

    def send_test(self, url: str) -> "_NtfyPostThread":
        """Send a test notification to the given URL, bypassing checkbox gating.

        Returns the thread so a UI caller can connect to its `sent` signal.
        """
        thread = self._dispatch(
            url.strip(),
            "Test notification from Flamingo Control. If you see this, ntfy is wired up correctly.",
            title="Flamingo: test",
            priority="default",
            tags="microscope",
        )
        return thread

    def _dispatch(
        self,
        url: str,
        message: str,
        title: Optional[str],
        priority: Optional[str],
        tags: Optional[str],
    ) -> _NtfyPostThread:
        # No parent — notify() may be called from non-GUI threads (e.g. the
        # ntfy log handler). QObject parents must live in the same thread as
        # the child, and we don't want to force callers onto the GUI thread.
        thread = _NtfyPostThread(url, message, title, priority, tags)
        with self._threads_lock:
            self._threads.append(thread)
        thread.finished.connect(lambda t=thread: self._cleanup(t))
        thread.sent.connect(self._on_sent)
        thread.start()
        return thread

    def _cleanup(self, thread: _NtfyPostThread) -> None:
        with self._threads_lock:
            try:
                self._threads.remove(thread)
            except ValueError:
                pass
        thread.deleteLater()

    def _on_sent(self, success: bool, info: str) -> None:
        if success:
            self._logger.info("ntfy notification sent")
        else:
            self._logger.warning("ntfy notification failed: %s", info)

    def make_log_handler(
        self,
        level: int = logging.ERROR,
        min_interval_s: float = 10.0,
    ) -> "NtfyLogHandler":
        """Build a logging.Handler that forwards records to ntfy.

        Attaching this to the root logger captures every logger.error /
        logger.exception in the codebase — no per-call-site wiring needed.
        """
        return NtfyLogHandler(self, level=level, min_interval_s=min_interval_s)


class NtfyLogHandler(logging.Handler):
    """Promote log records (ERROR+ by default) to ntfy notifications.

    Cross-cutting error capture: any code that calls `logger.error(...)` or
    `logger.exception(...)` anywhere in the app produces a notification,
    gated by the "errors" checkbox in Settings → Notifications.

    Rate-limited so a tight error-logging loop can't flood the topic. Only
    skips the *send*; records still propagate to other handlers (console,
    file) at full rate.
    """

    # Loggers whose output should never trigger a notification — avoids
    # recursion (the service logs its own send failures) and noise from
    # third-party libraries that error-log routinely.
    _IGNORED_LOGGERS = (
        "py2flamingo.services.notification_service",
        "urllib3",
        "asyncio",
    )

    def __init__(
        self,
        notification_service: NotificationService,
        level: int = logging.ERROR,
        min_interval_s: float = 10.0,
    ):
        super().__init__(level=level)
        self._svc = notification_service
        self._min_interval = float(min_interval_s)
        self._last_sent_at = 0.0
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            if any(
                record.name == n or record.name.startswith(n + ".")
                for n in self._IGNORED_LOGGERS
            ):
                return
            # Don't even check the URL / gating if the rate-limit blocks us.
            with self._lock:
                now = time.monotonic()
                if now - self._last_sent_at < self._min_interval:
                    return
                self._last_sent_at = now

            msg = self.format(record)
            if len(msg) > 1500:
                msg = msg[:1500] + "…"
            level_name = record.levelname.title()
            location = f"{record.module}:{record.lineno}"
            self._svc.notify(
                "errors",
                title=f"Flamingo: {level_name} in {location}",
                message=msg,
                priority="high" if record.levelno >= logging.ERROR else "default",
                tags="warning",
            )
        except Exception:  # never let logging itself crash the app
            self.handleError(record)


def get_notification_service(widget) -> Optional[NotificationService]:
    """Walk a widget's parent chain to find the application's NotificationService.

    Looks for `notification_service` / `_notification_service` directly, or via
    an `app` / `_app` attribute that exposes the same. Returns None if nothing
    in the chain has one.
    """
    seen = set()
    current = widget
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for attr in ("notification_service", "_notification_service"):
            svc = getattr(current, attr, None)
            if svc is not None:
                return svc
        for attr in ("app", "_app"):
            app = getattr(current, attr, None)
            if app is not None:
                svc = getattr(app, "notification_service", None)
                if svc is not None:
                    return svc
        try:
            current = current.parent()
        except Exception:
            return None
    return None
