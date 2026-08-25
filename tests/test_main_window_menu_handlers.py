"""Extensions-menu handlers must actually open.

`_on_mip_overview` and `_on_psf_analysis` both ended with

    self._mip_overview_dialogs.append(dialog)   # `dialog` never existed

so the menu action raised NameError, was swallowed by the handler's own
try/except, and the user got "Failed to open MIP Overview". Nothing caught it
because every other test constructs the dialogs directly rather than going
through the menu handler.

pyflakes finds this class of bug in one pass, so the last test here runs it over
the module rather than waiting for someone to click the right menu item.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src" / "py2flamingo"


class TestNoUndefinedNamesInExecutablePositions:
    """The check that would have caught it, over the whole package."""

    @staticmethod
    def _undefined(path: Path):
        import ast
        import re

        out = subprocess.run(
            [sys.executable, "-m", "pyflakes", str(path)],
            capture_output=True,
            text=True,
        ).stdout
        hits = []
        for line in out.splitlines():
            m = re.match(r"(.+?):(\d+):\d+: undefined name '(.+)'", line)
            if m:
                hits.append((m.group(1), int(m.group(2)), m.group(3)))
        if not hits:
            return []
        # An undefined name in an ANNOTATION cannot raise at runtime; one in an
        # executable position can. Only the second kind is a live bug, and the
        # package has a standing population of the first kind.
        runtime = []
        for file_path, lineno, name in hits:
            try:
                tree = ast.parse(Path(file_path).read_text())
            except SyntaxError:
                continue
            annotated = set()
            for node in ast.walk(tree):
                for field in ("annotation", "returns"):
                    ann = getattr(node, field, None)
                    if ann is not None:
                        annotated.update(
                            getattr(s, "lineno", None) for s in ast.walk(ann)
                        )
            if lineno not in annotated:
                runtime.append(f"{file_path}:{lineno} {name}")
        return runtime

    def test_the_two_menu_handlers_are_clean(self):
        pytest.importorskip("pyflakes")
        assert self._undefined(_SRC / "main_window.py") == []


# NOTE: a test that drives the handlers through a real MainWindow was tried and
# removed -- constructing one (even via QMainWindow.__new__) segfaults pytest
# under QT_QPA_PLATFORM=offscreen, the same trap recorded for per-test dialog
# construction elsewhere in this suite. The static check above catches this bug
# class in one pass and cannot crash, which is the better trade here.
