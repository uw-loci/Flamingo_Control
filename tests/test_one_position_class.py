"""One stage Position class, and a non-finite target never reaches the stage.

``models/hardware/stage.Position`` (a ValidatedModel with defaults, a name and
a NaN check at construction) lived next to ``models/microscope.Position`` (the
one the controllers, services and views pass around). The workflow models and
the Sample View click-to-move built the hardware one. It is gone; its NaN check
moved to ``StageService.move_to_position``, the one move path, because NaN
passes every min/max clamp unchanged.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

from py2flamingo.core.command_codes import StageCommands
from py2flamingo.services import stage_command_dedup
from py2flamingo.services.stage_service import StageService
from tests.synthetic_scope_case import SyntheticScopeCase

SRC = Path(__file__).resolve().parents[1] / "src" / "py2flamingo"


def test_exactly_one_position_class():
    found = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found += [
            str(path.relative_to(SRC))
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "Position"
        ]
    assert found == ["models/microscope.py"], found


def test_the_workflow_models_use_it():
    from py2flamingo.models.data.workflow import Workflow
    from py2flamingo.models.microscope import Position

    assert type(Workflow().start_position) is Position


class TestNonFiniteTargets(SyntheticScopeCase):
    def setUp(self):
        super().setUp()
        stage_command_dedup.reset()
        self.addCleanup(stage_command_dedup.reset)

    def test_nan_and_inf_are_refused_with_nothing_sent(self):
        service = StageService(self.connection)
        for bad in (math.nan, math.inf, -math.inf):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    service.move_to_position(1, bad)
        self.assertEqual(self.scope.received(StageCommands.POSITION_SET), [])

    def test_a_finite_move_still_goes(self):
        StageService(self.connection).move_to_position(1, 6.25)
        from tests.synthetic_scope_case import wait_until

        self.assertTrue(
            wait_until(
                lambda: len(self.scope.received(StageCommands.POSITION_SET)) == 1
            )
        )
