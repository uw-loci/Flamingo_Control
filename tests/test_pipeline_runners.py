"""Per-runner unit tests for all 9 pipeline NodeType runners.

Each runner has ~5 cases: happy path, missing required input, missing service,
cancellation mid-run, config edge case. Runners are exercised directly with
``run(node, pipeline, context)`` against fakes from ``pipeline_helpers``.

ForEach/Conditional/TimedLoop runners need a ``set_executor()`` and
``set_scope_resolver()`` injection — a small ``MockExecutor`` records the
``execute_subgraph`` calls so we can assert which body/branch ran without
spinning up a real ``PipelineExecutor`` thread.
"""

from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Self-contained path setup so this file works under run_tests.py, pytest, or
# `python -m unittest discover`. tests/ for pipeline_helpers; src/ for py2flamingo.
_TESTS_DIR = Path(__file__).resolve().parent
_SRC = _TESTS_DIR.parent / "src"
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
from pipeline_helpers import (  # noqa: E402
    FakePositionController,
    FakeVoxelStorage,
    FakeWorkflowFacade,
    fake_coord_config,
    make_bright_volume,
)

from py2flamingo.pipeline.engine.context import ExecutionContext  # noqa: E402
from py2flamingo.pipeline.engine.node_runners.conditional_runner import (  # noqa: E402
    ConditionalRunner,
)
from py2flamingo.pipeline.engine.node_runners.external_command_runner import (  # noqa: E402
    ExternalCommandRunner,
)
from py2flamingo.pipeline.engine.node_runners.foreach_runner import (  # noqa: E402
    ForEachRunner,
)
from py2flamingo.pipeline.engine.node_runners.overview_analysis_runner import (  # noqa: E402
    OverviewAnalysisRunner,
)
from py2flamingo.pipeline.engine.node_runners.post_processing_runner import (  # noqa: E402
    PostProcessingRunner,
)
from py2flamingo.pipeline.engine.node_runners.sample_view_data_runner import (  # noqa: E402
    SampleViewDataRunner,
)
from py2flamingo.pipeline.engine.node_runners.threshold_runner import (  # noqa: E402
    ThresholdRunner,
)
from py2flamingo.pipeline.engine.node_runners.timed_loop_runner import (  # noqa: E402
    TimedLoopRunner,
)
from py2flamingo.pipeline.engine.node_runners.workflow_runner import (  # noqa: E402
    WorkflowRunner,
)
from py2flamingo.pipeline.models.detected_object import DetectedObject  # noqa: E402
from py2flamingo.pipeline.models.pipeline import (  # noqa: E402
    NodeType,
    Pipeline,
    create_node,
)
from py2flamingo.pipeline.models.port_types import PortType, PortValue  # noqa: E402

# ---------------------------------------------------------------------------
# Test plumbing
# ---------------------------------------------------------------------------


class MockSignal:
    """Stand-in for ``pyqtSignal`` — records emit calls without needing Qt."""

    def __init__(self):
        self.calls: list = []

    def emit(self, *args):
        self.calls.append(args)


class MockExecutor:
    """Stand-in for ``PipelineExecutor`` used by ForEach/Conditional/TimedLoop.

    Records each ``execute_subgraph`` call so tests can assert which body or
    branch ran. Optionally raises a configurable exception to simulate body
    failure.
    """

    def __init__(self, *, raise_on_call=None):
        self.foreach_iteration = MockSignal()
        self.executed: list = []
        self._raise = raise_on_call

    def execute_subgraph(self, node_ids, context):
        self.executed.append((list(node_ids), context))
        if self._raise:
            raise self._raise


def _single(node_type: NodeType, config=None) -> tuple[Pipeline, "PipelineNode"]:
    """Build a Pipeline with a single node of the given type and return both."""
    p = Pipeline()
    n = create_node(node_type, name=node_type.name, config=config or {})
    p.add_node(n)
    return p, n


def _connect(p, src, src_port_name, tgt, tgt_port_name):
    return p.add_connection(
        src.id,
        src.get_output(src_port_name).id,
        tgt.id,
        tgt.get_input(tgt_port_name).id,
    )


# ---------------------------------------------------------------------------
# WorkflowRunner
# ---------------------------------------------------------------------------


