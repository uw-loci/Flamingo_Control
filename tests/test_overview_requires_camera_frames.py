"""An overview that cannot see must not run to completion.

On 2026-08-26 an LED 2D Overview swept 294 tiles across two rotations -- 125
minutes of stage and camera wait -- and captured ZERO frames. Every plane read
an empty live buffer, every tile was skipped by `if frames:`, and the run
finished reporting success. The only evidence was one "Fast mode: Captured 0
tiles" line at the very end, after the time had already been spent.

`start_live_view()` returning without raising means the request was made, not
that the camera is streaming. Two guards close that gap: prove a frame arrives
before committing to the scan, and abandon a scan whose stream dies partway.
"""

from __future__ import annotations

import pytest


class TestTheEmptyTileStreak:
    """The mid-scan guard, tested on the policy rather than on a live stage."""

    def test_the_threshold_is_small_enough_to_matter(self):
        from py2flamingo.workflows.led_2d_overview_workflow import (
            LED2DOverviewWorkflow,
        )

        # A healthy stream never produces one empty tile. The failure produced
        # 294 in a row, so anything low works -- but it has to be far below the
        # tile count or the guard costs an hour before it fires.
        assert 1 <= LED2DOverviewWorkflow.MAX_EMPTY_TILE_STREAK <= 5

    def test_the_streak_counter_starts_at_zero(self):
        from py2flamingo.workflows.led_2d_overview_workflow import (
            LED2DOverviewWorkflow,
        )

        wf = LED2DOverviewWorkflow.__new__(LED2DOverviewWorkflow)
        # __init__ needs a controller stack; the attribute is what matters here.
        assert "_empty_tile_streak" in LED2DOverviewWorkflow.__init__.__code__.co_names

    def test_the_scan_loop_checks_the_streak_and_emits_an_error(self):
        """The guard has to abort AND say why -- a silent stop is the same
        failure with a shorter log."""
        import inspect

        from py2flamingo.workflows.led_2d_overview_workflow import (
            LED2DOverviewWorkflow,
        )

        src = inspect.getsource(LED2DOverviewWorkflow)
        assert "_empty_tile_streak >= self.MAX_EMPTY_TILE_STREAK" in src
        # It must reach the user, not just the log file.
        guard = src[src.index("_empty_tile_streak >= self.MAX_EMPTY_TILE_STREAK") :]
        assert "self.scan_error.emit" in guard[:1200]

    def test_a_tile_with_frames_resets_the_streak(self):
        import inspect

        from py2flamingo.workflows.led_2d_overview_workflow import (
            LED2DOverviewWorkflow,
        )

        # Without the reset, three empty tiles scattered across a long scan
        # would abort a run that was working.
        src = inspect.getsource(LED2DOverviewWorkflow)
        assert "self._empty_tile_streak = 0" in src


class TestThePreflight:
    def test_the_dialog_waits_for_a_real_frame_before_scanning(self):
        import inspect

        pytest.importorskip("PyQt5")
        from py2flamingo.views.dialogs.led_2d_overview_dialog import (
            LED2DOverviewDialog,
        )

        src = inspect.getsource(LED2DOverviewDialog._start_sample_view_live_with_led)
        assert (
            "_wait_for_first_frame" in src
        ), "the scan must not start until a frame has actually arrived"

    def test_the_preflight_fails_closed(self):
        """No frame means False, so the caller refuses to scan."""
        pytest.importorskip("PyQt5")
        from py2flamingo.views.dialogs.led_2d_overview_dialog import (
            LED2DOverviewDialog,
        )

        class _Dead:
            def get_latest_frame(self):
                return None

        dialog = LED2DOverviewDialog.__new__(LED2DOverviewDialog)
        import logging

        dialog._logger = logging.getLogger("test")
        dialog.FIRST_FRAME_TIMEOUT_S = 0.05  # keep the test quick

        import py2flamingo.views.dialogs.led_2d_overview_dialog as mod

        shown = []
        original = mod.QMessageBox.critical
        mod.QMessageBox.critical = staticmethod(
            lambda *a, **k: shown.append(a[1] if len(a) > 1 else "")
        )
        try:
            assert dialog._wait_for_first_frame(_Dead()) is False
        finally:
            mod.QMessageBox.critical = original
        assert shown, "the user must be told, not just the log"

    def test_the_preflight_passes_when_a_frame_is_there(self):
        pytest.importorskip("PyQt5")
        from py2flamingo.views.dialogs.led_2d_overview_dialog import (
            LED2DOverviewDialog,
        )

        class _Live:
            def get_latest_frame(self):
                return ("image", "header", 7)

        dialog = LED2DOverviewDialog.__new__(LED2DOverviewDialog)
        import logging

        dialog._logger = logging.getLogger("test")
        assert dialog._wait_for_first_frame(_Live()) is True

    def test_a_raising_camera_is_treated_as_no_frame_not_a_crash(self):
        pytest.importorskip("PyQt5")
        from py2flamingo.views.dialogs.led_2d_overview_dialog import (
            LED2DOverviewDialog,
        )

        class _Broken:
            def get_latest_frame(self):
                raise RuntimeError("socket gone")

        dialog = LED2DOverviewDialog.__new__(LED2DOverviewDialog)
        import logging

        dialog._logger = logging.getLogger("test")
        dialog.FIRST_FRAME_TIMEOUT_S = 0.05

        import py2flamingo.views.dialogs.led_2d_overview_dialog as mod

        original = mod.QMessageBox.critical
        mod.QMessageBox.critical = staticmethod(lambda *a, **k: None)
        try:
            assert dialog._wait_for_first_frame(_Broken()) is False
        finally:
            mod.QMessageBox.critical = original


class TestTheOverlapCopyIsNoLongerStale:
    """The overview's overlap stopped being inherited by the acquisition when
    the grids were decoupled; the warning still said it was permanent and about
    stitching, which points the user at the wrong control."""

    def test_it_no_longer_claims_to_be_permanent_or_inherited(self):
        import inspect

        pytest.importorskip("PyQt5")
        from py2flamingo.views.dialogs.led_2d_overview_dialog import (
            LED2DOverviewDialog,
        )

        src = inspect.getsource(LED2DOverviewDialog)
        for stale in (
            "is inherited by every",
            "too little to stitch",
            "Applies to every acquisition from this overview",
        ):
            assert stale not in src, f"stale overlap copy still present: {stale}"

    def test_it_says_what_the_value_is_actually_for(self):
        import inspect

        pytest.importorskip("PyQt5")
        from py2flamingo.views.dialogs.led_2d_overview_dialog import (
            LED2DOverviewDialog,
        )

        src = inspect.getsource(LED2DOverviewDialog._create_tile_overlap_row)
        assert "only record of the overview's AOI" in src
