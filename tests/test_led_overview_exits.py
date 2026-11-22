"""Every exit of the LED 2D overview turns the LED off and releases the lock.

Until 2026-11-22:

* The exception handlers in ``_start_rotation`` (moving to a rotation) and
  ``_scan_next_tile`` (capturing a tile) returned the stage to its start but
  never turned the LED off or released the acquisition lock, so the LED stayed
  lit and the microscope controls stayed locked.
* An exception escaping ``_scan_tiles_continuous`` (a timer callback) left
  everything as it was.
* ``start()`` ignored a False from ``start_acquisition``: the optics guard was
  bypassed, and the scan's later ``stop_acquisition`` released the lock of
  whatever acquisition really held it.
* When the stage limits could not be loaded, the tile-limit guard was skipped
  with a warning and the tiles were commanded unchecked.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_led_overview_exits.py -q
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt5")


@pytest.fixture(autouse=True)
def _no_timers(monkeypatch):
    """start() schedules the first rotation on a QTimer; it must not fire in
    some later test's event loop."""
    import py2flamingo.acquisitions.led_2d_overview as mod

    monkeypatch.setattr(mod, "QTimer", SimpleNamespace(singleShot=lambda *a, **k: None))


def _workflow(start_ok=True):
    from py2flamingo.acquisitions.led_2d_overview import LED2DOverviewAcquisition

    app = MagicMock()
    app.start_acquisition.return_value = start_ok
    app.microscope_settings.get_stage_limits.return_value = {
        a: {"min": 0.0, "max": 30.0} for a in ("x", "y", "z")
    }
    config = SimpleNamespace(
        starting_r=0.0, single_rotation=True, fast_mode=False, led_name="led_green"
    )
    wf = LED2DOverviewAcquisition(app=app, config=config)
    wf._actual_fov_mm = 1.0
    wf._tile_step_mm = lambda: 1.0
    wf._tile_overlap_percent = lambda: 10.0
    wf._get_effective_bbox = lambda i: SimpleNamespace(
        tile_x_min=5.0, tile_x_max=5.0, tile_y_min=10.0, tile_y_max=10.0
    )
    wf._capture_origin_position = lambda: None
    wf.returned = []
    wf._return_to_origin = lambda: wf.returned.append(True)
    wf.errors = []
    wf.scan_error.connect(wf.errors.append)
    return wf, app


def _led(app):
    return app.sample_view.laser_led_controller


def test_refused_lock_ends_the_scan_before_anything_is_lit():
    wf, app = _workflow(start_ok=False)
    wf.start()
    assert not wf.is_running
    _led(app).enable_led_for_preview.assert_not_called()
    app.stop_acquisition.assert_not_called()  # it was never ours
    assert len(wf.errors) == 1
    from py2flamingo.services import stage_motion_gate

    assert "LED 2D overview" not in stage_motion_gate._stop_hooks


def _started():
    wf, app = _workflow()
    wf.start()
    assert wf.is_running
    app.start_acquisition.assert_called_once()
    _led(app).enable_led_for_preview.assert_called_once()
    return wf, app


def _assert_released(wf, app):
    assert not wf.is_running
    _led(app).disable_all_light_sources.assert_called()
    app.stop_acquisition.assert_called_once()
    assert wf.returned  # (the real _return_to_origin is once-only)
    from py2flamingo.services import stage_motion_gate

    assert "LED 2D overview" not in stage_motion_gate._stop_hooks


def test_tile_error_turns_led_off_and_unlocks():
    wf, app = _started()
    wf._tile_positions = [(5.0, 10.0, 15.0, 0, 0)]
    wf._current_tile_idx = 0

    def boom(*a, **k):
        raise RuntimeError("camera gone")

    wf._capture_tile = boom
    wf._scan_next_tile()
    _assert_released(wf, app)
    assert wf.errors == ["camera gone"]


def test_rotation_error_turns_led_off_and_unlocks():
    wf, app = _started()
    wf._generate_tile_positions = lambda bbox: []
    app.sample_view.movement_controller.move_absolute.side_effect = RuntimeError(
        "stage gone"
    )
    wf._start_rotation()
    _assert_released(wf, app)


def test_continuous_scan_error_turns_led_off_and_unlocks():
    wf, app = _started()

    def boom():
        raise RuntimeError("loop died")

    wf._scan_tiles_continuous = boom
    wf._scan_tiles_continuous_guarded()
    _assert_released(wf, app)


def test_cancel_and_complete_still_release_once():
    wf, app = _started()
    wf._show_results = lambda: None
    wf._finish_canceled()
    wf._finish_completed()  # a second finish must not unlock another owner
    _assert_released(wf, app)


def test_unknown_stage_limits_fail_closed():
    wf, app = _workflow()
    wf._stage_limits_cache = {}
    positions, fits = wf._fit_positions_to_limits([5.0, 6.0], "x")
    assert fits is False


def test_unknown_stage_limits_stop_the_scan_with_a_clear_message():
    wf, app = _started()
    wf._stage_limits_cache = {}
    bbox = SimpleNamespace(
        tile_x_min=5.0,
        tile_x_max=6.0,
        tile_y_min=10.0,
        tile_y_max=10.0,
        z_min=14.0,
        z_max=16.0,
    )
    assert wf._generate_tile_positions(bbox) == []
    _assert_released(wf, app)
    assert "could not be loaded" in wf.errors[-1]
