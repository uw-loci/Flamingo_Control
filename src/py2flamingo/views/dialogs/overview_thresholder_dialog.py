"""2D Overview Thresholder Dialog.

Dialog for automatically pre-selecting tiles in the LED 2D Overview
based on image analysis. Detects "sample" vs "background" tiles using:
- Variance analysis (low variance = background)
- Edge/focus detection (high edge content = sample)
- Intensity thresholding

Background tiles typically have low variability and uniform intensity.
Sample tiles have more texture, edges, and varying intensity.
"""

import logging
from typing import List, Optional, Set, Tuple

import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from py2flamingo.services.window_geometry_manager import PersistentDialog

logger = logging.getLogger(__name__)


def calculate_tile_variance(
    image: np.ndarray, tiles_x: int, tiles_y: int
) -> np.ndarray:
    """Calculate variance for each tile in the image.

    Args:
        image: Input image (grayscale or RGB)
        tiles_x: Number of tiles in X
        tiles_y: Number of tiles in Y

    Returns:
        2D array of variance values [tiles_y, tiles_x]
    """
    # Convert to grayscale if needed
    if len(image.shape) == 3:
        gray = np.mean(image, axis=2)
    else:
        gray = image.astype(np.float64)

    h, w = gray.shape
    tile_h = h // tiles_y
    tile_w = w // tiles_x

    variances = np.zeros((tiles_y, tiles_x))

    for ty in range(tiles_y):
        for tx in range(tiles_x):
            y_start = ty * tile_h
            y_end = (ty + 1) * tile_h
            x_start = tx * tile_w
            x_end = (tx + 1) * tile_w

            tile = gray[y_start:y_end, x_start:x_end]
            variances[ty, tx] = np.var(tile)

    return variances


def calculate_tile_edges(image: np.ndarray, tiles_x: int, tiles_y: int) -> np.ndarray:
    """Calculate edge content for each tile using Laplacian variance.

    Higher values indicate more edges/texture (likely sample, not background).

    Args:
        image: Input image (grayscale or RGB)
        tiles_x: Number of tiles in X
        tiles_y: Number of tiles in Y

    Returns:
        2D array of edge scores [tiles_y, tiles_x]
    """
    from scipy.ndimage import convolve

    # Convert to grayscale if needed
    if len(image.shape) == 3:
        gray = np.mean(image, axis=2).astype(np.float64)
    else:
        gray = image.astype(np.float64)

    h, w = gray.shape
    tile_h = h // tiles_y
    tile_w = w // tiles_x

    # Laplacian kernel for edge detection
    kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)

    # Apply Laplacian to entire image at once (vectorized, fast)
    laplacian = convolve(gray, kernel, mode="nearest")

    edge_scores = np.zeros((tiles_y, tiles_x))

    for ty in range(tiles_y):
        for tx in range(tiles_x):
            y_start = ty * tile_h
            y_end = (ty + 1) * tile_h
            x_start = tx * tile_w
            x_end = (tx + 1) * tile_w

            tile_lap = laplacian[y_start:y_end, x_start:x_end]
            edge_scores[ty, tx] = np.var(tile_lap)

    return edge_scores


def calculate_tile_intensity(
    image: np.ndarray, tiles_x: int, tiles_y: int
) -> np.ndarray:
    """Calculate mean intensity for each tile.

    Args:
        image: Input image (grayscale or RGB)
        tiles_x: Number of tiles in X
        tiles_y: Number of tiles in Y

    Returns:
        2D array of mean intensity values [tiles_y, tiles_x]
    """
    # Convert to grayscale if needed
    if len(image.shape) == 3:
        gray = np.mean(image, axis=2)
    else:
        gray = image.astype(np.float64)

    h, w = gray.shape
    tile_h = h // tiles_y
    tile_w = w // tiles_x

    intensities = np.zeros((tiles_y, tiles_x))

    for ty in range(tiles_y):
        for tx in range(tiles_x):
            y_start = ty * tile_h
            y_end = (ty + 1) * tile_h
            x_start = tx * tile_w
            x_end = (tx + 1) * tile_w

            tile = gray[y_start:y_end, x_start:x_end]
            intensities[ty, tx] = np.mean(tile)

    return intensities


def calculate_tile_entropy(image: np.ndarray, tiles_x: int, tiles_y: int) -> np.ndarray:
    """Calculate Shannon entropy for each tile using a 64-bin histogram.

    Higher entropy indicates more complex/varied pixel distributions (likely sample).
    Lower entropy indicates uniform regions (background or empty tube interior).

    Args:
        image: Input image (grayscale or RGB)
        tiles_x: Number of tiles in X
        tiles_y: Number of tiles in Y

    Returns:
        2D array of entropy values [tiles_y, tiles_x], range ~0-6
    """
    if len(image.shape) == 3:
        gray = np.mean(image, axis=2)
    else:
        gray = image.astype(np.float64)

    h, w = gray.shape
    tile_h = h // tiles_y
    tile_w = w // tiles_x
    n_bins = 64

    entropies = np.zeros((tiles_y, tiles_x))

    for ty in range(tiles_y):
        for tx in range(tiles_x):
            y_start = ty * tile_h
            y_end = (ty + 1) * tile_h
            x_start = tx * tile_w
            x_end = (tx + 1) * tile_w

            tile = gray[y_start:y_end, x_start:x_end]
            # Normalize tile to 0-1 range for consistent binning
            t_min, t_max = tile.min(), tile.max()
            if t_max > t_min:
                tile_norm = (tile - t_min) / (t_max - t_min)
            else:
                tile_norm = np.zeros_like(tile)

            hist, _ = np.histogram(tile_norm, bins=n_bins, range=(0, 1))
            # Normalize to probability distribution
            prob = hist / hist.sum()
            # Shannon entropy: -sum(p * log2(p)) for p > 0
            nonzero = prob > 0
            entropies[ty, tx] = -np.sum(prob[nonzero] * np.log2(prob[nonzero]))

    return entropies


def calculate_tile_mad(image: np.ndarray, tiles_x: int, tiles_y: int) -> np.ndarray:
    """Calculate median absolute deviation for each tile.

    MAD is a robust measure of variability, less sensitive to outliers than variance.

    Args:
        image: Input image (grayscale or RGB)
        tiles_x: Number of tiles in X
        tiles_y: Number of tiles in Y

    Returns:
        2D array of MAD values [tiles_y, tiles_x]
    """
    if len(image.shape) == 3:
        gray = np.mean(image, axis=2)
    else:
        gray = image.astype(np.float64)

    h, w = gray.shape
    tile_h = h // tiles_y
    tile_w = w // tiles_x

    mads = np.zeros((tiles_y, tiles_x))

    for ty in range(tiles_y):
        for tx in range(tiles_x):
            y_start = ty * tile_h
            y_end = (ty + 1) * tile_h
            x_start = tx * tile_w
            x_end = (tx + 1) * tile_w

            tile = gray[y_start:y_end, x_start:x_end]
            median = np.median(tile)
            mads[ty, tx] = np.median(np.abs(tile - median))

    return mads