class TestWorkflowRunner(unittest.TestCase):
    def _runner(self):
        return WorkflowRunner()

    def test_happy_path_completes_and_emits_outputs(self):
        p, n = _single(
            NodeType.WORKFLOW,
            config={"template_file": "/tmp/template.txt", "use_input_position": False},
        )
        facade = FakeWorkflowFacade(output_path="/tmp/wf_out")
        ctx = ExecutionContext(services={"workflow_facade": facade})
        self._runner().run(n, p, ctx)

        self.assertEqual(len(facade.started_workflows), 1)
        completed = ctx.get_port_value(n.get_output("completed").id)
        self.assertIsNotNone(completed)
        self.assertTrue(completed.data)
        file_path = ctx.get_port_value(n.get_output("file_path").id)
        self.assertEqual(file_path.data, "/tmp/wf_out")

    def test_missing_facade_raises(self):
        p, n = _single(NodeType.WORKFLOW, config={"template_file": "/tmp/x.txt"})
        ctx = ExecutionContext(services={})
        with self.assertRaisesRegex(RuntimeError, "WorkflowFacade"):
            self._runner().run(n, p, ctx)

    def test_no_template_raises(self):
        p, n = _single(NodeType.WORKFLOW, config={})
        ctx = ExecutionContext(services={"workflow_facade": FakeWorkflowFacade()})
        with self.assertRaisesRegex(RuntimeError, "template file"):
            self._runner().run(n, p, ctx)

    def test_legacy_inline_mode_short_circuits(self):
        p, n = _single(
            NodeType.WORKFLOW,
            config={"config_mode": "inline", "template_file": "/tmp/x.txt"},
        )
        facade = FakeWorkflowFacade()
        ctx = ExecutionContext(services={"workflow_facade": facade})
        self._runner().run(n, p, ctx)
        # Must NOT have started any workflow — legacy mode is warn-and-skip.
        self.assertEqual(facade.started_workflows, [])
        self.assertTrue(ctx.get_port_value(n.get_output("completed").id).data)

    def test_cancellation_stops_workflow(self):
        # Status sequence: RUNNING (so the runner stays in the poll loop)
        # then we cancel — runner should stop_workflow() and raise.
        p, n = _single(NodeType.WORKFLOW, config={"template_file": "/tmp/x.txt"})
        facade = FakeWorkflowFacade(
            status_sequence=[SimpleNamespace(name="RUNNING")] * 3,
        )
        ctx = ExecutionContext(services={"workflow_facade": facade})
        ctx.cancel()  # cancelled before first poll
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self._runner().run(n, p, ctx)
        self.assertTrue(facade.stop_called)


# ---------------------------------------------------------------------------
# ThresholdRunner
# ---------------------------------------------------------------------------


class TestThresholdRunner(unittest.TestCase):
    def _runner(self):
        return ThresholdRunner()

    def _bright_volume(self):
        return make_bright_volume(shape=(4, 8, 8), bright_value=255, bright_count=2)

    def test_happy_path_with_voxel_storage(self):
        p, n = _single(
            NodeType.THRESHOLD,
            config={
                "channel_thresholds": {0: 100},
                "enabled_channels": [0],
                "min_object_size": 0,
                "default_threshold": 100,
            },
        )
        ctx = ExecutionContext(
            services={"voxel_storage": FakeVoxelStorage({0: self._bright_volume()})}
        )
        self._runner().run(n, p, ctx)

        objects = ctx.get_port_value(n.get_output("objects").id).data
        count = ctx.get_port_value(n.get_output("count").id).data
        self.assertGreaterEqual(count, 1)
        self.assertEqual(len(objects), count)

    def test_no_volume_raises(self):
        p, n = _single(
            NodeType.THRESHOLD,
            config={
                "channel_thresholds": {0: 100},
                "default_threshold": 100,
            },
        )
        ctx = ExecutionContext(services={})
        with self.assertRaisesRegex(RuntimeError, "No input volumes"):
            self._runner().run(n, p, ctx)

    def test_default_threshold_used_when_no_channel_thresholds_configured(self):
        # Pipe a volume in directly via a SAMPLE_VIEW_DATA upstream so the
        # runner's "no input + voxel_storage fallback" branch isn't taken.
        p = Pipeline()
        src = create_node(NodeType.SAMPLE_VIEW_DATA, name="Src")
        tgt = create_node(
            NodeType.THRESHOLD,
            name="T",
            config={
                "channel_thresholds": {},
                "default_threshold": 100,
            },
        )
        p.add_node(src)
        p.add_node(tgt)
        _connect(p, src, "volume", tgt, "volume")

        ctx = ExecutionContext(services={})
        ctx.set_port_value(
            src.get_output("volume").id,
            PortValue(port_type=PortType.VOLUME, data=self._bright_volume()),
        )
        self._runner().run(tgt, p, ctx)
        # Channel 0 was assigned default_threshold and produced an object.
        self.assertGreaterEqual(ctx.get_port_value(tgt.get_output("count").id).data, 1)

    def test_uses_coord_config_voxel_size_when_provided(self):
        p, n = _single(
            NodeType.THRESHOLD,
            config={
                "channel_thresholds": {0: 100},
                "default_threshold": 100,
            },
        )
        ctx = ExecutionContext(
            services={
                "voxel_storage": FakeVoxelStorage({0: self._bright_volume()}),
                "coordinate_config": fake_coord_config(),
            }
        )
        # Should not raise; coord_config is consumed for voxel_to_stage transform.
        self._runner().run(n, p, ctx)

    def test_enabled_channels_filter(self):
        # channel_thresholds has 0 and 1, but enabled_channels excludes 1.
        p, n = _single(
            NodeType.THRESHOLD,
            config={
                "channel_thresholds": {0: 100, 1: 100},
                "enabled_channels": [0],
                "default_threshold": 100,
            },
        )
        # Only channel 0 has data, channel 1 is empty.
        ctx = ExecutionContext(
            services={
                "voxel_storage": FakeVoxelStorage(
                    {0: self._bright_volume(), 1: np.zeros((4, 8, 8), dtype=np.uint16)}
                )
            }
        )
        # Should still complete cleanly using only channel 0.
        self._runner().run(n, p, ctx)
        self.assertGreaterEqual(ctx.get_port_value(n.get_output("count").id).data, 1)


