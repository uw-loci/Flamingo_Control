"""A server workflow is not started while the stage must not move.

Until 2026-11-24 the emergency-stop latch (``stage_motion_gate``) was obeyed
by both stage-move funnels, but nothing checked it before WORKFLOW_START
(0x3004), and a server workflow drives the stage. The Workflow tab, the
workflow queue (Tile Collection) and a pipeline node could all start one right
after an emergency stop. The same held for a scope whose stage limits are
unknown (``set_motion_blocked``).

The check is now in the one command-socket writer
(``MVCConnectionService.send_raw_bytes``), with an earlier, exact refusal in
the workflow send path, the Workflow-tab controller and the queue. These tests
read what the synthetic microscope received.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_workflow_start_gate.py -q
"""

from __future__ import annotations

import struct
import threading
import unittest
from pathlib import Path

from PyQt5.QtCore import Qt

from py2flamingo.controllers.workflow_controller import WorkflowController
from py2flamingo.core.command_codes import CameraCommands, StageCommands
from py2flamingo.core.errors import WorkflowStartRefusedError
from py2flamingo.core.events import EventManager
from py2flamingo.core.frames import FRAME_SIZE, START_MARKER, command_codes_in
from py2flamingo.services import stage_motion_gate
from py2flamingo.services.workflow_queue_service import WorkflowQueueService
from py2flamingo.services.workflow_service import MVCWorkflowService
from py2flamingo.utils.workflow_keys import workflow_sections
from tests.pipeline_helpers import FAKE_TEMPLATE
from tests.synthetic_scope_case import SyntheticScopeCase, wait_until

START = CameraCommands.WORKFLOW_START

#: 10 planes at the synthetic scope's home position (inside its limits).
TEMPLATE = (
    FAKE_TEMPLATE.replace("X (mm) = 5.000000", "X (mm) = 6.000000")
    .replace("Y (mm) = 10.000000", "Y (mm) = 12.000000")
    .replace("Z (mm) = 15.000000", "Z (mm) = 19.000000")
    .replace("Z (mm) = 15.050000", "Z (mm) = 19.050000")
)


def ui_dict(text: str) -> dict:
    """Sections as the Workflow tab hands them over: numbers as numbers."""

    def num(v):
        try:
            f = float(v)
        except ValueError:
            return v
        return int(f) if f.is_integer() and "." not in v else f

    return {
        sec: {k: num(v) for k, v in vals.items()}
        for sec, vals in workflow_sections(text).items()
    }


def fast_queue(controller, connection) -> WorkflowQueueService:
    """The real queue with its fixed pauses shortened for the tests."""
    q = WorkflowQueueService(controller, connection_service=connection)
    q.POST_WORKFLOW_SEND_DELAY = 0.0
    q.WORKFLOW_START_POLL_INTERVAL = 0.05
    q.MIN_INTER_WORKFLOW_DELAY = 0.0
    q.STATE_POLL_INTERVAL = 0.2
    q.STOP_IDLE_POLL_INTERVAL = 0.1
    return q


class _Latched:
    """Mixin: each test runs once latched (e-stop) and once blocked."""

    def latch(self):
        stage_motion_gate.latch_emergency_stop()
        self.addCleanup(stage_motion_gate.clear_emergency_stop)

    def block(self):
        stage_motion_gate.set_motion_blocked("limits unknown (test)")
        self.addCleanup(stage_motion_gate.set_motion_blocked, None)


