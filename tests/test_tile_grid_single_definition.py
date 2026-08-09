"""The predicted tile grid and the scanned tile grid must be the same grid.

The 2026-08-08 LED overview logged, from its own estimate:

    Starting LED 2D Overview: ~273 total tiles, FOV: 1.0727 mm,
    overlap: 10.0% (step 0.9654 mm)

and then laid tiles down at 1.0727 mm — a full FOV, no overlap. The acquired
grid was 9x12 where 10x14 was predicted, and nothing said so until a
results-window warning fired on 228 tiles that were already on disk and whose
positions could no longer be changed.

Two causes, both structural rather than arithmetic:

* the step was computed in two places, and identical source lines are no
  guarantee of identical behaviour at runtime;
* the *count* was derived independently of the position walk
  (``int(w / step) + 1`` against ``while x <= hi + step / 2``), which disagree
  by a whole row whenever the range does not divide evenly.

Both now have exactly one definition. These tests fail if a second appears.

Run: QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest \\
        tests/test_tile_grid_single_definition.py -q
"""

import ast
from pathlib import Path

import pytest

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / "src/py2flamingo/workflows/led_2d_overview_workflow.py"
)

# The rig's real numbers.
FOV_MM = 1.0727
BBOX_X = (2.0, 11.0)
BBOX_Y = (11.725, 24.0)


def _cls():
    pytest.importorskip("PyQt5")
    from py2flamingo.workflows.led_2d_overview_workflow import LED2DOverviewWorkflow

    return LED2DOverviewWorkflow


class TestThereIsOneDefinitionOfTheStep:
    def test_the_step_is_not_computed_inline_anywhere(self):
        """`fov * (1 - overlap)` must appear only inside _tile_step_mm."""
        src = WORKFLOW.read_text(encoding="utf-8")
        occurrences = src.count("(1.0 - self._tile_overlap_fraction())")
        assert occurrences == 1, (
            f"the step is computed in {occurrences} places; it diverged at "
            "runtime once already. Route every caller through _tile_step_mm()"
        )

    def test_tile_step_mm_exists_and_applies_the_overlap(self):
        cls = _cls()
        wf = cls.__new__(cls)
        wf._actual_fov_mm = FOV_MM
        wf._config = type("C", (), {"tile_overlap_percent": 10.0})()
        assert wf._tile_step_mm() == pytest.approx(FOV_MM * 0.9)

    def test_no_fov_means_no_step_rather_than_a_wrong_one(self):
        cls = _cls()
        wf = cls.__new__(cls)
        wf._actual_fov_mm = None
        wf._config = type("C", (), {"tile_overlap_percent": 10.0})()
        assert wf._tile_step_mm() is None


class TestCountingIsTheSameWalkAsScanning:
    """A prediction derived differently from the action is not a prediction."""

    def test_the_rig_bounding_box_gives_the_grid_the_overlap_implies(self):
        cls = _cls()
        step = FOV_MM * 0.9
        nx = len(cls.tile_positions_1d(*BBOX_X, step))
        ny = len(cls.tile_positions_1d(*BBOX_Y, step))
        assert (nx, ny) == (10, 14), "10% overlap over the rig's bbox is 10x14"

    def test_zero_overlap_reproduces_the_grid_that_was_actually_scanned(self):
        """Confirms the diagnosis: 9x12 is exactly step == FOV."""
        cls = _cls()
        nx = len(cls.tile_positions_1d(*BBOX_X, FOV_MM))
        ny = len(cls.tile_positions_1d(*BBOX_Y, FOV_MM))
        assert (nx, ny) == (9, 12)

    def test_the_old_int_formula_disagreed_with_the_walk(self):
        """The 130-vs-140 discrepancy, pinned so it cannot come back."""
        cls = _cls()
        step = FOV_MM * 0.9
        walk_y = len(cls.tile_positions_1d(*BBOX_Y, step))
        old_y = int((BBOX_Y[1] - BBOX_Y[0]) / step) + 1
        assert walk_y == 14 and old_y == 13, (
            "the two counting methods must be shown to differ, or this test "
            "is not guarding anything"
        )

    @pytest.mark.parametrize(
        "lo,hi,step",
        [(0.0, 10.0, 1.0), (0.0, 9.5, 1.0), (2.0, 11.0, 0.9654), (5.0, 5.0, 1.0)],
    )
    def test_the_walk_never_returns_an_empty_grid(self, lo, hi, step):
        cls = _cls()
        assert len(cls.tile_positions_1d(lo, hi, step)) >= 1

    def test_a_degenerate_step_does_not_hang_or_divide_by_zero(self):
        cls = _cls()
        assert cls.tile_positions_1d(0.0, 10.0, 0.0) == [0.0]

    def test_positions_start_at_the_low_edge_and_stay_in_range(self):
        cls = _cls()
        step = FOV_MM * 0.9
        pos = cls.tile_positions_1d(*BBOX_X, step)
        assert pos[0] == BBOX_X[0]
        assert pos[-1] <= BBOX_X[1] + step / 2


class TestTheRunSaysWhatItIsAboutToDo:
    """The last moment the geometry can still be corrected is before scanning."""

    def _generator_source(self):
        src = WORKFLOW.read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_generate_tile_positions"
        )
        return ast.get_source_segment(src, fn) or ""

    def test_the_grid_is_logged_before_the_stage_moves(self):
        body = self._generator_source()
        assert "Tile grid for R=" in body
        assert "overlap" in body

    def test_requesting_overlap_but_getting_a_full_fov_step_is_an_error(self):
        """The exact 2026-08-08 failure, made loud instead of silent."""
        body = self._generator_source()
        assert "logger.error" in body
        assert "butt edge to edge" in body

    def test_the_step_is_logged_at_info_not_debug(self):
        """A DEBUG line is absent from the rig's logs, where it is needed."""
        body = self._generator_source()
        assert 'logger.info(\n            f"Tile step size' in body or (
            "Tile step size" in body and "logger.debug" not in body
        )


class TestTheLogCanAlwaysNameItsOwnBuild:
    def test_the_version_helper_never_returns_none(self):
        from py2flamingo.cli import _get_git_version

        v = _get_git_version()
        assert isinstance(v, str) and v, "the banner must always say something"

    def test_a_failure_explains_itself(self, monkeypatch):
        """Silence is what made 'which build ran?' unanswerable."""
        import subprocess

        import py2flamingo.cli as cli

        def _boom(*a, **k):
            raise FileNotFoundError("git")

        monkeypatch.setattr(subprocess, "run", _boom)
        v = cli._get_git_version()
        assert "unknown" in v and "git" in v
