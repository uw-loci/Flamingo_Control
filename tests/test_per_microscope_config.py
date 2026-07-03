"""Per-microscope visualization config selection (two fake microscopes).

A `microscopes:` map in the viz config lets each scope (matched by
get_microscope_name()) override `orientation`, camera, chamber, etc. via a
deep-merged overlay. Storage factory and Sample View resolve through the same
`resolve_visualization_config`, so they always agree on the active orientation.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from py2flamingo.visualization.axis_orientation import AxisOrientation  # noqa: E402
from py2flamingo.visualization.voxel_storage_factory import (  # noqa: E402
    create_voxel_storage,
    resolve_visualization_config,
)

# Two fake microscopes: one legacy (current scope, no orientation override),
# one ASLM (90-deg swap of X/Z) — plus a per-scope camera-angle override to
# exercise the deep-merge not clobbering sibling display keys.
_ASLM_ORI = {
    "depth": {"stage": "x", "flip": False},
    "vertical": {"stage": "y", "flip": True},
    "horizontal": {"stage": "z", "flip": False},
}


def _base_config():
    """A minimal but complete viz config with a two-scope `microscopes:` map."""
    import copy

    real = yaml.safe_load(
        open(_SRC / "py2flamingo" / "configs" / "visualization_3d_config.yaml")
    )
    cfg = copy.deepcopy(real)
    cfg.pop("orientation", None)  # base = legacy
    cfg["microscopes"] = {
        "scope-legacy": {"display": {"default_camera_zoom": 0.9}},
        "ASLM-25x": {
            "orientation": _ASLM_ORI,
            "display": {"default_camera_angles": [10, 20, 30]},
            "stage_control": {
                "direction_hints": {"x": "Toward User <-> Toward Excitation Arm"}
            },
        },
    }
    return cfg


def _write(cfg) -> str:
    d = tempfile.mkdtemp()
    p = os.path.join(d, "viz.yaml")
    yaml.safe_dump(cfg, open(p, "w"))
    return p


class TestResolveOverlay(unittest.TestCase):
    def setUp(self):
        self.path = _write(_base_config())

    def _ori(self, name):
        cfg = resolve_visualization_config(microscope_name=name, config_path=self.path)
        return AxisOrientation.from_config(cfg, invert_x=True)

    def test_aslm_scope_gets_permuted_orientation(self):
        ori = self._ori("ASLM-25x")
        self.assertEqual(ori.stage_axis_for(0), "x")  # depth = stage X
        self.assertEqual(ori.stage_axis_for(2), "z")  # horizontal = stage Z

    def test_legacy_scope_is_legacy_orientation(self):
        ori = self._ori("scope-legacy")
        self.assertEqual(ori.stage_axis_for(0), "z")  # depth = stage Z (legacy)
        self.assertEqual(ori.stage_axis_for(2), "x")

    def test_unknown_and_none_scope_fall_back_to_base(self):
        for name in (None, "no-such-scope"):
            ori = self._ori(name)
            self.assertEqual(ori.stage_axis_for(0), "z")

    def test_case_insensitive_match(self):
        ori = self._ori("aslm-25X")
        self.assertEqual(ori.stage_axis_for(0), "x")

    def test_microscopes_key_stripped_and_deep_merge(self):
        cfg = resolve_visualization_config("ASLM-25x", config_path=self.path)
        # The map itself must not leak to consumers.
        self.assertNotIn("microscopes", cfg)
        # Overlay adds default_camera_angles WITHOUT dropping base display keys.
        self.assertEqual(cfg["display"]["default_camera_angles"], [10, 20, 30])
        self.assertIn("voxel_size_um", cfg["display"])  # sibling preserved

    def test_per_scope_direction_hints_merge_without_clobbering(self):
        aslm = resolve_visualization_config("ASLM-25x", config_path=self.path)
        legacy = resolve_visualization_config("scope-legacy", config_path=self.path)
        # ASLM gets its slider hint; legacy has none (viewer falls back).
        self.assertEqual(
            aslm["stage_control"]["direction_hints"]["x"],
            "Toward User <-> Toward Excitation Arm",
        )
        self.assertNotIn("direction_hints", legacy["stage_control"])
        # The direction_hints overlay must NOT drop sibling stage_control keys.
        self.assertIn("x_range_mm", aslm["stage_control"])
        self.assertIn("invert_x_default", aslm["stage_control"])


class TestTwoScopesEndToEnd(unittest.TestCase):
    """The two scopes build storage whose display dims/orientation differ."""

    def setUp(self):
        self.path = _write(_base_config())

    def test_storage_dims_differ_per_scope(self):
        legacy = create_voxel_storage(
            config_path=self.path, microscope_name="scope-legacy"
        )
        aslm = create_voxel_storage(config_path=self.path, microscope_name="ASLM-25x")
        self.assertIsNotNone(legacy)
        self.assertIsNotNone(aslm)
        # Legacy: depth=Z-extent, horizontal=X-extent. ASLM swaps them.
        ld = legacy.voxel_storage.display_dims
        ad = aslm.voxel_storage.display_dims
        self.assertNotEqual(ld, ad)
        self.assertEqual((ld[0], ld[2]), (ad[2], ad[0]))  # depth<->horizontal swap
        # And a +Z stage move drives horizontal on ASLM, depth on legacy.
        self.assertAlmostEqual(
            legacy.voxel_storage.config.axis_orientation().delta_offset(0, 0, 1)[0], 1
        )
        self.assertAlmostEqual(
            aslm.voxel_storage.config.axis_orientation().delta_offset(0, 0, 1)[2], 1
        )


class TestPlanMicroscopeChange(unittest.TestCase):
    """The pure connect-time decision (re-init silently / prompt / do nothing)."""

    def setUp(self):
        from py2flamingo.views.sample_view import plan_microscope_change

        self.plan = plan_microscope_change

    def test_same_scope_is_noop(self):
        self.assertEqual(self.plan("ASLM-25x", "ASLM-25x", True), "none")
        self.assertEqual(self.plan("ASLM-25x", "aslm-25X", True), "none")  # case-insens

    def test_no_new_name_is_noop(self):
        self.assertEqual(self.plan("A", None, True), "none")
        self.assertEqual(self.plan("A", "", True), "none")

    def test_change_without_data_reinits_silently(self):
        self.assertEqual(self.plan("A", "B", False), "reinit")

    def test_change_with_data_prompts(self):
        self.assertEqual(self.plan("A", "B", True), "ask")


if __name__ == "__main__":
    unittest.main()