class TestEveryEntryPoint(_Latched, SyntheticScopeCase):
    def setUp(self):
        super().setUp()
        self.wf_file = Path("wf.txt")
        self.wf_file.write_text(TEMPLATE, encoding="utf-8")
        self.service = MVCWorkflowService(self.connection, EventManager())
        self.controller = WorkflowController(
            self.service, self.connection.model, connection_service=self.connection
        )

    def starts(self):
        return self.scope.received(START)

    # ------------------------------------------------------------ Workflow tab

    def test_workflow_tab_refused_while_latched(self):
        self.latch()
        ok, msg = self.controller.start_workflow_from_ui(
            None, ui_dict(TEMPLATE), save_to_disk=False
        )
        self.assertFalse(ok)
        self.assertIn("emergency stop active", msg)
        self.assertEqual(self.starts(), [])

    def test_workflow_tab_refused_while_limits_unknown(self):
        self.block()
        ok, msg = self.controller.start_workflow_from_ui(
            None, ui_dict(TEMPLATE), save_to_disk=False
        )
        self.assertFalse(ok)
        self.assertIn("limits unknown (test)", msg)
        self.assertEqual(self.starts(), [])

    def test_workflow_tab_sends_once_cleared(self):
        ok, msg = self.controller.start_workflow_from_ui(
            None, ui_dict(TEMPLATE), save_to_disk=False
        )
        self.assertTrue(ok, msg)
        self.assertTrue(wait_until(lambda: len(self.starts()) == 1))

    def test_loaded_file_refused_while_latched_then_sent(self):
        self.assertTrue(self.controller.load_workflow(str(self.wf_file))[0])
        self.latch()
        ok, msg = self.controller.start_workflow()
        self.assertFalse(ok)
        self.assertIn("emergency stop active", msg)
        self.assertEqual(self.starts(), [])
        stage_motion_gate.clear_emergency_stop()
        ok, msg = self.controller.start_workflow()
        self.assertTrue(ok, msg)
        self.assertTrue(wait_until(lambda: len(self.starts()) == 1))

    # ----------------------------------------------------- lower send paths

    def test_the_workflow_service_raises(self):
        self.latch()
        with self.assertRaises(WorkflowStartRefusedError) as cm:
            self.service.start_workflow(TEMPLATE.encode())
        self.assertIn("emergency stop active", cm.exception.message)
        self.assertEqual(self.starts(), [])

    def test_a_raw_frame_is_refused_by_the_writer(self):
        frame = self.connection.encoder.encode_command(
            code=START,
            status=0,
            params=[0, 0, 0, 0, 0, 0, 0x80000000],
            value=0.0,
            data=b"",
            additional_data_size=len(TEMPLATE),
        )
        for setup in (self.latch, self.block):
            with self.subTest(setup=setup.__name__):
                setup()
                with self.assertRaises(WorkflowStartRefusedError):
                    self.connection.send_raw_bytes(frame + TEMPLATE.encode())
                with self.assertRaises(WorkflowStartRefusedError):
                    self.connection.send_command_async(frame, START, timeout=0.1)
                stage_motion_gate.clear_emergency_stop()
                stage_motion_gate.set_motion_blocked(None)
        self.assertEqual(self.starts(), [])
        self.connection.send_raw_bytes(frame + TEMPLATE.encode())
        self.assertTrue(wait_until(lambda: len(self.starts()) == 1))

    def test_other_commands_still_go_out_while_latched(self):
        # Queries, and the stops the emergency stop itself sends, are not
        # workflow starts.
        self.latch()
        self.assertIsNotNone(self.connection.query_system_state())
        from py2flamingo.services import safe_state

        self.assertTrue(safe_state.stop_workflow(self.connection))
        self.assertTrue(
            wait_until(lambda: self.scope.received(CameraCommands.WORKFLOW_STOP))
        )

    def test_verification_t4_is_refused_too(self):
        from py2flamingo.verification.context import RunContext
        from py2flamingo.verification.model import Failed

        ctx = RunContext.__new__(RunContext)
        ctx.connection = self.connection
        ctx.abort = threading.Event()
        ctx.tap = type("Tap", (), {"mark": lambda self: 0})()
        ctx.code = lambda name: START
        self.latch()
        with self.assertRaises(Failed) as cm:
            ctx.exchange("CAMERA_WORK_FLOW_START", additional=b"x", reply=None)
        self.assertIn("emergency stop active", str(cm.exception))
        self.assertEqual(self.starts(), [])

    # ------------------------------------------------------- queue (Tile Coll.)

    def _queue_run(self, n=2):
        files = []
        for i in range(n):
            f = Path(f"tile{i}.txt")
            f.write_text(TEMPLATE, encoding="utf-8")
            files.append(f)
        q = fast_queue(self.controller, self.connection)
        seen = {"failed": [], "cancelled": 0, "completed": 0, "summary": []}
        # Direct: the queue emits from its own thread and no event loop runs.
        direct = Qt.DirectConnection
        q.queue_failed.connect(seen["failed"].append, direct)
        q.queue_cancelled.connect(lambda: seen.__setitem__("cancelled", 1), direct)
        q.queue_completed.connect(lambda: seen.__setitem__("completed", 1), direct)
        q.queue_summary.connect(lambda a, b: seen["summary"].append((a, b)), direct)
        q.enqueue(files)
        self.assertTrue(q.start())
        q._execution_thread.join(timeout=20)
        self.assertFalse(q._execution_thread.is_alive())
        return q, seen

    def test_queue_stops_instead_of_skipping_while_latched(self):
        self.latch()
        q, seen = self._queue_run(n=3)
        self.assertEqual(self.starts(), [])
        # Stopped at the first item; the others were not tried.
        self.assertEqual([bool(i.started) for i in q._queue], [True, False, False])
        self.assertEqual(seen["cancelled"], 1)
        self.assertEqual(seen["completed"], 0)
        self.assertEqual(len(seen["failed"]), 1)
        self.assertIn("emergency stop active", seen["failed"][0])
        self.assertIn("Workflows 2-3 were not sent", seen["failed"][0])
        # No return-to-origin move either.
        self.assertEqual(self.scope.received(StageCommands.POSITION_SET), [])

    def test_queue_stops_while_limits_unknown(self):
        self.block()
        q, seen = self._queue_run(n=2)
        self.assertEqual(self.starts(), [])
        self.assertEqual(len(seen["failed"]), 1)
        self.assertIn("limits unknown (test)", seen["failed"][0])

    def test_queue_runs_once_cleared(self):
        q, seen = self._queue_run(n=2)
        self.assertEqual(len(self.starts()), 2)
        self.assertEqual(seen["completed"], 1)
        self.assertEqual(seen["summary"], [(2, 0)])


