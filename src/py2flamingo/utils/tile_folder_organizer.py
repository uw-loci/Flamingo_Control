"""Tile folder reorganization utilities.

Pure file I/O function for reorganizing flattened tile folders into
nested structure, extracted from tile_collection_dialog.py.
"""

import logging
import shutil
from pathlib import Path
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


def reorganize_tile_folders(
    local_path: str,
    base_save_directory: str,
    tile_folder_mapping: Dict[str, Tuple[str, str]],
    local_access_enabled: bool = False
) -> bool:
    """Reorganize flattened folders into nested structure for MIP Overview compatibility.

    Moves: base_date_tile/ -> base/date/tile/

    Only runs if local path was configured in save settings and is accessible.
    This function should be called AFTER queue_completed signal, which guarantees all
    workflows have finished and all files are written.

    Args:
        local_path: Local drive path (e.g. 'G:\\CTLSM1')
        base_save_directory: Base save directory name
        tile_folder_mapping: Maps flattened_name -> (date_folder, tile_folder)
        local_access_enabled: Whether local access was enabled in save settings

    Returns:
        True if any folders were reorganized, False otherwise
    """
    # Check if we have folder mapping
    if not tile_folder_mapping:
        logger.debug("No tile folder mapping - skipping reorganization")
        return False

    # Check if local access was enabled
    if not local_access_enabled:
        logger.info("Local access not enabled - skipping folder reorganization")
        return False

    if not local_path:
        logger.info("No local path configured - skipping folder reorganization")
        return False

    local_base = Path(local_path)
    if not local_base.exists():
        logger.warning(f"Local drive path does not exist: {local_base} - skipping reorganization")
        return False

    logger.info(f"Starting folder reorganization: {local_base}")
    reorganized_count = 0

    # Find the timestamped folders created by server
    # They'll be named like: 20260127_123617_Test_2026-01-27_X11.09_Y14.46
    for flattened_name, (date_folder, tile_folder) in tile_folder_mapping.items():
        # Search for matching folder (with any timestamp prefix)
        # Pattern: *_{flattened_name} where flattened_name is like "Test_2026-01-27_X11.09_Y14.46"
        pattern = f"*_{flattened_name}"
        matching_folders = list(local_base.glob(pattern))

        if not matching_folders:
            logger.warning(f"Could not find folder matching pattern: {pattern}")
            continue

        for src_folder in matching_folders:
            if not src_folder.is_dir():
                continue

            # Target nested structure: base/date/tile/
            dest_folder = local_base / base_save_directory / date_folder / tile_folder

            try:
                dest_folder.mkdir(parents=True, exist_ok=True)

                # Move contents (not the folder itself)
                items_moved = 0
                for item in src_folder.iterdir():
                    dest_path = dest_folder / item.name
                    # Handle existing files by overwriting
                    if dest_path.exists():
                        if dest_path.is_dir():
                            shutil.rmtree(str(dest_path))
                        else:
                            dest_path.unlink()
                    shutil.move(str(item), str(dest_path))
                    items_moved += 1
                    logger.debug(f"Moved: {item.name} -> {dest_path}")

                # Remove now-empty source folder
                try:
                    src_folder.rmdir()
                except OSError:
                    # Folder not empty (might have hidden files)
                    logger.warning(f"Could not remove source folder (not empty): {src_folder}")

                logger.info(f"Reorganized: {src_folder.name} -> {base_save_directory}/{date_folder}/{tile_folder}/ ({items_moved} items)")
                reorganized_count += 1

            except Exception as e:
                logger.error(f"Failed to reorganize {src_folder}: {e}")

    if reorganized_count > 0:
        logger.info(f"Tile folder reorganization complete: {reorganized_count} folders moved")
    else:
        logger.info("No folders were reorganized")

    return reorganized_count > 0
