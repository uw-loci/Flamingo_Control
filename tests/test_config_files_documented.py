"""Keep `docs/config_files_reference.md` true as the code moves.

The doc exists because config files were classified from their names, and one of
them turned out to be the only saved route to the microscope. A reference nobody
maintains would recreate that problem with extra confidence, so the claims most
likely to rot silently are pinned here.

Run: QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest \
        tests/test_config_files_documented.py -q
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "config_files_reference.md"


def _src(rel):
    return (REPO / "src" / "py2flamingo" / rel).read_text(encoding="utf-8")


class TestTheDocExists:
    def test_the_reference_is_shipped_with_the_code(self):
        assert DOC.exists(), "the in-repo copy is the one users find"


class TestPerMicroscopeFilesAreStillNameDerived:
    """The doc's whole 'what multiplies' section rests on these f-strings."""

    def test_settings_json_is_built_from_the_microscope_name(self):
        assert "{microscope_name}_settings.json" in _src(
            "services/microscope_settings_service.py"
        )

    def test_start_position_is_built_from_the_microscope_name(self):
        assert "{microscope_name}_start_position.txt" in _src(
            "services/configuration_service.py"
        )

    def test_the_name_still_comes_from_scope_settings(self):
        body = _src("services/configuration_service.py")
        i = body.index("def get_microscope_name")
        assert '"Type"' in body[i : i + 600] or "'Type'" in body[i : i + 600]


class TestTheDocumentedMultiScopeGapsAreStillGaps:
    """If one is fixed, this fails — update the doc rather than the assert.

    A stale "known limitation" is worse than none: it sends someone hunting a
    bug that was already fixed, or worse, stops them fixing it.
    """

    def test_n7_reference_position_is_still_hardcoded(self):
        body = _src("controllers/movement_controller.py")
        assert '"n7_reference_position.json"' in body, (
            "n7_reference_position.json looks name-derived now — good. Update "
            "the ⚠⚠ row in docs/config_files_reference.md"
        )

    @pytest.mark.parametrize(
        "rel,name",
        [
            ("services/optics_guard_service.py", "pixel_calibration.json"),
            ("services/optics_guard_service.py", "optics_guard.json"),
            ("services/position_preset_service.py", "position_presets.json"),
        ],
    )
    def test_these_are_still_single_files_not_per_scope(self, rel, name):
        assert f'"{name}"' in _src(
            rel
        ), f"{name} may have become per-microscope; update the doc's ⚠ table"


class TestTheSeedableFilesMatchTheDoc:
    @pytest.mark.parametrize(
        "name", ["saved_configurations.example.json", "drive_mappings.example.json"]
    )
    def test_the_example_is_present(self, name):
        assert (REPO / name).exists()
        assert name in DOC.read_text(encoding="utf-8")
