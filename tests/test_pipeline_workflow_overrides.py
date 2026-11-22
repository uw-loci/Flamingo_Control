"""A pipeline WORKFLOW node's overrides reach the server, or nothing is sent.

Until 2026-11-22 the runner treated the facade's ``load_workflow`` result as a
workflow object, but the live facade (``MVCWorkflowService``) returns the
file's bytes. The position override raised AttributeError and the auto
Z-range and auto-tiling overrides caught it and only logged, so the template
went to the server unchanged. Now the overrides edit the workflow text in
place, every Start/End position is checked against the stage limits, and
these tests read what the synthetic microscope received.

The live facade cannot yet report when a server workflow has finished
(``get_workflow_status`` does not decode the state), so the node refuses to
start a workflow through it; the end-to-end tests here use the real send path
with a status read from the synthetic scope.
"""

from __future__ import annotations

import re
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from py2flamingo.core.command_codes import CameraCommands
from py2flamingo.core.events import EventManager
from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.node_runners.workflow_runner import WorkflowRunner
from py2flamingo.pipeline.engine.workflow_overrides import WorkflowEdit
from py2flamingo.pipeline.models.pipeline import NodeType, Pipeline, create_node
from py2flamingo.services import stage_motion_gate
from py2flamingo.services.workflow_service import MVCWorkflowService
from py2flamingo.testing import synthetic_microscope as synth
from py2flamingo.utils.tile_geometry import compute_tile_geometry
from py2flamingo.utils.workflow_keys import workflow_sections
from py2flamingo.utils.workflow_parser import edit_workflow_text
from tests.pipeline_helpers import FAKE_TEMPLATE
from tests.synthetic_scope_case import SyntheticScopeCase, wait_until

LIMITS = {a: {"min": lo, "max": hi} for a, (lo, hi) in synth.STAGE_LIMITS.items()}

#: 10 planes of 5 um at the synthetic scope's home, with a comment line and
#: a key the workflow writer does not know, both of which must survive.
TEMPLATE = (
    FAKE_TEMPLATE.replace("X (mm) = 5.000000", "X (mm) = 6.000000")
    .replace("Y (mm) = 10.000000", "Y (mm) = 12.000000")
    .replace("Z (mm) = 15.000000", "Z (mm) = 19.000000")
    .replace("Z (mm) = 15.050000", "Z (mm) = 19.050000")
    .replace(
        "    </Experiment Settings>",
        "    # kept as written\n    Custom key = 7\n    </Experiment Settings>",
    )
)

COORDS = {
    "display": {"voxel_size_um": [50.0, 50.0, 50.0]},
    "stage_control": {
        "x_range_mm": [1.0, 12.31],
        "y_range_mm": [5.0, 25.0],
        "z_range_mm": [12.5, 26.0],
        "invert_x_default": False,
    },
    "focus_frame": {"field_of_view_x_mm": 0.52, "field_of_view_y_mm": 0.52},
}


def _object(center=(6.0, 12.0, 19.04), z=(19.0, 19.08), extent=(0.3, 0.3)):
    return SimpleNamespace(
        centroid_stage=center,
        stage_coords_available=True,
        bounding_box=(slice(0, 1),) * 3,
        bounding_box_mm=lambda **kw: {"z_min": z[0], "z_max": z[1]},
        extent_mm=lambda voxel_um: (extent[0], extent[1], z[1] - z[0]),
    )


class _ScopeFacade(MVCWorkflowService):
    """The live workflow service, sending on the real wire path, plus the
    completion report it lacks: read from the synthetic scope."""

    reports_workflow_completion = True

    def __init__(self, connection, scope):
        super().__init__(connection, EventManager())
        self._scope = scope

    def get_workflow_status(self):
        time.sleep(0.05)
        return SimpleNamespace(
            name="RUNNING" if self._scope.workflow_running else "COMPLETED"
        )


