"""Webcam overview session save/load utilities.

Zarr-based persistence for WebcamSession, using the shared 2D session
infrastructure from zarr_2d_session.py.

Session structure:
    webcam_overview_{timestamp}.zarr/
      .zattrs                          # format_version, format_type, session_metadata
      view_0/
        image/                         # RGB uint8 array (H, W, 3)
      view_1/
        image/
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import numpy as np

from py2flamingo.models.data.webcam_models import WebcamAngleView, WebcamSession

logger = logging.getLogger(__name__)


def save_webcam_session(session: WebcamSession, parent_folder: Path) -> Path:
    """Save a WebcamSession to a Zarr store.

    Creates a timestamped .zarr folder inside parent_folder.

    Args:
        session: The WebcamSession to save.
        parent_folder: Directory in which to create the .zarr folder.

    Returns:
        Path to the created .zarr folder.
    """
    from py2flamingo.visualization.zarr_2d_session import save_2d_zarr_session

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zarr_name = f"webcam_overview_{timestamp}.zarr"
    save_path = parent_folder / zarr_name

    # Build metadata
    metadata = session.to_dict()
    # Remove image data from metadata (saved separately as Zarr datasets)
    for view_meta in metadata.get("views", []):
        view_meta.pop("image", None)

    # Build images dict
    images_dict: Dict[str, np.ndarray] = {}
    for i, view in enumerate(session.views):
        if view.image is not None:
            images_dict[f"view_{i}/image"] = view.image

    save_2d_zarr_session(save_path, metadata, images_dict, "webcam_overview")
    logger.info(
        f"Webcam session saved: {save_path} "
        f"({len(session.views)} views, {len(images_dict)} images)"
    )
    return save_path


def load_webcam_session(folder_path: Path) -> WebcamSession:
    """Load a WebcamSession from a Zarr store.

    Args:
        folder_path: Path to the .zarr folder.

    Returns:
        WebcamSession with images loaded.

    Raises:
        FileNotFoundError: If folder doesn't exist.
        ValueError: If format_type doesn't match.
    """
    from py2flamingo.visualization.zarr_2d_session import load_2d_zarr_session

    if not folder_path.exists():
        raise FileNotFoundError(f"Session folder not found: {folder_path}")

    metadata, images_dict = load_2d_zarr_session(folder_path)

    format_type = metadata.get("format_type", "")
    if format_type != "webcam_overview":
        raise ValueError(f"Expected format_type 'webcam_overview', got '{format_type}'")

    session_data = metadata.get("session_metadata", metadata)

    # Reconstruct images by view index
    view_images: Dict[int, np.ndarray] = {}
    for key, image in images_dict.items():
        # Keys like "view_0/image" -> index 0
        parts = key.split("/")
        if len(parts) >= 1 and parts[0].startswith("view_"):
            try:
                idx = int(parts[0].split("_")[1])
                view_images[idx] = image
            except (ValueError, IndexError):
                logger.warning(f"Unexpected image key: {key}")

    session = WebcamSession.from_dict(session_data, images=view_images)

    logger.info(
        f"Webcam session loaded: {folder_path} " f"({len(session.views)} views)"
    )
    return session


def load_webcam_session_metadata(folder_path: Path) -> Optional[dict]:
    """Load only the metadata (no images) for quick inspection.

    Returns:
        Session metadata dict, or None on error.
    """
    try:
        from py2flamingo.visualization.zarr_2d_session import (
            load_2d_zarr_session_lazy,
        )

        metadata, _ = load_2d_zarr_session_lazy(folder_path)
        return metadata.get("session_metadata", metadata)
    except Exception as e:
        logger.error(f"Error loading webcam session metadata: {e}")
        return None
