"""Stage Repeatability Testing dialog.

Jogs the stage OUT by a set distance and back to the starting point, N times per
selected axis, capturing a live frame after each return and comparing it to the
reference frame taken at the start. Reports the residual return error (µm, via
sub-pixel cross-correlation) per axis and shows the pixel-difference image.

Reuses the XY Pixel Calibrator's proven pattern: subscribe to the camera's
``new_image`` on the UI thread, cache the latest frame, and run the blocking
move/capture loop on a background QThread that reads that cache. The stitching
math lives in ``services/stage_repeatability_service.py`` (hardware-injected and
unit-tested).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from py2flamingo.services.stage_repeatability_service import (
    RepeatabilityReport,
    run_repeatability_test,
)
from py2flamingo.services.window_geometry_manager import PersistentDialog

logger = logging.getLogger(__name__)

_AXES = [("X", "x"), ("Y", "y"), ("Z", "z")]


class _RepeatabilityWorker(QThread):
    """Runs the out-and-back repeatability loop off the UI thread."""

    progress = pyqtSignal(str, float)
    diff_ready = pyqtSignal(str, int, object)  # axis, rep, diff image (2-D)
    finished_ok = pyqtSignal(object)  # RepeatabilityReport
    failed = pyqtSignal(str)

    def __init__(self, dialog: "StageRepeatabilityDialog", params: dict):
        super().__init__()
        self._dlg = dialog
        self._params = params

    def run(self):
        try:
            report = run_repeatability_test(
                self._params["axes"],
                distance_mm=self._params["distance_mm"],
                repetitions=self._params["repetitions"],
                pixel_size_um=self._params["pixel_size_um"],
                move_relative=self._dlg._move_relative,
                grab_frame=self._grab_frame,
                settle=lambda: time.sleep(self._params["settle_s"]),
                progress=lambda m, f: self.progress.emit(m, f),
                should_cancel=lambda: self._dlg._cancel_requested,
                on_diff=lambda a, i, d: self.diff_ready.emit(a, i, d),
            )
            self.finished_ok.emit(report)
        except Exception as e:  # noqa: BLE001 - surface to UI
            logger.error("Stage repeatability test failed: %s", e)
            logger.debug("repeatability failure traceback", exc_info=True)
            self.failed.emit(str(e))

    def _grab_frame(self) -> np.ndarray:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            frame = self._dlg._latest_frame
            if frame is not None:
                return np.asarray(frame)
            time.sleep(0.05)
        raise RuntimeError("No live frame received — is Live View running?")


class StageRepeatabilityDialog(PersistentDialog):
    """Measure how precisely the stage returns to a point, per axis."""

    def __init__(self, app=None, parent=None):
        super().__init__(parent=parent, window_id="StageRepeatability")
        self.app = app
        self.setWindowTitle("Stage Repeatability Testing")
        self.setMinimumSize(760, 660)

        self._latest_frame: Optional[np.ndarray] = None
        self._worker: Optional[_RepeatabilityWorker] = None
        self._started_live_view = False
        self._cancel_requested = False

        self._setup_ui()
        self._connect_signals()

    # ------------------------------------------------------------------ UI
    def _setup_ui(self):
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Jogs the stage out and back to the same point, N times per axis, "
            "and measures how far it actually returns (via image shift). A "
            "perfect stage returns ~0 µm and a black difference image. Live View "
            "starts automatically; keep a focused, textured feature in view."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # --- Parameters ---
        params = QGroupBox("Test parameters")
        grid = QGridLayout(params)

        grid.addWidget(QLabel("Axes:"), 0, 0)
        axis_row = QHBoxLayout()
        self._axis_checks = {}
        for label, key in _AXES:
            cb = QCheckBox(label)
            cb.setChecked(key in ("x", "y"))  # X, Y on by default
            self._axis_checks[key] = cb
            axis_row.addWidget(cb)
        axis_row.addStretch()
        grid.addLayout(axis_row, 0, 1, 1, 3)

        grid.addWidget(QLabel("Jog distance (mm):"), 1, 0)
        self._distance_spin = QDoubleSpinBox()
        self._distance_spin.setRange(0.001, 10.0)
        self._distance_spin.setDecimals(3)
        self._distance_spin.setSingleStep(0.1)
        self._distance_spin.setValue(0.5)
        self._distance_spin.setToolTip(
            "How far to jog OUT before returning. Larger travel stresses the "
            "stage more; keep within the axis range."
        )
        grid.addWidget(self._distance_spin, 1, 1)

        grid.addWidget(QLabel("Repetitions:"), 1, 2)
        self._reps_spin = QSpinBox()
        self._reps_spin.setRange(1, 200)
        self._reps_spin.setValue(10)
        self._reps_spin.setToolTip("Out-and-back cycles per axis.")
        grid.addWidget(self._reps_spin, 1, 3)

        grid.addWidget(QLabel("Settle (s):"), 2, 0)
        self._settle_spin = QDoubleSpinBox()
        self._settle_spin.setRange(0.0, 10.0)
        self._settle_spin.setDecimals(2)
        self._settle_spin.setSingleStep(0.1)
        self._settle_spin.setValue(0.5)
        self._settle_spin.setToolTip(
            "Extra dwell after each move before capturing, on top of the "
            "motion-complete wait — lets the stage mechanically settle."
        )
        grid.addWidget(self._settle_spin, 2, 1)

        grid.addWidget(QLabel("Pixel size (µm):"), 2, 2)
        self._pixel_label = QLabel("—")
        grid.addWidget(self._pixel_label, 2, 3)
        layout.addWidget(params)

        # --- Results table ---
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Axis", "Reps", "Mean µm", "Max µm", "Std µm"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setMaximumHeight(140)
        layout.addWidget(self._table)

        # --- Difference image preview ---
        self._diff_label = QLabel("Difference image will appear here during the test.")
        self._diff_label.setAlignment(Qt.AlignCenter)
        self._diff_label.setMinimumHeight(300)
        self._diff_label.setStyleSheet("background:#111; border:1px solid #444;")
        layout.addWidget(self._diff_label, 1)

        # --- Progress + actions ---
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)
        self._status = QLabel("Ready.")
        layout.addWidget(self._status)

        actions = QHBoxLayout()
        self._run_btn = QPushButton("Run Test")
        self._run_btn.clicked.connect(self._on_run)
        actions.addWidget(self._run_btn)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        actions.addWidget(self._stop_btn)
        actions.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

        self._update_pixel_label()

    def _connect_signals(self):
        cc = self._camera_controller()
        if cc is not None and hasattr(cc, "new_image"):
            try:
                cc.new_image.connect(self._on_new_image)
            except Exception:  # noqa: BLE001
                logger.warning("Could not connect to camera new_image signal")

    # -------------------------------------------------- service accessors
    def _camera_controller(self):
        return getattr(self.app, "camera_controller", None) if self.app else None

    def _movement_controller(self):
        return getattr(self.app, "movement_controller", None) if self.app else None

    def _is_connected(self) -> bool:
        if self.app is None:
            return False
        try:
            cs = getattr(self.app, "connection_service", None)
            if cs and hasattr(cs, "is_connected"):
                return cs.is_connected()
            cm = getattr(self.app, "connection_model", None)
            if cm and hasattr(cm, "connected"):
                return cm.connected
        except Exception:  # noqa: BLE001
            pass
        return False

    def _move_relative(self, axis: str, delta_mm: float) -> None:
        mc = self._movement_controller()
        if mc is None:
            raise RuntimeError("Movement controller unavailable")
        mc.move_relative(axis, delta_mm, verify=True)  # verify blocks until settled

    def _pixel_size_um(self) -> float:
        try:
            from py2flamingo.configs.config_loader import get_hardware_config

            px = get_hardware_config().effective_pixel_size_um
            if px and px > 0:
                return float(px)
        except Exception:  # noqa: BLE001
            pass
        return 0.406

    def _update_pixel_label(self):
        self._pixel_label.setText(f"{self._pixel_size_um():.4f}")

    # ----------------------------------------------------------- live frame
    def _on_new_image(self, image: np.ndarray, header=None):
        self._latest_frame = image

    # --------------------------------------------------------------- run
    def _on_run(self):
        if not self._is_connected():
            QMessageBox.warning(
                self, "Not connected", "Connect to the microscope first."
            )
            return
        axes = [key for _lbl, key in _AXES if self._axis_checks[key].isChecked()]
        if not axes:
            QMessageBox.information(
                self, "No axes", "Select at least one axis to test."
            )
            return

        cc = self._camera_controller()
        if cc is not None and not cc.is_live_view_active():
            try:
                cc.start_live_view()
                self._started_live_view = True
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(
                    self,
                    "Live View",
                    f"Could not start Live View (is the live port in use?):\n{e}",
                )
                return
        if self._latest_frame is None:
            QMessageBox.information(
                self,
                "Waiting for image",
                "No live frame yet. Start Live View and focus on a textured "
                "feature, then run again.",
            )
            return

        self._update_pixel_label()
        params = {
            "axes": axes,
            "distance_mm": self._distance_spin.value(),
            "repetitions": self._reps_spin.value(),
            "settle_s": self._settle_spin.value(),
            "pixel_size_um": self._pixel_size_um(),
        }
        self._cancel_requested = False
        self._table.setRowCount(0)
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._set_inputs_enabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._worker = _RepeatabilityWorker(self, params)
        self._worker.progress.connect(self._on_progress)
        self._worker.diff_ready.connect(self._on_diff)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_stop(self):
        self._cancel_requested = True
        self._status.setText("Stopping after the current move…")
        self._stop_btn.setEnabled(False)

    def _on_progress(self, msg: str, frac: float):
        self._status.setText(msg)
        self._progress.setValue(int(frac * 100))

    def _on_diff(self, axis: str, rep: int, diff: object):
        self._show_diff(np.asarray(diff), f"{axis.upper()} return {rep + 1}")

    def _on_finished(self, report: RepeatabilityReport):
        self._progress.setVisible(False)
        self._finish_ui()
        self._populate_table(report)
        n = sum(len(a.reps) for a in report.axes)
        worst = max((a.max_error_um for a in report.axes if a.reps), default=0.0)
        self._status.setText(
            f"Done — {n} returns measured; worst return error {worst:.3f} µm."
        )

    def _on_failed(self, err: str):
        self._progress.setVisible(False)
        self._finish_ui()
        self._status.setText("Failed.")
        QMessageBox.critical(self, "Repeatability test failed", err)

    def _finish_ui(self):
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._set_inputs_enabled(True)

    def _set_inputs_enabled(self, enabled: bool):
        self._distance_spin.setEnabled(enabled)
        self._reps_spin.setEnabled(enabled)
        self._settle_spin.setEnabled(enabled)
        for cb in self._axis_checks.values():
            cb.setEnabled(enabled)

    def _populate_table(self, report: RepeatabilityReport):
        self._table.setRowCount(len(report.axes))
        for row, axis in enumerate(report.axes):
            vals = [
                axis.axis.upper(),
                str(len(axis.reps)),
                f"{axis.mean_error_um:.3f}",
                f"{axis.max_error_um:.3f}",
                f"{axis.std_error_um:.3f}",
            ]
            for col, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setTextAlignment(Qt.AlignCenter)
                self._table.setItem(row, col, item)

    def _show_diff(self, diff: np.ndarray, caption: str):
        if diff is None or diff.size == 0:
            return
        arr = diff.astype(np.float32)
        hi = float(np.percentile(arr, 99.5)) if arr.any() else 1.0
        if hi <= 0:
            hi = 1.0
        u8 = np.clip(arr / hi * 255.0, 0, 255).astype(np.uint8)
        u8 = np.ascontiguousarray(u8)
        h, w = u8.shape
        img = QImage(u8.data, w, h, w, QImage.Format_Grayscale8)
        pix = QPixmap.fromImage(img.copy()).scaled(
            self._diff_label.width(),
            self._diff_label.height(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._diff_label.setPixmap(pix)
        self._status_caption = caption

    # ------------------------------------------------------------- close
    def _restore_camera_state(self) -> None:
        if not self._started_live_view:
            return
        self._started_live_view = False
        cc = self._camera_controller()
        try:
            if cc is not None and cc.is_live_view_active():
                cc.stop_live_view()
        except Exception:  # noqa: BLE001
            logger.debug("could not restore camera state on close", exc_info=True)

    def closeEvent(self, event):
        self._cancel_requested = True
        try:
            if self._worker is not None and self._worker.isRunning():
                self._worker.wait(3000)
        except Exception:  # noqa: BLE001
            pass
        cc = self._camera_controller()
        if cc is not None and hasattr(cc, "new_image"):
            try:
                cc.new_image.disconnect(self._on_new_image)
            except Exception:  # noqa: BLE001
                pass
        self._restore_camera_state()
        super().closeEvent(event)
