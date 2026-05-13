"""Persistent timing cache for stitching pipeline ETA.

Stores per-key total wall time and per-phase share-of-total as EMAs.
The key is built from the variables that most strongly affect cost
(tile count, channels, pyramid levels, timepoints, output format,
fusion method, registration on/off, planes per tile). Different
acquisitions naturally land in different keys, so the EMA for any
given key only blends "similar" runs.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_CACHE_PATH = Path("microscope_settings") / "stitching_timing_cache.json"
EMA_ALPHA = 0.3

# Phase identifiers used by the estimator. Order matters: it's the
# expected execution order, and the estimator uses it to determine
# which phases are "remaining" once the current phase is known.
PHASE_ORDER: List[str] = [
    "discover",
    "register",
    "preprocess",
    "fuse",
    "write",
    "metadata",
]


# ---------------------------------------------------------------------------
# Key bucketing
# ---------------------------------------------------------------------------


def _bucket_tiles(n: int) -> str:
    """Bucket tile count so adjacent counts share a cache key."""
    if n <= 4:
        return "1-4"
    if n <= 9:
        return "5-9"
    if n <= 24:
        return "10-24"
    if n <= 49:
        return "25-49"
    if n <= 99:
        return "50-99"
    if n <= 249:
        return "100-249"
    return "250+"


def _bucket_planes(n: int) -> str:
    """Bucket planes-per-tile (Z-stack depth)."""
    if n <= 50:
        return "1-50"
    if n <= 150:
        return "51-150"
    if n <= 400:
        return "151-400"
    if n <= 1000:
        return "401-1000"
    return "1000+"


@dataclass(frozen=True)
class StitchingTimingKey:
    """All key axes for the cache. Use ``.serialize()`` to get a flat
    string suitable for JSON dict lookup."""

    n_tiles: int
    n_channels: int
    n_pyramid_levels: int  # 0 if none
    n_timepoints: int  # 1 if not a time series
    output_format: str  # e.g. "ome-zarr-sharded", "imaris", "ome-tiff"
    fusion_method: str  # "content_based" | "cosine"
    skip_registration: bool
    planes_per_tile: int

    def serialize(self) -> str:
        return (
            f"t={_bucket_tiles(self.n_tiles)}|"
            f"c={self.n_channels}|"
            f"p={self.n_pyramid_levels}|"
            f"tp={self.n_timepoints}|"
            f"fmt={self.output_format}|"
            f"fus={self.fusion_method}|"
            f"skipreg={int(self.skip_registration)}|"
            f"pl={_bucket_planes(self.planes_per_tile)}"
        )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class StitchingTimingCache:
    """JSON-backed EMA cache of stitching phase timings.

    Each key maps to::

        {
          "total_s": {"mean": float, "samples": int},
          "phases":  {phase_name: {"mean_share": float, "samples": int}, ...}
        }

    Shares are fractions of total wall time (sum-to-1 across phases
    actually run; some phases — like ``register`` when
    ``skip_registration=True`` — may be absent).
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else DEFAULT_CACHE_PATH
        self._lock = threading.Lock()
        self._data: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self._path.exists():
                with open(self._path) as f:
                    self._data = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load stitching timing cache: {e}")
            self._data = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save stitching timing cache: {e}")

    def get_total_s(self, key: StitchingTimingKey) -> Optional[float]:
        """Cached mean total wall time for this key, if any."""
        with self._lock:
            entry = self._data.get(key.serialize())
            if not entry:
                return None
            total = entry.get("total_s", {}).get("mean")
            return float(total) if total and total > 0 else None

    def get_phase_shares(self, key: StitchingTimingKey) -> Dict[str, float]:
        """Cached mean share-of-total per phase. Empty dict if no data."""
        with self._lock:
            entry = self._data.get(key.serialize())
            if not entry:
                return {}
            phases = entry.get("phases", {})
            return {
                name: float(p["mean_share"])
                for name, p in phases.items()
                if p.get("mean_share")
            }

    def record_run(
        self,
        key: StitchingTimingKey,
        total_s: float,
        phase_durations_s: Dict[str, float],
        *,
        alpha: float = EMA_ALPHA,
    ) -> None:
        """Update the EMA with one completed run.

        ``phase_durations_s`` is the absolute wall time spent in each
        phase; shares are computed here from ``total_s``.
        """
        if total_s <= 0:
            return
        with self._lock:
            k = key.serialize()
            entry = self._data.setdefault(k, {"total_s": {}, "phases": {}})

            # Update total_s EMA
            total_block = entry["total_s"]
            prev_total = float(total_block.get("mean", 0.0))
            prev_samples = int(total_block.get("samples", 0))
            if prev_samples == 0:
                total_block["mean"] = total_s
            else:
                total_block["mean"] = alpha * total_s + (1.0 - alpha) * prev_total
            total_block["samples"] = prev_samples + 1

            # Update per-phase share EMA
            phases_block = entry["phases"]
            for phase, dur in phase_durations_s.items():
                share = max(0.0, dur / total_s)
                pb = phases_block.setdefault(phase, {})
                prev_share = float(pb.get("mean_share", 0.0))
                prev_n = int(pb.get("samples", 0))
                if prev_n == 0:
                    pb["mean_share"] = share
                else:
                    pb["mean_share"] = alpha * share + (1.0 - alpha) * prev_share
                pb["samples"] = prev_n + 1

            self._save()

    def clear(self, key: Optional[StitchingTimingKey] = None) -> None:
        with self._lock:
            if key is None:
                self._data.clear()
            else:
                self._data.pop(key.serialize(), None)
            self._save()
