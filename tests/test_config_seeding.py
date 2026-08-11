"""A missing config file is seeded from its tracked .example sibling.

On 2026-08-10 `saved_configurations.json` was untracked along with genuine
per-run state, on the strength of its filename. It held the ONLY saved
connection profile, so the next pull deleted it from the rig and the microscope
became unreachable. The app did not fail — it logged "Starting with empty
configuration set", which reads exactly like a normal first run.

The distinction that was missed:

* configuration — written only when someone changes it
  (saved_configurations.json, drive_mappings.json)
* per-run state — rewritten constantly (window_geometry.json, session_paths.json)

Configuration still should not be tracked (machine-specific values, and a
tracked copy is what kept every tree dirty), so the repo ships `.example`
siblings and the loaders seed from them.

Run: QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest \
        tests/test_config_seeding.py -q
"""

import json
from pathlib import Path

import pytest

from py2flamingo.utils.seed_config import example_path_for, seed_from_example

REPO = Path(__file__).resolve().parents[1]


class TestSeeding:
    def test_a_missing_file_is_created_from_the_example(self, tmp_path):
        target = tmp_path / "cfg.json"
        example_path_for(target).write_text('{"v": 1}', encoding="utf-8")

        assert seed_from_example(target) is True
        assert json.loads(target.read_text()) == {"v": 1}

    def test_an_existing_file_is_never_overwritten(self, tmp_path):
        """A machine that customised its copy must survive every update."""
        target = tmp_path / "cfg.json"
        target.write_text('{"mine": true}', encoding="utf-8")
        example_path_for(target).write_text('{"v": 1}', encoding="utf-8")

        assert seed_from_example(target) is False
        assert json.loads(target.read_text()) == {"mine": True}

    def test_no_example_is_not_an_error(self, tmp_path):
        assert seed_from_example(tmp_path / "cfg.json") is False

    def test_an_unwritable_target_degrades_quietly(self, tmp_path):
        """Seeding is best-effort; it must never break startup."""
        target = tmp_path / "sub" / "cfg.json"
        example_path_for(target).parent.mkdir(parents=True)
        example_path_for(target).write_text("{}", encoding="utf-8")
        (tmp_path / "sub").chmod(0o500)
        try:
            seed_from_example(target)  # must not raise
        finally:
            (tmp_path / "sub").chmod(0o700)

    def test_the_example_name_is_a_sibling_not_a_suffix_swap(self):
        assert example_path_for(Path("a/b/foo.json")) == Path("a/b/foo.example.json")


class TestTheShippedExamplesExist:
    """The whole mechanism is inert if the examples are missing or ignored."""

    @pytest.mark.parametrize(
        "name", ["saved_configurations.example.json", "drive_mappings.example.json"]
    )
    def test_the_example_is_present_and_valid_json(self, name):
        path = REPO / name
        assert path.exists(), f"{name} is what makes a fresh clone usable"
        json.loads(path.read_text(encoding="utf-8"))

    def test_the_connection_example_has_the_fields_the_loader_reads(self):
        data = json.loads(
            (REPO / "saved_configurations.example.json").read_text(encoding="utf-8")
        )
        entry = data["configurations"][0]
        for key in ("name", "ip_address", "port"):
            assert key in entry, f"a profile without {key} cannot connect"

    def test_the_drive_mapping_example_has_a_mapping(self):
        data = json.loads(
            (REPO / "drive_mappings.example.json").read_text(encoding="utf-8")
        )
        assert data["mappings"], "an empty mapping seeds nothing useful"


class TestTheRealFilesStayUntracked:
    """Re-tracking them would restore the permanently-dirty tree."""

    def test_no_tracked_file_is_also_gitignored(self):
        import subprocess

        out = subprocess.run(
            ["git", "ls-files", "-i", "-c", "--exclude-standard"],
            cwd=REPO,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert (
            not out
        ), f"tracked AND gitignored, so the tree can never be clean:\n{out}"
