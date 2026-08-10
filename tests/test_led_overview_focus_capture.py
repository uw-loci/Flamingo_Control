"""A Z-plane's frame must belong to that Z, and must not be a repeat.

"Best Focus" was picking a plane that is visibly not the sharpest. The metric
(variance of Laplacian) is fine; the frames fed to it were not. The sweep did::

    stage_service.move_to_position(AxisCode.Z_AXIS, z_pos)
    time.sleep(0.015)
    frame_data = camera_controller.get_latest_frame()

* ``move_to_position`` is asynchronous (its own docstring says to wait for
  motion monitoring), so 15 ms captures the stage mid-travel: the frame is
  motion-blurred and belongs to no particular Z.
* ``get_latest_frame`` returns ``_frame_buffer[-1]`` with no freshness check.
  At 40 fps a frame arrives every 25 ms, so a 15 ms sleep returns the SAME
  frame as the previous plane more often than not — the stack carries
  duplicates and may never sample the true best plane.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \\
        tests/test_led_overview_focus_capture.py -q
"""

import numpy as np
import pytest

from py2flamingo.workflows.led_2d_overview_workflow import LED2DOverviewWorkflow

Z_AXIS = 3


class FakeStage:
    """Arrives at a commanded position only after `moves_to_arrive` polls."""

    def __init__(self, moves_to_arrive=2):
        self._pos = {Z_AXIS: 0.0}
        self._target = {Z_AXIS: 0.0}
        self._polls = 0
        self._moves_to_arrive = moves_to_arrive
        self.commanded = []

    def move_to_position(self, axis, position_mm):
        self.commanded.append((axis, position_mm))
        self._target[axis] = position_mm
        self._polls = 0  # in transit

    def get_axis_position(self, axis):
        self._polls += 1
        if self._polls >= self._moves_to_arrive:
            self._pos[axis] = self._target[axis]
        return self._pos[axis]


class FakeCamera:
    """Delivers a new frame only every `calls_per_frame` reads.

    Models the real timing problem: the sweep polls faster than the camera
    produces frames.
    """

    def __init__(self, calls_per_frame=3):
        self._calls = 0
        self._number = 0
        self._calls_per_frame = calls_per_frame

    def get_latest_frame(self):
        self._calls += 1
        if self._calls % self._calls_per_frame == 0:
            self._number += 1
        # Frame content is keyed to its number so identity is checkable.
        image = np.full((4, 4), self._number, dtype=np.uint16)
        return (image, {}, self._number)


def _workflow(cancelled=False):
    wf = LED2DOverviewWorkflow.__new__(LED2DOverviewWorkflow)
    wf._cancelled = cancelled
    wf._last_xyz = [0.0, 0.0, 0.0]
    wf._broadcast_stage_position = lambda *a, **k: None
    # None = "looked up, no tracker available", which sends _wait_for_z_arrival
    # down its polling fallback. That is the path FakeStage models, so these
    # tests keep exercising arrival-before-frame without needing a live
    # STAGE_MOTION_STOPPED stream. The callback path is covered in
    # tests/test_motion_callback_queue.py.
    wf._motion_tracker_cache = None
    return wf


class TestThePlaneWaitsForTheStage:
    def test_the_move_is_commanded(self):
        wf, stage, cam = _workflow(), FakeStage(), FakeCamera()
        wf._capture_plane(stage, cam, 1.234)
        assert stage.commanded == [(Z_AXIS, 1.234)]

    def test_it_does_not_return_until_the_stage_has_arrived(self):
        """The old code grabbed a frame 15 ms after an async move command."""
        wf, cam = _workflow(), FakeCamera()
        stage = FakeStage(moves_to_arrive=5)
        wf._capture_plane(stage, cam, 2.0)
        assert stage.get_axis_position(Z_AXIS) == pytest.approx(2.0)


class TestTheFrameIsFresh:
    def test_consecutive_planes_get_different_frames(self):
        """The core regression: the same buffered frame served many planes."""
        wf, stage, cam = _workflow(), FakeStage(), FakeCamera(calls_per_frame=3)

        numbers = [wf._capture_plane(stage, cam, z)[1] for z in (1.0, 1.1, 1.2, 1.3)]

        assert len(set(numbers)) == len(numbers), f"duplicate frames: {numbers}"

    def test_the_image_matches_its_frame_number(self):
        wf, stage, cam = _workflow(), FakeStage(), FakeCamera()
        image, number = wf._capture_plane(stage, cam, 1.0)
        assert int(image[0, 0]) == number

    def test_a_camera_that_never_delivers_does_not_hang(self):
        class Dead:
            def get_latest_frame(self):
                return None

        wf, stage = _workflow(), FakeStage()
        assert wf._capture_plane(stage, Dead(), 1.0, frame_timeout_s=0.05) is None

    def test_a_stalled_camera_times_out_and_reports_the_reused_number(self):
        """A stuck frame is still returned — but with its number, so the caller
        can drop it instead of scoring a stale image."""

        class Stuck:
            def get_latest_frame(self):
                return (np.zeros((4, 4), dtype=np.uint16), {}, 7)

        wf, stage = _workflow(), FakeStage()
        first = wf._capture_plane(stage, Stuck(), 1.0, frame_timeout_s=0.05)
        second = wf._capture_plane(stage, Stuck(), 1.1, frame_timeout_s=0.05)
        assert first[1] == second[1] == 7  # caller sees the repeat


