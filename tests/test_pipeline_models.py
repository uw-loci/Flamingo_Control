"""Unit tests for pipeline model layer.

Covers graph operations (add_node/remove_node, add_connection rejection paths),
``Pipeline.validate()``, and JSON round-trip via ``to_dict``/``from_dict``.

These tests have no Qt dependency — they exercise dataclasses and pure-Python
graph algorithms only.
"""

import json
import sys
import unittest
import uuid
from pathlib import Path

# Make this test self-contained for any invocation mode (run_tests.py, pytest,
# `python -m unittest`, direct execution). conftest.py does the same setup
# for pytest; run_tests.py does it for the standard runner.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from py2flamingo.pipeline.models.pipeline import (
    Connection,
    NodeType,
    Pipeline,
    create_node,
)

FIXTURES = Path(__file__).parent / "fixtures" / "pipelines"


class TestNodeOps(unittest.TestCase):
    def test_add_node_succeeds(self):
        p = Pipeline()
        n = create_node(NodeType.THRESHOLD, name="T")
        p.add_node(n)
        self.assertIn(n.id, p.nodes)

    def test_add_node_duplicate_raises(self):
        p = Pipeline()
        n = create_node(NodeType.THRESHOLD, name="T")
        p.add_node(n)
        with self.assertRaises(ValueError):
            p.add_node(n)

    def test_remove_node_clears_connections(self):
        p = Pipeline()
        a = create_node(NodeType.SAMPLE_VIEW_DATA, name="A")
        b = create_node(NodeType.THRESHOLD, name="B")
        p.add_node(a)
        p.add_node(b)
        p.add_connection(
            a.id,
            a.get_output("volume").id,
            b.id,
            b.get_input("volume").id,
        )
        self.assertEqual(len(p.connections), 1)

        p.remove_node(b.id)

        self.assertNotIn(b.id, p.nodes)
        self.assertEqual(len(p.connections), 0)

    def test_remove_unknown_node_is_noop(self):
        p = Pipeline()
        # No raise — remove_node ignores unknown IDs.
        p.remove_node("does-not-exist")


class TestAddConnection(unittest.TestCase):
    def setUp(self):
        self.p = Pipeline()
        self.src = create_node(NodeType.SAMPLE_VIEW_DATA, name="Src")  # outputs only
        self.tgt = create_node(NodeType.THRESHOLD, name="Tgt")
        self.p.add_node(self.src)
        self.p.add_node(self.tgt)

    def test_compatible_connection_succeeds(self):
        c = self.p.add_connection(
            self.src.id,
            self.src.get_output("volume").id,
            self.tgt.id,
            self.tgt.get_input("volume").id,
        )
        self.assertIn(c.id, self.p.connections)

    def test_unknown_source_node_raises(self):
        with self.assertRaises(ValueError):
            self.p.add_connection(
                "missing",
                "x",
                self.tgt.id,
                self.tgt.get_input("volume").id,
            )

    def test_unknown_target_node_raises(self):
        with self.assertRaises(ValueError):
            self.p.add_connection(
                self.src.id,
                self.src.get_output("volume").id,
                "missing",
                "x",
            )

    def test_input_as_source_raises(self):
        # Tgt's input port cannot act as a source.
        with self.assertRaises(ValueError):
            self.p.add_connection(
                self.tgt.id,
                self.tgt.get_input("volume").id,
                self.src.id,
                self.src.get_output("volume").id,
            )

    def test_output_as_target_raises(self):
        # Src's output port cannot act as a target.
        with self.assertRaises(ValueError):
            self.p.add_connection(
                self.src.id,
                self.src.get_output("volume").id,
                self.src.id,
                self.src.get_output("position").id,
            )

    def test_type_mismatch_raises(self):
        # POSITION → VOLUME is not in the compatibility matrix.
        with self.assertRaises(ValueError):
            self.p.add_connection(
                self.src.id,
                self.src.get_output("position").id,
                self.tgt.id,
                self.tgt.get_input("volume").id,
            )

    def test_duplicate_target_input_raises(self):
        self.p.add_connection(
            self.src.id,
            self.src.get_output("volume").id,
            self.tgt.id,
            self.tgt.get_input("volume").id,
        )
        other_src = create_node(NodeType.SAMPLE_VIEW_DATA, name="Src2")
        self.p.add_node(other_src)
        with self.assertRaises(ValueError):
            self.p.add_connection(
                other_src.id,
                other_src.get_output("volume").id,
                self.tgt.id,
                self.tgt.get_input("volume").id,
            )

    def test_self_connection_raises(self):
        c = create_node(NodeType.CONDITIONAL, name="C")
        self.p.add_node(c)
        with self.assertRaises(ValueError):
            self.p.add_connection(
                c.id,
                c.get_output("pass_through").id,
                c.id,
                c.get_input("value").id,
            )

    def test_cycle_blocked_by_add_connection(self):
        a = create_node(NodeType.THRESHOLD, name="A")
        b = create_node(NodeType.THRESHOLD, name="B")
        p = Pipeline()
        p.add_node(a)
        p.add_node(b)
        p.add_connection(
            a.id,
            a.get_output("mask").id,
            b.id,
            b.get_input("volume").id,
        )
        with self.assertRaises(ValueError):
            p.add_connection(
                b.id,
                b.get_output("mask").id,
                a.id,
                a.get_input("volume").id,
            )


