"""Turning the light off must say when it did not happen, and reach every line.

Until 2026-11-22:

* ``LaserLEDController.disable_all_light_sources`` ignored the result of each
  step and returned True ("All light sources disabled") whatever happened.
* ``LaserLEDService.disable_all_lasers`` named lines 1-4 only. Servers before
  the 2026-11-20 build run LASER_DISABLE_ALL only in the laser engine that owns
  the line int32Data0 names (LaserControl::exeCommand), so a fifth line in a
  second engine was never turned off.
* The laser-enable worker discarded every step's result, and enabled a laser
  even when the "everything off" before it had failed.
* The enable/disable workers ran on the shared global thread pool with two
  threads, so two quick clicks could interleave their steps.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_light_off_is_honest.py -q
"""

from __future__ import annotations

import logging
import os
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt5")

from py2flamingo.core.tcp_connection import TCPConnection  # noqa: E402
from py2flamingo.core.tcp_protocol import ProtocolEncoder  # noqa: E402
from py2flamingo.models.connection import ConnectionConfig  # noqa: E402
from py2flamingo.services.connection_service import (  # noqa: E402
    MVCConnectionService,
)
from py2flamingo.services.laser_led_service import LaserLEDService  # noqa: E402
from py2flamingo.testing import synthetic_microscope as synth  # noqa: E402


def _adjacent_ports():
    for _ in range(50):
        with socket.socket() as a:
            a.bind(("127.0.0.1", 0))
            p = a.getsockname()[1]
        try:
            with socket.socket() as b, socket.socket() as c:
                b.bind(("127.0.0.1", p))
                c.bind(("127.0.0.1", p + 1))
            return p
        except OSError:
            continue
    raise RuntimeError("no adjacent free ports")


def _wait_until(predicate, timeout=3.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


SIX_LASERS = {1: 405, 2: 488, 3: 561, 4: 640, 5: 730, 6: 785}


class SyntheticScopeCase(unittest.TestCase):
    lasers = SIX_LASERS
    control_settings_lasers = SIX_LASERS

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        (base / "microscope_settings").mkdir()
        if self.control_settings_lasers:
            lines = "".join(
                f"  Laser {i} wave length (nm) = {w}\n"
                f"  Laser {i} max power (mw) = 20\n"
                for i, w in self.control_settings_lasers.items()
            )
            attached = "".join(
                f"  Attached laser line {i} = 1\n" for i in self.control_settings_lasers
            )
            (base / "microscope_settings" / "ControlSettings.txt").write_text(
                "<Laser parameters>\n"
                + lines
                + f"  Laser lines = {len(self.control_settings_lasers)}\n"
                "</Laser parameters>\n<Attached devices>\n"
                + attached
                + "  Attached LED = 1\n</Attached devices>\n"
            )
        port = _adjacent_ports()
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, cwd)
        self.scope = synth.SyntheticMicroscope(command_port=port, live_port=port + 1)
        self.scope.lasers = dict(self.lasers)
        # Two engines, as on a scope with a second laser box.
        self.scope.laser_engines = [{1, 2, 3, 4}, {5, 6}]
        self.scope.start()
        self.addCleanup(self.scope.stop)
        self.connection = MVCConnectionService(TCPConnection(), ProtocolEncoder())
        self.connection.connect(ConnectionConfig("127.0.0.1", port, port + 1))
        self.addCleanup(
            lambda: self.connection.is_connected() and self.connection.disconnect()
        )
        self.service = LaserLEDService(self.connection, SimpleNamespace(base_path=base))


class TestEveryLineIsTurnedOff(SyntheticScopeCase):
    def test_a_line_in_a_second_engine_goes_off(self):
        self.scope.lasers_on = {5}
        self.assertTrue(self.service.disable_all_lasers())
        self.assertTrue(_wait_until(lambda: len(self.scope.received(0x2007)) >= 6))
        self.assertFalse(self.scope.lasers_on)
        named = [c.params[3] for c in self.scope.received(0x2007)]
        self.assertEqual(sorted(named), [1, 2, 3, 4, 5, 6])


