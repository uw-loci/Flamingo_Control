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


def test_cavity_center_maps_to_stage_midrange(overlay):
    """The cavity centroid (134.125, -187.95, 678.50) in STEP frame must map
    to the middle of the stage travel ranges (stage_x~6.655, stage_y~5,
    stage_z~19.25) — that's how the offsets are calibrated so a sample at
    the default stage position renders near the cavity center.
    """
    sx, sy, sz = overlay._step_to_stage_mm((134.125, -187.95, 678.50))
    assert sx == pytest.approx(6.65, abs=1e-2)
    assert sy == pytest.approx(4.48, abs=1e-2)
    assert sz == pytest.approx(19.25, abs=1e-2)


def test_detector_lens_sits_behind_cavity_in_stage_frame(overlay):
    """The detection-objective lens (file_y=-218.39, outside the chamber
    body back face) maps to a stage_z position BEHIND the cavity in stage
    frame — correct, since the lens is fixed in space outside the chamber
    looking inward."""
    sx, sy, sz = overlay._step_to_stage_mm((134.13, -218.39, 680.65))
    # Should be in front-of-cavity stage_z (cavity stage_z spans ~[5.9, 32.6])
    assert sz < 0, f"detector lens should sit behind cavity, got stage_z={sz:.3f}"


def test_focal_point_stage_to_napari_centered(overlay):
    """The cavity centroid in stage coords maps somewhere within the napari
    display range (or close to it). The cavity is bigger than the
    rectangular voxel volume so this only checks one axis."""
    nz, ny, nx = overlay._stage_to_napari(6.65, 4.48, 19.25)
    assert nz > 0
    assert 100 < ny < 300
    assert 70 < nx < 160


def test_holder_at_cavity_center_clear_of_walls(overlay):
    """A point holder at the cavity center should be clear of all surfaces."""
    d = overlay.distance_to_holder(6.65, 4.48, 19.25, shaft_radius_mm=0.5)
    assert d > 5.0, f"expected >5 mm clearance at cavity center, got {d:.3f}"


def test_holder_offset_collides(overlay):
    """A holder placed far outside the cavity in X should report negative
    distance (collision/intrusion)."""
    d = overlay.distance_to_holder(50.0, 4.48, 19.25, shaft_radius_mm=0.5)
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


def test_sample_entry_port_renders_as_circle(overlay):
    """The front viewing port is a smaller circular cylinder (~r=7.63 mm),
    NOT a rectangle. It's just smaller than the illumination ports (r=10)."""
    feat = next(
        f
        for f in overlay._features_data["features"]
        if f["role"] == "sample_entry_port"
    )
    assert feat.get("display_as") != "rectangle"
    assert "rect_extents_step" not in feat
    assert feat["radius_mm"] == pytest.approx(7.63, abs=0.05)
    # Smaller than the illumination ports
    illum = next(
        f
        for f in overlay._features_data["features"]
        if f["role"] == "illumination_port_left"
    )
    assert feat["radius_mm"] < illum["radius_mm"]


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
    not depth-stacked rings. The top sample-entry hole uses this path."""
    overlay.viewer = _FakeViewer()
    overlay.add_layers(master_visible=True)
    layer = overlay.viewer.layers["STEP Top Sample-Entry Hole"]
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
    """Outer chamber body bounds must come from the actual chamber-body faces
    in the STEP file — excluding non-body features:
      - detection-objective lens sleeve (y down to -227.65)
      - octagonal base plate (z down to 628.82)
      - L/R mounting tabs / flanges (x out to 97.28 and 174.98)

    User-verified chamber body envelope:
      width  (X, between illumination mount faces): 31.7 mm  (118.28..149.98)
      depth  (Y, optical axis):                     43.0 mm  (-210.04..-167.02)
      height (Z, vertical):                         44.0 mm  ( 656.50..700.50)
    """
    feat = next(
        f
        for f in overlay._features_data["features"]
        if f["role"] == "chamber_outer_box"
    )
    bounds = feat["bounds_step"]
    assert bounds["x"][0] == pytest.approx(118.28, abs=0.5)
    assert bounds["x"][1] == pytest.approx(149.98, abs=0.5)
    assert bounds["y"][0] == pytest.approx(-210.04, abs=0.5)
    assert bounds["y"][1] == pytest.approx(-167.02, abs=0.5)
    assert bounds["z"][0] == pytest.approx(656.50, abs=0.5)
    assert bounds["z"][1] == pytest.approx(700.50, abs=0.5)
    # Sanity: dimensions match the user's eyeball measurements
    assert bounds["x"][1] - bounds["x"][0] == pytest.approx(31.70, abs=0.1)
    assert bounds["y"][1] - bounds["y"][0] == pytest.approx(43.02, abs=0.05)
    assert bounds["z"][1] - bounds["z"][0] == pytest.approx(44.00, abs=0.05)


def test_chamber_cavity_matches_user_measurements(overlay):
    """Inner cavity dimensions must match the user's physical measurements:
    19.3 mm wide (excitation-to-excitation), 26.7 mm front-to-back, 44 mm tall.
    """
    feat = next(
        f for f in overlay._features_data["features"] if f["role"] == "chamber_cavity"
    )
    b = feat["bounds_step"]
    width = b["x"][1] - b["x"][0]
    depth = b["y"][1] - b["y"][0]
    height = b["z"][1] - b["z"][0]
    assert width == pytest.approx(
        19.35, abs=0.1
    ), f"cavity width {width}, expected ~19.3"
    assert depth == pytest.approx(
        26.70, abs=0.1
    ), f"cavity depth {depth}, expected 26.7"
    assert height == pytest.approx(
        44.00, abs=0.1
    ), f"cavity height {height}, expected 44"