# ---------------------------------------------------------------------------
# ForEachRunner
# ---------------------------------------------------------------------------


def _build_foreach_pipeline_with_body(body_node_type=NodeType.EXTERNAL_COMMAND):
    """ForEach with one body node connected via current_item.

    Body is whatever ``body_node_type`` is — its actual runner is replaced by
    the MockExecutor in tests, so the body type doesn't matter.
    """
    p = Pipeline()
    fe = create_node(NodeType.FOR_EACH, name="FE")
    body = create_node(body_node_type, name="Body")
    p.add_node(fe)
    p.add_node(body)
    # current_item (OBJECT) → body's input (ANY for EXTERNAL_COMMAND)
    if body_node_type == NodeType.EXTERNAL_COMMAND:
        p.add_connection(
            fe.id,
            fe.get_output("current_item").id,
            body.id,
            body.get_input("input_data").id,
        )
    return p, fe, body


def _make_detected_objects(n: int):
    """Build n DetectedObjects with predictable centroids."""
    return [
        DetectedObject(
            label_id=i + 1,
            centroid_voxel=(0.0, 0.0, float(i)),
            centroid_stage=(float(i), 0.0, 0.0),
            bounding_box=(slice(0, 1), slice(0, 1), slice(i, i + 1)),
            volume_voxels=1,
            volume_mm3=0.0,
        )
        for i in range(n)
    ]


class TestForEachRunner(unittest.TestCase):
    def _runner(self):
        return ForEachRunner()

    def test_happy_path_executes_body_per_item(self):
        p, fe, body = _build_foreach_pipeline_with_body()
        # Feed a 3-item collection into ForEach.collection.
        ctx = ExecutionContext(services={})
        ctx.set_port_value(
            fe.get_input("collection").id,
            PortValue(port_type=PortType.OBJECT_LIST, data=_make_detected_objects(3)),
        )
        # The runner reads via _get_input, which follows connections — but
        # ForEach.collection has no connection here. Inject the upstream
        # value by binding the source port; easiest is to add a SAMPLE_VIEW
        # with OBJECT_LIST output... but SAMPLE_VIEW only outputs VOLUME.
        # Easier: connect THRESHOLD as upstream and put the value at its
        # objects output port.
        p2 = Pipeline()
        thresh = create_node(NodeType.THRESHOLD, name="T")
        p2.add_node(thresh)
        p2.add_node(fe)
        p2.add_connection(
            thresh.id,
            thresh.get_output("objects").id,
            fe.id,
            fe.get_input("collection").id,
        )
        # Body has to live in the new pipeline too.
        p2.add_node(body)
        p2.add_connection(
            fe.id,
            fe.get_output("current_item").id,
            body.id,
            body.get_input("input_data").id,
        )
        ctx2 = ExecutionContext(services={})
        ctx2.set_port_value(
            thresh.get_output("objects").id,
            PortValue(port_type=PortType.OBJECT_LIST, data=_make_detected_objects(3)),
        )

        from py2flamingo.pipeline.engine.scope_resolver import ScopeResolver

        resolver = ScopeResolver(p2)
        resolver.resolve()

        runner = self._runner()
        executor = MockExecutor()
        runner.set_scope_resolver(resolver)
        runner.set_executor(executor)
        runner.run(fe, p2, ctx2)

        self.assertEqual(len(executor.executed), 3)
        self.assertEqual(len(executor.foreach_iteration.calls), 3)
        # completed signal set on the ForEach node
        self.assertTrue(ctx2.get_port_value(fe.get_output("completed").id).data)

    def test_empty_collection_skips_body_but_completes(self):
        from py2flamingo.pipeline.engine.scope_resolver import ScopeResolver

        p2 = Pipeline()
        thresh = create_node(NodeType.THRESHOLD, name="T")
        fe = create_node(NodeType.FOR_EACH, name="FE")
        ext = create_node(NodeType.EXTERNAL_COMMAND, name="Ext")
        p2.add_node(thresh)
        p2.add_node(fe)
        p2.add_node(ext)
        p2.add_connection(
            thresh.id,
            thresh.get_output("objects").id,
            fe.id,
            fe.get_input("collection").id,
        )
        p2.add_connection(
            fe.id,
            fe.get_output("current_item").id,
            ext.id,
            ext.get_input("input_data").id,
        )
        ctx = ExecutionContext(services={})
        ctx.set_port_value(
            thresh.get_output("objects").id,
            PortValue(port_type=PortType.OBJECT_LIST, data=[]),
        )
        resolver = ScopeResolver(p2)
        resolver.resolve()

        runner = self._runner()
        runner.set_scope_resolver(resolver)
        executor = MockExecutor()
        runner.set_executor(executor)
        runner.run(fe, p2, ctx)
        # No iterations → no execute_subgraph calls, but completed signal set.
        self.assertEqual(executor.executed, [])
        self.assertTrue(ctx.get_port_value(fe.get_output("completed").id).data)

    def test_non_list_collection_raises(self):
        from py2flamingo.pipeline.engine.scope_resolver import ScopeResolver

        p2 = Pipeline()
        thresh = create_node(NodeType.THRESHOLD, name="T")
        fe = create_node(NodeType.FOR_EACH, name="FE")
        p2.add_node(thresh)
        p2.add_node(fe)
        p2.add_connection(
            thresh.id,
            thresh.get_output("objects").id,
            fe.id,
            fe.get_input("collection").id,
        )
        ctx = ExecutionContext(services={})
        # Inject a non-list (a dict).
        ctx.set_port_value(
            thresh.get_output("objects").id,
            PortValue(port_type=PortType.OBJECT_LIST, data={"oops": True}),
        )
        resolver = ScopeResolver(p2)
        resolver.resolve()

        runner = self._runner()
        runner.set_scope_resolver(resolver)
        runner.set_executor(MockExecutor())
        with self.assertRaisesRegex(RuntimeError, "must be a list"):
            runner.run(fe, p2, ctx)

    def test_missing_executor_or_resolver_raises(self):
        p, fe = _single(NodeType.FOR_EACH)
        ctx = ExecutionContext(services={})
        with self.assertRaisesRegex(RuntimeError, "scope_resolver"):
            self._runner().run(fe, p, ctx)

    def test_cancellation_mid_iteration_raises(self):
        from py2flamingo.pipeline.engine.scope_resolver import ScopeResolver

        p2 = Pipeline()
        thresh = create_node(NodeType.THRESHOLD, name="T")
        fe = create_node(NodeType.FOR_EACH, name="FE")
        ext = create_node(NodeType.EXTERNAL_COMMAND, name="Ext")
        p2.add_node(thresh)
        p2.add_node(fe)
        p2.add_node(ext)
        p2.add_connection(
            thresh.id,
            thresh.get_output("objects").id,
            fe.id,
            fe.get_input("collection").id,
        )
        p2.add_connection(
            fe.id,
            fe.get_output("current_item").id,
            ext.id,
            ext.get_input("input_data").id,
        )
        ctx = ExecutionContext(services={})
        ctx.set_port_value(
            thresh.get_output("objects").id,
            PortValue(port_type=PortType.OBJECT_LIST, data=_make_detected_objects(5)),
        )
        ctx.cancel()  # cancelled before first iteration

        resolver = ScopeResolver(p2)
        resolver.resolve()
        runner = self._runner()
        runner.set_scope_resolver(resolver)
        runner.set_executor(MockExecutor())
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            runner.run(fe, p2, ctx)


