"""
Image transformation utilities for live feed display.

This module provides functions for transforming microscope images:
- Rotation (90° increments)
- Flipping (horizontal/vertical)
- Downsampling
- Colormap application
"""
import numpy as np
from typing import Tuple, Optional
from enum import Enum


class Rotation(Enum):
    """Rotation angles for image display."""
    NONE = 0
    CW_90 = 90
    CW_180 = 180
    CW_270 = 270


class Colormap(Enum):
    """Available colormaps for image display."""
    GRAY = "gray"
    VIRIDIS = "viridis"
    PLASMA = "plasma"
    INFERNO = "inferno"
    MAGMA = "magma"
    TURBO = "turbo"
    JET = "jet"
    HOT = "hot"
    COOL = "cool"


def rotate_image(image: np.ndarray, rotation: Rotation) -> np.ndarray:
    """
    Rotate image by 90° increments.

    Args:
        image: Input image array (H, W) or (H, W, C)
        rotation: Rotation angle (0, 90, 180, 270)

    Returns:
        np.ndarray: Rotated image

    Example:
        >>> img = np.random.randint(0, 255, (512, 512), dtype=np.uint16)
        >>> rotated = rotate_image(img, Rotation.CW_90)
        >>> rotated.shape
        (512, 512)
    """
    if rotation == Rotation.NONE:
        return image

    # Calculate number of 90° rotations
    k = rotation.value // 90

    # numpy.rot90 rotates counterclockwise, so negate for clockwise
    return np.rot90(image, k=-k)


def flip_image(image: np.ndarray,
               flip_horizontal: bool = False,
               flip_vertical: bool = False) -> np.ndarray:
    """
    Flip image horizontally and/or vertically.

    Args:
        image: Input image array
        flip_horizontal: If True, flip left-right
        flip_vertical: If True, flip up-down

    Returns:
        np.ndarray: Flipped image

    Example:
        >>> img = np.random.randint(0, 255, (512, 512), dtype=np.uint16)
        >>> flipped = flip_image(img, flip_horizontal=True)
    """
    result = image

    if flip_horizontal:
        result = np.fliplr(result)

    if flip_vertical:
        result = np.flipud(result)

    return result


def downsample_image(image: np.ndarray, factor: int) -> np.ndarray:
    """
    Downsample image by integer factor using simple decimation.

    Args:
        image: Input image array (H, W) or (H, W, C)
        factor: Downsampling factor (1, 2, 4, 8, etc.)

    Returns:
        np.ndarray: Downsampled image

    Example:
        >>> img = np.random.randint(0, 255, (512, 512), dtype=np.uint16)
        >>> downsampled = downsample_image(img, factor=2)
        >>> downsampled.shape
        (256, 256)

    Notes:
        - Uses simple decimation (every Nth pixel)
        - For better quality, consider using scipy.ndimage.zoom
        - Preserves data type of input image
    """
    if factor <= 1:
        return image

    # Simple decimation - take every factor-th pixel
    if image.ndim == 2:
        return image[::factor, ::factor]
    elif image.ndim == 3:
        return image[::factor, ::factor, :]
    else:
        raise ValueError(f"Expected 2D or 3D image, got {image.ndim}D")


def normalize_to_uint8(image: np.ndarray,
                       percentile_low: float = 1.0,
                       percentile_high: float = 99.0) -> np.ndarray:
    """
    Normalize image to uint8 range using percentile clipping.

    Args:
        image: Input image (any dtype)
        percentile_low: Lower percentile for clipping (default: 1%)
        percentile_high: Upper percentile for clipping (default: 99%)

    Returns:
        np.ndarray: Uint8 image in range [0, 255]

    Example:
        >>> img_16bit = np.random.randint(0, 65535, (512, 512), dtype=np.uint16)
        >>> img_8bit = normalize_to_uint8(img_16bit)
        >>> img_8bit.dtype
        dtype('uint8')
    """
    # Convert to float for processing
    img_float = image.astype(np.float32)

    # Calculate percentile bounds
    lo = np.percentile(img_float, percentile_low)
    hi = np.percentile(img_float, percentile_high)

    # Avoid divide-by-zero
    if hi <= lo:
        lo = float(img_float.min())
        hi = float(img_float.max())
        if hi <= lo:
            hi = lo + 1.0

    # Clip and normalize to [0, 1]
    img_clipped = np.clip(img_float, lo, hi)
    img_norm = (img_clipped - lo) / (hi - lo)

    # Scale to [0, 255] and convert to uint8
    img_u8 = (img_norm * 255.0).astype(np.uint8)

    return img_u8


