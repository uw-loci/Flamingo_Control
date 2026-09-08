"""No two widgets may be placed in the same QGridLayout cell.

flamingo-stitcher v0.12.2 shipped a combo box into a grid row that was already
occupied across all four columns. Qt drew the new label and combo on top of the
existing row, and the widgets underneath took the clicks, so the control both
rendered as overlapping text and could not be operated. Its tests asserted the
widget existed, that the config read it, and that QSettings persisted it — all
true, and all blind to WHERE it had been put.

This repo has none today (checked: zero collisions across 24 files using
QGridLayout). This is the guard against the first one.

Static, by choice. Constructing the real views is not an option here — SampleView
pulls in napari and a live GL canvas that segfaults under pytest even offscreen,
as test_populate_button_width.py records — so a check that needs a running
widget could not cover the files that matter most. `assert_no_grid_collisions`
is exported for the tests that DO build a widget: it reads the positions Qt
actually holds, so it also catches rows computed at runtime, which the source
scan cannot see.

Run: ./.venv/bin/python -m pytest tests/test_gui_grid_layout.py -q
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "py2flamingo"
FILES = sorted(
    p
    for p in SRC.rglob("*.py")
    if "QGridLayout" in p.read_text(encoding="utf-8", errors="ignore")
)


# ---------------------------------------------------------------------------
# Runtime check — for tests that already have a widget in hand.
# ---------------------------------------------------------------------------


def assert_no_grid_collisions(widget) -> None:
    """Fail if any QGridLayout under `widget` has two items in one cell.

    Reads getItemPosition() from the live layout, so unlike the source scan it
    sees rows that were computed rather than written as literals.
    """
    from PyQt5.QtWidgets import QGridLayout

    layouts = [widget.layout()] if widget.layout() is not None else []
    layouts += list(widget.findChildren(QGridLayout))
    problems = []
    for layout in layouts:
        if not isinstance(layout, QGridLayout):
            continue
        owner = {}
        for i in range(layout.count()):
            row, col, rowspan, colspan = layout.getItemPosition(i)
            item = layout.itemAt(i)
            name = ""
            if item is not None and item.widget() is not None:
                w = item.widget()
                name = w.objectName() or type(w).__name__
            for r in range(row, row + max(1, rowspan)):
                for c in range(col, col + max(1, colspan)):
                    if (r, c) in owner:
                        problems.append(
                            f"{type(layout).__name__} cell ({r}, {c}): "
                            f"{owner[(r, c)]} and {name}"
                        )
                    else:
                        owner[(r, c)] = name
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# Source scan — runs everywhere, covers the files a widget test cannot reach.
# ---------------------------------------------------------------------------


def _layout_key(node):
    """`grid`, or `self._grid` — anything a placement can be called on."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def _rebindings(tree):
    """{layout key: [lineno, ...]} where the name is (re)assigned.

    A grid variable is routinely reused for the next group, so placements have
    to be split at each rebinding or two unrelated grids look like one and every
    row reads as a collision.
    """
    out = defaultdict(list)
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            key = _layout_key(target)
            if key:
                out[key].append(node.lineno)
    return {k: sorted(v) for k, v in out.items()}


def _own_nodes(scope):
    """Every node in `scope`, not descending into nested functions."""
    for node in ast.iter_child_nodes(scope):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield node
        yield from _own_nodes(node)


def _scopes(tree):
    """Each function body, plus module level, as a separate placement scope.

    Collisions only mean something WITHIN one function. A grid filled by two
    methods is a rebuild — one renders a placeholder, another renders the rows,
    each clearing with takeAt() first — and no static check can tell that from
    an accumulation.
    """
    yield "<module>", tree
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node


def _placements(tree, source, scope=None):
    """{(layout key, generation): [(row, col, rowspan, colspan, lineno, text)]}.

    Only literal integer positions are read; a computed row cannot be judged
    statically and guessing would produce false failures. The runtime helper
    above covers those.
    """
    rebound = _rebindings(tree)
    found = defaultdict(list)
    nodes = _own_nodes(scope) if scope is not None else ast.walk(tree)
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in ("addWidget", "addLayout"):
            continue
        key = _layout_key(func.value)
        if key is None:
            continue
        args = node.args[1:]  # first arg is the widget/layout
        if len(args) < 2:
            continue  # box layout, not a grid
        nums = []
        for arg in args[:4]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                nums.append(arg.value)
            else:
                nums = []
                break
        if len(nums) < 2:
            continue
        row, col = nums[0], nums[1]
        rowspan = nums[2] if len(nums) > 2 else 1
        colspan = nums[3] if len(nums) > 3 else 1
        text = ast.get_source_segment(source, node) or ""
        generation = sum(1 for line in rebound.get(key, []) if line <= node.lineno)
        found[(key, generation)].append(
            (row, col, rowspan, colspan, node.lineno, text.split("\n")[0])
        )
    return found


