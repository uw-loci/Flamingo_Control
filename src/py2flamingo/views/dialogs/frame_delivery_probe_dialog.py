"""Frame Delivery Probe: what the camera actually sent, and what we actually asked.

Two questions this app could not previously answer about a running microscope:

* **Is the camera delivering frames at the rate it reports?** In an external
  trigger mode it cannot know -- it reports its free-running period and has no
  way to signal that something slower is the master clock. Since
  ``z_velocity = plane_spacing * frame_rate``, a reported rate that is too high
  drives the stage too fast, and the stack comes back with a block of duplicate
  frames at the parked end position. No image is degraded, so nothing looks
  wrong; only the Z coordinates are.

* **Did the command we sent actually go out, and what came back?** A command
  whose steps all fail silently looks exactly like one that worked
  (``LEDEnableWorker``, 2026-09-03: the LED enable was sent, the intensity that
  should have preceded it never was, and nothing anywhere said so).

Both are measured passively. The frame tab listens to ``new_image``, which the
camera controller already emits, and reads ``frame_number`` and ``timestamp_ms``
out of the header the server already sends. The command tab attaches a logging
handler to the protocol logger. Neither sends anything, and in particular
neither polls the command socket -- per-plane polling is what turned a "quick"
LED overview into 2.2 hours (2026-08).

Both tabs export CSV, because the analysis is worth more off the rig than on it.
"""

from __future__ import annotations

import csv
import logging
import time
from collections import deque
from typing import List, Optional

import numpy as np
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from py2flamingo.models.aslm_timing import MEASURED_ASLM_OPERATING_POINT
from py2flamingo.models.frame_delivery import (
    CheckStatus,
    DeliveryVerdict,
    FrameArrival,
    analyze,
    check_light_sheet,
)
from py2flamingo.services.window_geometry_manager import PersistentDialog
from py2flamingo.views.colors import SUCCESS_BG, WARNING_BG

logger = logging.getLogger(__name__)

# The protocol module logs its one-line TX summary at INFO here.
PROTOCOL_LOGGER = "py2flamingo.core.tcp_protocol"

# Two identical commands closer together than this are near-certainly the same
# intent sent twice -- the two-panel LED duplicate was 66 ms apart.
DUPLICATE_WINDOW_MS = 250.0

# Bounded so an overnight capture cannot grow without limit.
MAX_FRAMES = 20000
MAX_COMMANDS = 5000


def _fmt(value, unit: str) -> str:
    """Render a read-back value, making "the camera would not say" unmissable."""
    if value is None:
        return "(no read-back)"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.3f} {unit}".strip()
    return f"{int(value)} {unit}".strip()


class _CommandLogHandler(logging.Handler):
    """Collects protocol TX/RX lines without touching the send path.

    Records arrive on the socket thread, so this only appends to a deque; the
    dialog drains it on a timer. Nothing here may touch a widget.
    """

    def __init__(self, sink: deque):
        super().__init__(level=logging.INFO)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            return
        if message.startswith("[TX]") or message.startswith("[RX]"):
            self._sink.append((time.monotonic(), message))