# ---------------------------------------------------------------------------
# ConditionalRunner
# ---------------------------------------------------------------------------


def _build_conditional_pipeline_with_branches():
    """Conditional with two branches; each branch has one node."""
    from py2flamingo.pipeline.engine.scope_resolver import ScopeResolver

    p = Pipeline()
    thresh = create_node(NodeType.THRESHOLD, name="T")
    cond = create_node(
        NodeType.CONDITIONAL,
        name="C",
        config={"comparison_op": ">", "threshold_value": 0},
    )
    true_body = create_node(NodeType.EXTERNAL_COMMAND, name="True")
    false_body = create_node(NodeType.EXTERNAL_COMMAND, name="False")
    p.add_node(thresh)
    p.add_node(cond)
    p.add_node(true_body)
    p.add_node(false_body)
    p.add_connection(
        thresh.id,
        thresh.get_output("count").id,
        cond.id,
        cond.get_input("value").id,
    )
    p.add_connection(
        cond.id,
        cond.get_output("true_branch").id,
        true_body.id,
        true_body.get_input("trigger").id,
    )
    p.add_connection(
        cond.id,
        cond.get_output("false_branch").id,
        false_body.id,
        false_body.get_input("trigger").id,
    )
    resolver = ScopeResolver(p)
    resolver.resolve()
    return p, cond, thresh, true_body, false_body, resolver


