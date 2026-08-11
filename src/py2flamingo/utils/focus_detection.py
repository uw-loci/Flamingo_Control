"""Focus detection utilities for LED 2D Overview.

This module provides functions to evaluate image focus quality,
used for selecting the best-focused frame from a Z-stack.
"""

import logging
from typing import List, Optional, Tuple

import numpy as np

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
            gray = (
                0.299 * image[:, :, 0] + 0.587 * image[:, :, 1] + 0.114 * image[:, :, 2]
            )
        else:
            gray = image[:, :, 0]
        gray = gray.astype(np.float64)
    else:
        gray = image.astype(np.float64)

    # Laplacian kernel
    kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)

    # Apply convolution (simplified without scipy)
    from numpy.lib.stride_tricks import sliding_window_view

    # Pad image
    padded = np.pad(gray, 1, mode="edge")

    # Use sliding window for convolution
    windows = sliding_window_view(padded, (3, 3))

    # Apply kernel
    laplacian = np.sum(windows * kernel, axis=(2, 3))

    return float(np.var(laplacian))
