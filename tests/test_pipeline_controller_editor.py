"""PipelineController opens the editor it is given, not one it imports.

The controller sits below the view layer (docs/architecture.md), so the
composition root passes the editor class in. These tests pin both halves: the
controller calls the factory once and wires the run/stop signals, and the two
places that build a controller (component_factory, the main window's lazy
path) pass the real editor.
"""

import ast
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src" / "py2flamingo"


class TestEditorFactory(unittest.TestCase):
    def test_open_editor_builds_once_and_wires_run_and_stop(self):
        from py2flamingo.pipeline.controllers.pipeline_controller import (
            PipelineController,
        )

        editor = mock.Mock()
        factory = mock.Mock(return_value=editor)
        app = object()
        controller = PipelineController(
            service=mock.Mock(), app=app, editor_factory=factory
        )
        controller.open_editor()
        controller.open_editor()
        factory.assert_called_once_with(app=app)
        editor.run_requested.connect.assert_called_once()
        editor.stop_requested.connect.assert_called_once()
        self.assertEqual(editor.show.call_count, 2)

    def test_without_a_factory_open_editor_says_so(self):
        from py2flamingo.pipeline.controllers.pipeline_controller import (
            PipelineController,
        )

        controller = PipelineController(service=mock.Mock())
        with self.assertRaisesRegex(RuntimeError, "editor_factory"):
            controller.open_editor()

    def test_every_constructor_passes_the_real_editor(self):
        for rel in ("component_factory.py", "main_window.py"):
            tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
            calls = [
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and getattr(n.func, "id", getattr(n.func, "attr", None))
                == "PipelineController"
            ]
            self.assertTrue(calls, rel)
            for call in calls:
                kw = {k.arg: k.value for k in call.keywords}
                self.assertIn("editor_factory", kw, rel)
                self.assertEqual(
                    getattr(kw["editor_factory"], "id", None),
                    "PipelineEditorDialog",
                    rel,
                )


if __name__ == "__main__":
    unittest.main()