class TestOverridesReachTheServer(SyntheticScopeCase):
    def setUp(self):
        super().setUp()
        self.template = Path("template.txt")
        self.template.write_text(TEMPLATE, encoding="utf-8")
        self.runner = WorkflowRunner()
        self.runner.POLL_INTERVAL = 0.05

    def _run(self, config, position=None, z_range=None, services=None, facade=None):
        pipeline = Pipeline()
        node = create_node(
            NodeType.WORKFLOW,
            name="WORKFLOW",
            config={"template_file": str(self.template), **config},
        )
        pipeline.add_node(node)
        inputs = {"position": position, "z_range": z_range}
        self.runner._get_input = lambda n, p, c, name: inputs.get(name)
        ctx = ExecutionContext(
            services={
                "workflow_facade": facade or _ScopeFacade(self.connection, self.scope),
                "coordinate_config": COORDS,
                "stage_limits": LIMITS,
                **(services or {}),
            }
        )
        self.runner.run(node, pipeline, ctx)

    def _sent(self):
        self.assertTrue(wait_until(lambda: len(self.scope.workflows) == 1))
        return self.scope.workflows[-1], workflow_sections(self.scope.workflows[-1])

    @staticmethod
    def _pos(sections, name):
        s = sections[name]
        return tuple(
            float(s[k]) for k in ("X (mm)", "Y (mm)", "Z (mm)", "Angle (degrees)")
        )

    def test_the_position_input_moves_start_and_end_together(self):
        self._run({"use_input_position": True}, position=(6.5, 13.0, 20.0))
        text, sent = self._sent()
        self.assertEqual(self._pos(sent, "Start Position"), (6.5, 13.0, 20.0, 0.0))
        # The template's 50 um Z range is kept.
        self.assertEqual(self._pos(sent, "End Position"), (6.5, 13.0, 20.05, 0.0))
        self.assertIn("# kept as written", text)
        self.assertEqual(sent["Experiment Settings"]["Custom key"], "7")
        self.assertEqual(sent["Stack Settings"]["Number of planes"], "10")

    def test_auto_z_range_sets_the_planes_and_both_ends(self):
        obj = _object(z=(19.0, 19.08))
        self._run({"use_input_position": False, "auto_z_range": True}, z_range=obj)
        _, sent = self._sent()
        # 0.08 mm plus 25 % each end: 18.98 to 19.10, at 5 um.
        self.assertAlmostEqual(self._pos(sent, "Start Position")[2], 18.98)
        self.assertAlmostEqual(self._pos(sent, "End Position")[2], 19.10)
        self.assertEqual(sent["Stack Settings"]["Number of planes"], "24")
        self.assertAlmostEqual(
            float(sent["Stack Settings"]["Change in Z axis (mm)"]), 0.12
        )
        # X and Y stay where the template had them.
        self.assertEqual(self._pos(sent, "Start Position")[:2], (6.0, 12.0))

    def test_auto_tiling_sends_the_grid_it_counted(self):
        obj = _object(center=(6.0, 12.0, 19.0), extent=(2.0, 1.0))
        self._run({"use_input_position": True, "auto_tiling": True}, position=obj)
        _, sent = self._sent()
        stack = sent["Stack Settings"]
        self.assertEqual(stack["Stack option"], "Tile")
        overlap = float(stack["Stack option settings 1"])
        self.assertEqual(overlap, 10.0)
        sx, sy, sz, _ = self._pos(sent, "Start Position")
        ex, ey, ez, _ = self._pos(sent, "End Position")
        geo = compute_tile_geometry(sx, ex, sy, ey, sz, ez, 0.52, 0.52, 10.0, 10.0)
        self.assertEqual((geo.tiles_x, geo.tiles_y), (7, 4))
        self.assertAlmostEqual((sx + ex) / 2, 6.0)
        self.assertAlmostEqual((sy + ey) / 2, 12.0)
        # The template's Z range moves with the object's Z.
        self.assertAlmostEqual(ez - sz, 0.05)

    def test_a_small_object_gets_no_tiles(self):
        obj = _object(extent=(0.3, 0.3))
        self._run({"use_input_position": True, "auto_tiling": True}, position=obj)
        _, sent = self._sent()
        self.assertEqual(sent["Stack Settings"]["Stack option"], "ZStack")
        start, end = self._pos(sent, "Start Position"), self._pos(sent, "End Position")
        self.assertEqual(start[:2], end[:2])

    def test_no_position_input_sends_the_template_unchanged(self):
        self._run({"use_input_position": True})
        text, _ = self._sent()
        # Only the exposure key is rewritten on send, for the server build.
        self.assertIn("    # kept as written\n    Custom key = 7\n", text)
        self.assertIn("    Z (mm) = 19.050000\n", text)

    # ---------------------------------------------------------- refusals

    def _refused(self, pattern, **kwargs):
        with self.assertRaisesRegex(RuntimeError, pattern):
            self._run(**kwargs)
        time.sleep(0.2)
        self.assertEqual(
            self.scope.received(CameraCommands.WORKFLOW_START),
            [],
            "a workflow was sent",
        )

    def test_a_position_outside_the_limits_is_refused(self):
        self._refused(
            "outside its limits",
            config={"use_input_position": True},
            position=(50.0, 12.0, 19.0),
        )

    def test_a_tile_grid_outside_the_limits_is_refused(self):
        obj = _object(center=(1.5, 12.0, 19.0), extent=(2.0, 1.0))
        self._refused(
            "Start Position x",
            config={"use_input_position": True, "auto_tiling": True},
            position=obj,
        )

    def test_unknown_limits_are_refused(self):
        self._refused(
            "stage limits are unknown",
            config={"use_input_position": True},
            position=(6.5, 13.0, 20.0),
            services={"stage_limits": None},
        )

    def test_the_template_position_is_checked_too(self):
        # A pipeline template's placeholder origin is a real move to (0, 0, 0).
        origin = re.sub(r"([XYZ] \(mm\)) = [0-9.]+", r"\1 = 0.000000", TEMPLATE)
        self.template.write_text(origin, encoding="utf-8")
        self._refused("outside its limits", config={"use_input_position": True})

    def test_auto_z_without_an_object_is_refused(self):
        self._refused(
            "Auto Z-Range is on but no object",
            config={"use_input_position": True, "auto_z_range": True},
        )

    def test_an_object_without_stage_coordinates_is_refused(self):
        obj = _object()
        obj.stage_coords_available = False
        self._refused(
            "no stage coordinates",
            config={"use_input_position": True},
            position=obj,
        )

    def test_the_emergency_stop_refuses_it(self):
        stage_motion_gate.latch_emergency_stop()
        self.addCleanup(stage_motion_gate.clear_emergency_stop)
        self._refused(
            "emergency stop active",
            config={"use_input_position": True},
            position=(6.5, 13.0, 20.0),
        )

    def test_the_live_service_is_refused_before_anything_is_sent(self):
        # MVCWorkflowService cannot tell when the workflow has finished.
        self._refused(
            "cannot report when a server workflow has finished",
            config={"use_input_position": True},
            position=(6.5, 13.0, 20.0),
            facade=MVCWorkflowService(self.connection, EventManager()),
        )


