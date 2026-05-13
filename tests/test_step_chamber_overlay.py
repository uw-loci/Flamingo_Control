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
    """The illumination-port centroid in STEP frame is the cavity center
    in stage frame. The new transform puts the detector at stage_z≈12.5
    (= where the rectangular yellow Objective sits), so the cavity center
    shifts forward to stage_z≈40.95.
    """
    sx, sy, sz = overlay._step_to_stage_mm((134.13, -189.94, 681.02))
    assert sx == pytest.approx(6.655, abs=1e-3)
    assert sy == pytest.approx(7.0, abs=1e-3)
    # Cavity centroid in stage Z (depth) — depends on transform offsets in YAML
    assert sz == pytest.approx(40.95, abs=0.5)


def test_detector_aligns_with_existing_yellow_objective(overlay):
    """The STEP detection_objective_port must land at the same stage_z as
    the existing rectangular Yellow Objective ring (stage_z=12.5).
    """
    sx, sy, sz = overlay._step_to_stage_mm((134.13, -218.39, 680.65))
    assert sz == pytest.approx(12.5, abs=0.1), (
        f"detector should be at the rectangular Objective stage_z (12.5), "
        f"got {sz:.3f}"
    )


def test_focal_point_stage_to_napari_centered(overlay):
    """The cavity centroid maps somewhere within napari display range."""
    nz, ny, nx = overlay._stage_to_napari(6.655, 7.0, 40.95)
    # display dims (Z, Y, X) ≈ (270, 280, 226) at voxel_size_um=50
    # nz may be off the far side of the rectangular subset (cavity is bigger)
    assert nz > 0
    assert 100 < ny < 200
    assert 70 < nx < 160


def test_holder_at_cavity_center_clear_of_walls(overlay):
    """A point holder at the cavity center should be clear of all surfaces."""
    d = overlay.distance_to_holder(6.655, 7.0, 40.95, shaft_radius_mm=0.5)
    assert d > 5.0, f"expected >5 mm clearance at cavity center, got {d:.3f}"


def test_holder_offset_collides(overlay):
    """A holder placed far outside the cavity in X should report negative
    distance (collision/intrusion)."""
    d = overlay.distance_to_holder(50.0, 7.0, 40.95, shaft_radius_mm=0.5)
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


def test_sample_entry_port_renders_as_rectangle(overlay):
    """The front viewing port should render as a flat rectangle outline, not
    rings (the production chamber has a rectangular glass window there)."""
    feat = next(
        f
        for f in overlay._features_data["features"]
        if f["role"] == "sample_entry_port"
    )
    assert feat.get("display_as") == "rectangle"
    assert "rect_extents_step" in feat
    assert "file_x" in feat["rect_extents_step"]
    assert "file_z" in feat["rect_extents_step"]


def test_top_sample_entry_renders_as_rectangle(overlay):
    """The top sample-entry hole is rectangular in the production chamber."""
    feat = next(
        f
        for f in overlay._features_data["features"]
        if f["role"] == "sample_entry_top_hole"
    )
    assert feat.get("display_as") == "rectangle"
    ext = feat["rect_extents_step"]
    assert "file_x" in ext
    assert "file_y" in ext


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


def test_rectangle_render_emits_single_closed_path(overlay):
    """The rectangle render path produces ONE closed polyline (5 points),
    not depth-stacked rings."""
    overlay.viewer = _FakeViewer()
    overlay.add_layers(master_visible=True)
    layer = overlay.viewer.layers["STEP Front Viewing Port"]
    # rect render: one path of 5 points (closed rectangle)
    assert len(layer.data) == 1
    assert len(layer.data[0]) == 5  # 4 corners + closing point


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
