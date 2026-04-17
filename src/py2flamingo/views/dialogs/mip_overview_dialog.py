# src/py2flamingo/views/dialogs/mip_overview_dialog.py
"""MIP Overview Dialog.

Loads and displays Maximum Intensity Projection (MIP) images from saved
tile acquisitions, allowing users to select tiles for re-acquisition.

The dialog supports:
- Loading MIP files from subfolder layout: base/date/X{x}_Y{y}/*_MP.tif
- Loading MIP files from flat layout: directory/*_X###_Y###_C##*_MP.tif
- Dual panel display: original MIPs (left) and new acquisition results (right)
- Click-to-select tile interface with channel switching (flat layout)
- Auto-select using variance/edge detection
- Integration with TileCollectionDialog for re-acquisition
- Export overview with grid lines and coordinate labels
- Session save/load for persistence
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import tifffile
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
)

from py2flamingo.models.data.overview_results import TileResult
from py2flamingo.models.mip_overview import (
    FlatMIPTileInfo,
    MIPOverviewConfig,
    MIPTileResult,
    calculate_grid_indices,
    detect_layout_type,
    discover_flat_mip_tiles,
    export_overview_with_labels,
    find_date_folders,
    find_tile_folders,
    load_invert_x_setting,
    parse_coords_from_folder,
    read_tile_overlap_from_workflow,
)
from py2flamingo.services.window_geometry_manager import PersistentDialog
from py2flamingo.visualization.zarr_2d_session import (
    ZARR_AVAILABLE,
    detect_session_format,
    load_2d_zarr_session,
    load_2d_zarr_session_lazy,
    save_2d_zarr_session,
)

# Import ImagePanel from LED 2D Overview (reuse UI components)
from .led_2d_overview_result import ImagePanel

logger = logging.getLogger(__name__)


class MIPOverviewDialog(PersistentDialog):
    """Dialog for loading and viewing MIP tile overviews.

    Allows users to:
    1. Browse and load MIP files from tile acquisition folders
    2. View stitched overview of all tiles
    3. Select tiles for re-acquisition
    4. Launch TileCollectionDialog with selected tiles
    5. View results from new acquisitions
    """

    # Signal emitted when tiles are selected for collection
    collect_tiles_requested = pyqtSignal(list)  # List of MIPTileResult

    def __init__(self, app=None, parent=None):
        """Initialize the MIP Overview Dialog.

        Args:
            app: FlamingoApplication instance for services
            parent: Parent widget
        """
        super().__init__(parent)
        self._app = app
        self._tiles: List[MIPTileResult] = []
        self._config: Optional[MIPOverviewConfig] = None
        self._stitched_image: Optional[np.ndarray] = None
        self._results_image: Optional[np.ndarray] = None
        self._results_tiles: List[MIPTileResult] = []
        self._first_show = True
        # Flat-layout state
        self._detected_layout: str = "subfolder"
        self._flat_tile_infos: List[FlatMIPTileInfo] = []
        self._channel_cache: Dict[int, List[np.ndarray]] = {}  # ch_id -> [images]

        self.setWindowTitle("MIP Overview")
        self.setMinimumSize(1200, 800)
        self._setup_ui()

    def _setup_ui(self):
        """Set up the dialog UI."""
        layout = QVBoxLayout()
        layout.setSpacing(8)

        # Folder browser section - load from raw acquisition folders
        folder_group = QGroupBox("Load from Acquisition Folders")
        folder_layout = QHBoxLayout()

        folder_layout.addWidget(QLabel("Folder:"))
        self._folder_edit = QLineEdit()
        self._folder_edit.setPlaceholderText(
            "Select folder containing tile acquisitions (subfolder or flat layout)..."
        )
        self._folder_edit.setReadOnly(True)
        folder_layout.addWidget(self._folder_edit, stretch=1)

        self._browse_btn = QPushButton("Browse...")
        self._browse_btn.setToolTip(
            "Browse for a folder containing tile acquisition data.\n"
            "Supports subfolder layout (X*_Y*/*_MP.tif) and\n"
            "flat layout (*_X###_Y###_C##*_MP.tif or *_X###_Y###_C##.tif)"
        )
        self._browse_btn.clicked.connect(self._on_browse)
        folder_layout.addWidget(self._browse_btn)

        folder_layout.addWidget(QLabel("Date:"))
        self._date_combo = QComboBox()
        self._date_combo.setMinimumWidth(120)
        self._date_combo.currentIndexChanged.connect(self._on_date_changed)
        folder_layout.addWidget(self._date_combo)

        # Channel selector (visible for flat layout only)
        self._channel_label = QLabel("Channel:")
        self._channel_label.setVisible(False)
        folder_layout.addWidget(self._channel_label)
        self._channel_combo = QComboBox()
        self._channel_combo.setMinimumWidth(80)
        self._channel_combo.setVisible(False)
        self._channel_combo.currentIndexChanged.connect(self._on_channel_changed)
        folder_layout.addWidget(self._channel_combo)

        self._load_btn = QPushButton("Load")
        self._load_btn.setEnabled(False)
        self._load_btn.setToolTip("Load MIP images from acquisition folders")
        self._load_btn.clicked.connect(self._on_load)
        folder_layout.addWidget(self._load_btn)

        folder_group.setLayout(folder_layout)
        layout.addWidget(folder_group)

        # Splitter for dual panels
        self._splitter = QSplitter(Qt.Horizontal)

        # Left panel - Original MIPs
        self._left_panel = ImagePanel("Original MIPs (Loaded)")
        self._left_panel.selection_changed.connect(self._on_selection_changed)
        self._left_panel.tile_right_clicked.connect(self._on_tile_right_clicked)
        self._splitter.addWidget(self._left_panel)

        # Right panel - Results from new acquisitions
        self._right_panel = ImagePanel("New Acquisition Results")
        self._right_panel.selection_changed.connect(self._on_selection_changed)
        self._splitter.addWidget(self._right_panel)

        # Equal split
        self._splitter.setSizes([500, 500])

        layout.addWidget(self._splitter, stretch=1)

        # Status bar
        status_frame = QFrame()
        status_frame.setFrameStyle(QFrame.StyledPanel)
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(8, 4, 8, 4)

        self._status_label = QLabel("No tiles loaded")
        status_layout.addWidget(self._status_label)

        status_layout.addStretch()

        # Info about loaded data
        self._info_label = QLabel("")
        self._info_label.setStyleSheet("color: #888;")
        status_layout.addWidget(self._info_label)

        status_frame.setLayout(status_layout)
        layout.addWidget(status_frame)

        # Action buttons
        button_layout = QHBoxLayout()

        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.clicked.connect(self._on_select_all)
        self._select_all_btn.setEnabled(False)
        button_layout.addWidget(self._select_all_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._on_clear_selection)
        self._clear_btn.setEnabled(False)
        button_layout.addWidget(self._clear_btn)

        self._auto_select_btn = QPushButton("Auto-Select...")
        self._auto_select_btn.clicked.connect(self._on_auto_select)
        self._auto_select_btn.setEnabled(False)
        button_layout.addWidget(self._auto_select_btn)

        button_layout.addStretch()

        self._collect_btn = QPushButton("Collect Tiles...")
        self._collect_btn.clicked.connect(self._on_collect_tiles)
        self._collect_btn.setEnabled(False)
        self._collect_btn.setToolTip("Open Tile Collection Dialog with selected tiles")
        button_layout.addWidget(self._collect_btn)

        button_layout.addStretch()

        self._fit_btn = QPushButton("Fit")
        self._fit_btn.clicked.connect(self._on_fit)
        self._fit_btn.setEnabled(False)
        button_layout.addWidget(self._fit_btn)

        self._one_to_one_btn = QPushButton("1:1")
        self._one_to_one_btn.clicked.connect(self._on_one_to_one)
        self._one_to_one_btn.setEnabled(False)
        button_layout.addWidget(self._one_to_one_btn)

        button_layout.addStretch()

        overlap_label = QLabel("Overlap:")
        overlap_label.setStyleSheet("color: gray; font-size: 9pt;")
        button_layout.addWidget(overlap_label)

        self._overlap_spin = QSpinBox()
        self._overlap_spin.setRange(0, 50)
        self._overlap_spin.setValue(5)
        self._overlap_spin.setSuffix("%")
        self._overlap_spin.setToolTip(
            "Tile overlap percentage — affects both the mosaic display\n"
            "and exported overview. Auto-detected from Workflow.txt\n"
            "when available. 0% = tiles placed edge-to-edge."
        )
        self._overlap_spin.setFixedWidth(65)
        self._overlap_spin.valueChanged.connect(self._on_overlap_changed)
        button_layout.addWidget(self._overlap_spin)

        self._export_btn = QPushButton("Export Overview...")
        self._export_btn.setToolTip(
            "Export downsampled overview with grid lines and\n"
            "coordinate labels as a multi-channel TIFF"
        )
        self._export_btn.clicked.connect(self._on_export_overview)
        self._export_btn.setEnabled(False)
        button_layout.addWidget(self._export_btn)

        self._save_btn = QPushButton("Save Session")
        self._save_btn.setToolTip(
            "Save current overview as a session (can be loaded later without original files)"
        )
        self._save_btn.clicked.connect(self._on_save_session)
        self._save_btn.setEnabled(False)
        button_layout.addWidget(self._save_btn)

        self._load_session_btn = QPushButton("Load Session")
        self._load_session_btn.setToolTip(
            "Load a previously saved session (metadata + stitched overview)"
        )
        self._load_session_btn.clicked.connect(self._on_load_session)
        button_layout.addWidget(self._load_session_btn)

        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.close)
        button_layout.addWidget(self._close_btn)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def showEvent(self, event):
        """Handle show event - fit images on first display."""
        super().showEvent(event)
        if self._first_show and self._stitched_image is not None:
            self._first_show = False
            # Trigger fit after window is visible
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(100, self._on_fit)

    def _on_browse(self):
        """Browse for folder containing tile acquisitions."""
        # Remember last browse location
        default_path = str(Path.home())
        if self._app and hasattr(self._app, "config_service"):
            saved = self._app.config_service.get_mip_browse_path()
            if saved and Path(saved).exists():
                default_path = saved

        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder with Tile Acquisitions", default_path
        )
        if folder:
            # Save browse location for next time
            if self._app and hasattr(self._app, "config_service"):
                self._app.config_service.set_mip_browse_path(folder)
            self._folder_edit.setText(folder)
            self._update_date_combo(Path(folder))

    def _update_date_combo(self, base_path: Path):
        """Update date combo box with available date folders."""
        self._date_combo.clear()
        self._detected_layout = "subfolder"

        date_folders = find_date_folders(base_path)

        if date_folders:
            # Check each date folder for subfolder or flat layout
            for df in date_folders:
                layout = detect_layout_type(base_path / df)
                if layout == "flat":
                    self._date_combo.addItem(f"{df} (flat)")
                else:
                    self._date_combo.addItem(df)
            self._load_btn.setEnabled(True)
        else:
            # Check current folder directly
            layout = detect_layout_type(base_path)
            if layout == "subfolder":
                self._date_combo.addItem("(current folder)")
                self._detected_layout = "subfolder"
                self._load_btn.setEnabled(True)
            elif layout == "flat":
                self._date_combo.addItem("(current folder)")
                self._detected_layout = "flat"
                self._load_btn.setEnabled(True)
            else:
                # Check if current folder has X_Y subfolders directly
                tile_folders = find_tile_folders(base_path)
                if tile_folders:
                    self._date_combo.addItem("(current folder)")
                    self._load_btn.setEnabled(True)
                else:
                    self._date_combo.addItem("(no tiles found)")
                    self._load_btn.setEnabled(False)

        # Update channel combo visibility
        self._update_channel_visibility()

    def _on_date_changed(self, index: int):
        """Handle date selection change."""
        # Enable/disable load button based on selection
        if self._date_combo.currentText() not in ["(no tiles found)", ""]:
            self._load_btn.setEnabled(True)
        else:
            self._load_btn.setEnabled(False)

    def _on_load(self):
        """Load MIP files from selected folder."""
        base_path = Path(self._folder_edit.text())
        date_text = self._date_combo.currentText()

        if date_text == "(current folder)":
            load_path = base_path
            date_folder = ""
        elif date_text.endswith(" (flat)"):
            date_folder = date_text.replace(" (flat)", "")
            load_path = base_path / date_folder
        else:
            load_path = base_path / date_text
            date_folder = date_text

        # Determine layout type for this specific load path
        layout = detect_layout_type(load_path)
        if layout == "flat" or date_text.endswith(" (flat)"):
            self._on_load_flat(load_path, base_path, date_folder)
            return

        # Subfolder layout: existing code path
        self._on_load_subfolder(load_path, base_path, date_folder)

    def _on_load_subfolder(self, load_path: Path, base_path: Path, date_folder: str):
        """Load MIP files from subfolder-per-tile layout."""
        tile_folders = find_tile_folders(load_path)
        if not tile_folders:
            QMessageBox.warning(
                self,
                "No Tiles Found",
                f"No tile folders (X*_Y*) found in:\n{load_path}",
            )
            return

        progress = QProgressDialog(
            "Loading MIP files...", "Cancel", 0, len(tile_folders), self
        )
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        tiles = []
        skipped = 0

        for i, tile_folder in enumerate(tile_folders):
            if progress.wasCanceled():
                return

            progress.setValue(i)
            progress.setLabelText(f"Loading {tile_folder.name}...")

            try:
                x, y = parse_coords_from_folder(tile_folder.name)
            except ValueError as e:
                logger.warning(f"Skipping folder {tile_folder.name}: {e}")
                skipped += 1
                continue

            mip_files = list(tile_folder.glob("*_MP.tif"))
            if not mip_files:
                logger.warning(f"No *_MP.tif file in {tile_folder}")
                skipped += 1
                continue

            mip_file = mip_files[0]
            try:
                image = tifffile.imread(str(mip_file))
                logger.debug(
                    f"Loaded {mip_file.name}: shape={image.shape}, dtype={image.dtype}"
                )
            except Exception as e:
                logger.error(f"Failed to load {mip_file}: {e}")
                skipped += 1
                continue

            tile = MIPTileResult(
                x=x,
                y=y,
                z=0.0,
                tile_x_idx=0,
                tile_y_idx=0,
                image=image,
                folder_path=tile_folder,
            )
            tiles.append(tile)

        progress.setValue(len(tile_folders))

        if not tiles:
            QMessageBox.warning(
                self,
                "No MIPs Loaded",
                f"Failed to load any MIP files from:\n{load_path}\n\n"
                f"Skipped {skipped} folders.",
            )
            return

        # Calculate grid indices
        calculate_grid_indices(tiles)

        tiles_x = max(t.tile_x_idx for t in tiles) + 1
        tiles_y = max(t.tile_y_idx for t in tiles) + 1
        tile_size = tiles[0].image.shape[0]

        self._config = MIPOverviewConfig(
            base_folder=base_path,
            date_folder=date_folder,
            tiles_x=tiles_x,
            tiles_y=tiles_y,
            tile_size_pixels=tile_size,
            downsample_factor=4,
            invert_x=load_invert_x_setting(),
            layout_type="subfolder",
        )

        self._tiles = tiles
        self._flat_tile_infos = []
        self._channel_cache.clear()
        self._detected_layout = "subfolder"

        self._stitch_and_display()
        self._update_status()
        self._enable_controls(True)
        self._update_channel_visibility()

        if skipped > 0:
            QMessageBox.information(
                self,
                "Load Complete",
                f"Loaded {len(tiles)} tiles ({tiles_x}x{tiles_y} grid).\n"
                f"Skipped {skipped} folders (no MIP or invalid name).",
            )

    def _on_load_flat(self, load_path: Path, base_path: Path, date_folder: str):
        """Load MIP files from flat-layout directory."""
        flat_infos = discover_flat_mip_tiles(load_path)
        if not flat_infos:
            QMessageBox.warning(
                self,
                "No Tiles Found",
                f"No flat-layout MIP tiles found in:\n{load_path}",
            )
            return

        self._flat_tile_infos = flat_infos
        self._channel_cache.clear()
        self._detected_layout = "flat"

        # Auto-detect tile overlap from Workflow.txt
        overlap = read_tile_overlap_from_workflow(load_path)
        if overlap is not None:
            # Use the average of X and Y overlap (they're usually equal)
            avg_overlap = round((overlap[0] + overlap[1]) / 2)
            self._overlap_spin.setValue(int(avg_overlap))
            logger.info(f"Auto-set overlap to {avg_overlap}% from Workflow.txt")

        # Determine available channels
        all_channels = sorted(set(ch for t in flat_infos for ch in t.channel_files))

        # Populate channel combo (block signals to avoid premature reload)
        self._channel_combo.blockSignals(True)
        self._channel_combo.clear()
        for ch in all_channels:
            self._channel_combo.addItem(f"C{ch:02d}", ch)
        self._channel_combo.blockSignals(False)

        # Pick the first channel to display
        display_channel = all_channels[0] if all_channels else 0

        # Load tiles for the selected channel
        tiles = self._load_flat_channel(flat_infos, display_channel)
        if not tiles:
            QMessageBox.warning(
                self,
                "Load Failed",
                "Failed to load any MIP images for the selected channel.",
            )
            return

        calculate_grid_indices(tiles)

        tiles_x = max(t.tile_x_idx for t in tiles) + 1
        tiles_y = max(t.tile_y_idx for t in tiles) + 1
        tile_size = tiles[0].image.shape[0]

        self._config = MIPOverviewConfig(
            base_folder=base_path,
            date_folder=date_folder,
            tiles_x=tiles_x,
            tiles_y=tiles_y,
            tile_size_pixels=tile_size,
            downsample_factor=4,
            invert_x=load_invert_x_setting(),
            layout_type="flat",
            display_channel=display_channel,
            available_channels=all_channels,
        )

        self._tiles = tiles

        self._stitch_and_display()
        self._update_status()
        self._enable_controls(True)
        self._update_channel_visibility()

        logger.info(
            f"Loaded flat MIP overview: {len(tiles)} tiles "
            f"({tiles_x}x{tiles_y}), {len(all_channels)} channels"
        )

    def _load_flat_channel(
        self,
        flat_infos: List[FlatMIPTileInfo],
        channel_id: int,
    ) -> List[MIPTileResult]:
        """Load MIP images for a specific channel from flat tile infos.

        Uses a cache to avoid re-reading previously loaded channels.

        Returns:
            List of MIPTileResult objects with images for the requested channel.
        """
        tiles = []
        for fi in flat_infos:
            if channel_id not in fi.channel_files:
                continue

            # Check cache first
            cache_key = (id(fi), channel_id)
            if channel_id in self._channel_cache:
                cached = self._channel_cache[channel_id]
                idx = next(
                    (i for i, f2 in enumerate(self._flat_tile_infos) if f2 is fi),
                    None,
                )
                if idx is not None and idx < len(cached):
                    image = cached[idx]
                    tiles.append(
                        MIPTileResult(
                            x=fi.x_mm,
                            y=fi.y_mm,
                            z=(fi.z_min_mm + fi.z_max_mm) / 2,
                            tile_x_idx=fi.x_idx,
                            tile_y_idx=fi.y_idx,
                            image=image,
                            folder_path=fi.channel_files[channel_id].parent,
                            z_stack_min=fi.z_min_mm,
                            z_stack_max=fi.z_max_mm,
                        )
                    )
                    continue

            # Read from disk
            try:
                image = tifffile.imread(str(fi.channel_files[channel_id]))
                if image.ndim == 3:
                    image = (
                        image[0] if image.shape[0] < image.shape[-1] else image[:, :, 0]
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to load MIP for tile X{fi.x_idx}_Y{fi.y_idx} "
                    f"C{channel_id}: {e}"
                )
                continue

            tiles.append(
                MIPTileResult(
                    x=fi.x_mm,
                    y=fi.y_mm,
                    z=(fi.z_min_mm + fi.z_max_mm) / 2,
                    tile_x_idx=fi.x_idx,
                    tile_y_idx=fi.y_idx,
                    image=image,
                    folder_path=fi.channel_files[channel_id].parent,
                    z_stack_min=fi.z_min_mm,
                    z_stack_max=fi.z_max_mm,
                )
            )

        # Cache the loaded images for this channel
        if tiles and channel_id not in self._channel_cache:
            self._channel_cache[channel_id] = [t.image for t in tiles]

        return tiles

    def _update_channel_visibility(self):
        """Show/hide the channel combo based on detected layout type."""
        is_flat = self._detected_layout == "flat"
        self._channel_label.setVisible(is_flat)
        self._channel_combo.setVisible(is_flat)

    def _on_channel_changed(self, index: int):
        """Handle channel selection change - reload tiles for the new channel."""
        if index < 0 or not self._flat_tile_infos or not self._config:
            return

        channel_id = self._channel_combo.currentData()
        if channel_id is None:
            return

        self._config.display_channel = channel_id

        # Reload tiles for the selected channel
        tiles = self._load_flat_channel(self._flat_tile_infos, channel_id)
        if not tiles:
            logger.warning(f"No tiles loaded for channel C{channel_id:02d}")
            return

        calculate_grid_indices(tiles)
        self._tiles = tiles

        # Re-stitch and display
        self._stitch_and_display()
        self._update_status()

        logger.info(f"Switched to channel C{channel_id:02d}")

    def _on_overlap_changed(self, value: int):
        """Re-stitch and redisplay when overlap percentage changes."""
        if self._tiles and self._config:
            self._stitch_and_display()

    def _on_export_overview(self):
        """Export overview with grid lines and coordinate labels as TIFF."""
        if not self._tiles or not self._config:
            QMessageBox.warning(self, "Nothing to Export", "No tiles loaded.")
            return

        # Default filename based on folder name
        default_name = "mip_overview_labeled.tif"
        if self._config.base_folder:
            default_name = f"{self._config.base_folder.name}_overview.tif"

        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Overview with Labels",
            str(Path.home() / default_name),
            "TIFF Files (*.tif *.tiff)",
        )
        if not output_path:
            return

        try:
            overlap_pct = self._overlap_spin.value() / 100.0
            export_overview_with_labels(
                tiles=self._tiles,
                config=self._config,
                output_path=Path(output_path),
                flat_tile_infos=(
                    self._flat_tile_infos if self._flat_tile_infos else None
                ),
                overlap_pct=overlap_pct,
            )
            QMessageBox.information(
                self,
                "Export Complete",
                f"Overview exported to:\n{output_path}",
            )
        except Exception as e:
            logger.exception("Failed to export overview")
            QMessageBox.critical(
                self,
                "Export Failed",
                f"Failed to export overview:\n{e}",
            )

    def _stitch_and_display(self):
        """Stitch loaded MIP tiles into overview and display.

        Uses the overlap percentage from the overlap spinbox to place tiles
        with the correct spatial relationship. Overlap regions use
        maximum-intensity blending.
        """
        if not self._tiles or not self._config:
            return

        tiles_x = self._config.tiles_x
        tiles_y = self._config.tiles_y
        downsample = self._config.downsample_factor
        overlap_pct = self._overlap_spin.value() / 100.0

        # Determine tile size after downsampling
        sample_tile = self._tiles[0].image
        if len(sample_tile.shape) == 2:
            orig_h, orig_w = sample_tile.shape
        else:
            orig_h, orig_w = sample_tile.shape[:2]

        tile_h = orig_h // downsample
        tile_w = orig_w // downsample

        # Compute stride (distance between tile origins) accounting for overlap
        overlap_x = int(tile_w * overlap_pct)
        overlap_y = int(tile_h * overlap_pct)
        stride_x = tile_w - overlap_x
        stride_y = tile_h - overlap_y

        # Mosaic dimensions with overlap
        if tiles_x > 1:
            stitched_w = stride_x * (tiles_x - 1) + tile_w
        else:
            stitched_w = tile_w
        if tiles_y > 1:
            stitched_h = stride_y * (tiles_y - 1) + tile_h
        else:
            stitched_h = tile_h

        # Use same dtype as source
        dtype = sample_tile.dtype
        stitched = np.zeros((stitched_h, stitched_w), dtype=dtype)

        # Place tiles (max-intensity blending in overlap regions)
        for tile in self._tiles:
            # Downsample tile image
            if downsample > 1:
                # Simple block averaging
                tile_img = tile.image
                if len(tile_img.shape) == 3:
                    tile_img = tile_img[:, :, 0]  # Take first channel if RGB

                # Reshape and average
                h, w = tile_img.shape
                new_h = h // downsample
                new_w = w // downsample
                # Crop to exact multiple
                cropped = tile_img[: new_h * downsample, : new_w * downsample]
                # Reshape and mean
                reshaped = cropped.reshape(new_h, downsample, new_w, downsample)
                downsampled = reshaped.mean(axis=(1, 3)).astype(dtype)
            else:
                downsampled = tile.image

            # Calculate position using stride (invert X if needed)
            if self._config.invert_x:
                inverted_x_idx = (tiles_x - 1) - tile.tile_x_idx
                x_pos = inverted_x_idx * stride_x
            else:
                x_pos = tile.tile_x_idx * stride_x
            y_pos = tile.tile_y_idx * stride_y

            # Place in stitched image with max-intensity blending for overlaps
            dh, dw = downsampled.shape[:2]
            region = stitched[y_pos : y_pos + dh, x_pos : x_pos + dw]
            np.maximum(region, downsampled[:dh, :dw], out=region)

        self._stitched_image = stitched

        # Build coordinate list for overlay
        coords = [(t.x, t.y, t.tile_x_idx, t.tile_y_idx) for t in self._tiles]

        # Convert tiles to TileResult format for ImagePanel
        tile_results = self._mip_tiles_to_tile_results(self._tiles)

        # Display in left panel with stride info for correct grid lines
        self._left_panel.set_image(stitched, tiles_x, tiles_y)
        self._left_panel.set_tile_stride(stride_x, stride_y, tile_w, tile_h)
        self._left_panel.set_tile_coordinates(coords, invert_x=self._config.invert_x)
        self._left_panel.set_tile_results(tile_results)

        logger.info(
            f"Stitched {len(self._tiles)} tiles into {stitched_w}x{stitched_h} overview "
            f"(overlap={self._overlap_spin.value()}%, stride={stride_x}x{stride_y})"
        )

    def _mip_tiles_to_tile_results(
        self, mip_tiles: List[MIPTileResult]
    ) -> List[TileResult]:
        """Convert MIPTileResult list to TileResult list for ImagePanel compatibility."""
        results = []
        for mip_tile in mip_tiles:
            tr = TileResult(
                x=mip_tile.x,
                y=mip_tile.y,
                z=mip_tile.z,
                tile_x_idx=mip_tile.tile_x_idx,
                tile_y_idx=mip_tile.tile_y_idx,
                images={"max_intensity": mip_tile.image},
                rotation_angle=mip_tile.rotation_angle,
                z_stack_min=mip_tile.z_stack_min,
                z_stack_max=mip_tile.z_stack_max,
            )
            results.append(tr)
        return results

    def _update_status(self):
        """Update status bar with current selection info."""
        if not self._tiles:
            self._status_label.setText("No tiles loaded")
            self._info_label.setText("")
            return

        selected = self._left_panel.get_selected_tile_count()
        total = len(self._tiles)
        self._status_label.setText(f"Selected: {selected}/{total} tiles")

        if self._config:
            info = (
                f"Grid: {self._config.tiles_x}x{self._config.tiles_y} | "
                f"Tile size: {self._config.tile_size_pixels}px"
            )
            if self._config.layout_type == "flat":
                info += f" | Layout: flat | Channels: {len(self._config.available_channels)}"
            self._info_label.setText(info)

    def _enable_controls(self, enabled: bool):
        """Enable or disable controls based on load state."""
        self._select_all_btn.setEnabled(enabled)
        self._clear_btn.setEnabled(enabled)
        self._auto_select_btn.setEnabled(enabled)
        self._collect_btn.setEnabled(enabled)
        self._fit_btn.setEnabled(enabled)
        self._one_to_one_btn.setEnabled(enabled)
        self._export_btn.setEnabled(enabled)
        self._save_btn.setEnabled(enabled)

    def _on_selection_changed(self):
        """Handle tile selection change."""
        self._update_status()

    def _on_tile_right_clicked(self, tile_x_idx: int, tile_y_idx: int):
        """Handle right-click on tile - move stage to tile position (X, Y, center Z)."""
        logger.info(f"Tile right-clicked: ({tile_x_idx}, {tile_y_idx})")

        if not self._tiles:
            return

        # Find the MIPTileResult for this tile
        target_tile = None
        for tile in self._tiles:
            if tile.tile_x_idx == tile_x_idx and tile.tile_y_idx == tile_y_idx:
                target_tile = tile
                break

        if target_tile is None:
            logger.warning(
                f"Could not find tile ({tile_x_idx}, {tile_y_idx}) in loaded tiles"
            )
            QMessageBox.warning(
                self,
                "Tile Not Found",
                f"Could not find data for tile ({tile_x_idx}, {tile_y_idx}).",
            )
            return

        # Calculate center Z from z_stack range
        z_center = (target_tile.z_stack_min + target_tile.z_stack_max) / 2
        if target_tile.z_stack_min == 0.0 and target_tile.z_stack_max == 0.0:
            # No Z range data, use tile's Z position
            z_center = target_tile.z

        logger.info(
            f"Moving to tile ({tile_x_idx}, {tile_y_idx}): "
            f"X={target_tile.x:.3f}, Y={target_tile.y:.3f}, Z={z_center:.3f} mm "
            f"(Z range: {target_tile.z_stack_min:.3f} - {target_tile.z_stack_max:.3f})"
        )

        # Move stage to tile position
        if (
            self._app
            and hasattr(self._app, "movement_controller")
            and self._app.movement_controller
        ):
            try:
                self._app.movement_controller.move_absolute("x", target_tile.x)
                self._app.movement_controller.move_absolute("y", target_tile.y)
                self._app.movement_controller.move_absolute("z", z_center)
                self._status_label.setText(
                    f"Moving to X={target_tile.x:.3f}, Y={target_tile.y:.3f}, "
                    f"Z={z_center:.3f} mm (tile {tile_x_idx},{tile_y_idx})"
                )
            except Exception as e:
                logger.error(f"Failed to move to tile position: {e}")
                QMessageBox.warning(self, "Move Failed", f"Failed to move stage: {e}")
        else:
            logger.warning("Movement controller not available")
            QMessageBox.warning(
                self,
                "Not Connected",
                "Cannot move stage - not connected to microscope.",
            )

    def _on_select_all(self):
        """Select all tiles."""
        self._left_panel.select_all_tiles()
        self._update_status()

    def _on_clear_selection(self):
        """Clear all selections."""
        self._left_panel.clear_selection()
        self._update_status()

    def _on_auto_select(self):
        """Open auto-select thresholder dialog."""
        if self._stitched_image is None or self._config is None:
            return

        try:
            from .overview_thresholder_dialog import OverviewThresholderDialog
        except ImportError:
            QMessageBox.warning(
                self, "Not Available", "Auto-select thresholder is not available."
            )
            return

        dialog = OverviewThresholderDialog(
            image=self._stitched_image,
            tiles_x=self._config.tiles_x,
            tiles_y=self._config.tiles_y,
            parent=self,
        )

        def apply_selection(selected_tiles: set):
            """Apply selection from thresholder dialog."""
            self._left_panel.clear_selection()
            for tile_x, tile_y in selected_tiles:
                self._left_panel._selected_tiles.add((tile_x, tile_y))
            self._left_panel._redraw_overlay()
            self._update_status()
            logger.info(f"Auto-select applied: {len(selected_tiles)} tiles")

        dialog.selection_ready.connect(apply_selection)
        dialog.exec_()

    def _on_collect_tiles(self):
        """Open TileCollectionDialog with selected tiles."""
        selected_tr = self._left_panel.get_selected_tiles()
        if not selected_tr:
            QMessageBox.information(
                self, "No Selection", "Please select tiles to collect."
            )
            return

        logger.info(f"Collecting {len(selected_tr)} selected tiles")

        try:
            from .tile_collection_dialog import TileCollectionDialog
        except ImportError as e:
            QMessageBox.warning(
                self, "Not Available", f"Tile Collection Dialog is not available:\n{e}"
            )
            return

        # Launch TileCollectionDialog with selected tiles
        dialog = TileCollectionDialog(
            left_tiles=selected_tr,
            right_tiles=[],
            left_rotation=self._config.rotation_angle if self._config else 0.0,
            right_rotation=self._config.rotation_angle if self._config else 0.0,
            config=None,  # No ScanConfiguration for MIP overview
            app=self._app,
            parent=self,
            local_base_folder=(
                str(self._config.base_folder.parent) if self._config else None
            ),
        )

        # Connect to completion signal to reload results
        dialog.accepted.connect(self._on_collection_complete)

        dialog.exec_()

    def _on_collection_complete(self):
        """Handle completion of tile collection - reload new MIPs."""
        logger.info("Tile collection completed - could reload results")
        # TODO: Reload new MIP files into right panel
        # This would require knowing the save directory from the collection

    def _on_fit(self):
        """Fit images to view."""
        self._left_panel._fit_to_view()
        self._right_panel._fit_to_view()

    def _on_one_to_one(self):
        """Reset to 1:1 zoom."""
        self._left_panel._reset_zoom()
        self._right_panel._reset_zoom()

    def _on_save_session(self):
        """Save current session to folder (Zarr if available, TIFF fallback)."""
        if not self._tiles or not self._config:
            QMessageBox.warning(self, "Nothing to Save", "No tiles loaded to save.")
            return

        # Determine default save location
        # Priority: 1) User's saved preference, 2) MIPOverviewSession in project folder
        default_folder = None

        # Check for user's saved preference via configuration service
        if self._app and hasattr(self._app, "config_service"):
            saved_path = self._app.config_service.get_mip_session_path()
            if saved_path and Path(saved_path).exists():
                default_folder = saved_path

        # Fall back to default MIPOverviewSession folder in project root
        if not default_folder:
            # Get project root (parent of src directory)
            project_root = Path(__file__).parent.parent.parent.parent.parent
            default_session_folder = project_root / "MIPOverviewSession"
            # Create it if it doesn't exist
            try:
                default_session_folder.mkdir(parents=True, exist_ok=True)
                default_folder = str(default_session_folder)
            except Exception as e:
                logger.warning(f"Could not create default session folder: {e}")
                default_folder = str(Path.home())

        # Ask for save location
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Folder to Save Session",
            default_folder,
            QFileDialog.ShowDirsOnly,
        )
        if not folder:
            return

        # Remember user's choice for future sessions
        if self._app and hasattr(self._app, "config_service"):
            self._app.config_service.set_mip_session_path(folder)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        metadata = {
            "version": "1.0",
            "saved_at": datetime.now().isoformat(),
            "config": self._config.to_dict(),
            "tiles": [t.to_dict() for t in self._tiles],
        }

        if ZARR_AVAILABLE:
            save_path = Path(folder) / f"mip_overview_{timestamp}.zarr"
            try:
                images = {}
                if self._stitched_image is not None:
                    images["stitched_overview"] = self._stitched_image
                save_2d_zarr_session(save_path, metadata, images, "mip_overview")
            except Exception as e:
                QMessageBox.critical(
                    self, "Error", f"Failed to save zarr session:\n{e}"
                )
                return
        else:
            save_path = Path(folder) / f"mip_overview_{timestamp}"
            self._save_session_tiff(save_path, metadata)

        QMessageBox.information(
            self, "Session Saved", f"Session saved to:\n{save_path}"
        )
        logger.info(f"MIP overview session saved to {save_path}")

    def _save_session_tiff(self, save_path: Path, metadata: dict):
        """TIFF fallback for session save when zarr is unavailable."""
        try:
            save_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create folder:\n{e}")
            return

        metadata_path = save_path / "metadata.json"
        try:
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save metadata:\n{e}")
            return

        if self._stitched_image is not None:
            overview_path = save_path / "stitched_overview.tif"
            try:
                tifffile.imwrite(str(overview_path), self._stitched_image)
            except Exception as e:
                logger.error(f"Failed to save stitched image: {e}")

    def _on_load_session(self):
        """Load a previously saved MIP overview session (Zarr or TIFF)."""
        # Determine default browse location (same as save session path)
        default_folder = str(Path.home())
        if self._app and hasattr(self._app, "config_service"):
            saved_path = self._app.config_service.get_mip_session_path()
            if saved_path and Path(saved_path).exists():
                default_folder = saved_path
            else:
                # Fall back to default MIPOverviewSession folder
                project_root = Path(__file__).parent.parent.parent.parent.parent
                default_session_folder = project_root / "MIPOverviewSession"
                if default_session_folder.exists():
                    default_folder = str(default_session_folder)

        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Saved Session Folder",
            default_folder,
            QFileDialog.ShowDirsOnly,
        )
        if not folder:
            return

        folder_path = Path(folder)
        fmt = detect_session_format(folder_path)

        if fmt == "zarr":
            try:
                metadata, zarr_root = load_2d_zarr_session_lazy(folder_path)
                # Only load the single overview dataset
                stitched_image = None
                if "stitched_overview" in zarr_root:
                    stitched_image = np.array(zarr_root["stitched_overview"])
            except Exception as e:
                QMessageBox.critical(
                    self, "Load Error", f"Failed to load zarr session:\n{e}"
                )
                return

            config = MIPOverviewConfig.from_dict(metadata["config"])
            tiles = []
            for tile_data in metadata.get("tiles", []):
                tiles.append(MIPTileResult.from_dict(tile_data))

        elif fmt == "tiff":
            metadata_path = folder_path / "metadata.json"
            try:
                with open(metadata_path, "r") as f:
                    metadata = json.load(f)
            except Exception as e:
                QMessageBox.critical(
                    self, "Load Error", f"Failed to read metadata:\n{e}"
                )
                return

            config = MIPOverviewConfig.from_dict(metadata["config"])

            overview_path = folder_path / "stitched_overview.tif"
            stitched_image = None
            if overview_path.exists():
                try:
                    stitched_image = tifffile.imread(str(overview_path))
                except Exception as e:
                    QMessageBox.critical(
                        self, "Load Error", f"Failed to load overview image:\n{e}"
                    )
                    return
            else:
                QMessageBox.warning(
                    self,
                    "Incomplete Session",
                    f"No stitched_overview.tif found in:\n{folder}\n\n"
                    "The session may be corrupted.",
                )
                return

            tiles = []
            for tile_data in metadata.get("tiles", []):
                tiles.append(MIPTileResult.from_dict(tile_data))
        else:
            QMessageBox.warning(
                self,
                "Invalid Session Folder",
                f"No valid session found in:\n{folder}\n\n"
                "Please select a folder created by 'Save Session'.",
            )
            return

        # Apply to current dialog
        self._config = config
        self._tiles = tiles
        self._stitched_image = stitched_image

        if stitched_image is not None:
            coords = [(t.x, t.y, t.tile_x_idx, t.tile_y_idx) for t in tiles]
            tile_results = self._mip_tiles_to_tile_results(tiles)

            self._left_panel.set_image(stitched_image, config.tiles_x, config.tiles_y)
            self._left_panel.set_tile_coordinates(coords, invert_x=config.invert_x)
            self._left_panel.set_tile_results(tile_results)

        self._folder_edit.setText(f"[Session] {folder_path.name}")
        self._update_status()
        self._enable_controls(True)

        logger.info(f"Loaded MIP overview session from {folder_path}")
        QMessageBox.information(
            self,
            "Session Loaded",
            f"Loaded session with {len(tiles)} tiles "
            f"({config.tiles_x}x{config.tiles_y} grid).",
        )

    @classmethod
    def load_from_folder(
        cls, folder: Path, app=None, parent=None
    ) -> Optional["MIPOverviewDialog"]:
        """Load a saved MIP overview session from folder (Zarr or TIFF).

        Args:
            folder: Path to saved session folder
            app: FlamingoApplication instance
            parent: Parent widget

        Returns:
            MIPOverviewDialog instance, or None if load failed
        """
        folder = Path(folder)
        fmt = detect_session_format(folder)

        if fmt == "zarr":
            try:
                metadata, zarr_root = load_2d_zarr_session_lazy(folder)
                # Only load the single overview dataset
                stitched_image = None
                if "stitched_overview" in zarr_root:
                    stitched_image = np.array(zarr_root["stitched_overview"])
            except Exception as e:
                logger.error(f"Failed to load zarr session: {e}")
                return None

            config = MIPOverviewConfig.from_dict(metadata["config"])
            tiles = []
            for tile_data in metadata.get("tiles", []):
                tiles.append(MIPTileResult.from_dict(tile_data))

        elif fmt == "tiff":
            metadata_path = folder / "metadata.json"
            try:
                with open(metadata_path, "r") as f:
                    metadata = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load metadata: {e}")
                return None

            config = MIPOverviewConfig.from_dict(metadata["config"])

            overview_path = folder / "stitched_overview.tif"
            stitched_image = None
            if overview_path.exists():
                try:
                    stitched_image = tifffile.imread(str(overview_path))
                except Exception as e:
                    logger.error(f"Failed to load stitched image: {e}")

            tiles = []
            for tile_data in metadata.get("tiles", []):
                tiles.append(MIPTileResult.from_dict(tile_data))
        else:
            logger.error(f"No valid session found in {folder}")
            return None

        # Create dialog
        dialog = cls(app=app, parent=parent)
        dialog._config = config
        dialog._tiles = tiles
        dialog._stitched_image = stitched_image

        if stitched_image is not None:
            coords = [(t.x, t.y, t.tile_x_idx, t.tile_y_idx) for t in tiles]
            tile_results = dialog._mip_tiles_to_tile_results(tiles)

            dialog._left_panel.set_image(stitched_image, config.tiles_x, config.tiles_y)
            dialog._left_panel.set_tile_coordinates(coords, invert_x=config.invert_x)
            dialog._left_panel.set_tile_results(tile_results)

        dialog._folder_edit.setText(str(config.base_folder))
        dialog._update_status()
        dialog._enable_controls(True)

        logger.info(f"Loaded MIP overview session from {folder}")
        return dialog
