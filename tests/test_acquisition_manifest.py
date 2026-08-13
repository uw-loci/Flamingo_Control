"""The acquisition manifest: what was collected, recorded next to the data.

The test that matters most is the overlap check. A 97-tile brain acquisition
was collected with tiles stepping a full field apart — 0.25% overlap where 20%
had been requested — and nothing on disk could say whether 20% was ever
entered, whether it reached the tile-step calculation, or which field of view
it was applied to. These pin the line that makes that answerable.

Run: python3 -m pytest tests/test_acquisition_manifest.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.utils.acquisition_manifest import (  # noqa: E402
    MANIFEST_FILENAME,
    AcquisitionManifest,
    TargetingSource,
    TileRecord,
    format_manifest_text,
    overlap_check,
    write_manifest,
)
from py2flamingo.utils.file_handlers import text_to_dict  # noqa: E402


def _manifest(**overrides) -> AcquisitionManifest:
    base = AcquisitionManifest(
        microscope="CTLSM1",
        software_version="0.6.2",
        started="2026-08-12 10:04:11",
        finished="2026-08-12 14:29:02",
        acquisition_dir=r"D:\CTLSM1\BrainSingleChannel5\2026-08-08",
        tiles=[
            TileRecord(0, "X2.00_Y11.72", 2.00, 11.72, 13.60, 23.26, 1931, 0.0, "0,1"),
            TileRecord(1, "X3.07_Y11.72", 3.07, 11.72, 13.60, 23.26, 1287, 0.0, "0,1"),
        ],
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


class TestOverlapCheck:
    def test_agreement_produces_no_mismatch(self):
        out = overlap_check(fov_mm=1.0726, step_mm=0.8581, requested_percent=20.0)
        assert out["Achieved overlap (%)"] == pytest.approx(20.0, abs=0.1)
        assert "MISMATCH" not in out

    def test_the_real_failure_is_caught_and_explained(self):
        # The observed numbers: 1024 px x 1.0475 µm frame, stepped a full field.
        out = overlap_check(fov_mm=1.0726, step_mm=1.0700, requested_percent=20.0)
        assert out["Achieved overlap (%)"] == pytest.approx(0.24, abs=0.05)
        assert "MISMATCH" in out
        assert "20.0%" in out["MISMATCH"]
        assert "cannot be registered" in out["MISMATCH"]

    def test_folder_name_quantisation_does_not_trip_it(self):
        # Stage positions land on a 0.01 mm grid, so the achieved overlap is
        # never exactly the requested one. A percentage point of slack absorbs
        # that without hiding a real mismatch.
        out = overlap_check(fov_mm=1.0726, step_mm=0.8600, requested_percent=20.0)
        assert "MISMATCH" not in out

    def test_a_gap_reads_as_negative_overlap(self):
        out = overlap_check(fov_mm=1.0, step_mm=1.2, requested_percent=10.0)
        assert out["Achieved overlap (%)"] == pytest.approx(-20.0)
        assert "MISMATCH" in out

    @pytest.mark.parametrize(
        "fov,step,req",
        [(None, 0.86, 20.0), (1.07, None, 20.0), (0.0, 0.86, 20.0)],
    )
    def test_missing_inputs_report_unknown_rather_than_guess(self, fov, step, req):
        out = overlap_check(fov_mm=fov, step_mm=step, requested_percent=req)
        assert out["Achieved overlap (%)"] == "unknown"
        assert "MISMATCH" not in out

    def test_no_requested_value_still_reports_what_was_achieved(self):
        out = overlap_check(fov_mm=1.0, step_mm=0.9, requested_percent=None)
        assert out["Achieved overlap (%)"] == pytest.approx(10.0)
        assert out["Requested overlap (%)"] == "unknown"


class TestManifestText:
    def test_it_parses_back_with_the_workflow_parser(self, tmp_path):
        # Same <Section> shape as Workflow.txt, so it is readable in Notepad
        # and machine-readable with the parser the project already has.
        written = write_manifest(tmp_path, _manifest())
        parsed = text_to_dict(written)
        assert parsed["Acquisition"]["Microscope"] == "CTLSM1"
        assert parsed["Acquisition"]["Tiles requested"] == "2"

    def test_the_targeting_source_is_always_present(self):
        # "which overview was this aimed from" is unrecoverable afterwards, so
        # the section exists even when nothing was recorded.
        text = format_manifest_text(_manifest())
        assert "<Targeting>" in text
        assert "(none recorded)" in text

    def test_a_recorded_targeting_source_names_the_file(self):
        text = format_manifest_text(
            _manifest(
                targeting=TargetingSource(
                    kind="LED 2D Overview",
                    path=r"F:\overviews\20260808_brain.zarr",
                    detail="left panel, 0 deg",
                )
            )
        )
        assert "LED 2D Overview" in text
        assert "20260808_brain.zarr" in text
        assert "left panel" in text

    def test_every_tile_gets_a_row(self):
        text = format_manifest_text(_manifest())
        assert "X2.00_Y11.72" in text and "X3.07_Y11.72" in text

    def test_per_tile_z_ranges_are_visible(self):
        # Tiles legitimately differ in depth; a manifest that showed one
        # representative range would misdescribe the set.
        text = format_manifest_text(_manifest())
        assert "1931" in text and "1287" in text

    def test_warnings_are_surfaced_not_buried(self):
        text = format_manifest_text(_manifest(warnings=["overlap did not apply"]))
        assert "<Warnings>" in text
        assert "! overlap did not apply" in text

    def test_empty_sections_are_omitted(self):
        assert "<Batch>" not in format_manifest_text(_manifest())

    def test_a_populated_section_appears(self):
        text = format_manifest_text(_manifest(batch={"Position in batch": "2 of 5"}))
        assert "<Batch>" in text and "2 of 5" in text

    def test_unknowns_are_labelled_not_zero(self):
        text = format_manifest_text(AcquisitionManifest())
        assert "unknown" in text
        assert "<Acquisition>" in text


class TestWriteManifest:
    def test_it_lands_in_the_acquisition_folder(self, tmp_path):
        path = write_manifest(tmp_path, _manifest())
        assert path == tmp_path / MANIFEST_FILENAME
        assert path.exists()
        assert "CTLSM1" in path.read_text(encoding="utf-8")

    def test_it_creates_a_missing_folder_rather_than_failing(self, tmp_path):
        target = tmp_path / "acq" / "2026-08-08"
        assert write_manifest(target, _manifest()) is not None

    def test_an_unwritable_target_warns_and_returns_none(self, tmp_path):
        blocked = tmp_path / "not_a_dir"
        blocked.write_text("I am a file")
        warnings = []

        class _Log:
            def info(self, msg):
                pass

            def warning(self, msg):
                warnings.append(msg)

        assert write_manifest(blocked, _manifest(), logger=_Log()) is None
        assert warnings

    def test_writing_is_atomic(self, tmp_path):
        # safe_write goes through a .tmp + replace, so a crash mid-write cannot
        # leave a half-manifest that reads as authoritative.
        write_manifest(tmp_path, _manifest())
        assert not list(tmp_path.glob("*.tmp"))
