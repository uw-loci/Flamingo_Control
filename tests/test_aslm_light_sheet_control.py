"""ASLM light-sheet control, checked against the ScopeControl 3.0.0 source.

Until 2026-09-19 this client had no way to drive the swept-waist light sheet,
and the source that would show how was missing from the version drops. It
arrived with `Source F2V2`, and `Linux/ControlSystem/Subsystems/Camera/
PCOBase.cpp` settles every open question:

* All four light-sheet commands read **int32Data0** and nothing else.
* Each maps onto one PCO SDK call, so the semantics are the SDK's.
* The field the vendor GUI labels "Exposure Time (ns)" is the **line time**.
  The exposure is ``lines x line_time``, shown separately.
* `ShutterModeSet` and `ReadoutFormatSet` both just call their Get counterpart.
  They are **read-only** -- "Rolling Shutter" and "Single Top Down" cannot be
  set from here.

The operating point is a real one, reported from the instrument: light sheet on,
trigger Ext-Exp. Start, 44364 ns line time, 128 exposure lines, 11.00 fps.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_aslm_light_sheet_control.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import py2flamingo.models.aslm_timing as aslm  # noqa: E402
from py2flamingo.services.camera_service import CameraCommandCode  # noqa: E402

RIG = aslm.MEASURED_ASLM_OPERATING_POINT
SheetSyncStatus = aslm.SheetSyncStatus
from_light_sheet_settings = aslm.from_light_sheet_settings


class _FakeCameraService:
    """Captures what would go on the wire."""

    def __init__(self, fail_on=None):
        self.sent = []
        self.fail_on = fail_on
        import logging

        self.logger = logging.getLogger("test_camera_service")

    def _send_command(self, command_code, command_name, params=None, value=0.0):
        self.sent.append((int(command_code), command_name, list(params or []), value))
        if self.fail_on is not None and int(command_code) == self.fail_on:
            return {"success": False, "error": "refused"}
        return {"success": True}


@pytest.fixture
def svc():
    from py2flamingo.services.camera_service import CameraService

    s = _FakeCameraService()
    for name in (
        "set_light_sheet_mode",
        "set_light_sheet_line_time",
        "set_light_sheet_exposure_lines",
        "set_light_sheet_delay_lines",
        "set_trigger_mode",
        "configure_light_sheet",
    ):
        setattr(_FakeCameraService, name, getattr(CameraService, name))
    return s


def _payload(svc, code):
    """int32Data0 of the command sent for `code` — params[3] on this wire."""
    for sent_code, _, params, _ in svc.sent:
        if sent_code == int(code):
            return params[3]
    raise AssertionError(f"0x{int(code):04X} was never sent")


class TestTheCodesMatchTheServerSource:
    @pytest.mark.parametrize(
        "attr,value",
        [
            ("LIGHT_SHEET_MODE_ON_OFF", 0x3030),
            ("LIGHT_SHEET_MODE_LINES", 0x3031),
            ("LIGHT_SHEET_MODE_DELAY_LINES", 0x3032),
            ("LIGHT_SHEET_MODE_EXPOSURE_TIME", 0x3033),
            ("TRIGGER_MODE_SET", 0x300D),
            ("SHUTTER_MODE_GET", 0x302E),
            ("READOUT_FORMAT_GET", 0x302F),
        ],
    )
    def test_code_value(self, attr, value):
        assert getattr(CameraCommandCode, attr) == value

    def test_the_read_only_ones_are_named_as_gets(self):
        # PCOBase::ShutterModeSet and ::ReadoutFormatSet both just call Get.
        # Naming them *_SET here would invite someone to try to set them and
        # believe the status=1 that comes back.
        assert not hasattr(CameraCommandCode, "SHUTTER_MODE_SET")
        assert not hasattr(CameraCommandCode, "READOUT_FORMAT_SET")


class TestEveryValueGoesInInt32Data0:
    def test_mode_on(self, svc):
        svc.set_light_sheet_mode(True)
        assert _payload(svc, CameraCommandCode.LIGHT_SHEET_MODE_ON_OFF) == 1

    def test_mode_off_is_anything_but_one(self, svc):
        svc.set_light_sheet_mode(False)
        assert _payload(svc, CameraCommandCode.LIGHT_SHEET_MODE_ON_OFF) == 0

    def test_line_time(self, svc):
        svc.set_light_sheet_line_time(RIG["line_time_ns"])
        assert _payload(svc, CameraCommandCode.LIGHT_SHEET_MODE_EXPOSURE_TIME) == 44364

    def test_exposure_lines(self, svc):
        svc.set_light_sheet_exposure_lines(RIG["exposure_lines"])
        assert _payload(svc, CameraCommandCode.LIGHT_SHEET_MODE_LINES) == 128

    def test_delay_lines_default_to_zero(self, svc):
        svc.set_light_sheet_delay_lines()
        assert _payload(svc, CameraCommandCode.LIGHT_SHEET_MODE_DELAY_LINES) == 0

    def test_trigger_mode_two_is_ext_exp_start(self, svc):
        svc.set_trigger_mode(2)
        assert _payload(svc, CameraCommandCode.TRIGGER_MODE_SET) == 2

    def test_nothing_is_smuggled_into_the_double(self, svc):
        # The server reads int32Data0 only; a value in `doubleData` would be
        # silently ignored, which is the kind of thing that costs a session.
        svc.configure_light_sheet(line_time_ns=44364, exposure_lines=128)
        assert all(value == 0.0 for _, _, _, value in svc.sent)


class TestTheConfigurationOrderIsDeliberate:
    def test_geometry_is_set_before_the_mode_is_switched_on(self, svc):
        svc.configure_light_sheet(line_time_ns=44364, exposure_lines=128)
        codes = [c for c, _, _, _ in svc.sent]
        assert codes[-1] == CameraCommandCode.LIGHT_SHEET_MODE_ON_OFF

    def test_turning_it_off_switches_the_mode_first(self, svc):
        # So the slit stops governing before the numbers move under it.
        svc.configure_light_sheet(line_time_ns=44364, exposure_lines=128, enabled=False)
        codes = [c for c, _, _, _ in svc.sent]
        assert codes[0] == CameraCommandCode.LIGHT_SHEET_MODE_ON_OFF

    def test_it_stops_at_the_first_failure(self, svc):
        # A half-applied light-sheet configuration still acquires -- it just
        # acquires the wrong thing.
        svc.fail_on = int(CameraCommandCode.LIGHT_SHEET_MODE_LINES)
        result = svc.configure_light_sheet(line_time_ns=44364, exposure_lines=128)
        assert not result["success"]
        assert result["failed_step"] == "exposure lines"
        assert int(CameraCommandCode.LIGHT_SHEET_MODE_ON_OFF) not in [
            c for c, _, _, _ in svc.sent
        ]


class TestTheTimingModelReproducesTheRig:
    def _run(self, **kw):
        base = dict(
            line_time_ns=RIG["line_time_ns"],
            exposure_lines=RIG["exposure_lines"],
            rows=RIG["rows"],
            plane_spacing_um=10.0,
            z_range_mm=1.0,
            pixel_size_um=1.0475,
            sync=SheetSyncStatus.SYNCED,
        )
        base.update(kw)
        return from_light_sheet_settings(**base)

    def test_the_line_time_alone_gives_the_reported_frame_rate(self):
        # 2048 rows x 44.364 us = 90.86 ms. Nothing else is needed.
        assert self._run().camera.frame_rate_hz == pytest.approx(
            RIG["reported_fps"], abs=0.01
        )

    def test_the_slit_is_the_exposure_lines(self):
        assert self._run().camera.slit_rows == pytest.approx(128, abs=0.5)

    def test_sectioning_is_rows_over_lines(self):
        assert self._run().camera.sectioning_factor == pytest.approx(16.0, rel=1e-3)

    def test_the_light_cost_is_exactly_the_inverse(self):
        assert self._run().camera.duty_cycle == pytest.approx(1 / 16, rel=1e-3)

    def test_this_configuration_raises_no_warnings(self):
        # A real, working experiment must come through clean, or the warnings
        # are worthless.
        assert self._run().warnings == []

    def test_exposure_lines_do_not_change_the_frame_rate(self):
        # The counter-intuitive one the vendor GUI's labelling hides: the line
        # time sets the rate, the lines set the slit.
        rates = {
            self._run(exposure_lines=n).camera.frame_rate_hz for n in (32, 64, 128, 256)
        }
        assert len(rates) == 1

    def test_the_line_time_is_what_changes_the_frame_rate(self):
        slow = self._run().camera.frame_rate_hz
        fast = self._run(line_time_ns=RIG["line_time_ns"] // 2).camera.frame_rate_hz
        assert fast == pytest.approx(2 * slow, rel=1e-6)

    def test_the_free_running_floor_is_faster_than_the_rig_uses(self):
        # Light-sheet mode slows the line time to let the waist keep up; the
        # sensor's own floor is 12.207 us.
        from py2flamingo.models.aslm_timing import DEFAULT_LINE_TIME_US

        assert RIG["line_time_ns"] / 1000.0 > DEFAULT_LINE_TIME_US
