"""Tests for ProgressEstimator and TimingCache."""

import time
from pathlib import Path

import pytest

from py2flamingo.services.progress_estimator import (
    MIN_SAMPLES_FOR_ESTIMATE,
    ProgressEstimator,
    TimingCache,
    _format_duration,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeClock:
    """Monkeypatchable replacement for time.monotonic in the estimator."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fc = FakeClock()
    monkeypatch.setattr("py2flamingo.services.progress_estimator.time.monotonic", fc)
    return fc


# ---------------------------------------------------------------------------
# ProgressEstimator
# ---------------------------------------------------------------------------


class TestProgressEstimator:
    def test_first_sample_excluded_from_history(self, clock):
        est = ProgressEstimator(total_units=10)
        est.tick(0)
        clock.advance(5.0)  # First-tile spike: should NOT enter history
        est.tick(1)
        clock.advance(1.0)
        est.tick(2)
        clock.advance(1.0)
        est.tick(3)
        # Spike (5000ms) recorded in recent window but NOT history
        assert list(est._history) == pytest.approx([1000.0, 1000.0])
        assert 5000.0 in list(est._recent)

    def test_remaining_seconds_uses_history_mean(self, clock):
        est = ProgressEstimator(total_units=10)
        est.tick(0)
        clock.advance(5.0)  # spike, excluded
        est.tick(1)
        for _ in range(MIN_SAMPLES_FOR_ESTIMATE):
            clock.advance(2.0)
            est.tick(est.completed + 1)
        # 2s/unit, units remaining = 10 - completed
        expected = 2.0 * (10 - est.completed)
        assert est.remaining_seconds() == pytest.approx(expected)

    def test_no_estimate_until_min_samples(self, clock):
        est = ProgressEstimator(total_units=20, window=10)
        est.tick(0)
        clock.advance(5.0)
        est.tick(1)
        # 0 history samples
        assert not est.has_estimate
        clock.advance(1.0)
        est.tick(2)
        # 1 history sample, still below threshold (5)
        assert not est.has_estimate
        for _ in range(MIN_SAMPLES_FOR_ESTIMATE - 1):
            clock.advance(1.0)
            est.tick(est.completed + 1)
        assert est.has_estimate

    def test_window_rollover_drops_old_samples(self, clock):
        est = ProgressEstimator(total_units=100, window=3, history_mult=1)
        est.tick(0)
        clock.advance(10.0)  # first-tick spike, excluded from history
        est.tick(1)
        # Push 3 history samples at 1s each
        for _ in range(3):
            clock.advance(1.0)
            est.tick(est.completed + 1)
        # History should be exactly 3 entries of ~1000ms
        assert len(est._history) == 3
        assert est._mean_ms() == pytest.approx(1000.0)
        # Now push 3 more at 4s each — these should fully replace the old
        for _ in range(3):
            clock.advance(4.0)
            est.tick(est.completed + 1)
        assert len(est._history) == 3
        assert est._mean_ms() == pytest.approx(4000.0)

    def test_pause_resume_excludes_pause_duration(self, clock):
        est = ProgressEstimator(total_units=10)
        est.tick(0)
        clock.advance(5.0)
        est.tick(1)  # spike, excluded
        # Establish 1s baseline
        clock.advance(1.0)
        est.tick(2)
        # Pause for 30s, then a normal 1s tick
        est.pause()
        clock.advance(30.0)
        est.resume()
        clock.advance(1.0)
        est.tick(3)
        # Both history samples should be ~1000ms, NOT 31000ms
        assert all(900 < dt < 1100 for dt in est._history)

    def test_pause_without_resume_is_idempotent(self, clock):
        est = ProgressEstimator(total_units=10)
        est.pause()
        first_paused = est._paused_at
        clock.advance(1.0)
        est.pause()  # second call should be a no-op
        assert est._paused_at == first_paused

    def test_resume_without_pause_is_safe(self, clock):
        est = ProgressEstimator(total_units=10)
        est.resume()  # no-op, must not crash

    def test_completed_capped_at_total_via_remaining(self, clock):
        est = ProgressEstimator(total_units=5)
        est.tick(0)
        for _ in range(10):
            clock.advance(1.0)
            est.tick(est.completed + 1)
        assert est.remaining_units == 0  # never negative

    def test_set_total_updates_remaining(self, clock):
        est = ProgressEstimator(total_units=10)
        est.tick(0)
        clock.advance(5.0)
        est.tick(1)
        for _ in range(MIN_SAMPLES_FOR_ESTIMATE):
            clock.advance(1.0)
            est.tick(est.completed + 1)
        before = est.remaining_seconds()
        est.set_total(20)
        after = est.remaining_seconds()
        assert after > before

    def test_format_remaining_before_data(self, clock):
        est = ProgressEstimator(total_units=10)
        assert est.format_remaining() == "estimating..."

    def test_format_label_includes_eta(self, clock):
        est = ProgressEstimator(total_units=10)
        est.tick(0)
        clock.advance(5.0)
        est.tick(1)
        for _ in range(MIN_SAMPLES_FOR_ESTIMATE):
            clock.advance(1.0)
            est.tick(est.completed + 1)
        label = est.format_label()
        assert "remaining" in label
        assert "Done at ~" in label

    def test_reset_clears_state(self, clock):
        est = ProgressEstimator(total_units=10)
        est.tick(0)
        clock.advance(1.0)
        est.tick(1)
        est.reset()
        assert est.completed == 0
        assert len(est._history) == 0
        assert len(est._recent) == 0


# ---------------------------------------------------------------------------
# TimingCache
# ---------------------------------------------------------------------------


class TestTimingCache:
    def test_get_returns_none_when_missing(self, tmp_path: Path):
        cache = TimingCache(path=tmp_path / "cache.json")
        assert cache.get_mean_ms("anything") is None

    def test_first_update_stores_value_directly(self, tmp_path: Path):
        cache = TimingCache(path=tmp_path / "cache.json")
        cache.update("key", 1000.0)
        assert cache.get_mean_ms("key") == pytest.approx(1000.0)

    def test_subsequent_updates_apply_ema(self, tmp_path: Path):
        cache = TimingCache(path=tmp_path / "cache.json")
        cache.update("key", 1000.0)  # first: stored as-is
        cache.update("key", 2000.0, alpha=0.3)
        # 0.3 * 2000 + 0.7 * 1000 = 1300
        assert cache.get_mean_ms("key") == pytest.approx(1300.0)

    def test_persistence_across_instances(self, tmp_path: Path):
        path = tmp_path / "cache.json"
        cache_a = TimingCache(path=path)
        cache_a.update("workflow:foo", 500.0)
        cache_b = TimingCache(path=path)
        assert cache_b.get_mean_ms("workflow:foo") == pytest.approx(500.0)

    def test_different_keys_isolated(self, tmp_path: Path):
        cache = TimingCache(path=tmp_path / "cache.json")
        cache.update("a", 1000.0)
        cache.update("b", 5000.0)
        assert cache.get_mean_ms("a") == pytest.approx(1000.0)
        assert cache.get_mean_ms("b") == pytest.approx(5000.0)

    def test_clear_removes_one_or_all(self, tmp_path: Path):
        cache = TimingCache(path=tmp_path / "cache.json")
        cache.update("a", 1000.0)
        cache.update("b", 2000.0)
        cache.clear("a")
        assert cache.get_mean_ms("a") is None
        assert cache.get_mean_ms("b") == pytest.approx(2000.0)
        cache.clear()
        assert cache.get_mean_ms("b") is None

    def test_zero_or_negative_update_ignored(self, tmp_path: Path):
        cache = TimingCache(path=tmp_path / "cache.json")
        cache.update("k", 0)
        cache.update("k", -5)
        assert cache.get_mean_ms("k") is None

    def test_corrupt_file_does_not_crash(self, tmp_path: Path):
        path = tmp_path / "cache.json"
        path.write_text("{not valid json")
        cache = TimingCache(path=path)
        assert cache.get_mean_ms("k") is None
        # Should still be writable afterwards
        cache.update("k", 100.0)
        assert cache.get_mean_ms("k") == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Estimator + Cache integration
# ---------------------------------------------------------------------------


class TestEstimatorWithCache:
    def test_seed_from_cache_enables_estimate_immediately(self, clock, tmp_path: Path):
        cache = TimingCache(path=tmp_path / "cache.json")
        cache.update("key", 1500.0)
        est = ProgressEstimator(total_units=10, cache=cache, cache_key="key")
        # No ticks yet but seed mean is available
        assert est.has_estimate
        assert est._mean_ms() == pytest.approx(1500.0)

    def test_finalize_persists_mean_to_cache(self, clock, tmp_path: Path):
        cache = TimingCache(path=tmp_path / "cache.json")
        est = ProgressEstimator(total_units=10, cache=cache, cache_key="run_a")
        est.tick(0)
        clock.advance(5.0)
        est.tick(1)  # spike
        for _ in range(MIN_SAMPLES_FOR_ESTIMATE):
            clock.advance(2.0)
            est.tick(est.completed + 1)
        saved = est.finalize()
        assert saved == pytest.approx(2000.0)
        assert cache.get_mean_ms("run_a") == pytest.approx(2000.0)

    def test_finalize_without_enough_samples_skips_save(self, clock, tmp_path: Path):
        cache = TimingCache(path=tmp_path / "cache.json")
        est = ProgressEstimator(total_units=10, cache=cache, cache_key="short")
        est.tick(0)
        clock.advance(1.0)
        est.tick(1)
        clock.advance(1.0)
        est.tick(2)
        # Only one history sample (2nd tick was the spike-exclusion)
        assert est.finalize() is None
        assert cache.get_mean_ms("short") is None

    def test_cache_key_required_when_cache_passed(self, tmp_path: Path):
        cache = TimingCache(path=tmp_path / "cache.json")
        with pytest.raises(ValueError):
            ProgressEstimator(total_units=10, cache=cache)

    def test_history_mean_overrides_seed_after_quorum(self, clock, tmp_path: Path):
        cache = TimingCache(path=tmp_path / "cache.json")
        cache.update("k", 9000.0)  # very pessimistic seed
        est = ProgressEstimator(total_units=20, cache=cache, cache_key="k")
        est.tick(0)
        clock.advance(5.0)
        est.tick(1)
        # Live deltas at 1s each — once we have >= MIN samples in
        # history, the seed should no longer dominate the estimate.
        for _ in range(MIN_SAMPLES_FOR_ESTIMATE):
            clock.advance(1.0)
            est.tick(est.completed + 1)
        assert est._mean_ms() == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


class TestFormatDuration:
    @pytest.mark.parametrize(
        "secs,expected",
        [
            (0, "0s"),
            (1, "1s"),
            (59, "59s"),
            (60, "1:00"),
            (125, "2:05"),
            (3599, "59:59"),
            (3600, "1:00:00"),
            (3725, "1:02:05"),
        ],
    )
    def test_format_duration(self, secs, expected):
        assert _format_duration(secs) == expected

    def test_negative_treated_as_zero(self):
        assert _format_duration(-5) == "0s"
