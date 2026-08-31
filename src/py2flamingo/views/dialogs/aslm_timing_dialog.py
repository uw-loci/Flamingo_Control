"""Acquisition Timing Explorer: how the shutter, the sheet and the sweep interact.

An offline calculator over :mod:`py2flamingo.models.aslm_timing`. No hardware, no
connection -- the point is to be able to reason about a configuration before
committing a rig session to it, and to see which knobs are actually coupled.

The three couplings it exists to make visible:

* **Exposure IS the slit width.** They are not separate settings. A rolling
  shutter offsets each row's exposure window by one line time, so the number of
  rows lit at once is exposure/line_time.
* **Below the readout time, exposure stops changing the frame rate.** The whole
  ASLM regime lives there, so the usual "shorter exposure, faster acquisition"
  intuition is simply false for it.
* **Frame rate is stage speed.** ``z_velocity = plane_spacing * frame_rate``, so
  a camera setting moves the stage.

What it does not do is claim the sheet is swept. This package has no ASLM sheet
control, so sheet sync is an input the user asserts, defaulting to UNKNOWN.
"""

from __future__ import annotations

import logging

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from py2flamingo.models.aslm_timing import (
    DEFAULT_LINE_TIME_US,
    SheetSyncStatus,
    evaluate,
)
from py2flamingo.services.window_geometry_manager import PersistentDialog
from py2flamingo.views.colors import SUCCESS_BG, WARNING_BG

logger = logging.getLogger(__name__)

SYNC_CHOICES = [
    ("Unknown - no sheet control in this app", SheetSyncStatus.UNKNOWN),
    ("Swept in sync with the shutter", SheetSyncStatus.SYNCED),
    ("Static sheet (not swept)", SheetSyncStatus.STATIC),
]


