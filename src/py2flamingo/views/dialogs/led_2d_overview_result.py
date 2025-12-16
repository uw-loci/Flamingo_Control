"""LED 2D Overview Result Window.

Displays the results of an LED 2D Overview scan, showing two side-by-side
images at different rotation angles with coordinate overlays.
"""

import logging
from typing import Optional, List
from dataclasses import dataclass
import numpy as np

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSplitter, QGroupBox, QFileDialog, QMessageBox,
    QSizePolicy, QFrame
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QFont


logger = logging.getLogger(__name__)


class ImagePanel(QWidget):
    """Widget displaying a single image with coordinate overlay."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)

        self._title = title
        self._image: Optional[np.ndarray] = None
        self._pixmap: Optional[QPixmap] = None
        self._show_grid = True
        self._tiles_x = 0
        self._tiles_y = 0
        self._tile_coords: List[tuple] = []  # (x, y, z) for each tile

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)

        # Title label
        self.title_label = QLabel(self._title)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet("font-weight: bold; font-size: 12pt;")
        layout.addWidget(self.title_label)

        # Image label with scroll area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignCenter)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setMinimumSize(100, 100)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.scroll_area.setWidget(self.image_label)
        layout.addWidget(self.scroll_area)

        # Info label
        self.info_label = QLabel("No image")
        self.info_label.setAlignment(Qt.AlignCenter)
        self.info_label.setStyleSheet("color: gray;")
        layout.addWidget(self.info_label)

        self.setLayout(layout)

    def set_title(self, title: str):
        """Set the panel title."""
        self._title = title
        self.title_label.setText(title)

    def set_image(self, image: Optional[np.ndarray], tiles_x: int = 0, tiles_y: int = 0):
        """Set the image to display.

        Args:
            image: Numpy array image (grayscale or RGB)
            tiles_x: Number of tiles in X dimension (for grid overlay)
            tiles_y: Number of tiles in Y dimension (for grid overlay)
        """
        self._image = image
        self._tiles_x = tiles_x
        self._tiles_y = tiles_y

        if image is None:
            self._pixmap = None
            self.image_label.clear()
            self.info_label.setText("No image")
            return

        # Convert numpy array to QPixmap
        self._pixmap = self._array_to_pixmap(image)

        # Draw grid overlay if enabled
        if self._show_grid and tiles_x > 0 and tiles_y > 0:
            self._draw_grid_overlay()

        self.image_label.setPixmap(self._pixmap)
        self.info_label.setText(f"Size: {image.shape[1]} x {image.shape[0]} pixels")

    def set_tile_coordinates(self, coords: List[tuple]):
        """Set tile coordinate data for overlay.

        Args:
            coords: List of (x, y, z) tuples for each tile
        """
        self._tile_coords = coords
        if self._pixmap and self._show_grid:
            self._draw_grid_overlay()
            self.image_label.setPixmap(self._pixmap)

    def set_show_grid(self, show: bool):
        """Enable or disable grid overlay."""
        self._show_grid = show
        if self._image is not None:
            self.set_image(self._image, self._tiles_x, self._tiles_y)

    def _array_to_pixmap(self, image: np.ndarray) -> QPixmap:
        """Convert numpy array to QPixmap."""
        if len(image.shape) == 2:
            # Grayscale
            h, w = image.shape
            bytes_per_line = w
            # Normalize to 8-bit if needed
            if image.dtype != np.uint8:
                img_8bit = ((image - image.min()) / (image.max() - image.min() + 1e-10) * 255).astype(np.uint8)
            else:
                img_8bit = image
            qimg = QImage(img_8bit.data, w, h, bytes_per_line, QImage.Format_Grayscale8)
        elif len(image.shape) == 3:
            h, w, c = image.shape
            if c == 4:  # RGBA
                bytes_per_line = w * 4
                qimg = QImage(image.data, w, h, bytes_per_line, QImage.Format_RGBA8888)
            else:  # RGB
                bytes_per_line = w * 3
                # Ensure contiguous
                img_cont = np.ascontiguousarray(image)
                qimg = QImage(img_cont.data, w, h, bytes_per_line, QImage.Format_RGB888)
        else:
            # Fallback: create blank image
            qimg = QImage(100, 100, QImage.Format_RGB888)
            qimg.fill(QColor(128, 128, 128))

        return QPixmap.fromImage(qimg.copy())  # Copy to ensure data persists

    def _draw_grid_overlay(self):
        """Draw grid lines on the pixmap."""
        if self._pixmap is None or self._tiles_x <= 0 or self._tiles_y <= 0:
            return

        # Create painter
        painter = QPainter(self._pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        # Grid line pen
        pen = QPen(QColor(255, 255, 0, 180))  # Yellow, semi-transparent
        pen.setWidth(1)
        painter.setPen(pen)

        w = self._pixmap.width()
        h = self._pixmap.height()
        tile_w = w / self._tiles_x
        tile_h = h / self._tiles_y

        # Draw vertical lines
        for i in range(1, self._tiles_x):
            x = int(i * tile_w)
            painter.drawLine(x, 0, x, h)

        # Draw horizontal lines
        for i in range(1, self._tiles_y):
            y = int(i * tile_h)
            painter.drawLine(0, y, w, y)

        # Draw coordinate labels if available
        if self._tile_coords:
            font = QFont("Courier", 8)
            painter.setFont(font)
            painter.setPen(QColor(255, 255, 255))

            for idx, (x, y, z) in enumerate(self._tile_coords):
                tile_x_idx = idx % self._tiles_x
                tile_y_idx = idx // self._tiles_x

                # Position text in tile
                text_x = int(tile_x_idx * tile_w + 4)
                text_y = int(tile_y_idx * tile_h + 14)

                text = f"Z:{z:.2f}"
                painter.drawText(text_x, text_y, text)

        painter.end()

    def get_image(self) -> Optional[np.ndarray]:
        """Get the current image."""
        return self._image


class LED2DOverviewResultWindow(QWidget):
    """Window displaying LED 2D Overview scan results.

    Shows two side-by-side images for the two rotation angles,
    with grid overlays and coordinate information.
    """

    def __init__(self, results=None, config=None, preview_mode: bool = False, parent=None):
        """Initialize the result window.

        Args:
            results: List of RotationResult from workflow (None for preview)
            config: ScanConfiguration used for the scan
            preview_mode: If True, show empty grid preview
            parent: Parent widget
        """
        super().__init__(parent)

        self._results = results or []
        self._config = config
        self._preview_mode = preview_mode

        self.setWindowTitle("LED 2D Overview - Results" if not preview_mode else "LED 2D Overview - Preview")
        self.setMinimumSize(800, 500)

        # Make window stay on top if previewing
        if preview_mode:
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self._setup_ui()

        if results:
            self._display_results()
        elif preview_mode:
            self._display_preview()

    def _setup_ui(self):
        """Create the window UI."""
        layout = QVBoxLayout()

        # Splitter for side-by-side images
        splitter = QSplitter(Qt.Horizontal)

        # Left panel (first rotation)
        self.left_panel = ImagePanel("Rotation 1")
        splitter.addWidget(self.left_panel)

        # Right panel (second rotation)
        self.right_panel = ImagePanel("Rotation 2")
        splitter.addWidget(self.right_panel)

        # Set equal split
        splitter.setSizes([400, 400])

        layout.addWidget(splitter)

        # Info section
        info_group = QGroupBox("Scan Information")
        info_layout = QVBoxLayout()

        self.info_text = QLabel("No scan data")
        self.info_text.setWordWrap(True)
        info_layout.addWidget(self.info_text)

        info_group.setLayout(info_layout)
        layout.addWidget(info_group)

        # Button row
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        # Toggle grid button
        self.grid_btn = QPushButton("Toggle Grid")
        self.grid_btn.setCheckable(True)
        self.grid_btn.setChecked(True)
        self.grid_btn.clicked.connect(self._toggle_grid)
        button_layout.addWidget(self.grid_btn)

        # Save buttons
        self.save_left_btn = QPushButton("Save Left")
        self.save_left_btn.clicked.connect(lambda: self._save_image('left'))
        button_layout.addWidget(self.save_left_btn)

        self.save_right_btn = QPushButton("Save Right")
        self.save_right_btn.clicked.connect(lambda: self._save_image('right'))
        button_layout.addWidget(self.save_right_btn)

        self.save_both_btn = QPushButton("Save Both")
        self.save_both_btn.clicked.connect(lambda: self._save_image('both'))
        button_layout.addWidget(self.save_both_btn)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def _display_results(self):
        """Display scan results."""
        if not self._results:
            self.info_text.setText("No results to display")
            return

        # Display first rotation
        if len(self._results) >= 1:
            result1 = self._results[0]
            self.left_panel.set_title(f"R = {result1.rotation_angle}°")

            if result1.stitched_image is not None:
                self.left_panel.set_image(
                    result1.stitched_image,
                    result1.tiles_x,
                    result1.tiles_y
                )
                # Set tile coordinates
                coords = [(t.x, t.y, t.z_best) for t in result1.tiles]
                self.left_panel.set_tile_coordinates(coords)

        # Display second rotation
        if len(self._results) >= 2:
            result2 = self._results[1]
            self.right_panel.set_title(f"R = {result2.rotation_angle}°")

            if result2.stitched_image is not None:
                self.right_panel.set_image(
                    result2.stitched_image,
                    result2.tiles_x,
                    result2.tiles_y
                )
                coords = [(t.x, t.y, t.z_best) for t in result2.tiles]
                self.right_panel.set_tile_coordinates(coords)

        # Update info text
        self._update_info_text()

    def _display_preview(self):
        """Display preview grid (empty tiles)."""
        if not self._config:
            self.info_text.setText("No configuration for preview")
            return

        # Calculate tile dimensions
        bbox = self._config.bounding_box
        fov = 0.5182  # mm
        overlap = self._config.tile_overlap / 100.0
        effective_step = fov * (1 - overlap)

        tiles_x = max(1, int((bbox.width / effective_step) + 1))
        tiles_y = max(1, int((bbox.height / effective_step) + 1))

        # Create preview images (gray grids)
        preview_w = tiles_x * 100
        preview_h = tiles_y * 100
        preview_img = np.ones((preview_h, preview_w), dtype=np.uint8) * 200

        # Draw tile boundaries
        for i in range(tiles_x + 1):
            x = i * 100
            if x < preview_w:
                preview_img[:, x:x+2] = 100

        for i in range(tiles_y + 1):
            y = i * 100
            if y < preview_h:
                preview_img[y:y+2, :] = 100

        # Set titles
        r1 = self._config.starting_r
        r2 = self._config.starting_r + 90

        self.left_panel.set_title(f"Preview: R = {r1}°")
        self.left_panel.set_image(preview_img, tiles_x, tiles_y)

        self.right_panel.set_title(f"Preview: R = {r2}°")
        self.right_panel.set_image(preview_img.copy(), tiles_x, tiles_y)

        # Update info
        self.info_text.setText(
            f"Preview Mode\n"
            f"Tiles: {tiles_x} x {tiles_y} = {tiles_x * tiles_y} per rotation\n"
            f"Total: {tiles_x * tiles_y * 2} tiles\n"
            f"Region: X [{bbox.x_min:.2f} to {bbox.x_max:.2f}], "
            f"Y [{bbox.y_min:.2f} to {bbox.y_max:.2f}] mm"
        )

    def _update_info_text(self):
        """Update the info text with scan details."""
        if not self._results:
            return

        lines = []

        for i, result in enumerate(self._results):
            lines.append(f"Rotation {i+1}: {result.rotation_angle}°")
            lines.append(f"  Tiles: {result.tiles_x} x {result.tiles_y} = {len(result.tiles)}")

            if result.tiles:
                z_values = [t.z_best for t in result.tiles]
                lines.append(f"  Z range: {min(z_values):.3f} to {max(z_values):.3f} mm")

        if self._config:
            bbox = self._config.bounding_box
            lines.append("")
            lines.append(f"Region: X [{bbox.x_min:.2f} to {bbox.x_max:.2f}], "
                        f"Y [{bbox.y_min:.2f} to {bbox.y_max:.2f}] mm")

        self.info_text.setText("\n".join(lines))

    def _toggle_grid(self):
        """Toggle grid overlay."""
        show_grid = self.grid_btn.isChecked()
        self.left_panel.set_show_grid(show_grid)
        self.right_panel.set_show_grid(show_grid)

    def _save_image(self, which: str):
        """Save image(s) to file.

        Args:
            which: 'left', 'right', or 'both'
        """
        if which == 'both':
            self._save_image('left')
            self._save_image('right')
            return

        panel = self.left_panel if which == 'left' else self.right_panel
        image = panel.get_image()

        if image is None:
            QMessageBox.warning(self, "No Image", f"No image in {which} panel")
            return

        # Get save path
        default_name = f"led_2d_overview_{which}.png"
        path, _ = QFileDialog.getSaveFileName(
            self,
            f"Save {which.title()} Image",
            default_name,
            "PNG Images (*.png);;TIFF Images (*.tiff *.tif);;All Files (*)"
        )

        if not path:
            return

        try:
            import cv2
            # Ensure image is in the right format for saving
            if len(image.shape) == 3 and image.shape[2] == 3:
                # RGB to BGR for OpenCV
                save_img = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            else:
                save_img = image

            cv2.imwrite(path, save_img)
            logger.info(f"Saved image to {path}")
            QMessageBox.information(self, "Saved", f"Image saved to:\n{path}")

        except ImportError:
            # Fallback without OpenCV
            try:
                from PIL import Image
                if len(image.shape) == 2:
                    pil_img = Image.fromarray(image)
                else:
                    pil_img = Image.fromarray(image)
                pil_img.save(path)
                logger.info(f"Saved image to {path}")
                QMessageBox.information(self, "Saved", f"Image saved to:\n{path}")
            except ImportError:
                QMessageBox.critical(
                    self, "Error",
                    "Neither OpenCV nor PIL available for saving images"
                )
        except Exception as e:
            logger.error(f"Error saving image: {e}")
            QMessageBox.critical(self, "Error", f"Failed to save image:\n{e}")

    def update_tile(self, rotation_idx: int, tile_idx: int, image: np.ndarray):
        """Update a single tile during scanning.

        Args:
            rotation_idx: Which rotation (0 or 1)
            tile_idx: Which tile
            image: Tile image
        """
        # This method allows updating tiles as they're captured
        # during a scan, for live preview
        panel = self.left_panel if rotation_idx == 0 else self.right_panel

        # For now, just redraw the full image
        # A more efficient implementation would update just the tile
        pass
