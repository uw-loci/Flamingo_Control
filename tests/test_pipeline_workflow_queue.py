"""A pipeline WORKFLOW node runs on the microscope through the workflow queue.

Until 2026-11-24 the node refused to start a workflow on the microscope: the
live workflow service cannot report when a server workflow has finished. Now,
when the application's ``WorkflowQueueService`` is in the context, the node
hands its edited, limit-checked workflow text to the queue as a one-item run
and blocks until the queue reports the item succeeded or failed. The queue
already confirms the start (server busy) and the end (SYSTEM_STATE_IDLE or
the state poll), sends WORKFLOW_STOP on a timeout, and is canceled by the
emergency stop. Headless runs have no queue and keep the facade path.

These tests run the real queue, the real ``WorkflowController`` and
``MVCWorkflowService`` against the synthetic microscope.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_pipeline_workflow_queue.py -q
"""

from __future__ import annotations

import threading
import unittest
from pathlib import Path

from py2flamingo.controllers.workflow_controller import WorkflowController
from py2flamingo.core.command_codes import CameraCommands, StageCommands
from py2flamingo.core.events import EventManager
from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.executor import PipelineExecutor
from py2flamingo.pipeline.engine.node_runners.workflow_runner import WorkflowRunner
from py2flamingo.pipeline.models.pipeline import NodeType, Pipeline, create_node
from py2flamingo.services import stage_motion_gate
from py2flamingo.services.workflow_service import MVCWorkflowService
from py2flamingo.testing import synthetic_microscope as synth
from py2flamingo.utils.workflow_keys import workflow_sections
from tests.synthetic_scope_case import SyntheticScopeCase, wait_until
from tests.test_workflow_start_gate import TEMPLATE, fast_queue

START = CameraCommands.WORKFLOW_START
STOP = CameraCommands.WORKFLOW_STOP

LIMITS = {a: {"min": lo, "max": hi} for a, (lo, hi) in synth.STAGE_LIMITS.items()}

#: The synthetic scope saves 4 x LIVE_FPS planes a second: about 40 minutes.
LONG = TEMPLATE.replace("Number of planes = 10", "Number of planes = 100000")


class _Lock:
    """Stands in for the application's acquisition lock."""

    def __init__(self, free=True):
        self.free = free
        self.calls = []

    def acquire(self, name):
        self.calls.append(("acquire", name))
        return self.free

    def release(self, name):
        self.calls.append(("release", name))


class PipelineQueueCase(SyntheticScopeCase):
    def setUp(self):
        super().setUp()
        self.template = Path("template.txt")
        self.template.write_text(TEMPLATE, encoding="utf-8")
        self.facade = MVCWorkflowService(self.connection, EventManager())
        self.controller = WorkflowController(
            self.facade, self.connection.model, connection_service=self.connection
        )
        self.queue = fast_queue(self.controller, self.connection)
        # Each start: was the scope already running a workflow?
        self.busy_at_start = []
        original = self.scope._on_workflow_start

        def record(client, code, params, value, additional):
            self.busy_at_start.append(self.scope.workflow_running)
            original(client, code, params, value, additional)

        # The scope builds its handler table per command, from these names.
        self.scope._on_workflow_start = record
        self.lock = _Lock()

    def services(self, **extra):
        return {
            "workflow_facade": self.facade,
            "workflow_queue_service": self.queue,
            "stage_limits": LIMITS,
            "acquisition_lock": self.lock,
            **extra,
        }

    def node(self, name="WORKFLOW", **config):
        return create_node(
            NodeType.WORKFLOW,
            name=name,
            config={"template_file": str(self.template), **config},
        )

    def run_node(self, position=None, context=None, **config):
        pipeline = Pipeline()
        node = self.node(**config)
        pipeline.add_node(node)
        runner = WorkflowRunner()
        runner._get_input = lambda n, p, c, name: (
            position if name == "position" else None
        )
        ctx = context or ExecutionContext(services=self.services())
        runner.run(node, pipeline, ctx)
        return node, ctx

    def starts(self):
        return self.scope.received(START)


