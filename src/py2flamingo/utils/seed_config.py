"""Seed a missing config file from a tracked ``.example`` sibling.

Some files are configuration (a connection profile, a drive mapping) and some
are per-run state (window geometry, last-used folders). Both used to be tracked
in git, which kept the working tree permanently dirty because the app rewrites
the state ones constantly. Untracking all of them on 2026-08-10 fixed that and
went one file too far: ``saved_configurations.json`` held the ONLY saved route to
the microscope, so the next pull deleted it and the rig could not connect.

The distinction that was missed:

* **configuration** — written only when the user changes something
  (``saved_configurations.json``, ``drive_mappings.json``). Losing it is losing
  a setting someone entered deliberately.
* **per-run state** — rewritten on every run (``window_geometry.json``,
  ``session_paths.json``). Losing it costs nothing.

Configuration still should not be tracked directly: it holds machine-specific
values, and a tracked copy is what made every rig report a dirty tree. So the
repo carries a ``<name>.example.json`` instead, and the loader copies it the
first time the real file is absent. A fresh clone then starts with a working
default rather than silently with nothing, and a machine that has customised its
copy is never overwritten -- seeding happens only when the file does not exist.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

EXAMPLE_SUFFIX = ".example"


def example_path_for(path: Path) -> Path:
    """``foo.json`` -> ``foo.example.json``."""
    return path.with_suffix(EXAMPLE_SUFFIX + path.suffix)


def seed_from_example(path: Path, log: Optional[logging.Logger] = None) -> bool:
    """Create ``path`` from its ``.example`` sibling if it does not exist.

    Returns True if a file was created. Never overwrites, never raises: a
    failure here must degrade to "no config" exactly as before, not break
    startup.
    """
    log = log or logger
    try:
        path = Path(path)
        if path.exists():
            return False
        example = example_path_for(path)
        if not example.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(example, path)
        # INFO, not DEBUG: this silently changes what the app is configured
        # with, and the one time it matters is the time someone is wondering
        # why settings appeared or did not.
        log.info(
            f"{path.name} was missing; seeded it from {example.name}. "
            f"Edit {path.name} to customise — it is not tracked by git, so your "
            f"changes stay local and survive updates."
        )
        return True
    except Exception as exc:  # noqa: BLE001 - seeding is best-effort
        log.warning(f"Could not seed {path} from its example: {exc!r}")
        return False