class TestUnknownLinesFallBackToSeven(SyntheticScopeCase):
    control_settings_lasers = {}

    def test_without_control_settings_lines_1_to_7_are_named(self):
        self.scope.lasers_on = {6}
        self.assertTrue(self.service.disable_all_lasers())
        self.assertTrue(_wait_until(lambda: len(self.scope.received(0x2007)) >= 7))
        self.assertFalse(self.scope.lasers_on)
        named = [c.params[3] for c in self.scope.received(0x2007)]
        self.assertEqual(sorted(named), [1, 2, 3, 4, 5, 6, 7])


# --------------------------------------------------------------- controller


class _Service:
    """Records which steps ran; any step named in ``fails`` returns False."""

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

    def set_laser_power(self, index, power):
        self.calls.append("set_laser_power")
        return ("set_laser_power" not in self.fails), power

    def enable_laser_preview(self, index):
        return self._run("enable_laser_preview")

    def enable_illumination(self, left=True, right=False):
        return self._run("enable_illumination")

    def is_led_available(self):
        return True

    def get_available_lasers(self):
        return []


def _controller(fails=()):
    from py2flamingo.controllers.laser_led_controller import LaserLEDController

    service = _Service(fails)
    controller = LaserLEDController(service)
    errors, enabled, disabled = [], [], []
    controller.error_occurred.connect(errors.append)
    controller.preview_enabled.connect(enabled.append)
    controller.preview_disabled.connect(lambda: disabled.append(True))
    return controller, service, errors, enabled, disabled


class TestDisableAllLightSources:
    def test_success_when_every_step_is_sent(self):
        controller, service, errors, _, disabled = _controller()
        assert controller.disable_all_light_sources() is True
        assert errors == [] and disabled == [True]

    @pytest.mark.parametrize(
        "step", ["disable_all_lasers", "disable_led_preview", "disable_illumination"]
    )
    def test_a_failed_step_is_reported(self, step):
        controller, service, errors, _, disabled = _controller(fails={step})
        controller._active_source = "laser_2"
        assert controller.disable_all_light_sources() is False
        assert len(errors) == 1 and "may still be ON" in errors[0]
        assert disabled == []
        # The source may still be lit, so the record of it stays.
        assert controller.get_active_source() == "laser_2"

    def test_every_step_runs_even_after_a_failure(self):
        controller, service, *_ = _controller(fails={"disable_all_lasers"})
        controller.disable_all_light_sources()
        assert service.calls == [
            "disable_all_lasers",
            "disable_led_preview",
            "disable_illumination",
        ]


def _run_laser_worker(fails=()):
    from py2flamingo.controllers.laser_led_controller import LaserEnableWorker

    controller, service, errors, enabled, _ = _controller(fails)
    controller.logger = logging.getLogger("test_light_off")
    LaserEnableWorker(controller, 2, "left").run()
    return controller, service, errors, enabled


class TestLaserEnableWorker:
    def test_happy_path(self):
        controller, service, errors, enabled = _run_laser_worker()
        assert errors == [] and len(enabled) == 1
        assert controller.get_active_source() == "laser_2"

    def test_no_enable_when_lights_off_failed(self):
        controller, service, errors, enabled = _run_laser_worker(
            fails={"disable_all_lasers"}
        )
        assert "enable_laser_preview" not in service.calls
        assert "set_laser_power" not in service.calls
        assert len(errors) == 1 and enabled == []

    @pytest.mark.parametrize(
        "step", ["set_laser_power", "enable_laser_preview", "enable_illumination"]
    )
    def test_a_failed_step_is_reported_and_lasers_go_off(self, step):
        controller, service, errors, enabled = _run_laser_worker(fails={step})
        assert len(errors) == 1 and enabled == []
        assert controller.get_active_source() is None
        # Lasers sent off again after the failure.
        assert service.calls[-1] == "disable_all_lasers"


def test_workers_run_one_at_a_time_on_their_own_pool():
    from PyQt5.QtCore import QThreadPool

    controller, *_ = _controller()
    assert controller._thread_pool is not QThreadPool.globalInstance()
    assert controller._thread_pool.maxThreadCount() == 1
