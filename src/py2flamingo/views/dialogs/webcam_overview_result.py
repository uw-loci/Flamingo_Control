"""Webcam Overview Result Viewer.

Displays captured webcam angle views with grid overlay and interactive
tile selection. Supports side-by-side display for 2 views or tabbed
display for 3+ views. Integrates with TileCollectionDialog for
acquisition workflow creation.
"""

import logging
import math
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from PyQt5.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QAction,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from py2flamingo.models.data.webcam_models import (
    WebcamAngleView,
    WebcamSession,
    WebcamTileSelection,
)
from py2flamingo.services.webcam_calibration_service import (
    WebcamCalibrationService,
)
from py2flamingo.services.window_geometry_manager import PersistentWidget

logger = logging.getLogger(__name__)


class WebcamImagePanel(QWidget):
    """Widget displaying a webcam image with grid overlay and tile selection.

    Features:
    - NxN grid overlay
    - Click to toggle tile selection (green highlight)
    - Shift+drag rectangle multi-select
    - Right-click context menu for go-to-tile
    - Selection order numbered labels
    - Coordinate display on hover
    """

    selection_changed = pyqtSignal()
    tile_right_clicked = pyqtSignal(int, int)  # row, col

    def __init__(
        self,
        title: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._title = title
        self._view: Optional[WebcamAngleView] = None
        self._calibration_service: Optional[WebcamCalibrationService] = None

        # Image data
        self._image: Optional[np.ndarray] = None
        self._pixmap: Optional[QPixmap] = None

        # Grid
        self._grid_rows = 20
        self._grid_cols = 20

        # Selection state
        self._selected_tiles: Set[Tuple[int, int]] = set()  # (row, col)
        self._selection_order: List[Tuple[int, int]] = []
        self._next_order = 1

        # Mouse state for drag selection
        self._dragging = False
        self._drag_start: Optional[QPoint] = None
        self._drag_rect: Optional[QRect] = None

        # Contrast
        self._contrast_min = 0
        self._contrast_max = 255

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # Title
        self._title_label = QLabel(self._title)
        self._title_label.setAlignment(Qt.AlignCenter)
        self._title_label.setStyleSheet("font-weight: bold; font-size: 11pt;")
        layout.addWidget(self._title_label)

        # Hint
        hint = QLabel(
            "Click tile to select, Shift+drag for area, Right-click for go-to"
        )
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #888; font-size: 9pt; font-style: italic;")
        layout.addWidget(hint)

        # Scroll area with image
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setAlignment(Qt.AlignCenter)
        self._scroll_area.setMinimumSize(200, 200)
        self._scroll_area.setStyleSheet("background-color: #2a2a2a;")

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._image_label.setMouseTracking(True)
        self._image_label.mousePressEvent = self._on_mouse_press
        self._image_label.mouseReleaseEvent = self._on_mouse_release
        self._image_label.mouseMoveEvent = self._on_mouse_move
        self._image_label.setContextMenuPolicy(Qt.CustomContextMenu)
        self._image_label.customContextMenuRequested.connect(self._on_context_menu)

        self._scroll_area.setWidget(self._image_label)
        layout.addWidget(self._scroll_area, stretch=1)

        # Contrast controls
        contrast_layout = QHBoxLayout()
        contrast_layout.addWidget(QLabel("Brightness:"), alignment=Qt.AlignRight)
        self._brightness_slider = QSlider(Qt.Horizontal)
        self._brightness_slider.setRange(0, 255)
        self._brightness_slider.setValue(0)
        self._brightness_slider.setMaximumWidth(150)
        self._brightness_slider.valueChanged.connect(self._on_contrast_changed)
        contrast_layout.addWidget(self._brightness_slider)

        contrast_layout.addStretch()

        # Coordinate display
        self._coord_label = QLabel("")
        self._coord_label.setStyleSheet("color: #aaa; font-size: 9pt;")
        contrast_layout.addWidget(self._coord_label)

        layout.addLayout(contrast_layout)

        # Selection controls
        sel_layout = QHBoxLayout()

        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.clicked.connect(self._on_select_all)
        sel_layout.addWidget(self._select_all_btn)

        self._clear_all_btn = QPushButton("Clear")
        self._clear_all_btn.clicked.connect(self._on_clear_all)
        sel_layout.addWidget(self._clear_all_btn)

        sel_layout.addStretch()

        self._count_label = QLabel("0 tiles selected")
        self._count_label.setStyleSheet("color: #aaa;")
        sel_layout.addWidget(self._count_label)

        layout.addLayout(sel_layout)

    def set_view(
        self,
        view: WebcamAngleView,
        calibration_service: Optional[WebcamCalibrationService] = None,
    ):
        """Set the webcam view to display."""
        self._view = view
        self._image = view.image
        self._grid_rows = view.grid_rows
        self._grid_cols = view.grid_cols
        self._calibration_service = calibration_service
        self._title_label.setText(f"{self._title} (R={view.rotation_angle:.1f}°)")

        # Reset selection
        self._selected_tiles.clear()
        self._selection_order.clear()
        self._next_order = 1

        self._render_image()

    def _render_image(self):
        """Render the image with grid overlay and selections."""
        if self._image is None:
            return

        image = self._image.copy()

        # Apply brightness adjustment
        if self._contrast_min > 0:
            image = np.clip(image.astype(np.int16) + self._contrast_min, 0, 255).astype(
                np.uint8
            )

        # Convert to QPixmap
        h, w = image.shape[:2]
        if image.ndim == 3:
            bytes_per_line = 3 * w
            qimg = QImage(image.data, w, h, bytes_per_line, QImage.Format_RGB888)
        else:
            qimg = QImage(image.data, w, h, w, QImage.Format_Grayscale8)

        pixmap = QPixmap.fromImage(qimg)

        # Draw grid and selections
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        self._draw_grid(painter, w, h)
        self._draw_selections(painter, w, h)

        if self._drag_rect is not None:
            # Draw drag rectangle
            pen = QPen(QColor(255, 255, 0), 2, Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(self._drag_rect)

        painter.end()

        self._pixmap = pixmap
        self._image_label.setPixmap(
            pixmap.scaled(
                self._scroll_area.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    def _draw_grid(self, painter: QPainter, w: int, h: int):
        """Draw the tile grid overlay."""
        pen = QPen(QColor(255, 80, 80, 120), 1)
        painter.setPen(pen)

        # Vertical lines
        for col in range(self._grid_cols + 1):
            x = int(col * w / self._grid_cols)
            painter.drawLine(x, 0, x, h)

        # Horizontal lines
        for row in range(self._grid_rows + 1):
            y = int(row * h / self._grid_rows)
            painter.drawLine(0, y, w, y)

    def _draw_selections(self, painter: QPainter, w: int, h: int):
        """Draw selected tile highlights and order numbers."""
        cell_w = w / self._grid_cols
        cell_h = h / self._grid_rows

        for row, col in self._selected_tiles:
            x = int(col * cell_w)
            y = int(row * cell_h)
            cw = int(cell_w)
            ch = int(cell_h)

            # Green semi-transparent fill
            painter.fillRect(x, y, cw, ch, QColor(0, 200, 100, 77))

            # Cyan border
            pen = QPen(QColor(0, 220, 220), 2)
            painter.setPen(pen)
            painter.drawRect(x, y, cw, ch)

        # Draw selection order numbers
        font = QFont("Arial", max(8, int(min(cell_w, cell_h) * 0.3)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor(50, 100, 255)))

        for tile in self._selection_order:
            if tile in self._selected_tiles:
                row, col = tile
                idx = self._selection_order.index(tile) + 1
                cx = int((col + 0.5) * cell_w)
                cy = int((row + 0.5) * cell_h)
                painter.drawText(cx - 10, cy + 5, str(idx))

    def _pixel_to_grid(self, label_x: int, label_y: int) -> Optional[Tuple[int, int]]:
        """Convert label pixel position to grid (row, col)."""
        if self._image is None:
            return None

        displayed = self._image_label.pixmap()
        if displayed is None or displayed.isNull():
            return None

        img_h, img_w = self._image.shape[:2]
        disp_w = displayed.width()
        disp_h = displayed.height()

        # Account for centering in the label
        label_w = self._image_label.width()
        label_h = self._image_label.height()
        x_off = (label_w - disp_w) / 2
        y_off = (label_h - disp_h) / 2

        # Position relative to displayed image
        px = label_x - x_off
        py = label_y - y_off

        if px < 0 or py < 0 or px >= disp_w or py >= disp_h:
            return None

        # Map to original image coordinates
        orig_x = px / disp_w * img_w
        orig_y = py / disp_h * img_h

        col = int(orig_x / img_w * self._grid_cols)
        row = int(orig_y / img_h * self._grid_rows)

        col = max(0, min(col, self._grid_cols - 1))
        row = max(0, min(row, self._grid_rows - 1))

        return (row, col)

    def _pixel_to_image_coords(
        self, label_x: int, label_y: int
    ) -> Optional[Tuple[float, float]]:
        """Convert label pixel to original image pixel coordinates."""
        if self._image is None:
            return None

        displayed = self._image_label.pixmap()
        if displayed is None or displayed.isNull():
            return None

        img_h, img_w = self._image.shape[:2]
        disp_w = displayed.width()
        disp_h = displayed.height()

        label_w = self._image_label.width()
        label_h = self._image_label.height()
        x_off = (label_w - disp_w) / 2
        y_off = (label_h - disp_h) / 2

        px = label_x - x_off
        py = label_y - y_off

        if px < 0 or py < 0 or px >= disp_w or py >= disp_h:
            return None

        orig_x = px / disp_w * img_w
        orig_y = py / disp_h * img_h
        return (orig_x, orig_y)

    # ========== Mouse Events ==========

    def _on_mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            if event.modifiers() & Qt.ShiftModifier:
                # Start drag selection
                self._dragging = True
                self._drag_start = event.pos()
                self._drag_rect = None
            else:
                # Toggle single tile
                grid = self._pixel_to_grid(event.x(), event.y())
                if grid:
                    self._toggle_tile(grid)

    def _on_mouse_release(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            if self._drag_rect is not None:
                self._select_tiles_in_rect()
            self._drag_rect = None
            self._render_image()

    def _on_mouse_move(self, event):
        # Update coordinate display
        img_coords = self._pixel_to_image_coords(event.x(), event.y())
        if img_coords:
            u, v = img_coords
            text = f"Pixel: ({u:.0f}, {v:.0f})"

            # Show stage coordinates if calibrated
            if (
                self._calibration_service
                and self._view
                and self._calibration_service.is_calibrated(self._view.rotation_angle)
            ):
                try:
                    h, sv = self._calibration_service.pixel_to_stage(
                        u, v, self._view.rotation_angle
                    )
                    angle = self._view.rotation_angle
                    if abs(angle) < 1:
                        text += f"  Stage: X={h:.3f} Y={sv:.3f} mm"
                    elif abs(angle - 90) < 1:
                        text += f"  Stage: Z={h:.3f} Y={sv:.3f} mm"
                    else:
                        text += f"  Stage: H={h:.3f} Y={sv:.3f} mm"
                except Exception:
                    text += "  [Uncalibrated]"
            else:
                text += "  [Uncalibrated]"
            self._coord_label.setText(text)
        else:
            self._coord_label.setText("")

        # Update drag rectangle
        if self._dragging and self._drag_start:
            self._drag_rect = QRect(self._drag_start, event.pos()).normalized()
            self._render_image()

    def _on_context_menu(self, pos):
        """Show context menu on right-click."""
        grid = self._pixel_to_grid(pos.x(), pos.y())
        if grid is None:
            return

        row, col = grid
        menu = QMenu(self)

        go_to_action = menu.addAction(f"Go to tile ({row}, {col})")
        go_to_action.triggered.connect(lambda: self.tile_right_clicked.emit(row, col))

        select_action = menu.addAction(
            "Deselect" if grid in self._selected_tiles else "Select"
        )
        select_action.triggered.connect(lambda: self._toggle_tile(grid))

        menu.exec_(self._image_label.mapToGlobal(pos))

    # ========== Tile Selection ==========

    def _toggle_tile(self, tile: Tuple[int, int]):
        """Toggle tile selection."""
        if tile in self._selected_tiles:
            self._selected_tiles.discard(tile)
            if tile in self._selection_order:
                self._selection_order.remove(tile)
        else:
            self._selected_tiles.add(tile)
            self._selection_order.append(tile)

        self._update_count()
        self._render_image()
        self.selection_changed.emit()

    def _select_tiles_in_rect(self):
        """Select all tiles within the drag rectangle."""
        if self._drag_rect is None or self._image is None:
            return

        for row in range(self._grid_rows):
            for col in range(self._grid_cols):
                # Compute tile center in label coordinates
                img_h, img_w = self._image.shape[:2]
                displayed = self._image_label.pixmap()
                if displayed is None:
                    continue

                disp_w = displayed.width()
                disp_h = displayed.height()
                label_w = self._image_label.width()
                label_h = self._image_label.height()
                x_off = (label_w - disp_w) / 2
                y_off = (label_h - disp_h) / 2

                cx = x_off + (col + 0.5) / self._grid_cols * disp_w
                cy = y_off + (row + 0.5) / self._grid_rows * disp_h

                if self._drag_rect.contains(int(cx), int(cy)):
                    tile = (row, col)
                    if tile not in self._selected_tiles:
                        self._selected_tiles.add(tile)
                        self._selection_order.append(tile)

        self._update_count()
        self.selection_changed.emit()

    def _on_select_all(self):
        """Select all tiles."""
        for row in range(self._grid_rows):
            for col in range(self._grid_cols):
                tile = (row, col)
                if tile not in self._selected_tiles:
                    self._selected_tiles.add(tile)
                    self._selection_order.append(tile)
        self._update_count()
        self._render_image()
        self.selection_changed.emit()

    def _on_clear_all(self):
        """Clear all selections."""
        self._selected_tiles.clear()
        self._selection_order.clear()
        self._next_order = 1
        self._update_count()
        self._render_image()
        self.selection_changed.emit()

    def _update_count(self):
        n = len(self._selected_tiles)
        self._count_label.setText(f"{n} tile{'s' if n != 1 else ''} selected")

    def _on_contrast_changed(self, value):
        self._contrast_min = value
        self._render_image()

    # ========== Public API ==========

    def get_selected_tiles(self) -> List[WebcamTileSelection]:
        """Get WebcamTileSelection objects for all selected tiles."""
        if self._view is None:
            return []

        selections = []
        for order_idx, (row, col) in enumerate(self._selection_order):
            if (row, col) not in self._selected_tiles:
                continue

            stage_x = None
            stage_y = None
            stage_z = None

            # Map to stage coordinates if calibrated
            if self._calibration_service and self._view:
                angle = self._view.rotation_angle
                if self._calibration_service.is_calibrated(angle):
                    img_h, img_w = self._image.shape[:2]
                    # Tile center in pixel coordinates
                    u = (col + 0.5) / self._grid_cols * img_w
                    v = (row + 0.5) / self._grid_rows * img_h
                    try:
                        h_stage, y_stage = self._calibration_service.pixel_to_stage(
                            u, v, angle
                        )
                        stage_y = y_stage
                        # Assign to correct axis based on angle
                        r_rad = math.radians(angle)
                        if abs(math.cos(r_rad)) > abs(math.sin(r_rad)):
                            stage_x = h_stage
                        else:
                            stage_z = h_stage
                    except Exception:
                        pass

            selections.append(
                WebcamTileSelection(
                    grid_row=row,
                    grid_col=col,
                    selection_order=order_idx + 1,
                    stage_x_mm=stage_x,
                    stage_y_mm=stage_y,
                    stage_z_mm=stage_z,
                    rotation_angle=self._view.rotation_angle,
                )
            )

        return selections

    def get_selected_tile_results(self) -> list:
        """Get TileResult-compatible objects for TileCollectionDialog.

        Returns list of objects with .x, .y, .z, .tile_x_idx, .tile_y_idx,
        .rotation_angle attributes matching TileResult interface.
        """
        from py2flamingo.models.data.overview_results import TileResult

        tile_results = []
        for sel in self.get_selected_tiles():
            if sel.stage_x_mm is None or sel.stage_y_mm is None:
                continue  # Skip uncalibrated tiles

            tile_results.append(
                TileResult(
                    x=sel.stage_x_mm or 0.0,
                    y=sel.stage_y_mm or 0.0,
                    z=sel.stage_z_mm or 0.0,
                    tile_x_idx=sel.grid_col,
                    tile_y_idx=sel.grid_row,
                    rotation_angle=sel.rotation_angle,
                )
            )

        return tile_results

    def get_image(self) -> Optional[np.ndarray]:
        """Get the raw image array."""
        return self._image


class WebcamOverviewResultWindow(PersistentWidget):
    """Window displaying webcam overview results with tile selection.

    Shows captured angle views side-by-side (2 views) or in tabs (3+).
    Each view has a grid overlay with interactive tile selection.
    """

    def __init__(
        self,
        session: WebcamSession,
        calibration_service: Optional[WebcamCalibrationService] = None,
        app=None,
        parent=None,
    ):
        super().__init__(parent=parent, window_id="WebcamOverviewResult")
        self._session = session
        self._calibration_service = calibration_service
        self._app = app

        self.setWindowTitle("Webcam Overview Results")
        self.setMinimumSize(800, 500)

        self._panels: List[WebcamImagePanel] = []

        self._setup_ui()
        self._load_views()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Content area (splitter or tabs depending on view count)
        self._content_widget = QWidget()
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._content_widget, stretch=1)

        # Action bar
        action_layout = QHBoxLayout()

        self._collect_btn = QPushButton("Collect Tiles")
        self._collect_btn.setToolTip("Create acquisition workflows for selected tiles")
        self._collect_btn.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 6px 16px; }"
        )
        self._collect_btn.clicked.connect(self._on_collect_tiles)
        action_layout.addWidget(self._collect_btn)

        self._export_btn = QPushButton("Export Positions")
        self._export_btn.setToolTip("Export selected tile positions to a text file")
        self._export_btn.clicked.connect(self._on_export_positions)
        action_layout.addWidget(self._export_btn)

        action_layout.addStretch()

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #aaa;")
        action_layout.addWidget(self._status_label)

        layout.addLayout(action_layout)

    def _load_views(self):
        """Create panels for each view in the session."""
        views = self._session.views
        if not views:
            self._status_label.setText("No views to display")
            return

        # Clear previous content
        for panel in self._panels:
            panel.setParent(None)
        self._panels.clear()

        # Remove old content widget children
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

        if len(views) <= 2:
            # Side-by-side splitter
            splitter = QSplitter(Qt.Horizontal)

            for i, view in enumerate(views):
                title = f"View {i + 1}"
                panel = WebcamImagePanel(title=title)
                panel.set_view(view, self._calibration_service)
                panel.selection_changed.connect(self._on_selection_changed)
                panel.tile_right_clicked.connect(
                    lambda r, c, v=view: self._on_go_to_tile(r, c, v)
                )
                splitter.addWidget(panel)
                self._panels.append(panel)

            self._content_layout.addWidget(splitter)

        else:
            # Tabbed view for 3+ views
            tabs = QTabWidget()

            for i, view in enumerate(views):
                title = f"R={view.rotation_angle:.1f}°"
                panel = WebcamImagePanel(title=title)
                panel.set_view(view, self._calibration_service)
                panel.selection_changed.connect(self._on_selection_changed)
                panel.tile_right_clicked.connect(
                    lambda r, c, v=view: self._on_go_to_tile(r, c, v)
                )
                tabs.addTab(panel, title)
                self._panels.append(panel)

            self._content_layout.addWidget(tabs)

        self._update_status()

    def _on_selection_changed(self):
        """Update status when tile selection changes."""
        self._update_status()

    def _update_status(self):
        """Update the status label with selection counts."""
        total = sum(len(p.get_selected_tiles()) for p in self._panels)
        calibrated = sum(len(p.get_selected_tile_results()) for p in self._panels)
        if total > 0:
            status = f"{total} tiles selected"
            if calibrated < total:
                status += f" ({calibrated} with stage coordinates)"
            self._status_label.setText(status)
        else:
            self._status_label.setText("Select tiles for acquisition")

    def _on_go_to_tile(self, row: int, col: int, view: WebcamAngleView):
        """Move stage to the selected tile position."""
        if self._app is None or not self._is_connected():
            QMessageBox.information(
                self,
                "Connection Required",
                "Connect to the microscope to use go-to-tile.",
            )
            return

        if (
            self._calibration_service is None
            or not self._calibration_service.is_calibrated(view.rotation_angle)
        ):
            QMessageBox.information(
                self,
                "Calibration Required",
                "Calibrate the webcam first to use go-to-tile.",
            )
            return

        try:
            img_h, img_w = view.image.shape[:2]
            u = (col + 0.5) / view.grid_cols * img_w
            v = (row + 0.5) / view.grid_rows * img_h

            x, y, z = self._calibration_service.pixel_to_full_stage(
                u, v, view.rotation_angle
            )

            mc = getattr(self._app, "movement_controller", None)
            if mc:
                logger.info(
                    f"Moving to tile ({row},{col}): " f"X={x:.3f} Y={y:.3f} Z={z:.3f}"
                )
                if hasattr(mc, "move_to_position"):
                    from py2flamingo.models.microscope import Position

                    mc.move_to_position(Position(x=x, y=y, z=z, r=view.rotation_angle))
                elif hasattr(mc, "move_xyz"):
                    mc.move_xyz(x, y, z)
        except Exception as e:
            logger.error(f"Error moving to tile: {e}", exc_info=True)
            QMessageBox.warning(self, "Move Error", f"Could not move to tile:\n{e}")

    def _on_collect_tiles(self):
        """Open TileCollectionDialog with selected tiles."""
        # Gather tiles from all panels
        all_tile_results = []
        panel_tiles = {}
        for i, panel in enumerate(self._panels):
            tiles = panel.get_selected_tile_results()
            if tiles:
                panel_tiles[i] = tiles
                all_tile_results.extend(tiles)

        if not all_tile_results:
            QMessageBox.warning(
                self,
                "No Calibrated Selection",
                "No selected tiles have stage coordinates.\n\n"
                "Ensure the webcam is calibrated and tiles are selected.",
            )
            return

        # Map to left/right tiles (first two panels)
        left_tiles = panel_tiles.get(0, [])
        right_tiles = panel_tiles.get(1, [])

        left_rotation = (
            self._session.views[0].rotation_angle
            if len(self._session.views) >= 1
            else 0.0
        )
        right_rotation = (
            self._session.views[1].rotation_angle
            if len(self._session.views) >= 2
            else 90.0
        )

        try:
            from py2flamingo.views.dialogs.tile_collection_dialog import (
                TileCollectionDialog,
            )

            dialog = TileCollectionDialog(
                left_tiles=left_tiles,
                right_tiles=right_tiles,
                left_rotation=left_rotation,
                right_rotation=right_rotation,
                config=None,
                app=self._app,
                parent=self,
            )
            dialog.exec_()

        except Exception as e:
            logger.error(f"Error opening tile collection dialog: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to open tile collection dialog:\n{e}",
            )

    def _on_export_positions(self):
        """Export selected tile positions to a text file."""
        from PyQt5.QtWidgets import QFileDialog

        all_tiles = []
        for panel in self._panels:
            all_tiles.extend(panel.get_selected_tiles())

        if not all_tiles:
            QMessageBox.information(
                self,
                "No Selection",
                "Select at least one tile first.",
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Tile Positions",
            "tile_positions.txt",
            "Text files (*.txt);;CSV files (*.csv);;All files (*)",
        )
        if not path:
            return

        try:
            with open(path, "w") as f:
                f.write(
                    "# Webcam Overview Tile Positions\n"
                    "# order, grid_row, grid_col, rotation, "
                    "stage_x_mm, stage_y_mm, stage_z_mm\n"
                )
                for tile in sorted(all_tiles, key=lambda t: t.selection_order):
                    f.write(
                        f"{tile.selection_order}, "
                        f"{tile.grid_row}, {tile.grid_col}, "
                        f"{tile.rotation_angle:.1f}, "
                        f"{tile.stage_x_mm or 'N/A'}, "
                        f"{tile.stage_y_mm or 'N/A'}, "
                        f"{tile.stage_z_mm or 'N/A'}\n"
                    )

            QMessageBox.information(
                self,
                "Export Complete",
                f"Exported {len(all_tiles)} tile positions to:\n{path}",
            )

        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export:\n{e}")

    def _is_connected(self) -> bool:
        if self._app is None:
            return False
        try:
            cs = getattr(self._app, "connection_service", None)
            if cs and hasattr(cs, "is_connected"):
                return cs.is_connected()
            cm = getattr(self._app, "connection_model", None)
            if cm and hasattr(cm, "connected"):
                return cm.connected
        except Exception:
            pass
        return False

    @classmethod
    def load_from_folder(
        cls,
        folder_path: Path,
        app=None,
    ) -> Optional["WebcamOverviewResultWindow"]:
        """Load a result window from a saved session folder.

        Returns:
            WebcamOverviewResultWindow instance, or None on failure.
        """
        try:
            from py2flamingo.visualization.webcam_session_io import (
                load_webcam_session,
            )

            session = load_webcam_session(folder_path)

            # Try to load calibration service
            cal_service = WebcamCalibrationService()

            window = cls(
                session=session,
                calibration_service=cal_service,
                app=app,
            )
            return window

        except Exception as e:
            logger.error(
                f"Error loading webcam session from {folder_path}: {e}",
                exc_info=True,
            )
            return None

    # ========== Future TODO ==========

    def _reconstruct_3d_from_angles(self):
        """TODO: 3D surface reconstruction from multi-angle webcam captures.

        Approach:
        1. Capture N views at small angular increments (e.g., every 5-10 degrees)
        2. Silhouette extraction from each view
        3. Shape-from-silhouette or visual hull reconstruction
        4. Generate 3D mesh/point cloud of the sample
        5. Display in napari 3D viewer alongside microscopy data
        6. Use for 3D acquisition region planning

        Libraries: OpenCV (feature matching), trimesh (mesh), scipy (hull)
        """
        raise NotImplementedError("3D reconstruction not yet implemented")
