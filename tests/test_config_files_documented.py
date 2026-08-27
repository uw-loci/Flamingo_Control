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

    def test_start_position_is_now_vestigial(self):
        """`get_start_position()` was dead and was removed on 2026-08-11.

        Nothing reads the file's CONTENTS any more; `FlamingoConnect` only
        checks that some `*_start_position.txt` exists and creates an empty
        placeholder if not. Asserted so the doc and the code cannot drift apart
        again — if a real reader comes back, this fails and the doc's
        "vestigial" row needs revisiting.
        """
        assert "{microscope_name}_start_position.txt" not in _src(
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

    def test_the_reference_position_is_no_longer_hardcoded(self):
        """Fixed 2026-08-11: it moved into {name}_settings.json.

        This assertion was inverted when the gap was closed, which is exactly
        what the old version asked for. A stale "known limitation" sends the
        next person hunting a bug that no longer exists.
        """
        body = _src("controllers/movement_controller.py")
        code = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("#")
        )
        assert "n7_reference_position.json" not in code

    @pytest.mark.parametrize("name", ["pixel_calibration.json", "optics_guard.json"])
    def test_these_became_per_scope_on_2026_08_26(self, name):
        """The ⚠ table is now empty; these resolve through the scoped helpers.

        Writes always go to `{scope}_{name}`, so measuring on one instrument
        can no longer destroy another's. Reads fall back to the shared
        pre-split file, so an existing single-scope install keeps working.
        """
        loader = _src("configs/config_loader.py")
        assert "def scoped_settings_read_path" in loader
        assert "def scoped_settings_write_path" in loader
        guard = _src("services/optics_guard_service.py")
        assert "scoped_settings_write_path" in guard
        assert f'"{name}"' in guard or f'"{name}"' in _src(
            "services/pixel_calibration_service.py"
        )


class TestPositionPresetsAreNowPerMicroscope:
    """The ⚠ row for position_presets.json was retired on 2026-08-26.

    Presets are stage coordinates and `move_to_position(validate=True)` clamps
    rather than refusing, so a shared file moved the stage to a silently
    different place while the UI reported the preset's name.
    """

    def test_the_service_builds_a_per_scope_filename(self):
        body = _src("services/position_preset_service.py")
        assert "_position_presets.json" in body

    def test_the_legacy_name_survives_only_as_the_migration_source(self):
        body = _src("services/position_preset_service.py")
        assert 'LEGACY_FILENAME = "position_presets.json"' in body

    def test_the_doc_no_longer_lists_it_as_a_shared_hazard(self):
        doc = DOC.read_text(encoding="utf-8")
        assert "| `position_presets.json` | Stage coordinates are instrument" not in doc


class TestTheSeedableFilesMatchTheDoc:
    @pytest.mark.parametrize(
        "name", ["saved_configurations.example.json", "drive_mappings.example.json"]
    )
    def test_the_example_is_present(self, name):
        assert (REPO / name).exists()
        assert name in DOC.read_text(encoding="utf-8")


class TestThePerMicroscopeHardwareOverlayIsDocumented:
    """The `microscopes:` overlay changed what "adding a scope" means.

    The doc's §3 checklist used to say "Change nothing: everything in
    src/py2flamingo/configs/". That is now false, and §3 is exactly what
    someone follows when wiring up a second instrument.
    """

    def test_the_hardware_yaml_has_the_overlay(self):
        assert "microscopes:" in _src("configs/microscope_hardware.yaml")

    def test_the_doc_no_longer_calls_the_hardware_yaml_single(self):
        doc = DOC.read_text(encoding="utf-8")
        assert "`microscope_hardware.yaml` | Sensor size" in doc
        line = next(
            ln
            for ln in doc.splitlines()
            if ln.startswith("| `microscope_hardware.yaml`")
        )
        assert "N entries" in line, "the multiplicity column still claims one setting"

    def test_the_doc_names_the_per_scope_layer_in_the_overlay_order(self):
        doc = DOC.read_text(encoding="utf-8")
        assert "the `microscopes:` block > the base YAML" in doc

    def test_the_doc_records_the_yaml_ordering_constraint(self):
        """A future editor moving the block would silently reroute calibration."""
        doc = DOC.read_text(encoding="utf-8")
        assert "must stay LAST in `microscope_hardware.yaml`" in doc

    def test_the_doc_stopped_telling_people_to_change_nothing_in_configs(self):
        doc = DOC.read_text(encoding="utf-8")
        assert "Everything in `src/py2flamingo/configs/`, and the" not in doc
