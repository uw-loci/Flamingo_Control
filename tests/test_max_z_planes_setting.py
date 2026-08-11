"""Planes per tile is an acquisition setting, not a constant in the workflow.

Sweep time is `planes x per-plane cost`, so with the Z step this IS the cost of
a tile — the dominant term in a scan that took 2.2 hours on 2026-08-09. It was a
class constant, which meant a deep bounding box quietly cost several times more
per tile than a shallow one at the same settings, with nothing on the dialog
saying so.

The cap subsamples across the full Z range rather than truncating: coverage is
kept and only the Z resolution of the focus search drops. Truncating would stop
looking at the far half of the sample.

Run: QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest \
        tests/test_max_z_planes_setting.py -q
"""

from types import SimpleNamespace

import pytest

from py2flamingo.workflows.led_2d_overview_workflow import LED2DOverviewWorkflow


def _wf(config):
    wf = LED2DOverviewWorkflow.__new__(LED2DOverviewWorkflow)
    wf._config = config
    return wf


class TestTheAcquisitionValueIsUsed:
    def test_the_configured_cap_wins_over_the_class_constant(self):
        wf = _wf(SimpleNamespace(max_z_planes=3))
        assert wf._max_z_planes() == 3
        assert LED2DOverviewWorkflow.MAX_Z_PLANES_PER_TILE == 10

    def test_a_config_without_the_field_falls_back_to_the_constant(self):
        """An old session must keep behaving the way it did, not go unlimited."""
        wf = _wf(SimpleNamespace())
        assert wf._max_z_planes() == LED2DOverviewWorkflow.MAX_Z_PLANES_PER_TILE

    @pytest.mark.parametrize("bad", [0, -5, None, "lots"])
    def test_a_nonsense_value_falls_back_rather_than_disabling_the_cap(self, bad):
        wf = _wf(SimpleNamespace(max_z_planes=bad))
        assert wf._max_z_planes() == LED2DOverviewWorkflow.MAX_Z_PLANES_PER_TILE

    def test_it_actually_bounds_the_sweep(self):
        wf = _wf(SimpleNamespace(max_z_planes=4))
        planes = LED2DOverviewWorkflow._z_sweep_positions(
            0.0, 10.0, 0.25, True, wf._max_z_planes()
        )
        assert len(planes) == 4
        # Subsampled, not truncated: the far end is still visited.
        assert planes[0] == pytest.approx(0.0)
        assert planes[-1] == pytest.approx(10.0)


class TestItIsOnTheDialog:
    def _src(self, rel):
        from pathlib import Path

        return (
            Path(__file__).resolve().parents[1] / "src" / "py2flamingo" / rel
        ).read_text(encoding="utf-8")

    def test_the_dialog_has_a_control(self):
        body = self._src("views/dialogs/led_2d_overview_dialog.py")
        assert "self.max_z_planes = QSpinBox()" in body
        assert "max_z_planes=self.max_z_planes.value()" in body

    def test_the_config_carries_it(self):
        from py2flamingo.views.dialogs.led_2d_overview_dialog import ScanConfiguration

        assert "max_z_planes" in ScanConfiguration.__dataclass_fields__

    def test_a_saved_session_round_trips_it(self):
        body = self._src("views/dialogs/led_2d_overview_result.py")
        assert '"max_z_planes"' in body
        assert "max_z_planes=metadata" in body

    def test_only_the_fallback_helper_reads_the_constant(self):
        """The constant is a fallback now; the scan itself must ask the config.

        Structural, not line-counting: any function OTHER than `_max_z_planes`
        naming the constant is a scan path that would ignore the dialog.
        """
        import ast

        tree = ast.parse(self._src("workflows/led_2d_overview_workflow.py"))
        offenders = []
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef) or fn.name == "_max_z_planes":
                continue
            # A default argument is a fallback for a caller that omits the
            # value, not a scan path that ignores the config — every live
            # caller passes it explicitly. Only the BODY is interesting.
            body_nodes = [n for stmt in fn.body for n in ast.walk(stmt)]
            for node in body_nodes:
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "MAX_Z_PLANES_PER_TILE"
                ) or (
                    isinstance(node, ast.Name) and node.id == "MAX_Z_PLANES_PER_TILE"
                ):
                    offenders.append(fn.name)
        assert not offenders, (
            f"these read the constant instead of the acquisition's value, so "
            f"the dialog setting would not reach them: {sorted(set(offenders))}"
        )

    def test_both_scan_paths_ask_for_the_configured_cap(self):
        """Fast mode and the slow path must both pass it through."""
        body = self._src("workflows/led_2d_overview_workflow.py")
        assert body.count("self._max_z_planes()") >= 3, (
            "expected the fast-mode sweep, the slow path, and the up-front "
            "plane-count log to each use the configured cap"
        )