class TestConditionalRunner(unittest.TestCase):
    def _runner(self):
        return ConditionalRunner()

    def test_true_branch_executes_when_condition_true(self):
        p, cond, thresh, true_body, false_body, resolver = (
            _build_conditional_pipeline_with_branches()
        )
        ctx = ExecutionContext(services={})
        ctx.set_port_value(
            thresh.get_output("count").id,
            PortValue(port_type=PortType.SCALAR, data=5),
        )

        runner = self._runner()
        executor = MockExecutor()
        runner.set_scope_resolver(resolver)
        runner.set_executor(executor)
        runner.run(cond, p, ctx)

        # One execute_subgraph call with the true_body
        self.assertEqual(len(executor.executed), 1)
        ran_ids, _ = executor.executed[0]
        self.assertIn(true_body.id, ran_ids)
        self.assertNotIn(false_body.id, ran_ids)

    def test_false_branch_executes_when_condition_false(self):
        p, cond, thresh, true_body, false_body, resolver = (
            _build_conditional_pipeline_with_branches()
        )
        ctx = ExecutionContext(services={})
        ctx.set_port_value(
            thresh.get_output("count").id,
            PortValue(port_type=PortType.SCALAR, data=0),
        )
        runner = self._runner()
        executor = MockExecutor()
        runner.set_scope_resolver(resolver)
        runner.set_executor(executor)
        runner.run(cond, p, ctx)

        ran_ids, _ = executor.executed[0]
        self.assertIn(false_body.id, ran_ids)
        self.assertNotIn(true_body.id, ran_ids)

    def test_missing_value_raises(self):
        p, cond = _single(
            NodeType.CONDITIONAL,
            config={"comparison_op": ">", "threshold_value": 0},
        )
        ctx = ExecutionContext(services={})
        from py2flamingo.pipeline.engine.scope_resolver import ScopeResolver

        resolver = ScopeResolver(p)
        resolver.resolve()
        runner = self._runner()
        runner.set_scope_resolver(resolver)
        runner.set_executor(MockExecutor())
        with self.assertRaisesRegex(RuntimeError, "no input value"):
            runner.run(cond, p, ctx)

    def test_unknown_operator_raises(self):
        p, cond, thresh, *_ = _build_conditional_pipeline_with_branches()
        cond.config["comparison_op"] = "??"
        ctx = ExecutionContext(services={})
        ctx.set_port_value(
            thresh.get_output("count").id,
            PortValue(port_type=PortType.SCALAR, data=5),
        )
        from py2flamingo.pipeline.engine.scope_resolver import ScopeResolver

        resolver = ScopeResolver(p)
        resolver.resolve()
        runner = self._runner()
        runner.set_scope_resolver(resolver)
        runner.set_executor(MockExecutor())
        with self.assertRaisesRegex(RuntimeError, "Unknown comparison"):
            runner.run(cond, p, ctx)

    def test_pass_through_always_set(self):
        p, cond, thresh, *_ = _build_conditional_pipeline_with_branches()
        ctx = ExecutionContext(services={})
        ctx.set_port_value(
            thresh.get_output("count").id,
            PortValue(port_type=PortType.SCALAR, data=42),
        )
        from py2flamingo.pipeline.engine.scope_resolver import ScopeResolver

        resolver = ScopeResolver(p)
        resolver.resolve()
        runner = self._runner()
        runner.set_scope_resolver(resolver)
        runner.set_executor(MockExecutor())
        runner.run(cond, p, ctx)

        passthrough = ctx.get_port_value(cond.get_output("pass_through").id)
        self.assertEqual(passthrough.data, 42)


# ---------------------------------------------------------------------------
# ExternalCommandRunner
# ---------------------------------------------------------------------------


class TestExternalCommandRunner(unittest.TestCase):
    def _runner(self):
        return ExternalCommandRunner()

    def test_happy_path_writes_and_reads_json(self):
        # Command writes a JSON file in {output_dir}; runner parses with
        # output_format=json.
        p, n = _single(
            NodeType.EXTERNAL_COMMAND,
            config={
                "command_template": ("printf '[1,2,3]' > '{output_dir}/out.json'"),
                "input_format": "json",
                "output_format": "json",
                "timeout_seconds": 30,
            },
        )
        ctx = ExecutionContext(services={})
        self._runner().run(n, p, ctx)

        out = ctx.get_port_value(n.get_output("output_data").id)
        self.assertEqual(out.data, [1, 2, 3])
        self.assertTrue(ctx.get_port_value(n.get_output("completed").id).data)

    def test_empty_command_template_raises(self):
        p, n = _single(NodeType.EXTERNAL_COMMAND, config={"command_template": ""})
        ctx = ExecutionContext(services={})
        with self.assertRaisesRegex(RuntimeError, "no command_template"):
            self._runner().run(n, p, ctx)

    def test_nonzero_exit_raises(self):
        p, n = _single(
            NodeType.EXTERNAL_COMMAND,
            config={
                "command_template": "exit 7",
                "timeout_seconds": 5,
            },
        )
        ctx = ExecutionContext(services={})
        with self.assertRaisesRegex(RuntimeError, r"exit 7"):
            self._runner().run(n, p, ctx)

    def test_timeout_raises(self):
        p, n = _single(
            NodeType.EXTERNAL_COMMAND,
            config={
                "command_template": "sleep 5",
                "timeout_seconds": 1,
            },
        )
        ctx = ExecutionContext(services={})
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            self._runner().run(n, p, ctx)

    def test_no_output_files_returns_none(self):
        p, n = _single(
            NodeType.EXTERNAL_COMMAND,
            config={
                "command_template": "true",  # produces nothing
                "output_format": "json",
                "timeout_seconds": 5,
            },
        )
        ctx = ExecutionContext(services={})
        self._runner().run(n, p, ctx)
        # No output files → output_data is None but the node still completes.
        self.assertIsNone(ctx.get_port_value(n.get_output("output_data").id).data)


# ---------------------------------------------------------------------------
# SampleViewDataRunner
# ---------------------------------------------------------------------------


