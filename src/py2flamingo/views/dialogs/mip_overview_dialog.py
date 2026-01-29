# src/py2flamingo/views/dialogs/mip_overview_dialog.py
"""MIP Overview Dialog.

Loads and displays Maximum Intensity Projection (MIP) images from saved
tile acquisitions, allowing users to select tiles for re-acquisition.

The dialog supports:
- Loading MIP files from folder structure: base/date/X{x}_Y{y}/*_MP.tif
- Dual panel display: original MIPs (left) and new acquisition results (right)
- Click-to-select tile interface
- Auto-select using variance/edge detection
- Integration with TileCollectionDialog for re-acquisition
- Session save/load for persistence
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

import numpy as np
import tifffile

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSplitter, QGroupBox, QFileDialog, QMessageBox,
    QComboBox, QProgressDialog, QLineEdit, QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QIcon

from py2flamingo.models.mip_overview import (
    MIPTileResult, MIPOverviewConfig,
    parse_coords_from_folder, calculate_grid_indices,
    find_date_folders, find_tile_folders, load_invert_x_setting,
)
from py2flamingo.workflows.led_2d_overview_workflow import TileResult

# Import ImagePanel from LED 2D Overview (reuse UI components)
from .led_2d_overview_result import ImagePanel


logger = logging.getLogger(__name__)


class MIPOverviewDialog(QDialog):
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

        self.setWindowTitle("MIP Overview")
        self.setWindowIcon(QIcon())  # Clear inherited napari icon
        self.setMinimumSize(1200, 800)
        self._setup_ui()

    def _setup_ui(self):
        """Set up the dialog UI."""
        layout = QVBoxLayout()
        layout.setSpacing(8)

        # Folder browser section
        folder_group = QGroupBox("Load MIP Files")
        folder_layout = QHBoxLayout()

        folder_layout.addWidget(QLabel("Folder:"))
        self._folder_edit = QLineEdit()
        self._folder_edit.setPlaceholderText("Select folder containing tile acquisitions...")
        self._folder_edit.setReadOnly(True)
        folder_layout.addWidget(self._folder_edit, stretch=1)

        self._browse_btn = QPushButton("Browse...")
        self._browse_btn.clicked.connect(self._on_browse)
        folder_layout.addWidget(self._browse_btn)

        folder_layout.addWidget(QLabel("Date:"))
        self._date_combo = QComboBox()
        self._date_combo.setMinimumWidth(120)
        self._date_combo.currentIndexChanged.connect(self._on_date_changed)
        folder_layout.addWidget(self._date_combo)

        self._load_btn = QPushButton("Load")
        self._load_btn.setEnabled(False)
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

        self._save_btn = QPushButton("Save Session")
        self._save_btn.clicked.connect(self._on_save_session)
        self._save_btn.setEnabled(False)
        button_layout.addWidget(self._save_btn)

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
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder with Tile Acquisitions",
            str(Path.home())
        )
        if folder:
            self._folder_edit.setText(folder)
            self._update_date_combo(Path(folder))

    def _update_date_combo(self, base_path: Path):
        """Update date combo box with available date folders."""
        self._date_combo.clear()
        date_folders = find_date_folders(base_path)

        if date_folders:
            self._date_combo.addItems(date_folders)
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
        else:
            load_path = base_path / date_text
            date_folder = date_text

        # Find tile folders
        tile_folders = find_tile_folders(load_path)
        if not tile_folders:
            QMessageBox.warning(
                self, "No Tiles Found",
                f"No tile folders (X*_Y*) found in:\n{load_path}"
            )
            return

        # Load tiles with progress dialog
        progress = QProgressDialog("Loading MIP files...", "Cancel", 0, len(tile_folders), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        tiles = []
        skipped = 0

        for i, tile_folder in enumerate(tile_folders):
            if progress.wasCanceled():
                return

            progress.setValue(i)
            progress.setLabelText(f"Loading {tile_folder.name}...")

            # Parse coordinates from folder name
            try:
                x, y = parse_coords_from_folder(tile_folder.name)
            except ValueError as e:
                logger.warning(f"Skipping folder {tile_folder.name}: {e}")
                skipped += 1
                continue

            # Find MIP file
            mip_files = list(tile_folder.glob("*_MP.tif"))
            if not mip_files:
                logger.warning(f"No *_MP.tif file in {tile_folder}")
                skipped += 1
                continue

            # Load first MIP file found
            mip_file = mip_files[0]
            try:
                image = tifffile.imread(str(mip_file))
                logger.debug(f"Loaded {mip_file.name}: shape={image.shape}, dtype={image.dtype}")
            except Exception as e:
                logger.error(f"Failed to load {mip_file}: {e}")
                skipped += 1
                continue

            tile = MIPTileResult(
                x=x, y=y, z=0.0,
                tile_x_idx=0, tile_y_idx=0,  # Will be calculated later
                image=image,
                folder_path=tile_folder,
            )
            tiles.append(tile)

        progress.setValue(len(tile_folders))

        if not tiles:
            QMessageBox.warning(
                self, "No MIPs Loaded",
                f"Failed to load any MIP files from:\n{load_path}\n\n"
                f"Skipped {skipped} folders."
            )
            return

        # Calculate grid indices
        calculate_grid_indices(tiles)

        # Get grid dimensions
        tiles_x = max(t.tile_x_idx for t in tiles) + 1
        tiles_y = max(t.tile_y_idx for t in tiles) + 1

        # Get tile size from first image
        tile_size = tiles[0].image.shape[0]  # Assume square tiles

        # Create config (load axis inversion setting from visualization config)
        self._config = MIPOverviewConfig(
            base_folder=base_path,
            date_folder=date_folder,
            tiles_x=tiles_x,
            tiles_y=tiles_y,
            tile_size_pixels=tile_size,
            downsample_factor=4,
            invert_x=load_invert_x_setting(),
        )

        self._tiles = tiles

        # Stitch overview
        self._stitch_and_display()

        # Update UI
        self._update_status()
        self._enable_controls(True)

        # Show load summary
        if skipped > 0:
            QMessageBox.information(
                self, "Load Complete",
                f"Loaded {len(tiles)} tiles ({tiles_x}x{tiles_y} grid).\n"
                f"Skipped {skipped} folders (no MIP or invalid name)."
            )

    def _stitch_and_display(self):
        """Stitch loaded MIP tiles into overview and display."""
        if not self._tiles or not self._config:
            return

        tiles_x = self._config.tiles_x
        tiles_y = self._config.tiles_y
        downsample = self._config.downsample_factor

        # Determine tile size after downsampling
        sample_tile = self._tiles[0].image
        if len(sample_tile.shape) == 2:
            orig_h, orig_w = sample_tile.shape
        else:
            orig_h, orig_w = sample_tile.shape[:2]

        tile_h = orig_h // downsample
        tile_w = orig_w // downsample

        # Create stitched image
        stitched_h = tiles_y * tile_h
        stitched_w = tiles_x * tile_w

        # Use same dtype as source
        dtype = sample_tile.dtype
        stitched = np.zeros((stitched_h, stitched_w), dtype=dtype)

        # Place tiles
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
                cropped = tile_img[:new_h * downsample, :new_w * downsample]
                # Reshape and mean
                reshaped = cropped.reshape(new_h, downsample, new_w, downsample)
                downsampled = reshaped.mean(axis=(1, 3)).astype(dtype)
            else:
                downsampled = tile.image

            # Calculate position (invert X if needed to match stage orientation)
            if self._config.invert_x:
                # Invert: tile_x_idx=0 goes on right, tile_x_idx=max goes on left
                inverted_x_idx = (tiles_x - 1) - tile.tile_x_idx
                x_pos = inverted_x_idx * tile_w
            else:
                x_pos = tile.tile_x_idx * tile_w
            y_pos = tile.tile_y_idx * tile_h

            # Place in stitched image
            dh, dw = downsampled.shape[:2]
            stitched[y_pos:y_pos + dh, x_pos:x_pos + dw] = downsampled

        self._stitched_image = stitched

        # Build coordinate list for overlay
        coords = [(t.x, t.y, t.tile_x_idx, t.tile_y_idx) for t in self._tiles]

        # Convert tiles to TileResult format for ImagePanel
        tile_results = self._mip_tiles_to_tile_results(self._tiles)

        # Display in left panel (use invert_x from config for correct axis orientation)
        self._left_panel.set_image(stitched, tiles_x, tiles_y)
        self._left_panel.set_tile_coordinates(coords, invert_x=self._config.invert_x)
        self._left_panel.set_tile_results(tile_results)

        logger.info(f"Stitched {len(self._tiles)} tiles into {stitched_w}x{stitched_h} overview")

    def _mip_tiles_to_tile_results(self, mip_tiles: List[MIPTileResult]) -> List[TileResult]:
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
            self._info_label.setText(
                f"Grid: {self._config.tiles_x}x{self._config.tiles_y} | "
                f"Tile size: {self._config.tile_size_pixels}px"
            )

    def _enable_controls(self, enabled: bool):
        """Enable or disable controls based on load state."""
        self._select_all_btn.setEnabled(enabled)
        self._clear_btn.setEnabled(enabled)
        self._auto_select_btn.setEnabled(enabled)
        self._collect_btn.setEnabled(enabled)
        self._fit_btn.setEnabled(enabled)
        self._one_to_one_btn.setEnabled(enabled)
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
            logger.warning(f"Could not find tile ({tile_x_idx}, {tile_y_idx}) in loaded tiles")
            QMessageBox.warning(self, "Tile Not Found",
                              f"Could not find data for tile ({tile_x_idx}, {tile_y_idx}).")
            return

        # Calculate center Z from z_stack range
        z_center = (target_tile.z_stack_min + target_tile.z_stack_max) / 2
        if target_tile.z_stack_min == 0.0 and target_tile.z_stack_max == 0.0:
            # No Z range data, use tile's Z position
            z_center = target_tile.z

        logger.info(f"Moving to tile ({tile_x_idx}, {tile_y_idx}): "
                    f"X={target_tile.x:.3f}, Y={target_tile.y:.3f}, Z={z_center:.3f} mm "
                    f"(Z range: {target_tile.z_stack_min:.3f} - {target_tile.z_stack_max:.3f})")

        # Move stage to tile position
        if self._app and hasattr(self._app, 'movement_controller') and self._app.movement_controller:
            try:
                self._app.movement_controller.move_absolute('x', target_tile.x)
                self._app.movement_controller.move_absolute('y', target_tile.y)
                self._app.movement_controller.move_absolute('z', z_center)
                self._status_label.setText(
                    f"Moving to X={target_tile.x:.3f}, Y={target_tile.y:.3f}, "
                    f"Z={z_center:.3f} mm (tile {tile_x_idx},{tile_y_idx})")
            except Exception as e:
                logger.error(f"Failed to move to tile position: {e}")
                QMessageBox.warning(self, "Move Failed",
                                  f"Failed to move stage: {e}")
        else:
            logger.warning("Movement controller not available")
            QMessageBox.warning(self, "Not Connected",
                              "Cannot move stage - not connected to microscope.")

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
                self, "Not Available",
                "Auto-select thresholder is not available."
            )
            return

        dialog = OverviewThresholderDialog(
            image=self._stitched_image,
            tiles_x=self._config.tiles_x,
            tiles_y=self._config.tiles_y,
            parent=self
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
                self, "No Selection",
                "Please select tiles to collect."
            )
            return

        logger.info(f"Collecting {len(selected_tr)} selected tiles")

        try:
            from .tile_collection_dialog import TileCollectionDialog
        except ImportError as e:
            QMessageBox.warning(
                self, "Not Available",
                f"Tile Collection Dialog is not available:\n{e}"
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
        """Save current session to folder."""
        if not self._tiles or not self._config:
            QMessageBox.warning(self, "Nothing to Save", "No tiles loaded to save.")
            return

        # Determine default save location
        # Priority: 1) User's saved preference, 2) MIPOverviewSession in project folder
        default_folder = None

        # Check for user's saved preference via configuration service
        if self._app and hasattr(self._app, 'configuration_service'):
            saved_path = self._app.configuration_service.get_mip_session_path()
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
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"mip_overview_{timestamp}"

        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder to Save Session",
            default_folder,
            QFileDialog.ShowDirsOnly
        )
        if not folder:
            return

        # Remember user's choice for future sessions
        if self._app and hasattr(self._app, 'configuration_service'):
            self._app.configuration_service.set_mip_session_path(folder)

        save_path = Path(folder) / default_name
        try:
            save_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create folder:\n{e}")
            return

        # Save metadata
        metadata = {
            "version": "1.0",
            "saved_at": datetime.now().isoformat(),
            "config": self._config.to_dict(),
            "tiles": [t.to_dict() for t in self._tiles],
        }

        metadata_path = save_path / "metadata.json"
        try:
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save metadata:\n{e}")
            return

        # Save stitched image
        if self._stitched_image is not None:
            overview_path = save_path / "stitched_overview.tif"
            try:
                tifffile.imwrite(str(overview_path), self._stitched_image)
            except Exception as e:
                logger.error(f"Failed to save stitched image: {e}")

        QMessageBox.information(
            self, "Session Saved",
            f"Session saved to:\n{save_path}"
        )
        logger.info(f"MIP overview session saved to {save_path}")

    @classmethod
    def load_from_folder(cls, folder: Path, app=None, parent=None) -> Optional['MIPOverviewDialog']:
        """Load a saved MIP overview session from folder.

        Args:
            folder: Path to saved session folder
            app: FlamingoApplication instance
            parent: Parent widget

        Returns:
            MIPOverviewDialog instance, or None if load failed
        """
        metadata_path = folder / "metadata.json"
        if not metadata_path.exists():
            logger.error(f"No metadata.json found in {folder}")
            return None

        try:
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load metadata: {e}")
            return None

        # Load config
        config = MIPOverviewConfig.from_dict(metadata['config'])

        # Load stitched image
        overview_path = folder / "stitched_overview.tif"
        stitched_image = None
        if overview_path.exists():
            try:
                stitched_image = tifffile.imread(str(overview_path))
            except Exception as e:
                logger.error(f"Failed to load stitched image: {e}")

        # Reconstruct tiles from metadata (without full images)
        tiles = []
        for tile_data in metadata.get('tiles', []):
            tile = MIPTileResult.from_dict(tile_data)
            tiles.append(tile)

        # Create dialog
        dialog = cls(app=app, parent=parent)
        dialog._config = config
        dialog._tiles = tiles
        dialog._stitched_image = stitched_image

        if stitched_image is not None:
            # Build coordinate list
            coords = [(t.x, t.y, t.tile_x_idx, t.tile_y_idx) for t in tiles]

            # Convert tiles to TileResult format
            tile_results = dialog._mip_tiles_to_tile_results(tiles)

            # Display (use invert_x from loaded config for correct axis orientation)
            dialog._left_panel.set_image(stitched_image, config.tiles_x, config.tiles_y)
            dialog._left_panel.set_tile_coordinates(coords, invert_x=config.invert_x)
            dialog._left_panel.set_tile_results(tile_results)

        dialog._folder_edit.setText(str(config.base_folder))
        dialog._update_status()
        dialog._enable_controls(True)

        logger.info(f"Loaded MIP overview session from {folder}")
        return dialog
