"""Tile Stitching Dialog.

Non-modal dialog for stitching raw acquisition tile data into a single volume.
Operates on saved acquisition data on disk — no microscope connection required.
"""

import logging
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import QProcess, QSettings, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from py2flamingo.services.window_geometry_manager import PersistentDialog

logger = logging.getLogger(__name__)

# QSettings keys
_SETTINGS_GROUP = "StitchingDialog"


class StitchingDialog(PersistentDialog):
    """Dialog for stitching raw acquisition tile data.

    Provides UI for configuring and running the stitching pipeline:
    - Acquisition/output directory selection
    - Pixel size, Z step, downsample factor, illumination fusion, destripe
    - Tile discovery, pipeline execution with progress/log, cancellation
    """

    # Emitted when user wants to load stitched output into SampleView
    load_stitched_requested = pyqtSignal(str)

    # How many directory levels to go up when restoring the acq dir path.
    # Subfolder-per-tile layout: grandparent (2 levels up).
    _acq_dir_restore_levels_up = 2

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._logger = logging.getLogger(__name__)
        self._worker = None
        self._tiles = None  # Cached discovered tiles

        self.setWindowTitle("Tile Stitching")
        self.setMinimumWidth(650)
        self.setMinimumHeight(550)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self._setup_ui()
        self._restore_settings()

    def _setup_ui(self):
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # --- Directory selection ---
        dir_layout = QGridLayout()
        dir_layout.setSpacing(6)

        dir_layout.addWidget(QLabel("Acquisition Directory:"), 0, 0)
        self._acq_dir_edit = QLineEdit()
        self._acq_dir_edit.setPlaceholderText("Path to raw acquisition folder...")
        dir_layout.addWidget(self._acq_dir_edit, 0, 1)
        acq_browse_btn = QPushButton("Browse...")
        acq_browse_btn.clicked.connect(self._browse_acq_dir)
        dir_layout.addWidget(acq_browse_btn, 0, 2)

        dir_layout.addWidget(QLabel("Output Directory:"), 1, 0)
        self._output_dir_edit = QLineEdit()
        self._output_dir_edit.setPlaceholderText("Where to save stitched output...")
        dir_layout.addWidget(self._output_dir_edit, 1, 1)
        out_browse_btn = QPushButton("Browse...")
        out_browse_btn.clicked.connect(self._browse_output_dir)
        dir_layout.addWidget(out_browse_btn, 1, 2)

        layout.addLayout(dir_layout)

        # --- Settings group ---
        settings_group = QGroupBox("Settings")
        settings_layout = QGridLayout()
        settings_layout.setSpacing(6)

        # Pixel size
        settings_layout.addWidget(QLabel("Pixel size (\u00b5m):"), 0, 0)
        self._pixel_size_spin = QDoubleSpinBox()
        self._pixel_size_spin.setRange(0.01, 100.0)
        self._pixel_size_spin.setDecimals(3)
        self._pixel_size_spin.setValue(0.406)
        self._pixel_size_spin.setSingleStep(0.001)
        settings_layout.addWidget(self._pixel_size_spin, 0, 1)

        # Z step
        settings_layout.addWidget(QLabel("Z step (\u00b5m):"), 0, 2)
        self._z_step_spin = QDoubleSpinBox()
        self._z_step_spin.setRange(0.0, 1000.0)
        self._z_step_spin.setDecimals(3)
        self._z_step_spin.setValue(0.0)
        self._z_step_spin.setSpecialValueText("Auto")
        self._z_step_spin.setToolTip(
            "0 = auto-detect from Workflow.txt Z range and plane count"
        )
        settings_layout.addWidget(self._z_step_spin, 0, 3)

        # Downsample
        settings_layout.addWidget(QLabel("Downsample:"), 1, 0)
        self._downsample_combo = QComboBox()
        for label, value in [("1x (none)", 1), ("2x", 2), ("4x", 4), ("8x", 8)]:
            self._downsample_combo.addItem(label, value)
        self._downsample_combo.setToolTip(
            "Downsample tiles before registration/fusion for faster processing"
        )
        settings_layout.addWidget(self._downsample_combo, 1, 1)

        # Illumination fusion
        settings_layout.addWidget(QLabel("Illum. fusion:"), 1, 2)
        self._fusion_combo = QComboBox()
        for label, value in [
            ("Max", "max"),
            ("Mean", "mean"),
            ("Leonardo FUSE", "leonardo"),
        ]:
            self._fusion_combo.addItem(label, value)
        settings_layout.addWidget(self._fusion_combo, 1, 3)

        # Destripe
        self._destripe_cb = QCheckBox("Destripe (PyStripe) \u2731")
        self._destripe_cb.setToolTip(
            "\u2731 Processes every Z-plane at full resolution\n"
            "before downsampling.\n\n"
            "Removes horizontal stripe artifacts from light-sheet data.\n"
            "Uses multiple CPU cores automatically."
        )
        settings_layout.addWidget(self._destripe_cb, 2, 0)

        self._destripe_fast_cb = QCheckBox("Fast")
        self._destripe_fast_cb.setToolTip(
            "Destripe after downsampling instead of before.\n"
            "Much faster but slightly lower quality.\n\n"
            "Only effective when downsample factor > 1."
        )
        self._destripe_fast_cb.setEnabled(False)
        self._destripe_cb.toggled.connect(self._destripe_fast_cb.setEnabled)
        settings_layout.addWidget(self._destripe_fast_cb, 2, 1)

        # Output format
        settings_layout.addWidget(QLabel("Output format:"), 2, 2)
        self._format_combo = QComboBox()
        self._format_combo.addItem("OME-Zarr (Fiji compatible)", "ome-zarr-v2")
        self._format_combo.addItem("OME-Zarr Sharded", "ome-zarr-sharded")
        self._format_combo.addItem("OME-TIFF (single file)", "ome-tiff")
        self._format_combo.addItem("Both (Sharded + TIFF)", "both")
        self._format_combo.setToolTip(
            "OME-Zarr (Fiji compatible): opens in Fiji, QuPath, BigDataViewer, napari\n"
            "OME-Zarr Sharded: fewest files, napari only (Fiji cannot open)\n"
            "OME-TIFF: single file, universal viewer support\n"
            "Both: write Zarr Sharded + TIFF"
        )
        self._format_combo.currentIndexChanged.connect(self._on_format_changed)
        settings_layout.addWidget(self._format_combo, 2, 3)

        # Format help label
        format_help = QLabel("Zarr?")
        format_help.setStyleSheet(
            "QLabel { color: #1976D2; font-weight: bold; font-size: 11px; "
            "border: 1px solid #1976D2; border-radius: 8px; "
            "padding: 0px 4px; "
            "qproperty-alignment: AlignCenter; }"
        )
        format_help.setToolTip(
            "<b>Zarr Format Guide</b><br><br>"
            "<b>OME-Zarr (Fiji compatible)</b> &mdash; Zarr v2 + OME-NGFF v0.4<br>"
            "Opens in <b>Fiji</b> (N5 plugin), <b>QuPath</b>, <b>BigDataViewer</b>, "
            "<b>napari</b>, and most bio-imaging tools. More files on disk "
            "(~250k/TB) but universal reader support.<br><br>"
            "<b>OME-Zarr Sharded</b> &mdash; Zarr v3 + OME-NGFF v0.5<br>"
            "Fewest files (~2,000/TB) via sharding. Only readable by "
            "<b>napari</b> and zarr-python 3.x. Fiji, QuPath, and "
            "BigDataViewer <b>cannot open this format</b>.<br><br>"
            "<b>OME-TIFF</b> &mdash; Single file per channel<br>"
            "Universally readable. Best for sharing and archiving. "
            "Larger files, slower random access than Zarr.<br><br>"
            "<hr>"
            "<b>Which should I choose?</b><br>"
            "<ul>"
            "<li><b>Need Fiji/QuPath?</b> &rarr; OME-Zarr (Fiji compatible) "
            "or OME-TIFF</li>"
            "<li><b>napari only, large data?</b> &rarr; OME-Zarr Sharded</li>"
            "<li><b>Sharing with collaborators?</b> &rarr; OME-TIFF</li>"
            "</ul>"
            "<hr>"
            "<b>Need Imaris .ims format?</b><br>"
            "Export as <b>OME-TIFF</b>, then use <b>ImarisFileConverter</b> "
            "(bundled with Imaris) to convert OME-TIFF &rarr; .ims."
        )
        settings_layout.addWidget(format_help, 2, 4)

        # Deconvolution
        self._deconv_cb = QCheckBox("Deconvolution \u2731")
        self._deconv_cb.setToolTip(
            "\u2731 GPU Richardson-Lucy deconvolution per tile.\n"
            "Requires pycudadecon or RedLionfish.\n\n"
            "Significantly improves resolution."
        )
        settings_layout.addWidget(self._deconv_cb, 3, 0, 1, 2)

        # Content-based fusion (BigStitcher-inspired)
        self._content_fusion_cb = QCheckBox("Content-based blending \u2731")
        self._content_fusion_cb.setToolTip(
            "\u2731 Weights tile overlaps by local sharpness\n"
            "(Preibisch local-variance, inspired by BigStitcher).\n\n"
            "Improves fusion quality in overlap regions."
        )
        settings_layout.addWidget(self._content_fusion_cb, 3, 2, 1, 2)

        # Skip registration
        self._skip_reg_cb = QCheckBox("Skip registration")
        self._skip_reg_cb.setToolTip(
            "Use stage positions only — skip phase-correlation registration.\n\n"
            "CHECK this when:\n"
            "  \u2022 Tiles have no overlap (registration needs overlap to work)\n"
            "  \u2022 Stage positions are precise (e.g. single-workflow tiling)\n"
            "  \u2022 You want faster processing on large tile grids\n\n"
            "UNCHECK (default) when:\n"
            "  \u2022 Tiles were acquired across multiple sessions with drift\n"
            "  \u2022 You need sub-pixel alignment at tile boundaries\n"
            "  \u2022 Tiles have sufficient overlap for correlation (~10-25%)"
        )
        self._skip_reg_cb.toggled.connect(self._on_skip_reg_toggled)
        settings_layout.addWidget(self._skip_reg_cb, 4, 0)

        # Registration binning
        self._reg_binning_label = QLabel("Reg. binning:")
        settings_layout.addWidget(self._reg_binning_label, 4, 1)
        self._reg_binning_combo = QComboBox()
        self._reg_binning_combo.addItem("Fine (z1 y2 x2)", {"z": 1, "y": 2, "x": 2})
        self._reg_binning_combo.addItem("Default (z2 y4 x4)", {"z": 2, "y": 4, "x": 4})
        self._reg_binning_combo.addItem("Fast (z4 y8 x8)", {"z": 4, "y": 8, "x": 8})
        self._reg_binning_combo.setCurrentIndex(1)  # Default
        self._reg_binning_combo.setToolTip(
            "How much to downsample tiles for phase-correlation registration.\n\n"
            "Fine (z1 y2 x2): highest accuracy, slowest, most memory.\n"
            "Best for small tiles or when sub-pixel precision matters.\n\n"
            "Default (z2 y4 x4): good balance of speed and accuracy.\n"
            "Works well for 2048\u00d72048 tiles with 10-25% overlap.\n\n"
            "Fast (z4 y8 x8): fastest, lower precision.\n"
            "Use for large tile grids where speed matters more\n"
            "than sub-pixel accuracy, or as a quick check before\n"
            "re-running with finer binning."
        )
        settings_layout.addWidget(self._reg_binning_combo, 4, 2)

        # Flat-field correction
        self._flat_field_cb = QCheckBox("Flat-field correction")
        self._update_preprocessing_availability()
        settings_layout.addWidget(self._flat_field_cb, 4, 2, 1, 2)

        # Depth-dependent attenuation correction
        self._depth_atten_cb = QCheckBox("Depth attenuation")
        self._depth_atten_cb.setToolTip(
            "Correct exponential Z-intensity falloff\n"
            "(Beer-Lambert scattering/absorption compensation).\n"
            "Auto-fits decay coefficient from data unless overridden."
        )
        settings_layout.addWidget(self._depth_atten_cb, 5, 0)

        self._depth_atten_mu_spin = QDoubleSpinBox()
        self._depth_atten_mu_spin.setRange(0.0, 1.0)
        self._depth_atten_mu_spin.setDecimals(5)
        self._depth_atten_mu_spin.setValue(0.0)
        self._depth_atten_mu_spin.setSingleStep(0.0001)
        self._depth_atten_mu_spin.setSpecialValueText("Auto")
        self._depth_atten_mu_spin.setToolTip(
            "Decay coefficient \u00b5 (1/\u00b5m). 0 = auto-fit from data."
        )
        self._depth_atten_mu_spin.setEnabled(False)
        self._depth_atten_cb.toggled.connect(self._depth_atten_mu_spin.setEnabled)
        settings_layout.addWidget(self._depth_atten_mu_spin, 5, 1)

        # Row 6: package + memory mode
        self._ozx_cb = QCheckBox("Package as .ozx")
        self._ozx_cb.setToolTip(
            "Create a single .ozx ZIP file from the OME-Zarr output\n"
            "for easy sharing/copying"
        )
        settings_layout.addWidget(self._ozx_cb, 6, 0, 1, 2)

        settings_layout.addWidget(QLabel("Memory mode:"), 6, 2)
        self._streaming_combo = QComboBox()
        self._streaming_combo.addItem("Auto", None)
        self._streaming_combo.addItem("In-memory (fast)", False)
        self._streaming_combo.addItem("Streaming (low memory)", True)
        self._streaming_combo.setToolTip(
            "Auto: automatically chooses based on estimated data size and RAM.\n\n"
            "In-memory: computes the full fused volume in RAM before writing.\n"
            "Faster, but requires RAM > ~2.5x the output size.\n\n"
            "Streaming: writes output chunk-by-chunk directly from the fusion\n"
            "graph. Uses minimal RAM (~2 tile volumes), required for TB-scale data."
        )
        settings_layout.addWidget(self._streaming_combo, 6, 3)

        # Channels
        settings_layout.addWidget(QLabel("Channels:"), 7, 0)
        self._channels_edit = QLineEdit()
        self._channels_edit.setPlaceholderText("All (or e.g. 0,1)")
        self._channels_edit.setToolTip(
            "Leave empty for all channels, or comma-separated list (e.g. 0,1)"
        )
        settings_layout.addWidget(self._channels_edit, 7, 1, 1, 3)

        # Memory estimate label (updated after tile discovery)
        self._memory_label = QLabel("")
        self._memory_label.setStyleSheet(
            "color: #888; font-style: italic; font-size: 11px;"
        )
        settings_layout.addWidget(self._memory_label, 8, 0, 1, 5)

        # Timing legend
        legend = QLabel("\u2731 = significantly increases processing time")
        legend.setStyleSheet("color: #FF8C00; font-style: italic; font-size: 11px;")
        settings_layout.addWidget(legend, 9, 0, 1, 4)

        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)

        # --- Action buttons ---
        btn_layout = QHBoxLayout()

        self._discover_btn = QPushButton("Discover Tiles")
        self._discover_btn.setToolTip("Scan acquisition directory for tile data")
        self._discover_btn.clicked.connect(self._on_discover)
        self._set_btn_green(self._discover_btn)
        btn_layout.addWidget(self._discover_btn)

        self._run_btn = QPushButton("Run Stitching")
        self._set_btn_default(self._run_btn)
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self._on_run)
        btn_layout.addWidget(self._run_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self._cancel_btn)

        btn_layout.addStretch()

        self._setup_env_btn = QPushButton("Setup Preprocessing...")
        self._setup_env_btn.setToolTip(
            "Install isolated environment for flat-field correction\n"
            "and Leonardo dual-illumination fusion.\n\n"
            "Downloads ~3 GB (torch, basicpy, leonardo-toolset).\n"
            "Only needed once."
        )
        self._setup_env_btn.clicked.connect(self._on_setup_env)
        btn_layout.addWidget(self._setup_env_btn)
        layout.addLayout(btn_layout)

        # --- Log area ---
        log_group = QGroupBox("Log")
        log_layout = QVBoxLayout()
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMinimumHeight(150)
        self._log_text.setStyleSheet(
            "QTextEdit { font-family: monospace; font-size: 11px; }"
        )
        log_layout.addWidget(self._log_text)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)

        # --- Progress bar ---
        progress_layout = QHBoxLayout()
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        progress_layout.addWidget(self._progress_bar)

        self._status_label = QLabel("Ready")
        self._status_label.setMinimumWidth(120)
        progress_layout.addWidget(self._status_label)
        layout.addLayout(progress_layout)

        # Sync .ozx checkbox enabled state with initial format
        self._on_format_changed()

    # --- Directory browsing ---

    def _browse_acq_dir(self):
        current = self._acq_dir_edit.text() or ""
        if current:
            # Start Browse near sibling acquisitions (go up N levels)
            start = Path(current)
            for _ in range(self._acq_dir_restore_levels_up):
                if start.parent != start:
                    start = start.parent
            start = str(start)
        else:
            # Fall back to output dir parent, then mapped drives, then home
            output = self._output_dir_edit.text()
            if output and Path(output).parent.exists():
                start = str(Path(output).parent)
            else:
                start = str(Path.home())
        folder = QFileDialog.getExistingDirectory(
            self, "Select Acquisition Directory", start
        )
        if folder:
            # Normalize to native separators (Qt returns forward slashes)
            folder = str(Path(folder))
            self._acq_dir_edit.setText(folder)
            # Auto-update output dir to match the new acquisition folder
            acq_name = Path(folder).name
            new_output = str(Path(folder).parent / f"{acq_name}_stitched")
            self._output_dir_edit.setText(new_output)
            # Reset tile discovery — Discover is next action again
            self._tiles = None
            self._run_btn.setEnabled(False)
            self._set_btn_green(self._discover_btn)
            self._set_btn_default(self._run_btn)

    def _browse_output_dir(self):
        start = self._output_dir_edit.text() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", start
        )
        if folder:
            self._output_dir_edit.setText(str(Path(folder)))

    # --- Tile discovery ---

    def _on_discover(self):
        """Discover tiles in the acquisition directory."""
        acq_dir = self._acq_dir_edit.text().strip()
        if not acq_dir:
            QMessageBox.warning(
                self, "No Directory", "Please select an acquisition directory."
            )
            return

        acq_path = Path(acq_dir)
        if not acq_path.is_dir():
            QMessageBox.warning(
                self,
                "Invalid Directory",
                f"Directory does not exist:\n{acq_dir}",
            )
            return

        self._log_text.clear()
        self._log(f"Discovering tiles in: {acq_dir}")

        try:
            from py2flamingo.stitching.pipeline import discover_tiles

            tiles = discover_tiles(acq_path)

            if not tiles:
                self._log("No tile folders found.")
                self._tiles = None
                self._run_btn.setEnabled(False)
                return

            self._tiles = tiles
            self._log_tile_summary(tiles)
            self._update_memory_estimate()
            self._run_btn.setEnabled(True)
            # Swap green highlight: Discover done → Run is next action
            self._set_btn_default(self._discover_btn)
            self._set_btn_green(self._run_btn)

        except Exception as e:
            self._log(f"Error discovering tiles: {e}")
            self._logger.exception("Tile discovery error")
            self._tiles = None
            self._run_btn.setEnabled(False)

    def _log_tile_summary(self, tiles):
        """Display a summary of discovered tiles."""
        xs = sorted(set(t.x_mm for t in tiles))
        ys = sorted(set(t.y_mm for t in tiles))
        all_ch = sorted(set(ch for t in tiles for ch in t.channels))
        all_illum = sorted(set(il for t in tiles for il in t.illumination_sides))

        self._log(f"Found {len(tiles)} tiles in ~{len(xs)}x{len(ys)} grid")
        self._log(
            f"  X range: {min(xs):.2f} \u2013 {max(xs):.2f} mm  "
            f"Y range: {min(ys):.2f} \u2013 {max(ys):.2f} mm"
        )
        self._log(f"  Channels: {all_ch}")
        self._log(f"  Illumination sides: {all_illum}")
        self._log(
            f"  Planes per tile: {tiles[0].n_planes} "
            f"(Z: {tiles[0].z_min_mm:.3f} \u2013 {tiles[0].z_max_mm:.3f} mm)"
        )
        self._log("")
        self._log("Ready to stitch. Click 'Run Stitching' to begin.")

    def _on_skip_reg_toggled(self, checked: bool):
        """Enable/disable registration controls based on skip state."""
        self._reg_binning_combo.setEnabled(not checked)
        self._reg_binning_label.setEnabled(not checked)

    def _update_memory_estimate(self):
        """Compute and display memory estimates for in-memory vs streaming modes."""
        if not self._tiles:
            self._memory_label.setText("")
            return

        try:
            from py2flamingo.stitching.pipeline import estimate_memory_usage

            config = self._build_config()
            channels = self._parse_channels()
            all_ch = sorted(set(ch for t in self._tiles for ch in t.channels))
            process_ch = channels if channels else all_ch

            est = estimate_memory_usage(self._tiles, process_ch, config)

            mode_hint = ""
            if est["auto_streaming"]:
                mode_hint = " \u2192 auto will use streaming"
            else:
                mode_hint = " \u2192 auto will use in-memory"

            self._memory_label.setText(
                f"In-memory: ~{est['in_memory_gb']:.0f} GB  |  "
                f"Streaming: ~{est['streaming_gb']:.1f} GB  |  "
                f"Output: ~{est['output_gb']:.0f} GB"
                f"{mode_hint}"
            )
        except Exception as e:
            self._logger.debug(f"Memory estimate failed: {e}")
            self._memory_label.setText("")

    # --- Run / Cancel ---

    def _build_config(self):
        """Build a StitchingConfig from current UI settings."""
        from py2flamingo.stitching.pipeline import StitchingConfig

        z_step = self._z_step_spin.value()
        return StitchingConfig(
            pixel_size_um=self._pixel_size_spin.value(),
            z_step_um=z_step if z_step > 0 else None,
            illumination_fusion=self._fusion_combo.currentData(),
            output_format=self._format_combo.currentData(),
            flat_field_correction=self._flat_field_cb.isChecked(),
            destripe=self._destripe_cb.isChecked(),
            destripe_fast=self._destripe_fast_cb.isChecked(),
            depth_attenuation=self._depth_atten_cb.isChecked(),
            depth_attenuation_mu=(
                self._depth_atten_mu_spin.value()
                if self._depth_atten_mu_spin.value() > 0
                else None
            ),
            downsample_factor=self._downsample_combo.currentData(),
            deconvolution_enabled=self._deconv_cb.isChecked(),
            content_based_fusion=self._content_fusion_cb.isChecked(),
            skip_registration=self._skip_reg_cb.isChecked(),
            registration_binning=self._reg_binning_combo.currentData(),
            package_ozx=self._ozx_cb.isChecked(),
            streaming_mode=self._streaming_combo.currentData(),
        )

    def _parse_channels(self) -> Optional[List[int]]:
        """Parse channels from the channels line edit. Returns None for 'all'."""
        text = self._channels_edit.text().strip()
        if not text:
            return None
        try:
            return [int(ch.strip()) for ch in text.split(",") if ch.strip()]
        except ValueError:
            return None

    def _on_run(self):
        """Start the stitching pipeline."""
        acq_dir = self._acq_dir_edit.text().strip()
        output_dir = self._output_dir_edit.text().strip()

        if not acq_dir or not Path(acq_dir).is_dir():
            QMessageBox.warning(self, "Invalid Input", "Invalid acquisition directory.")
            return

        if not output_dir:
            QMessageBox.warning(
                self, "Invalid Input", "Please specify an output directory."
            )
            return

        config = self._build_config()
        channels = self._parse_channels()

        # Pre-flight: warn if flat-field is requested but basicpy missing
        if config.flat_field_correction:
            from py2flamingo.stitching.flat_field import is_available

            if not is_available():
                reply = QMessageBox.question(
                    self,
                    "basicpy Not Installed",
                    "Flat-field correction requires basicpy which is not installed.\n\n"
                    "Install with:  pip install basicpy\n\n"
                    "Continue without flat-field correction?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply == QMessageBox.No:
                    return
                config.flat_field_correction = False

        self._log_text.clear()
        self._log("Starting stitching pipeline...")

        # Disable controls
        self._run_btn.setEnabled(False)
        self._discover_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)

        from py2flamingo.stitching.worker import StitchingWorker

        self._worker = StitchingWorker(
            config=config,
            acq_dir=Path(acq_dir),
            output_dir=Path(output_dir),
            channels=channels,
            tiles=self._tiles,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.log_message.connect(self._on_log_message)
        self._worker.completed.connect(self._on_completed)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_cancel(self):
        """Cancel the running pipeline."""
        if self._worker:
            self._worker.cancel()
            self._status_label.setText("Cancelling...")
            self._log("Cancellation requested...")

    def _on_progress(self, percentage: int, status: str):
        """Handle progress updates from worker."""
        self._progress_bar.setValue(percentage)
        self._status_label.setText(status)

    def _on_log_message(self, message: str):
        """Handle log messages from worker."""
        self._log_text.append(message)
        # Auto-scroll to bottom
        scrollbar = self._log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_completed(self, output_path: str):
        """Handle successful pipeline completion."""
        self._log(f"\nStitching complete! Output: {output_path}")

        msg = QMessageBox(self)
        msg.setWindowTitle("Stitching Complete")
        msg.setText(f"Stitched volume saved to:\n{output_path}")
        msg.setIcon(QMessageBox.Information)

        load_btn = msg.addButton("Load into Sample View", QMessageBox.AcceptRole)
        open_btn = msg.addButton("Open Folder", QMessageBox.ActionRole)
        msg.addButton(QMessageBox.Close)

        msg.exec_()
        clicked = msg.clickedButton()

        if clicked == load_btn:
            self.load_stitched_requested.emit(output_path)
        elif clicked == open_btn:
            import subprocess
            import sys

            folder = str(Path(output_path))
            if sys.platform == "win32":
                subprocess.Popen(["explorer", folder])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])

    def _on_error(self, error_msg: str):
        """Handle pipeline error."""
        self._log(f"\nERROR: {error_msg}")
        self._status_label.setText("Error")
        QMessageBox.critical(self, "Stitching Error", f"Pipeline failed:\n{error_msg}")

    def _on_worker_finished(self):
        """Handle worker thread completion (success, error, or cancel)."""
        self._discover_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._worker = None
        if self._tiles:
            # Tiles still valid — Run is the next action
            self._run_btn.setEnabled(True)
            self._set_btn_green(self._run_btn)
            self._set_btn_default(self._discover_btn)
        else:
            # No tiles — Discover is the next action
            self._run_btn.setEnabled(False)
            self._set_btn_green(self._discover_btn)
            self._set_btn_default(self._run_btn)

    # --- Format-dependent UI ---

    def _on_format_changed(self, _index=None):
        """Enable/disable .ozx checkbox based on selected output format."""
        fmt = self._format_combo.currentData()
        has_zarr = fmt in ("ome-zarr-sharded", "ome-zarr-v2", "both")
        self._ozx_cb.setEnabled(has_zarr)
        if not has_zarr:
            self._ozx_cb.setChecked(False)

    # --- Button styling helpers ---

    def _set_btn_green(self, btn):
        """Style a button with a green 'call to action' appearance."""
        btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; "
            "font-weight: bold; padding: 6px 14px; }"
            "QPushButton:hover { background-color: #45a049; }"
            "QPushButton:disabled { background-color: #888; color: #ccc; }"
        )

    def _set_btn_default(self, btn):
        """Reset a button to the default (platform) appearance."""
        btn.setStyleSheet("")

    # --- Logging helper ---

    def _log(self, message: str):
        """Append a message to the log area."""
        self._log_text.append(message)

    # --- Preprocessing environment ---

    def _update_preprocessing_availability(self):
        """Update flat-field/leonardo availability based on env status."""
        from py2flamingo.stitching.flat_field import is_available as _ff_available

        if _ff_available():
            self._flat_field_cb.setEnabled(True)
            self._flat_field_cb.setToolTip(
                "Estimate and correct illumination non-uniformity\n"
                "from tile data (BaSiC algorithm, no calibration needed).\n"
                "Improves tile intensity consistency and reduces seams."
            )
        else:
            self._flat_field_cb.setEnabled(False)
            self._flat_field_cb.setToolTip(
                "Flat-field correction requires basicpy.\n"
                "Click 'Setup Preprocessing...' to install it\n"
                "in an isolated environment."
            )

    def _on_setup_env(self):
        """Run the preprocessing environment setup script."""
        import sys

        from py2flamingo.stitching.isolated_service import (
            IsolatedPreprocessingService,
        )

        service = IsolatedPreprocessingService()
        if service.is_available():
            status_parts = []
            if service.has_basicpy():
                status_parts.append("basicpy")
            if service.has_leonardo():
                status_parts.append("leonardo-toolset")
            if status_parts:
                reply = QMessageBox.question(
                    self,
                    "Environment Exists",
                    f"Preprocessing environment already exists with: "
                    f"{', '.join(status_parts)}.\n\n"
                    f"Reinstall? (This will recreate the environment.)",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
                # Delete existing env so script recreates it
                import shutil

                env_path = service.env_path()
                self._log(f"Removing existing environment: {env_path}")
                try:
                    shutil.rmtree(env_path)
                except Exception as e:
                    self._log(f"ERROR: Could not remove environment: {e}")
                    return

        reply = QMessageBox.question(
            self,
            "Setup Preprocessing Environment",
            "This will download and install ~3 GB of packages:\n"
            "  - PyTorch (CPU)\n"
            "  - basicpy (flat-field correction)\n"
            "  - leonardo-toolset (dual-illumination fusion)\n\n"
            "This only needs to be done once. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        # Find the setup script
        script_dir = Path(__file__).resolve().parents[2] / "scripts"
        if sys.platform == "win32":
            script = script_dir / "create_preprocessing_env.bat"
        else:
            script = script_dir / "create_preprocessing_env.sh"

        if not script.is_file():
            # Try relative to package root
            from importlib import resources

            script_dir = Path(__file__).resolve().parents[3] / "scripts"
            if sys.platform == "win32":
                script = script_dir / "create_preprocessing_env.bat"
            else:
                script = script_dir / "create_preprocessing_env.sh"

        if not script.is_file():
            QMessageBox.warning(
                self,
                "Script Not Found",
                f"Could not find setup script.\n"
                f"Expected at: {script}\n\n"
                f"Run it manually from the scripts/ directory.",
            )
            return

        self._log(f"\n=== Setting up preprocessing environment ===")
        self._log(f"Script: {script}")
        self._setup_env_btn.setEnabled(False)
        self._setup_env_btn.setText("Setting up...")

        self._env_process = QProcess(self)
        self._env_process.setProcessChannelMode(QProcess.MergedChannels)
        self._env_process.readyReadStandardOutput.connect(self._on_env_process_output)
        self._env_process.finished.connect(self._on_env_process_finished)

        if sys.platform == "win32":
            self._env_process.start("cmd.exe", ["/c", str(script)])
        else:
            self._env_process.start("bash", [str(script)])

    def _on_env_process_output(self):
        """Read and display output from the setup process."""
        data = self._env_process.readAllStandardOutput()
        text = bytes(data).decode("utf-8", errors="replace").strip()
        if text:
            for line in text.splitlines():
                self._log(line)

    def _on_env_process_finished(self, exit_code, _exit_status):
        """Handle setup process completion."""
        self._setup_env_btn.setEnabled(True)
        self._setup_env_btn.setText("Setup Preprocessing...")

        if exit_code == 0:
            self._log("\n=== Preprocessing environment setup complete ===")
            # Clear cached availability checks
            from py2flamingo.stitching.isolated_service import (
                IsolatedPreprocessingService,
            )

            service = IsolatedPreprocessingService()
            service.clear_cache()

            # Refresh UI availability
            self._update_preprocessing_availability()
            self._log(
                f"  basicpy: {'available' if service.has_basicpy() else 'not found'}"
            )
            self._log(
                f"  leonardo: {'available' if service.has_leonardo() else 'not found'}"
            )
        else:
            self._log(f"\n=== Setup failed (exit code {exit_code}) ===")
            self._log("Check the log above for errors.")

    # --- Settings persistence ---

    def _save_settings(self):
        """Save dialog settings to QSettings."""
        s = QSettings()
        s.beginGroup(_SETTINGS_GROUP)
        s.setValue("acq_dir", self._acq_dir_edit.text())
        s.setValue("output_dir", self._output_dir_edit.text())
        s.setValue("pixel_size", self._pixel_size_spin.value())
        s.setValue("z_step", self._z_step_spin.value())
        s.setValue("downsample", self._downsample_combo.currentData())
        s.setValue("fusion", self._fusion_combo.currentData())
        s.setValue("flat_field", self._flat_field_cb.isChecked())
        s.setValue("destripe", self._destripe_cb.isChecked())
        s.setValue("destripe_fast", self._destripe_fast_cb.isChecked())
        s.setValue("depth_attenuation", self._depth_atten_cb.isChecked())
        s.setValue("depth_attenuation_mu", self._depth_atten_mu_spin.value())
        s.setValue("deconvolution", self._deconv_cb.isChecked())
        s.setValue("content_based_fusion", self._content_fusion_cb.isChecked())
        s.setValue("package_ozx", self._ozx_cb.isChecked())
        s.setValue("output_format", self._format_combo.currentData())
        s.setValue("channels", self._channels_edit.text())
        s.setValue("streaming_mode", self._streaming_combo.currentIndex())
        s.setValue("skip_registration", self._skip_reg_cb.isChecked())
        s.setValue("reg_binning", self._reg_binning_combo.currentIndex())
        s.endGroup()

    def _restore_settings(self):
        """Restore dialog settings from QSettings."""
        s = QSettings()
        s.beginGroup(_SETTINGS_GROUP)

        acq_dir = s.value("acq_dir", "", type=str)
        if acq_dir:
            self._acq_dir_edit.setText(acq_dir)

        output_dir = s.value("output_dir", "", type=str)
        if output_dir:
            self._output_dir_edit.setText(output_dir)

        pixel_size = s.value("pixel_size", 0.406, type=float)
        self._pixel_size_spin.setValue(pixel_size)

        z_step = s.value("z_step", 0.0, type=float)
        self._z_step_spin.setValue(z_step)

        downsample = s.value("downsample", 0, type=int)
        if downsample:
            idx = self._downsample_combo.findData(downsample)
            if idx >= 0:
                self._downsample_combo.setCurrentIndex(idx)

        fusion = s.value("fusion", "", type=str)
        if fusion:
            idx = self._fusion_combo.findData(fusion)
            if idx >= 0:
                self._fusion_combo.setCurrentIndex(idx)

        flat_field = s.value("flat_field", False, type=bool)
        self._flat_field_cb.setChecked(flat_field)

        destripe = s.value("destripe", False, type=bool)
        self._destripe_cb.setChecked(destripe)

        destripe_fast = s.value("destripe_fast", False, type=bool)
        self._destripe_fast_cb.setChecked(destripe_fast)

        depth_atten = s.value("depth_attenuation", False, type=bool)
        self._depth_atten_cb.setChecked(depth_atten)

        depth_atten_mu = s.value("depth_attenuation_mu", 0.0, type=float)
        self._depth_atten_mu_spin.setValue(depth_atten_mu)

        deconv = s.value("deconvolution", False, type=bool)
        self._deconv_cb.setChecked(deconv)

        content_fusion = s.value("content_based_fusion", False, type=bool)
        self._content_fusion_cb.setChecked(content_fusion)

        ozx = s.value("package_ozx", False, type=bool)
        self._ozx_cb.setChecked(ozx)

        output_format = s.value("output_format", "", type=str)
        if output_format:
            idx = self._format_combo.findData(output_format)
            if idx >= 0:
                self._format_combo.setCurrentIndex(idx)

        channels = s.value("channels", "", type=str)
        if channels:
            self._channels_edit.setText(channels)

        streaming_idx = s.value("streaming_mode", 0, type=int)
        if 0 <= streaming_idx < self._streaming_combo.count():
            self._streaming_combo.setCurrentIndex(streaming_idx)

        skip_reg = s.value("skip_registration", False, type=bool)
        self._skip_reg_cb.setChecked(skip_reg)

        reg_binning_idx = s.value("reg_binning", 1, type=int)  # default = index 1
        if 0 <= reg_binning_idx < self._reg_binning_combo.count():
            self._reg_binning_combo.setCurrentIndex(reg_binning_idx)

        s.endGroup()

    def closeEvent(self, event):
        """Save settings and cancel worker on close."""
        self._save_settings()
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
        super().closeEvent(event)

    def hideEvent(self, event):
        """Save settings on hide."""
        self._save_settings()
        super().hideEvent(event)


# QSettings keys for native dialog (independent persistence)
_NATIVE_SETTINGS_GROUP = "NativeStitchingDialog"


class NativeStitchingDialog(StitchingDialog):
    """Stitching dialog for C++ server native flat-layout acquisitions.

    Overrides tile discovery to use discover_flat_tiles() which scans for
    .raw files with integer tile indices (X000_Y000) in a flat directory,
    rather than the subfolder-per-tile layout.
    """

    # Flat layout: parent only (1 level up).
    _acq_dir_restore_levels_up = 1

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setWindowTitle("Tile Stitching (Single Workflow)")

    def _on_discover(self):
        """Discover tiles using flat-layout scanner."""
        acq_dir = self._acq_dir_edit.text().strip()
        if not acq_dir:
            QMessageBox.warning(
                self, "No Directory", "Please select an acquisition directory."
            )
            return

        acq_path = Path(acq_dir)
        if not acq_path.is_dir():
            QMessageBox.warning(
                self,
                "Invalid Directory",
                f"Directory does not exist:\n{acq_dir}",
            )
            return

        self._log_text.clear()
        self._log(f"Discovering flat-layout tiles in: {acq_dir}")

        try:
            from py2flamingo.stitching.pipeline import (
                _read_plane_spacing,
                discover_flat_tiles,
            )

            tiles = discover_flat_tiles(acq_path)

            if not tiles:
                self._log("No flat-layout tiles found.")
                self._tiles = None
                self._run_btn.setEnabled(False)
                return

            self._tiles = tiles

            # Auto-set Z step from root Workflow.txt if currently "Auto"
            if self._z_step_spin.value() == 0.0:
                for wf_candidate in [
                    acq_path / "Workflow.txt",
                    tiles[0].folder / "Workflow.txt",
                ]:
                    if wf_candidate.exists():
                        spacing = _read_plane_spacing(wf_candidate)
                        if spacing:
                            self._z_step_spin.setValue(spacing)
                            self._log(f"Auto-detected Z step: {spacing} \u00b5m")
                        break

            self._log_tile_summary(tiles)
            self._run_btn.setEnabled(True)
            self._set_btn_default(self._discover_btn)
            self._set_btn_green(self._run_btn)

        except Exception as e:
            self._log(f"Error discovering tiles: {e}")
            self._logger.exception("Flat tile discovery error")
            self._tiles = None
            self._run_btn.setEnabled(False)

    def _save_settings(self):
        """Save dialog settings to QSettings (independent group)."""
        s = QSettings()
        s.beginGroup(_NATIVE_SETTINGS_GROUP)
        s.setValue("acq_dir", self._acq_dir_edit.text())
        s.setValue("output_dir", self._output_dir_edit.text())
        s.setValue("pixel_size", self._pixel_size_spin.value())
        s.setValue("z_step", self._z_step_spin.value())
        s.setValue("downsample", self._downsample_combo.currentData())
        s.setValue("fusion", self._fusion_combo.currentData())
        s.setValue("flat_field", self._flat_field_cb.isChecked())
        s.setValue("destripe", self._destripe_cb.isChecked())
        s.setValue("destripe_fast", self._destripe_fast_cb.isChecked())
        s.setValue("depth_attenuation", self._depth_atten_cb.isChecked())
        s.setValue("depth_attenuation_mu", self._depth_atten_mu_spin.value())
        s.setValue("deconvolution", self._deconv_cb.isChecked())
        s.setValue("content_based_fusion", self._content_fusion_cb.isChecked())
        s.setValue("package_ozx", self._ozx_cb.isChecked())
        s.setValue("output_format", self._format_combo.currentData())
        s.setValue("channels", self._channels_edit.text())
        s.endGroup()

    def _restore_settings(self):
        """Restore dialog settings from QSettings (independent group)."""
        s = QSettings()
        s.beginGroup(_NATIVE_SETTINGS_GROUP)

        acq_dir = s.value("acq_dir", "", type=str)
        if acq_dir:
            self._acq_dir_edit.setText(acq_dir)

        output_dir = s.value("output_dir", "", type=str)
        if output_dir:
            self._output_dir_edit.setText(output_dir)

        pixel_size = s.value("pixel_size", 0.406, type=float)
        self._pixel_size_spin.setValue(pixel_size)

        z_step = s.value("z_step", 0.0, type=float)
        self._z_step_spin.setValue(z_step)

        downsample = s.value("downsample", 0, type=int)
        if downsample:
            idx = self._downsample_combo.findData(downsample)
            if idx >= 0:
                self._downsample_combo.setCurrentIndex(idx)

        fusion = s.value("fusion", "", type=str)
        if fusion:
            idx = self._fusion_combo.findData(fusion)
            if idx >= 0:
                self._fusion_combo.setCurrentIndex(idx)

        flat_field = s.value("flat_field", False, type=bool)
        self._flat_field_cb.setChecked(flat_field)

        destripe = s.value("destripe", False, type=bool)
        self._destripe_cb.setChecked(destripe)

        destripe_fast = s.value("destripe_fast", False, type=bool)
        self._destripe_fast_cb.setChecked(destripe_fast)

        depth_atten = s.value("depth_attenuation", False, type=bool)
        self._depth_atten_cb.setChecked(depth_atten)

        depth_atten_mu = s.value("depth_attenuation_mu", 0.0, type=float)
        self._depth_atten_mu_spin.setValue(depth_atten_mu)

        deconv = s.value("deconvolution", False, type=bool)
        self._deconv_cb.setChecked(deconv)

        content_fusion = s.value("content_based_fusion", False, type=bool)
        self._content_fusion_cb.setChecked(content_fusion)

        ozx = s.value("package_ozx", False, type=bool)
        self._ozx_cb.setChecked(ozx)

        output_format = s.value("output_format", "", type=str)
        if output_format:
            idx = self._format_combo.findData(output_format)
            if idx >= 0:
                self._format_combo.setCurrentIndex(idx)

        channels = s.value("channels", "", type=str)
        if channels:
            self._channels_edit.setText(channels)

        s.endGroup()
