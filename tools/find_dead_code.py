#!/usr/bin/env python3
"""Find functions and methods that nothing calls.

WHY THIS EXISTS
---------------
Two whole position-storage features were found dead by hand on 2026-08-10:
``movement_controller``'s N7 reference position (~65 lines, a JSON file, and a
docstring advertising a UI that was never built) and
``ConfigurationService.get_start_position`` (zero callers). Both had survived
since 2025. Worse, mid-investigation I nearly recommended the second one as the
"live alternative" to the first. A repeatable check finds these in a second.

WHAT IT WILL FIND
-----------------
* module-level functions and class methods whose name appears nowhere else in
  the scanned tree -- not called, not referenced as an attribute, not named in
  any string literal.
* functions referenced ONLY by tests (``--test-only``), which are usually either
  dead production code kept alive by its own test, or genuinely test-only
  helpers living in the wrong place.

WHAT IT WILL NOT FIND (inspect these by hand)
---------------------------------------------
* **Dead classes, constants, config keys, or whole modules.** Only ``def``s.
* **Unreachable code inside a live function** -- a branch nobody takes still
  counts as used.
* **Anything reached dynamically by a computed name**: ``getattr(obj, "get_"
  + suffix)``, a Qt slot connected from a ``.ui`` file, a plugin registry keyed
  by config. To stay safe the scanner treats ANY string literal equal to a
  function's name as a use, so these show up as live even when they are not.
* **Cross-repo callers.** flamingo-stitcher and external scripts are not
  scanned. A public API called only from outside will look dead here.
* **Overrides.** A method overriding a base class is reported only if its name
  is not in KNOWN_OVERRIDES; a novel override of a third-party base class can
  still be a false positive.

So: a name reported here is *probably* dead, and worth reading. A name NOT
reported is not proof of life. Always ``git show`` a file before deleting it --
that is the lesson that produced this script.

USAGE
-----
    python3 toolsAndTesting/find_dead_code.py <repo>/src/py2flamingo
    python3 toolsAndTesting/find_dead_code.py <src> --tests <repo>/tests
    python3 toolsAndTesting/find_dead_code.py <src> --tests <t> --test-only
    python3 toolsAndTesting/find_dead_code.py <src> --baseline dead_code_baseline.txt --check

``--check`` exits 1 if anything is dead that is not in the baseline, so the
check can be adopted on a codebase that already has findings: record today's
list once, then only NEW dead code fails.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Called by a framework, never by us.
KNOWN_OVERRIDES = {
    # Qt event handlers / virtuals
    "paintEvent",
    "closeEvent",
    "resizeEvent",
    "showEvent",
    "hideEvent",
    "keyPressEvent",
    "keyReleaseEvent",
    "mousePressEvent",
    "mouseMoveEvent",
    "mouseReleaseEvent",
    "mouseDoubleClickEvent",
    "wheelEvent",
    "enterEvent",
    "leaveEvent",
    "dragEnterEvent",
    "dragMoveEvent",
    "dropEvent",
    "focusInEvent",
    "focusOutEvent",
    "contextMenuEvent",
    "changeEvent",
    "moveEvent",
    "eventFilter",
    "event",
    "sizeHint",
    "minimumSizeHint",
    "accept",
    "reject",
    "done",
    "exec",
    "exec_",
    "run",
    "timerEvent",
    "customEvent",
    "dragLeaveEvent",
    "inputMethodEvent",
    "actionEvent",
    # QGraphicsItem / QAbstractItemModel / QValidator virtuals -- Qt calls these
    # from C++, so no Python caller exists and they are NOT dead.
    "boundingRect",
    "paint",
    "shape",
    "itemChange",
    "hoverEnterEvent",
    "hoverLeaveEvent",
    "hoverMoveEvent",
    "rowCount",
    "columnCount",
    "data",
    "headerData",
    "flags",
    "setData",
    "index",
    "parent",
    "insertRows",
    "removeRows",
    "validate",
    "fixup",
    "createEditor",
    "setEditorData",
    "setModelData",
    "updateEditorGeometry",
    "sizeHintForColumn",
    # unittest / pytest
    "setUp",
    "tearDown",
    "setUpClass",
    "tearDownClass",
    "setup_method",
    "teardown_method",
    "setup_class",
    "teardown_class",
    # context managers / protocols not always called explicitly
    "__enter__",
    "__exit__",
}


def _is_exempt(name: str) -> bool:
    if name.startswith("__") and name.endswith("__"):
        return True  # dunders are called by the interpreter
    if name.startswith("test_"):
        return True  # pytest collects these
    return name in KNOWN_OVERRIDES


class _Definitions(ast.NodeVisitor):
    """Collect every def, with the class it belongs to."""

    def __init__(self, path: Path):
        self.path = path
        self.defs: List[Tuple[str, str, int]] = []  # (name, qualifier, lineno)
        self._class_stack: List[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def _record(self, node) -> None:
        qualifier = ".".join(self._class_stack) if self._class_stack else "<module>"
        self.defs.append((node.name, qualifier, node.lineno))
        self.generic_visit(node)

    visit_FunctionDef = _record
    visit_AsyncFunctionDef = _record


class _Uses(ast.NodeVisitor):
    """Collect every name that could possibly be a reference to a def.

    Deliberately over-collects. A false "used" costs nothing; a false "dead"
    could get working code deleted.
    """

    def __init__(self) -> None:
        self.used: Set[str] = set()
        self._own_defs: Set[int] = set()

    def visit_Name(self, node: ast.Name) -> None:
        self.used.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # `self.foo()` and a bare `self.foo` both count.
        self.used.add(node.attr)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # getattr(o, "name"), a registry keyed by string, a Qt slot name.
        if isinstance(node.value, str):
            self.used.add(node.value)
            for part in node.value.replace(",", " ").split():
                self.used.add(part.strip("\"'()[]{}.:"))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # A decorator naming the function it wraps is a use.
        for dec in node.decorator_list:
            self.visit(dec)
        for child in node.body:
            self.visit(child)
        for child in node.args.defaults:
            self.visit(child)

    visit_AsyncFunctionDef = visit_FunctionDef


def _python_files(root: Path) -> List[Path]:
    return [
        p
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and ".venv" not in p.parts
    ]


def _scan(paths: List[Path]) -> Tuple[Dict[str, List[Tuple[Path, str, int]]], Set[str]]:
    defs: Dict[str, List[Tuple[Path, str, int]]] = {}
    uses: Set[str] = set()
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            print(f"  ! skipped {path}: {exc}", file=sys.stderr)
            continue
        d = _Definitions(path)
        d.visit(tree)
        for name, qualifier, lineno in d.defs:
            defs.setdefault(name, []).append((path, qualifier, lineno))
        u = _Uses()
        u.visit(tree)
        uses |= u.used
    return defs, uses


def _defined_names_used_by_their_own_definition(defs, src_files) -> Set[str]:
    """Names used only at their own `def` line are not really used elsewhere."""
    return set()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", type=Path, help="package root to scan for defs")
    ap.add_argument(
        "--tests", type=Path, default=None, help="test tree (counts as uses)"
    )
    ap.add_argument(
        "--test-only",
        action="store_true",
        help="report functions referenced ONLY from tests",
    )
    ap.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="file of known findings, one 'path:line:qualifier.name' per line",
    )
    ap.add_argument(
        "--check", action="store_true", help="exit 1 on any finding not in the baseline"
    )
    ap.add_argument(
        "--write-baseline",
        action="store_true",
        help="rewrite the baseline file with current findings",
    )
    ap.add_argument(
        "--by-file",
        action="store_true",
        help="summarise per file, worst first, flagging fully-dead modules",
    )
    args = ap.parse_args()

    src_files = _python_files(args.source)
    defs, src_uses = _scan(src_files)

    test_uses: Set[str] = set()
    if args.tests and args.tests.exists():
        _, test_uses = _scan(_python_files(args.tests))

    findings: List[str] = []
    test_only: List[str] = []

    for name, sites in sorted(defs.items()):
        if _is_exempt(name):
            continue
        in_src = name in src_uses
        in_tests = name in test_uses
        for path, qualifier, lineno in sites:
            entry = f"{path}:{lineno}:{qualifier}.{name}"
            if not in_src and not in_tests:
                findings.append(entry)
            elif not in_src and in_tests:
                test_only.append(entry)

    if args.test_only:
        print(f"Referenced only by tests ({len(test_only)}):")
        for entry in test_only:
            print(f"  {entry}")
        return 0

    if args.by_file:
        # Total defs per file, so "12 dead of 12" reads differently from
        # "12 dead of 300" -- the first is a module to delete, the second is
        # a module to prune.
        total_by_file: Dict[str, int] = {}
        for name, sites in defs.items():
            for path, _q, _l in sites:
                total_by_file[str(path)] = total_by_file.get(str(path), 0) + 1
        dead_by_file: Dict[str, int] = {}
        for entry in findings:
            path = entry.rsplit(":", 2)[0]
            dead_by_file[path] = dead_by_file.get(path, 0) + 1
        print(f"{'dead':>5} {'of':>5}  file")
        for path, count in sorted(dead_by_file.items(), key=lambda kv: -kv[1]):
            total = total_by_file.get(path, count)
            flag = "   <-- ENTIRE MODULE" if count == total else ""
            print(f"{count:>5} {total:>5}  {path}{flag}")
        return 0

    baseline: Set[str] = set()
    if args.baseline and args.baseline.exists():
        baseline = {
            line.strip()
            for line in args.baseline.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }

    if args.write_baseline and args.baseline:
        args.baseline.write_text(
            "# Dead code known at baseline time. Shrink this list; do not grow it.\n"
            "# Regenerate: find_dead_code.py <src> --tests <tests> "
            "--baseline <file> --write-baseline\n" + "\n".join(sorted(findings)) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {len(findings)} entries to {args.baseline}")
        return 0

    new = [f for f in findings if f not in baseline]

    print(f"Uncalled functions: {len(findings)} total, {len(new)} not in baseline")
    for entry in new:
        print(f"  {entry}")
    if test_only:
        print(
            f"\n({len(test_only)} more are referenced only by tests; "
            f"re-run with --test-only to see them)"
        )

    if args.check and new:
        print(
            f"\nFAIL: {len(new)} function(s) with no caller. Either delete them, "
            f"wire them up, or -- if the scanner is wrong -- add them to the "
            f"baseline with a comment saying why.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
