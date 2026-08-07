"""LED 2D Overview must acquire tiles WITH overlap, and let the user set it.

The tile grid was generated with ``step = fov`` under a comment reading "No
overlap - tiles are adjacent". Butting tiles edge-to-edge leaves the stitcher
no shared content to register on, and any stage-repeatability error becomes a
visible seam — the tiles were being collected with zero overlap and no control
anywhere in the UI to change it.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \\
        tests/test_led_overview_tile_overlap.py -q
"""

import pytest

from py2flamingo.utils.tile_geometry import OVERLAP_PERCENT_MAX
from py2flamingo.workflows.led_2d_overview_workflow import LED2DOverviewWorkflow


class _Config:
    """Minimal stand-in for ScanConfiguration."""

    def __init__(self, **kw):
        self.tile_overlap_percent = kw.pop("tile_overlap_percent", 10.0)
        for k, v in kw.items():
            setattr(self, k, v)


def _workflow(config):
    """A workflow instance without running __init__ (which needs hardware)."""
    wf = LED2DOverviewWorkflow.__new__(LED2DOverviewWorkflow)
    wf._config = config
    return wf


class TestOverlapResolution:
    def test_the_configured_overlap_is_used(self):
        assert (
            _workflow(_Config(tile_overlap_percent=25.0))._tile_overlap_percent()
            == 25.0
        )

    def test_zero_is_honoured_when_explicitly_asked_for(self):
        """0% is a legitimate choice — it just must not be the silent default."""
        assert (
            _workflow(_Config(tile_overlap_percent=0.0))._tile_overlap_percent() == 0.0
        )

    def test_a_config_predating_the_field_does_not_fall_back_to_zero(self):
        """Zero was the bug; an old saved config must get the sane default."""
        wf = _workflow(_Config())
        del wf._config.tile_overlap_percent
        assert wf._tile_overlap_percent() == 10.0

    def test_it_is_clamped_to_the_server_tiling_limit(self):
        assert _workflow(
            _Config(tile_overlap_percent=90.0)
        )._tile_overlap_percent() == (OVERLAP_PERCENT_MAX)
        assert (
            _workflow(_Config(tile_overlap_percent=-5.0))._tile_overlap_percent() == 0.0
        )

    def test_garbage_does_not_raise(self):
        assert (
            _workflow(_Config(tile_overlap_percent="lots"))._tile_overlap_percent()
            == 10.0
        )

    @pytest.mark.parametrize(
        "pct,expected_fraction", [(0.0, 0.0), (10.0, 0.1), (25.0, 0.25), (50.0, 0.5)]
    )
    def test_fraction_matches_percent(self, pct, expected_fraction):
        wf = _workflow(_Config(tile_overlap_percent=pct))
        assert wf._tile_overlap_fraction() == pytest.approx(expected_fraction)


class TestTheStepShrinksByTheOverlap:
    """step = FOV x (1 - overlap) — the arithmetic the grid actually uses."""

    FOV = 2.1454  # mm

    @pytest.mark.parametrize(
        "pct,expected_step",
        [(0.0, 2.1454), (10.0, 1.93086), (25.0, 1.60905), (50.0, 1.0727)],
    )
    def test_step_for_a_real_fov(self, pct, expected_step):
        wf = _workflow(_Config(tile_overlap_percent=pct))
        step = self.FOV * (1.0 - wf._tile_overlap_fraction())
        assert step == pytest.approx(expected_step, abs=1e-5)

    def test_more_overlap_means_more_tiles_over_the_same_region(self):
        """The user-visible consequence: denser sampling, longer run."""
        span = 6.0  # mm

        def tiles(pct):
            wf = _workflow(_Config(tile_overlap_percent=pct))
            step = self.FOV * (1.0 - wf._tile_overlap_fraction())
            return max(1, int(span / step) + 1)

        assert tiles(0.0) == 3
        assert tiles(10.0) == 4
        assert tiles(50.0) == 6


class TestTheDialogExposesIt:
    def test_the_scan_configuration_carries_the_field(self):
        from py2flamingo.views.dialogs.led_2d_overview_dialog import ScanConfiguration

        assert "tile_overlap_percent" in ScanConfiguration.__dataclass_fields__

    def test_it_does_not_default_to_zero(self):
        """A default of 0 would reintroduce the bug for anyone who never looks."""
        from py2flamingo.views.dialogs.led_2d_overview_dialog import ScanConfiguration

        default = ScanConfiguration.__dataclass_fields__["tile_overlap_percent"].default
        assert default > 0


class TestTheRealGridUsesTheOverlap:
    """Drives _generate_tile_positions itself.

    The arithmetic tests above recompute `fov * (1 - overlap)` in the test,
    which means they still pass if the production code goes back to
    `step = fov`. These call the real generator so that mutation fails.
    """

    FOV = 2.1454  # mm

    class _Bbox:
        tile_x_min, tile_x_max = 0.0, 6.0
        tile_y_min, tile_y_max = 0.0, 6.0
        z_min, z_max = 1.0, 3.0

    def _positions(self, pct):
        wf = _workflow(_Config(tile_overlap_percent=pct))
        wf._actual_fov_mm = self.FOV
        # Stage-limit fitting is a separate concern; pass everything through.
        wf._fit_positions_to_limits = lambda positions, axis: (positions, True)
        return wf._generate_tile_positions(self._Bbox())

    def _x_pitch(self, pct):
        xs = sorted({p[0] for p in self._positions(pct)})
        assert len(xs) >= 2, "need at least two columns to measure a pitch"
        return xs[1] - xs[0]

    def test_the_pitch_is_a_full_fov_only_at_zero_overlap(self):
        assert self._x_pitch(0.0) == pytest.approx(self.FOV, abs=1e-6)

    def test_the_pitch_shrinks_by_the_requested_overlap(self):
        assert self._x_pitch(10.0) == pytest.approx(self.FOV * 0.90, abs=1e-6)
        assert self._x_pitch(25.0) == pytest.approx(self.FOV * 0.75, abs=1e-6)
        assert self._x_pitch(50.0) == pytest.approx(self.FOV * 0.50, abs=1e-6)

    def test_neighbouring_tiles_actually_share_image_area(self):
        """The point of the whole exercise: something for the stitcher to use."""
        pitch = self._x_pitch(25.0)
        shared_mm = self.FOV - pitch
        assert shared_mm > 0
        assert shared_mm / self.FOV == pytest.approx(0.25, abs=1e-6)

    def test_more_overlap_yields_more_tiles(self):
        assert len(self._positions(25.0)) > len(self._positions(0.0))
