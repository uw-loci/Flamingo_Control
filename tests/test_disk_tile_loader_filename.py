"""Tests for raw-stack filename parsing in disk_tile_loader.

The loader previously matched only C{channel} and P{planes} and ignored the
I{side} field, so a dual-side acquisition's C03_I0 and C03_I1 files collided on
the C-number — one overwrote the other and both channels read the survivor
(duplicated channel). The same collapse would hit a time series (t), multiple
views (V), or rotations (R). These tests lock in full-field parsing and the
side-aware key.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_disk_tile_loader_filename.py -q
"""

from py2flamingo.visualization.disk_tile_loader import (
    _raw_file_key,
    parse_raw_filename,
)

_I0 = "S000_t000000_V000_R0000_X000_Y000_C03_I0_D1_P04260.raw"
_I1 = "S000_t000000_V000_R0000_X000_Y000_C03_I1_D1_P04260.raw"


def test_parses_all_fields():
    f = parse_raw_filename("S002_t000005_V001_R0090_X003_Y004_C01_I1_D1_P00750.raw")
    assert f == {
        "series": 2,
        "timepoint": 5,
        "view": 1,
        "rotation": 90,
        "tile_x": 3,
        "tile_y": 4,
        "channel": 1,
        "illum": 1,
        "detection": 1,
        "planes": 750,
    }


def test_two_sides_get_distinct_keys():
    a, b = parse_raw_filename(_I0), parse_raw_filename(_I1)
    ka = _raw_file_key(a["channel"], a["illum"])
    kb = _raw_file_key(b["channel"], b["illum"])
    assert ka == 3  # left (I0) -> C
    assert kb == 7  # right (I1) -> C + 4
    assert ka != kb  # no collision -> no duplicate channel


def test_key_matches_channel_offset_scheme():
    # `channels` uses left=C, right=C+4; the file key must agree so load maps
    # channel_id -> file directly.
    assert _raw_file_key(3, 0) == 3
    assert _raw_file_key(3, 1) == 7
    assert _raw_file_key(0, 0) == 0
    assert _raw_file_key(0, 1) == 4


def test_non_raw_is_ignored():
    assert parse_raw_filename("S000_..._P00750_MP.tif") is None
    assert parse_raw_filename("Workflow.txt") is None


def test_channel_and_planes_required():
    # Missing C or P -> not a usable raw stack.
    assert parse_raw_filename("S000_t000000_V000_I0_D1_P00750.raw") is None  # no C
    assert parse_raw_filename("S000_t000000_C03_I0_D1.raw") is None  # no P


def test_optional_fields_default_to_zero():
    # Channel + planes present (with their leading underscores); absent optional
    # fields default to 0.
    f = parse_raw_filename("S000_X000_Y000_C03_P00750.raw")
    assert f is not None
    assert f["channel"] == 3 and f["planes"] == 750
    assert f["timepoint"] == 0 and f["view"] == 0 and f["illum"] == 0