class TestSampleViewDataRunner(unittest.TestCase):
    def _runner(self):
        return SampleViewDataRunner()

    def _config_all_off_except(self, *enabled):
        cfg = {f"channel_{i}": False for i in range(8)}
        for i in enabled:
            cfg[f"channel_{i}"] = True
        return cfg

    def test_happy_path_reads_volumes(self):
        p, n = _single(NodeType.SAMPLE_VIEW_DATA, config=self._config_all_off_except(0))
        ctx = ExecutionContext(
            services={
                "voxel_storage": FakeVoxelStorage({0: make_bright_volume()}),
                "position_controller": FakePositionController(),
            }
        )
        self._runner().run(n, p, ctx)

        volumes_value = ctx.get_port_value(n.get_output("volume").id)
        self.assertIn(0, volumes_value.data)
        position = ctx.get_port_value(n.get_output("position").id)
        self.assertEqual(len(position.data), 4)

    def test_no_channels_selected_raises(self):
        p, n = _single(NodeType.SAMPLE_VIEW_DATA, config=self._config_all_off_except())
        ctx = ExecutionContext(services={})
        with self.assertRaisesRegex(RuntimeError, "No channels selected"):
            self._runner().run(n, p, ctx)

    def test_missing_voxel_storage_raises(self):
        p, n = _single(NodeType.SAMPLE_VIEW_DATA, config=self._config_all_off_except(0))
        ctx = ExecutionContext(services={})
        with self.assertRaisesRegex(RuntimeError, "Voxel storage"):
            self._runner().run(n, p, ctx)

    def test_no_data_in_any_channel_raises(self):
        p, n = _single(NodeType.SAMPLE_VIEW_DATA, config=self._config_all_off_except(0))
        ctx = ExecutionContext(
            services={
                "voxel_storage": FakeVoxelStorage(
                    {0: np.zeros((4, 8, 8), dtype=np.uint16)}
                )
            }
        )
        with self.assertRaisesRegex(RuntimeError, "No volume data"):
            self._runner().run(n, p, ctx)

    def test_coord_config_pass_through(self):
        p, n = _single(NodeType.SAMPLE_VIEW_DATA, config=self._config_all_off_except(0))
        cfg = fake_coord_config()
        ctx = ExecutionContext(
            services={
                "voxel_storage": FakeVoxelStorage({0: make_bright_volume()}),
                "coordinate_config": cfg,
            }
        )
        self._runner().run(n, p, ctx)
        self.assertEqual(ctx.get_port_value(n.get_output("config").id).data, cfg)


# ---------------------------------------------------------------------------
# OverviewAnalysisRunner
# ---------------------------------------------------------------------------


