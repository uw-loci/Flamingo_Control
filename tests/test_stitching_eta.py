"""Tests for StitchingTimingCache and MultiPhaseEstimator."""

from pathlib import Path

import pytest

from py2flamingo.stitching.multi_phase_estimator import (
    MultiPhaseEstimator,
    _format_duration,
)
from py2flamingo.stitching.timing_cache import (
    PHASE_ORDER,
    StitchingTimingCache,
    StitchingTimingKey,
    _bucket_planes,
    _bucket_tiles,
)

# ---------------------------------------------------------------------------
# Fake clock
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fc = FakeClock()
    monkeypatch.setattr(
        "py2flamingo.stitching.multi_phase_estimator.time.monotonic", fc
    )
    return fc


def make_key(**overrides):
    base = dict(
        n_tiles=20,
        n_channels=2,
        n_pyramid_levels=4,
        n_timepoints=1,
        output_format="ome-zarr-sharded",
        fusion_method="cosine",
        skip_registration=False,
        planes_per_tile=200,
    )
    base.update(overrides)
    return StitchingTimingKey(**base)


# ---------------------------------------------------------------------------
# Key bucketing
# ---------------------------------------------------------------------------


class TestKeyBucketing:
    @pytest.mark.parametrize(
        "n,expected",
        [
            (1, "1-4"),
            (4, "1-4"),
            (5, "5-9"),
            (9, "5-9"),
            (10, "10-24"),
            (24, "10-24"),
            (25, "25-49"),
            (49, "25-49"),
            (50, "50-99"),
            (99, "50-99"),
            (100, "100-249"),
            (249, "100-249"),
            (250, "250+"),
            (1000, "250+"),
        ],
    )
    def test_bucket_tiles(self, n, expected):
        assert _bucket_tiles(n) == expected

    def test_bucket_planes_bounds(self):
        assert _bucket_planes(1) == "1-50"
        assert _bucket_planes(50) == "1-50"
        assert _bucket_planes(51) == "51-150"
        assert _bucket_planes(2000) == "1000+"

    def test_key_serialization_stable(self):
        k1 = make_key()
        k2 = make_key()
        assert k1.serialize() == k2.serialize()

    def test_different_keys_serialize_differently(self):
        k1 = make_key(n_tiles=20)
        k2 = make_key(n_tiles=200)
        assert k1.serialize() != k2.serialize()

    def test_neighboring_counts_share_bucket(self):
        # 15 and 20 both fall in the 10-24 bucket -> same key
        assert make_key(n_tiles=15).serialize() == make_key(n_tiles=20).serialize()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


