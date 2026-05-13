"""Tests for StepChamberOverlay coordinate transform and collision API.

These tests do not require napari/PyQt5 — they exercise the loader and
geometric routines directly.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_overlay_module():
    """Import the overlay module bypassing the views package (which pulls PyQt5)."""
    path = REPO_ROOT / "src" / "py2flamingo" / "views" / "step_chamber_overlay.py"
    spec = importlib.util.spec_from_file_location("step_chamber_overlay", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["step_chamber_overlay"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_visualization_config() -> dict:
    import yaml

    path = (
        REPO_ROOT / "src" / "py2flamingo" / "configs" / "visualization_3d_config.yaml"
    )
    with path.open() as f:
        return yaml.safe_load(f)


@pytest.fixture
def overlay():
    mod = _load_overlay_module()
    cfg = _load_visualization_config()
    yaml_path = (
        REPO_ROOT / "src" / "py2flamingo" / "configs" / "step_chamber_features.yaml"
    )
    if not yaml_path.exists():
        pytest.skip("step_chamber_features.yaml not generated yet")
    ov = mod.StepChamberOverlay(
        viewer=None,
        features_yaml_path=yaml_path,
        config=cfg,
        invert_x=True,
    )
    assert ov.load(), "overlay YAML failed to load"
    return ov


def test_yaml_loads_all_features(overlay):
    roles = [f["role"] for f in overlay._features_data["features"]]
    assert "chamber_outer_box" in roles
    assert "chamber_cavity" in roles
    assert "detection_objective_port" in roles
    assert "sample_entry_port" in roles
    assert "illumination_port_left" in roles
    assert "illumination_port_right" in roles


def test_focal_point_step_to_stage_round_trip(overlay):
    """The centroid of the two illumination ports in STEP frame must map to
    the existing rectangular focal point in stage frame: (6.655, 7.0, 19.25).
    """
    sx, sy, sz = overlay._step_to_stage_mm((134.13, -189.94, 681.02))
    assert sx == pytest.approx(6.655, abs=1e-3)
    assert sy == pytest.approx(7.0, abs=1e-3)
    assert sz == pytest.approx(19.25, abs=1e-3)


def test_focal_point_stage_to_napari_centered(overlay):
    """Focal stage point lands roughly in the middle of the napari display."""
    nz, ny, nx = overlay._stage_to_napari(6.655, 7.0, 19.25)
    # display dims (Z, Y, X) ≈ (270, 280, 226) at voxel_size_um=50
    assert 100 < nz < 200
    assert 100 < ny < 200
    assert 70 < nx < 160


def test_holder_at_focal_clear_of_walls(overlay):
    """A point holder at the focal point should be clear of all surfaces."""
    d = overlay.distance_to_holder(6.655, 7.0, 19.25, shaft_radius_mm=0.5)
    assert d > 5.0, f"expected >5 mm clearance at focal, got {d:.3f}"


def test_holder_offset_collides(overlay):
    """A holder placed far outside the cavity in X should report negative
    distance (collision/intrusion)."""
    d = overlay.distance_to_holder(50.0, 7.0, 19.25, shaft_radius_mm=0.5)
    assert d < 0, f"expected negative (collision) at X=50, got {d:.3f}"


def test_detector_port_radius_matches_user_spec(overlay):
    """The detector port should be the 33 mm dia (r=16.6 mm) bore the user
    described, on the back face of the chamber."""
    feat = next(
        f
        for f in overlay._features_data["features"]
        if f["role"] == "detection_objective_port"
    )
    assert feat["radius_mm"] == pytest.approx(16.6, abs=0.05)
    # Axis along -Y in file frame (back direction)
    assert feat["axis"][1] == -1


def test_sample_entry_port_carries_real_world_override(overlay):
    """The sample-entry port should carry a real_world_override block so the
    user can specify the production rounded-rectangle slot."""
    feat = next(
        f
        for f in overlay._features_data["features"]
        if f["role"] == "sample_entry_port"
    )
    assert feat["real_world_shape"] == "rounded_rectangle"
    assert "real_world_override" in feat


def test_illumination_ports_symmetric(overlay):
    """Illumination ports are a symmetric pair on opposite ±X faces."""
    left = next(
        f
        for f in overlay._features_data["features"]
        if f["role"] == "illumination_port_left"
    )
    right = next(
        f
        for f in overlay._features_data["features"]
        if f["role"] == "illumination_port_right"
    )
    assert left["radius_mm"] == pytest.approx(right["radius_mm"], abs=0.05)
    # Centers at same Y, Z, mirrored X around chamber center (~134.13)
    assert left["center_step"][1] == pytest.approx(right["center_step"][1], abs=0.5)
    assert left["center_step"][2] == pytest.approx(right["center_step"][2], abs=0.5)
    midpoint_x = (left["center_step"][0] + right["center_step"][0]) / 2
    assert midpoint_x == pytest.approx(134.13, abs=0.5)


class _FakeShapesLayer:
    def __init__(self, data, **kwargs):
        self.data = data
        self.kwargs = kwargs
        self.visible = True


class _FakeViewer:
    """Minimal fake napari viewer for exercising the render code paths."""

    def __init__(self):
        self.layers: dict = {}

    def add_shapes(self, data, **kwargs):
        name = kwargs.get("name", f"shape_{len(self.layers)}")
        layer = _FakeShapesLayer(data, **kwargs)
        self.layers[name] = layer
        return layer

    def add_surface(self, data, **kwargs):
        name = kwargs.get("name", f"surface_{len(self.layers)}")
        layer = _FakeShapesLayer(data, **kwargs)
        self.layers[name] = layer
        return layer


def test_stadium_slot_renders_when_override_enabled(overlay):
    """Turning on real_world_override on the sample_entry_port should make the
    renderer emit a stadium-shape polyline instead of a circle."""
    # Locate the feature dict and enable the override with realistic slot dims
    for f in overlay._features_data["features"]:
        if f["role"] == "sample_entry_port":
            f["real_world_override"] = {
                "enabled": True,
                # Plausible slot: 20 mm long along Z, 10 mm wide along X
                "slot_x_extent_mm": [129.13, 139.13],
                "slot_z_extent_mm": [670.65, 690.65],
            }
            break
    overlay.viewer = _FakeViewer()
    overlay.add_layers(master_visible=True)
    # The stadium renderer attaches the slot ring(s) under the same layer name
    assert "STEP Sample-Entry / Front Port" in overlay.viewer.layers
    layer = overlay.viewer.layers["STEP Sample-Entry / Front Port"]
    # Stadium rendering: 3 depth-stacked closed polylines
    assert len(layer.data) == 3
    # Each ring is 2 caps × 19 arc points + 2 straight edges + closure ≈ 40 pts
    assert all(len(ring) >= 38 for ring in layer.data)


def test_bulk_with_holes_has_more_triangles_than_solid_box(overlay):
    """The bulk renderer should punch holes for the 4 large bores (detection
    objective + sample entry + 2 illumination ports). That produces many more
    triangles than a plain 12-triangle box."""
    overlay.viewer = _FakeViewer()
    overlay.add_layers(master_visible=True)
    bulk = overlay.viewer.layers["STEP Chamber Bulk"]
    verts, faces, _values = bulk.data
    # 4 faces with bores × N_ARC=28 quad strips × 2 triangles + 2 faces solid × 2
    # = 4 × 56 + 4 = 228 triangles. Strictly: at least 100 triangles
    assert len(faces) > 100, f"expected many triangles, got {len(faces)}"
    # Sanity: bulk verts must include points away from the AABB corners
    # (the radial fan creates points along the arcs)
    assert len(verts) > 24


def test_cavity_renders_three_layers(overlay):
    """chamber_cavity must produce a wireframe + back wall + bottom wall."""
    overlay.viewer = _FakeViewer()
    overlay.add_layers(master_visible=True)
    for name in (
        "STEP Cavity Wireframe",
        "STEP Cavity Back Wall",
        "STEP Cavity Bottom Wall",
    ):
        assert name in overlay.viewer.layers, f"missing layer: {name}"


def test_cavity_master_toggle_unit(overlay):
    """set_feature_visible('chamber_cavity', False) hides all three layers."""
    overlay.viewer = _FakeViewer()
    overlay.add_layers(master_visible=True)
    overlay.set_feature_visible("chamber_cavity", False)
    for name in (
        "STEP Cavity Wireframe",
        "STEP Cavity Back Wall",
        "STEP Cavity Bottom Wall",
    ):
        assert overlay.viewer.layers[name].visible is False
    overlay.set_feature_visible("chamber_cavity", True)
    for name in (
        "STEP Cavity Wireframe",
        "STEP Cavity Back Wall",
        "STEP Cavity Bottom Wall",
    ):
        assert overlay.viewer.layers[name].visible is True


def test_chamber_outer_box_uses_outer_planes(overlay):
    """Outer body bounds must come from the actual outer face planes, not
    from the dense cavity interior."""
    feat = next(
        f
        for f in overlay._features_data["features"]
        if f["role"] == "chamber_outer_box"
    )
    bounds = feat["bounds_step"]
    # Expected outer bounds (from the user's manually verified geometry)
    assert bounds["x"][0] == pytest.approx(97.28, abs=0.5)
    assert bounds["x"][1] == pytest.approx(174.98, abs=0.5)
    assert bounds["y"][0] == pytest.approx(-227.65, abs=0.5)
    assert bounds["y"][1] == pytest.approx(-167.02, abs=0.5)
    assert bounds["z"][0] == pytest.approx(628.82, abs=0.5)
    assert bounds["z"][1] == pytest.approx(700.5, abs=2.0)
