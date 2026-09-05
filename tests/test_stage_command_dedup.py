"""One intent must not become two stage commands.

The server log of 2026-09-03 shows **56 of 551 "stage set position" commands
(10%) were identical repeats of the command immediately before them** -- same
axis, same value, all from client 24, gaps 105-372 ms. The server tore down the
in-flight motion thread and restarted it each time: 1469
`threadMotionTerminate` / `waitForMotionStopThread started` pairs for 578
commands.

The two existing guards could not catch it. `SampleView`'s no-op guard compares
against the *cached* position, which does not update during an asynchronous
move -- so it is blind exactly when a duplicate does harm. `_movement_lock` is
released when the *send* returns, not when the *motion* completes.

Run: .venv/bin/python -m pytest tests/test_stage_command_dedup.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.services import stage_command_dedup as dedup  # noqa: E402

X, Y, Z, R = 1, 2, 3, 4


@pytest.fixture(autouse=True)
def clean():
    dedup.reset()
    yield
    dedup.reset()


class TestTheDuplicateIsDropped:
    def test_the_first_command_is_sent(self):
        assert dedup.should_send(Z, 22.622)

    def test_an_identical_command_while_moving_is_dropped(self):
        dedup.should_send(Z, 22.622)
        assert not dedup.should_send(Z, 22.622)

    def test_the_rigs_actual_duplicate_pairs_are_dropped(self):
        # Verbatim from ControlSystem.txt, 2026-09-03 05:38-05:39.
        for axis, value in [
            (Z, 22.622),
            (Z, 22.822),
            (Y, 14.126),
            (Y, 15.918999999999999),
            (Y, 16.118),
            (Z, 22.579),
        ]:
            dedup.reset()
            assert dedup.should_send(axis, value)
            assert not dedup.should_send(axis, value)

    def test_it_counts_what_it_dropped(self):
        dedup.should_send(Z, 1.0)
        dedup.should_send(Z, 1.0)
        dedup.should_send(Z, 1.0)
        assert dedup.suppressed_count() == 2

    def test_a_float_that_differs_below_a_micrometre_is_the_same_command(self):
        # The stage reports 3 decimals and the workflow format writes 3, so a
        # difference finer than that is one command expressed twice.
        dedup.should_send(Z, 22.622)
        assert not dedup.should_send(Z, 22.62201)


class TestGenuineMovesAlwaysGetThrough:
    def test_a_different_value_is_never_suppressed(self):
        # A change of intent. The server is entitled to interrupt its own
        # motion for it.
        dedup.should_send(Z, 22.622)
        assert dedup.should_send(Z, 22.700)

    def test_axes_are_tracked_independently(self):
        # The rig pattern is a Z move then a Y move; Y must not be blocked by Z.
        dedup.should_send(Z, 22.622)
        assert dedup.should_send(Y, 22.622)

    def test_the_same_position_is_sendable_again_once_the_stage_stops(self):
        # The failure that would be WORSE than the bug: a stage that ignores a
        # move request is indistinguishable from a broken stage. Re-commanding
        # after drift, after a failed move, or just because the user asked
        # again must always reach the hardware.
        dedup.should_send(Z, 22.622)
        dedup.note_motion_stopped()
        assert dedup.should_send(Z, 22.622)

    def test_an_async_stop_clears_every_axis(self):
        # The server reports STAGE_MOTION_STOPPED with axis 255 for async
        # moves, meaning "whatever was moving has stopped".
        dedup.should_send(X, 1.0)
        dedup.should_send(Y, 2.0)
        dedup.should_send(Z, 3.0)
        dedup.note_motion_stopped()
        assert dedup.should_send(X, 1.0)
        assert dedup.should_send(Y, 2.0)
        assert dedup.should_send(Z, 3.0)

    def test_clearing_an_unknown_axis_clears_everything_rather_than_nothing(self):
        # Safe direction: a cleared memory can only let a command through,
        # never block one.
        dedup.should_send(Z, 3.0)
        dedup.note_motion_stopped(axis_code=255)
        assert dedup.should_send(Z, 3.0)

    def test_the_four_axis_return_home_burst_is_untouched(self):
        # workflow_queue_service sends R, X, Y, Z back-to-back on purpose.
        # Different axes, so none of them is a duplicate.
        assert dedup.should_send(R, -210.7)
        assert dedup.should_send(X, 9.313)
        assert dedup.should_send(Y, 16.820)
        assert dedup.should_send(Z, 21.414)


class TestItIsWiredIntoBothEmitters:
    """The motion gate had to cover two emitters for the same reason.

    Fixing this in the widgets instead would mean one copy per control, which is
    how the tile-step calculation ended up with five.
    """

    def _src(self, rel):
        return (Path(__file__).resolve().parents[1] / "src" / rel).read_text()

    def test_the_controller_funnel_checks_it(self):
        src = self._src("py2flamingo/controllers/position_controller.py")
        assert "stage_command_dedup" in src
        assert "should_send" in src

    def test_the_workflow_bypass_path_checks_it(self):
        # workflow_queue_service and led_2d_overview_workflow reach the stage
        # through StageService and never touch PositionController.
        src = self._src("py2flamingo/services/stage_service.py")
        assert "stage_command_dedup" in src
        assert "should_send" in src

    def test_the_motion_stopped_callback_clears_it(self):
        src = self._src("py2flamingo/controllers/motion_tracker.py")
        assert "note_motion_stopped" in src

    def test_the_emergency_stop_is_not_gated_by_it(self):
        # HALT sends directly and never passes through _move_axis. Stopping
        # motion must always be possible -- and a repeated HALT is not a bug.
        src = self._src("py2flamingo/controllers/position_controller.py")
        halt = src.split("EMERGENCY STOP ACTIVATED")[1].split("def ")[0]
        assert "should_send" not in halt
