"""Live progress / ETA estimator for long-running workflows.

Ported from the qpsc DualProgressDialog estimator. Two pieces:

* ``ProgressEstimator`` — per-run rolling-window estimator. Records
  per-unit wall-clock deltas, excludes the first sample (always
  contains a one-time setup/settling spike), supports pause/resume
  to keep manual or hardware waits out of the mean, and exposes
  ``remaining_seconds()`` / ``eta_clock()`` / ``format_eta()``.
* ``TimingCache`` — cross-run JSON persistence. Stores an EMA of
  the per-unit mean keyed by an opaque string. Used to seed the
  initial estimate on the next run so the first few units don't
  show "Collecting timing data...".
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Deque, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_CACHE_PATH = Path("microscope_settings") / "progress_timing_cache.json"
EMA_ALPHA = 0.3
MIN_SAMPLES_FOR_ESTIMATE = 5


class TimingCache:
    """JSON-backed EMA cache of per-unit mean times in milliseconds.

    Stores per-key entries of the form
    ``{"mean_ms": float, "samples": int}``. A "key change" reset is
    not modeled here — callers pick a key that already encodes the
    relevant config axes (workflow type, LED, z_step, ...). Different
    keys naturally never share data.
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else DEFAULT_CACHE_PATH
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, float]] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self._path.exists():
                with open(self._path) as f:
                    self._data = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load timing cache from {self._path}: {e}")
            self._data = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save timing cache to {self._path}: {e}")

    def get_mean_ms(self, key: str) -> Optional[float]:
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            mean = entry.get("mean_ms")
            return float(mean) if mean and mean > 0 else None

    def update(self, key: str, mean_ms: float, alpha: float = EMA_ALPHA) -> None:
        if mean_ms <= 0:
            return
        with self._lock:
            entry = self._data.get(key)
            if not entry or entry.get("samples", 0) == 0:
                blended = mean_ms
                samples = 1
            else:
                prev = float(entry["mean_ms"])
                blended = alpha * mean_ms + (1.0 - alpha) * prev
                samples = int(entry["samples"]) + 1
            self._data[key] = {"mean_ms": blended, "samples": samples}
            self._save()

    def clear(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._data.clear()
            else:
                self._data.pop(key, None)
            self._save()


class ProgressEstimator:
    """Rolling-window per-unit time estimator with pause support.

    Units can be tiles, frames, images, or whole workflows — whatever
    the caller chooses to ``tick()`` on. The estimator is unit-agnostic.

    Args:
        total_units: Expected total number of units. May be updated mid-run.
        window: Live rolling-window size for the mean. Default 10.
        history_mult: History deque is sized at ``window * history_mult``.
        cache: Optional ``TimingCache`` used to (a) seed the initial
            estimate from prior runs and (b) persist the final mean.
        cache_key: Key into the cache. Required when ``cache`` is given.
    """

    def __init__(
        self,
        total_units: int,
        *,
        window: int = 10,
        history_mult: int = 3,
        cache: Optional[TimingCache] = None,
        cache_key: Optional[str] = None,
    ):
        if window < 1:
            raise ValueError("window must be >= 1")
        if history_mult < 1:
            raise ValueError("history_mult must be >= 1")
        if cache is not None and not cache_key:
            raise ValueError("cache_key required when cache is provided")

        self._total = max(0, int(total_units))
        self._window = int(window)
        self._recent: Deque[float] = deque(maxlen=self._window)
        self._history: Deque[float] = deque(maxlen=self._window * history_mult)
        self._completed = 0
        self._last_tick: Optional[float] = None
        self._paused_at: Optional[float] = None
        self._start_t: Optional[float] = None
        self._cache = cache
        self._cache_key = cache_key
        self._seed_mean_ms: Optional[float] = (
            cache.get_mean_ms(cache_key) if cache and cache_key else None
        )

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def set_total(self, total_units: int) -> None:
        """Update the expected total mid-run (e.g. queue grows)."""
        self._total = max(0, int(total_units))

    def tick(self, completed: int) -> None:
        """Record progress.

        ``completed`` is the cumulative count of units done, not a delta.
        Any unit-count increase pushes one delta = ``now - last_tick``
        onto the rolling windows. The first delta (i.e. the time from
        the first ``tick`` to the second) is recorded in the live
        window but excluded from the history mean — it usually
        contains one-time setup overhead (autofocus, hardware
        settling, dialog modals) that never recurs and would bias
        early estimates badly.

        Calls during ``pause()`` are still recorded so progress can
        be tracked, but the wall-clock delta is offset on
        ``resume()`` so the pause duration does not enter the mean.
        """
        now = time.monotonic()

        if self._start_t is None:
            self._start_t = now

        if completed > self._completed and self._last_tick is not None:
            dt_ms = (now - self._last_tick) * 1000.0
            if dt_ms > 0:
                self._recent.append(dt_ms)
                if self._completed > 0:
                    self._history.append(dt_ms)

        self._last_tick = now
        if completed > self._completed:
            self._completed = int(completed)

    def pause(self) -> None:
        """Stop counting wall time toward the next-unit estimate.

        Use around hardware settling, manual focus, inter-workflow
        sleep, or any other interval that should not pollute the
        per-unit mean.
        """
        if self._paused_at is None:
            self._paused_at = time.monotonic()

    def resume(self) -> None:
        if self._paused_at is None:
            return
        pause_dur = time.monotonic() - self._paused_at
        if self._last_tick is not None:
            self._last_tick += pause_dur
        self._paused_at = None

    def reset(self) -> None:
        self._recent.clear()
        self._history.clear()
        self._completed = 0
        self._last_tick = None
        self._paused_at = None
        self._start_t = None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def completed(self) -> int:
        return self._completed

    @property
    def total(self) -> int:
        return self._total

    @property
    def remaining_units(self) -> int:
        return max(0, self._total - self._completed)

    @property
    def has_estimate(self) -> bool:
        return self._mean_ms() is not None

    def _mean_ms(self) -> Optional[float]:
        if len(self._history) >= min(self._window, MIN_SAMPLES_FOR_ESTIMATE):
            return sum(self._history) / len(self._history)
        # Pre-quorum: prior-run seed if available; otherwise nothing,
        # so the UI shows "estimating..." until enough samples land.
        # We deliberately do NOT fall back to the live recent window
        # here — early deltas (especially the very first one) are
        # noisy and would produce wildly wrong ETAs.
        return self._seed_mean_ms

    def remaining_seconds(self) -> Optional[float]:
        mean_ms = self._mean_ms()
        if mean_ms is None:
            return None
        return mean_ms * self.remaining_units / 1000.0

    def eta_clock(self) -> Optional[datetime]:
        rem = self.remaining_seconds()
        if rem is None:
            return None
        return datetime.now() + timedelta(seconds=rem)

    def format_remaining(self) -> str:
        rem = self.remaining_seconds()
        if rem is None:
            if self._completed == 0 or len(self._history) == 0:
                return "estimating..."
            return "--:--"
        return _format_duration(rem)

    def format_eta(self) -> str:
        clock = self.eta_clock()
        if clock is None:
            return "--:--"
        if clock.date() == datetime.now().date():
            return clock.strftime("%H:%M")
        return clock.strftime("%a %H:%M")

    def format_label(self) -> str:
        """Combined human label: ``"02:34 remaining (done ~14:07)"``.

        Returns ``"estimating..."`` until the rolling window has
        enough samples (or a cache seed is available).
        """
        rem = self.remaining_seconds()
        if rem is None:
            return "estimating..."
        return f"{_format_duration(rem)} remaining (done ~{self.format_eta()})"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def finalize(self) -> Optional[float]:
        """Push the final mean into the timing cache, if configured.

        Returns the mean used (ms) or None if there were not enough
        samples to be worth saving.
        """
        if self._cache is None or self._cache_key is None:
            return None
        if len(self._history) < MIN_SAMPLES_FOR_ESTIMATE:
            return None
        mean = sum(self._history) / len(self._history)
        self._cache.update(self._cache_key, mean)
        return mean


def _format_duration(seconds: float) -> str:
    """Format ``seconds`` as ``"H:MM:SS"``, ``"M:SS"``, or ``"Ns"``."""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}:{s:02d}"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"
