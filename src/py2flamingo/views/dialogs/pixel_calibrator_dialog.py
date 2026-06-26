"""XY Pixel Calibrator dialog.

Measures the true sample-plane pixel size by moving the stage by known deltas
and cross-correlating the resulting microscope live frames (MicroManager-style).
Reports the X/Y pixel size and the camera-vs-stage rotation, saves the result,
and optionally patches the stale config values.

The sweep runs in a worker thread so the UI stays responsive; the live frame is
cached from ``camera_controller.new_image`` on the main thread and read by the
worker between moves.
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
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from py2flamingo.models.data.pixel_calibration_models import PixelCalibration
from py2flamingo.services.pixel_calibration_service import PixelCalibrationService
from py2flamingo.services.window_geometry_manager import PersistentDialog

logger = logging.getLogger(__name__)


class _SweepWorker(QThread):
    """Runs the calibration sweep off the UI thread."""

    progress = pyqtSignal(str, float)
    finished_ok = pyqtSignal(object)  # PixelCalibration
    failed = pyqtSignal(str)

    def __init__(self, dialog: "PixelCalibratorDialog", params: dict):
        super().__init__()
        self._dlg = dialog
        self._params = params

    def run(self):
        try:
            svc = self._dlg.service
            cal = svc.run_sweep(
                move_relative=self._dlg._move_relative,
                get_position=self._dlg._get_axis,
                grab_frame=self._grab_frame,
                settle=lambda: time.sleep(self._params["settle_s"]),
                progress=lambda m, f: self.progress.emit(m, f),
                nominal_move_um=self._params["nominal_move_um"],
                initial_pixel_um=self._params["initial_pixel_um"],
                quality_threshold=self._params["quality_threshold"],
                get_limits=self._dlg._get_limits,
            )
            self.finished_ok.emit(cal)
        except Exception as e:  # noqa: BLE001 - surface to UI
            # Expected, user-actionable failures (e.g. not enough travel) get a
            # clean one-line log + a dialog; the traceback stays at debug level.
            logger.error("Pixel calibration sweep failed: %s", e)
            logger.debug("sweep failure traceback", exc_info=True)
            self.failed.emit(str(e))

    def _grab_frame(self) -> np.ndarray:
        """Return the latest cached live frame, waiting briefly for a fresh one."""
        deadline = time.time() + 5.0
        while time.time() < deadline:
            frame = self._dlg._latest_frame
            if frame is not None:
                return np.asarray(frame)
            time.sleep(0.05)
        raise RuntimeError("No live frame received — is Live View running?")


class PixelCalibratorDialog(PersistentDialog):
    """Dialog to measure XY pixel size via stage-move / image-shift."""

    def __init__(self, app=None, parent=None):
        super().__init__(parent=parent, window_id="PixelCalibrator")
        self.app = app
        self.setWindowTitle("XY Pixel Calibrator")
        self.setMinimumSize(720, 640)

        self.service = PixelCalibrationService()
        self._latest_frame: Optional[np.ndarray] = None
        self._worker: Optional[_SweepWorker] = None
        # True once *we* started live view (so we can restore the prior state on
        # close instead of leaving the system half-changed: live view running
        # with the light source the user wasn't aware we left it in).
        self._started_live_view = False
        self._result: Optional[PixelCalibration] = self.service.calibration

        self._setup_ui()
        self._connect_signals()
        self._refresh_firmware_reference()
        if self._result is not None:
            self._show_result(self._result)
        self._update_button_states()

    # ================================================================
    # UI
    # ================================================================

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(
            "Focus a textured sample, then run the sweep. The stage is moved by "
            "known small steps and the image shift is measured to compute the "
            "true XY pixel size (and camera rotation)."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Live preview
        self._preview = QLabel("No live frame — start Live View from the main window.")
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setMinimumSize(360, 270)
        self._preview.setStyleSheet(
            "background:#202020; color:#aaa; border:1px solid #444;"
        )
        layout.addWidget(self._preview, alignment=Qt.AlignCenter)

        # Parameters
        params = QGroupBox("Sweep parameters")
        form = QFormLayout(params)
        self._auto_move = QCheckBox("Auto (size from current pixel estimate)")
        self._auto_move.setChecked(True)
        self._auto_move.toggled.connect(self._on_auto_toggled)
        form.addRow(self._auto_move)
        self._move_spin = QDoubleSpinBox()
        self._move_spin.setRange(0.1, 5000.0)
        self._move_spin.setValue(50.0)
        self._move_spin.setSuffix(" µm nominal move")
        self._move_spin.setEnabled(False)
        form.addRow("Move size:", self._move_spin)
        self._quality_spin = QDoubleSpinBox()
        self._quality_spin.setRange(0.0, 1.0)
        self._quality_spin.setSingleStep(0.05)
        self._quality_spin.setValue(0.30)
        form.addRow("Min quality:", self._quality_spin)
        self._settle_spin = QDoubleSpinBox()
        self._settle_spin.setRange(0.0, 5.0)
        self._settle_spin.setSingleStep(0.1)
        self._settle_spin.setValue(0.5)
        self._settle_spin.setSuffix(" s settle")
        form.addRow("Settle:", self._settle_spin)
        self._firmware_label = QLabel("Firmware pixel size: —")
        form.addRow(self._firmware_label)
        layout.addWidget(params)

        # Run
        self._run_btn = QPushButton("▶  Run Calibration Sweep")
        self._run_btn.setStyleSheet(
            "QPushButton{background:#2e7d32;color:white;font-weight:bold;padding:8px;}"
            "QPushButton:disabled{background:#555;}"
        )
        self._run_btn.clicked.connect(self._on_run)
        layout.addWidget(self._run_btn)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        # Results
        res = QGroupBox("Result")
        rlayout = QVBoxLayout(res)
        self._result_label = QLabel("No calibration yet.")
        self._result_label.setWordWrap(True)
        rlayout.addWidget(self._result_label)
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["axis", "dx (µm)", "dy (µm)", "shift x (px)", "shift y (px)", "quality"]
        )
        self._table.setMaximumHeight(170)
        rlayout.addWidget(self._table)
        layout.addWidget(res, stretch=1)

        # Actions
        actions = QHBoxLayout()
        self._save_btn = QPushButton("Save Calibration")
        self._save_btn.clicked.connect(self._on_save)
        actions.addWidget(self._save_btn)
        self._patch_btn = QPushButton("Patch Configs…")
        self._patch_btn.clicked.connect(self._on_patch)
        actions.addWidget(self._patch_btn)
        actions.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

    def _connect_signals(self):
        cc = self._camera_controller()
        if cc is not None and hasattr(cc, "new_image"):
            try:
                cc.new_image.connect(self._on_new_image)
            except Exception:
                logger.warning("Could not connect to camera new_image signal")

    # ================================================================
    # Service accessors
    # ================================================================

    def _camera_controller(self):
        return getattr(self.app, "camera_controller", None) if self.app else None

    def _camera_service(self):
        return getattr(self.app, "camera_service", None) if self.app else None

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
        except Exception:
            pass
        return False

    def _move_relative(self, axis: str, delta_mm: float) -> None:
        mc = self._movement_controller()
        if mc is None:
            raise RuntimeError("Movement controller unavailable")
        mc.move_relative(axis, delta_mm, verify=True)

    def _get_axis(self, axis: str) -> float:
        mc = self._movement_controller()
        if mc is None:
            raise RuntimeError("Movement controller unavailable")
        val = mc.get_position(axis)
        if val is None:
            raise RuntimeError(f"Could not read stage {axis} position")
        return float(val)

    def _get_limits(self, axis: str):
        """Soft limits ``(min_mm, max_mm)`` for an axis, or None if unavailable.

        Lets the sweep plan moves that stay within the stage range instead of
        commanding an out-of-bounds move (which the stage layer hard-rejects).
        """
        mc = self._movement_controller()
        pc = getattr(mc, "position_controller", None) if mc else None
        if pc is None:
            return None
        try:
            lim = pc.get_stage_limits().get(axis.lower())
            if lim is not None:
                return (float(lim["min"]), float(lim["max"]))
        except Exception:  # noqa: BLE001 - unknown limits -> caller falls back
            return None
        return None

    # ================================================================
    # Live frame
    # ================================================================

    def _on_new_image(self, image: np.ndarray, header=None):
        self._latest_frame = image
        self._update_preview(image)

    def _update_preview(self, image: np.ndarray):
        try:
            arr = np.asarray(image)
            if arr.ndim > 2:
                arr = arr.reshape(-1, *arr.shape[-2:]).mean(axis=0)
            lo, hi = np.percentile(arr, (1, 99))
            if hi <= lo:
                hi = lo + 1
            disp = np.clip((arr - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
            disp = np.ascontiguousarray(disp)
            h, w = disp.shape
            qimg = QImage(disp.data, w, h, w, QImage.Format_Grayscale8)
            pix = QPixmap.fromImage(qimg).scaled(
                self._preview.width(),
                self._preview.height(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self._preview.setPixmap(pix)
        except Exception as e:  # noqa: BLE001
            logger.debug("preview update failed: %s", e)

    def _refresh_firmware_reference(self):
        cs = self._camera_service()
        if cs is None or not self._is_connected():
            return
        try:
            mm_per_px = cs.get_pixel_field_of_view()
            if mm_per_px:
                um = mm_per_px * 1000.0
                self._firmware_label.setText(
                    f"Firmware pixel size: {um:.4f} µm/px "
                    f"(from reported magnification — the value we aim to improve)"
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("firmware pixel size unavailable: %s", e)

    # ================================================================
    # Run / results
    # ================================================================

    def _on_auto_toggled(self, checked: bool):
        self._move_spin.setEnabled(not checked)

    def _initial_pixel_um(self) -> Optional[float]:
        cs = self._camera_service()
        if cs is None:
            return None
        try:
            mm_per_px = cs.get_pixel_field_of_view()
            return mm_per_px * 1000.0 if mm_per_px else None
        except Exception:
            return None

    def _on_run(self):
        if not self._is_connected():
            QMessageBox.warning(
                self, "Not connected", "Connect to the microscope first."
            )
            return
        # Ensure live view is running so frames arrive. Remember that we were the
        # one to start it, so we can restore the prior state when the dialog
        # closes (see _restore_camera_state).
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
                "No live frame yet. Start Live View and ensure the sample is in "
                "focus, then run again.",
            )
            return

        params = {
            "nominal_move_um": (
                None if self._auto_move.isChecked() else self._move_spin.value()
            ),
            "initial_pixel_um": self._initial_pixel_um(),
            "quality_threshold": self._quality_spin.value(),
            "settle_s": self._settle_spin.value(),
        }
        self._run_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._worker = _SweepWorker(self, params)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, msg: str, frac: float):
        self._status.setText(msg)
        self._progress.setValue(int(frac * 100))

    def _on_finished(self, cal: PixelCalibration):
        self._result = cal
        self._progress.setVisible(False)
        self._run_btn.setEnabled(True)
        self._show_result(cal)
        self._update_button_states()

    def _on_failed(self, err: str):
        self._progress.setVisible(False)
        self._run_btn.setEnabled(True)
        self._status.setText("Failed.")
        QMessageBox.critical(self, "Calibration failed", err)

    def _show_result(self, cal: PixelCalibration):
        self._result_label.setText(
            f"<b>Pixel size:  X = {cal.pixel_size_x_um:.4f}   "
            f"Y = {cal.pixel_size_y_um:.4f} µm/px</b>  "
            f"(mean {cal.mean_pixel_size_um:.4f})<br>"
            f"Rotation: {cal.rotation_deg:.2f}°   "
            f"Shear: {cal.shear_deg:.2f}°   "
            f"Anisotropy: {cal.anisotropy:.3f}<br>"
            f"Residual: {cal.residual_px:.2f} px   "
            f"Points: {cal.n_points}   Min quality: {cal.min_quality:.2f}"
        )
        self._table.setRowCount(len(cal.moves))
        for r, m in enumerate(cal.moves):
            vals = [
                m.axis,
                f"{m.dx_mm * 1000:.2f}",
                f"{m.dy_mm * 1000:.2f}",
                f"{m.shift_x_px:.2f}",
                f"{m.shift_y_px:.2f}",
                f"{m.quality:.2f}",
            ]
            for c, v in enumerate(vals):
                self._table.setItem(r, c, QTableWidgetItem(str(v)))

    # ================================================================
    # Save / patch
    # ================================================================

    def _on_save(self):
        if self._result is None:
            return
        try:
            self.service.save(self._result)
            # If an optics mismatch had blocked acquisition, a calibration saved
            # for the current optics should clear it.
            guard = getattr(self.app, "optics_guard", None) if self.app else None
            if guard is not None:
                try:
                    guard.note_calibration_saved()
                except Exception:
                    logger.debug("optics guard re-check failed", exc_info=True)
            QMessageBox.information(
                self, "Saved", f"Calibration saved to\n{self.service._file}"
            )
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Save failed", str(e))

    def _on_patch(self):
        if self._result is None:
            return
        try:
            patches = self.service.propose_config_patch(self._result)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Patch failed", str(e))
            return
        if not patches:
            QMessageBox.information(self, "Nothing to patch", "No config files found.")
            return

        lines = ["The following config values will be updated:\n"]
        for p in patches:
            fname = p["file"].split("/")[-1]
            lines.append(
                f"• {fname}: {p['key']}\n    {p['old']} → {p['new']}\n    {p['note']}"
            )
        lines.append("\nA .bak backup is written before any change. Proceed?")
        reply = QMessageBox.question(
            self,
            "Patch config files",
            "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            written = self.service.apply_config_patch(patches)
            QMessageBox.information(
                self,
                "Configs patched",
                "Updated:\n" + "\n".join(written) + "\n\n"
                "Restart the app for hardware-config changes to take effect.",
            )
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Patch failed", str(e))

    def _update_button_states(self):
        has_result = self._result is not None
        self._save_btn.setEnabled(has_result)
        self._patch_btn.setEnabled(has_result)
        self._run_btn.setEnabled(self._is_connected())

    def _restore_camera_state(self) -> None:
        """Leave the camera/illumination as we found it.

        If the calibrator started live view, stop it on the way out. Going
        through ``stop_live_view`` also disables the light sources and emits the
        ``state_changed`` / ``preview_disabled`` signals, so the Live Viewer,
        Sample View live toggle, and the laser/LED panels all resync to a clean,
        consistent state — rather than being left showing "live + LED on" while
        the hardware is actually half-off.
        """
        if not self._started_live_view:
            return
        self._started_live_view = False
        cc = self._camera_controller()
        try:
            if cc is not None and cc.is_live_view_active():
                logger.info("Pixel calibrator: stopping the live view it started")
                cc.stop_live_view()
        except Exception:  # noqa: BLE001 - cleanup must not raise on close
            logger.debug("could not restore camera state on close", exc_info=True)

    def closeEvent(self, event):
        try:
            if self._worker is not None and self._worker.isRunning():
                self._worker.wait(2000)
        except Exception:
            pass
        # Stop consuming live frames and restore the pre-calibration state so the
        # Live Viewer / Sample View reflect reality after the dialog closes.
        cc = self._camera_controller()
        if cc is not None and hasattr(cc, "new_image"):
            try:
                cc.new_image.disconnect(self._on_new_image)
            except Exception:
                pass
        self._restore_camera_state()
        super().closeEvent(event)