class TestTimingCache:
    def test_empty_cache_returns_none(self, tmp_path: Path):
        cache = StitchingTimingCache(path=tmp_path / "c.json")
        assert cache.get_total_s(make_key()) is None
        assert cache.get_phase_shares(make_key()) == {}

    def test_record_run_stores_total_and_shares(self, tmp_path: Path):
        cache = StitchingTimingCache(path=tmp_path / "c.json")
        cache.record_run(
            make_key(),
            total_s=1000.0,
            phase_durations_s={
                "discover": 5.0,
                "register": 200.0,
                "fuse": 600.0,
                "write": 195.0,
            },
        )
        assert cache.get_total_s(make_key()) == pytest.approx(1000.0)
        shares = cache.get_phase_shares(make_key())
        assert shares["discover"] == pytest.approx(0.005)
        assert shares["fuse"] == pytest.approx(0.6)
        assert shares["write"] == pytest.approx(0.195)

    def test_ema_blends_runs(self, tmp_path: Path):
        cache = StitchingTimingCache(path=tmp_path / "c.json")
        cache.record_run(make_key(), 1000.0, {"fuse": 600.0})
        cache.record_run(make_key(), 2000.0, {"fuse": 1000.0})
        # alpha=0.3: total = 0.3*2000 + 0.7*1000 = 1300
        assert cache.get_total_s(make_key()) == pytest.approx(1300.0)
        # share: first 0.6, then 0.5; EMA = 0.3*0.5 + 0.7*0.6 = 0.57
        assert cache.get_phase_shares(make_key())["fuse"] == pytest.approx(0.57)

    def test_persistence_round_trip(self, tmp_path: Path):
        path = tmp_path / "c.json"
        c1 = StitchingTimingCache(path=path)
        c1.record_run(make_key(), 500.0, {"fuse": 250.0})
        c2 = StitchingTimingCache(path=path)
        assert c2.get_total_s(make_key()) == pytest.approx(500.0)

    def test_different_keys_isolated(self, tmp_path: Path):
        cache = StitchingTimingCache(path=tmp_path / "c.json")
        cache.record_run(make_key(n_tiles=20), 1000.0, {"fuse": 600.0})
        cache.record_run(make_key(n_tiles=200), 5000.0, {"fuse": 3000.0})
        assert cache.get_total_s(make_key(n_tiles=20)) == pytest.approx(1000.0)
        assert cache.get_total_s(make_key(n_tiles=200)) == pytest.approx(5000.0)

    def test_zero_total_ignored(self, tmp_path: Path):
        cache = StitchingTimingCache(path=tmp_path / "c.json")
        cache.record_run(make_key(), 0.0, {"fuse": 100.0})
        assert cache.get_total_s(make_key()) is None

    def test_corrupt_file_recovers(self, tmp_path: Path):
        path = tmp_path / "c.json"
        path.write_text("not json")
        cache = StitchingTimingCache(path=path)
        assert cache.get_total_s(make_key()) is None
        cache.record_run(make_key(), 100.0, {"fuse": 50.0})
        assert cache.get_total_s(make_key()) == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Estimator
# ---------------------------------------------------------------------------