def calculate_tile_gradient_anisotropy(
    image: np.ndarray, tiles_x: int, tiles_y: int
) -> np.ndarray:
    """Calculate gradient orientation anisotropy per tile.

    Range 0-1: 0 = isotropic (sample texture), 1 = strongly directional (tube edges).
    Uses Sobel-like filters: anisotropy = |sum(gx^2) - sum(gy^2)| / (sum(gx^2) + sum(gy^2))

    Args:
        image: Input image (grayscale or RGB)
        tiles_x: Number of tiles in X
        tiles_y: Number of tiles in Y

    Returns:
        2D array of anisotropy values [tiles_y, tiles_x]
    """
    from scipy.ndimage import convolve

    if len(image.shape) == 3:
        gray = np.mean(image, axis=2).astype(np.float64)
    else:
        gray = image.astype(np.float64)

    # Sobel kernels
    sobel_x = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float64)
    sobel_y = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=np.float64)

    gx = convolve(gray, sobel_x, mode="nearest")
    gy = convolve(gray, sobel_y, mode="nearest")

    h, w = gray.shape
    tile_h = h // tiles_y
    tile_w = w // tiles_x

    anisotropy = np.zeros((tiles_y, tiles_x))

    for ty in range(tiles_y):
        for tx in range(tiles_x):
            y_start = ty * tile_h
            y_end = (ty + 1) * tile_h
            x_start = tx * tile_w
            x_end = (tx + 1) * tile_w

            gx_tile = gx[y_start:y_end, x_start:x_end]
            gy_tile = gy[y_start:y_end, x_start:x_end]

            sum_gx2 = np.sum(gx_tile**2)
            sum_gy2 = np.sum(gy_tile**2)
            total = sum_gx2 + sum_gy2

            if total > 0:
                anisotropy[ty, tx] = abs(sum_gx2 - sum_gy2) / total
            else:
                anisotropy[ty, tx] = 0.0

    return anisotropy


def calculate_tile_dog_variance(
    image: np.ndarray,
    tiles_x: int,
    tiles_y: int,
    sigma1: float = 1.0,
    sigma2: float = 4.0,
) -> np.ndarray:
    """Calculate variance of Difference-of-Gaussians filtered image per tile.

    DoG suppresses thin high-frequency features (tube edges) while preserving
    broader texture. Higher DoG variance = more broad texture (likely sample).

    Args:
        image: Input image (grayscale or RGB)
        tiles_x: Number of tiles in X
        tiles_y: Number of tiles in Y
        sigma1: Narrow Gaussian sigma
        sigma2: Wide Gaussian sigma

    Returns:
        2D array of DoG variance values [tiles_y, tiles_x]
    """
    from scipy.ndimage import gaussian_filter

    if len(image.shape) == 3:
        gray = np.mean(image, axis=2).astype(np.float64)
    else:
        gray = image.astype(np.float64)

    g1 = gaussian_filter(gray, sigma=sigma1)
    g2 = gaussian_filter(gray, sigma=sigma2)
    dog = g1 - g2

    h, w = gray.shape
    tile_h = h // tiles_y
    tile_w = w // tiles_x

    variances = np.zeros((tiles_y, tiles_x))

    for ty in range(tiles_y):
        for tx in range(tiles_x):
            y_start = ty * tile_h
            y_end = (ty + 1) * tile_h
            x_start = tx * tile_w
            x_end = (tx + 1) * tile_w

            tile = dog[y_start:y_end, x_start:x_end]
            variances[ty, tx] = np.var(tile)

    return variances


def otsu_threshold(values: np.ndarray) -> float:
    """Compute Otsu's threshold for a 1D array of metric values.

    Maximizes inter-class variance to find optimal binary split point.

    Args:
        values: 1D array of metric values

    Returns:
        Optimal threshold value
    """
    flat = values.ravel()
    if len(flat) == 0:
        return 0.0

    # Use 256 bins for histogram
    v_min, v_max = flat.min(), flat.max()
    if v_max == v_min:
        return float(v_min)

    n_bins = min(256, len(flat))
    hist, bin_edges = np.histogram(flat, bins=n_bins, range=(v_min, v_max))
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

    total = hist.sum()
    if total == 0:
        return float(v_min)

    sum_total = np.sum(bin_centers * hist)
    sum_bg = 0.0
    weight_bg = 0
    best_thresh = float(bin_centers[0])
    best_var = -1.0

    for i in range(len(hist)):
        weight_bg += hist[i]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break

        sum_bg += bin_centers[i] * hist[i]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_total - sum_bg) / weight_fg

        var_between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if var_between > best_var:
            best_var = var_between
            best_thresh = float(bin_centers[i])

    return best_thresh


