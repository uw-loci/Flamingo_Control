"""Tile Stitching Dialog.

Non-modal dialog for stitching raw acquisition tile data into a single volume.
Operates on saved acquisition data on disk — no microscope connection required.
"""

import logging
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import QProcess, QSettings, Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
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

        # Batch queue state
        self._queue = []  # List of dicts: {path, status, tiles, error, output_path}
        self._queue_index = -1  # Index of currently processing item
        self._batch_running = False
        self._batch_config = None
        self._batch_channels = None
        self._batch_results = []  # List of (path, success, error_msg)

        self.setWindowTitle("Tile Stitching")
        self.setMinimumWidth(650)
        self.setMinimumHeight(700)
        self.resize(720, 750)  # Default size before geometry restore
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self._setup_ui()
        self._restore_settings()

    def _setup_ui(self):
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # --- Batch queue ---
        queue_group = QGroupBox("Acquisition Queue")
        queue_layout = QVBoxLayout()
        queue_layout.setSpacing(4)

        self._queue_table = QTableWidget(0, 2)
        self._queue_table.setHorizontalHeaderLabels(["Status", "Directory"])
        self._queue_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self._queue_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self._queue_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._queue_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._queue_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._queue_table.verticalHeader().setVisible(False)
        self._queue_table.setMaximumHeight(140)
        queue_layout.addWidget(self._queue_table)

        queue_btn_layout = QHBoxLayout()
        self._add_btn = QPushButton("Add...")
        self._add_btn.setToolTip("Add an acquisition directory to the queue")
        self._add_btn.clicked.connect(self._add_to_queue)
        queue_btn_layout.addWidget(self._add_btn)

        self._add_folder_btn = QPushButton("Add All in Folder...")
        self._add_folder_btn.setToolTip(
            "Select a parent folder and add all acquisition\n"
            "subdirectories to the queue"
        )
        self._add_folder_btn.clicked.connect(self._add_folder_to_queue)
        queue_btn_layout.addWidget(self._add_folder_btn)

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setToolTip("Remove selected directories from the queue")
        self._remove_btn.clicked.connect(self._remove_from_queue)
        queue_btn_layout.addWidget(self._remove_btn)

        queue_btn_layout.addStretch()
        queue_layout.addLayout(queue_btn_layout)
        queue_group.setLayout(queue_layout)
        layout.addWidget(queue_group)

        # --- Output directory ---
        out_layout = QHBoxLayout()
        out_layout.addWidget(QLabel("Output Directory:"))
        self._output_dir_edit = QLineEdit()
        self._output_dir_edit.setPlaceholderText(
            "Shared output folder (each acquisition gets a subfolder)..."
        )
        out_layout.addWidget(self._output_dir_edit)
        out_browse_btn = QPushButton("Browse...")
        out_browse_btn.clicked.connect(self._browse_output_dir)
        out_layout.addWidget(out_browse_btn)
        layout.addLayout(out_layout)

        # --- Settings group ---
        settings_group = QGroupBox("Settings")
        settings_layout = QGridLayout()
        settings_layout.setSpacing(6)

        # Load hardware-derived pixel size default
        try:
            from py2flamingo.configs.config_loader import get_hardware_config

            _hw = get_hardware_config()
            self._default_pixel_um = round(_hw.effective_pixel_size_um, 4)
        except Exception:
            self._default_pixel_um = 0.406

        # Row 0: Pixel size + Z step
        settings_layout.addWidget(QLabel("Pixel size (\u00b5m):"), 0, 0)
        self._pixel_size_spin = QDoubleSpinBox()
        self._pixel_size_spin.setRange(0.01, 100.0)
        self._pixel_size_spin.setDecimals(3)
        self._pixel_size_spin.setValue(self._default_pixel_um)
        self._pixel_size_spin.setSingleStep(0.001)
        settings_layout.addWidget(self._pixel_size_spin, 0, 1)

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

        # Row 1: Downsample XY/Z + Illumination fusion
        settings_layout.addWidget(QLabel("Downsample:"), 1, 0)
        ds_layout = QHBoxLayout()
        ds_layout.setSpacing(4)
        ds_layout.addWidget(QLabel("XY"))
        self._downsample_xy_combo = QComboBox()
        for label, value in [("1x", 1), ("2x", 2), ("4x", 4), ("8x", 8)]:
            self._downsample_xy_combo.addItem(label, value)
        self._downsample_xy_combo.setToolTip(
            "XY downsample factor.\n"
            "Reduces tile width/height before registration.\n"
            "2x: 2048\u21921024, 4x: 2048\u2192512"
        )
        ds_layout.addWidget(self._downsample_xy_combo)
        ds_layout.addWidget(QLabel("Z"))
        self._downsample_z_combo = QComboBox()
        for label, value in [("1x", 1), ("2x", 2), ("4x", 4)]:
            self._downsample_z_combo.addItem(label, value)
        self._downsample_z_combo.setToolTip(
            "Z downsample factor.\n"
            "Z pixel size is often already much coarser than XY,\n"
            "so 1x (no Z downsample) is common.\n\n"
            "Example: XY=0.406\u00b5m, Z=2.5\u00b5m \u2192 Z is 6x coarser.\n"
            "2x XY + 1x Z keeps Z resolution while shrinking XY."
        )
        ds_layout.addWidget(self._downsample_z_combo)
        settings_layout.addLayout(ds_layout, 1, 1)

        settings_layout.addWidget(QLabel("Illum. fusion:"), 1, 2)
        self._fusion_combo = QComboBox()
        for label, value in [
            ("Max", "max"),
            ("Mean", "mean"),
            ("Leonardo FUSE", "leonardo"),
        ]:
            self._fusion_combo.addItem(label, value)
        settings_layout.addWidget(self._fusion_combo, 1, 3)

        # Row 2: Output format + Compression
        settings_layout.addWidget(QLabel("Output format:"), 2, 0)
        self._format_combo = QComboBox()
        self._format_combo.addItem("OME-Zarr (Fiji compatible)", "ome-zarr-v2")
        self._format_combo.addItem("OME-Zarr Sharded", "ome-zarr-sharded")
        self._format_combo.addItem("OME-TIFF (single file)", "ome-tiff")
        self._format_combo.addItem("Imaris (.ims)", "imaris")
        self._format_combo.addItem("Both (Sharded + TIFF)", "both")
        # Disable Imaris option if PyImarisWriter not available
        try:
            from py2flamingo.stitching.writers import imaris_writer

            if not imaris_writer.is_available():
                idx = self._format_combo.findData("imaris")
                if idx >= 0:
                    item = self._format_combo.model().item(idx)
                    item.setEnabled(False)
                    item.setToolTip(
                        "Imaris (.ims) unavailable:\n"
                        + imaris_writer.unavailable_reason()
                    )
        except Exception:
            pass

        self._format_combo.setToolTip(
            "OME-Zarr (Fiji compatible): opens in Fiji, QuPath, BigDataViewer, napari\n"
            "OME-Zarr Sharded: fewest files, napari only (Fiji cannot open)\n"
            "OME-TIFF: single file, universal viewer support\n"
            "Imaris (.ims): direct writer \u2014 opens correctly in Imaris (Windows only)\n"
            "Both: write Zarr Sharded + TIFF"
        )
        self._format_combo.currentIndexChanged.connect(self._on_format_changed)
        settings_layout.addWidget(self._format_combo, 2, 1)

        # Format help label
        format_help = QLabel("?")
        format_help.setStyleSheet(
            "QLabel { color: #1976D2; font-weight: bold; font-size: 11px; "
            "border: 1px solid #1976D2; border-radius: 8px; "
            "padding: 0px 4px; "
            "qproperty-alignment: AlignCenter; }"
        )
        format_help.setToolTip(
            "<b>Output Format Guide</b><br><br>"
            "<b>OME-Zarr (Fiji compatible)</b> &mdash; Zarr v2 + OME-NGFF v0.4<br>"
            "Opens in <b>Fiji</b> (N5 plugin), <b>QuPath</b>, <b>BigDataViewer</b>, "
            "<b>napari</b>, and most bio-imaging tools.<br><br>"
            "<b>OME-Zarr Sharded</b> &mdash; Zarr v3 + OME-NGFF v0.5<br>"
            "Fewest files via sharding. Only readable by "
            "<b>napari</b> and zarr-python 3.x.<br><br>"
            "<b>OME-TIFF</b> &mdash; Single file per channel<br>"
            "Universally readable. Best for sharing and archiving.<br><br>"
            "<b>Imaris (.ims)</b> &mdash; Direct writer via PyImarisWriter (Apache 2.0)<br>"
            "Opens correctly in <b>Imaris</b> with auto-generated pyramids. "
            "Avoids ImarisFileConverter's SubIFD-pyramid bug that otherwise "
            "collapses all data into Z=0. Block-streamed write for TB-scale "
            "datasets. <b>Windows only</b> (<code>pip install PyImarisWriter</code>).<br><br>"
            "<b>Alternative for Imaris:</b> Export OME-TIFF with pyramids OFF, "
            "then run through ImarisFileConverter."
        )
        format_help.setFixedWidth(20)
        settings_layout.addWidget(format_help, 2, 2)

        settings_layout.addWidget(QLabel("Compression:"), 2, 3)
        self._compression_combo = QComboBox()
        self._compression_combo.setToolTip(
            "Compression codec for the output file.\n\n"
            "Options depend on the output format:\n"
            "  Zarr: zstd (recommended), lz4 (fastest), blosc, none\n"
            "  TIFF: zlib (universal), lzw (fast read), zstd (best ratio), none"
        )
        settings_layout.addWidget(self._compression_combo, 2, 4)
        self._update_compression_options()

        # Row 3: Channels + Memory mode
        settings_layout.addWidget(QLabel("Channels:"), 3, 0)
        self._channels_edit = QLineEdit()
        self._channels_edit.setPlaceholderText("All (or e.g. 0,1)")
        self._channels_edit.setToolTip(
            "Leave empty for all channels, or comma-separated list (e.g. 0,1)"
        )
        settings_layout.addWidget(self._channels_edit, 3, 1)

        settings_layout.addWidget(QLabel("Memory mode:"), 3, 3)
        self._streaming_combo = QComboBox()
        self._streaming_combo.addItem("Auto", None)
        self._streaming_combo.addItem("In-memory (fast)", False)
        self._streaming_combo.addItem("Streaming (low memory)", True)
        self._streaming_combo.setToolTip(
            "Auto: automatically chooses based on estimated data size and RAM.\n"
            "In-memory: fast, requires RAM > ~2.5x output size.\n"
            "Streaming: low RAM, required for TB-scale data."
        )
        self._streaming_combo.currentIndexChanged.connect(self._update_memory_indicator)
        settings_layout.addWidget(self._streaming_combo, 3, 4)

        # Memory safety indicator
        self._memory_indicator = QLabel("")
        self._memory_indicator.setFixedWidth(40)
        self._memory_indicator.setAlignment(Qt.AlignCenter)
        self._last_mem_estimate = None
        settings_layout.addWidget(self._memory_indicator, 3, 2)

        # Row 4: Memory estimate
        self._memory_label = QLabel("")
        self._memory_label.setStyleSheet(
            "color: #888; font-style: italic; font-size: 11px;"
        )
        settings_layout.addWidget(self._memory_label, 4, 0, 1, 5)

        settings_group.setLayout(settings_layout)
        self._settings_group = settings_group
        layout.addWidget(settings_group)

        # --- Collapsible processing options ---
        self._proc_toggle = QPushButton("\u25b6 Processing Options")
        self._proc_toggle.setCheckable(True)
        self._proc_toggle.setChecked(False)
        self._proc_toggle.setStyleSheet(
            "QPushButton { text-align: left; border: none; "
            "padding: 4px 2px; font-weight: bold; color: #555; }"
            "QPushButton:hover { color: #333; }"
        )
        self._proc_toggle.toggled.connect(self._on_proc_toggle)
        layout.addWidget(self._proc_toggle)

        self._proc_widget = QGroupBox()
        self._proc_widget.setStyleSheet(
            "QGroupBox { border: 1px solid #ccc; border-radius: 4px; "
            "margin-top: 0px; padding-top: 6px; }"
        )
        proc_layout = QGridLayout()
        proc_layout.setSpacing(6)

        # Proc Row 0: Destripe + Content-based blending
        self._destripe_cb = QCheckBox("Destripe (PyStripe) \u2731")
        self._destripe_cb.setToolTip(
            "\u2731 Processes every Z-plane at full resolution\n"
            "before downsampling.\n\n"
            "Removes horizontal stripe artifacts from light-sheet data.\n"
            "Uses multiple CPU cores automatically."
        )
        proc_layout.addWidget(self._destripe_cb, 0, 0)

        self._destripe_fast_cb = QCheckBox("Fast")
        self._destripe_fast_cb.setToolTip(
            "Destripe after downsampling instead of before.\n"
            "Much faster but slightly lower quality.\n\n"
            "Only effective when downsample factor > 1."
        )
        self._destripe_fast_cb.setEnabled(False)
        self._destripe_cb.toggled.connect(self._destripe_fast_cb.setEnabled)
        proc_layout.addWidget(self._destripe_fast_cb, 0, 1)

        self._content_fusion_cb = QCheckBox("Content-based blending \u2731")
        self._content_fusion_cb.setToolTip(
            "\u2731 Weights tile overlaps by local sharpness\n"
            "(Preibisch local-variance, inspired by BigStitcher).\n\n"
            "Improves fusion quality in overlap regions."
        )
        proc_layout.addWidget(self._content_fusion_cb, 0, 2, 1, 2)

        # Proc Row 1: Deconvolution + Flat-field
        self._deconv_cb = QCheckBox("Deconvolution \u2731")
        self._deconv_cb.setToolTip(
            "\u2731 GPU Richardson-Lucy deconvolution per tile.\n"
            "Requires pycudadecon or RedLionfish.\n\n"
            "Significantly improves resolution."
        )
        proc_layout.addWidget(self._deconv_cb, 1, 0)

        self._flat_field_cb = QCheckBox("Flat-field correction")
        self._update_preprocessing_availability()
        proc_layout.addWidget(self._flat_field_cb, 1, 1)

        self._ozx_cb = QCheckBox("Package as .ozx")
        self._ozx_cb.setToolTip(
            "Create a single .ozx ZIP file from the OME-Zarr output\n"
            "for easy sharing/copying"
        )
        proc_layout.addWidget(self._ozx_cb, 1, 2)

        self._tiff_pyramids_cb = QCheckBox("TIFF pyramids")
        self._tiff_pyramids_cb.setChecked(True)
        self._tiff_pyramids_cb.setToolTip(
            "Write multi-resolution pyramid SubIFDs in the OME-TIFF output.\n\n"
            "UNCHECK for ImarisFileConverter compatibility.\n"
            "ImarisFileConverter may misread SubIFD pyramids as extra Z\n"
            "planes, collapsing all real data into the first Z layer.\n\n"
            "Pyramids help napari and QuPath viewing but are not required\n"
            "for Fiji or Imaris (.ims has its own pyramid format)."
        )
        proc_layout.addWidget(self._tiff_pyramids_cb, 1, 3)

        # Proc Row 2: Registration
        self._skip_reg_cb = QCheckBox("Skip registration")
        self._skip_reg_cb.setToolTip(
            "Use stage positions only \u2014 skip phase-correlation registration.\n\n"
            "CHECK this when:\n"
            "  \u2022 Tiles have no overlap\n"
            "  \u2022 Stage positions are precise\n\n"
            "UNCHECK (default) when:\n"
            "  \u2022 Tiles overlap and you need sub-pixel alignment"
        )
        self._skip_reg_cb.toggled.connect(self._on_skip_reg_toggled)
        proc_layout.addWidget(self._skip_reg_cb, 2, 0)

        self._reg_binning_label = QLabel("Reg. binning:")
        proc_layout.addWidget(self._reg_binning_label, 2, 1)
        self._reg_binning_combo = QComboBox()
        self._reg_binning_combo.addItem("Fine (z1 y2 x2)", {"z": 1, "y": 2, "x": 2})
        self._reg_binning_combo.addItem("Default (z2 y4 x4)", {"z": 2, "y": 4, "x": 4})
        self._reg_binning_combo.addItem("Fast (z4 y8 x8)", {"z": 4, "y": 8, "x": 8})
        self._reg_binning_combo.setCurrentIndex(1)
        self._reg_binning_combo.setToolTip(
            "How much to downsample tiles for phase-correlation registration."
        )
        proc_layout.addWidget(self._reg_binning_combo, 2, 2, 1, 2)

        # Proc Row 3: Depth attenuation
        self._depth_atten_cb = QCheckBox("Depth attenuation")
        self._depth_atten_cb.setToolTip(
            "Correct exponential Z-intensity falloff\n"
            "(Beer-Lambert scattering/absorption compensation).\n"
            "Auto-fits decay coefficient from data unless overridden."
        )
        proc_layout.addWidget(self._depth_atten_cb, 3, 0)

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
        proc_layout.addWidget(self._depth_atten_mu_spin, 3, 1)

        # Proc Row 4: Legend
        legend = QLabel("\u2731 = significantly increases processing time")
        legend.setStyleSheet("color: #FF8C00; font-style: italic; font-size: 11px;")
        proc_layout.addWidget(legend, 4, 0, 1, 4)

        self._proc_widget.setLayout(proc_layout)
        self._proc_widget.setVisible(False)
        layout.addWidget(self._proc_widget)

        # --- Action buttons ---
        btn_layout = QHBoxLayout()

        self._discover_btn = QPushButton("Discover Tiles")
        self._discover_btn.setToolTip(
            "Scan all queued directories for tile data\n"
            "(optional — Run will auto-discover if needed)"
        )
        self._discover_btn.clicked.connect(self._on_discover)
        btn_layout.addWidget(self._discover_btn)

        self._run_btn = QPushButton("Run All")
        self._run_btn.setToolTip(
            "Process all pending directories in the queue sequentially"
        )
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

    # --- Queue management ---

    def _add_to_queue(self):
        """Add an acquisition directory to the batch queue."""
        start = self._queue_browse_start()
        folder = QFileDialog.getExistingDirectory(
            self, "Select Acquisition Directory", start
        )
        if folder:
            self._add_path_to_queue(Path(folder))

    def _add_folder_to_queue(self):
        """Add all acquisition subdirectories from a parent folder."""
        start = self._queue_browse_start()
        parent = QFileDialog.getExistingDirectory(
            self, "Select Parent Folder (contains acquisition folders)", start
        )
        if not parent:
            return

        parent_path = Path(parent)
        added = 0
        for child in sorted(parent_path.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                if self._looks_like_acquisition(child):
                    self._add_path_to_queue(child)
                    added += 1

        if added == 0:
            self._log(f"No acquisition directories found in: {parent}")
        else:
            self._log(f"Added {added} directories from: {parent}")

    def _queue_browse_start(self) -> str:
        """Determine the starting path for the file browser."""
        if self._queue:
            last = self._queue[-1]["path"]
            start = last.parent
            for _ in range(self._acq_dir_restore_levels_up - 1):
                if start.parent != start:
                    start = start.parent
            return str(start)
        output = self._output_dir_edit.text()
        if output and Path(output).parent.exists():
            return str(Path(output).parent)
        return str(Path.home())

    def _looks_like_acquisition(self, path: Path) -> bool:
        """Check if a directory looks like an acquisition folder.

        Subclasses can override for different layout detection.
        """
        if (path / "Workflow.txt").exists():
            return True
        # Check first few children for tile indicators
        checked = 0
        for child in path.iterdir():
            if child.is_dir() and (child / "Workflow.txt").exists():
                return True
            if child.suffix == ".raw":
                return True
            checked += 1
            if checked >= 5:
                break
        return False

    def _add_path_to_queue(self, path: Path):
        """Add a single path to the queue (with dedup)."""
        path = Path(path)
        for item in self._queue:
            if item["path"] == path:
                return  # Already in queue

        self._queue.append(
            {
                "path": path,
                "status": "pending",
                "tiles": None,
                "error": None,
                "output_path": None,
            }
        )
        self._update_queue_table()

        # Auto-set output directory from first item
        if len(self._queue) == 1 and not self._output_dir_edit.text().strip():
            self._output_dir_edit.setText(str(path.parent))

        self._update_action_buttons()

    def _remove_from_queue(self):
        """Remove selected items from the queue."""
        rows = sorted(
            set(idx.row() for idx in self._queue_table.selectedIndexes()),
            reverse=True,
        )
        for row in rows:
            if 0 <= row < len(self._queue):
                # Don't remove the currently running item
                if self._batch_running and row == self._queue_index:
                    continue
                del self._queue[row]
                if self._batch_running and row < self._queue_index:
                    self._queue_index -= 1
        self._update_queue_table()
        self._update_action_buttons()

    def _update_queue_table(self):
        """Refresh the queue table from self._queue."""
        self._queue_table.setRowCount(len(self._queue))
        status_styles = {
            "pending": ("\u25cb Pending", "#888888"),
            "discovering": ("\u25c9 Discovering", "#1976D2"),
            "stitching": ("\u25b6 Stitching", "#1976D2"),
            "done": ("\u2713 Done", "#388E3C"),
            "error": ("\u2717 Error", "#D32F2F"),
            "cancelled": ("\u2014 Cancelled", "#888888"),
        }
        for i, item in enumerate(self._queue):
            text, color = status_styles.get(item["status"], (item["status"], "#888888"))
            status_item = QTableWidgetItem(text)
            status_item.setForeground(QColor(color))
            if item["status"] == "stitching":
                font = status_item.font()
                font.setBold(True)
                status_item.setFont(font)
            self._queue_table.setItem(i, 0, status_item)

            path_item = QTableWidgetItem(str(item["path"]))
            if item.get("error"):
                path_item.setToolTip(f"Error: {item['error']}")
            self._queue_table.setItem(i, 1, path_item)

    def _update_action_buttons(self):
        """Update Discover/Run button states based on queue."""
        has_pending = any(item["status"] == "pending" for item in self._queue)
        if self._batch_running:
            self._discover_btn.setEnabled(False)
            self._run_btn.setEnabled(False)
            self._cancel_btn.setEnabled(True)
        elif has_pending:
            self._discover_btn.setEnabled(True)
            self._run_btn.setEnabled(True)
            self._set_btn_green(self._run_btn)
            self._set_btn_default(self._discover_btn)
        elif self._queue:
            # Queue exists but nothing pending (all done/error)
            self._discover_btn.setEnabled(False)
            self._run_btn.setEnabled(False)
            self._set_btn_default(self._discover_btn)
            self._set_btn_default(self._run_btn)
        else:
            # Empty queue
            self._discover_btn.setEnabled(False)
            self._run_btn.setEnabled(False)
            self._set_btn_default(self._discover_btn)
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
        """Discover tiles for all pending items in the queue."""
        pending = [item for item in self._queue if item["status"] == "pending"]
        if not pending:
            QMessageBox.warning(
                self,
                "Nothing to Discover",
                "No pending directories in the queue.\n"
                "Add directories with 'Add...' first.",
            )
            return

        self._log_text.clear()
        self._log(f"Discovering tiles for {len(pending)} directories...\n")

        for item in pending:
            self._log(f"Scanning: {item['path'].name}")
            try:
                tiles = self._discover_tiles_for_path(item["path"])
                if tiles:
                    item["tiles"] = tiles
                    self._log(f"  Found {len(tiles)} tiles")
                else:
                    self._log("  No tiles found")
                    item["status"] = "error"
                    item["error"] = "No tiles found"
            except Exception as e:
                self._log(f"  Error: {e}")
                self._logger.exception("Tile discovery error")
                item["status"] = "error"
                item["error"] = str(e)

        self._update_queue_table()
        total = sum(len(it["tiles"]) for it in self._queue if it["tiles"])
        ok = sum(1 for it in pending if it["tiles"])
        self._log(f"\nDiscovered {total} tiles across {ok}/{len(pending)} directories")
        self._update_action_buttons()

        # Show memory estimate for first discovered set (representative)
        first_tiles = next((it["tiles"] for it in self._queue if it["tiles"]), None)
        if first_tiles:
            self._update_memory_estimate(first_tiles)

    def _discover_tiles_for_path(self, acq_path: Path):
        """Discover tiles in an acquisition directory.

        Override in subclasses for different tile layouts.
        """
        from py2flamingo.stitching.pipeline import discover_tiles

        return discover_tiles(acq_path)

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

    def _on_proc_toggle(self, checked: bool):
        """Show/hide the processing options panel."""
        self._proc_widget.setVisible(checked)
        self._proc_toggle.setText(
            ("\u25bc " if checked else "\u25b6 ") + "Processing Options"
        )

    def _on_skip_reg_toggled(self, checked: bool):
        """Enable/disable registration controls based on skip state."""
        self._reg_binning_combo.setEnabled(not checked)
        self._reg_binning_label.setEnabled(not checked)

    def _update_memory_estimate(self, tiles=None):
        """Compute and display memory estimates for in-memory vs streaming modes."""
        if tiles is None:
            # Try to find tiles from first queue item with discovered tiles
            tiles = next((it["tiles"] for it in self._queue if it["tiles"]), None)
        if not tiles:
            self._memory_label.setText("")
            self._last_mem_estimate = None
            self._update_memory_indicator()
            return

        try:
            from py2flamingo.stitching.pipeline import estimate_memory_usage

            config = self._build_config()
            channels = self._parse_channels()
            all_ch = sorted(set(ch for t in tiles for ch in t.channels))
            process_ch = channels if channels else all_ch

            est = estimate_memory_usage(tiles, process_ch, config)
            self._last_mem_estimate = est

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

            # Update the indicator badge
            self._update_memory_indicator()

            # Also log to the log area for visibility
            try:
                import psutil

                sys_ram = psutil.virtual_memory().total / (1024**3)
            except ImportError:
                sys_ram = 0
            self._log(
                f"Memory estimate (system RAM: {sys_ram:.0f} GB):\n"
                f"  In-memory mode: ~{est['in_memory_gb']:.0f} GB peak\n"
                f"  Streaming mode: ~{est['streaming_gb']:.1f} GB peak\n"
                f"  Output size:    ~{est['output_gb']:.0f} GB\n"
                f"  Recommendation: {'Streaming (low memory)' if est['auto_streaming'] else 'In-memory (fast)'}"
            )
        except Exception as e:
            self._logger.debug(f"Memory estimate failed: {e}")
            self._memory_label.setText("")
            self._last_mem_estimate = None
            self._update_memory_indicator()

    def _update_memory_indicator(self, _index=None):
        """Update the memory safety indicator next to the Memory Mode combo.

        Shows a colored badge:
          Green "OK"     — selected mode fits comfortably in RAM
          Orange "Warn"  — selected mode is tight (>80% RAM) or auto would differ
          Red "OOM!"     — selected mode will likely exceed RAM
          Empty          — no tile data yet (nothing to estimate)
        """
        est = self._last_mem_estimate
        if est is None:
            self._memory_indicator.setText("")
            self._memory_indicator.setToolTip("")
            return

        # Get system RAM
        try:
            import psutil

            sys_ram = psutil.virtual_memory().total / (1024**3)
        except ImportError:
            sys_ram = 192.0

        # Determine which mode will actually be used
        selected = self._streaming_combo.currentData()
        if selected is None:  # Auto
            will_stream = est["auto_streaming"]
            peak_gb = est["streaming_gb"] if will_stream else est["in_memory_gb"]
            mode_name = "streaming" if will_stream else "in-memory"
        elif selected:  # Streaming forced
            will_stream = True
            peak_gb = est["streaming_gb"]
            mode_name = "streaming"
        else:  # In-memory forced
            will_stream = False
            peak_gb = est["in_memory_gb"]
            mode_name = "in-memory"

        ratio = peak_gb / sys_ram if sys_ram > 0 else 1.0

        if ratio > 0.95:
            # Red — will almost certainly OOM
            self._memory_indicator.setText("OOM!")
            self._memory_indicator.setStyleSheet(
                "QLabel { color: white; background-color: #D32F2F; "
                "font-weight: bold; font-size: 10px; "
                "border-radius: 8px; padding: 1px 3px; }"
            )
            self._memory_indicator.setToolTip(
                f"<b>Out of memory risk!</b><br>"
                f"Estimated peak: ~{peak_gb:.0f} GB ({mode_name})<br>"
                f"System RAM: {sys_ram:.0f} GB<br><br>"
                f"Switch to <b>Streaming</b> mode (~{est['streaming_gb']:.1f} GB peak) "
                f"or increase downsample factor."
            )
        elif ratio > 0.70:
            # Orange — tight, might work but risky
            self._memory_indicator.setText("Tight")
            self._memory_indicator.setStyleSheet(
                "QLabel { color: white; background-color: #F57C00; "
                "font-weight: bold; font-size: 10px; "
                "border-radius: 8px; padding: 1px 3px; }"
            )
            self._memory_indicator.setToolTip(
                f"<b>Memory is tight</b><br>"
                f"Estimated peak: ~{peak_gb:.0f} GB ({mode_name})<br>"
                f"System RAM: {sys_ram:.0f} GB ({ratio*100:.0f}% usage)<br><br>"
                f"Should work but leave little room for other applications.<br>"
                f"Streaming mode would use ~{est['streaming_gb']:.1f} GB."
            )
        else:
            # Green — comfortable
            self._memory_indicator.setText("OK")
            self._memory_indicator.setStyleSheet(
                "QLabel { color: white; background-color: #388E3C; "
                "font-weight: bold; font-size: 10px; "
                "border-radius: 8px; padding: 1px 3px; }"
            )
            self._memory_indicator.setToolTip(
                f"Estimated peak: ~{peak_gb:.0f} GB ({mode_name})<br>"
                f"System RAM: {sys_ram:.0f} GB ({ratio*100:.0f}% usage)"
            )

    # --- Run / Cancel ---

    def _build_config(self):
        """Build a StitchingConfig from YAML defaults + current UI settings."""
        from py2flamingo.stitching.pipeline import StitchingConfig

        # Start from YAML defaults (fills in all non-UI-exposed fields)
        config = StitchingConfig.with_yaml_defaults()

        # Overlay UI settings
        z_step = self._z_step_spin.value()
        config.pixel_size_um = self._pixel_size_spin.value()
        config.z_step_um = z_step if z_step > 0 else None
        config.illumination_fusion = self._fusion_combo.currentData()
        config.output_format = self._format_combo.currentData()
        config.flat_field_correction = self._flat_field_cb.isChecked()
        config.destripe = self._destripe_cb.isChecked()
        config.destripe_fast = self._destripe_fast_cb.isChecked()
        config.depth_attenuation = self._depth_atten_cb.isChecked()
        config.depth_attenuation_mu = (
            self._depth_atten_mu_spin.value()
            if self._depth_atten_mu_spin.value() > 0
            else None
        )
        config.downsample_xy = self._downsample_xy_combo.currentData()
        config.downsample_z = self._downsample_z_combo.currentData()
        config.deconvolution_enabled = self._deconv_cb.isChecked()
        config.content_based_fusion = self._content_fusion_cb.isChecked()
        config.skip_registration = self._skip_reg_cb.isChecked()
        config.registration_binning = self._reg_binning_combo.currentData()
        config.package_ozx = self._ozx_cb.isChecked()
        config.tiff_pyramids = self._tiff_pyramids_cb.isChecked()
        config.streaming_mode = self._streaming_combo.currentData()

        # Set compression based on format
        compression = self._compression_combo.currentData()
        if compression:
            fmt = config.output_format
            if fmt == "ome-tiff":
                config.tiff_compression = compression
            elif fmt in ("ome-zarr-sharded", "ome-zarr-v2"):
                config.zarr_compression = compression
            elif fmt == "both":
                config.zarr_compression = compression
                config.tiff_compression = compression
        return config

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
        """Start batch stitching of all pending queue items."""
        pending = [item for item in self._queue if item["status"] == "pending"]
        if not pending:
            QMessageBox.warning(
                self, "Nothing to Run", "No pending directories in the queue."
            )
            return

        output_dir = self._output_dir_edit.text().strip()
        if not output_dir:
            QMessageBox.warning(
                self, "Invalid Input", "Please specify an output directory."
            )
            return

        config = self._build_config()

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
        n_pending = len(pending)
        self._log(f"Starting batch stitching: {n_pending} directories\n")

        # Store batch state
        self._batch_running = True
        self._batch_config = config
        self._batch_channels = self._parse_channels()
        self._batch_results = []

        # Lock settings during run
        self._settings_group.setEnabled(False)
        self._output_dir_edit.setEnabled(False)
        self._update_action_buttons()

        # Start processing
        self._advance_queue()

    def _advance_queue(self):
        """Process the next pending item in the queue."""
        # Find next pending
        next_idx = None
        for i, item in enumerate(self._queue):
            if item["status"] == "pending":
                next_idx = i
                break

        if next_idx is None:
            self._on_batch_complete()
            return

        self._queue_index = next_idx
        item = self._queue[next_idx]
        n_total = sum(1 for it in self._queue if it["status"] not in ("cancelled",))
        n_done = sum(1 for it in self._queue if it["status"] in ("done", "error"))

        self._log(f"\n{'=' * 60}")
        self._log(f"Processing {n_done + 1}/{n_total}: {item['path'].name}")
        self._log(f"{'=' * 60}\n")

        # Discover tiles if not already discovered
        if item["tiles"] is None:
            item["status"] = "discovering"
            self._update_queue_table()
            try:
                tiles = self._discover_tiles_for_path(item["path"])
                if not tiles:
                    item["status"] = "error"
                    item["error"] = "No tiles found"
                    self._log("  No tiles found \u2014 skipping")
                    self._update_queue_table()
                    self._batch_results.append((item["path"], False, "No tiles found"))
                    self._advance_queue()
                    return
                item["tiles"] = tiles
                self._log_tile_summary(tiles)
            except Exception as e:
                item["status"] = "error"
                item["error"] = str(e)
                self._log(f"  Discovery error: {e}")
                self._logger.exception("Batch tile discovery error")
                self._update_queue_table()
                self._batch_results.append((item["path"], False, str(e)))
                self._advance_queue()
                return

        # Compute output path
        acq_name = item["path"].name
        output_dir = Path(self._output_dir_edit.text()) / f"{acq_name}_stitched"
        item["output_path"] = str(output_dir)

        # Update status
        item["status"] = "stitching"
        self._update_queue_table()

        # Start worker
        from py2flamingo.stitching.worker import StitchingWorker

        self._worker = StitchingWorker(
            config=self._batch_config,
            acq_dir=item["path"],
            output_dir=output_dir,
            channels=self._batch_channels,
            tiles=item["tiles"],
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.log_message.connect(self._on_log_message)
        self._worker.completed.connect(self._on_item_completed)
        self._worker.error.connect(self._on_item_error)
        self._worker.finished.connect(self._on_item_finished)
        self._worker.start()

    def _on_cancel(self):
        """Cancel the running pipeline and stop the batch."""
        if self._worker:
            self._worker.cancel()
            self._status_label.setText("Cancelling...")
            self._log("Cancellation requested...")
        # Mark remaining pending items as cancelled
        for item in self._queue:
            if item["status"] == "pending":
                item["status"] = "cancelled"
        self._update_queue_table()

    def _on_progress(self, percentage: int, status: str):
        """Handle progress updates from worker."""
        self._progress_bar.setValue(percentage)
        # Add batch context if multiple items
        if self._batch_running and len(self._queue) > 1:
            n_total = sum(1 for it in self._queue if it["status"] != "cancelled")
            n_done = sum(1 for it in self._queue if it["status"] in ("done", "error"))
            self._status_label.setText(f"[{n_done + 1}/{n_total}] {status}")
        else:
            self._status_label.setText(status)

    def _on_log_message(self, message: str):
        """Handle log messages from worker."""
        self._log_text.append(message)
        scrollbar = self._log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_item_completed(self, output_path: str):
        """Handle successful completion of one queue item."""
        if 0 <= self._queue_index < len(self._queue):
            item = self._queue[self._queue_index]
            item["status"] = "done"
            item["output_path"] = output_path
            self._batch_results.append((item["path"], True, None))
            self._update_queue_table()
        self._log(f"\n\u2713 Completed: {Path(output_path).parent.name}")

    def _on_item_error(self, error_msg: str):
        """Handle error in one queue item."""
        if 0 <= self._queue_index < len(self._queue):
            item = self._queue[self._queue_index]
            item["status"] = "error"
            item["error"] = error_msg
            self._batch_results.append((item["path"], False, error_msg))
            self._update_queue_table()
        self._log(f"\n\u2717 Error: {error_msg}")

    def _on_item_finished(self):
        """Handle worker thread completion for a queue item."""
        self._worker = None
        # If the item is still 'stitching', it was cancelled mid-run
        if 0 <= self._queue_index < len(self._queue):
            item = self._queue[self._queue_index]
            if item["status"] in ("stitching", "discovering"):
                item["status"] = "cancelled"
                self._batch_results.append((item["path"], False, "Cancelled"))
                self._update_queue_table()

        if self._batch_running:
            self._advance_queue()

    def _on_batch_complete(self):
        """Handle completion of all queue items."""
        self._batch_running = False
        self._queue_index = -1
        self._batch_config = None
        self._batch_channels = None

        # Re-enable UI
        self._settings_group.setEnabled(True)
        self._output_dir_edit.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._update_action_buttons()

        n_success = sum(1 for _, ok, _ in self._batch_results if ok)
        n_error = sum(1 for _, ok, _ in self._batch_results if not ok)
        total = len(self._batch_results)

        self._log(f"\n{'=' * 60}")
        self._log(f"Batch complete: {n_success}/{total} succeeded")
        if n_error:
            self._log(f"  {n_error} failed:")
            for path, ok, err in self._batch_results:
                if not ok:
                    self._log(f"    \u2717 {path.name}: {err}")
        self._log(f"{'=' * 60}")

        self._progress_bar.setValue(100 if n_success > 0 else 0)
        self._status_label.setText(f"Done: {n_success}/{total} succeeded")

        # Show summary dialog
        msg = QMessageBox(self)
        msg.setWindowTitle("Batch Stitching Complete")
        if n_error:
            msg.setIcon(QMessageBox.Warning)
            msg.setText(
                f"Batch stitching complete.\n\n"
                f"Succeeded: {n_success}/{total}\n"
                f"Failed: {n_error}/{total}"
            )
        else:
            msg.setIcon(QMessageBox.Information)
            msg.setText(f"All {total} acquisition(s) stitched successfully!")

        # Find last successful output for "Load" option
        last_success_path = None
        for path, ok, _ in reversed(self._batch_results):
            if ok:
                acq_name = path.name
                last_success_path = str(
                    Path(self._output_dir_edit.text()) / f"{acq_name}_stitched"
                )
                break

        if last_success_path:
            load_btn = msg.addButton(
                "Load Latest into Sample View", QMessageBox.AcceptRole
            )
            open_btn = msg.addButton("Open Output Folder", QMessageBox.ActionRole)
            msg.addButton(QMessageBox.Close)
            msg.exec_()
            clicked = msg.clickedButton()
            if clicked == load_btn:
                self.load_stitched_requested.emit(last_success_path)
            elif clicked == open_btn:
                import subprocess
                import sys

                folder = self._output_dir_edit.text()
                if sys.platform == "win32":
                    subprocess.Popen(["explorer", folder])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", folder])
                else:
                    subprocess.Popen(["xdg-open", folder])
        else:
            msg.addButton(QMessageBox.Close)
            msg.exec_()

    # --- Format-dependent UI ---

    def _on_format_changed(self, _index=None):
        """Update compression options and .ozx/pyramid toggles for selected format."""
        fmt = self._format_combo.currentData()
        has_zarr = fmt in ("ome-zarr-sharded", "ome-zarr-v2", "both")
        has_tiff = fmt in ("ome-tiff", "both")
        self._ozx_cb.setEnabled(has_zarr)
        if not has_zarr:
            self._ozx_cb.setChecked(False)
        # TIFF pyramids toggle only relevant for TIFF output
        if hasattr(self, "_tiff_pyramids_cb"):
            self._tiff_pyramids_cb.setEnabled(has_tiff)
        self._update_compression_options()

    def _update_compression_options(self):
        """Populate compression combo based on selected output format.

        Only offers codecs that are actually available in the current
        environment. zstd for TIFF requires the imagecodecs package.
        """
        fmt = self._format_combo.currentData()
        prev = self._compression_combo.currentData()
        self._compression_combo.blockSignals(True)
        self._compression_combo.clear()

        if fmt == "imaris":
            # PyImarisWriter handles compression internally (Gzip Level 2).
            # Lock the combo to a single informational entry.
            self._compression_combo.addItem("(internal: Gzip L2)", "gzip")
            self._compression_combo.setEnabled(False)
            self._compression_combo.blockSignals(False)
            return

        self._compression_combo.setEnabled(True)

        if fmt == "ome-tiff":
            # zlib and lzw are always available (built into tifffile/Python)
            self._compression_combo.addItem("zlib (universal)", "zlib")
            self._compression_combo.addItem("lzw (fast read)", "lzw")
            # zstd for TIFF requires imagecodecs
            if self._tiff_zstd_available():
                self._compression_combo.addItem("zstd (best ratio)", "zstd")
            self._compression_combo.addItem("None (fastest write)", "none")
            default = "zlib"
        elif fmt in ("ome-zarr-sharded", "ome-zarr-v2"):
            # Zarr codecs are handled by numcodecs, always available
            self._compression_combo.addItem("zstd (recommended)", "zstd")
            self._compression_combo.addItem("lz4 (fastest)", "lz4")
            self._compression_combo.addItem("blosc (compatible)", "blosc")
            self._compression_combo.addItem("None (fastest write)", "none")
            default = "zstd"
        else:
            # "both" — show zarr options (tiff will use zlib internally)
            self._compression_combo.addItem("zstd (recommended)", "zstd")
            self._compression_combo.addItem("lz4 (fastest)", "lz4")
            self._compression_combo.addItem("blosc (compatible)", "blosc")
            self._compression_combo.addItem("None (fastest write)", "none")
            default = "zstd"

        # Restore previous selection if still valid for this format
        idx = self._compression_combo.findData(prev)
        if idx >= 0:
            self._compression_combo.setCurrentIndex(idx)
        else:
            idx = self._compression_combo.findData(default)
            if idx >= 0:
                self._compression_combo.setCurrentIndex(idx)
        self._compression_combo.blockSignals(False)

    @staticmethod
    def _tiff_zstd_available() -> bool:
        """Check if zstd compression is available for TIFF output."""
        try:
            import imagecodecs  # noqa: F401

            return True
        except ImportError:
            pass
        try:
            from compression import zstd  # noqa: F401

            return True
        except ImportError:
            pass
        return False

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
        # Save queue paths (only pending/done items, not transient states)
        paths = [str(item["path"]) for item in self._queue]
        s.setValue("queue_paths", paths)
        s.setValue("output_dir", self._output_dir_edit.text())
        s.setValue("pixel_size", self._pixel_size_spin.value())
        s.setValue("z_step", self._z_step_spin.value())
        s.setValue("downsample_xy", self._downsample_xy_combo.currentData())
        s.setValue("downsample_z", self._downsample_z_combo.currentData())
        s.setValue("fusion", self._fusion_combo.currentData())
        s.setValue("flat_field", self._flat_field_cb.isChecked())
        s.setValue("destripe", self._destripe_cb.isChecked())
        s.setValue("destripe_fast", self._destripe_fast_cb.isChecked())
        s.setValue("depth_attenuation", self._depth_atten_cb.isChecked())
        s.setValue("depth_attenuation_mu", self._depth_atten_mu_spin.value())
        s.setValue("deconvolution", self._deconv_cb.isChecked())
        s.setValue("content_based_fusion", self._content_fusion_cb.isChecked())
        s.setValue("package_ozx", self._ozx_cb.isChecked())
        s.setValue("tiff_pyramids", self._tiff_pyramids_cb.isChecked())
        s.setValue("output_format", self._format_combo.currentData())
        s.setValue("compression", self._compression_combo.currentData())
        s.setValue("channels", self._channels_edit.text())
        s.setValue("streaming_mode", self._streaming_combo.currentIndex())
        s.setValue("skip_registration", self._skip_reg_cb.isChecked())
        s.setValue("reg_binning", self._reg_binning_combo.currentIndex())
        s.setValue("proc_options_expanded", self._proc_toggle.isChecked())
        s.endGroup()

    def _restore_settings(self):
        """Restore dialog settings from QSettings."""
        s = QSettings()
        s.beginGroup(_SETTINGS_GROUP)

        # Restore queue paths
        paths = s.value("queue_paths", [], type=list)
        if paths:
            if isinstance(paths, str):
                paths = [paths]
            for p in paths:
                if p and Path(p).is_dir():
                    self._add_path_to_queue(Path(p))
        # Legacy: also try old single acq_dir key
        if not self._queue:
            acq_dir = s.value("acq_dir", "", type=str)
            if acq_dir and Path(acq_dir).is_dir():
                self._add_path_to_queue(Path(acq_dir))

        output_dir = s.value("output_dir", "", type=str)
        if output_dir:
            self._output_dir_edit.setText(output_dir)

        pixel_size = s.value("pixel_size", self._default_pixel_um, type=float)
        self._pixel_size_spin.setValue(pixel_size)

        z_step = s.value("z_step", 0.0, type=float)
        self._z_step_spin.setValue(z_step)

        ds_xy = s.value("downsample_xy", 0, type=int)
        if ds_xy:
            idx = self._downsample_xy_combo.findData(ds_xy)
            if idx >= 0:
                self._downsample_xy_combo.setCurrentIndex(idx)
        else:
            # Legacy: try old single "downsample" key as XY
            ds_old = s.value("downsample", 0, type=int)
            if ds_old:
                idx = self._downsample_xy_combo.findData(ds_old)
                if idx >= 0:
                    self._downsample_xy_combo.setCurrentIndex(idx)
        ds_z = s.value("downsample_z", 0, type=int)
        if ds_z:
            idx = self._downsample_z_combo.findData(ds_z)
            if idx >= 0:
                self._downsample_z_combo.setCurrentIndex(idx)

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

        tiff_pyramids = s.value("tiff_pyramids", True, type=bool)
        self._tiff_pyramids_cb.setChecked(tiff_pyramids)

        output_format = s.value("output_format", "", type=str)
        if output_format:
            idx = self._format_combo.findData(output_format)
            if idx >= 0:
                self._format_combo.setCurrentIndex(idx)

        # Restore compression after format (format determines available options)
        compression = s.value("compression", "", type=str)
        if compression:
            idx = self._compression_combo.findData(compression)
            if idx >= 0:
                self._compression_combo.setCurrentIndex(idx)

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

        proc_expanded = s.value("proc_options_expanded", False, type=bool)
        self._proc_toggle.setChecked(proc_expanded)

        s.endGroup()

    def closeEvent(self, event):
        """Save settings and cancel worker on close."""
        self._save_settings()
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
        self._batch_running = False
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

    def _discover_tiles_for_path(self, acq_path: Path):
        """Discover tiles using flat-layout scanner."""
        from py2flamingo.stitching.pipeline import (
            _read_plane_spacing,
            discover_flat_tiles,
        )

        tiles = discover_flat_tiles(acq_path)

        # Auto-set Z step from Workflow.txt if currently "Auto"
        if tiles and self._z_step_spin.value() == 0.0:
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

        return tiles

    def _looks_like_acquisition(self, path: Path) -> bool:
        """Check if a directory looks like a flat-layout acquisition."""
        if (path / "Workflow.txt").exists():
            return True
        return any(path.glob("*.raw"))

    def _save_settings(self):
        """Save dialog settings to QSettings (independent group)."""
        s = QSettings()
        s.beginGroup(_NATIVE_SETTINGS_GROUP)
        paths = [str(item["path"]) for item in self._queue]
        s.setValue("queue_paths", paths)
        s.setValue("output_dir", self._output_dir_edit.text())
        s.setValue("pixel_size", self._pixel_size_spin.value())
        s.setValue("z_step", self._z_step_spin.value())
        s.setValue("downsample_xy", self._downsample_xy_combo.currentData())
        s.setValue("downsample_z", self._downsample_z_combo.currentData())
        s.setValue("fusion", self._fusion_combo.currentData())
        s.setValue("flat_field", self._flat_field_cb.isChecked())
        s.setValue("destripe", self._destripe_cb.isChecked())
        s.setValue("destripe_fast", self._destripe_fast_cb.isChecked())
        s.setValue("depth_attenuation", self._depth_atten_cb.isChecked())
        s.setValue("depth_attenuation_mu", self._depth_atten_mu_spin.value())
        s.setValue("deconvolution", self._deconv_cb.isChecked())
        s.setValue("content_based_fusion", self._content_fusion_cb.isChecked())
        s.setValue("package_ozx", self._ozx_cb.isChecked())
        s.setValue("tiff_pyramids", self._tiff_pyramids_cb.isChecked())
        s.setValue("output_format", self._format_combo.currentData())
        s.setValue("compression", self._compression_combo.currentData())
        s.setValue("channels", self._channels_edit.text())
        s.setValue("streaming_mode", self._streaming_combo.currentIndex())
        s.setValue("skip_registration", self._skip_reg_cb.isChecked())
        s.setValue("reg_binning", self._reg_binning_combo.currentIndex())
        s.setValue("proc_options_expanded", self._proc_toggle.isChecked())
        s.endGroup()

    def _restore_settings(self):
        """Restore dialog settings from QSettings (independent group)."""
        s = QSettings()
        s.beginGroup(_NATIVE_SETTINGS_GROUP)

        # Restore queue paths
        paths = s.value("queue_paths", [], type=list)
        if paths:
            if isinstance(paths, str):
                paths = [paths]
            for p in paths:
                if p and Path(p).is_dir():
                    self._add_path_to_queue(Path(p))
        # Legacy: also try old single acq_dir key
        if not self._queue:
            acq_dir = s.value("acq_dir", "", type=str)
            if acq_dir and Path(acq_dir).is_dir():
                self._add_path_to_queue(Path(acq_dir))

        output_dir = s.value("output_dir", "", type=str)
        if output_dir:
            self._output_dir_edit.setText(output_dir)

        pixel_size = s.value("pixel_size", self._default_pixel_um, type=float)
        self._pixel_size_spin.setValue(pixel_size)

        z_step = s.value("z_step", 0.0, type=float)
        self._z_step_spin.setValue(z_step)

        ds_xy = s.value("downsample_xy", 0, type=int)
        if ds_xy:
            idx = self._downsample_xy_combo.findData(ds_xy)
            if idx >= 0:
                self._downsample_xy_combo.setCurrentIndex(idx)
        else:
            # Legacy: try old single "downsample" key as XY
            ds_old = s.value("downsample", 0, type=int)
            if ds_old:
                idx = self._downsample_xy_combo.findData(ds_old)
                if idx >= 0:
                    self._downsample_xy_combo.setCurrentIndex(idx)
        ds_z = s.value("downsample_z", 0, type=int)
        if ds_z:
            idx = self._downsample_z_combo.findData(ds_z)
            if idx >= 0:
                self._downsample_z_combo.setCurrentIndex(idx)

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

        tiff_pyramids = s.value("tiff_pyramids", True, type=bool)
        self._tiff_pyramids_cb.setChecked(tiff_pyramids)

        output_format = s.value("output_format", "", type=str)
        if output_format:
            idx = self._format_combo.findData(output_format)
            if idx >= 0:
                self._format_combo.setCurrentIndex(idx)

        compression = s.value("compression", "", type=str)
        if compression:
            idx = self._compression_combo.findData(compression)
            if idx >= 0:
                self._compression_combo.setCurrentIndex(idx)

        channels = s.value("channels", "", type=str)
        if channels:
            self._channels_edit.setText(channels)

        streaming_idx = s.value("streaming_mode", 0, type=int)
        if 0 <= streaming_idx < self._streaming_combo.count():
            self._streaming_combo.setCurrentIndex(streaming_idx)

        skip_reg = s.value("skip_registration", False, type=bool)
        self._skip_reg_cb.setChecked(skip_reg)

        reg_binning_idx = s.value("reg_binning", 1, type=int)
        if 0 <= reg_binning_idx < self._reg_binning_combo.count():
            self._reg_binning_combo.setCurrentIndex(reg_binning_idx)

        proc_expanded = s.value("proc_options_expanded", False, type=bool)
        self._proc_toggle.setChecked(proc_expanded)

        s.endGroup()
