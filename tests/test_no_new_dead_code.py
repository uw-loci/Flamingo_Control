"""No NEW function may be added without a caller.

Two whole position-storage features were found dead by hand on 2026-08-10 --
`movement_controller`'s N7 reference position and
`ConfigurationService.get_start_position` -- both surviving since 2025. During
that same investigation I nearly recommended the second as the "live
alternative" to the first, which is what a one-off manual search buys you.

This runs `tools/find_dead_code.py` against a baseline of what was already dead
when the check was introduced, so it can be adopted on a codebase with 378
existing findings: today's list is recorded once, and only NEW uncalled
functions fail. The baseline is meant to shrink.

If this fails, the options in order of preference: delete the function, wire it
up, or -- when the scanner is wrong, e.g. a framework calls it by name -- add it
to KNOWN_OVERRIDES with a comment, or to the baseline with a reason.

Run: ./.venv/bin/python -m pytest tests/test_no_new_dead_code.py -q
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "find_dead_code.py"
BASELINE = REPO / "dead_code_baseline.txt"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


class TestTheCheckItselfWorks:
    def test_the_tool_and_baseline_are_present(self):
        assert TOOL.exists(), "the check cannot run without its script"
        assert BASELINE.exists(), "without a baseline every existing finding fails"

    def test_it_detects_a_function_nobody_calls(self, tmp_path):
        """A canary: if the scanner stops finding anything, this test is a lie."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "m.py").write_text(
            "def used():\n    return 1\n\n"
            "def never_called_anywhere():\n    return 2\n\n"
            "def caller():\n    return used()\n",
            encoding="utf-8",
        )
        out = _run(str(pkg))
        assert "never_called_anywhere" in out.stdout
        assert "def used" not in out.stdout

    def test_dunders_and_tests_are_not_reported(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "m.py").write_text(
            "class C:\n    def __init__(self):\n        pass\n\n"
            "def test_something():\n    pass\n",
            encoding="utf-8",
        )
        out = _run(str(pkg))
        assert "__init__" not in out.stdout
        assert "test_something" not in out.stdout

    def test_a_name_that_is_only_imported_counts_as_live(self, tmp_path):
        """The bug that broke 18 modules on 2026-08-11.

        `from mod import helper` binds the name without ever producing a Name
        node for it in the importing module's expressions. The scanner did not
        visit Import/ImportFrom, called `dict_comment` dead, and the sweep
        removed it out from under `image_acquisition_service`.
        """
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
        (pkg / "user.py").write_text("from .lib import helper\n", encoding="utf-8")
        out = _run(str(pkg))
        assert (
            "helper" not in out.stdout
        ), "an imported name is a used name; deleting it breaks the importer"

    def test_a_name_used_only_in_a_string_counts_as_live(self, tmp_path):
        """getattr/registry dispatch must not be reported. Over-report is worse."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "m.py").write_text(
            "def handler_alpha():\n    return 1\n\n"
            "def dispatch(n):\n    return globals()['handler_alpha']\n",
            encoding="utf-8",
        )
        out = _run(str(pkg))
        assert "handler_alpha" not in out.stdout


class TestNoNewDeadCode:
    def test_nothing_uncalled_outside_the_baseline(self):
        out = _run(
            "src/py2flamingo",
            "--tests",
            "tests",
            "--baseline",
            "dead_code_baseline.txt",
            "--check",
        )
        assert out.returncode == 0, (
            "new function(s) with no caller:\n"
            + out.stdout
            + "\nDelete them, wire them up, or justify them in the baseline."
        )