class TestTheEditor(unittest.TestCase):
    """``edit_workflow_text`` changes only what it is asked to."""

    def test_every_other_byte_is_kept(self):
        text = TEMPLATE.replace("\n", "\r\n")
        out = edit_workflow_text(text, {"Start Position": {"X (mm)": "7.000000"}})
        self.assertEqual(out, text.replace("X (mm) = 6.000000", "X (mm) = 7.000000", 1))

    def test_a_missing_key_is_added_to_its_section(self):
        text = TEMPLATE.replace("    Stack option settings 2 = 0\n", "")
        out = edit_workflow_text(
            text, {"Stack Settings": {"Stack option settings 2": "10"}}
        )
        self.assertEqual(
            workflow_sections(out)["Stack Settings"]["Stack option settings 2"], "10"
        )

    def test_a_missing_section_is_an_error(self):
        from py2flamingo.core.errors import WorkflowError

        text = TEMPLATE.split("    <End Position>")[0] + "</Workflow Settings>\n"
        with self.assertRaises(WorkflowError):
            edit_workflow_text(text, {"End Position": {"X (mm)": "1.0"}})

    def test_a_template_without_an_end_position_is_refused(self):
        text = TEMPLATE.split("    <End Position>")[0] + "</Workflow Settings>\n"
        with self.assertRaisesRegex(RuntimeError, "End Position"):
            WorkflowEdit.from_bytes(text.encode())
