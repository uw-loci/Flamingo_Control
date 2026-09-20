"""Enabling the LED must not report success when a step failed.

The rig's server log for 2026-09-03 is the evidence. Across 1115 commands in the
whole session, `LED_PREVIEW_ENABLE` arrived **twice** and `LED_SET` arrived
**zero times**. So the lamp was switched on at whatever intensity the scope had
stored, the slider was never in the loop, and nothing anywhere said so.

`LEDEnableWorker.run` discarded the return value of every step, then set
`_active_source` and emitted `preview_enabled` regardless. The synchronous
`enable_led_for_preview` has always raised on each of those -- but the Select
checkbox uses the async path, so the one that mattered was the quiet one.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_led_enable_reports_failure.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("PyQt5")

RED, WHITE = 0, 3


class _Service:
    """Records which steps ran; any step named in `fails` returns False."""

    def __init__(self, fails=()):
        self.fails = set(fails)
        self.calls = []

    def _run(self, name):
        self.calls.append(name)
        return name not in self.fails

    def disable_all_lasers(self):
        return self._run("disable_all_lasers")

    def disable_led_preview(self):
        return self._run("disable_led_preview")

    def disable_illumination(self):
        return self._run("disable_illumination")

    def set_led_intensity(self, colour, intensity):
        self.calls.append(("set_led_intensity", colour, intensity))
        return "set_led_intensity" not in self.fails

    def enable_led_preview(self):
        return self._run("enable_led_preview")

    def enable_illumination(self):
        return self._run("enable_illumination")


class _Controller:
    def __init__(self, service):
        import logging

        self.laser_led_service = service
        self.logger = logging.getLogger("test_led")
        self._led_intensities = {RED: 40.0, WHITE: 77.0}
        self._active_source = None
        self._active_laser_index = None
        self.errors = []
        self.enabled = []

        class _Sig:
            def __init__(self, sink):
                self._sink = sink

            def emit(self, *a):
                self._sink.append(a[0] if len(a) == 1 else a)

        self.error_occurred = _Sig(self.errors)
        self.preview_enabled = _Sig(self.enabled)

    def is_led_available(self):
        return True


def _run_worker(fails=()):
    from py2flamingo.controllers.laser_led_controller import LEDEnableWorker

    service = _Service(fails=fails)
    controller = _Controller(service)
    worker = LEDEnableWorker.__new__(LEDEnableWorker)
    worker.controller = controller
    worker.led_color = WHITE
    worker.run()
    return controller, service


def _names(service):
    return [c if isinstance(c, str) else c[0] for c in service.calls]


class TestTheHappyPathStillWorks:
    def test_all_steps_run_in_order(self):
        _, service = _run_worker()
        assert _names(service) == [
            "disable_all_lasers",
            "disable_led_preview",
            "disable_illumination",
            "set_led_intensity",
            "enable_led_preview",
            "enable_illumination",
        ]

    def test_the_intensity_is_the_cached_one_for_that_colour(self):
        _, service = _run_worker()
        assert ("set_led_intensity", WHITE, 77.0) in service.calls

    def test_success_is_reported_and_the_source_recorded(self):
        controller, _ = _run_worker()
        assert controller.enabled == ["White LED"]
        assert controller._active_source == "led_W"
        assert controller.errors == []


class TestAFailedStepIsNotSuccess:
    def test_a_failed_intensity_stops_the_sequence(self):
        # The 2026-09-03 signature exactly: no LED_SET reached the scope.
        # Enabling preview anyway lights the lamp at a value nobody chose.
        _, service = _run_worker(fails={"set_led_intensity"})
        assert "enable_led_preview" not in _names(service)

    def test_a_failed_intensity_is_reported(self):
        controller, _ = _run_worker(fails={"set_led_intensity"})
        assert len(controller.errors) == 1
        assert "intensity" in controller.errors[0]

    def test_a_failed_step_does_not_claim_the_led_is_active(self):
        # `get_active_source()` is what the panel trusts to decide whether to
        # warn that no lamp is lit. Setting it on a failed sequence made that
        # check report a lamp that was never switched on.
        controller, _ = _run_worker(fails={"set_led_intensity"})
        assert controller._active_source is None
        assert controller.enabled == []

    def test_a_failed_preview_enable_is_reported(self):
        controller, service = _run_worker(fails={"enable_led_preview"})
        assert controller._active_source is None
        assert len(controller.errors) == 1
        assert "enable illumination" not in _names(service)

    def test_a_failed_illumination_is_reported(self):
        controller, _ = _run_worker(fails={"enable_illumination"})
        assert controller._active_source is None
        assert len(controller.errors) == 1

    def test_the_message_says_the_lamp_is_partly_configured(self):
        # The honest statement: earlier steps DID apply, so this is not a
        # no-op — the hardware is in a state nobody asked for.
        controller, _ = _run_worker(fails={"enable_led_preview"})
        assert "partly-configured" in controller.errors[0]
