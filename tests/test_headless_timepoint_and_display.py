"""Two things the headless entry point has to get right to be usable offline.

Both were found by running the documented CLI on real synthetic data rather
than by reading it:

* ``QApplication([])`` aborts the process when no Qt platform is available, so
  the *headless* entry point failed over SSH, in CI, and for an agent -- the
  three places it exists for.
* A 4-D/5-D input was reduced to a single timepoint silently, with no way to
  choose which. A ten-point timelapse analyzed at T=0 reads exactly like a
  result about the whole series.

Run: .venv/bin/python -m pytest tests/test_headless_timepoint_and_display.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.pipeline.headless_io import load_volumes  # noqa: E402

tifffile = pytest.importorskip("tifffile")


@pytest.fixture
def timelapse(tmp_path):
    """A (T=4, Z=3, C=2, Y=8, X=8) ImageJ-style hyperstack, T distinguishable."""
    arr = np.zeros((4, 3, 2, 8, 8), dtype=np.uint16)
    for t in range(4):
        arr[t] = t + 1  # so the loaded volume identifies its own timepoint
    p = tmp_path / "timelapse.tif"
    tifffile.imwrite(str(p), arr, imagej=True, metadata={"axes": "TZCYX"})
    return p


class TestTimepointSelection:
    def test_the_default_is_the_first_timepoint(self, timelapse):
        vols = load_volumes(timelapse)
        assert {v[0, 0, 0] for v in vols.values()} == {1}

    def test_a_timepoint_can_be_chosen(self, timelapse):
        vols = load_volumes(timelapse, timepoint=2)
        assert {v[0, 0, 0] for v in vols.values()} == {3}

    def test_channels_survive_the_reduction(self, timelapse):
        vols = load_volumes(timelapse, timepoint=1)
        assert set(vols) == {0, 1}
        assert all(v.shape == (3, 8, 8) for v in vols.values())

    def test_out_of_range_raises_rather_than_clamping(self, timelapse):
        # Clamping would analyze a different timepoint than asked for and
        # report success.
        with pytest.raises(ValueError) as exc:
            load_volumes(timelapse, timepoint=9)
        assert "out of range" in str(exc.value)
        assert "4 point" in str(exc.value)

    def test_the_reduction_is_logged(self, timelapse, caplog):
        # The whole point: never silent.
        import logging

        with caplog.at_level(logging.WARNING):
            load_volumes(timelapse)
        assert any("Reducing T axis" in r.getMessage() for r in caplog.records)

    def test_a_plain_3d_volume_logs_nothing(self, tmp_path, caplog):
        import logging

        p = tmp_path / "vol.tif"
        # photometric is explicit because tifffile stores a bare 3-plane array
        # as RGB component planes, which really does give it a reducible
        # sample axis -- a fixture artefact, not the behaviour under test.
        tifffile.imwrite(
            str(p), np.zeros((4, 8, 8), dtype=np.uint16), photometric="minisblack"
        )
        with caplog.at_level(logging.WARNING):
            load_volumes(p)
        assert not [r for r in caplog.records if "Reducing" in r.getMessage()]


class TestItRunsWithNoDisplay:
    def test_the_headless_path_picks_a_platform_when_none_is_set(self, monkeypatch):
        monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
        import os

        from py2flamingo.pipeline.headless_services import _ensure_qapplication

        _ensure_qapplication()
        assert os.environ["QT_QPA_PLATFORM"] == "offscreen"

    def test_an_explicit_platform_is_honoured(self, monkeypatch):
        # A caller that does have a display and wants it keeps it.
        monkeypatch.setenv("QT_QPA_PLATFORM", "minimal")
        import os

        from py2flamingo.pipeline.headless_services import _ensure_qapplication

        _ensure_qapplication()
        assert os.environ["QT_QPA_PLATFORM"] == "minimal"

    def test_the_cli_exposes_a_timepoint_flag(self):
        # Documented in docs/headless_pipelines.md; keep the surfaces in step.
        from py2flamingo.pipeline.cli import _build_parser

        args = _build_parser().parse_args(["run", "p.json", "--timepoint", "3"])
        assert args.timepoint == 3

    def test_timepoint_defaults_to_zero(self):
        from py2flamingo.pipeline.cli import _build_parser

        assert _build_parser().parse_args(["run", "p.json"]).timepoint == 0


class TestUnlabelledAxesDoNotLosePlanes:
    """A plain TIFF stack has no axis metadata, and that used to cost the data.

    tifffile reports an axis it cannot identify as ``Q``. Treating ``Q`` as
    non-spatial reduced it to its first index, so a 128-plane stack loaded as
    ONE plane and every object volume, centroid and count downstream was
    computed on a single slice -- with nothing in the output to say so.
    """

    def test_a_plain_stack_keeps_every_plane(self, tmp_path):
        vol = np.arange(16 * 8 * 8, dtype=np.uint16).reshape(16, 8, 8)
        p = tmp_path / "plain.tif"
        tifffile.imwrite(str(p), vol, photometric="minisblack")
        loaded = load_volumes(p)
        assert len(loaded) == 1
        assert loaded[0].shape == (16, 8, 8)
        assert np.array_equal(loaded[0], vol)

    def test_an_explicit_z_axis_still_wins(self, tmp_path):
        vol = np.zeros((5, 8, 8), dtype=np.uint16)
        p = tmp_path / "ij.tif"
        tifffile.imwrite(str(p), vol, imagej=True, metadata={"axes": "ZYX"})
        assert load_volumes(p)[0].shape == (5, 8, 8)

    def test_a_real_time_axis_is_not_renamed_to_z(self, tmp_path):
        # Only an UNKNOWN axis is reinterpreted. Inventing depth where the file
        # says time would trade a loud failure for a quiet wrong answer.
        arr = np.zeros((4, 5, 8, 8), dtype=np.uint16)
        p = tmp_path / "t.tif"
        tifffile.imwrite(str(p), arr, imagej=True, metadata={"axes": "TZYX"})
        assert load_volumes(p)[0].shape == (5, 8, 8)

    def test_misread_channels_are_flagged_not_silently_accepted(self, tmp_path, caplog):
        # A Z stack saved as .ome.tif with no OME axes reads as N 1-plane
        # channels. That is indistinguishable from a real N-channel snapshot,
        # so it warns rather than guessing.
        import logging

        vol = np.zeros((16, 8, 8), dtype=np.uint16)
        p = tmp_path / "noaxes.ome.tif"
        tifffile.imwrite(str(p), vol, photometric="minisblack")
        with caplog.at_level(logging.WARNING):
            load_volumes(p)
        assert any("single Z plane each" in r.getMessage() for r in caplog.records)

    def test_a_plausible_channel_count_is_not_flagged(self, tmp_path, caplog):
        import logging

        arr = np.zeros((2, 8, 8), dtype=np.uint16)
        p = tmp_path / "two.tif"
        tifffile.imwrite(str(p), arr, imagej=True, metadata={"axes": "CYX"})
        with caplog.at_level(logging.WARNING):
            load_volumes(p)
        assert not [r for r in caplog.records if "single Z plane" in r.getMessage()]
