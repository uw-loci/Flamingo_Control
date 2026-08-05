"""Tile folder reorganization utilities.

Pure file I/O function for reorganizing flattened tile folders into
nested structure, extracted from tile_collection_dialog.py.

Background: the server can only create a SINGLE directory level, so tile
collection asks it for ``<base>_<date>_X<x>_Y<y>`` and the server prefixes its
own timestamp, giving e.g.::

    D:/CTLSM1/20260805_011617_BrainSingleChannel2_2026-08-05_X4.47_Y17.17/

After the run we move those into the nested layout every downstream tool
(MIP Overview, the stitcher) expects::

    D:/CTLSM1/BrainSingleChannel2/2026-08-05/X4.47_Y17.17/

That move is only possible when this PC can see the server's drive, so the
skip reasons are reported rather than silently swallowed -- a skipped
reorganization looks exactly like a successful run until the user goes
looking for the data.
"""

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class ReorganizeResult:
    """Outcome of a folder reorganization pass.

    Attributes:
        moved: Number of source folders successfully relocated.
        skip_reason: Human-readable reason the pass did not run at all, or
            None if it did run (``moved`` may still be 0).
        unmatched: Flattened names that had no matching folder on disk.
        failed: Source folders that raised while being moved.
    """

    moved: int = 0
    skip_reason: Optional[str] = None
    unmatched: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)

    @property
    def ran(self) -> bool:
        """True if the pass executed (as opposed to being skipped)."""
        return self.skip_reason is None

    def __bool__(self) -> bool:
        # Callers historically treated the return value as "did anything move?".
        return self.moved > 0

    def summary(self) -> str:
        """One-line, user-facing description of what happened."""
        if self.skip_reason:
            return f"Folders left in flat layout: {self.skip_reason}"
        parts = [f"{self.moved} folder(s) reorganized into nested layout"]
        if self.unmatched:
            parts.append(f"{len(self.unmatched)} not found on disk")
        if self.failed:
            parts.append(f"{len(self.failed)} failed to move")
        return "; ".join(parts)


def reorganization_skip_reason(
    local_path: Optional[str],
    local_access_enabled: bool = False,
) -> Optional[str]:
    """Return why reorganization cannot run, or None if it can.

    Split out from :func:`reorganize_tile_folders` so callers can warn the
    user BEFORE a long acquisition instead of after, when sorting the folders
    out by hand is the only remaining option.

    Args:
        local_path: Local mount point of the server's save drive.
        local_access_enabled: Whether local access was enabled in save settings.

    Returns:
        A human-readable reason string, or None when reorganization can run.
    """
    if not local_access_enabled:
        return (
            "'Enable post-processing' is off in the Save settings, so this PC "
            "will not move the server's folders"
        )
    if not local_path:
        return (
            "no local path is configured for the save drive (use Browse... in "
            "the Save settings' Local Access row)"
        )
    if not Path(local_path).exists():
        return f"the configured local path is not accessible: {local_path}"
    return None


def infer_local_drive_root(
    base_folder: Path,
    date_folder: str = "",
    layout_type: str = "subfolder",
) -> Optional[Path]:
    """Infer the local mount of the save drive from a loaded acquisition path.

    The drive root is the directory the server drops ``<timestamp>_<name>``
    folders into -- i.e. the parent of the per-sample folder. Which ancestor
    of the browsed folder that is depends on how the user navigated, so this
    cannot be a blanket ``.parent``:

    * ``flat``: the timestamped folders were found in the browsed folder
      itself, so that folder IS the drive root.
    * ``subfolder`` with a date subfolder: browsed folder is ``<root>/<sample>``
      -> root is its parent.
    * ``subfolder`` with no date subfolder: browsed folder is either
      ``<root>/<sample>`` or ``<root>/<sample>/<date>``; a YYYY-MM-DD name
      means the latter, so climb one extra level.

    Args:
        base_folder: Folder the user browsed to.
        date_folder: Selected date subfolder name, "" when loading in place.
        layout_type: "flat" or "subfolder".

    Returns:
        Best guess at the drive root, or None if it cannot be determined.
    """
    if base_folder is None:
        return None

    base_folder = Path(base_folder)

    if layout_type == "flat":
        # Flat folders sit directly in the drive root; date_folder is a
        # subdirectory the user drilled into, and that is where they were found.
        return base_folder / date_folder if date_folder else base_folder

    sample_folder = base_folder
    if not date_folder and _DATE_RE.match(base_folder.name):
        # Browsed straight into <root>/<sample>/<date>.
        sample_folder = base_folder.parent

    root = sample_folder.parent
    # Refuse to hand back a filesystem root ("D:\\", "/"): that means we ran
    # out of levels and the guess is meaningless.
    if root == sample_folder or not root.name:
        return None
    return root


def reorganize_tile_folders(
    local_path: str,
    base_save_directory: str,
    tile_folder_mapping: Dict[str, Tuple[str, str]],
    local_access_enabled: bool = False,
) -> ReorganizeResult:
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
        A :class:`ReorganizeResult`. It is falsey when nothing moved, so the
        historical ``if reorganize_tile_folders(...):`` usage still works.
    """
    # Check if we have folder mapping
    if not tile_folder_mapping:
        logger.debug("No tile folder mapping - skipping reorganization")
        return ReorganizeResult(skip_reason="no tile folders were tracked for this run")

    skip_reason = reorganization_skip_reason(local_path, local_access_enabled)
    if skip_reason:
        logger.info(f"Skipping folder reorganization: {skip_reason}")
        return ReorganizeResult(skip_reason=skip_reason)

    local_base = Path(local_path)

    logger.info(f"Starting folder reorganization: {local_base}")
    result = ReorganizeResult()

    # Find the timestamped folders created by server
    # They'll be named like: 20260127_123617_Test_2026-01-27_X11.09_Y14.46
    for flattened_name, (date_folder, tile_folder) in tile_folder_mapping.items():
        # Search for matching folder (with any timestamp prefix)
        # Pattern: *_{flattened_name} where flattened_name is like "Test_2026-01-27_X11.09_Y14.46"
        pattern = f"*_{flattened_name}"
        matching_folders = list(local_base.glob(pattern))

        if not matching_folders:
            logger.warning(f"Could not find folder matching pattern: {pattern}")
            result.unmatched.append(flattened_name)
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
                    logger.warning(
                        f"Could not remove source folder (not empty): {src_folder}"
                    )

                logger.info(
                    f"Reorganized: {src_folder.name} -> {base_save_directory}/{date_folder}/{tile_folder}/ ({items_moved} items)"
                )
                result.moved += 1

            except Exception as e:
                logger.error(f"Failed to reorganize {src_folder}: {e}")
                result.failed.append(src_folder.name)

    if result.moved > 0:
        logger.info(
            f"Tile folder reorganization complete: {result.moved} folders moved"
        )
    else:
        logger.info("No folders were reorganized")

    return result
