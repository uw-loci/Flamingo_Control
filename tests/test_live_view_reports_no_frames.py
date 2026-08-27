"""Live view must not run silently on a stream that delivers nothing.

`start_live_view` starts the display timer and reports success. The timer then
calls `get_latest_frame`, gets None, and returns -- forever, without a word. A
stream that was never really opened is indistinguishable from one that is merely
quiet, so the app shows a frozen panel and no explanation.

That is what cost an LED overview 125 minutes and 294 tiles on 2026-08-26, and
what the 2026-08-27 session read as "the LED controls aren't working" -- the live
feed could not have changed whatever the LED did. The overview path got a
pre-flight check then; live view is the other way in, and had none.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_live_view_reports_no_frames.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("PyQt5")


class _Header:
    frame_number = 1
    timestamp_ms = 0
    image_scale_min = 0
    image_scale_max = 100
    exposure_us = 1000


class _FakeCameraService:
    """A camera stream that can be told whether to deliver anything."""

    def __init__(self, deliver=False):
        self.deliver = deliver
        self.started = False

    def start_live_view_streaming(self):
        self.started = True

    def stop_live_view_streaming(self):
        self.started = False

    def get_latest_frame(self, clear_buffer=False):
        if not self.deliver:
            return None
        return (np.zeros((4, 4), dtype=np.uint16), _Header())

    def set_image_callback(self, callback):
        self._callback = callback

    def drain_all_frames(self):
        return []

    def prepend_frames(self, frames):
        pass


@pytest.fixture(scope="module")
def app():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _controller(app, deliver=False):
    from py2flamingo.controllers.camera_controller import CameraController

    service = _FakeCameraService(deliver=deliver)
    controller = CameraController(service)
    controller._fake_service = service
    return controller


def _errors(controller):
    seen = []
    controller.error_occurred.connect(seen.append)
    return seen


class TestADeadStreamIsReported:
    def test_a_stream_with_no_frames_raises_an_error(self, app):
        controller = _controller(app, deliver=False)
        errors = _errors(controller)

        controller.start_live_view()
        controller._on_first_frame_timeout()

        assert len(errors) == 1

    def test_the_message_names_the_likeliest_cause(self, app):
        # Live-port contention with the C++ GUI is the known one, and it is not
        # something the user would guess from a frozen panel.
        controller = _controller(app, deliver=False)
        errors = _errors(controller)

        controller.start_live_view()
        controller._on_first_frame_timeout()

        assert "port" in errors[0].lower()

    def test_starting_the_stream_is_not_treated_as_proof_it_works(self, app):
        # `start_live_view_streaming` succeeded; that is a request, not a frame.
        controller = _controller(app, deliver=False)
        controller.start_live_view()
        assert controller._fake_service.started
        assert controller._frames_displayed == 0

    def test_the_watchdog_is_armed_by_starting_live_view(self, app):
        controller = _controller(app, deliver=False)
        assert not controller._live_view_watchdog.isActive()
        controller.start_live_view()
        assert controller._live_view_watchdog.isActive()


class TestAWorkingStreamIsLeftAlone:
    def test_a_delivered_frame_re_arms_rather_than_disarms(self, app):
        # This test used to assert the watchdog was stopped for good on the
        # first frame. That is precisely what let the 2026-08-27 failure
        # through: the receiver thread died immediately *after* delivering
        # exactly one frame, so the frame that killed the stream also disarmed
        # the only check that would have noticed.
        controller = _controller(app, deliver=True)
        controller.start_live_view()
        controller._pull_and_display_frame()

        assert controller._frames_displayed == 1
        assert controller._live_view_watchdog.isActive()

    def test_the_re_armed_gap_is_longer_than_the_first_frame_budget(self, app):
        # A running acquisition can put real pauses between frames. A false
        # alarm here would train people to ignore the one message that matters.
        from py2flamingo.controllers.camera_controller import CameraController

        assert (
            CameraController.STALL_TIMEOUT_MS > CameraController.FIRST_FRAME_TIMEOUT_MS
        )

    def test_no_error_while_frames_keep_arriving(self, app):
        controller = _controller(app, deliver=True)
        errors = _errors(controller)

        controller.start_live_view()
        for _ in range(5):
            controller._pull_and_display_frame()

        assert errors == []


class TestAStreamThatDiesAfterOneFrameIsReported:
    """The 2026-08-27 failure exactly: one good frame, then the receiver died."""

    def test_a_stall_after_one_frame_is_an_error(self, app):
        controller = _controller(app, deliver=True)
        errors = _errors(controller)

        controller.start_live_view()
        controller._pull_and_display_frame()
        controller._fake_service.deliver = False  # receiver thread dies
        controller._on_first_frame_timeout()

        assert len(errors) == 1

    def test_the_message_says_the_image_is_frozen_not_live(self, app):
        # The user-visible symptom is a picture that looks fine and is stale.
        controller = _controller(app, deliver=True)
        errors = _errors(controller)

        controller.start_live_view()
        controller._pull_and_display_frame()
        controller._on_first_frame_timeout()

        assert "frozen" in errors[0]

    def test_the_message_counts_the_frames_that_did_arrive(self, app):
        controller = _controller(app, deliver=True)
        errors = _errors(controller)

        controller.start_live_view()
        controller._pull_and_display_frame()
        controller._on_first_frame_timeout()

        assert "1 frame" in errors[0]

    def test_it_reports_once_not_on_every_timeout(self, app):
        controller = _controller(app, deliver=False)
        errors = _errors(controller)

        controller.start_live_view()
        for _ in range(4):
            controller._on_first_frame_timeout()

        assert len(errors) == 1

    def test_a_restart_allows_a_fresh_report(self, app):
        controller = _controller(app, deliver=False)
        errors = _errors(controller)

        controller.start_live_view()
        controller._on_first_frame_timeout()
        controller.stop_live_view()

        controller.start_live_view()
        controller._on_first_frame_timeout()

        assert len(errors) == 2

    def test_stopping_live_view_disarms_the_watchdog(self, app):
        # Otherwise a short deliberate live view that is stopped before the
        # timeout reports a failure that did not happen.
        controller = _controller(app, deliver=False)
        controller.start_live_view()
        controller.stop_live_view()

        assert not controller._live_view_watchdog.isActive()

    def test_a_restart_re_arms_it(self, app):
        controller = _controller(app, deliver=True)
        controller.start_live_view()
        controller._pull_and_display_frame()
        controller.stop_live_view()

        controller.start_live_view()
        assert controller._frames_displayed == 0
        assert controller._live_view_watchdog.isActive()


class TestTheTimeoutIsSane:
    def test_it_is_seconds_not_minutes(self, app):
        from py2flamingo.controllers.camera_controller import CameraController

        # The whole point is failing fast. Anything approaching a minute puts
        # this back in the territory the overview bug lived in.
        assert 1000 <= CameraController.FIRST_FRAME_TIMEOUT_MS <= 15000