class TestValidate(unittest.TestCase):
    def test_empty_pipeline_reports_error(self):
        p = Pipeline()
        errs = p.validate()
        self.assertTrue(any("no nodes" in e.lower() for e in errs))

    def test_required_input_unconnected_reports_error(self):
        p = Pipeline()
        fe = create_node(NodeType.FOR_EACH, name="FE")  # 'collection' is required
        p.add_node(fe)
        errs = p.validate()
        self.assertTrue(
            any("collection" in e.lower() for e in errs),
            f"Expected 'collection' in errors: {errs}",
        )

    def test_minimal_pipeline_validates_clean(self):
        p = Pipeline()
        n = create_node(NodeType.THRESHOLD, name="T")
        p.add_node(n)
        self.assertEqual(p.validate(), [])

    def test_validate_detects_manually_injected_cycle(self):
        # add_connection blocks cycles; bypass it to verify validate() catches one.
        p = Pipeline()
        a = create_node(NodeType.THRESHOLD, name="A")
        b = create_node(NodeType.THRESHOLD, name="B")
        p.add_node(a)
        p.add_node(b)
        p.add_connection(
            a.id,
            a.get_output("mask").id,
            b.id,
            b.get_input("volume").id,
        )
        bad = Connection(
            id=str(uuid.uuid4()),
            source_node_id=b.id,
            source_port_id=b.get_output("mask").id,
            target_node_id=a.id,
            target_port_id=a.get_input("volume").id,
        )
        p.connections[bad.id] = bad

        errs = p.validate()
        self.assertTrue(any("cycle" in e.lower() for e in errs))


class TestSerialization(unittest.TestCase):
    def test_round_trip_preserves_nodes_connections_config(self):
        p = Pipeline(name="RoundTrip")
        a = create_node(NodeType.SAMPLE_VIEW_DATA, name="A")
        b = create_node(
            NodeType.THRESHOLD,
            name="B",
            config={"gauss_sigma": 1.5, "min_object_size": 42},
        )
        p.add_node(a)
        p.add_node(b)
        p.add_connection(
            a.id,
            a.get_output("volume").id,
            b.id,
            b.get_input("volume").id,
        )

        d = p.to_dict()
        # JSON-serializable
        json.dumps(d)

        p2 = Pipeline.from_dict(d)
        self.assertEqual(p2.name, "RoundTrip")
        self.assertEqual(set(p2.nodes), {a.id, b.id})
        self.assertEqual(len(p2.connections), 1)
        b2 = p2.get_node(b.id)
        self.assertEqual(b2.config["gauss_sigma"], 1.5)
        self.assertEqual(b2.config["min_object_size"], 42)
        # Validates clean after round-trip.
        self.assertEqual(p2.validate(), [])

    def test_load_fixture_01_threshold_only(self):
        d = json.loads((FIXTURES / "01_threshold_only.json").read_text())
        p = Pipeline.from_dict(d)
        self.assertEqual(len(p.nodes), 1)
        self.assertEqual(p.validate(), [])

    def test_load_fixture_02_threshold_foreach(self):
        d = json.loads((FIXTURES / "02_threshold_foreach.json").read_text())
        p = Pipeline.from_dict(d)
        self.assertEqual(len(p.nodes), 2)
        self.assertEqual(p.validate(), [])
        # Threshold.objects feeds ForEach.collection.
        self.assertEqual(len(p.connections), 1)

    def test_load_fixture_03_threshold_foreach_workflow(self):
        d = json.loads((FIXTURES / "03_threshold_foreach_workflow.json").read_text())
        p = Pipeline.from_dict(d)
        self.assertEqual(len(p.nodes), 3)
        self.assertEqual(p.validate(), [])
        # ForEach.current_item feeds Workflow.position; Workflow lives inside
        # the ForEach scope.
        self.assertEqual(len(p.connections), 2)


if __name__ == "__main__":
    unittest.main()