class TestBestFocusPicksTheSharpestPlane:
    """End-to-end on the selection itself, with frames of known sharpness."""

    def _stack(self):
        rng = np.random.default_rng(0)
        # Plane 2 is the sharp one: high-frequency noise. The others are smooth.
        smooth = np.zeros((64, 64), dtype=np.uint16)
        sharp = (rng.integers(0, 4096, (64, 64))).astype(np.uint16)
        return [smooth, smooth.copy(), sharp, smooth.copy()]

    def test_the_metric_ranks_the_sharp_plane_highest(self):
        from py2flamingo.utils.focus_detection import variance_of_laplacian

        scores = [variance_of_laplacian(f) for f in self._stack()]
        assert scores.index(max(scores)) == 2

    def test_a_duplicated_blurry_frame_cannot_win(self):
        """Why dropping repeats matters: duplicates skew nothing, but a stale
        frame standing in for the sharp plane loses it entirely."""
        from py2flamingo.utils.focus_detection import variance_of_laplacian

        stack = self._stack()
        # Simulate the old behaviour: the sharp plane's slot got a stale copy.
        stale = [stack[0], stack[1], stack[1], stack[3]]
        scores = [variance_of_laplacian(f) for f in stale]
        assert max(scores) < variance_of_laplacian(stack[2])


class TestTheTileMoveWaitsOnTheEventNotThePoll:
    """The per-tile settle was the other half of the ~25 s/tile overview.

    ``_wait_for_axes_settled`` opens with a position query and repeats every
    100 ms until the tolerance is met. On a command socket the LED preview has
    saturated those queries time out, so a tile move could run its full 10 s
    budget without ever confirming. ``_move_and_settle`` waits for the stage's
    own motion-stopped callback first and uses polling only to confirm.
    """

    class _Tracker:
        def __init__(self):
            self.events = []

        def arm(self):
            self.events.append("arm")

        def disarm(self):
            self.events.append("disarm")

        def wait_for_motion_complete(self, timeout=0.0, allow_cancel=True):
            self.events.append("wait")
            return True

    def _wf(self, tracker):
        wf = LED2DOverviewWorkflow.__new__(LED2DOverviewWorkflow)
        wf._cancelled = False
        wf._last_xyz = [0.0, 0.0, 0.0]
        wf._broadcast_stage_position = lambda *a, **k: None
        wf._motion_tracker_cache = tracker
        return wf

    def test_it_arms_before_commanding_the_move(self):
        tracker = self._Tracker()
        wf, stage = self._wf(tracker), FakeStage(moves_to_arrive=1)

        commanded_at = []
        real_move = stage.move_to_position

        def spy(axis, mm):
            commanded_at.append(len(tracker.events))
            real_move(axis, mm)

        stage.move_to_position = spy
        wf._move_and_settle(stage, {Z_AXIS: 1.0})

        assert tracker.events[0] == "arm"
        assert commanded_at and commanded_at[0] >= 1, (
            "the move went out before arming — a short move can complete inside "
            "the ack window and its callback would be dropped"
        )
        assert tracker.events.index("wait") > 0
        assert "disarm" in tracker.events

    def test_an_already_arrived_stage_costs_one_query_per_axis(self):
        """The whole point: confirm, do not interrogate."""
        tracker = self._Tracker()
        wf, stage = self._wf(tracker), FakeStage(moves_to_arrive=1)

        queries = []
        real_get = stage.get_axis_position
        stage.get_axis_position = lambda a: (queries.append(a), real_get(a))[1]

        assert wf._move_and_settle(stage, {Z_AXIS: 2.0}) is True
        assert len(queries) == 1, f"expected one confirming query, got {queries}"

    def test_it_still_settles_when_no_tracker_is_available(self):
        """No async reader: degrade to polling rather than skip the wait."""
        wf, stage = self._wf(None), FakeStage(moves_to_arrive=3)
        assert wf._move_and_settle(stage, {Z_AXIS: 5.0}, timeout_s=2.0) is True
        assert stage.get_axis_position(Z_AXIS) == pytest.approx(5.0)

    def test_a_confirming_pass_happens_even_with_no_time_left(self):
        """A zero budget must check, not report a phantom timeout."""
        wf, stage = self._wf(None), FakeStage(moves_to_arrive=1)
        stage.move_to_position(Z_AXIS, 3.0)
        assert wf._wait_for_axes_settled(stage, {Z_AXIS: 3.0}, timeout_s=0.0) is True
