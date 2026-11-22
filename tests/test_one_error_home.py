"""core/errors.py is the one home of the error types and codes.

``models/base.py`` had its own ``ValidationError(Exception)`` next to
``core.errors.ValidationError``; ``workflows/workflow_validator.py`` defined
``WorkflowValidationError``; and ``ErrorCodes`` assigned ``CONNECTION_TIMEOUT``
twice (1002 in the connection range, then 8003 in the timeout range; the second
silently won). One definition each now, CONNECTION_TIMEOUT = 1002.
"""

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "py2flamingo"
ERRORS = SRC / "core" / "errors.py"


class OneErrorHome(unittest.TestCase):
    def test_error_classes_are_defined_only_in_core_errors(self):
        names = {"ValidationError", "WorkflowError", "WorkflowValidationError"}
        where = {}
        for path in SRC.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ClassDef) and node.name in names:
                    where.setdefault(node.name, []).append(
                        path.relative_to(SRC).as_posix()
                    )
        self.assertEqual(where, {n: ["core/errors.py"] for n in names})

    def test_each_error_code_is_assigned_once_with_a_unique_value(self):
        tree = ast.parse(ERRORS.read_text(encoding="utf-8"))
        (codes,) = [
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "ErrorCodes"
        ]
        assigned = [
            (t.id, n.value.value)
            for n in codes.body
            if isinstance(n, ast.Assign)
            for t in n.targets
        ]
        names = [a for a, _ in assigned]
        values = [v for _, v in assigned]
        self.assertEqual(len(names), len(set(names)), "a code name assigned twice")
        self.assertEqual(len(values), len(set(values)), "two codes share a value")

    def test_connection_timeout_is_in_the_connection_range(self):
        from py2flamingo.core.errors import ErrorCodes

        self.assertEqual(ErrorCodes.CONNECTION_TIMEOUT, 1002)

    def test_model_validation_raises_the_core_type(self):
        from py2flamingo.core.errors import FlamingoError, ValidationError
        from py2flamingo.models.data.workflow import StackSettings

        with self.assertRaises(ValidationError) as cm:
            StackSettings(num_planes=0)
        self.assertIsInstance(cm.exception, FlamingoError)
        self.assertEqual(cm.exception.error_code, 7001)


if __name__ == "__main__":
    unittest.main()