def apply_colormap(image: np.ndarray, colormap: Colormap) -> np.ndarray:
    """
    Apply colormap to grayscale image.

    Args:
        image: Input grayscale image (H, W) - should be uint8
        colormap: Colormap to apply

    Returns:
        np.ndarray: RGB image (H, W, 3) with colormap applied

    Example:
        >>> img = np.random.randint(0, 255, (512, 512), dtype=np.uint8)
        >>> colored = apply_colormap(img, Colormap.VIRIDIS)
        >>> colored.shape
        (512, 512, 3)

    Notes:
        - Requires matplotlib for colormap lookups
        - Returns RGB image with values in [0, 255]
        - If colormap is GRAY, returns grayscale image as-is
    """
    if colormap == Colormap.GRAY:
        # Return as-is (or expand to 3 channels if needed)
        if image.ndim == 2:
            return image
        else:
            return image

    # Import matplotlib colormap
    try:
        import matplotlib.pyplot as plt
        import matplotlib.cm as cm
    except ImportError:
        # Fallback to grayscale if matplotlib not available
        return image

    # Ensure image is uint8 in range [0, 255]
    if image.dtype != np.uint8:
        image = normalize_to_uint8(image)

    # Normalize to [0, 1] for colormap
    img_norm = image.astype(np.float32) / 255.0

    # Get colormap function
    cmap_func = cm.get_cmap(colormap.value)

    # Apply colormap (returns RGBA)
    img_rgba = cmap_func(img_norm)

    # Convert to RGB uint8
    img_rgb = (img_rgba[:, :, :3] * 255).astype(np.uint8)

    return img_rgb


def apply_transforms(image: np.ndarray,
                    rotation: Rotation = Rotation.NONE,
                    flip_horizontal: bool = False,
                    flip_vertical: bool = False,
                    downsample_factor: int = 1,
                    colormap: Colormap = Colormap.GRAY,
                    normalize: bool = True) -> np.ndarray:
    """
    Apply complete transformation pipeline to image.

    This is a convenience function that applies all transformations in sequence:
    1. Rotate
    2. Flip
    3. Downsample
    4. Normalize to uint8 (if needed)
    5. Apply colormap

    Args:
        image: Input image (any dtype, typically uint16 from microscope)
        rotation: Rotation angle
        flip_horizontal: Flip left-right
        flip_vertical: Flip up-down
        downsample_factor: Downsampling factor
        colormap: Colormap to apply
        normalize: Whether to normalize to uint8 before colormap

    Returns:
        np.ndarray: Transformed image ready for display

    Example:
        >>> img_raw = np.random.randint(0, 65535, (512, 512), dtype=np.uint16)
        >>> img_display = apply_transforms(
        ...     img_raw,
        ...     rotation=Rotation.CW_90,
        ...     flip_horizontal=True,
        ...     downsample_factor=2,
        ...     colormap=Colormap.VIRIDIS
        ... )
        >>> img_display.shape
        (256, 256, 3)
    """
    # Start with input image
    result = image.copy()

    # 1. Rotate
    if rotation != Rotation.NONE:
        result = rotate_image(result, rotation)

    # 2. Flip
    if flip_horizontal or flip_vertical:
        result = flip_image(result, flip_horizontal, flip_vertical)

    # 3. Downsample
    if downsample_factor > 1:
        result = downsample_image(result, downsample_factor)

    # 4. Normalize to uint8 (if not already)
    if normalize and result.dtype != np.uint8:
        result = normalize_to_uint8(result)

    # 5. Apply colormap
    if colormap != Colormap.GRAY:
        result = apply_colormap(result, colormap)

    return result