class FrameDeliveryProbeDialog(PersistentDialog):
    """Measure delivered frame timing and watch the command stream."""

    def __init__(self, app=None, parent=None):
        super().__init__(parent=parent, window_id="FrameDeliveryProbe")
        self.app = app
        self.setWindowTitle("Frame Delivery Probe")
        self.setMinimumSize(760, 640)

        self._arrivals: List[FrameArrival] = []
        self._capturing = False
        self._connected_signal = False

        self._command_sink: deque = deque(maxlen=MAX_COMMANDS)
        self._commands: List[tuple] = []
        self._handler: Optional[_CommandLogHandler] = None
        self._restore_level: Optional[int] = None

        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(500)
        self._drain_timer.timeout.connect(self._drain)

        self._setup_ui()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_light_sheet_tab(), "Light Sheet Control")
        tabs.addTab(self._build_frame_tab(), "Frame Timing")
        tabs.addTab(self._build_command_tab(), "Command Trace")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_light_sheet_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        intro = QLabel(
            "Apply an ASLM light-sheet configuration and check what the camera "
            "will admit to afterwards.\n\n"
            "Three of the five settings are WRITE-ONLY — the server has no GET "
            "for light-sheet mode, exposure lines or delay lines, so they can "
            "be sent but never confirmed. Only the line time (indirectly, via "
            "readout time) and the trigger mode can be verified. The report "
            "below says which is which rather than implying everything checked "
            "out."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #555;")
        layout.addWidget(intro)

        box = QGroupBox("Configuration")
        form = QFormLayout(box)

        self._ls_line_time = QSpinBox()
        self._ls_line_time.setRange(1, 10_000_000)
        self._ls_line_time.setValue(int(MEASURED_ASLM_OPERATING_POINT["line_time_ns"]))
        self._ls_line_time.setSuffix(" ns")
        self._ls_line_time.setToolTip(
            "The vendor GUI calls this 'Exposure Time (ns)'. It is the LINE "
            "TIME. The exposure is lines x line time."
        )
        form.addRow("Line time:", self._ls_line_time)

        self._ls_lines = QSpinBox()
        self._ls_lines.setRange(1, 8192)
        self._ls_lines.setValue(int(MEASURED_ASLM_OPERATING_POINT["exposure_lines"]))
        self._ls_lines.setToolTip(
            "Slit width in rows. Write-only — cannot be verified."
        )
        form.addRow("Exposure lines:", self._ls_lines)

        self._ls_delay = QSpinBox()
        self._ls_delay.setRange(0, 8192)
        self._ls_delay.setValue(0)
        self._ls_delay.setToolTip("Write-only — cannot be verified.")
        form.addRow("Delay lines:", self._ls_delay)

        self._ls_rows = QSpinBox()
        self._ls_rows.setRange(1, 8192)
        self._ls_rows.setValue(int(MEASURED_ASLM_OPERATING_POINT["rows"]))
        self._ls_rows.setToolTip(
            "AOI height in force. Needed to turn readout time back into a line "
            "time — a cropped AOI reads out proportionally faster."
        )
        form.addRow("AOI rows:", self._ls_rows)
        layout.addWidget(box)

        controls = QHBoxLayout()
        self._ls_apply = QPushButton("Apply Configuration")
        self._ls_apply.clicked.connect(self._on_apply_light_sheet)
        controls.addWidget(self._ls_apply)

        self._ls_read = QPushButton("Read Back Only")
        self._ls_read.clicked.connect(self._on_read_light_sheet)
        self._ls_read.setToolTip("Query the camera without changing anything.")
        controls.addWidget(self._ls_read)
        controls.addStretch()
        layout.addLayout(controls)

        self._ls_results = QTextEdit()
        self._ls_results.setReadOnly(True)
        layout.addWidget(self._ls_results)
        return page

    def _camera_service(self):
        controller = self._camera_controller()
        for attr in ("camera_service", "_camera_service", "service"):
            service = getattr(controller, attr, None)
            if service is not None and hasattr(service, "read_light_sheet_state"):
                return service
        return None

    def _on_apply_light_sheet(self) -> None:
        service = self._camera_service()
        if service is None:
            self._ls_results.setPlainText(
                "No camera service available. Connect to a microscope first."
            )
            return
        try:
            result = service.configure_light_sheet(
                line_time_ns=self._ls_line_time.value(),
                exposure_lines=self._ls_lines.value(),
                delay_lines=self._ls_delay.value(),
                enabled=True,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self._ls_results.setPlainText(f"Configuration raised: {exc}")
            return
        if not result.get("success"):
            self._ls_results.setPlainText(
                f"FAILED at step '{result.get('failed_step', '?')}': "
                f"{result.get('error', 'unknown error')}\n\n"
                "The camera is in a partly-applied state — do not acquire."
            )
            self._ls_results.setStyleSheet(f"background: {WARNING_BG};")
            return
        self._on_read_light_sheet(applied=True)

    def _on_read_light_sheet(self, applied: bool = False) -> None:
        service = self._camera_service()
        if service is None:
            self._ls_results.setPlainText(
                "No camera service available. Connect to a microscope first."
            )
            return
        rows = int(self._ls_rows.value())
        try:
            state = service.read_light_sheet_state(rows)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self._ls_results.setPlainText(f"Read-back raised: {exc}")
            return

        checks = check_light_sheet(
            rows=rows,
            state=state,
            requested_line_time_ns=float(self._ls_line_time.value()),
            requested_exposure_lines=self._ls_lines.value(),
            requested_delay_lines=self._ls_delay.value(),
            requested_mode_on=True,
        )
        self._render_light_sheet(checks, state, rows, applied)

    def _render_light_sheet(self, checks, state, rows: int, applied: bool) -> None:
        lines = []
        if applied:
            lines.append("Configuration sent successfully.")
            lines.append("")
        lines.append("READ-BACK")
        lines.append(f"  Readout time   {_fmt(state.get('readout_time_us'), 'us')}")
        lines.append(
            f"  Line time      {_fmt(state.get('line_time_ns'), 'ns')} "
            f"(derived from readout / {rows} rows)"
        )
        lines.append(f"  Reported FPS   {_fmt(state.get('reported_fps'), '')}")
        lines.append(f"  Trigger mode   {_fmt(state.get('trigger_mode'), '')}")
        lines.append("")
        lines.append("VERIFICATION")
        mismatched = False
        for check in checks:
            status = check.status
            if status is CheckStatus.NOT_REQUESTED:
                continue
            if status is CheckStatus.MISMATCH:
                mismatched = True
            error = (
                f"  ({check.error_percent:+.2f}%)"
                if check.error_percent is not None
                else ""
            )
            lines.append(
                f"  [{status.value.upper():>12}]  {check.setting}: "
                f"asked {_fmt(check.requested, check.unit)}, "
                f"camera says {_fmt(check.reported, check.unit)}{error}"
            )
            lines.append(f"                  {check.note}")
        lines.append("")
        unverifiable = [
            c.setting for c in checks if c.status is CheckStatus.NO_READBACK
        ]
        if unverifiable:
            lines.append(
                "NOT VERIFIABLE: " + ", ".join(unverifiable) + ". These were "
                "sent and acknowledged, but the server offers no way to confirm "
                "them. Acknowledgement is not effect."
            )
        self._ls_results.setPlainText("\n".join(lines))
        self._ls_results.setStyleSheet(
            f"background: {WARNING_BG};"
            if mismatched or unverifiable
            else f"background: {SUCCESS_BG};"
        )

    def _build_frame_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        intro = QLabel(
            "Start Live View, then capture. This only listens to frames that "
            "are already arriving — it sends nothing and changes nothing.\n"
            "The camera's reported rate is its free-running period; this "
            "measures what was actually delivered."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #555;")
        layout.addWidget(intro)

        settings = QGroupBox("Camera timing (read these off the camera, don't assume)")
        form = QFormLayout(settings)

        self._line_time = QSpinBox()
        self._line_time.setRange(1, 10_000_000)
        self._line_time.setValue(int(MEASURED_ASLM_OPERATING_POINT["line_time_ns"]))
        self._line_time.setSuffix(" ns")
        self._line_time.setToolTip(
            "Row period. In ASLM light-sheet mode this is a knob "
            "(PCO_SetCmosLineTiming), not the sensor's free-running floor."
        )
        form.addRow("Line time:", self._line_time)

        self._rows = QSpinBox()
        self._rows.setRange(1, 8192)
        self._rows.setValue(int(MEASURED_ASLM_OPERATING_POINT["rows"]))
        self._rows.setToolTip("AOI height. The sweep is one line time per row.")
        form.addRow("AOI rows:", self._rows)

        self._planes = QSpinBox()
        self._planes.setRange(1, 100000)
        self._planes.setValue(500)
        self._planes.setToolTip(
            "Planes per stack, used only to predict how many frames would land "
            "on the parked end position."
        )
        form.addRow("Planes per stack:", self._planes)
        layout.addWidget(settings)

        controls = QHBoxLayout()
        self._capture_button = QPushButton("Start Capture")
        self._capture_button.clicked.connect(self._toggle_capture)
        controls.addWidget(self._capture_button)

        self._frame_count = QLabel("0 frames")
        self._frame_count.setStyleSheet("font-weight: bold;")
        controls.addWidget(self._frame_count)
        controls.addStretch()

        self._export_frames = QPushButton("Export CSV...")
        self._export_frames.clicked.connect(self._on_export_frames)
        self._export_frames.setEnabled(False)
        controls.addWidget(self._export_frames)
        layout.addLayout(controls)

        self._frame_results = QTextEdit()
        self._frame_results.setReadOnly(True)
        layout.addWidget(self._frame_results)
        return page

    def _build_command_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        intro = QLabel(
            "Every command this app sends, in order, with the gap since the "
            "previous one. Duplicates within "
            f"{DUPLICATE_WINDOW_MS:.0f} ms are flagged — that is what two GUI "
            "panels sharing one controller looks like."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #555;")
        layout.addWidget(intro)

        controls = QHBoxLayout()
        self._trace_button = QPushButton("Start Trace")
        self._trace_button.clicked.connect(self._toggle_trace)
        controls.addWidget(self._trace_button)

        self._command_count = QLabel("0 commands")
        self._command_count.setStyleSheet("font-weight: bold;")
        controls.addWidget(self._command_count)
        controls.addStretch()

        self._export_commands = QPushButton("Export CSV...")
        self._export_commands.clicked.connect(self._on_export_commands)
        self._export_commands.setEnabled(False)
        controls.addWidget(self._export_commands)
        layout.addLayout(controls)

        self._command_view = QTextEdit()
        self._command_view.setReadOnly(True)
        self._command_view.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(self._command_view)
        return page

    # ------------------------------------------------------------------ #
    # Frame capture
    # ------------------------------------------------------------------ #

    def _camera_controller(self):
        return getattr(self.app, "camera_controller", None) if self.app else None

    def _toggle_capture(self) -> None:
        if self._capturing:
            self._stop_capture()
        else:
            self._start_capture()

    def _start_capture(self) -> None:
        controller = self._camera_controller()
        if controller is None or not hasattr(controller, "new_image"):
            self._frame_results.setPlainText(
                "No camera controller available. Connect to a microscope first."
            )
            return
        if not self._connected_signal:
            try:
                controller.new_image.connect(self._on_new_image)
                self._connected_signal = True
            except Exception:
                logger.warning("Could not connect to camera new_image signal")
                self._frame_results.setPlainText(
                    "Could not subscribe to the camera frame signal."
                )
                return

        self._arrivals = []
        self._capturing = True
        self._capture_button.setText("Stop Capture")
        self._frame_count.setText("0 frames")
        self._frame_results.setPlainText(
            "Capturing. Let it run for at least a few seconds — a hundred "
            "frames pins the period to better than a hundredth of a millisecond."
        )

    def _stop_capture(self) -> None:
        self._capturing = False
        self._capture_button.setText("Start Capture")
        self._export_frames.setEnabled(bool(self._arrivals))
        self._render_frame_results()

    def _on_new_image(self, image: np.ndarray, header=None) -> None:
        if not self._capturing or header is None:
            return
        if len(self._arrivals) >= MAX_FRAMES:
            self._stop_capture()
            return
        self._arrivals.append(
            FrameArrival(
                frame_number=int(getattr(header, "frame_number", 0)),
                server_timestamp_ms=int(getattr(header, "timestamp_ms", 0)),
                local_time_s=time.monotonic(),
            )
        )
        self._frame_count.setText(f"{len(self._arrivals)} frames")

    def _render_frame_results(self) -> None:
        report = analyze(
            self._arrivals,
            line_time_ns=float(self._line_time.value()),
            rows=int(self._rows.value()),
        )
        planes = int(self._planes.value())
        lines = []

        if report.stats is None:
            lines.append("Not enough frames to measure a period.")
        else:
            stats = report.stats
            lines.append(f"VERDICT: {report.verdict.value.upper()}")
            lines.append("")
            lines.append(f"Frames received       {stats.frames_received}")
            lines.append(f"Frame numbers spanned {stats.frame_number_span}")
            lines.append(f"Dropped               {stats.dropped}")
            lines.append(
                f"Measured over         {stats.span_ms / 1000.0:.2f} s "
                f"({stats.source.value})"
            )
            lines.append("")
            lines.append(
                f"Trigger period        {stats.trigger_period_ms:.3f} ms "
                f"= {stats.trigger_fps:.3f} fps  <- what actually happened"
            )
            lines.append(
                f"Delivery period       {stats.delivery_period_ms:.3f} ms "
                f"= {stats.delivery_fps:.3f} fps"
            )
            lines.append(f"Jitter (median dev.)  {stats.jitter_ms:.3f} ms")
            lines.append("")
            lines.append(
                f"Sweep period          {report.sweep_period_ms:.3f} ms "
                f"= {report.reported_fps:.3f} fps  <- what the camera reports"
            )
            lines.append("")

            if report.verdict is DeliveryVerdict.EXTERNALLY_CLOCKED:
                lines.append(f"Dead time per cycle   {report.dead_fraction * 100:.1f}%")
                lines.append(
                    f"Z step will be        {report.z_step_error_percent:.1f}% larger "
                    f"than requested"
                )
                lines.append(
                    f"Duplicate frames      {report.duplicate_frames(planes)} of "
                    f"{planes} planes, at the parked end position"
                )
                lines.append(
                    f"Objects measure       {report.apparent_z_scale() * 100:.1f}% of "
                    f"their true Z extent"
                )
                lines.append("")
                lines.append(
                    "Recoverable in post: drop the trailing duplicates and "
                    f"multiply the recorded Z step by "
                    f"{1.0 / report.apparent_z_scale():.4f}."
                )
            elif report.verdict is DeliveryVerdict.MATCHED:
                lines.append(
                    "Delivered rate matches the sweep. The camera is the master "
                    "clock here, so the reported rate is safe to build a "
                    "workflow from."
                )

        for warning in report.warnings:
            lines.append("")
            lines.append(f"! {warning}")

        self._frame_results.setPlainText("\n".join(lines))
        ok = report.verdict is DeliveryVerdict.MATCHED
        self._frame_results.setStyleSheet(
            f"background: {SUCCESS_BG};" if ok else f"background: {WARNING_BG};"
        )

    # ------------------------------------------------------------------ #
    # Command trace
    # ------------------------------------------------------------------ #

    def _toggle_trace(self) -> None:
        if self._handler is not None:
            self._stop_trace()
        else:
            self._start_trace()

    def _start_trace(self) -> None:
        self._commands = []
        self._command_sink.clear()
        self._handler = _CommandLogHandler(self._command_sink)
        protocol = logging.getLogger(PROTOCOL_LOGGER)
        # A handler only sees records the LOGGER already let through, so a
        # protocol logger sitting above INFO makes this tab silently capture
        # nothing -- an empty trace reading as "no commands were sent" is
        # precisely the failure this tool exists to catch. Lower it for the
        # duration and put it back afterwards.
        if protocol.level == logging.NOTSET or protocol.level > logging.INFO:
            self._restore_level = protocol.level
            protocol.setLevel(logging.INFO)
        protocol.addHandler(self._handler)
        self._drain_timer.start()
        self._trace_button.setText("Stop Trace")
        self._command_view.setPlainText("Tracing...")

    def _stop_trace(self) -> None:
        self._drain_timer.stop()
        self._drain()
        protocol = logging.getLogger(PROTOCOL_LOGGER)
        if self._handler is not None:
            protocol.removeHandler(self._handler)
            self._handler = None
        if self._restore_level is not None:
            protocol.setLevel(self._restore_level)
            self._restore_level = None
        self._trace_button.setText("Start Trace")
        self._export_commands.setEnabled(bool(self._commands))

    def _drain(self) -> None:
        while self._command_sink:
            self._commands.append(self._command_sink.popleft())
        self._command_count.setText(f"{len(self._commands)} commands")
        self._render_commands()

    def _render_commands(self) -> None:
        if not self._commands:
            self._command_view.setPlainText(
                "No commands seen yet. If this stays empty while the app is "
                "plainly talking to the microscope, the protocol logger is not "
                "emitting its TX summary."
            )
            return
        start = self._commands[0][0]
        lines = []
        previous_time = None
        previous_message = None
        for timestamp, message in self._commands[-500:]:
            gap_ms = (timestamp - previous_time) * 1000.0 if previous_time else 0.0
            flag = ""
            if (
                previous_message == message
                and previous_time is not None
                and gap_ms <= DUPLICATE_WINDOW_MS
            ):
                flag = "  <-- DUPLICATE"
            lines.append(
                f"{timestamp - start:8.3f}s  +{gap_ms:7.1f}ms  {message}{flag}"
            )
            previous_time = timestamp
            previous_message = message
        self._command_view.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #

    def _on_export_frames(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export frame timing", "frame_timing.csv", "CSV (*.csv)"
        )
        if not path:
            return
        with open(path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["frame_number", "server_timestamp_ms", "local_time_s"])
            for arrival in self._arrivals:
                writer.writerow(
                    [
                        arrival.frame_number,
                        arrival.server_timestamp_ms,
                        f"{arrival.local_time_s:.6f}",
                    ]
                )

    def _on_export_commands(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export command trace", "command_trace.csv", "CSV (*.csv)"
        )
        if not path:
            return
        start = self._commands[0][0] if self._commands else 0.0
        with open(path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["elapsed_s", "message"])
            for timestamp, message in self._commands:
                writer.writerow([f"{timestamp - start:.6f}", message])

    # ------------------------------------------------------------------ #

    def closeEvent(self, event):  # noqa: N802 - Qt signature
        if self._handler is not None:
            self._stop_trace()
        controller = self._camera_controller()
        if self._connected_signal and controller is not None:
            try:
                controller.new_image.disconnect(self._on_new_image)
            except Exception:
                pass
            self._connected_signal = False
        super().closeEvent(event)