class TestMultiPhaseEstimator:
    def test_no_estimate_without_data(self, clock, tmp_path: Path):
        cache = StitchingTimingCache(path=tmp_path / "c.json")
        est = MultiPhaseEstimator(cache, make_key())
        est.start()
        clock.advance(10.0)
        # No phase completed, no cache: nothing to estimate from
        assert est.remaining_seconds() is None
        assert est.format_label() == "estimating..."

    def test_cold_start_from_cache(self, clock, tmp_path: Path):
        cache = StitchingTimingCache(path=tmp_path / "c.json")
        cache.record_run(make_key(), 600.0, {"fuse": 300.0})
        est = MultiPhaseEstimator(cache, make_key())
        est.start()
        clock.advance(60.0)
        # Cached total = 600s, elapsed = 60s -> ~540s remaining
        assert est.remaining_seconds() == pytest.approx(540.0)

    def test_phase_shares_refine_estimate(self, clock, tmp_path: Path):
        cache = StitchingTimingCache(path=tmp_path / "c.json")
        cache.record_run(
            make_key(),
            1000.0,
            {"discover": 10.0, "fuse": 500.0, "write": 490.0},
        )
        est = MultiPhaseEstimator(cache, make_key())
        est.start_phase("discover")
        clock.advance(20.0)  # discover actually took 20s -> 2x slower
        est.start_phase("fuse")
        # Now we've observed discover share = 20/T_projected. With
        # cached share 0.01, T_projected = 20 / 0.01 = 2000s.
        # Elapsed = 20s. Remaining ~ 1980.
        rem = est.remaining_seconds()
        assert rem is not None
        assert 1900 < rem < 2100

    def test_finalize_records_to_cache(self, clock, tmp_path: Path):
        cache = StitchingTimingCache(path=tmp_path / "c.json")
        est = MultiPhaseEstimator(cache, make_key())
        est.start_phase("discover")
        clock.advance(10.0)
        est.start_phase("fuse")
        clock.advance(100.0)
        est.start_phase("write")
        clock.advance(90.0)
        est.finalize(success=True)
        assert cache.get_total_s(make_key()) == pytest.approx(200.0)
        shares = cache.get_phase_shares(make_key())
        assert shares["discover"] == pytest.approx(0.05)
        assert shares["fuse"] == pytest.approx(0.5)
        assert shares["write"] == pytest.approx(0.45)

    def test_finalize_failure_does_not_pollute_cache(self, clock, tmp_path: Path):
        cache = StitchingTimingCache(path=tmp_path / "c.json")
        est = MultiPhaseEstimator(cache, make_key())
        est.start_phase("fuse")
        clock.advance(50.0)
        est.finalize(success=False)
        assert cache.get_total_s(make_key()) is None

    def test_phase_re_entry_accumulates(self, clock, tmp_path: Path):
        cache = StitchingTimingCache(path=tmp_path / "c.json")
        est = MultiPhaseEstimator(cache, make_key())
        est.start_phase("fuse")
        clock.advance(30.0)
        est.start_phase("write")
        clock.advance(10.0)
        est.start_phase("fuse")  # re-entry (e.g. multi-channel)
        clock.advance(20.0)
        est.finalize(success=True)
        # fuse total should be 30 + 20 = 50
        shares = cache.get_phase_shares(make_key())
        total = 30 + 10 + 20
        assert shares["fuse"] == pytest.approx(50.0 / total)
        assert shares["write"] == pytest.approx(10.0 / total)

    def test_in_progress_phase_counted_in_remaining(self, clock, tmp_path: Path):
        cache = StitchingTimingCache(path=tmp_path / "c.json")
        cache.record_run(make_key(), 1000.0, {"fuse": 600.0, "write": 400.0})
        est = MultiPhaseEstimator(cache, make_key())
        est.start_phase("fuse")
        # Mid-phase, no end_phase yet
        clock.advance(300.0)
        rem = est.remaining_seconds()
        assert rem is not None
        # Cached fuse share = 0.6. Observed 300s -> projected total
        # = 300/0.6 = 500s. Elapsed = 300. Remaining = 200.
        assert 150 < rem < 250

    def test_format_label_with_cached_total(self, clock, tmp_path: Path):
        cache = StitchingTimingCache(path=tmp_path / "c.json")
        cache.record_run(make_key(), 120.0, {"fuse": 60.0})
        est = MultiPhaseEstimator(cache, make_key())
        est.start()
        label = est.format_label()
        assert "remaining" in label
        # Casing-robust: the stitcher emits "done at ~"; the app uses "Done at ~".
        assert "done at ~" in label.lower()

    def test_no_cache_extrapolates_from_phase_count(self, clock, tmp_path: Path):
        cache = StitchingTimingCache(path=tmp_path / "c.json")
        est = MultiPhaseEstimator(cache, make_key())
        est.start_phase("discover")
        clock.advance(60.0)
        est.start_phase("fuse")  # auto-ends discover
        # 1 / 6 phases completed in 60s -> projected total 360s,
        # remaining ~300s. Crude but non-None.
        rem = est.remaining_seconds()
        assert rem is not None
        assert rem > 0

    def test_short_run_not_recorded(self, clock, tmp_path: Path):
        cache = StitchingTimingCache(path=tmp_path / "c.json")
        est = MultiPhaseEstimator(cache, make_key())
        est.start_phase("discover")
        clock.advance(0.5)  # < 1s threshold
        est.finalize(success=True)
        assert cache.get_total_s(make_key()) is None


class TestFormatDuration:
    @pytest.mark.parametrize(
        "secs,expected",
        [
            (0, "0s"),
            (59, "59s"),
            (60, "1:00"),
            (3599, "59:59"),
            (3600, "1:00:00"),
            (3725, "1:02:05"),
        ],
    )
    def test_basics(self, secs, expected):
        assert _format_duration(secs) == expected


class TestPhaseOrderInvariants:
    def test_phase_order_has_expected_entries(self):
        for p in ("discover", "register", "preprocess", "fuse", "write"):
            assert p in PHASE_ORDER