class OverviewThresholderDialog(PersistentDialog):
    """Dialog for automatic tile selection based on image analysis.

    Analyzes the 2D Overview image to detect which tiles contain
    sample (vs background) based on variance, edge content, and intensity.

    Signals:
        selection_ready: Emitted with set of (tile_x, tile_y) tuples to select
    """

    selection_ready = pyqtSignal(set)  # Set of (tile_x, tile_y) tuples

    def __init__(self, image: np.ndarray, tiles_x: int, tiles_y: int, parent=None):
        """Initialize the thresholder dialog.

        Args:
            image: The 2D Overview image as numpy array
            tiles_x: Number of tiles in X dimension
            tiles_y: Number of tiles in Y dimension
            parent: Parent widget
        """
        super().__init__(parent)
        self._image = image
        self._tiles_x = tiles_x
        self._tiles_y = tiles_y

        # Pre-calculate metrics for all tiles
        self._variances: Optional[np.ndarray] = None
        self._edge_scores: Optional[np.ndarray] = None
        self._intensities: Optional[np.ndarray] = None
        self._entropies: Optional[np.ndarray] = None
        self._mads: Optional[np.ndarray] = None
        self._entropies_smoothed: Optional[np.ndarray] = None
        self._gradient_aniso: Optional[np.ndarray] = None
        self._dog_variances: Optional[np.ndarray] = None

        # Current selection
        self._selected_tiles: Set[Tuple[int, int]] = set()

        self.setWindowTitle("2D Overview Tile Thresholder")
        self.setMinimumSize(800, 600)

        self._setup_ui()
        self._calculate_metrics()
        self._restore_dialog_state()
        self._update_preview()

    def _setup_ui(self):
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)

        # Main content: image preview and controls side by side
        content_layout = QHBoxLayout()

        # Left: Image preview with tile overlay
        preview_frame = QFrame()
        preview_frame.setFrameStyle(QFrame.StyledPanel)
        preview_layout = QVBoxLayout(preview_frame)

        self._preview_label = QLabel()
        self._preview_label.setAlignment(Qt.AlignCenter)
        self._preview_label.setMinimumSize(400, 400)
        self._preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        preview_layout.addWidget(self._preview_label)

        # Selection info
        self._info_label = QLabel("0 tiles selected")
        self._info_label.setAlignment(Qt.AlignCenter)
        preview_layout.addWidget(self._info_label)

        content_layout.addWidget(preview_frame, stretch=2)

        # Right: Controls
        controls_frame = QFrame()
        controls_frame.setMaximumWidth(350)
        controls_layout = QVBoxLayout(controls_frame)

        # Method selection
        method_group = QGroupBox("Detection Method")
        method_layout = QFormLayout()

        self._method_combo = QComboBox()
        self._method_combo.addItem("Entropy (recommended)", "entropy")
        self._method_combo.addItem("Band-pass (Variance + Entropy)", "bandpass")
        self._method_combo.addItem("Gradient Orientation", "gradient")
        self._method_combo.addItem("DoG Texture", "dog")
        self._method_combo.addItem("Tube Detection", "tube_detect")
        self._method_combo.addItem("Variance (low = background)", "variance")
        self._method_combo.addItem("Edge Detection (high = sample)", "edge")
        self._method_combo.addItem("Intensity Range", "intensity")
        self._method_combo.addItem("Combined (Variance + Edge)", "combined")
        self._method_combo.setToolTip(
            "Entropy: select tiles by information content with spatial smoothing\n"
            "Band-pass: exclude tube edges (high var) and background (low entropy)\n"
            "Gradient Orientation: exclude directional tube edges (anisotropy filter)\n"
            "DoG Texture: Difference-of-Gaussians texture filter\n"
            "Tube Detection: two-stage tube boundary + interior classification\n"
            "Variance: select tiles with high pixel variability (texture = sample)\n"
            "Edge Detection: select tiles with strong edges (Laplacian filter)\n"
            "Intensity Range: select tiles by mean brightness\n"
            "Combined: select tiles passing either variance or edge threshold"
        )
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)
        method_layout.addRow("Method:", self._method_combo)

        method_group.setLayout(method_layout)
        controls_layout.addWidget(method_group)

        # Entropy settings
        self._entropy_group = QGroupBox("Entropy Threshold")
        entropy_layout = QFormLayout()

        self._entropy_threshold_spin = QDoubleSpinBox()
        self._entropy_threshold_spin.setRange(0.0, 6.0)
        self._entropy_threshold_spin.setDecimals(2)
        self._entropy_threshold_spin.setSingleStep(0.05)
        self._entropy_threshold_spin.setValue(3.0)
        self._entropy_threshold_spin.setToolTip(
            "Tiles with entropy above this value are selected as sample.\n"
            "Higher entropy = more texture/information content."
        )
        self._entropy_threshold_spin.valueChanged.connect(self._update_preview)
        entropy_layout.addRow("Min entropy:", self._entropy_threshold_spin)

        self._smoothing_check = QCheckBox("Spatial smoothing (σ=1.5)")
        self._smoothing_check.setToolTip(
            "Apply Gaussian smoothing to per-tile entropy scores before thresholding.\n"
            "Adds spatial coherence — isolated noisy tiles are suppressed."
        )
        self._smoothing_check.setChecked(True)
        self._smoothing_check.stateChanged.connect(self._update_preview)
        entropy_layout.addRow(self._smoothing_check)

        self._entropy_otsu_btn = QPushButton("Auto (Otsu)")
        self._entropy_otsu_btn.setToolTip(
            "Set threshold automatically using Otsu's method"
        )
        self._entropy_otsu_btn.clicked.connect(self._on_entropy_otsu)
        entropy_layout.addRow(self._entropy_otsu_btn)

        self._entropy_group.setLayout(entropy_layout)
        controls_layout.addWidget(self._entropy_group)

        # Band-pass settings
        self._bandpass_group = QGroupBox("Band-pass (Variance + Entropy)")
        bandpass_layout = QFormLayout()

        self._bp_var_min_spin = QDoubleSpinBox()
        self._bp_var_min_spin.setDecimals(1)
        self._bp_var_min_spin.setSingleStep(1.0)
        self._bp_var_min_spin.setToolTip("Minimum variance — excludes dead/black tiles")
        self._bp_var_min_spin.valueChanged.connect(self._update_preview)
        bandpass_layout.addRow("Variance min:", self._bp_var_min_spin)

        self._bp_var_max_spin = QDoubleSpinBox()
        self._bp_var_max_spin.setDecimals(1)
        self._bp_var_max_spin.setSingleStep(1.0)
        self._bp_var_max_spin.setToolTip(
            "Maximum variance — excludes capillary tube edges"
        )
        self._bp_var_max_spin.valueChanged.connect(self._update_preview)
        bandpass_layout.addRow("Variance max:", self._bp_var_max_spin)

        self._bp_entropy_min_spin = QDoubleSpinBox()
        self._bp_entropy_min_spin.setRange(0.0, 6.0)
        self._bp_entropy_min_spin.setDecimals(2)
        self._bp_entropy_min_spin.setSingleStep(0.05)
        self._bp_entropy_min_spin.setToolTip(
            "Minimum entropy — excludes uniform background"
        )
        self._bp_entropy_min_spin.valueChanged.connect(self._update_preview)
        bandpass_layout.addRow("Entropy min:", self._bp_entropy_min_spin)

        self._bandpass_group.setLayout(bandpass_layout)
        controls_layout.addWidget(self._bandpass_group)
        self._bandpass_group.hide()

        # Gradient Orientation settings
        self._gradient_group = QGroupBox("Gradient Orientation")
        gradient_layout = QFormLayout()

        self._gradient_threshold_spin = QDoubleSpinBox()
        self._gradient_threshold_spin.setRange(0.0, 1.0)
        self._gradient_threshold_spin.setDecimals(2)
        self._gradient_threshold_spin.setSingleStep(0.05)
        self._gradient_threshold_spin.setValue(0.5)
        self._gradient_threshold_spin.setToolTip(
            "Max anisotropy — tiles with anisotropy BELOW this are selected.\n"
            "Low anisotropy = multi-directional gradients = sample texture.\n"
            "High anisotropy = directional edges = tube boundaries."
        )
        self._gradient_threshold_spin.valueChanged.connect(self._update_preview)
        gradient_layout.addRow("Max anisotropy:", self._gradient_threshold_spin)

        self._gradient_otsu_btn = QPushButton("Auto (Otsu)")
        self._gradient_otsu_btn.setToolTip(
            "Set threshold automatically using Otsu's method"
        )
        self._gradient_otsu_btn.clicked.connect(self._on_gradient_otsu)
        gradient_layout.addRow(self._gradient_otsu_btn)

        self._gradient_group.setLayout(gradient_layout)
        controls_layout.addWidget(self._gradient_group)
        self._gradient_group.hide()

        # DoG Texture settings
        self._dog_group = QGroupBox("DoG Texture")
        dog_layout = QFormLayout()

        self._dog_sigma1_spin = QDoubleSpinBox()
        self._dog_sigma1_spin.setRange(0.1, 10.0)
        self._dog_sigma1_spin.setDecimals(1)
        self._dog_sigma1_spin.setSingleStep(0.5)
        self._dog_sigma1_spin.setValue(1.0)
        self._dog_sigma1_spin.setToolTip("Narrow Gaussian sigma")
        self._dog_sigma1_spin.valueChanged.connect(self._on_dog_sigma_changed)
        dog_layout.addRow("Sigma 1 (narrow):", self._dog_sigma1_spin)

        self._dog_sigma2_spin = QDoubleSpinBox()
        self._dog_sigma2_spin.setRange(0.5, 20.0)
        self._dog_sigma2_spin.setDecimals(1)
        self._dog_sigma2_spin.setSingleStep(0.5)
        self._dog_sigma2_spin.setValue(4.0)
        self._dog_sigma2_spin.setToolTip("Wide Gaussian sigma")
        self._dog_sigma2_spin.valueChanged.connect(self._on_dog_sigma_changed)
        dog_layout.addRow("Sigma 2 (wide):", self._dog_sigma2_spin)

        self._dog_threshold_spin = QDoubleSpinBox()
        self._dog_threshold_spin.setDecimals(1)
        self._dog_threshold_spin.setSingleStep(1.0)
        self._dog_threshold_spin.setToolTip(
            "Min DoG variance — tiles above this are selected as sample"
        )
        self._dog_threshold_spin.valueChanged.connect(self._update_preview)
        dog_layout.addRow("Min DoG variance:", self._dog_threshold_spin)

        self._dog_otsu_btn = QPushButton("Auto (Otsu)")
        self._dog_otsu_btn.setToolTip("Set threshold automatically using Otsu's method")
        self._dog_otsu_btn.clicked.connect(self._on_dog_otsu)
        dog_layout.addRow(self._dog_otsu_btn)

        self._dog_group.setLayout(dog_layout)
        controls_layout.addWidget(self._dog_group)
        self._dog_group.hide()

        # Tube Detection settings
        self._tube_group = QGroupBox("Tube Detection")
        tube_layout = QFormLayout()

        self._tube_interior_method_combo = QComboBox()
        self._tube_interior_method_combo.addItem("Entropy", "entropy")
        self._tube_interior_method_combo.addItem("Variance", "variance")
        self._tube_interior_method_combo.setToolTip(
            "Method to classify tiles inside the tube as sample vs empty"
        )
        self._tube_interior_method_combo.currentIndexChanged.connect(
            self._update_preview
        )
        tube_layout.addRow("Interior method:", self._tube_interior_method_combo)

        self._tube_interior_threshold_spin = QDoubleSpinBox()
        self._tube_interior_threshold_spin.setRange(0.0, 10000.0)
        self._tube_interior_threshold_spin.setDecimals(2)
        self._tube_interior_threshold_spin.setSingleStep(0.1)
        self._tube_interior_threshold_spin.setValue(3.0)
        self._tube_interior_threshold_spin.setToolTip(
            "Threshold for interior classification (entropy or variance)"
        )
        self._tube_interior_threshold_spin.valueChanged.connect(self._update_preview)
        tube_layout.addRow("Interior threshold:", self._tube_interior_threshold_spin)

        self._tube_sensitivity_spin = QDoubleSpinBox()
        self._tube_sensitivity_spin.setRange(0.0, 5.0)
        self._tube_sensitivity_spin.setDecimals(2)
        self._tube_sensitivity_spin.setSingleStep(0.1)
        self._tube_sensitivity_spin.setValue(0.5)
        self._tube_sensitivity_spin.setToolTip(
            "Edge detection sensitivity — lower = more aggressive edge detection"
        )
        self._tube_sensitivity_spin.valueChanged.connect(self._update_preview)
        tube_layout.addRow("Edge sensitivity:", self._tube_sensitivity_spin)

        self._tube_group.setLayout(tube_layout)
        controls_layout.addWidget(self._tube_group)
        self._tube_group.hide()

        # Variance settings
        self._variance_group = QGroupBox("Variance Threshold")
        variance_layout = QFormLayout()

        self._variance_slider = QSlider(Qt.Horizontal)
        self._variance_slider.setRange(0, 1000)
        self._variance_slider.setValue(100)
        self._variance_slider.setToolTip(
            "Tiles with variance above this threshold are selected as sample"
        )
        self._variance_slider.valueChanged.connect(self._update_preview)
        variance_layout.addRow("Min variance:", self._variance_slider)

        self._variance_value = QLabel("100")
        variance_layout.addRow("Value:", self._variance_value)

        self._variance_group.setLayout(variance_layout)
        controls_layout.addWidget(self._variance_group)
        self._variance_group.hide()

        # Edge settings
        self._edge_group = QGroupBox("Edge Detection Threshold")
        edge_layout = QFormLayout()

        self._edge_slider = QSlider(Qt.Horizontal)
        self._edge_slider.setRange(0, 10000)
        self._edge_slider.setValue(500)
        self._edge_slider.setToolTip(
            "Tiles with edge score above this threshold are selected as sample"
        )
        self._edge_slider.valueChanged.connect(self._update_preview)
        edge_layout.addRow("Min edge score:", self._edge_slider)

        self._edge_value = QLabel("500")
        edge_layout.addRow("Value:", self._edge_value)

        self._edge_group.setLayout(edge_layout)
        controls_layout.addWidget(self._edge_group)
        self._edge_group.hide()

        # Intensity settings
        self._intensity_group = QGroupBox("Intensity Range")
        intensity_layout = QFormLayout()

        self._intensity_min = QSlider(Qt.Horizontal)
        self._intensity_min.setRange(0, 255)
        self._intensity_min.setValue(20)
        self._intensity_min.valueChanged.connect(self._update_preview)
        intensity_layout.addRow("Min intensity:", self._intensity_min)

        self._intensity_max = QSlider(Qt.Horizontal)
        self._intensity_max.setRange(0, 255)
        self._intensity_max.setValue(255)
        self._intensity_max.valueChanged.connect(self._update_preview)
        intensity_layout.addRow("Max intensity:", self._intensity_max)

        self._intensity_group.setLayout(intensity_layout)
        controls_layout.addWidget(self._intensity_group)
        self._intensity_group.hide()

        # Options
        options_group = QGroupBox("Options")
        options_layout = QVBoxLayout()

        self._invert_check = QCheckBox("Invert selection")
        self._invert_check.setToolTip("Select background tiles instead of sample tiles")
        self._invert_check.stateChanged.connect(self._update_preview)
        options_layout.addWidget(self._invert_check)

        self._preview_check = QCheckBox("Show preview overlay")
        self._preview_check.setToolTip(
            "Show green outlines on selected tiles in the preview"
        )
        self._preview_check.setChecked(True)
        self._preview_check.stateChanged.connect(self._update_preview)
        options_layout.addWidget(self._preview_check)

        # Morphological cleanup
        morph_layout = QHBoxLayout()
        self._morphological_check = QCheckBox("Morphological cleanup")
        self._morphological_check.setToolTip(
            "Apply closing then opening to fill gaps and remove isolated tiles"
        )
        self._morphological_check.stateChanged.connect(self._update_preview)
        morph_layout.addWidget(self._morphological_check)

        self._morphological_radius_spin = QSpinBox()
        self._morphological_radius_spin.setRange(1, 3)
        self._morphological_radius_spin.setValue(1)
        self._morphological_radius_spin.setToolTip("Cleanup radius (iterations)")
        self._morphological_radius_spin.valueChanged.connect(self._update_preview)
        morph_layout.addWidget(QLabel("Radius:"))
        morph_layout.addWidget(self._morphological_radius_spin)
        options_layout.addLayout(morph_layout)

        options_group.setLayout(options_layout)
        controls_layout.addWidget(options_group)

        # Statistics
        stats_group = QGroupBox("Tile Statistics")
        stats_layout = QFormLayout()

        self._stats_entropy = QLabel("-")
        stats_layout.addRow("Entropy range:", self._stats_entropy)

        self._stats_variance = QLabel("-")
        stats_layout.addRow("Variance range:", self._stats_variance)

        self._stats_edge = QLabel("-")
        stats_layout.addRow("Edge range:", self._stats_edge)

        self._stats_intensity = QLabel("-")
        stats_layout.addRow("Intensity range:", self._stats_intensity)

        self._stats_gradient = QLabel("-")
        stats_layout.addRow("Anisotropy range:", self._stats_gradient)

        self._stats_dog = QLabel("-")
        stats_layout.addRow("DoG var range:", self._stats_dog)

        stats_group.setLayout(stats_layout)
        controls_layout.addWidget(stats_group)

        controls_layout.addStretch()

        content_layout.addWidget(controls_frame, stretch=1)
        layout.addLayout(content_layout)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        apply_btn = QPushButton("Apply Selection")
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self._on_apply)
        button_layout.addWidget(apply_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

    def _calculate_metrics(self):
        """Pre-calculate all metrics for the tiles."""
        from scipy.ndimage import gaussian_filter

        logger.info(
            f"Calculating tile metrics for {self._tiles_x}x{self._tiles_y} grid..."
        )

        try:
            self._variances = calculate_tile_variance(
                self._image, self._tiles_x, self._tiles_y
            )
        except Exception as e:
            logger.error(f"Failed to calculate tile variance: {e}")

        try:
            self._edge_scores = calculate_tile_edges(
                self._image, self._tiles_x, self._tiles_y
            )
        except Exception as e:
            logger.error(f"Failed to calculate tile edges: {e}")

        try:
            self._intensities = calculate_tile_intensity(
                self._image, self._tiles_x, self._tiles_y
            )
        except Exception as e:
            logger.error(f"Failed to calculate tile intensity: {e}")

        try:
            self._entropies = calculate_tile_entropy(
                self._image, self._tiles_x, self._tiles_y
            )
            self._entropies_smoothed = gaussian_filter(self._entropies, sigma=1.5)
        except Exception as e:
            logger.error(f"Failed to calculate tile entropy: {e}")

        try:
            self._mads = calculate_tile_mad(self._image, self._tiles_x, self._tiles_y)
        except Exception as e:
            logger.error(f"Failed to calculate tile MAD: {e}")

        try:
            self._gradient_aniso = calculate_tile_gradient_anisotropy(
                self._image, self._tiles_x, self._tiles_y
            )
        except Exception as e:
            logger.error(f"Failed to calculate gradient anisotropy: {e}")

        try:
            self._dog_variances = calculate_tile_dog_variance(
                self._image,
                self._tiles_x,
                self._tiles_y,
                sigma1=self._dog_sigma1_spin.value(),
                sigma2=self._dog_sigma2_spin.value(),
            )
        except Exception as e:
            logger.error(f"Failed to calculate DoG variance: {e}")

        # Update statistics display and set initial slider ranges/defaults
        if self._entropies is not None:
            ent_min, ent_max = self._entropies.min(), self._entropies.max()
            self._stats_entropy.setText(f"{ent_min:.2f} - {ent_max:.2f}")
            # Entropy default: 35th percentile
            pct35 = float(np.percentile(self._entropies, 35))
            self._entropy_threshold_spin.setValue(round(pct35, 2))
            # Band-pass entropy min: 25th percentile
            pct25_ent = float(np.percentile(self._entropies, 25))
            self._bp_entropy_min_spin.setValue(round(pct25_ent, 2))

        if self._variances is not None:
            v_min, v_max = self._variances.min(), self._variances.max()
            self._stats_variance.setText(f"{v_min:.1f} - {v_max:.1f}")
            # Set slider range based on actual data
            self._variance_slider.setRange(0, int(v_max * 1.1) + 1)
            self._variance_slider.setValue(int(v_min + (v_max - v_min) * 0.2))
            # Band-pass variance ranges
            self._bp_var_min_spin.setRange(0.0, float(v_max * 1.1))
            self._bp_var_max_spin.setRange(0.0, float(v_max * 1.1))
            pct5_var = float(np.percentile(self._variances, 5))
            pct75_var = float(np.percentile(self._variances, 75))
            self._bp_var_min_spin.setValue(round(pct5_var, 1))
            self._bp_var_max_spin.setValue(round(pct75_var, 1))

        if self._edge_scores is not None:
            e_min, e_max = self._edge_scores.min(), self._edge_scores.max()
            self._stats_edge.setText(f"{e_min:.1f} - {e_max:.1f}")
            self._edge_slider.setRange(0, int(e_max * 1.1) + 1)
            self._edge_slider.setValue(int(e_min + (e_max - e_min) * 0.3))

        if self._intensities is not None:
            i_min, i_max = self._intensities.min(), self._intensities.max()
            self._stats_intensity.setText(f"{i_min:.1f} - {i_max:.1f}")

        if self._gradient_aniso is not None:
            g_min, g_max = self._gradient_aniso.min(), self._gradient_aniso.max()
            self._stats_gradient.setText(f"{g_min:.3f} - {g_max:.3f}")

        if self._dog_variances is not None:
            d_min, d_max = self._dog_variances.min(), self._dog_variances.max()
            self._stats_dog.setText(f"{d_min:.1f} - {d_max:.1f}")
            self._dog_threshold_spin.setRange(0.0, float(d_max * 1.1) + 1)
            pct25_dog = float(np.percentile(self._dog_variances, 25))
            self._dog_threshold_spin.setValue(round(pct25_dog, 1))

        logger.info("Tile metrics calculated")

    def _on_method_changed(self, index: int):
        """Handle detection method change."""
        method = self._method_combo.currentData()

        # Show/hide relevant control groups
        self._entropy_group.setVisible(method == "entropy")
        self._bandpass_group.setVisible(method == "bandpass")
        self._gradient_group.setVisible(method == "gradient")
        self._dog_group.setVisible(method == "dog")
        self._tube_group.setVisible(method == "tube_detect")
        self._variance_group.setVisible(method in ("variance", "combined"))
        self._edge_group.setVisible(method in ("edge", "combined"))
        self._intensity_group.setVisible(method == "intensity")

        self._update_preview()

    def _get_selected_tiles(self) -> Set[Tuple[int, int]]:
        """Calculate which tiles should be selected based on current settings."""
        method = self._method_combo.currentData()
        selected = set()

        if method == "entropy":
            if self._entropies is None:
                return selected
            threshold = self._entropy_threshold_spin.value()
            use_smoothed = self._smoothing_check.isChecked()
            scores = self._entropies_smoothed if use_smoothed else self._entropies
            if scores is None:
                scores = self._entropies

            for ty in range(self._tiles_y):
                for tx in range(self._tiles_x):
                    if scores[ty, tx] >= threshold:
                        selected.add((tx, ty))

        elif method == "bandpass":
            if self._variances is None or self._entropies is None:
                return selected
            var_min = self._bp_var_min_spin.value()
            var_max = self._bp_var_max_spin.value()
            ent_min = self._bp_entropy_min_spin.value()

            for ty in range(self._tiles_y):
                for tx in range(self._tiles_x):
                    v = self._variances[ty, tx]
                    e = self._entropies[ty, tx]
                    if var_min <= v <= var_max and e >= ent_min:
                        selected.add((tx, ty))

        elif method == "gradient":
            if self._gradient_aniso is None:
                return selected
            threshold = self._gradient_threshold_spin.value()
            # Low anisotropy = sample; select tiles BELOW threshold
            for ty in range(self._tiles_y):
                for tx in range(self._tiles_x):
                    if self._gradient_aniso[ty, tx] <= threshold:
                        selected.add((tx, ty))

        elif method == "dog":
            if self._dog_variances is None:
                return selected
            threshold = self._dog_threshold_spin.value()
            # High DoG variance = broad texture = sample
            for ty in range(self._tiles_y):
                for tx in range(self._tiles_x):
                    if self._dog_variances[ty, tx] >= threshold:
                        selected.add((tx, ty))

        elif method == "tube_detect":
            selected = self._detect_tube_tiles()

        elif method == "variance":
            if self._variances is None:
                return selected
            threshold = self._variance_slider.value()
            self._variance_value.setText(str(threshold))

            # Select tiles with variance ABOVE threshold (not background)
            for ty in range(self._tiles_y):
                for tx in range(self._tiles_x):
                    if self._variances[ty, tx] >= threshold:
                        selected.add((tx, ty))

        elif method == "edge":
            if self._edge_scores is None:
                return selected
            threshold = self._edge_slider.value()
            self._edge_value.setText(str(threshold))

            # Select tiles with edge score ABOVE threshold
            for ty in range(self._tiles_y):
                for tx in range(self._tiles_x):
                    if self._edge_scores[ty, tx] >= threshold:
                        selected.add((tx, ty))

        elif method == "intensity":
            if self._intensities is None:
                return selected
            min_int = self._intensity_min.value()
            max_int = self._intensity_max.value()

            # Select tiles with mean intensity in range
            for ty in range(self._tiles_y):
                for tx in range(self._tiles_x):
                    intensity = self._intensities[ty, tx]
                    if min_int <= intensity <= max_int:
                        selected.add((tx, ty))

        elif method == "combined":
            if self._variances is None or self._edge_scores is None:
                return selected
            var_threshold = self._variance_slider.value()
            edge_threshold = self._edge_slider.value()
            self._variance_value.setText(str(var_threshold))
            self._edge_value.setText(str(edge_threshold))

            # Select tiles that pass EITHER threshold
            for ty in range(self._tiles_y):
                for tx in range(self._tiles_x):
                    if (
                        self._variances[ty, tx] >= var_threshold
                        or self._edge_scores[ty, tx] >= edge_threshold
                    ):
                        selected.add((tx, ty))

        # Apply morphological cleanup if enabled
        if self._morphological_check.isChecked() and selected:
            selected = self._apply_morphological_cleanup(selected)

        # Apply inversion if checked
        if self._invert_check.isChecked():
            all_tiles = {
                (tx, ty) for tx in range(self._tiles_x) for ty in range(self._tiles_y)
            }
            selected = all_tiles - selected

        return selected

    def _detect_tube_tiles(self) -> Set[Tuple[int, int]]:
        """Two-stage tube detection: find tube boundaries, then classify interior."""
        from scipy.ndimage import gaussian_filter

        if len(self._image.shape) == 3:
            gray = np.mean(self._image, axis=2).astype(np.float64)
        else:
            gray = self._image.astype(np.float64)

        h, w = gray.shape
        tile_w = w // self._tiles_x
        sensitivity = self._tube_sensitivity_spin.value()

        # Stage 1: Column-wise mean intensity profile
        col_profile = np.mean(gray, axis=0)
        # Smooth the profile
        smoothed = gaussian_filter(col_profile, sigma=max(tile_w * 0.5, 5))
        # Gradient of smoothed profile
        gradient = np.gradient(smoothed)
        grad_abs = np.abs(gradient)

        # Find tube edges: large gradient magnitude
        grad_threshold = np.percentile(grad_abs, 95) * (1.0 - sensitivity * 0.8)
        grad_threshold = max(grad_threshold, np.std(grad_abs) * 0.5)
        edge_positions = np.where(grad_abs > grad_threshold)[0]

        if len(edge_positions) < 2:
            # Can't detect tube — fall back to all tiles
            logger.debug("Tube detection: insufficient edges found, selecting all")
            return {
                (tx, ty) for tx in range(self._tiles_x) for ty in range(self._tiles_y)
            }

        left_edge = edge_positions[0]
        right_edge = edge_positions[-1]

        # Map pixel edges to tile column range
        left_tile_col = left_edge // tile_w
        right_tile_col = min(right_edge // tile_w, self._tiles_x - 1)

        logger.debug(
            f"Tube detection: edges at px {left_edge}-{right_edge}, "
            f"tile cols {left_tile_col}-{right_tile_col}"
        )

        # Stage 2: Interior classification
        interior_method = self._tube_interior_method_combo.currentData()
        interior_threshold = self._tube_interior_threshold_spin.value()
        selected = set()

        for ty in range(self._tiles_y):
            for tx in range(self._tiles_x):
                # Exclude tiles outside tube
                if tx < left_tile_col or tx > right_tile_col:
                    continue

                # Apply interior method
                if interior_method == "entropy" and self._entropies is not None:
                    if self._entropies[ty, tx] >= interior_threshold:
                        selected.add((tx, ty))
                elif interior_method == "variance" and self._variances is not None:
                    if self._variances[ty, tx] >= interior_threshold:
                        selected.add((tx, ty))

        return selected

    def _apply_morphological_cleanup(
        self, selected: Set[Tuple[int, int]]
    ) -> Set[Tuple[int, int]]:
        """Apply morphological closing then opening to clean up tile selection."""
        from scipy.ndimage import binary_closing, binary_opening

        radius = self._morphological_radius_spin.value()

        # Convert set to boolean mask
        mask = np.zeros((self._tiles_y, self._tiles_x), dtype=bool)
        for tx, ty in selected:
            if 0 <= ty < self._tiles_y and 0 <= tx < self._tiles_x:
                mask[ty, tx] = True

        # Closing (fill gaps) then opening (remove isolated)
        mask = binary_closing(mask, iterations=radius)
        mask = binary_opening(mask, iterations=radius)

        # Convert back to set
        result = set()
        for ty in range(self._tiles_y):
            for tx in range(self._tiles_x):
                if mask[ty, tx]:
                    result.add((tx, ty))
        return result

    def _on_entropy_otsu(self):
        """Set entropy threshold using Otsu's method."""
        scores = (
            self._entropies_smoothed
            if self._smoothing_check.isChecked()
            else self._entropies
        )
        if scores is None:
            return
        threshold = otsu_threshold(scores)
        self._entropy_threshold_spin.setValue(round(threshold, 2))

    def _on_gradient_otsu(self):
        """Set gradient anisotropy threshold using Otsu's method."""
        if self._gradient_aniso is None:
            return
        threshold = otsu_threshold(self._gradient_aniso)
        self._gradient_threshold_spin.setValue(round(threshold, 2))

    def _on_dog_otsu(self):
        """Set DoG variance threshold using Otsu's method."""
        if self._dog_variances is None:
            return
        threshold = otsu_threshold(self._dog_variances)
        self._dog_threshold_spin.setValue(round(threshold, 1))

    def _on_dog_sigma_changed(self):
        """Recompute DoG variances when sigma values change."""
        try:
            self._dog_variances = calculate_tile_dog_variance(
                self._image,
                self._tiles_x,
                self._tiles_y,
                sigma1=self._dog_sigma1_spin.value(),
                sigma2=self._dog_sigma2_spin.value(),
            )
            if self._dog_variances is not None:
                d_min, d_max = self._dog_variances.min(), self._dog_variances.max()
                self._stats_dog.setText(f"{d_min:.1f} - {d_max:.1f}")
                self._dog_threshold_spin.setRange(0.0, float(d_max * 1.1) + 1)
        except Exception as e:
            logger.error(f"Failed to recalculate DoG variance: {e}")
        self._update_preview()

    def _update_preview(self):
        """Update the preview image with selection overlay."""
        # Get current selection
        self._selected_tiles = self._get_selected_tiles()

        # Update info label
        total_tiles = self._tiles_x * self._tiles_y
        self._info_label.setText(
            f"{len(self._selected_tiles)} / {total_tiles} tiles selected"
        )

        # Create preview image
        self._draw_preview()

    def _draw_preview(self):
        """Draw the preview image with tile overlay."""
        if self._image is None:
            return

        # Normalize to uint8 for display (handles uint16 camera images)
        display_img = self._image
        if display_img.dtype != np.uint8:
            if display_img.max() > 0:
                display_img = (
                    (display_img.astype(np.float64) / display_img.max()) * 255
                ).astype(np.uint8)
            else:
                display_img = np.zeros_like(display_img, dtype=np.uint8)

        # Convert numpy image to QPixmap
        if len(display_img.shape) == 3:
            h, w, c = display_img.shape
            if c == 4:
                fmt = QImage.Format_RGBA8888
            else:
                fmt = QImage.Format_RGB888

            # Ensure contiguous array
            img_data = np.ascontiguousarray(display_img)
            qimg = QImage(img_data.data, w, h, img_data.strides[0], fmt)
        else:
            h, w = display_img.shape
            # Convert grayscale to RGB for display
            rgb = np.stack([display_img] * 3, axis=-1)
            rgb = np.ascontiguousarray(rgb)
            qimg = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888)

        # Scale to fit preview area while maintaining aspect ratio
        preview_size = self._preview_label.size()
        pixmap = QPixmap.fromImage(qimg)

        # Draw overlay if enabled
        if self._preview_check.isChecked() and self._selected_tiles:
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)

            tile_w = w / self._tiles_x
            tile_h = h / self._tiles_y

            # Draw selected tiles with green outline
            painter.setBrush(Qt.NoBrush)
            pen = QPen(QColor(0, 255, 0, 200))
            pen.setWidth(max(2, int(min(tile_w, tile_h) / 20)))
            painter.setPen(pen)

            for tx, ty in self._selected_tiles:
                x = int(tx * tile_w)
                y = int(ty * tile_h)
                painter.drawRect(x, y, int(tile_w), int(tile_h))

            painter.end()

        # Scale to fit
        scaled = pixmap.scaled(
            preview_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self._preview_label.setPixmap(scaled)

    def _on_apply(self):
        """Apply the current selection."""
        if not self._selected_tiles:
            result = QMessageBox.question(
                self,
                "No Selection",
                "No tiles are selected. Apply anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if result != QMessageBox.Yes:
                return

        logger.info(
            f"Applying thresholder selection: {len(self._selected_tiles)} tiles"
        )
        self.selection_ready.emit(self._selected_tiles)
        self.accept()

    def get_selected_tiles(self) -> Set[Tuple[int, int]]:
        """Get the current tile selection.

        Returns:
            Set of (tile_x, tile_y) tuples
        """
        return self._selected_tiles.copy()

    def _save_dialog_state(self) -> None:
        """Save all dialog settings for persistence across sessions."""
        if not self._geometry_manager:
            return

        state = {
            "method": self._method_combo.currentData(),
            "entropy_threshold": self._entropy_threshold_spin.value(),
            "smoothing": self._smoothing_check.isChecked(),
            "bp_var_min": self._bp_var_min_spin.value(),
            "bp_var_max": self._bp_var_max_spin.value(),
            "bp_entropy_min": self._bp_entropy_min_spin.value(),
            "gradient_threshold": self._gradient_threshold_spin.value(),
            "dog_sigma1": self._dog_sigma1_spin.value(),
            "dog_sigma2": self._dog_sigma2_spin.value(),
            "dog_threshold": self._dog_threshold_spin.value(),
            "tube_interior_method": self._tube_interior_method_combo.currentData(),
            "tube_interior_threshold": self._tube_interior_threshold_spin.value(),
            "tube_sensitivity": self._tube_sensitivity_spin.value(),
            "variance": self._variance_slider.value(),
            "edge": self._edge_slider.value(),
            "intensity_min": self._intensity_min.value(),
            "intensity_max": self._intensity_max.value(),
            "invert": self._invert_check.isChecked(),
            "preview_overlay": self._preview_check.isChecked(),
            "morphological_cleanup": self._morphological_check.isChecked(),
            "morphological_radius": self._morphological_radius_spin.value(),
        }

        try:
            self._geometry_manager.save_dialog_state("OverviewThresholderDialog", state)
            self._geometry_manager.save_all()
            logger.debug("Saved OverviewThresholderDialog state")
        except Exception as e:
            logger.warning(f"Failed to save overview thresholder state: {e}")

    def _restore_dialog_state(self) -> None:
        """Restore dialog settings from persistence."""
        if not self._geometry_manager:
            return

        try:
            state = self._geometry_manager.restore_dialog_state(
                "OverviewThresholderDialog"
            )
        except Exception as e:
            logger.warning(f"Failed to restore overview thresholder state: {e}")
            return

        if not state:
            return

        logger.debug("Restoring OverviewThresholderDialog state")

        # Block signals during restore to avoid cascading updates
        widgets = [
            self._method_combo,
            self._entropy_threshold_spin,
            self._smoothing_check,
            self._bp_var_min_spin,
            self._bp_var_max_spin,
            self._bp_entropy_min_spin,
            self._gradient_threshold_spin,
            self._dog_sigma1_spin,
            self._dog_sigma2_spin,
            self._dog_threshold_spin,
            self._tube_interior_method_combo,
            self._tube_interior_threshold_spin,
            self._tube_sensitivity_spin,
            self._variance_slider,
            self._edge_slider,
            self._intensity_min,
            self._intensity_max,
            self._invert_check,
            self._preview_check,
            self._morphological_check,
            self._morphological_radius_spin,
        ]
        for w in widgets:
            w.blockSignals(True)

        try:
            # Restore method by matching data key, not index
            if "method" in state:
                for i in range(self._method_combo.count()):
                    if self._method_combo.itemData(i) == state["method"]:
                        self._method_combo.setCurrentIndex(i)
                        break

            if "entropy_threshold" in state:
                self._entropy_threshold_spin.setValue(state["entropy_threshold"])
            if "smoothing" in state:
                self._smoothing_check.setChecked(state["smoothing"])
            if "bp_var_min" in state:
                self._bp_var_min_spin.setValue(state["bp_var_min"])
            if "bp_var_max" in state:
                self._bp_var_max_spin.setValue(state["bp_var_max"])
            if "bp_entropy_min" in state:
                self._bp_entropy_min_spin.setValue(state["bp_entropy_min"])
            if "gradient_threshold" in state:
                self._gradient_threshold_spin.setValue(state["gradient_threshold"])
            if "dog_sigma1" in state:
                self._dog_sigma1_spin.setValue(state["dog_sigma1"])
            if "dog_sigma2" in state:
                self._dog_sigma2_spin.setValue(state["dog_sigma2"])
            if "dog_threshold" in state:
                self._dog_threshold_spin.setValue(state["dog_threshold"])
            if "tube_interior_method" in state:
                for i in range(self._tube_interior_method_combo.count()):
                    if (
                        self._tube_interior_method_combo.itemData(i)
                        == state["tube_interior_method"]
                    ):
                        self._tube_interior_method_combo.setCurrentIndex(i)
                        break
            if "tube_interior_threshold" in state:
                self._tube_interior_threshold_spin.setValue(
                    state["tube_interior_threshold"]
                )
            if "tube_sensitivity" in state:
                self._tube_sensitivity_spin.setValue(state["tube_sensitivity"])
            if "variance" in state:
                self._variance_slider.setValue(state["variance"])
            if "edge" in state:
                self._edge_slider.setValue(state["edge"])
            if "intensity_min" in state:
                self._intensity_min.setValue(state["intensity_min"])
            if "intensity_max" in state:
                self._intensity_max.setValue(state["intensity_max"])
            if "invert" in state:
                self._invert_check.setChecked(state["invert"])
            if "preview_overlay" in state:
                self._preview_check.setChecked(state["preview_overlay"])
            if "morphological_cleanup" in state:
                self._morphological_check.setChecked(state["morphological_cleanup"])
            if "morphological_radius" in state:
                self._morphological_radius_spin.setValue(state["morphological_radius"])
        finally:
            for w in widgets:
                w.blockSignals(False)

        # Update visibility for restored method
        self._on_method_changed(self._method_combo.currentIndex())

    def hideEvent(self, event):
        """Save dialog state when hiding."""
        self._save_dialog_state()
        super().hideEvent(event)

    def closeEvent(self, event):
        """Save dialog state when closing."""
        self._save_dialog_state()
        super().closeEvent(event)
