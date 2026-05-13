"""Multi-phase stitching ETA estimator.

Tracks per-phase wall time during a stitching run and projects total
elapsed time / remaining time from observed phase durations combined
with cached per-phase share-of-total from prior runs.

ETA strategies, in order of preference:

1. **Cold start** (no phases done, cached total available): use the
   cached mean total wall time as both elapsed-projection and remaining.
2. **Mid run, with cached shares**: ``T_projected = sum(observed) /
   sum(shares_observed)``. Remaining = ``T_projected - sum(observed)``.
3. **Mid run, no cached shares**: extrapolate linearly from the
   completed-phase count. Crude but better than silence.
4. **Nothing**: return ``None`` so the caller can render "estimating...".
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

from py2flamingo.stitching.timing_cache import (
    PHASE_ORDER,
    StitchingTimingCache,
    StitchingTimingKey,
)

logger = logging.getLogger(__name__)

# Minimum partial duration (seconds) for the in-progress phase to
# count toward the share-based projection. Below this, the partial is
# too noisy: dividing tiny partial by tiny share gives garbage.
_MIN_INPROGRESS_S = 5.0


class MultiPhaseEstimator:
    """Live ETA across the discrete phases of a stitching run.

    Lifecycle::

        est = MultiPhaseEstimator(cache, key)
        est.start()                  # at run start
        est.start_phase("discover")  # entering a phase
        est.end_phase("discover")    # leaving a phase (or start_phase
                                     # of the next phase auto-ends it)
        ...
        eta = est.format_label()     # any time after start()
        est.finalize()               # at run end (writes to cache)
    """

    def __init__(self, cache: StitchingTimingCache, key: StitchingTimingKey):
        self._cache = cache
        self._key = key
        self._start_t: Optional[float] = None
        self._current_phase: Optional[str] = None
        self._current_phase_start: Optional[float] = None
        self._phase_durations: Dict[str, float] = {}

        self._cached_total_s = cache.get_total_s(key)
        self._cached_shares = cache.get_phase_shares(key)
        if self._cached_total_s:
            logger.info(
                f"Stitching ETA: seeded from cache "
                f"(total ~{self._cached_total_s:.0f}s, "
                f"{len(self._cached_shares)} phase shares)"
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._start_t is None:
            self._start_t = time.monotonic()

    def start_phase(self, phase: str) -> None:
        """Mark the start of a phase. If another phase was running it's
        ended first."""
        if phase not in PHASE_ORDER:
            logger.debug(f"Unknown phase '{phase}', tracking anyway")
        if self._current_phase is not None:
            self.end_phase(self._current_phase)
        self.start()
        self._current_phase = phase
        self._current_phase_start = time.monotonic()

    def end_phase(self, phase: str) -> None:
        """End the named phase. No-op if it isn't the current phase."""
        if self._current_phase != phase or self._current_phase_start is None:
            return
        dur = time.monotonic() - self._current_phase_start
        # If we re-enter the same phase later (e.g. multi-channel fuse
        # reports the same status repeatedly), accumulate rather than
        # overwrite.
        self._phase_durations[phase] = self._phase_durations.get(phase, 0.0) + max(
            0.0, dur
        )
        self._current_phase = None
        self._current_phase_start = None

    def finalize(self, success: bool = True) -> None:
        """End the current phase and, on success, push timings to cache."""
        if self._current_phase is not None:
            self.end_phase(self._current_phase)
        if not success:
            return  # don't poison cache with failed/cancelled runs
        elapsed = self.elapsed_seconds()
        if elapsed is None or elapsed < 1.0:
            return  # not worth recording (presumably aborted)
        self._cache.record_run(self._key, elapsed, dict(self._phase_durations))
        logger.info(
            f"Stitching ETA: recorded run "
            f"(total={elapsed:.0f}s, phases={self._phase_durations})"
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def elapsed_seconds(self) -> Optional[float]:
        if self._start_t is None:
            return None
        return time.monotonic() - self._start_t

    def remaining_seconds(self) -> Optional[float]:
        elapsed = self.elapsed_seconds()
        if elapsed is None:
            return None

        # Build the observed-duration map. Include the in-progress
        # phase's partial time ONLY if it's substantial (>=5s) -- a
        # just-started phase has sub-millisecond partial which, divided
        # by a small share like 0.008, blows up to a projected total of
        # near zero. The threshold filters out that pathological case
        # while still picking up legitimate "this fuse phase is running
        # long" signal once meaningful time has passed.
        observed: Dict[str, float] = dict(self._phase_durations)
        if self._current_phase is not None and self._current_phase_start is not None:
            partial = time.monotonic() - self._current_phase_start
            if partial >= _MIN_INPROGRESS_S:
                observed[self._current_phase] = (
                    observed.get(self._current_phase, 0.0) + partial
                )

        # Strategy 2: cached shares + observed (completed + substantial
        # in-progress) phases.
        if self._cached_shares and observed:
            sum_observed_dur = sum(observed.values())
            sum_observed_shares = sum(self._cached_shares.get(p, 0.0) for p in observed)
            if sum_observed_shares > 0 and sum_observed_dur > 0:
                projected_total = sum_observed_dur / sum_observed_shares
                return max(0.0, projected_total - elapsed)

        # Strategy 1: cold start with cached total
        if self._cached_total_s:
            return max(0.0, self._cached_total_s - elapsed)

        # Strategy 3: no cache; extrapolate from completed-phase count.
        # Crude but better than silence.
        completed = list(self._phase_durations)
        if completed:
            done_fraction = len(completed) / max(len(PHASE_ORDER), 1)
            if done_fraction > 0:
                projected = elapsed / done_fraction
                return max(0.0, projected - elapsed)

        return None

    def eta_clock(self) -> Optional[datetime]:
        rem = self.remaining_seconds()
        if rem is None:
            return None
        return datetime.now() + timedelta(seconds=rem)

    def format_remaining(self) -> str:
        rem = self.remaining_seconds()
        if rem is None:
            return "estimating..."
        return _format_duration(rem)

    def format_eta(self) -> str:
        clock = self.eta_clock()
        if clock is None:
            return "--:--"
        if clock.date() == datetime.now().date():
            return clock.strftime("%H:%M")
        return clock.strftime("%a %H:%M")

    def format_label(self) -> str:
        """``"M:SS remaining (done ~HH:MM)"`` or ``"estimating..."``."""
        rem = self.remaining_seconds()
        if rem is None:
            return "estimating..."
        return f"{_format_duration(rem)} remaining (done ~{self.format_eta()})"


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}:{s:02d}"
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}"