class TestOneNode(PipelineQueueCase):
    def test_runs_and_completes_when_the_workflow_completes(self):
        node, ctx = self.run_node(position=(6.5, 13.0, 20.0), use_input_position=True)
        # Exactly one start, carrying the overridden position.
        self.assertEqual(len(self.starts()), 1)
        sent = workflow_sections(self.scope.workflows[-1])
        self.assertEqual(float(sent["Start Position"]["X (mm)"]), 6.5)
        self.assertEqual(float(sent["End Position"]["Z (mm)"]), 20.05)
        # The node returned because the server finished, not before.
        self.assertFalse(self.scope.workflow_running)
        completed = node.get_output("completed")
        self.assertTrue(ctx.get_port_value(completed.id).data)
        # No return-to-origin between pipeline nodes: no move was sent.
        self.assertEqual(self.scope.received(StageCommands.POSITION_SET), [])
        # The acquisition lock was held around the run, and released.
        self.assertEqual([c[0] for c in self.lock.calls], ["acquire", "release"])
        self.assertFalse(self.queue.is_running)

    def test_the_emergency_stop_fails_it_with_nothing_sent(self):
        stage_motion_gate.latch_emergency_stop()
        self.addCleanup(stage_motion_gate.clear_emergency_stop)
        with self.assertRaisesRegex(Exception, "emergency stop active"):
            self.run_node()
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.lock.calls, [])

    def test_a_busy_queue_fails_it_with_nothing_sent(self):
        self.queue._is_running = True  # e.g. a Tile Collection is running
        self.addCleanup(setattr, self.queue, "_is_running", False)
        with self.assertRaisesRegex(Exception, "already running"):
            self.run_node()
        self.assertEqual(self.starts(), [])

    def test_a_held_acquisition_lock_fails_it_with_nothing_sent(self):
        self.lock.free = False
        with self.assertRaisesRegex(Exception, "another acquisition"):
            self.run_node()
        self.assertEqual(self.starts(), [])

    def test_a_timeout_fails_it_and_stops_the_workflow(self):
        self.template.write_text(LONG, encoding="utf-8")
        self.queue.MAX_WORKFLOW_TIMEOUT = 1.0
        with self.assertRaisesRegex(Exception, "Timeout after"):
            self.run_node()
        self.assertEqual(len(self.starts()), 1)
        self.assertTrue(self.scope.received(STOP))
        self.assertTrue(wait_until(lambda: not self.scope.workflow_running))
        self.assertEqual([c[0] for c in self.lock.calls], ["acquire", "release"])

    def test_cancel_stops_it(self):
        self.template.write_text(LONG, encoding="utf-8")
        ctx = ExecutionContext(services=self.services())
        canceler = threading.Thread(
            target=lambda: wait_until(lambda: self.scope.workflow_running, 10)
            and ctx.cancel(),
            daemon=True,
        )
        canceler.start()
        with self.assertRaisesRegex(Exception, "[Cc]anceled"):
            self.run_node(context=ctx)
        self.assertEqual(len(self.starts()), 1)
        self.assertTrue(self.scope.received(STOP))
        self.assertTrue(wait_until(lambda: not self.scope.workflow_running))
        self.assertFalse(self.queue.is_running)


class TestTwoNodes(PipelineQueueCase):
    def test_the_second_node_starts_after_the_first_finished(self):
        pipeline = Pipeline()
        first = self.node("first")
        second = self.node("second")
        pipeline.add_node(first)
        pipeline.add_node(second)
        pipeline.add_connection(
            first.id,
            first.get_output("completed").id,
            second.id,
            second.get_input("trigger").id,
        )
        ctx = ExecutionContext(services=self.services())
        executor = PipelineExecutor(
            pipeline, ctx, {NodeType.WORKFLOW: WorkflowRunner()}
        )
        errors = []
        executor.node_error.connect(lambda nid, msg: errors.append(msg))
        executor.run()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.starts()), 2)
        # The scope was idle at each start: nothing was interleaved.
        self.assertEqual(self.busy_at_start, [False, False])
        self.assertFalse(self.scope.workflow_running)


class TestHeadlessPathUnchanged(PipelineQueueCase):
    def test_without_a_queue_the_live_service_still_refuses(self):
        services = self.services()
        del services["workflow_queue_service"]
        ctx = ExecutionContext(services=services)
        with self.assertRaisesRegex(Exception, "no workflow queue in this run"):
            self.run_node(context=ctx)
        self.assertEqual(self.starts(), [])


class TestGuiThreadAcquisitionLock(unittest.TestCase):
    """The pipeline controller's lock runs start/stop_acquisition on the GUI
    thread, whichever thread asks."""

    def setUp(self):
        from PyQt5.QtWidgets import QApplication

        # A QApplication, not a bare QCoreApplication: a later test that needs
        # widgets could not create one once a QCoreApplication exists.
        self.qapp = QApplication.instance() or QApplication([])

    def _app(self):
        calls = []

        class App:
            busy = False

            def start_acquisition(self, name):
                calls.append(("start", name, threading.current_thread().name))
                if App.busy:
                    return False
                App.busy = True
                return True

            def stop_acquisition(self, name):
                calls.append(("stop", name, threading.current_thread().name))
                App.busy = False

        return App(), calls

    def test_from_the_gui_thread(self):
        from py2flamingo.pipeline.controllers.pipeline_controller import (
            GuiThreadAcquisitionLock,
        )

        app, calls = self._app()
        lock = GuiThreadAcquisitionLock(app)
        self.assertTrue(lock.acquire("a"))
        self.assertFalse(lock.acquire("b"))
        lock.release("a")
        self.assertEqual([c[0] for c in calls], ["start", "start", "stop"])

    def test_from_a_pipeline_thread(self):
        from PyQt5.QtCore import QCoreApplication

        from py2flamingo.pipeline.controllers.pipeline_controller import (
            GuiThreadAcquisitionLock,
        )

        app, calls = self._app()
        lock = GuiThreadAcquisitionLock(app)
        result = {}

        def worker():
            result["got"] = lock.acquire("node")
            lock.release("node")

        t = threading.Thread(target=worker, name="pipeline-thread")
        t.start()
        while t.is_alive():
            QCoreApplication.processEvents()
            t.join(0.01)
        self.assertTrue(result["got"])
        main = threading.main_thread().name
        self.assertEqual(calls, [("start", "node", main), ("stop", "node", main)])
