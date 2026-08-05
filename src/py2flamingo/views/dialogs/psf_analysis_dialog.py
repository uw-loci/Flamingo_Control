"""PSF Analysis dialog — measure optical resolution (FWHM) from a bead image.

This is the ONLY file that couples the app to the ``psf_analysis`` package: it
imports the package's public API plus Qt/napari and nothing of the core touches
the app. If ``psf_analysis`` is later split into its own repo, this dialog is the
adapter that stays behind (or is swapped for a shim import).

Flow: browse a bead stack (TIFF/Zarr/npy) → prefill voxel size from the file's
metadata, falling back to the microscope config for the XY pixel size → run
:class:`PSFAnalysisService` on a worker thread → show a per-bead FWHM table, the
selected bead's per-axis Gaussian fits (embedded matplotlib), and (optionally) a
napari overlay of the accepted beads on the Sample View viewer.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from py2flamingo.psf_analysis import PSFAnalysisService, PSFSettings, load_volume

logger = logging.getLogger(__name__)

_SUPPORTED_FILTER = "Bead images (*.tif *.tiff *.npy);;TIFF (*.tif *.tiff);;NumPy (*.npy);;All files (*)"


class _AnalysisWorker(QThread):
    """Runs PSFAnalysisService off the UI thread."""

    finished_result = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, volume, voxel_size_um, settings, parent=None):
        super().__init__(parent)
        self._volume = volume
        self._voxel_size_um = voxel_size_um
        self._settings = settings

    def run(self):
        try:
            result = PSFAnalysisService().analyze(
                self._volume, voxel_size_um=self._voxel_size_um, settings=self._settings
            )
            self.finished_result.emit(result)
        except Exception as exc:  # surfaced to the user via failed()
            logger.error("PSF analysis failed", exc_info=True)
            self.failed.emit(str(exc))


class PSFAnalysisDialog(QDialog):
    """Extensions → PSF Analysis dialog."""

    def __init__(self, app=None, parent=None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("PSF Analysis")
        self.resize(1000, 640)

        self._volume: Optional[np.ndarray] = None
        self._result = None
        self._worker: Optional[_AnalysisWorker] = None
        self._last_dir = str(Path.home())

        self._build_ui()
        self._prefill_from_config()

    # ------------------------------------------------------------------ build
    def _build_ui(self):
        root = QVBoxLayout(self)

        # --- Input row ---
        input_row = QHBoxLayout()
        self._path_label = QLabel("No file loaded")
        self._path_label.setStyleSheet("color: gray;")
        browse_btn = QPushButton("Load bead image…")
        browse_btn.clicked.connect(self._on_browse)
        input_row.addWidget(browse_btn)
        input_row.addWidget(self._path_label, stretch=1)
        root.addLayout(input_row)

        # --- Parameters ---
        params_box = QGroupBox("Parameters")
        form = QFormLayout(params_box)
        self._xy_um = QDoubleSpinBox()
        self._xy_um.setRange(0.001, 100.0)
        self._xy_um.setDecimals(4)
        self._xy_um.setValue(0.406)
        self._xy_um.setSuffix(" µm")
        form.addRow("XY pixel size:", self._xy_um)

        self._z_um = QDoubleSpinBox()
        self._z_um.setRange(0.001, 1000.0)
        self._z_um.setDecimals(4)
        self._z_um.setValue(4.0)
        self._z_um.setSuffix(" µm")
        form.addRow("Z step:", self._z_um)

        self._window_um = QDoubleSpinBox()
        self._window_um.setRange(1.0, 100.0)
        self._window_um.setValue(6.0)
        self._window_um.setSuffix(" µm")
        form.addRow("Crop window:", self._window_um)

        self._min_dist = QSpinBox()
        self._min_dist.setRange(1, 500)
        self._min_dist.setValue(10)
        self._min_dist.setSuffix(" px")
        form.addRow("Min peak distance:", self._min_dist)

        self._threshold_rel = QDoubleSpinBox()
        self._threshold_rel.setRange(0.0, 1.0)
        self._threshold_rel.setSingleStep(0.05)
        self._threshold_rel.setValue(0.2)
        form.addRow("Detection threshold (rel):", self._threshold_rel)

        self._min_sep = QDoubleSpinBox()
        self._min_sep.setRange(0.0, 1000.0)
        self._min_sep.setValue(10.0)
        self._min_sep.setSuffix(" µm")
        form.addRow("Min bead separation:", self._min_sep)
        root.addWidget(params_box)

        # --- Run + summary ---
        run_row = QHBoxLayout()
        self._run_btn = QPushButton("Run PSF Analysis")
        self._run_btn.setStyleSheet("font-weight: bold; padding: 6px;")
        self._run_btn.clicked.connect(self._on_run)
        self._run_btn.setEnabled(False)
        run_row.addWidget(self._run_btn)
        self._summary_label = QLabel("")
        run_row.addWidget(self._summary_label, stretch=1)
        root.addLayout(run_row)

        # --- Results: table (left) + fit plot (right) ---
        splitter = QSplitter(Qt.Horizontal)
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["#", "FWHM X", "FWHM Y", "FWHM Z", "R²(x)", "status"]
        )
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        splitter.addWidget(self._table)

        self._plot_widget = _FitPlot()
        splitter.addWidget(self._plot_widget)
        splitter.setSizes([460, 540])
        root.addWidget(splitter, stretch=1)

        # --- Bottom actions ---
        actions = QHBoxLayout()
        self._overlay_cb = QCheckBox("Show beads in Sample View")
        self._overlay_cb.setChecked(True)
        actions.addWidget(self._overlay_cb)
        actions.addStretch(1)
        self._export_btn = QPushButton("Export CSV…")
        self._export_btn.clicked.connect(self._on_export)
        self._export_btn.setEnabled(False)
        actions.addWidget(self._export_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        actions.addWidget(close_btn)
        root.addLayout(actions)

    def _prefill_from_config(self):
        """Prefill XY pixel size from the microscope hardware config."""
        try:
            from py2flamingo.configs.config_loader import get_hardware_config

            hw = get_hardware_config()
            if hw.effective_pixel_size_um:
                self._xy_um.setValue(float(hw.effective_pixel_size_um))
        except Exception as exc:  # config is best-effort; user can override
            logger.debug("Could not prefill pixel size from config: %s", exc)

    # ------------------------------------------------------------------- load
    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select bead image", self._last_dir, _SUPPORTED_FILTER
        )
        if not path:
            return
        self._last_dir = str(Path(path).parent)
        try:
            volume, (z_um, y_um, x_um) = load_volume(path)
        except Exception as exc:
            QMessageBox.critical(self, "Load failed", f"Could not load image:\n{exc}")
            return

        self._volume = volume
        self._path_label.setText(f"{Path(path).name}   shape={volume.shape}")
        self._path_label.setStyleSheet("")
        # Prefill voxel size from file metadata when present (keep config/user
        # value otherwise).
        if x_um:
            self._xy_um.setValue(float(x_um))
        if z_um:
            self._z_um.setValue(float(z_um))
        self._run_btn.setEnabled(True)

    # -------------------------------------------------------------------- run
    def _on_run(self):
        if self._volume is None:
            return
        settings = PSFSettings(
            threshold_rel=float(self._threshold_rel.value()),
            min_distance_px=int(self._min_dist.value()),
            window_um=float(self._window_um.value()),
            min_separation_um=float(self._min_sep.value()),
        )
        voxel = (
            float(self._z_um.value()),
            float(self._xy_um.value()),
            float(self._xy_um.value()),
        )
        self._run_btn.setEnabled(False)
        self._summary_label.setText("Running…")
        self._worker = _AnalysisWorker(self._volume, voxel, settings, parent=self)
        self._worker.finished_result.connect(self._on_analysis_done)
        self._worker.failed.connect(self._on_analysis_failed)
        self._worker.start()

    def _on_analysis_failed(self, message: str):
        self._run_btn.setEnabled(True)
        self._summary_label.setText("")
        QMessageBox.critical(self, "Analysis failed", message)

    def _on_analysis_done(self, result):
        self._result = result
        self._run_btn.setEnabled(True)
        self._export_btn.setEnabled(result.n_accepted > 0)
        self._populate_table(result)
        self._update_summary(result)
        if self._overlay_cb.isChecked():
            self._show_overlay(result)

    # ---------------------------------------------------------------- results
    def _update_summary(self, result):
        s = result.summary()

        def fmt(axis):
            m = s.get(f"fwhm_{axis}_um_mean")
            sd = s.get(f"fwhm_{axis}_um_std")
            return f"{m:.2f}±{sd:.2f}" if m is not None else "—"

        self._summary_label.setText(
            f"Detected {result.n_detected}, accepted {result.n_accepted}   "
            f"|  FWHM µm  X {fmt('x')}  Y {fmt('y')}  Z {fmt('z')}"
        )

    def _populate_table(self, result):
        beads = result.beads
        self._table.setRowCount(len(beads))
        for row, bead in enumerate(beads):

            def cell(text):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                return item

            def fwhm(v):
                return f"{v:.2f}" if v is not None else "—"

            r2x = bead.fits["x"].r_squared if "x" in bead.fits else None
            status = "accepted" if bead.accepted else (bead.reject_reason or "rejected")
            self._table.setItem(row, 0, cell(str(bead.bead_id)))
            self._table.setItem(row, 1, cell(fwhm(bead.fwhm_x_um)))
            self._table.setItem(row, 2, cell(fwhm(bead.fwhm_y_um)))
            self._table.setItem(row, 3, cell(fwhm(bead.fwhm_z_um)))
            self._table.setItem(row, 4, cell(f"{r2x:.3f}" if r2x is not None else "—"))
            self._table.setItem(row, 5, cell(status))
        self._table.resizeColumnsToContents()

    def _on_row_selected(self):
        if self._result is None:
            return
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        if 0 <= idx < len(self._result.beads):
            self._plot_widget.show_bead(self._result.beads[idx])

    # --------------------------------------------------------------- overlay
    def _show_overlay(self, result):
        """Add the analyzed volume + accepted bead points to the Sample View."""
        viewer = None
        if self.app is not None and getattr(self.app, "sample_view", None) is not None:
            try:
                viewer = self.app.sample_view.get_viewer()
            except Exception:
                viewer = None
        if viewer is None:
            logger.info("No Sample View viewer available; skipping bead overlay")
            return
        try:
            for name in ("PSF beads volume", "PSF beads"):
                if name in viewer.layers:
                    del viewer.layers[name]
            viewer.add_image(self._volume, name="PSF beads volume", blending="additive")
            coords = np.array([b.centroid_voxel for b in result.accepted], dtype=float)
            if coords.size:
                viewer.add_points(
                    coords,
                    name="PSF beads",
                    size=10,
                    face_color="red",
                    border_color="white",
                    symbol="ring",
                )
        except Exception as exc:
            logger.warning("Could not add bead overlay: %s", exc)

    # ---------------------------------------------------------------- export
    def _on_export(self):
        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export PSF results",
            str(Path(self._last_dir) / "psf_results.csv"),
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            self._result.to_csv(path)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Exported", f"Wrote {path}")


class _FitPlot(QWidget):
    """Embedded matplotlib canvas showing a bead's three Gaussian fits."""

    def __init__(self, parent=None):
        super().__init__(parent)
        import matplotlib

        matplotlib.use("QtAgg", force=False)
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure

        self._figure = Figure(figsize=(5, 4), tight_layout=True)
        self._canvas = FigureCanvasQTAgg(self._figure)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas)
        self._draw_placeholder()

    def _draw_placeholder(self):
        self._figure.clear()
        ax = self._figure.add_subplot(111)
        ax.text(
            0.5,
            0.5,
            "Select a bead to see its fits",
            ha="center",
            va="center",
            transform=ax.transAxes,
            color="gray",
        )
        ax.set_axis_off()
        self._canvas.draw_idle()

    def show_bead(self, bead):
        self._figure.clear()
        axes_present = [a for a in ("x", "y", "z") if a in bead.fits]
        if not axes_present:
            self._draw_placeholder()
            return
        n = len(axes_present)
        for i, axis in enumerate(axes_present):
            fit = bead.fits[axis]
            ax = self._figure.add_subplot(n, 1, i + 1)
            if fit.coords_px is not None and fit.profile is not None:
                ax.plot(fit.coords_px, fit.profile, "o", ms=3, label="data")
                ax.plot(fit.coords_px, fit.fit_curve, "-", label="fit")
            ax.set_title(
                f"{axis.upper()}  FWHM={fit.fwhm_um:.2f} µm  R²={fit.r_squared:.3f}",
                fontsize=9,
            )
            ax.tick_params(labelsize=7)
        self._figure.suptitle(f"Bead #{bead.bead_id}", fontsize=10)
        self._canvas.draw_idle()
