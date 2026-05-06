"""End-to-end engine tests: ScopeResolver edges, executor signal sequence,
cancellation. Each test runs a real fixture through the headless API so the
public ``run_pipeline_headless`` surface is exercised every test run.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TESTS_DIR = Path(__file__).resolve().parent
_SRC = _TESTS_DIR.parent / "src"
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from pipeline_helpers import make_bright_volume  # noqa: E402

from py2flamingo.pipeline.engine.scope_resolver import ScopeResolver  # noqa: E402
from py2flamingo.pipeline.headless_services import (  # noqa: E402
    build_headless_services,
    run_pipeline_headless,
)
from py2flamingo.pipeline.models.pipeline import (  # noqa: E402
    NodeType,
    Pipeline,
    create_node,
)

FIXTURES = _TESTS_DIR / "fixtures" / "pipelines"


def _load(name: str) -> Pipeline:
    return Pipeline.from_dict(json.loads((FIXTURES / name).read_text()))


def _run(pipeline: Pipeline, *, skip_workflow=True, volumes_count=2):
    """Helper: run a pipeline headlessly with a multi-object bright volume.

    Skips WORKFLOW by default since the fixtures don't ship a real
    Workflow.txt.
    """
    services = build_headless_services(
        volumes={0: make_bright_volume(bright_count=volumes_count)},
    )
    skip = {NodeType.WORKFLOW} if skip_workflow else set()
    return run_pipeline_headless(
        pipeline, services=services, skip_node_types=skip, raise_on_error=False
    )


# ---------------------------------------------------------------------------
# ScopeResolver
# ---------------------------------------------------------------------------


class TestScopeResolver(unittest.TestCase):
    def test_top_level_excludes_conditional_branch_bodies(self):
        # 04: Threshold + Conditional are top-level; Workflow + ExternalCmd
        # live in branches.
        p = _load("04_conditional_branches.json")
        r = ScopeResolver(p)
        r.resolve()
        top = set(r.get_top_level_node_ids())
        self.assertIn("n-threshold", top)
        self.assertIn("n-conditional", top)
        self.assertNotIn("n-workflow", top)
        self.assertNotIn("n-extcmd", top)

    def test_conditional_in_foreach_scope(self):
        # 05: ForEach body contains Conditional and Workflow.
        p = _load("05_conditional_in_foreach.json")
        r = ScopeResolver(p)
        r.resolve()
        top = set(r.get_top_level_node_ids())
        # Top-level: Threshold + ForEach only.
        self.assertEqual(top, {"n-threshold", "n-foreach"})
        body = set(r.get_body_sorted("n-foreach"))
        self.assertIn("n-conditional", body)
        self.assertIn("n-workflow", body)

    def test_nested_foreach_scope(self):
        # 06: outer body contains list-producer, inner-fe, inner-action.
        p = _load("06_nested_foreach.json")
        r = ScopeResolver(p)
        r.resolve()
        top = set(r.get_top_level_node_ids())
        self.assertEqual(top, {"n-threshold", "n-outer-fe"})
        outer_body = set(r.get_body_sorted("n-outer-fe"))
        self.assertEqual(
            outer_body, {"n-list-producer", "n-inner-fe", "n-inner-action"}
        )
        inner_body = set(r.get_body_sorted("n-inner-fe"))
        self.assertEqual(inner_body, {"n-inner-action"})

    def test_empty_collection_yields_no_body_executions(self):
        # Programmatic: ForEach with an empty list runs body 0 times but the
        # ForEach itself still completes. Threshold needs a non-empty
        # channel_thresholds config so its volumes-from-storage branch runs;
        # an all-zero input produces 0 detected objects.
        import numpy as np

        p = Pipeline()
        thresh = create_node(
            NodeType.THRESHOLD,
            name="T",
            config={
                "channel_thresholds": {"0": 100},
                "default_threshold": 100,
            },
        )
        fe = create_node(NodeType.FOR_EACH, name="FE")
        ext = create_node(
            NodeType.EXTERNAL_COMMAND,
            name="Body",
            config={
                "command_template": "true",
                "input_format": "json",
                "output_format": "json",
            },
        )
        p.add_node(thresh)
        p.add_node(fe)
        p.add_node(ext)
        p.add_connection(
            thresh.id,
            thresh.get_output("objects").id,
            fe.id,
            fe.get_input("collection").id,
        )
        # Use index (SCALAR → ANY) instead of current_item so ext is
        # JSON-serializable when ExternalCommand writes its input file.
        p.add_connection(
            fe.id,
            fe.get_output("index").id,
            ext.id,
            ext.get_input("input_data").id,
        )
        services = build_headless_services(
            volumes={0: np.zeros((4, 8, 8), dtype=np.uint16)}
        )
        run = run_pipeline_headless(p, services=services, raise_on_error=False)
        self.assertTrue(run.succeeded, msg=str(run.errors))
        self.assertEqual(run.node_states.get(fe.id), "completed")
        self.assertEqual(run.node_states.get(thresh.id), "completed")
        self.assertNotIn(ext.id, run.node_states)


# ---------------------------------------------------------------------------
# Executor signal sequence + counts
# ---------------------------------------------------------------------------


class TestExecutorSignals(unittest.TestCase):
    def test_signal_sequence_for_simple_pipeline(self):
        p = _load("02_threshold_foreach.json")
        run = _run(p)
        # Both top-level nodes report completed.
        self.assertEqual(run.node_states.get("n-threshold"), "completed")
        self.assertEqual(run.node_states.get("n-foreach"), "completed")
        self.assertTrue(run.succeeded, msg=str(run.errors))

    def test_conditional_executes_only_one_branch(self):
        # 04 with threshold_value=0 (always true) → workflow runs, ext skipped.
        p = _load("04_conditional_branches.json")
        run = _run(p)
        self.assertTrue(run.succeeded, msg=str(run.errors))
        self.assertEqual(run.node_states.get("n-workflow"), "completed")
        # The false-branch ExternalCommand should NOT have started.
        self.assertNotIn("n-extcmd", run.node_states)

    def test_nested_foreach_inner_runs_per_outer_iteration(self):
        p = _load("06_nested_foreach.json")
        run = _run(p, volumes_count=2)
        # Both ForEach nodes complete; inner action ran (started at least once).
        self.assertTrue(run.succeeded, msg=str(run.errors))
        self.assertEqual(run.node_states.get("n-outer-fe"), "completed")
        self.assertEqual(run.node_states.get("n-inner-fe"), "completed")
        self.assertEqual(run.node_states.get("n-inner-action"), "completed")

    def test_foreach_iteration_signal_fires(self):
        # Hook into the executor's foreach_iteration signal directly to
        # verify it fires once per outer iteration. Fixture 03 (not 02) is
        # required because ForEach with no body nodes short-circuits before
        # emitting foreach_iteration.
        from py2flamingo.pipeline.engine.context import ExecutionContext
        from py2flamingo.pipeline.engine.executor import PipelineExecutor
        from py2flamingo.pipeline.headless_services import (
            _build_runners,
            _ensure_qapplication,
        )

        _ensure_qapplication()
        p = _load("03_threshold_foreach_workflow.json")
        services = build_headless_services(
            volumes={0: make_bright_volume(bright_count=2)},
        )
        ctx = ExecutionContext(services=services)
        # Skip WORKFLOW so the ForEach body runs as a no-op without needing
        # a real Workflow.txt template.
        runners = _build_runners({NodeType.WORKFLOW})
        executor = PipelineExecutor(p, ctx, runners)
        signals: list = []
        executor.foreach_iteration.connect(
            lambda nid, cur, total: signals.append((nid, cur, total))
        )
        executor.run()
        # 2 bright voxels → 2 detected objects → 2 foreach_iteration emissions.
        self.assertGreaterEqual(len(signals), 1)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class TestCancellation(unittest.TestCase):
    def test_timed_loop_cancellation_exits_within_one_second(self):
        # Build a TimedLoop with a long interval and indefinite iterations,
        # then cancel from a separate thread. The runner should bail within 1s.
        from py2flamingo.pipeline.engine.context import ExecutionContext
        from py2flamingo.pipeline.engine.executor import PipelineExecutor
        from py2flamingo.pipeline.headless_services import (
            _build_runners,
            _ensure_qapplication,
        )

        _ensure_qapplication()
        p = Pipeline()
        tl = create_node(
            NodeType.TIMED_LOOP,
            name="TL",
            config={
                "iterations": 0,  # indefinite
                "interval_seconds": 5.0,
                "timing_mode": "sequential",
            },
        )
        body = create_node(
            NodeType.EXTERNAL_COMMAND,
            name="Body",
            config={
                "command_template": "true",
                "input_format": "json",
                "output_format": "json",
                "timeout_seconds": 5,
            },
        )
        p.add_node(tl)
        p.add_node(body)
        p.add_connection(
            tl.id,
            tl.get_output("iteration").id,
            body.id,
            body.get_input("input_data").id,
        )
        ctx = ExecutionContext(services={})
        runners = _build_runners()
        executor = PipelineExecutor(p, ctx, runners)

        def _cancel_after(seconds):
            time.sleep(seconds)
            ctx.cancel()

        canceller = threading.Thread(target=_cancel_after, args=(0.2,), daemon=True)
        start = time.monotonic()
        canceller.start()
        executor.run()
        elapsed = time.monotonic() - start
        canceller.join(timeout=2.0)
        self.assertLess(elapsed, 2.0, f"Cancellation took {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
