"""Per-tile image metrics for picking sample tiles in a 2D overview.

Pure numpy (scipy is imported inside the functions that need it). The
Overview Thresholder dialog and the pipeline's OVERVIEW_ANALYSIS node both
score tiles with these, so they live below both of them.
"""

import numpy as np


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
