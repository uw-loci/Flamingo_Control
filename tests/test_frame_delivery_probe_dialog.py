"""The probe must survive a camera that answers nothing, and say so.

Every path here is a diagnostic, which means the failure modes are the point: a
disconnected app, a server that never fills in a header field, a light-sheet
setting that cannot be read back. None of those may look like success.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_frame_delivery_probe_dialog.py -q
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("PyQt5")

LINE_TIME_NS = 44364
ROWS = 2048
SWEEP_MS = ROWS * LINE_TIME_NS / 1e6


@pytest.fixture(scope="module")
def app():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class FakeHeader:
    def __init__(self, frame_number, timestamp_ms):
        self.frame_number = frame_number
        self.timestamp_ms = timestamp_ms


class FakeService:
    """A camera service that reports only what the real server reports."""

    def __init__(self, line_time_ns=LINE_TIME_NS, trigger_mode=2, succeed=True):
        self.line_time_ns = line_time_ns
        self.trigger_mode = trigger_mode
        self.succeed = succeed
        self.configured = None

    def configure_light_sheet(self, **kwargs):
        self.configured = kwargs
        if self.succeed:
            return {"success": True}
        return {"success": False, "failed_step": "line time", "error": "nope"}

    def read_light_sheet_state(self, rows):
        return {
            "readout_time_us": rows * self.line_time_ns / 1000.0,
            "line_time_ns": self.line_time_ns,
            "reported_fps": 11.0,
            "trigger_mode": self.trigger_mode,
            "rows": rows,
        }


class FakeController:
    def __init__(self, service=None):
        self.camera_service = service


class FakeApp:
    def __init__(self, controller=None):
        self.camera_controller = controller


@pytest.fixture
def dialog(app):
    from py2flamingo.views.dialogs.frame_delivery_probe_dialog import (
        FrameDeliveryProbeDialog,
    )

    d = FrameDeliveryProbeDialog(app=FakeApp(FakeController(FakeService())))
    yield d
    d.deleteLater()


def feed(dialog, period_ms, count=100):
    dialog._capturing = True
    for i in range(count):
        dialog._on_new_image(None, FakeHeader(i, int(round(i * period_ms))))
    dialog._stop_capture()


class TestFrameTiming:
    def test_external_clock_is_reported_with_its_consequences(self, dialog):
        dialog._line_time.setValue(LINE_TIME_NS)
        dialog._rows.setValue(ROWS)
        dialog._planes.setValue(500)
        feed(dialog, 110.0)
        text = dialog._frame_results.toPlainText()
        assert "EXTERNALLY CLOCKED" in text
        assert "87 of 500" in text
        assert "21.1% larger" in text

    def test_a_matched_camera_says_so(self, dialog):
        dialog._line_time.setValue(LINE_TIME_NS)
        dialog._rows.setValue(ROWS)
        feed(dialog, SWEEP_MS)
        assert "MATCHED" in dialog._frame_results.toPlainText()

    def test_capture_without_a_camera_does_not_pretend_to_measure(self, app):
        from py2flamingo.views.dialogs.frame_delivery_probe_dialog import (
            FrameDeliveryProbeDialog,
        )

        d = FrameDeliveryProbeDialog(app=FakeApp(None))
        d._start_capture()
        assert not d._capturing
        assert "Connect to a microscope" in d._frame_results.toPlainText()
        d.deleteLater()

    def test_frames_are_ignored_until_capture_starts(self, dialog):
        dialog._on_new_image(None, FakeHeader(1, 100))
        assert dialog._arrivals == []

    def test_export_stays_disabled_until_there_is_something_to_export(self, dialog):
        assert not dialog._export_frames.isEnabled()
        feed(dialog, 110.0, count=20)
        assert dialog._export_frames.isEnabled()

    def test_exported_csv_round_trips_the_raw_arrivals(
        self, dialog, tmp_path, monkeypatch
    ):
        import csv

        from py2flamingo.views.dialogs import frame_delivery_probe_dialog as mod

        feed(dialog, 110.0, count=20)
        target = tmp_path / "frames.csv"
        monkeypatch.setattr(
            mod.QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "")
        )
        dialog._on_export_frames()
        rows = list(csv.DictReader(target.open()))
        assert len(rows) == 20
        assert rows[0]["frame_number"] == "0"


class TestLightSheetTab:
    def test_applying_records_what_was_sent(self, dialog):
        dialog._ls_line_time.setValue(LINE_TIME_NS)
        dialog._ls_lines.setValue(128)
        dialog._on_apply_light_sheet()
        sent = dialog.app.camera_controller.camera_service.configured
        assert sent["line_time_ns"] == LINE_TIME_NS
        assert sent["exposure_lines"] == 128

    def test_a_failed_step_warns_against_acquiring(self, app):
        from py2flamingo.views.dialogs.frame_delivery_probe_dialog import (
            FrameDeliveryProbeDialog,
        )

        service = FakeService(succeed=False)
        d = FrameDeliveryProbeDialog(app=FakeApp(FakeController(service)))
        d._on_apply_light_sheet()
        text = d._ls_results.toPlainText()
        assert "FAILED" in text
        assert "do not acquire" in text
        d.deleteLater()

    def test_write_only_settings_are_named_as_unverifiable(self, dialog):
        dialog._on_read_light_sheet()
        text = dialog._ls_results.toPlainText()
        assert "NOT VERIFIABLE" in text
        assert "Exposure lines" in text
        assert "Acknowledgement is not effect" in text

    def test_a_line_time_that_did_not_take_shows_as_mismatch(self, app):
        from py2flamingo.views.dialogs.frame_delivery_probe_dialog import (
            FrameDeliveryProbeDialog,
        )

        service = FakeService(line_time_ns=12207)
        d = FrameDeliveryProbeDialog(app=FakeApp(FakeController(service)))
        d._ls_line_time.setValue(LINE_TIME_NS)
        d._on_read_light_sheet()
        assert "MISMATCH" in d._ls_results.toPlainText()
        d.deleteLater()

    def test_no_service_does_not_silently_succeed(self, app):
        from py2flamingo.views.dialogs.frame_delivery_probe_dialog import (
            FrameDeliveryProbeDialog,
        )

        d = FrameDeliveryProbeDialog(app=FakeApp(FakeController(None)))
        d._on_apply_light_sheet()
        assert "Connect to a microscope" in d._ls_results.toPlainText()
        d.deleteLater()


class TestCommandTrace:
    def test_protocol_lines_are_captured_and_the_handler_is_removed(self, dialog):
        from py2flamingo.views.dialogs.frame_delivery_probe_dialog import (
            PROTOCOL_LOGGER,
        )

        protocol = logging.getLogger(PROTOCOL_LOGGER)
        before = len(protocol.handlers)
        dialog._start_trace()
        protocol.info("[TX] LED_SET (0x4001) data0=0 data1=27 value=0.0")
        protocol.info("this is not a protocol line")
        dialog._stop_trace()
        assert len(dialog._commands) == 1
        assert "LED_SET" in dialog._commands[0][1]
        assert len(protocol.handlers) == before

    def test_repeated_commands_are_flagged_as_duplicates(self, dialog):
        from py2flamingo.views.dialogs.frame_delivery_probe_dialog import (
            PROTOCOL_LOGGER,
        )

        protocol = logging.getLogger(PROTOCOL_LOGGER)
        dialog._start_trace()
        for _ in range(2):
            protocol.info("[RX] LED_PREVIEW_ENABLE (0x4002) data0=0 data1=0 value=0.0")
        dialog._stop_trace()
        assert "DUPLICATE" in dialog._command_view.toPlainText()

    def test_a_quiet_protocol_logger_does_not_silence_the_trace(self, dialog):
        """An empty trace must not be an artefact of the logging configuration.

        A handler only sees records the logger already let through, so a
        protocol logger at WARNING would capture nothing — and an empty trace
        reads as "no commands were sent", which is the exact wrong conclusion.
        """
        from py2flamingo.views.dialogs.frame_delivery_probe_dialog import (
            PROTOCOL_LOGGER,
        )

        protocol = logging.getLogger(PROTOCOL_LOGGER)
        original = protocol.level
        try:
            protocol.setLevel(logging.WARNING)
            dialog._start_trace()
            protocol.info("[TX] LED_SET (0x4001) data0=0 data1=27 value=0.0")
            dialog._stop_trace()
            assert len(dialog._commands) == 1
            assert protocol.level == logging.WARNING  # restored
        finally:
            protocol.setLevel(original)

    def test_an_empty_trace_says_so_rather_than_looking_busy(self, dialog):
        dialog._start_trace()
        dialog._stop_trace()
        assert "No commands seen yet" in dialog._command_view.toPlainText()
