"""Focus detection utilities for LED 2D Overview.

This module provides functions to evaluate image focus quality,
used for selecting the best-focused frame from a Z-stack.
"""

import numpy as np
from typing import Optional, Tuple, List
import logging

logger = logging.getLogger(__name__)


def variance_of_laplacian(image: np.ndarray) -> float:
    """Calculate focus measure using Laplacian variance.

    This is a widely-used focus metric that measures the amount of
    high-frequency content in an image. Higher values indicate better focus.

    The Laplacian operator highlights edges and rapid intensity changes,
    which are more pronounced in well-focused images.

    Args:
        image: Input image as numpy array (grayscale or color)

    Returns:
        Focus score (higher = more in-focus)
    """
    try:
        import cv2
    except ImportError:
        # Fallback to numpy-only implementation
        return _variance_of_laplacian_numpy(image)

    # Convert to grayscale if needed
    if len(image.shape) == 3:
        if image.shape[2] == 4:  # RGBA
            gray = cv2.cvtColor(image, cv2.COLOR_RGBA2GRAY)
        else:  # RGB/BGR
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image

    # Calculate Laplacian
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)

    # Return variance
    return float(laplacian.var())


def _variance_of_laplacian_numpy(image: np.ndarray) -> float:
    """Pure numpy implementation of Laplacian variance.

    Used as fallback when OpenCV is not available.

    Args:
        image: Input image as numpy array

    Returns:
        Focus score (higher = more in-focus)
    """
    # Convert to grayscale if needed
    if len(image.shape) == 3:
        # Use luminosity method
        if image.shape[2] >= 3:
            gray = 0.299 * image[:, :, 0] + 0.587 * image[:, :, 1] + 0.114 * image[:, :, 2]
        else:
            gray = image[:, :, 0]
        gray = gray.astype(np.float64)
    else:
        gray = image.astype(np.float64)

    # Laplacian kernel
    kernel = np.array([[0, 1, 0],
                       [1, -4, 1],
                       [0, 1, 0]], dtype=np.float64)

    # Apply convolution (simplified without scipy)
    from numpy.lib.stride_tricks import sliding_window_view

    # Pad image
    padded = np.pad(gray, 1, mode='edge')

    # Use sliding window for convolution
    windows = sliding_window_view(padded, (3, 3))

    # Apply kernel
    laplacian = np.sum(windows * kernel, axis=(2, 3))

    return float(np.var(laplacian))


def brenner_gradient(image: np.ndarray) -> float:
    """Calculate focus using Brenner gradient.

    Alternative focus metric that measures the gradient between
    pixels separated by a distance of 2.

    Args:
        image: Input image as numpy array

    Returns:
        Focus score (higher = more in-focus)
    """
    # Convert to grayscale if needed
    if len(image.shape) == 3:
        if image.shape[2] >= 3:
            gray = 0.299 * image[:, :, 0] + 0.587 * image[:, :, 1] + 0.114 * image[:, :, 2]
        else:
            gray = image[:, :, 0]
        gray = gray.astype(np.float64)
    else:
        gray = image.astype(np.float64)

    # Calculate horizontal gradient with step of 2
    h_diff = gray[:, 2:] - gray[:, :-2]

    # Calculate vertical gradient with step of 2
    v_diff = gray[2:, :] - gray[:-2, :]

    # Return sum of squared differences
    return float(np.sum(h_diff ** 2) + np.sum(v_diff ** 2))


def tenengrad(image: np.ndarray) -> float:
    """Calculate focus using Tenengrad method.

    Uses Sobel operators to compute gradient magnitude.

    Args:
        image: Input image as numpy array

    Returns:
        Focus score (higher = more in-focus)
    """
    try:
        import cv2

        # Convert to grayscale if needed
        if len(image.shape) == 3:
            if image.shape[2] == 4:
                gray = cv2.cvtColor(image, cv2.COLOR_RGBA2GRAY)
            else:
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        # Calculate Sobel gradients
        gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)

        # Return sum of squared gradient magnitudes
        return float(np.sum(gx ** 2 + gy ** 2))

    except ImportError:
        # Fallback: use Brenner gradient
        return brenner_gradient(image)


def find_best_focus(images: List[np.ndarray],
                    z_positions: List[float],
                    method: str = 'laplacian') -> Tuple[int, float, np.ndarray]:
    """Find the best-focused image from a Z-stack.

    Args:
        images: List of images from Z-stack
        z_positions: Corresponding Z positions for each image
        method: Focus metric to use ('laplacian', 'brenner', 'tenengrad')

    Returns:
        Tuple of (best_index, best_z_position, best_image)

    Raises:
        ValueError: If images or z_positions are empty or mismatched
    """
    if not images or not z_positions:
        raise ValueError("Images and z_positions cannot be empty")

    if len(images) != len(z_positions):
        raise ValueError(f"Mismatch: {len(images)} images vs {len(z_positions)} positions")

    # Select focus metric
    if method == 'laplacian':
        focus_fn = variance_of_laplacian
    elif method == 'brenner':
        focus_fn = brenner_gradient
    elif method == 'tenengrad':
        focus_fn = tenengrad
    else:
        raise ValueError(f"Unknown focus method: {method}")

    # Calculate focus scores
    scores = []
    for i, img in enumerate(images):
        try:
            score = focus_fn(img)
            scores.append(score)
        except Exception as e:
            logger.warning(f"Error calculating focus score for image {i}: {e}")
            scores.append(0.0)

    # Find best
    best_idx = int(np.argmax(scores))
    best_z = z_positions[best_idx]
    best_img = images[best_idx]

    logger.debug(f"Best focus at index {best_idx}, Z={best_z:.3f}mm, score={scores[best_idx]:.2f}")

    return best_idx, best_z, best_img


def evaluate_focus_quality(image: np.ndarray) -> str:
    """Evaluate the overall focus quality of an image.

    Args:
        image: Input image

    Returns:
        Quality assessment string ('poor', 'fair', 'good', 'excellent')
    """
    score = variance_of_laplacian(image)

    # These thresholds are empirical and may need adjustment
    # based on the specific imaging conditions
    if score < 100:
        return 'poor'
    elif score < 500:
        return 'fair'
    elif score < 1000:
        return 'good'
    else:
        return 'excellent'
