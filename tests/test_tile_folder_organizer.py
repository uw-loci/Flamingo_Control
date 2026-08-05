"""Tests for post-collection tile folder reorganization.

The server can only create one directory level, so tile collection asks for
``<base>_<date>_X_Y`` and relies on this reorganization to produce the nested
``<base>/<date>/X_Y`` layout that MIP Overview and the stitcher read. When it
silently does not run, a finished acquisition looks fine but is unreadable by
every downstream tool -- so the skip reasons are asserted here too.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.utils.tile_folder_organizer import (  # noqa: E402
    ReorganizeResult,
    infer_local_drive_root,
    reorganization_skip_reason,
    reorganize_tile_folders,
)


def _make_server_folder(root: Path, timestamp: str, flat_name: str, files=("a.tif",)):
    """Create the folder shape the server produces: <timestamp>_<flat_name>/."""
    folder = root / f"{timestamp}_{flat_name}"
    folder.mkdir(parents=True)
    for name in files:
        (folder / name).write_text("data")
    return folder


class TestReorganize:
    def test_moves_flat_folders_into_nested_layout(self, tmp_path):
        flat = "BrainSingleChannel2_2026-08-05_X4.47_Y17.17"
        src = _make_server_folder(
            tmp_path, "20260805_011617", flat, ("t_MP.tif", "t.raw")
        )

        result = reorganize_tile_folders(
            str(tmp_path),
            "BrainSingleChannel2",
            {flat: ("2026-08-05", "X4.47_Y17.17")},
            local_access_enabled=True,
        )

        assert result.moved == 1
        assert result.ran
        dest = tmp_path / "BrainSingleChannel2" / "2026-08-05" / "X4.47_Y17.17"
        assert sorted(p.name for p in dest.iterdir()) == ["t.raw", "t_MP.tif"]
        # Source folder is consumed, not left behind as a confusing duplicate.
        assert not src.exists()

    def test_result_is_truthy_only_when_something_moved(self, tmp_path):
        """Callers historically did `if reorganize_tile_folders(...)`."""
        flat = "S_2026-08-05_X1.00_Y2.00"
        _make_server_folder(tmp_path, "20260805_000000", flat)

        moved = reorganize_tile_folders(
            str(tmp_path), "S", {flat: ("2026-08-05", "X1.00_Y2.00")}, True
        )
        assert bool(moved) is True
        assert bool(ReorganizeResult()) is False

    def test_missing_folder_is_reported_not_silently_dropped(self, tmp_path):
        result = reorganize_tile_folders(
            str(tmp_path),
            "S",
            {"S_2026-08-05_X1.00_Y2.00": ("2026-08-05", "X1.00_Y2.00")},
            local_access_enabled=True,
        )

        assert result.ran  # it ran, it just found nothing
        assert result.moved == 0
        assert result.unmatched == ["S_2026-08-05_X1.00_Y2.00"]

    def test_partial_run_organizes_the_tiles_that_finished(self, tmp_path):
        """A cancelled queue leaves some tiles done; those still get moved."""
        done = "S_2026-08-05_X1.00_Y2.00"
        never_ran = "S_2026-08-05_X9.00_Y9.00"
        _make_server_folder(tmp_path, "20260805_000000", done)

        result = reorganize_tile_folders(
            str(tmp_path),
            "S",
            {
                done: ("2026-08-05", "X1.00_Y2.00"),
                never_ran: ("2026-08-05", "X9.00_Y9.00"),
            },
            local_access_enabled=True,
        )

        assert result.moved == 1
        assert result.unmatched == [never_ran]
        assert (tmp_path / "S" / "2026-08-05" / "X1.00_Y2.00" / "a.tif").exists()


class TestSkipReasons:
    def test_local_access_disabled(self, tmp_path):
        reason = reorganization_skip_reason(str(tmp_path), local_access_enabled=False)
        assert reason and "post-processing" in reason

    def test_no_local_path(self):
        reason = reorganization_skip_reason(None, local_access_enabled=True)
        assert reason and "no local path" in reason

    def test_path_not_accessible(self, tmp_path):
        missing = tmp_path / "not-mounted"
        reason = reorganization_skip_reason(str(missing), local_access_enabled=True)
        assert reason and "not accessible" in reason

    def test_ready(self, tmp_path):
        assert reorganization_skip_reason(str(tmp_path), True) is None

    def test_skip_is_surfaced_through_the_result(self, tmp_path):
        flat = "S_2026-08-05_X1.00_Y2.00"
        _make_server_folder(tmp_path, "20260805_000000", flat)

        result = reorganize_tile_folders(
            str(tmp_path), "S", {flat: ("2026-08-05", "X1.00_Y2.00")}, False
        )

        assert not result.ran
        assert result.moved == 0
        assert "post-processing" in result.skip_reason
        assert "flat layout" in result.summary()

    def test_empty_mapping_is_a_skip_not_a_success(self):
        result = reorganize_tile_folders("/anywhere", "S", {}, True)
        assert not result.ran
        assert not result


class TestInferLocalDriveRoot:
    """The drive root is the directory the server drops its folders into.

    A blanket ``base_folder.parent`` is right for only one of the ways the
    user can navigate to an acquisition, which is why this is a real function.
    """

    def test_browsed_to_sample_folder_with_date_subfolders(self):
        root = infer_local_drive_root(
            Path("/mnt/D/CTLSM1/BrainSingleChannel2"), "2026-08-05", "subfolder"
        )
        assert root == Path("/mnt/D/CTLSM1")

    def test_browsed_straight_into_the_date_folder(self):
        root = infer_local_drive_root(
            Path("/mnt/D/CTLSM1/BrainSingleChannel2/2026-08-05"), "", "subfolder"
        )
        assert root == Path("/mnt/D/CTLSM1")

    def test_browsed_to_sample_folder_with_tiles_directly_inside(self):
        root = infer_local_drive_root(
            Path("/mnt/D/CTLSM1/BrainSingleChannel2"), "", "subfolder"
        )
        assert root == Path("/mnt/D/CTLSM1")

    def test_flat_layout_folder_is_itself_the_drive_root(self):
        """Regression: `.parent` here pointed one level ABOVE the drive root.

        Flat timestamped folders only exist in the drive root, so loading a
        flat overview and hitting Collect Tiles used to configure the
        reorganizer with the wrong directory -- it then found nothing and left
        the next run flat as well, which is self-perpetuating.
        """
        root = infer_local_drive_root(Path("/mnt/D/CTLSM1"), "", "flat")
        assert root == Path("/mnt/D/CTLSM1")

    def test_flat_layout_inside_a_date_subfolder(self):
        root = infer_local_drive_root(Path("/mnt/D/CTLSM1"), "2026-08-05", "flat")
        assert root == Path("/mnt/D/CTLSM1/2026-08-05")

    def test_returns_none_rather_than_a_filesystem_root(self):
        assert infer_local_drive_root(Path("/CTLSM1"), "", "subfolder") is None

    def test_none_config_is_handled(self):
        assert infer_local_drive_root(None) is None


class TestDialogHook:
    """The dialog's wrapper must never let a failure escape into a Qt slot.

    ``on_queue_completed`` is a signal handler: an exception there unwinds into
    the dispatcher and disappears, which is how a run can finish "successfully"
    with the data still flat and nothing in the log explaining it.
    """

    @staticmethod
    def _dialog_cls():
        from py2flamingo.views.dialogs.tile_collection_dialog import (
            TileCollectionDialog,
        )

        return TileCollectionDialog

    def test_reorganize_wrapper_contains_exceptions(self, monkeypatch, tmp_path):
        from py2flamingo.views.dialogs import tile_collection_dialog as mod

        def boom(*args, **kwargs):
            raise OSError("drive vanished mid-move")

        monkeypatch.setattr(mod, "reorganize_tile_folders", boom)

        stub = type("Stub", (), {})()
        stub._local_path = str(tmp_path)
        stub._base_save_directory = "S"
        stub._tile_folder_mapping = {
            "S_2026-08-05_X1.00_Y2.00": ("2026-08-05", "X1.00_Y2.00")
        }
        stub._local_access_enabled = True

        result = self._dialog_cls()._reorganize_after_collection(stub)

        assert not result.ran
        assert "drive vanished" in result.skip_reason

    def test_reorganize_wrapper_tolerates_unset_attributes(self):
        """Reached when execution is attempted before any workflow was built."""
        stub = type("Stub", (), {})()

        result = self._dialog_cls()._reorganize_after_collection(stub)

        assert not result.ran
        assert result.moved == 0

    def test_preflight_reads_the_save_settings(self, tmp_path):
        cls = self._dialog_cls()
        stub = type("Stub", (), {})()

        ready = {"local_path": str(tmp_path), "local_access_enabled": True}
        assert cls._reorganization_preflight(stub, ready) is None

        off = {"local_path": str(tmp_path), "local_access_enabled": False}
        assert cls._reorganization_preflight(stub, off) is not None

        assert cls._reorganization_preflight(stub, {}) is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
