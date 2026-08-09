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


class TestOverlapIsUnmissableInTheUI:
    """The setting is a one-way door, so the UI has to say so.

    The overview fixes the tile grid step; Collect Tiles re-images those exact
    stage positions. Overlap chosen here is therefore inherited by every later
    acquisition and cannot be raised afterwards without moving every tile — at
    which point the tiles the user selected on the overview no longer match
    what would be acquired. A quiet spin box in the corner of a grid was not
    enough: a 104-tile brain was acquired at 0.013% overlap and nobody saw it
    until the stitch came back with seams.

    One QApplication and one dialog for the class: constructing this dialog
    per-test and letting Qt garbage-collect it segfaults the interpreter.
    """

    @classmethod
    def setup_class(cls):
        pytest.importorskip("PyQt5")
        from PyQt5.QtWidgets import QApplication

        from py2flamingo.views.dialogs.led_2d_overview_dialog import (
            LED2DOverviewDialog,
        )

        # Held on the class so neither is collected mid-run.
        cls._qapp = QApplication.instance() or QApplication([])
        cls._dlg = LED2DOverviewDialog(app=None)

    @classmethod
    def teardown_class(cls):
        dlg = getattr(cls, "_dlg", None)
        if dlg is not None:
            dlg.deleteLater()
            cls._dlg = None

    def test_overlap_defaults_to_ten_percent_not_zero(self):
        assert self._dlg.tile_overlap.value() == pytest.approx(10.0)

    def test_it_has_its_own_framed_row_not_a_grid_cell(self):
        from PyQt5.QtWidgets import QFrame

        assert self._dlg.findChild(QFrame, "tileOverlapRow") is not None

    def test_there_is_a_bang_bang_explainer(self):
        btn = self._dlg._overlap_help_btn
        assert btn.text() == "!!"
        tip = btn.toolTip()
        # It must actually say the two things that were missed.
        assert "PERMANENT" in tip
        assert "cannot be increased afterwards" in tip

    @pytest.mark.parametrize("value", [0.0, 1.0, 4.9])
    def test_below_five_percent_goes_red(self, value):
        self._dlg.tile_overlap.setValue(value)
        label = self._dlg._overlap_warning_label
        assert "#c62828" in label.styleSheet()
        assert "too little to stitch" in label.text()

    @pytest.mark.parametrize("value", [5.0, 10.0, 30.0])
    def test_five_percent_and_above_is_calm(self, value):
        self._dlg.tile_overlap.setValue(value)
        assert "#c62828" not in self._dlg._overlap_warning_label.styleSheet()

    def test_the_row_border_tracks_the_same_threshold(self):
        from PyQt5.QtWidgets import QFrame

        frame = self._dlg.findChild(QFrame, "tileOverlapRow")
        self._dlg.tile_overlap.setValue(0.0)
        assert "#c62828" in frame.styleSheet()
        self._dlg.tile_overlap.setValue(10.0)
        assert "#c62828" not in frame.styleSheet()

    def test_the_resting_message_still_states_the_consequence(self):
        self._dlg.tile_overlap.setValue(10.0)
        assert "every acquisition" in self._dlg._overlap_warning_label.text()


class TestFocusStackingIsNotAnAcquisitionChoice:
    """ "Use focus stacking" was a checkbox that could only do harm.

    Every projection is computed from the same Z sweep by
    _calculate_projections, "focus_stack" (Extended Depth of Focus) included,
    and all of them reach the results window. The checkbox did not change what
    was captured — it only overwrote images["best_focus"] with a focus
    composite. Checking it therefore made two result options byte-identical and
    threw away the single sharpest plane, which is the one thing "Best Focus"
    is supposed to mean.

    A choice that must be made before a 4-hour acquisition, cannot be undone
    afterwards, and whose only effect is to destroy information, is worse than
    no choice at all.
    """

    def _dialog_source(self):
        from pathlib import Path

        return (
            Path(__file__).resolve().parents[1]
            / "src/py2flamingo/views/dialogs/led_2d_overview_dialog.py"
        ).read_text(encoding="utf-8")

    def _workflow_source(self):
        from pathlib import Path

        return (
            Path(__file__).resolve().parents[1]
            / "src/py2flamingo/workflows/led_2d_overview_workflow.py"
        ).read_text(encoding="utf-8")

    def test_the_checkbox_is_gone(self):
        assert "focus_stacking_checkbox" not in self._dialog_source()

    def test_best_focus_is_always_the_single_sharpest_plane(self):
        src = self._workflow_source()
        assert "if self._config.use_focus_stacking:" not in src
        assert "max(frames, key=lambda f: f[2])" in src

    def test_extended_depth_of_focus_is_still_offered_in_the_results(self):
        """Removing the checkbox must not remove the capability."""
        from py2flamingo.models.data.overview_results import VISUALIZATION_TYPES

        keys = [k for k, _label in VISUALIZATION_TYPES]
        assert "focus_stack" in keys
        assert "best_focus" in keys

    def test_the_projection_is_computed_regardless(self):
        src = self._workflow_source()
        assert 'projections["focus_stack"]' in src

    def test_old_sessions_still_load(self):
        """The config field stays so a saved session does not fail to open."""
        import pytest

        pytest.importorskip("PyQt5")
        from py2flamingo.views.dialogs.led_2d_overview_dialog import ScanConfiguration

        assert "use_focus_stacking" in ScanConfiguration.__dataclass_fields__
