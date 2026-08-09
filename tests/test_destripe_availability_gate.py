"""The Destripe checkbox must probe the backend the pipeline actually calls.

Since flamingo-stitcher v0.9.5 destriping runs on the **vendored** stripe
filter (``flamingo_stitcher._pystripe_core``), which needs only
pywt/scipy/scikit-image. The full ``pystripe`` package is not used by the
pipeline at all — it imports dcimg/imageio/tqdm at module load, which is
exactly why the filter was vendored.

This dialog kept probing ``import pystripe``. That was correct while
requirements pinned flamingo-stitcher v0.4.2, and became wrong the moment the
pin moved to v0.10.1: ``update_and_run.bat`` installs pystripe with
``--no-deps``, so the package is importable-in-name-only and the probe fails.
The result was a greyed-out Destripe checkbox on a rig where destriping works
perfectly, under a tooltip telling the user to install the thing they had
already installed.

Run: QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest \\
        tests/test_destripe_availability_gate.py -q
"""

import ast
from pathlib import Path

import pytest

DIALOG = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "py2flamingo"
    / "views"
    / "dialogs"
    / "stitching_dialog.py"
)


def _source():
    return DIALOG.read_text(encoding="utf-8")


class TestTheGateProbesTheRealBackend:
    def test_the_vendored_filter_is_what_gets_probed(self):
        assert "from flamingo_stitcher import _pystripe_core" in _source()

    def test_the_full_pystripe_package_is_not_probed(self):
        """`import pystripe` is the stale check — it must not gate the UI.

        Parsed rather than grepped so a mention in a comment or tooltip (the
        feature is still *called* PyStripe) cannot fail this.
        """
        tree = ast.parse(_source())
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == "pystripe"
        ]
        assert not offenders, (
            f"stale `import pystripe` gate at line(s) {offenders}; the pipeline "
            f"uses flamingo_stitcher._pystripe_core"
        )

    def test_the_unavailable_tooltip_does_not_send_users_to_pip(self):
        """Installing pystripe never fixes this, so it must not be advised."""
        assert "pip install pystripe" not in _source()


class TestTheBackendIsActuallyImportable:
    """If this fails, the gate is right to close and the environment is wrong."""

    def test_the_vendored_filter_imports_and_runs(self):
        np = pytest.importorskip("numpy")
        pytest.importorskip("flamingo_stitcher")
        from flamingo_stitcher._pystripe_core import filter_streaks

        img = (np.random.default_rng(0).random((64, 64)) * 100).astype(np.float32)
        out = filter_streaks(img, sigma=(8, 8))
        assert out.shape == img.shape

    def test_the_stitcher_being_imported_is_new_enough_to_have_it(self):
        """The vendored filter arrived in v0.9.5; the pin must not slip back.

        Judged on the imported module's ``__version__``, not on package
        metadata. An editable install carries whatever dist-info it was created
        with — this venv reports 0.1.10 while importing the 0.10.1 source tree —
        so metadata answers a question about packaging, not about the code that
        will run. (The stitcher's own multiview-stitcher guard was fooled by the
        same gap; see flamingo-stitcher v0.10.1.)
        """
        pytest.importorskip("flamingo_stitcher")
        import flamingo_stitcher

        raw = str(getattr(flamingo_stitcher, "__version__", ""))
        assert raw, "flamingo_stitcher exposes no __version__ to judge"
        parts = []
        for chunk in raw.split("."):
            digits = ""
            for ch in chunk:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            if not digits:
                break
            parts.append(int(digits))
        assert tuple(parts) >= (0, 9, 5), (
            f"flamingo-stitcher {raw} predates the vendored stripe filter; "
            f"bump the pin in requirements.txt"
        )


class TestTheStitcherPinTracksTheReleaseTag:
    """The pin has to move with the tag or the "shared code" claim is false.

    It sat at v0.4.2 for six releases. Then, on the very commit that added
    "BUMP THIS WITH EVERY STITCHER RELEASE" to requirements.txt, v0.10.2 was
    tagged and the pin was left at v0.10.1 — so the comment was already stale
    when it was written. A note to a human is not a mechanism.
    """

    def _pinned_version(self):
        import re
        from pathlib import Path

        req = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(
            encoding="utf-8"
        )
        m = re.search(r"flamingo-stitcher\.git@v([0-9]+(?:\.[0-9]+)*)", req)
        assert m, "the flamingo-stitcher pin is missing or no longer tag-based"
        return tuple(int(p) for p in m.group(1).split("."))

    def test_the_pin_is_a_released_tag(self):
        assert self._pinned_version() >= (0, 9, 5), (
            "the vendored destripe filter arrived in v0.9.5; anything older "
            "silently disables Destriping"
        )

    def test_the_pin_is_not_older_than_the_installed_stitcher(self):
        """Catches the exact miss: a new tag released, the pin left behind.

        Judged against the imported module's __version__, which is what will
        actually run. An editable dev install may legitimately be ahead — that
        is skipped rather than failed, since only the rig's pin matters.
        """
        import pytest

        pytest.importorskip("flamingo_stitcher")
        import flamingo_stitcher

        raw = str(getattr(flamingo_stitcher, "__version__", ""))
        if not raw:
            pytest.skip("flamingo_stitcher exposes no __version__")
        installed = []
        for chunk in raw.split("."):
            digits = "".join(c for c in chunk if c.isdigit())
            if not digits:
                break
            installed.append(int(digits))
        installed = tuple(installed)

        pinned = self._pinned_version()
        assert pinned >= installed, (
            f"requirements.txt pins v{'.'.join(map(str, pinned))} but the "
            f"stitcher is at {raw}. Bump the pin — in-app stitching runs the "
            f"pin, not the tag."
        )