class ASLMTimingDialog(PersistentDialog):
    """Explore how camera timing, the light sheet and the Z sweep constrain each other."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Acquisition Timing Explorer")
        self.setMinimumWidth(680)
        self._building = True
        self._setup_ui()
        self._building = False
        self._recalculate()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #

    def _spin(self, lo, hi, value, decimals=0, suffix="", step=1.0):
        box = QDoubleSpinBox() if decimals else QSpinBox()
        box.setRange(lo, hi)
        if decimals:
            box.setDecimals(decimals)
            box.setSingleStep(step)
        box.setValue(value)
        if suffix:
            box.setSuffix(suffix)
        box.valueChanged.connect(self._recalculate)
        return box

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()

        intro = QLabel(
            "Exposure <b>is</b> the rolling-shutter slit width, and the frame "
            "rate <b>is</b> the stage speed. Change one number and watch what "
            "else moves."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        columns = QHBoxLayout()

        # --- camera ---
        cam_box = QGroupBox("Camera")
        cam_form = QFormLayout()
        self._rows = self._spin(8, 8192, 2048)
        self._exposure = self._spin(
            1.0, 200_000.0, 24998.0, decimals=1, suffix=" us", step=100.0
        )
        self._line_time = self._spin(
            0.1, 100.0, DEFAULT_LINE_TIME_US, decimals=3, suffix=" us", step=0.1
        )
        self._pixel = self._spin(
            0.01, 50.0, 1.0475, decimals=4, suffix=" um", step=0.01
        )
        cam_form.addRow("AOI height (rows):", self._rows)
        cam_form.addRow("Exposure:", self._exposure)
        cam_form.addRow("Line time:", self._line_time)
        cam_form.addRow("Pixel size:", self._pixel)
        line_note = QLabel(
            "Line time is <i>measured</i>, not assumed: 40 fps at 2048 rows and "
            "80 fps at 1024 rows both give 12.207 us/row."
        )
        line_note.setWordWrap(True)
        line_note.setStyleSheet("color: gray; font-size: 9pt;")
        cam_form.addRow(line_note)
        cam_box.setLayout(cam_form)
        columns.addWidget(cam_box)

        # --- acquisition ---
        acq_box = QGroupBox("Acquisition")
        acq_form = QFormLayout()
        self._spacing = self._spin(0.1, 500.0, 10.0, decimals=2, suffix=" um", step=0.5)
        self._z_range = self._spin(0.001, 50.0, 1.0, decimals=3, suffix=" mm", step=0.1)
        self._tiles = self._spin(1, 10_000, 1)
        self._angles = self._spin(1, 64, 1)
        self._channels = self._spin(1, 16, 1)
        self._overhead = self._spin(0.0, 600.0, 0.0, decimals=1, suffix=" s", step=0.5)
        self._configured_fps = self._spin(
            0.0, 2000.0, 40.0, decimals=1, suffix=" fps", step=1.0
        )
        acq_form.addRow("Plane spacing:", self._spacing)
        acq_form.addRow("Z range:", self._z_range)
        acq_form.addRow("Tiles:", self._tiles)
        acq_form.addRow("Angles:", self._angles)
        acq_form.addRow("Channels:", self._channels)
        acq_form.addRow("Overhead per stack:", self._overhead)
        acq_form.addRow("Configured rate:", self._configured_fps)

        self._sync = QComboBox()
        for label, _ in SYNC_CHOICES:
            self._sync.addItem(label)
        self._sync.currentIndexChanged.connect(self._recalculate)
        acq_form.addRow("Light sheet:", self._sync)
        acq_box.setLayout(acq_form)
        columns.addWidget(acq_box)

        layout.addLayout(columns)

        # --- results ---
        self._results = QLabel()
        self._results.setWordWrap(True)
        self._results.setTextFormat(Qt.RichText)
        self._results.setStyleSheet(
            f"background-color: {SUCCESS_BG}; padding: 10px; border-radius: 4px;"
        )
        layout.addWidget(self._results)

        self._warnings = QTextEdit()
        self._warnings.setReadOnly(True)
        self._warnings.setMaximumHeight(150)
        self._warnings.setStyleSheet(f"background-color: {WARNING_BG};")
        layout.addWidget(self._warnings)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.setLayout(layout)

    # ------------------------------------------------------------------ #
    # Calculation
    # ------------------------------------------------------------------ #

    def _current_result(self):
        return evaluate(
            rows=int(self._rows.value()),
            exposure_us=float(self._exposure.value()),
            line_time_us=float(self._line_time.value()),
            pixel_size_um=float(self._pixel.value()),
            plane_spacing_um=float(self._spacing.value()),
            z_range_mm=float(self._z_range.value()),
            tiles=int(self._tiles.value()),
            angles=int(self._angles.value()),
            channels=int(self._channels.value()),
            per_stack_overhead_s=float(self._overhead.value()),
            sync=SYNC_CHOICES[self._sync.currentIndex()][1],
            configured_frame_rate_hz=float(self._configured_fps.value()) or None,
        )

    @staticmethod
    def _duration(seconds: float) -> str:
        if seconds < 90:
            return f"{seconds:.1f} s"
        if seconds < 5400:
            return f"{seconds / 60:.1f} min"
        return f"{seconds / 3600:.2f} h"

    def _recalculate(self) -> None:
        if self._building:
            return
        try:
            r = self._current_result()
        except Exception as e:  # a calculator must never take the app down
            logger.warning(f"Timing calculation failed: {e}")
            self._results.setText(f"Could not compute: {e}")
            return

        cam = r.camera
        limiter = "exposure" if cam.exposure_us >= cam.readout_us else "readout"
        self._results.setText(
            f"<b>Slit</b> {cam.slit_rows:,.0f} of {cam.rows:,} rows "
            f"({r.sheet.slit_um:,.1f} um in sample) &nbsp;=&nbsp; "
            f"<b>{cam.sectioning_factor:.1f}x</b> sectioning, keeping "
            f"<b>{cam.duty_cycle * 100:.1f}%</b> of the light<br>"
            f"<b>Frame period</b> {cam.frame_period_us:,.0f} us "
            f"(readout {cam.readout_us:,.0f} us) &rarr; "
            f"<b>{cam.frame_rate_hz:.2f} fps</b>, limited by <b>{limiter}</b><br>"
            f"<b>Stage sweep</b> {r.z_velocity_mm_s:.4f} mm/s "
            f"({self._spacing.value():.2f} um x {cam.frame_rate_hz:.2f} fps)<br>"
            f"<b>{r.plan.planes:,}</b> planes/stack &rarr; "
            f"{self._duration(r.stack_seconds)} per stack &nbsp;|&nbsp; "
            f"<b>{r.plan.stacks:,}</b> stacks &rarr; "
            f"<b>{self._duration(r.total_seconds)}</b> total"
        )

        if r.warnings:
            self._warnings.setPlainText("\n\n".join(f"- {w}" for w in r.warnings))
            self._warnings.setVisible(True)
        else:
            self._warnings.setPlainText("")
            self._warnings.setVisible(False)
