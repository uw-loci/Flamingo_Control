"""The workflow models live in one module, each class name once.

``models/workflow.py`` and ``models/data/workflow.py`` both defined
WorkflowType, IlluminationSettings, StackSettings, TileSettings and
ExperimentSettings, with different units (``z_step_mm`` against
``z_step_um``). Only the second was used to build workflows; the first lived
on only for ``WorkflowModel``'s run state. That run state is now
``models.data.workflow.WorkflowModel`` and the first module is gone.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "py2flamingo"
NAMES = {
    "WorkflowType",
    "IlluminationSettings",
    "StackSettings",
    "TileSettings",
    "ExperimentSettings",
    "WorkflowModel",
}


def test_each_workflow_model_class_is_defined_once():
    where = {}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in NAMES:
                where.setdefault(node.name, []).append(path.relative_to(SRC).as_posix())
    assert where == {n: ["models/data/workflow.py"] for n in NAMES}, where


def test_the_run_state():
    from py2flamingo.models import WorkflowModel

    model = WorkflowModel()
    assert not model.is_running() and model.get_execution_time() is None
    model.mark_started()
    assert model.is_running() and model.get_execution_time() >= 0
    model.mark_completed()
    assert not model.is_running()