def _collisions(placements):
    owner = {}
    clashes = []
    for row, col, rowspan, colspan, lineno, text in placements:
        for r in range(row, row + max(1, rowspan)):
            for c in range(col, col + max(1, colspan)):
                if (r, c) in owner:
                    clashes.append(((r, c), owner[(r, c)], (lineno, text)))
                else:
                    owner[(r, c)] = (lineno, text)
    return clashes


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_grid_cell_is_claimed_twice(path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for scope_name, scope in _scopes(tree):
        for (layout, _gen), placements in _placements(tree, source, scope).items():
            clashes = _collisions(placements)
            assert not clashes, "\n".join(
                f"{path.name}: {scope_name}() {layout} cell {cell} claimed by "
                f"line {a[0]} ({a[1]}) and line {b[0]} ({b[1]})"
                for cell, a, b in clashes
            )


def test_the_scan_actually_covers_the_gui():
    """A parametrised test over an empty list passes silently."""
    assert len(FILES) >= 10, f"only found {len(FILES)} files using QGridLayout"


class TestTheDetectorItself:
    """A checker that cannot fail is worse than no checker."""

    def test_it_catches_an_exact_overlap(self):
        source = "g = QGridLayout()\ng.addWidget(a, 6, 0)\ng.addWidget(b, 6, 0)\n"
        found = _placements(ast.parse(source), source)
        assert _collisions(found[("g", 1)])

    def test_it_catches_a_span_running_over_a_neighbour(self):
        """The stitcher bug: a 1x4 span onto a row already holding four."""
        source = "g = QGridLayout()\ng.addWidget(a, 6, 1, 1, 3)\ng.addWidget(b, 6, 3)\n"
        found = _placements(ast.parse(source), source)
        assert _collisions(found[("g", 1)])

    def test_it_allows_adjacent_cells(self):
        source = (
            "g = QGridLayout()\ng.addWidget(a, 5, 0)\n"
            "g.addWidget(b, 6, 0)\ng.addWidget(c, 5, 1)\n"
        )
        found = _placements(ast.parse(source), source)
        assert not _collisions(found[("g", 1)])

    def test_a_rebound_name_is_a_separate_grid(self):
        source = (
            "g = QGridLayout()\ng.addWidget(a, 1, 0)\n"
            "g = QGridLayout()\ng.addWidget(b, 1, 0)\n"
        )
        found = _placements(ast.parse(source), source)
        assert len(found) == 2
        for placements in found.values():
            assert not _collisions(placements)

    def test_it_ignores_box_layouts(self):
        source = "v.addWidget(a)\nv.addWidget(b)\n"
        assert _placements(ast.parse(source), source) == {}


class TestTheRuntimeHelper:
    """Covers the rows the source scan cannot see, for tests holding a widget."""

    def test_it_catches_a_computed_collision(self, qtbot):
        from PyQt5.QtWidgets import QGridLayout, QLabel, QWidget

        w = QWidget()
        qtbot.addWidget(w)
        grid = QGridLayout(w)
        for i in range(3):
            grid.addWidget(QLabel(f"a{i}"), i // 2, 0)  # rows 0, 0, 1
        with pytest.raises(AssertionError, match=r"cell \(0, 0\)"):
            assert_no_grid_collisions(w)

    def test_a_clean_grid_passes(self, qtbot):
        from PyQt5.QtWidgets import QGridLayout, QLabel, QWidget

        w = QWidget()
        qtbot.addWidget(w)
        grid = QGridLayout(w)
        grid.addWidget(QLabel("a"), 0, 0, 1, 2)
        grid.addWidget(QLabel("b"), 1, 0)
        grid.addWidget(QLabel("c"), 1, 1)
        assert_no_grid_collisions(w)
