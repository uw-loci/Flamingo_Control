import sys
from pathlib import Path

# Ensure the src directory is on the Python path for imports
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# These modules are interactive debug *scripts*, not pytest unit tests. Their
# ``test_*`` functions take a live ``main_window`` / ``app`` / ``controller``
# (a connected microscope GUI) — pytest would otherwise mis-collect them and
# treat those parameters as missing fixtures, producing collection errors.
# They are kept on disk because src/py2flamingo/main_window.py and
# tests/run_3d_voxel_test.py import their entry-point functions to drive the
# manual debug flow from the Connection tab.
#
# ``pytest_ignore_collect`` (unlike ``collect_ignore``) is consulted even when
# these files are passed explicitly on the command line, so they stay excluded
# in every invocation.
_DEBUG_SCRIPT_NAMES = {
    "test_3d_voxel_rotation.py",
    "test_3d_voxel_movement.py",
    "test_3d_movement_simple.py",
}


def pytest_ignore_collect(collection_path, config):
    # Firstresult hook: return True to ignore, or None to defer to other
    # mechanisms (e.g. the ``--ignore`` CLI flag). Returning False here would
    # *override* --ignore and force-collect everything, so only return True.
    if collection_path.name in _DEBUG_SCRIPT_NAMES:
        return True
    return None


def pytest_collection_modifyitems(config, items):
    # Belt-and-suspenders: pytest still collects a file's ``test_*`` functions
    # when that file is passed explicitly on the command line (bypassing
    # ``pytest_ignore_collect``). Drop any items that slipped through from the
    # debug scripts so they never reach fixture setup.
    kept = []
    removed = []
    for item in items:
        if Path(str(item.fspath)).name in _DEBUG_SCRIPT_NAMES:
            removed.append(item)
        else:
            kept.append(item)
    if removed:
        config.hook.pytest_deselected(items=removed)
        items[:] = kept
