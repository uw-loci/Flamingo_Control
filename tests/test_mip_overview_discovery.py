"""MIP Overview must discover single-workflow acquisition folders too.

Vendor / single-workflow acquisitions write timestamped folder names that
carry the X_Y coordinate inside a longer string (e.g.
``20260307_041426_SmallTile3_2026-03-07_X6.43_Y18.14``), and a single stack has
just one ``*_MP.tif``. Discovery must find these — not only the bare
``X{mm}_Y{mm}`` folders our own tile collection produces — matching the
stitcher, which already handles both. These lock the unanchored discovery.
"""

from py2flamingo.models.mip_overview import (
    detect_layout_type,
    find_tile_folders,
    parse_coords_from_folder,
)

_TIMESTAMPED = "20260307_041426_SmallTile3_2026-03-07_X6.43_Y18.14"


def _mk_tile_folder(parent, name, with_mip=True):
    d = parent / name
    d.mkdir()
    if with_mip:
        (d / "S000_t000000_V000_R0000_X000_Y000_C01_I0_D1_P00363_MP.tif").write_bytes(
            b""
        )
    return d


def test_parse_coords_handles_timestamped_single_workflow_name():
    assert parse_coords_from_folder(_TIMESTAMPED) == (6.43, 18.14)


def test_find_tile_folders_discovers_timestamped_single_workflow(tmp_path):
    _mk_tile_folder(tmp_path, _TIMESTAMPED)
    found = find_tile_folders(tmp_path)
    assert [f.name for f in found] == [_TIMESTAMPED]


def test_find_tile_folders_still_matches_bare_xy(tmp_path):
    # Regression: our own tile-collection folders must keep working.
    _mk_tile_folder(tmp_path, "X4.88_Y17.63")
    _mk_tile_folder(tmp_path, "X-1.20_Y5.00")
    assert {f.name for f in find_tile_folders(tmp_path)} == {
        "X4.88_Y17.63",
        "X-1.20_Y5.00",
    }


def test_find_tile_folders_ignores_non_coordinate_folders(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "preprocessing_env").mkdir()
    _mk_tile_folder(tmp_path, _TIMESTAMPED)
    assert [f.name for f in find_tile_folders(tmp_path)] == [_TIMESTAMPED]


def test_detect_layout_subfolder_for_timestamped_single_workflow(tmp_path):
    _mk_tile_folder(tmp_path, _TIMESTAMPED, with_mip=True)
    assert detect_layout_type(tmp_path) == "subfolder"


def test_detect_layout_needs_a_mip_in_the_folder(tmp_path):
    # An X_Y-named folder with no MIP is not a subfolder layout.
    _mk_tile_folder(tmp_path, _TIMESTAMPED, with_mip=False)
    assert detect_layout_type(tmp_path) == "none"


def test_detect_layout_flat_for_single_workflow_mip(tmp_path):
    # A single-workflow stack's MIP sitting directly in the folder (flat).
    (
        tmp_path / "S000_t000000_V000_R0000_X000_Y000_C01_I0_D1_P00363_MP.tif"
    ).write_bytes(b"")
    assert detect_layout_type(tmp_path) == "flat"
