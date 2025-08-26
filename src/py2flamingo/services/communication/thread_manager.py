# src/py2flamingo/services/communication/thread_manager.py
from __future__ import annotations
import threading
from typing import Callable, Optional

class ThreadManager:
    """
    Starts & stops communication/processing threads.
    Headless-safe: doesn't import legacy thread targets until start_* is called.
    """

    def __init__(self):
        self._threads: list[threading.Thread] = []
        self._stopped = threading.Event()

        # Allow dependency injection for tests
        self.command_listen_target: Optional[Callable] = None
        self.live_listen_target: Optional[Callable] = None
        self.send_target: Optional[Callable] = None
        self.processing_target: Optional[Callable] = None

    # ----- helpers -----
    @staticmethod
    def _try_legacy_import():
        """Try to import legacy thread targets, return dict of callables or {}."""
        try:
            # Import ONLY when needed; repository may not ship this file anymore
            from py2flamingo.functions.threads import (  # type: ignore
                command_listen_thread, live_listen_thread,
                send_thread, processing_thread
            )
            return {
                "command_listen_thread": command_listen_thread,
                "live_listen_thread": live_listen_thread,
                "send_thread": send_thread,
                "processing_thread": processing_thread,
            }
        except Exception:
            return {}

    def _resolve_target(self, name: str) -> Callable:
        # Prefer injected target (tests), otherwise legacy, else no-op
        injected = getattr(self, f"{name}", None)
        if callable(injected):
            return injected
        legacy = self._try_legacy_import().get(name)
        if callable(legacy):
            return legacy
        # Fallback no-op
        def _noop(*args, **kwargs):
            return None
        return _noop

    def _spawn(self, target: Callable, *args, name: str):
        t = threading.Thread(target=target, args=args, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    # ----- public API used by ConnectionService -----
    def start_receivers(self, *args):
        self._spawn(self._resolve_target("command_listen_target"), *args, name="command-listen")

    def start_live_receiver(self, *args):
        self._spawn(self._resolve_target("live_listen_target"), *args, name="live-listen")

    def start_sender(self, *args):
        self._spawn(self._resolve_target("send_target"), *args, name="send-thread")

    def start_processing(self, *args):
        self._spawn(self._resolve_target("processing_target"), *args, name="processing-thread")

    def stop_all(self, timeout: float = 1.0):
        self._stopped.set()
        # In a minimal test env, there may be no real threads running; join safely
        for t in self._threads:
            try:
                t.join(timeout=timeout)
            except Exception:
                pass
        self._threads.clear()