class TestFrameScanner(unittest.TestCase):
    @staticmethod
    def frame(code, extra=0):
        buf = bytearray(FRAME_SIZE)
        struct.pack_into("<II", buf, 0, START_MARKER, code)
        struct.pack_into("<I", buf, 48, extra)
        return bytes(buf)

    def test_codes_of_frames_and_payloads(self):
        payload = self.frame(0x3005)  # a payload that looks like a frame
        data = (
            self.frame(0x6004)
            + self.frame(0x1234, len(payload))
            + payload
            + self.frame(START)
        )
        # The payload is skipped by its length, never read as a frame.
        self.assertEqual(command_codes_in(data), [0x6004, 0x1234, START])

    def test_not_a_frame(self):
        self.assertEqual(command_codes_in(b"\x00" * 256), [])
        self.assertEqual(command_codes_in(b""), [])


class TestStackCompleteConfirmsTheRun(unittest.TestCase):
    """The queue's STACK_COMPLETE handler read ``message.double_data``, which
    ParsedMessage has never had (the field is ``value``), so it raised on
    every stack and never confirmed the run or kept its statistics."""

    def test_stack_complete_is_read(self):
        from py2flamingo.core.frames import parse_frame
        from py2flamingo.core.tcp_protocol import ProtocolEncoder

        frame = ProtocolEncoder().encode_command(
            code=0x3014,
            status=1,
            params=[0, 0, 0, 10, 10, 0, 0],
            value=1234.0,
            data=b"",
        )
        q = WorkflowQueueService(object(), connection_service=None)
        q._on_stack_complete(parse_frame(frame))
        self.assertTrue(q._workflow_running)
        self.assertEqual(q._completion_data["images_acquired"], 10)
        self.assertEqual(q._completion_data["acquisition_time_us"], 1234.0)
