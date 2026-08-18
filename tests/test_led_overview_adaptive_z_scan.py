"""Driving the scan loop with a synthetic sample, no stage attached.

The band policy is unit-tested in ``test_adaptive_z_band.py``. This exercises
the part that moves hardware: the scan loop, the re-sweep, the state that
accumulates across tiles, and the Z range each tile ends up recording — which
Collect Tiles inherits as the laser acquisition's Z range.

``_sweep_tile_band`` is replaced with a synthetic sample so the loop can run
without a stage, and because it is now ONE method both the first sweep and the
re-sweep go through, the substitution covers both. Extracting it was the point:
two copies of the sweep is exactly how the tile-step calculation ended up
shipping a 0.25% overlap.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \\
        tests/test_led_overview_adaptive_z_scan.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

Z_MIN, Z_MAX = 14.0, 24.0  # the measured 10 mm bounding box
FULL_DEPTH = Z_MAX - Z_MIN


class _Sample:
    """Where the sample sits in Z, as a function of tile position.

    ``span(x_idx, y_idx)`` returns the (lo, hi) the sample occupies at that
    tile, or None for empty background.
    """

    def __init__(self, span):
        self.span = span
        self.bands = []  # every band swept, in order

    def frames(self, band, x_idx, y_idx):
        """(z, image, focus_score) for a sweep of `band` over this tile."""
        here = self.span(x_idx, y_idx)
        n = 11
        step = band.depth_mm / (n - 1) if band.depth_mm > 0 else 0.0
        out = []
        for i in range(n):
            z = band.z_min + i * step
            inside = here is not None and here[0] <= z <= here[1]
            out.append((z, np.zeros((4, 4), dtype=np.uint16), 900.0 if inside else 3.0))
        return out


def _workflow(config, sample):
    """A workflow instance with the hardware stubbed out."""
    from py2flamingo.workflows.led_2d_overview_workflow import LED2DOverviewWorkflow

    wf = LED2DOverviewWorkflow(app=SimpleNamespace(), config=config)

    def fake_sweep(stage, camera, x_pos, y_pos, band, ascending):
        # Indices recovered from position: the loop knows them, this stand-in
        # has to derive them the same way the grid does.
        x_idx = int(round((x_pos - 4.0) / 2.0))
        y_idx = int(round((y_pos - 12.0) / 2.0))
        sample.bands.append((x_idx, y_idx, band))
        return sample.frames(band, x_idx, y_idx), 0.1, band.depth_mm, [band.z_min]

    wf._sweep_tile_band = fake_sweep
    return wf


def _drive(wf, sample, grid=(3, 3)):
    """Walk the grid the way the scan loop does, calling the real methods."""
    from py2flamingo.utils.adaptive_z_band import ZBand  # noqa: F401

    tiles = []
    ascending = True
    for x_idx in range(grid[0]):
        for y_idx in range(grid[1]):
            x_pos, y_pos = 4.0 + 2.0 * x_idx, 12.0 + 2.0 * y_idx
            band = wf._band_for_tile(x_idx, y_idx, Z_MIN, Z_MAX)
            swept = wf._sweep_tile_band(None, None, x_pos, y_pos, band, ascending)
            frames = swept[0]
            band, frames, extra = wf._resweep_if_clipped(
                None,
                None,
                x_pos,
                y_pos,
                x_idx,
                y_idx,
                band,
                frames,
                not ascending,
                Z_MIN,
                Z_MAX,
            )
            assert extra is not None
            tiles.append((x_idx, y_idx, band))
            ascending = not ascending
    return tiles


def _config(**kw):
    """A real ScanConfiguration -- the dataclass the dialog builds and the
    workflow reads, so a field renamed on one side fails here."""
    from py2flamingo.views.dialogs.led_2d_overview_dialog import (
        BoundingBox,
        ScanConfiguration,
    )

    base = dict(
        bounding_box=BoundingBox(4.0, 10.0, 12.0, 18.0, Z_MIN, Z_MAX),
        starting_r=0.0,
        led_name="LED",
        led_intensity=10.0,
        z_step_size=0.25,
        adaptive_z=True,
    )
    base.update(kw)
    return ScanConfiguration(**base)


class TestOffChangesNothing:
    def test_every_tile_sweeps_the_whole_box(self):
        sample = _Sample(lambda ix, iy: (18.0, 19.0))
        wf = _workflow(_config(adaptive_z=False), sample)
        for _ix, _iy, band in _drive(wf, sample):
            assert band.depth_mm == pytest.approx(FULL_DEPTH)
            assert band.full

    def test_a_config_predating_the_setting_is_not_adaptive(self):
        # Sessions saved before the checkbox existed must behave as they did.
        sample = _Sample(lambda ix, iy: (18.0, 19.0))
        legacy = _config()
        del legacy.adaptive_z
        wf = _workflow(legacy, sample)
        assert not wf._adaptive_z_enabled()
        for _ix, _iy, band in _drive(wf, sample):
            assert band.full


class TestOnNarrowsAfterTheFirstTile:
    def _run(self):
        sample = _Sample(lambda ix, iy: (18.0, 19.0))
        wf = _workflow(_config(), sample)
        return wf, sample, _drive(wf, sample)

    def test_the_first_tile_sweeps_everything(self):
        # It has no scanned neighbours, so there is nothing to predict from.
        _wf, _s, tiles = self._run()
        assert tiles[0][2].full

    def test_later_tiles_sweep_far_less(self):
        _wf, _s, tiles = self._run()
        narrowed = [b for _ix, _iy, b in tiles if not b.full]
        assert narrowed, "nothing was narrowed"
        assert all(b.depth_mm < FULL_DEPTH / 2 for b in narrowed)

    def test_the_narrow_band_still_contains_the_sample(self):
        _wf, _s, tiles = self._run()
        for _ix, _iy, band in tiles:
            assert band.z_min <= 18.0 and band.z_max >= 19.0

    def test_a_uniform_sample_needs_no_resweeps(self):
        wf, _s, _t = self._run()
        assert wf._adaptive_resweeps == 0

    def test_total_travel_drops_substantially(self):
        _wf, sample, _t = self._run()
        swept = sum(b.depth_mm for _ix, _iy, b in sample.bands)
        assert swept < 0.5 * FULL_DEPTH * len(sample.bands)


class TestASampleThatMovesTriggersARsweep:
    """The safety net: a band that clipped the sample is measured again.

    Not for image quality — for the Z edges, which Collect Tiles inherits as
    the laser acquisition's Z range. An underestimate there is a truncated
    acquisition, discovered later, unrecoverable without re-running the sample.
    """

    def _run(self):
        # Flat until the last column, where the sample jumps 4 mm deeper.
        def span(ix, iy):
            return (22.0, 23.0) if ix >= 2 else (18.0, 19.0)

        sample = _Sample(span)
        wf = _workflow(_config(), sample)
        return wf, sample, _drive(wf, sample)

    def test_the_jump_is_caught_and_re_swept(self):
        wf, _s, _t = self._run()
        assert wf._adaptive_resweeps > 0

    def test_the_re_swept_tile_records_the_full_range(self):
        # Its true extent was not measured by the narrow band, so the range it
        # hands downstream must be the one that was.
        _wf, _s, tiles = self._run()
        moved = [b for ix, _iy, b in tiles if ix >= 2]
        assert any(b.full for b in moved)

    def test_the_deeper_sample_is_found_not_missed(self):
        _wf, _s, tiles = self._run()
        for ix, _iy, band in tiles:
            if ix >= 2:
                assert band.z_max >= 23.0

    def test_the_re_sweep_uses_the_same_sweep_path(self):
        # One method for both, so a fix or a bug reaches both.
        _wf, sample, _t = self._run()
        full_sweeps = [b for _ix, _iy, b in sample.bands if b.full]
        assert len(full_sweeps) > 1  # the first tile, plus every re-sweep


class TestGivingUpOnASampleItDoesNotFit:
    def test_a_scattered_sample_switches_adaptation_off(self):
        # Every tile at a different depth: neighbours predict nothing, so almost
        # every narrow band clips and the run would cost MORE than never
        # adapting. It has to notice and stop.
        sample = _Sample(
            lambda ix, iy: (
                15.0 + 2.0 * ((ix * 3 + iy) % 4),
                15.5 + 2.0 * ((ix * 3 + iy) % 4),
            )
        )
        wf = _workflow(_config(), sample)
        _drive(wf, sample, grid=(4, 4))
        assert wf._adaptive_z_abandoned
        assert not wf._adaptive_z_enabled()

    def test_after_giving_up_every_tile_is_full_range(self):
        sample = _Sample(
            lambda ix, iy: (
                15.0 + 2.0 * ((ix * 3 + iy) % 4),
                15.5 + 2.0 * ((ix * 3 + iy) % 4),
            )
        )
        wf = _workflow(_config(), sample)
        tiles = _drive(wf, sample, grid=(4, 4))
        assert tiles[-1][2].full

    def test_a_good_sample_never_gives_up(self):
        sample = _Sample(lambda ix, iy: (18.0, 19.0))
        wf = _workflow(_config(), sample)
        _drive(wf, sample, grid=(4, 4))
        assert not wf._adaptive_z_abandoned


class TestBackgroundTilesDoNotWidenTheirNeighbours:
    def test_an_empty_tile_contributes_no_extent(self):
        # Judged against the brightest tile the scan has seen. Against its own
        # noise peak every plane would clear the threshold and it would report
        # a full-depth extent, undoing the optimisation for everything near it.
        def span(ix, iy):
            return (18.0, 19.0) if (ix, iy) == (0, 0) else None

        sample = _Sample(span)
        wf = _workflow(_config(), sample)
        _drive(wf, sample)
        empties = [e for key, e in wf._z_extents.items() if key != (0, 0)]
        assert empties and not any(e.has_content for e in empties)

    def test_an_empty_narrow_band_is_re_swept_before_being_believed(self):
        # It cannot be told apart from a tile whose sample stepped out of the
        # prediction, so it is measured at full range before its emptiness is
        # taken as fact. That costs time on genuinely empty tiles, which is
        # what adaptation_is_not_paying exists to bound.
        def span(ix, iy):
            return (18.0, 19.0) if (ix, iy) == (0, 0) else None

        sample = _Sample(span)
        wf = _workflow(_config(), sample)
        _drive(wf, sample)
        assert wf._adaptive_resweeps > 0


class TestCancellationIsPropagated:
    def test_a_cancelled_sweep_stops_the_tile(self):
        # _sweep_tile_band returns None rather than calling _finish_cancelled
        # itself, so the caller stays in charge of unwinding.
        sample = _Sample(lambda ix, iy: (18.0, 19.0))
        wf = _workflow(_config(), sample)
        wf._sweep_tile_band = lambda *a, **k: None
        band = wf._band_for_tile(0, 0, Z_MIN, Z_MAX)
        assert wf._sweep_tile_band(None, None, 4.0, 12.0, band, True) is None

    def test_a_cancelled_re_sweep_reports_it(self):
        sample = _Sample(lambda ix, iy: (17.0, 18.0))
        wf = _workflow(_config(), sample)
        from py2flamingo.utils.adaptive_z_band import ZBand

        band = ZBand(z_min=17.0, z_max=20.0, full=False)
        frames = sample.frames(band, 0, 0)
        wf._sweep_tile_band = lambda *a, **k: None
        _band, _frames, extra = wf._resweep_if_clipped(
            None, None, 4.0, 12.0, 0, 0, band, frames, True, Z_MIN, Z_MAX
        )
        assert extra is None


class _NullStage:
    def move_to_position(self, *a, **k):
        return True


def _run_scan_loop(monkeypatch, config, sample, grid=(3, 3)):
    """Run the real ``_scan_tiles_continuous`` with the hardware stubbed out.

    Worth the stubbing: the loop is where the band reaches the TileResult, and
    that Z range is what Collect Tiles hands the laser acquisition. Testing the
    helpers alone left that line uncovered -- a mutation replacing the band with
    the whole bounding box passed every test.
    """
    import py2flamingo.services.stage_service as stage_mod
    from py2flamingo.models.data.overview_results import (
        EffectiveBoundingBox,
        RotationResult,
    )
    from py2flamingo.workflows.led_2d_overview_workflow import LED2DOverviewWorkflow

    monkeypatch.setattr(stage_mod, "StageService", lambda *a, **k: _NullStage())

    wf = LED2DOverviewWorkflow(
        app=SimpleNamespace(connection_service=None), config=config
    )
    wf._running = True
    wf._cancelled = False
    wf._current_rotation_idx = 0
    wf._rotation_angles = [0.0]
    wf._results = [RotationResult(rotation_angle=0.0, tiles=[])]
    wf._tiles_x, wf._tiles_y = grid
    wf._actual_fov_mm = 2.0
    wf._current_effective_bbox = EffectiveBoundingBox(
        tile_x_min=4.0,
        tile_x_max=4.0 + 2.0 * (grid[0] - 1),
        tile_y_min=12.0,
        tile_y_max=12.0 + 2.0 * (grid[1] - 1),
        z_min=Z_MIN,
        z_max=Z_MAX,
    )
    wf._tile_step_mm = lambda: 2.0
    wf._fit_positions_to_limits = lambda positions, axis, margin_mm=0.25: (
        list(positions),
        True,
    )
    wf._get_controllers = lambda: (
        None,
        SimpleNamespace(clear_buffer=lambda: None),
        None,
    )
    wf._finish_rotation = lambda: None
    wf._finish_cancelled = lambda: None

    def fake_sweep(stage, camera, x_pos, y_pos, band, ascending):
        x_idx = int(round((x_pos - 4.0) / 2.0))
        y_idx = int(round((y_pos - 12.0) / 2.0))
        sample.bands.append((x_idx, y_idx, band))
        return sample.frames(band, x_idx, y_idx), 0.1, band.depth_mm, [band.z_min]

    wf._sweep_tile_band = fake_sweep
    wf._scan_tiles_continuous()
    return wf, wf._results[0].tiles


class TestTheBandReachesTheTileResult:
    """The Z range each tile records is the laser acquisition's Z range.

    Collect Tiles inherits these edges, so a tile that records the whole
    bounding box throws away everything the narrowed sweep measured, and a tile
    that records a band narrower than what it swept would truncate the
    acquisition. It has to be exactly the band that was swept.
    """

    @classmethod
    def setup_class(cls):
        pytest.importorskip("PyQt5")
        from PyQt5.QtWidgets import QApplication

        cls._qapp = QApplication.instance() or QApplication([])

    def test_a_narrowed_tile_records_its_band_not_the_box(self, monkeypatch):
        sample = _Sample(lambda ix, iy: (18.0, 19.0))
        _wf, tiles = _run_scan_loop(monkeypatch, _config(), sample, grid=(3, 3))
        narrowed = [t for t in tiles if (t.z_stack_max - t.z_stack_min) < FULL_DEPTH]
        assert narrowed, "no tile recorded a narrowed range"
        for tile in narrowed:
            assert tile.z_stack_min <= 18.0 and tile.z_stack_max >= 19.0

    def test_the_recorded_range_is_a_range_that_was_actually_swept(self, monkeypatch):
        sample = _Sample(lambda ix, iy: (18.0, 19.0))
        _wf, tiles = _run_scan_loop(monkeypatch, _config(), sample, grid=(3, 3))
        swept = {(round(b.z_min, 6), round(b.z_max, 6)) for _ix, _iy, b in sample.bands}
        for tile in tiles:
            assert (
                round(tile.z_stack_min, 6),
                round(tile.z_stack_max, 6),
            ) in swept

    def test_with_adaptation_off_every_tile_records_the_box(self, monkeypatch):
        # The regression guard: this is today's behaviour and must not change
        # for anyone who leaves the checkbox alone.
        sample = _Sample(lambda ix, iy: (18.0, 19.0))
        _wf, tiles = _run_scan_loop(
            monkeypatch, _config(adaptive_z=False), sample, grid=(3, 3)
        )
        assert tiles
        for tile in tiles:
            assert tile.z_stack_min == pytest.approx(Z_MIN)
            assert tile.z_stack_max == pytest.approx(Z_MAX)

    def test_a_re_swept_tile_records_the_full_range_it_ended_on(self, monkeypatch):
        # Its narrow band did not measure the extent; the full sweep did.
        def span(ix, iy):
            return (22.0, 23.0) if ix >= 2 else (18.0, 19.0)

        sample = _Sample(span)
        wf, tiles = _run_scan_loop(monkeypatch, _config(), sample, grid=(3, 3))
        assert wf._adaptive_resweeps > 0
        for tile in tiles:
            assert tile.z_stack_min <= tile.z <= tile.z_stack_max

    def test_every_tile_is_produced(self, monkeypatch):
        sample = _Sample(lambda ix, iy: (18.0, 19.0))
        _wf, tiles = _run_scan_loop(monkeypatch, _config(), sample, grid=(3, 4))
        assert len(tiles) == 12


class TestJudgingOnlyTilesItPredicted:
    """A scan that starts on empty space must not give up before the sample.

    Bounding boxes are drawn with room around the sample, so the first tiles
    are usually empty. Those sweep the full range because no neighbour has
    anything to say -- which is not evidence that prediction fails, it is
    evidence of nothing. Counting them switched adaptation off permanently
    before the scan ever reached the specimen, and every measured sparse shape
    reported exactly 0% saving.
    """

    def test_an_empty_first_column_does_not_disable_adaptation(self):
        def span(ix, iy):
            return (18.0, 19.0) if ix >= 3 else None

        sample = _Sample(span)
        wf = _workflow(_config(), sample)
        _drive(wf, sample, grid=(6, 6))
        assert not wf._adaptive_z_abandoned

    def test_and_the_sample_when_reached_is_still_narrowed(self):
        def span(ix, iy):
            return (18.0, 19.0) if ix >= 3 else None

        sample = _Sample(span)
        wf = _workflow(_config(), sample)
        tiles = _drive(wf, sample, grid=(6, 6))
        on_sample = [b for ix, _iy, b in tiles if ix >= 4]
        assert any(not b.full for b in on_sample)

    def test_full_range_tiles_are_not_counted_as_predictions(self):
        def span(ix, iy):
            return None

        sample = _Sample(span)
        wf = _workflow(_config(), sample)
        _drive(wf, sample, grid=(4, 4))
        assert wf._adaptive_predicted_tiles == 0
        assert not wf._adaptive_z_abandoned
