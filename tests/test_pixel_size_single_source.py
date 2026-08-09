"""There must be exactly one source for the sample-plane pixel size.

Every tile grid the client previews or scans is ``FOV x (1 - overlap)``, and
``FOV`` is ``pixel_size x frame_px``. If two code paths resolve pixel size
differently, the grid the user is shown is not the grid the scope images, and
nothing in the UI reveals which one is wrong.

That was the state of the code: the LED 2D Overview dialog previewed tile counts
from ``camera_service.get_pixel_field_of_view()`` (firmware, magnification-derived)
while the workflow that moved the stage resolved its step through
``get_hardware_config()`` (calibration-aware). On the rig the two happen to agree
today — both ~1.0475 µm at 6.205x — so it looked fine. They diverge the moment an
XY Pixel Calibrator result is saved.

``py2flamingo.utils.fov`` is now that single source. The allowlist below is the
whole of the exception: the Pixel Calibrator must read the firmware value
directly, because showing it against its own measurement is the entire point of
the dialog.

Run: QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest \\
        tests/test_pixel_size_single_source.py -q
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "py2flamingo"

# Modules permitted to call the firmware pixel size directly, and why.
FIRMWARE_ALLOWLIST = {
    # Owns the resolution order; reads firmware as fallback + for comparison.
    "utils/fov.py",
    # Defines the accessor.
    "services/camera_service.py",
    # Displays firmware vs measured — that comparison IS the feature.
    "views/dialogs/pixel_calibrator_dialog.py",
}


def _modules_calling(name: str):
    """Repo-relative paths of modules containing a call to ``name``."""
    hits = []
    for path in SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == name
            ):
                hits.append(str(path.relative_to(SRC)).replace("\\", "/"))
                break
    return set(hits)


class TestOnlyOneSourceOfPixelSize:
    def test_no_new_module_reads_the_firmware_pixel_size(self):
        offenders = _modules_calling("get_pixel_field_of_view") - FIRMWARE_ALLOWLIST
        assert not offenders, (
            "these modules bypass py2flamingo.utils.fov and read the firmware "
            f"pixel size directly: {sorted(offenders)}. Use resolve_fov_mm()/"
            "resolve_pixel_size_mm() so the preview and the scan cannot "
            "disagree about tile size."
        )

    def test_the_led_overview_dialog_and_workflow_share_one_resolver(self):
        """The two that actually diverged — preview vs the stage that moves."""
        dialog = (SRC / "views/dialogs/led_2d_overview_dialog.py").read_text()
        workflow = (SRC / "workflows/led_2d_overview_workflow.py").read_text()
        for name, text in (("dialog", dialog), ("workflow", workflow)):
            assert (
                "from py2flamingo.utils.fov import resolve_fov_mm" in text
            ), f"the LED overview {name} must resolve FOV through utils.fov"

    def test_the_resolver_prefers_the_config_over_the_firmware(self, monkeypatch):
        """Behaviour, not prose: with both available, the config wins."""
        import py2flamingo.configs.config_loader as cl
        from py2flamingo.utils import fov

        monkeypatch.setattr(
            cl,
            "get_hardware_config",
            lambda: SimpleNamespace(
                effective_pixel_size_um=1.0475,
                optics_source="calibration",
                pixel_size_override_um=1.0475,
            ),
        )
        app = SimpleNamespace(
            camera_service=SimpleNamespace(
                get_pixel_field_of_view=lambda: 0.0005,  # 0.5 µm — deliberately different
                get_image_size=lambda: (1024, 1024),
            )
        )
        assert fov.resolve_pixel_size_mm(app) == pytest.approx(1.0475 / 1000.0)

    def test_the_firmware_is_used_when_the_config_cannot_be_read(self, monkeypatch):
        """A fallback, so an unconfigured scope still previews something."""
        import py2flamingo.configs.config_loader as cl
        from py2flamingo.utils import fov

        def _boom():
            raise RuntimeError("no config")

        monkeypatch.setattr(cl, "get_hardware_config", _boom)
        app = SimpleNamespace(
            camera_service=SimpleNamespace(
                get_pixel_field_of_view=lambda: 0.0005,
                get_image_size=lambda: (1024, 1024),
            )
        )
        assert fov.resolve_pixel_size_mm(app) == pytest.approx(0.0005)

    def test_an_unresolvable_pixel_size_refuses_rather_than_guesses(self, monkeypatch):
        """None is a refusal — a wrong FOV drives the stage the wrong distance."""
        import py2flamingo.configs.config_loader as cl
        from py2flamingo.utils import fov

        def _boom():
            raise RuntimeError("no config")

        monkeypatch.setattr(cl, "get_hardware_config", _boom)
        app = SimpleNamespace(
            camera_service=SimpleNamespace(
                get_pixel_field_of_view=lambda: 0.0,
                get_image_size=lambda: (1024, 1024),
            )
        )
        assert fov.resolve_pixel_size_mm(app) is None
        assert fov.resolve_fov_mm(app) is None

    def test_fov_is_pixel_size_times_the_smaller_frame_dimension(self, monkeypatch):
        """Honours a cropped AOI — the static config does not know about it."""
        import py2flamingo.configs.config_loader as cl
        from py2flamingo.utils import fov

        monkeypatch.setattr(
            cl,
            "get_hardware_config",
            lambda: SimpleNamespace(
                effective_pixel_size_um=1.0475,
                optics_source="scope",
                pixel_size_override_um=None,
            ),
        )
        app = SimpleNamespace(
            camera_service=SimpleNamespace(
                get_pixel_field_of_view=lambda: 0.0010475,
                get_image_size=lambda: (2048, 1024),  # cropped AOI
            )
        )
        # 1024, not 2048 — and this is the 1.0726 mm that set the rig's grid.
        assert fov.resolve_fov_mm(app) == pytest.approx(1.0475 * 1024 / 1000.0)


class TestTheMismatchIsReportedAtConnectionNotMidRun:
    """A mismatch is a property of the scope's setup, not of one acquisition."""

    def test_the_resolver_does_not_warn_from_inside_the_tile_loop(self):
        src = (SRC / "utils/fov.py").read_text()
        tree = ast.parse(src)
        resolver = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "resolve_pixel_size_mm"
        )
        warned = [
            n
            for n in ast.walk(resolver)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "warning"
            and "divergence" in ast.dump(n).lower()
        ]
        assert not warned, "divergence belongs in the Connection tab, not here"

    def test_the_connection_view_reports_it(self):
        src = (SRC / "views/connection_view.py").read_text()
        assert "compare_pixel_size_sources" in src
        assert "_pixel_size_lines" in src

    def test_agreeing_sources_produce_no_warning(self):
        from py2flamingo.utils.fov import PixelSizeComparison

        r = PixelSizeComparison(config_um=1.0475, firmware_um=1.0480)
        assert r.agrees
        assert r.warning() is None
        assert "1.0475" in r.summary()

    def test_diverging_sources_produce_an_actionable_warning(self):
        from py2flamingo.utils.fov import PixelSizeComparison

        r = PixelSizeComparison(config_um=1.0475, firmware_um=0.5000)
        assert not r.agrees
        msg = r.warning()
        assert msg and "mismatch" in msg.lower()
        assert "XY Pixel Calibrator" in msg, "must say what to DO about it"
        assert "1.0475" in msg and "0.5000" in msg

    def test_a_missing_source_is_not_treated_as_disagreement(self):
        from py2flamingo.utils.fov import PixelSizeComparison

        assert PixelSizeComparison(config_um=1.0475, firmware_um=None).agrees
        assert PixelSizeComparison(config_um=None, firmware_um=None).warning() is None
        assert "unknown" in PixelSizeComparison(None, None).summary()


class TestTheOverviewRemembersItsOverlap:
    """The overlap fixes the tile positions, so it must survive save/load."""

    def test_the_session_save_records_the_overlap(self):
        src = (SRC / "views/dialogs/led_2d_overview_result.py").read_text()
        assert '"tile_overlap_percent"' in src

    def test_a_session_without_a_recorded_overlap_does_not_assume_zero(self):
        """Missing means unknown; zero would silently shift every tile."""
        src = (SRC / "views/dialogs/led_2d_overview_result.py").read_text()
        assert 'get("tile_overlap_percent", 0)' not in src
        assert 'get("tile_overlap_percent", 0.0)' not in src

    def test_the_dataclass_default_is_a_real_overlap(self):
        pytest.importorskip("PyQt5")
        from py2flamingo.views.dialogs.led_2d_overview_dialog import ScanConfiguration

        assert (
            ScanConfiguration.__dataclass_fields__["tile_overlap_percent"].default > 0
        ), "defaulting to 0 overlap is the bug this all started from"
