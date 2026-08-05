"""MIP loading must reuse the acquisition's own ``*_MP.tif`` projection.

Every acquisition writes a max projection next to each stack. Reading it costs
one small image; recomputing the same projection from the stack pulls the whole
(potentially multi-gigabyte) volume through RAM for no gain. These tests pin
that the projection is preferred wherever a tile is read, that discovery can't
be tricked into choosing the stack by filename ordering, and that the fallback
(no companion) produces a real projection rather than a single plane.
"""

from pathlib import Path

import numpy as np
import pytest
import tifffile

from py2flamingo.models.mip_overview import (
    discover_flat_mip_tiles,
    find_mip_companion,
    is_mip_file,
    load_tile_mip,
)


def _stack(path: Path, n_planes=6, size=8):
    """A stack whose max projection is distinguishable from any single plane."""
    vol = np.zeros((n_planes, size, size), dtype=np.uint16)
    for z in range(n_planes):
        vol[z, z % size, :] = 100 + z  # a different bright row per plane
    tifffile.imwrite(str(path), vol)
    return vol


def _projection(path: Path, value=4242, size=8):
    img = np.full((size, size), value, dtype=np.uint16)
    tifffile.imwrite(str(path), img)
    return img


# --------------------------------------------------------------------------- #
# Companion resolution
# --------------------------------------------------------------------------- #
def test_is_mip_file():
    assert is_mip_file(Path("a_X000_Y000_C02_MP.tif"))
    assert is_mip_file(Path("a_X000_Y000_C02_mp.tif"))  # case-insensitive
    assert not is_mip_file(Path("a_X000_Y000_C02.tif"))


def test_find_mip_companion(tmp_path):
    stack = tmp_path / "tile_X000_Y000_C02.tif"
    _stack(stack)
    assert find_mip_companion(stack) is None

    mip = tmp_path / "tile_X000_Y000_C02_MP.tif"
    _projection(mip)
    assert find_mip_companion(stack) == mip
    # A projection has no companion of its own (no infinite _MP_MP chase).
    assert find_mip_companion(mip) is None


# --------------------------------------------------------------------------- #
# load_tile_mip
# --------------------------------------------------------------------------- #
def test_companion_is_read_instead_of_the_stack(tmp_path):
    stack = tmp_path / "tile_X000_Y000_C02.tif"
    _stack(stack)
    mip = tmp_path / "tile_X000_Y000_C02_MP.tif"
    expected = _projection(mip)

    loaded = load_tile_mip(stack)
    assert np.array_equal(loaded, expected), "did not use the existing projection"


def test_stack_without_companion_is_truly_projected(tmp_path):
    """The fallback must MAX over Z — the old code took plane 0."""
    stack = tmp_path / "tile_X000_Y000_C02.tif"
    vol = _stack(stack)

    loaded = load_tile_mip(stack)
    assert loaded.ndim == 2
    assert np.array_equal(loaded, vol.max(axis=0))
    assert not np.array_equal(loaded, vol[0]), "returned one plane, not a projection"


def test_single_page_file_is_returned_as_is(tmp_path):
    mip = tmp_path / "tile_X000_Y000_C02_MP.tif"
    expected = _projection(mip)
    assert np.array_equal(load_tile_mip(mip), expected)


def test_unreadable_file_returns_none(tmp_path):
    bad = tmp_path / "tile_X000_Y000_C02.tif"
    bad.write_bytes(b"not a tiff")
    assert load_tile_mip(bad) is None


# --------------------------------------------------------------------------- #
# Discovery prefers the projection
# --------------------------------------------------------------------------- #
def test_discovery_picks_the_projection_over_the_stack(tmp_path):
    """Both files exist for one tile+channel; the projection must be chosen.

    This used to depend on '.' sorting before '_' in the filename — luck, not a
    rule, and it broke as soon as the naming changed.
    """
    stack = tmp_path / "sample_X000_Y000_C02.tif"
    _stack(stack)
    mip = tmp_path / "sample_X000_Y000_C02_MP.tif"
    expected = _projection(mip)

    tiles = discover_flat_mip_tiles(tmp_path)
    assert len(tiles) == 1
    assert tiles[0].channel_files[2] == mip
    assert np.array_equal(load_tile_mip(tiles[0].channel_files[2]), expected)


def test_discovery_still_finds_a_lone_stack(tmp_path):
    """With no projection written, the stack is still discovered (and projected)."""
    stack = tmp_path / "sample_X001_Y000_C02.tif"
    vol = _stack(stack)

    tiles = discover_flat_mip_tiles(tmp_path)
    assert len(tiles) == 1
    assert tiles[0].channel_files[2] == stack
    assert np.array_equal(load_tile_mip(stack), vol.max(axis=0))


def test_server_pattern_projection_is_discovered(tmp_path):
    name = "S000_t000000_V000_R0000_X002_Y003_C02_I0_D1_P363_MP.tif"
    _projection(tmp_path / name)
    tiles = discover_flat_mip_tiles(tmp_path)
    assert len(tiles) == 1
    assert (tiles[0].x_idx, tiles[0].y_idx) == (2, 3)


# --------------------------------------------------------------------------- #
# Subfolder layout
# --------------------------------------------------------------------------- #
def test_subfolder_source_prefers_the_projection(tmp_path):
    from py2flamingo.views.dialogs.mip_overview_dialog import _pick_tile_source

    folder = tmp_path / "X6.43_Y18.14"
    folder.mkdir()
    _stack(folder / "S000_t000000_X000_Y000_C02_P363.tif")
    mip = folder / "S000_t000000_X000_Y000_C02_P363_MP.tif"
    _projection(mip)

    assert _pick_tile_source(folder) == mip


def test_subfolder_falls_back_to_a_stack(tmp_path):
    from py2flamingo.views.dialogs.mip_overview_dialog import _pick_tile_source

    folder = tmp_path / "X6.43_Y18.14"
    folder.mkdir()
    stack = folder / "S000_t000000_X000_Y000_C02_P363.tif"
    _stack(stack)
    assert _pick_tile_source(folder) == stack


def test_subfolder_with_only_raw_is_skipped(tmp_path):
    """A .raw has no self-describing shape; the server always writes its _MP."""
    from py2flamingo.views.dialogs.mip_overview_dialog import _pick_tile_source

    folder = tmp_path / "X6.43_Y18.14"
    folder.mkdir()
    (folder / "S000_t000000_X000_Y000_C02_P363.raw").write_bytes(b"\x00" * 64)
    assert _pick_tile_source(folder) is None
