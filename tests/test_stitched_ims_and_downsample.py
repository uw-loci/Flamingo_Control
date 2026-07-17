"""Tests for memory-safe stitched downsampling and Imaris .ims loading.

Covers two robustness fixes in the "Load Stitched" path:

1. ``downsample_to_voxel_grid`` — resamples a large volume to the display grid
   without ever allocating a full-resolution float32 copy (the old
   ``ndi.zoom(volume.astype(float32), ...)`` blew up to 377 GB on a 194 GB
   uint16 volume).
2. ``.ims`` (Imaris HDF5) loading — the stitcher can write Imaris output, but
   the viewer only knew zarr/OME-TIFF and crashed with GroupNotFoundError. The
   reader here pulls a coarse pyramid level and folds its downsample factor into
   the effective voxel size.

The .ims tests synthesize a standard Imaris HDF5 layout with h5py (no
PyImarisWriter needed): ``/DataSet/ResolutionLevel N/TimePoint 0/Channel C/Data``
with ``ImageSizeX/Y/Z`` attrs and edge padding, plus a v2 stitch_metadata.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

from py2flamingo.visualization.session_manager import (  # noqa: E402
    downsample_to_voxel_grid,
    load_stitched_volume,
)

# --------------------------------------------------------------------------
# downsample_to_voxel_grid
# --------------------------------------------------------------------------


def test_downsample_output_shape_matches_zoom_contract():
    # Output shape must equal round(shape * factor), same as ndi.zoom would give.
    vol = np.arange(20 * 40 * 60, dtype=np.uint16).reshape(20, 40, 60)
    factors = np.array([0.5, 0.25, 0.1])
    out = downsample_to_voxel_grid(vol, factors)
    expected = tuple(np.maximum(1, np.round(np.array(vol.shape) * factors)).astype(int))
    assert out.shape == expected
    assert out.dtype == np.float32


def test_downsample_extreme_factor_hits_target_and_preserves_mean():
    # A steep downsample (stride path) must still land on the exact target and
    # keep the overall intensity level roughly intact.
    vol = np.full((100, 200, 300), 1000, dtype=np.uint16)
    vol[:, :, :] += (np.arange(300, dtype=np.uint16))[None, None, :] % 7
    factors = np.array([0.02, 0.02, 0.02])
    out = downsample_to_voxel_grid(vol, factors)
    expected = tuple(np.maximum(1, np.round(np.array(vol.shape) * factors)).astype(int))
    assert out.shape == expected
    assert abs(float(out.mean()) - float(vol.mean())) < 20.0


def test_downsample_factor_one_is_identity_shape():
    vol = np.zeros((5, 6, 7), dtype=np.uint16)
    out = downsample_to_voxel_grid(vol, np.array([1.0, 1.0, 1.0]))
    assert out.shape == (5, 6, 7)


def test_downsample_does_not_upcast_full_volume(monkeypatch):
    # Guard the whole point of the fix: the float32 array handed to zoom must be
    # the small decimated array, never a full-size copy of the input.
    import scipy.ndimage as ndi

    seen = {}
    real_zoom = ndi.zoom

    def spy_zoom(arr, *a, **k):
        seen["in_size"] = arr.size
        return real_zoom(arr, *a, **k)

    monkeypatch.setattr(ndi, "zoom", spy_zoom)
    vol = np.ones((60, 400, 400), dtype=np.uint16)  # 9.6M voxels
    downsample_to_voxel_grid(vol, np.array([0.05, 0.02, 0.02]))
    # Decimated input must be a tiny fraction of the full volume.
    assert seen["in_size"] < vol.size / 100


# --------------------------------------------------------------------------
# Imaris .ims loading
# --------------------------------------------------------------------------


def _set_ims_size_attrs(group, z, y, x):
    """Write ImageSizeX/Y/Z the way Imaris does — arrays of single-char bytes."""
    for axis, val in (("X", x), ("Y", y), ("Z", z)):
        s = str(int(val))
        group.attrs.create(
            f"ImageSize{axis}",
            np.array([c.encode("ascii") for c in s], dtype="S1"),
        )


def _write_synthetic_ims(path: Path, levels, n_channels=2, fill=None):
    """levels: list of (z, y, x, pad_z, pad_y, pad_x) per resolution level.

    Data is stored padded to (z+pad_z, ...) with ImageSize attrs giving the true
    (z, y, x). Level 0 is full resolution.
    """
    with h5py.File(str(path), "w") as f:
        dset = f.create_group("DataSet")
        for li, (z, y, x, pz, py, px) in enumerate(levels):
            tp = dset.create_group(f"ResolutionLevel {li}/TimePoint 0")
            for c in range(n_channels):
                cg = tp.create_group(f"Channel {c}")
                padded = np.zeros((z + pz, y + py, x + px), dtype=np.uint16)
                if fill is not None:
                    padded[:z, :y, :x] = fill(li, c, z, y, x)
                cg.create_dataset("Data", data=padded)
                _set_ims_size_attrs(cg, z, y, x)


def _write_metadata(
    output_dir: Path, store_name: str, channel_ids, voxel_zyx, origin_zyx
):
    meta = {
        "version": 2,
        "store_path": store_name,
        "voxel_size_um": {"z": voxel_zyx[0], "y": voxel_zyx[1], "x": voxel_zyx[2]},
        "channel_ids": channel_ids,
        "origin_um": list(origin_zyx),
    }
    (output_dir / "stitch_metadata.json").write_text(json.dumps(meta))


def test_load_ims_picks_coarse_level_and_scales_voxel(tmp_path):
    out = tmp_path
    ims = out / "stitched.ims"
    # Level 0 max dim 1100 (> 1024 threshold), level 1 halved to 550/4 (<= 1024).
    _write_synthetic_ims(
        ims,
        levels=[
            (2, 1100, 8, 0, 12, 0),  # padded on Y
            (2, 550, 4, 0, 8, 0),
        ],
        n_channels=2,
        fill=lambda li, c, z, y, x: np.uint16((c + 1) * 100 + li),
    )
    _write_metadata(
        out,
        "stitched.ims",
        channel_ids=[3, 5],
        voxel_zyx=(1.0, 2.0, 2.0),
        origin_zyx=(10.0, 20.0, 30.0),
    )

    result = load_stitched_volume(out)
    chans = result["channels"]
    assert [c["ch_id"] for c in chans] == [3, 5]

    # Coarse level chosen (1): true size (2, 550, 4), padding cropped away.
    assert chans[0]["volume"].shape == (2, 550, 4)
    # Effective voxel = native * (full/chosen) = (1,2,2)*(2/2,1100/550,8/4)=(1,4,4)
    np.testing.assert_allclose(chans[0]["voxel_size_um"], [1.0, 4.0, 4.0])
    # Origin (stitch corner) is unchanged by the level choice.
    np.testing.assert_allclose(chans[0]["origin_um"], [10.0, 20.0, 30.0])
    # Per-channel content survived (channel 1 filled with 200, channel 0 with 100).
    assert int(chans[0]["volume"].max()) == 101  # (0+1)*100 + level 1
    assert int(chans[1]["volume"].max()) == 201


def test_load_ims_single_level_full_resolution(tmp_path):
    # With only a full-res level, that level is used as-is (factor 1).
    out = tmp_path
    _write_synthetic_ims(
        out / "stitched.ims",
        levels=[(3, 40, 50, 0, 0, 0)],
        n_channels=1,
        fill=lambda li, c, z, y, x: np.uint16(7),
    )
    _write_metadata(
        out,
        "stitched.ims",
        channel_ids=[0],
        voxel_zyx=(1.0, 1.0, 1.0),
        origin_zyx=(0.0, 0.0, 0.0),
    )
    chans = load_stitched_volume(out)["channels"]
    assert len(chans) == 1
    assert chans[0]["volume"].shape == (3, 40, 50)
    np.testing.assert_allclose(chans[0]["voxel_size_um"], [1.0, 1.0, 1.0])


def test_load_ims_bad_file_raises_clear_error(tmp_path):
    out = tmp_path
    bad = out / "stitched.ims"
    with h5py.File(str(bad), "w") as f:
        f.create_group("NotADataSet")
    _write_metadata(
        out,
        "stitched.ims",
        channel_ids=[0],
        voxel_zyx=(1.0, 1.0, 1.0),
        origin_zyx=(0.0, 0.0, 0.0),
    )
    with pytest.raises(ValueError, match="not a readable Imaris file"):
        load_stitched_volume(out)