class TestOverviewAnalysisRunner(unittest.TestCase):
    def _runner(self):
        return OverviewAnalysisRunner()

    def _2d_image(self):
        # Bright square aligned to tile boundaries (4×4 tiles → 16×16 each).
        # rows 16:48 / cols 16:48 fully covers tiles (1,1)(1,2)(2,1)(2,2)
        # with intensity 200 — well within [intensity_min=100, intensity_max=255].
        img = np.full((64, 64), 30, dtype=np.uint8)
        img[16:48, 16:48] = 200
        return img

    def test_happy_path_with_input_image(self):
        p = Pipeline()
        # Use SAMPLE_VIEW_DATA as a synthetic upstream — its volume output is
        # VOLUME-typed which matches OverviewAnalysis.image input.
        src = create_node(NodeType.SAMPLE_VIEW_DATA, name="Src")
        oa = create_node(
            NodeType.OVERVIEW_ANALYSIS,
            name="OA",
            config={
                "method": "intensity",
                "tiles_x": 4,
                "tiles_y": 4,
                "intensity_min": 100.0,
                "intensity_max": 255.0,
            },
        )
        p.add_node(src)
        p.add_node(oa)
        _connect(p, src, "volume", oa, "image")

        ctx = ExecutionContext(services={})
        ctx.set_port_value(
            src.get_output("volume").id,
            PortValue(port_type=PortType.VOLUME, data=self._2d_image()),
        )
        self._runner().run(oa, p, ctx)

        count = ctx.get_port_value(oa.get_output("count").id).data
        self.assertGreaterEqual(count, 1)
        # mask shape matches tile grid
        mask = ctx.get_port_value(oa.get_output("mask").id).data
        self.assertEqual(mask.shape, (4, 4))

    def test_no_image_no_path_raises(self):
        p, n = _single(
            NodeType.OVERVIEW_ANALYSIS,
            config={"method": "intensity", "tiles_x": 4, "tiles_y": 4},
        )
        ctx = ExecutionContext(services={})
        with self.assertRaisesRegex(RuntimeError, "No input image"):
            self._runner().run(n, p, ctx)

    def test_image_path_loads_npy(self):
        # Save a small npy image to a tempfile and let the runner read it.
        import tempfile

        img = self._2d_image()
        with tempfile.TemporaryDirectory() as td:
            npy_path = Path(td) / "img.npy"
            np.save(str(npy_path), img)
            p, n = _single(
                NodeType.OVERVIEW_ANALYSIS,
                config={
                    "method": "intensity",
                    "tiles_x": 4,
                    "tiles_y": 4,
                    "image_path": str(npy_path),
                    "intensity_min": 100.0,
                    "intensity_max": 255.0,
                },
            )
            ctx = ExecutionContext(services={})
            self._runner().run(n, p, ctx)
        self.assertGreaterEqual(ctx.get_port_value(n.get_output("count").id).data, 1)

    def test_missing_image_path_raises(self):
        p, n = _single(
            NodeType.OVERVIEW_ANALYSIS,
            config={
                "method": "intensity",
                "tiles_x": 4,
                "tiles_y": 4,
                "image_path": "/no/such/file.tif",
            },
        )
        ctx = ExecutionContext(services={})
        with self.assertRaisesRegex(RuntimeError, "Image file not found"):
            self._runner().run(n, p, ctx)

    def test_3d_image_takes_first_slice(self):
        p = Pipeline()
        src = create_node(NodeType.SAMPLE_VIEW_DATA, name="Src")
        oa = create_node(
            NodeType.OVERVIEW_ANALYSIS,
            name="OA",
            config={
                "method": "intensity",
                "tiles_x": 2,
                "tiles_y": 2,
                "intensity_min": 100.0,
                "intensity_max": 255.0,
            },
        )
        p.add_node(src)
        p.add_node(oa)
        _connect(p, src, "volume", oa, "image")

        # 4D-with-Z=1, plus 2D slice taken — runner code at line 71 handles
        # 3D by taking [0]. Use a 3D volume with non-RGB depth.
        vol = np.stack([self._2d_image(), self._2d_image() // 2], axis=0)
        ctx = ExecutionContext(services={})
        ctx.set_port_value(
            src.get_output("volume").id,
            PortValue(port_type=PortType.VOLUME, data=vol),
        )
        # No raise — 3D handling kicks in.
        self._runner().run(oa, p, ctx)


# ---------------------------------------------------------------------------
# PostProcessingRunner
# ---------------------------------------------------------------------------


class TestPostProcessingRunner(unittest.TestCase):
    def _runner(self):
        return PostProcessingRunner()

    def test_missing_acquisition_dir_raises(self):
        p, n = _single(NodeType.POST_PROCESSING, config={})
        ctx = ExecutionContext(services={})
        with self.assertRaisesRegex(ValueError, "No acquisition directory"):
            self._runner().run(n, p, ctx)

    def test_nonexistent_acquisition_dir_raises(self):
        p, n = _single(
            NodeType.POST_PROCESSING,
            config={"acquisition_dir": "/no/such/dir/__missing__"},
        )
        ctx = ExecutionContext(services={})
        with self.assertRaises(FileNotFoundError):
            self._runner().run(n, p, ctx)

    def test_happy_path_calls_stitching_pipeline(self):
        # Patch the lazy-imported StitchingPipeline so we don't need pyimagej.
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            acq = Path(td) / "acq"
            acq.mkdir()

            class FakeStitchingPipeline:
                def __init__(self, config, cancelled_fn=None):
                    self.config = config
                    self.cancelled_fn = cancelled_fn

                def run(self, acq_path, output_path, channels=None):
                    return Path(output_path)

            class FakeStitchingConfig:
                # Real StitchingConfig exposes output_format etc. as attrs;
                # the runner reads `config.output_format` after construction.
                def __init__(self, **kwargs):
                    for k, v in kwargs.items():
                        setattr(self, k, v)
                    self.output_format = kwargs.get("output_format", "ome-zarr-sharded")

            fake_module = SimpleNamespace(
                StitchingConfig=FakeStitchingConfig,
                StitchingPipeline=FakeStitchingPipeline,
            )
            with patch.dict(
                sys.modules, {"py2flamingo.stitching.pipeline": fake_module}
            ):
                p, n = _single(
                    NodeType.POST_PROCESSING,
                    config={
                        "acquisition_dir": str(acq),
                        "pixel_size_um": 0.5,
                        "channels": "0,1",
                    },
                )
                ctx = ExecutionContext(services={})
                self._runner().run(n, p, ctx)
            self.assertTrue(ctx.get_port_value(n.get_output("completed").id).data)

    def test_input_port_overrides_config_acquisition_dir(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            acq = Path(td) / "from_input"
            acq.mkdir()

            class FakeStitchingPipeline:
                last_acq = None

                def __init__(self, config, cancelled_fn=None):
                    pass

                def run(self, acq_path, output_path, channels=None):
                    type(self).last_acq = Path(acq_path)
                    return Path(output_path)

            class FakeStitchingConfig:
                def __init__(self, **kwargs):
                    self.output_format = kwargs.get("output_format", "ome-zarr-sharded")

            fake_module = SimpleNamespace(
                StitchingConfig=FakeStitchingConfig,
                StitchingPipeline=FakeStitchingPipeline,
            )
            with patch.dict(
                sys.modules, {"py2flamingo.stitching.pipeline": fake_module}
            ):
                p = Pipeline()
                src = create_node(NodeType.SAMPLE_VIEW_DATA, name="Src")
                pp = create_node(
                    NodeType.POST_PROCESSING,
                    name="PP",
                    config={"acquisition_dir": "/wrong/path"},
                )
                p.add_node(src)
                p.add_node(pp)
                # Connect src.config (ANY) → pp.acquisition_dir (FILE_PATH).
                p.add_connection(
                    src.id,
                    src.get_output("config").id,
                    pp.id,
                    pp.get_input("acquisition_dir").id,
                )
                ctx = ExecutionContext(services={})
                ctx.set_port_value(
                    src.get_output("config").id,
                    PortValue(port_type=PortType.ANY, data=str(acq)),
                )
                self._runner().run(pp, p, ctx)
            self.assertEqual(FakeStitchingPipeline.last_acq, acq)

    def test_invalid_channels_string_falls_back_to_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            acq = Path(td) / "acq"
            acq.mkdir()

            class FakeStitchingPipeline:
                last_channels = "sentinel"

                def __init__(self, config, cancelled_fn=None):
                    pass

                def run(self, acq_path, output_path, channels=None):
                    type(self).last_channels = channels
                    return Path(output_path)

            class FakeStitchingConfig:
                def __init__(self, **kwargs):
                    self.output_format = kwargs.get("output_format", "ome-zarr-sharded")

            fake_module = SimpleNamespace(
                StitchingConfig=FakeStitchingConfig,
                StitchingPipeline=FakeStitchingPipeline,
            )
            with patch.dict(
                sys.modules, {"py2flamingo.stitching.pipeline": fake_module}
            ):
                p, n = _single(
                    NodeType.POST_PROCESSING,
                    config={
                        "acquisition_dir": str(acq),
                        "channels": "abc,def",  # not parseable as ints
                    },
                )
                ctx = ExecutionContext(services={})
                self._runner().run(n, p, ctx)
            self.assertIsNone(FakeStitchingPipeline.last_channels)


# ---------------------------------------------------------------------------
# TimedLoopRunner
# ---------------------------------------------------------------------------


def _build_timed_loop_pipeline(*, iterations=2, interval=0.0):
    """TimedLoop with one ExternalCommand body, connected via 'iteration'."""
    from py2flamingo.pipeline.engine.scope_resolver import ScopeResolver

    p = Pipeline()
    tl = create_node(
        NodeType.TIMED_LOOP,
        name="TL",
        config={
            "iterations": iterations,
            "interval_seconds": interval,
            "timing_mode": "sequential",
        },
    )
    body = create_node(NodeType.EXTERNAL_COMMAND, name="Body")
    p.add_node(tl)
    p.add_node(body)
    # iteration (SCALAR) → body.input_data (ANY) — puts body in the loop scope.
    p.add_connection(
        tl.id,
        tl.get_output("iteration").id,
        body.id,
        body.get_input("input_data").id,
    )
    resolver = ScopeResolver(p)
    resolver.resolve()
    return p, tl, body, resolver


class TestTimedLoopRunner(unittest.TestCase):
    def _runner(self):
        return TimedLoopRunner()

    def test_happy_path_runs_n_iterations(self):
        p, tl, body, resolver = _build_timed_loop_pipeline(iterations=3, interval=0.0)
        ctx = ExecutionContext(services={})
        runner = self._runner()
        executor = MockExecutor()
        runner.set_scope_resolver(resolver)
        runner.set_executor(executor)
        runner.run(tl, p, ctx)
        self.assertEqual(len(executor.executed), 3)
        self.assertTrue(ctx.get_port_value(tl.get_output("completed").id).data)

    def test_zero_iterations_indefinite_short_circuits_on_cancel(self):
        # iterations=0 means indefinite; cancel before run to confirm exit.
        p, tl, body, resolver = _build_timed_loop_pipeline(iterations=0, interval=0.0)
        ctx = ExecutionContext(services={})
        ctx.cancel()
        runner = self._runner()
        runner.set_scope_resolver(resolver)
        runner.set_executor(MockExecutor())
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            runner.run(tl, p, ctx)

    def test_missing_executor_raises(self):
        p, tl = _single(
            NodeType.TIMED_LOOP,
            config={"iterations": 1, "interval_seconds": 0.0},
        )
        ctx = ExecutionContext(services={})
        with self.assertRaisesRegex(RuntimeError, "scope_resolver"):
            self._runner().run(tl, p, ctx)

    def test_no_body_completes_immediately(self):
        # Use ScopeResolver on a TimedLoop with no downstream body.
        from py2flamingo.pipeline.engine.scope_resolver import ScopeResolver

        p = Pipeline()
        tl = create_node(
            NodeType.TIMED_LOOP,
            name="TL",
            config={"iterations": 5, "interval_seconds": 0.0},
        )
        p.add_node(tl)
        resolver = ScopeResolver(p)
        resolver.resolve()
        ctx = ExecutionContext(services={})
        runner = self._runner()
        executor = MockExecutor()
        runner.set_scope_resolver(resolver)
        runner.set_executor(executor)
        runner.run(tl, p, ctx)
        # No body → no execute_subgraph calls, completes anyway.
        self.assertEqual(executor.executed, [])
        self.assertTrue(ctx.get_port_value(tl.get_output("completed").id).data)

    def test_cancellation_during_loop_raises(self):
        # The TimedLoop runner checks cancellation on the *parent* context at
        # the top of each iteration. The body's iter_context is a scoped copy
        # whose cancel() doesn't propagate upward, so we cancel the parent
        # directly via a closure.
        p, tl, body, resolver = _build_timed_loop_pipeline(iterations=10, interval=0.0)
        outer_ctx = ExecutionContext(services={})

        class CancellingExecutor(MockExecutor):
            def __init__(self, parent_ctx):
                super().__init__()
                self._parent = parent_ctx

            def execute_subgraph(self, node_ids, context):
                super().execute_subgraph(node_ids, context)
                self._parent.cancel()

        runner = self._runner()
        runner.set_scope_resolver(resolver)
        runner.set_executor(CancellingExecutor(outer_ctx))
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            runner.run(tl, p, outer_ctx)


if __name__ == "__main__":
    unittest.main()
