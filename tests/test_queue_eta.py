"""Regression tests for the tile-queue ETA math (`_queue_eta_seconds`).

The live queue ETA used to compute ``per_frame_mean * (frames_left +
workflows_remaining * cur_exp)`` -- i.e. it costed *every* remaining
tile's frames at the per-frame rate. Right at a tile switch the per-frame
mean briefly reverts to a coarse cross-run cache seed (the per-tile
history was just ``reset()``), and the per-tile ``expected`` count can be
momentarily off for one gauge callback. Multiplying that inflated
per-frame value across all remaining tiles ballooned a several-minute run
to hours, then it snapped back once the Z scan produced real samples.

The fix splits the estimate: current tile prorated by the per-frame
cadence, remaining whole tiles costed at the measured end-to-end per-tile
time (which already includes the XY move onto each tile). These tests pin
that a boundary transient can no longer explode the estimate.
"""

import pytest

from py2flamingo.views.dialogs.tile_collection_dialog import _queue_eta_seconds


class TestQueueEtaSeconds:
    def test_returns_none_without_any_signal(self):
        assert (
            _queue_eta_seconds(
                img_mean_ms=None,
                tile_mean_ms=None,
                cur_acq=0,
                cur_exp=0,
                workflows_remaining=5,
            )
            is None
        )

    def test_steady_state_current_plus_future(self):
        # 100 ms/frame, 60 s/tile end-to-end. Current tile: 300 of 400
        # planes done -> 100 frames left = 10 s. Remaining tiles: 5 * 60 s.
        secs = _queue_eta_seconds(
            img_mean_ms=100.0,
            tile_mean_ms=60_000.0,
            cur_acq=300,
            cur_exp=400,
            workflows_remaining=5,
        )
        assert secs == pytest.approx(10.0 + 300.0)

    def test_boundary_seed_spike_does_not_explode(self):
        # The failure mode: at a tile switch img_mean_ms is the coarse
        # seed (say 800 ms) AND cur_exp is briefly wrong (huge). With a
        # per-tile time available, future tiles ignore img_mean_ms/cur_exp
        # entirely, so the estimate stays bounded near the true per-tile
        # total instead of jumping to hours.
        good = _queue_eta_seconds(
            img_mean_ms=100.0,
            tile_mean_ms=60_000.0,
            cur_acq=0,
            cur_exp=400,
            workflows_remaining=11,
        )
        spiked = _queue_eta_seconds(
            img_mean_ms=800.0,  # seed, ~8x per-frame
            tile_mean_ms=60_000.0,
            cur_acq=1,
            cur_exp=99_999,  # transient garbage expected count
            workflows_remaining=11,
        )
        # Future tiles are unaffected (per-tile time). The only difference
        # is the current tile's proration, bounded to one tile's work.
        one_tile = 60_000.0 / 1000.0
        assert spiked <= good + one_tile + 1.0
        # And nowhere near the old ~hours-long blow-up.
        assert spiked < 900.0  # << the ~7200 s (2 h) the old form produced

    def test_old_form_would_have_exploded(self):
        # Documents the magnitude of the bug the split fixes: the old
        # "per-frame * all remaining frames" form on the same transient.
        img_mean_ms, cur_exp, workflows_remaining = 800.0, 99_999, 11
        old_seconds = img_mean_ms * (workflows_remaining * cur_exp) / 1000.0
        assert old_seconds > 7200.0  # > 2 hours -- exactly the reported jump

    def test_first_tile_fallback_uses_frames(self):
        # No per-tile time yet (fresh install, no seed): fall back to the
        # per-frame approximation so an ETA still appears.
        secs = _queue_eta_seconds(
            img_mean_ms=100.0,
            tile_mean_ms=None,
            cur_acq=0,
            cur_exp=400,
            workflows_remaining=2,
        )
        # 400 frames current + 2 * 400 future, all at 100 ms.
        assert secs == pytest.approx((400 + 800) * 100 / 1000.0)

    def test_no_negative_when_acq_exceeds_expected(self):
        secs = _queue_eta_seconds(
            img_mean_ms=100.0,
            tile_mean_ms=60_000.0,
            cur_acq=500,  # > expected (stale/overshoot)
            cur_exp=400,
            workflows_remaining=0,
        )
        assert secs == pytest.approx(0.0)
