# src/py2flamingo/utils/image_processing.py
"""
Utility functions for image processing, conversion, and saving.

Restored percentile scaling and 512×512 downsampling behavior
from the pre-refactor implementation.
"""

import os
import time
import numpy as np
from PIL import Image
from PyQt5.QtGui import QImage

# -------------------------
# Image saving (PNG)
# -------------------------

def save_png(image_data: np.ndarray, image_title: str) -> None:
    """
    Save a 16-bit (or float) 2D numpy array as a downsized PNG image.

    - Clips to the 2.5–97.5 percentile window
    - Normalizes to [0, 1]
    - Converts to 8-bit
    - Resizes to 512×512
    - Writes to output_png/{image_title}.png
    """
    # Ensure numpy array (make a copy to avoid modifying caller’s memory)
    img = np.asarray(image_data)

    # Percentile-based display window
    lower_p, upper_p = 2.5, 97.5
    lo = np.percentile(img, lower_p)
    hi = np.percentile(img, upper_p)

    # Avoid divide-by-zero
    if hi <= lo:
        lo, hi = float(img.min()), float(img.max())
        if hi <= lo:  # fully constant image
            hi = lo + 1.0

    # Clip + normalize
    img_clipped = np.clip(img, lo, hi)
    img_norm = (img_clipped - lo) / (hi - lo)

    # 8-bit
    img_u8 = (img_norm * 255.0).astype(np.uint8)

    # Downsample to 512×512 with bilinear
    pil = Image.fromarray(img_u8)
    pil_resized = pil.resize((512, 512), resample=Image.BILINEAR)

    # Ensure output dir
    out_dir = "output_png"
    os.makedirs(out_dir, exist_ok=True)

    # Save
    pil_resized.save(os.path.join(out_dir, f"{image_title}.png"), "PNG")


# -------------------------
# QImage conversion
# -------------------------

def convert_to_qimage(image_data: np.ndarray) -> QImage:
    """
    Convert a 16-bit (or float) grayscale numpy array to a 512×512 8-bit QImage
    using percentile windowing (1–99%) and bilinear resize.
    """
    t0 = time.time()

    img = np.asarray(image_data)

    # Convert to PIL, force grayscale first (handles >8-bit nicely)
    pil = Image.fromarray(img).convert("L")
    pil_scaled = pil.resize((512, 512), resample=Image.BILINEAR)

    # Convert back to numpy for percentile windowing
    arr = np.asarray(pil_scaled, dtype=np.float32)

    # Percentile scaling (1–99%)
    lo = np.percentile(arr, 1.0)
    hi = np.percentile(arr, 99.0)
    if hi <= lo:
        lo, hi = float(arr.min()), float(arr.max())
        if hi <= lo:
            hi = lo + 1.0

    arr = np.clip(arr, lo, hi)
    arr = (arr - lo) / (hi - lo + 1e-7)
    arr_u8 = (arr * 255.0).astype(np.uint8, copy=False)

    h, w = arr_u8.shape
    bytes_per_line = w  # grayscale

    # Important: ensure the array is C-contiguous so QImage can read it
    if not arr_u8.flags['C_CONTIGUOUS']:
        arr_u8 = np.ascontiguousarray(arr_u8)

    qimg = QImage(arr_u8.data, w, h, bytes_per_line, QImage.Format_Grayscale8).copy()
    # ^ .copy() makes QImage own the data (safe once function returns)

    # print(f"convert_to_qimage took {time.time() - t0:.3f}s")
    return qimg
