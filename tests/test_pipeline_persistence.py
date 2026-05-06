"""Persistence tests: JSON round-trip, validation rejection paths, and
PipelineRepository save/load against a tmp directory (so tests never touch
``~/.flamingo/``).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TESTS_DIR = Path(__file__).resolve().parent
_SRC = _TESTS_DIR.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from py2flamingo.pipeline.models.pipeline import (  # noqa: E402
    NodeType,
    Pipeline,
    create_node,
)
from py2flamingo.pipeline.services.pipeline_repository import (  # noqa: E402
    PipelineRepository,
)

FIXTURES = _TESTS_DIR / "fixtures" / "pipelines"


def _load(name: str) -> Pipeline:
    return Pipeline.from_dict(json.loads((FIXTURES / name).read_text()))


# ---------------------------------------------------------------------------
# Validation rejection paths
# ---------------------------------------------------------------------------


class TestValidationRejection(unittest.TestCase):
    def test_invalid_type_fixture_fails_validation(self):
        # 12: SCALAR -> VOLUME, hand-built to bypass add_connection's check.
        p = _load("12_invalid_type.json")
        errs = p.validate()
        self.assertTrue(errs, "validate() should report errors")
        self.assertTrue(
            any("type mismatch" in e.lower() for e in errs),
            f"Expected 'type mismatch' in errors: {errs}",
        )

    def test_cycle_fixture_fails_validation(self):
        p = _load("13_cycle.json")
        errs = p.validate()
        self.assertTrue(any("cycle" in e.lower() for e in errs), f"errors={errs}")

    def test_missing_required_fixture_fails_validation(self):
        p = _load("14_missing_required.json")
        errs = p.validate()
        self.assertTrue(
            any("collection" in e.lower() for e in errs),
            f"Expected 'collection' (the required port name) in errors: {errs}",
        )


# ---------------------------------------------------------------------------
# JSON round-trip via to_dict/from_dict
# ---------------------------------------------------------------------------


class TestJSONRoundTrip(unittest.TestCase):
    def test_round_trip_all_fixtures(self):
        # For every shipped engine fixture, to_dict→from_dict round-trips
        # without drift in node count, connections, or names.
        for name in (
            "01_threshold_only.json",
            "02_threshold_foreach.json",
            "03_threshold_foreach_workflow.json",
            "04_conditional_branches.json",
            "05_conditional_in_foreach.json",
            "06_nested_foreach.json",
            "07_external_command.json",
        ):
            with self.subTest(fixture=name):
                p = _load(name)
                d = p.to_dict()
                # JSON-serializable
                round_tripped = json.loads(json.dumps(d))
                p2 = Pipeline.from_dict(round_tripped)
                self.assertEqual(p2.name, p.name)
                self.assertEqual(set(p2.nodes), set(p.nodes))
                self.assertEqual(set(p2.connections), set(p.connections))
                # Validates clean (engine fixtures should all be valid).
                self.assertEqual(p2.validate(), [], f"{name} validation drift")

    def test_round_trip_preserves_config_dict(self):
        p = Pipeline(name="ConfigTest")
        n = create_node(
            NodeType.EXTERNAL_COMMAND,
            name="X",
            config={
                "command_template": "true",
                "timeout_seconds": 7,
                "nested": {"a": 1, "b": [2, 3, 4]},
            },
        )
        p.add_node(n)
        d = p.to_dict()
        p2 = Pipeline.from_dict(json.loads(json.dumps(d)))
        cfg = p2.get_node(n.id).config
        self.assertEqual(cfg["command_template"], "true")
        self.assertEqual(cfg["timeout_seconds"], 7)
        self.assertEqual(cfg["nested"], {"a": 1, "b": [2, 3, 4]})


# ---------------------------------------------------------------------------
# PipelineRepository (uses tmp_path, never touches ~/.flamingo/)
# ---------------------------------------------------------------------------


class TestPipelineRepository(unittest.TestCase):
    def test_save_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            repo = PipelineRepository(base_dir=str(td))
            p = Pipeline(name="My Test Pipeline")
            n = create_node(NodeType.THRESHOLD, name="T")
            p.add_node(n)

            saved_path = repo.save(p)
            self.assertTrue(saved_path.exists())
            self.assertTrue(saved_path.is_relative_to(td))

            loaded = repo.load(saved_path.name)
            self.assertEqual(loaded.name, "My Test Pipeline")
            self.assertEqual(set(loaded.nodes), {n.id})

    def test_explicit_filename_used_verbatim(self):
        with tempfile.TemporaryDirectory() as td:
            repo = PipelineRepository(base_dir=str(td))
            p = Pipeline(name="Whatever")
            p.add_node(create_node(NodeType.THRESHOLD))
            saved = repo.save(p, filename="explicit.json")
            self.assertEqual(saved.name, "explicit.json")

    def test_load_missing_raises(self):
        with tempfile.TemporaryDirectory() as td:
            repo = PipelineRepository(base_dir=str(td))
            with self.assertRaises(FileNotFoundError):
                repo.load("nope.json")

    def test_load_from_path_absolute(self):
        # load_from_path takes an absolute path, not a filename relative to base_dir.
        with tempfile.TemporaryDirectory() as td:
            other_dir = Path(td) / "other"
            other_dir.mkdir()
            extern = other_dir / "ext.json"
            data = json.loads((FIXTURES / "01_threshold_only.json").read_text())
            extern.write_text(json.dumps(data))

            repo = PipelineRepository(base_dir=str(td))
            loaded = repo.load_from_path(str(extern))
            self.assertEqual(len(loaded.nodes), 1)


# ---------------------------------------------------------------------------
# Default save dir handling
# ---------------------------------------------------------------------------


class TestDefaultSaveDir(unittest.TestCase):
    def test_default_dir_resolves_under_home_flamingo(self):
        # Use a tmp HOME so we don't actually create ~/.flamingo/ on the
        # developer's machine.
        with tempfile.TemporaryDirectory() as fake_home:
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = fake_home
            try:
                repo = PipelineRepository()  # base_dir=None → ~/.flamingo/pipelines
                self.assertTrue(
                    str(repo.directory).startswith(fake_home),
                    f"Expected default dir under {fake_home}, got {repo.directory}",
                )
                self.assertTrue(repo.directory.exists())
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
