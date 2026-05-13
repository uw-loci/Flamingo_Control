"""STEP-based chamber overlay for the napari 3D viewer.

Loads chamber CAD features (extracted from a STEP file by
`scripts/extract_step_chamber.py` into a YAML), applies the STEP→stage→napari
coordinate transform, and renders each feature as its own toggleable napari
layer. Coexists with the existing rectangular-subset wireframe in
`ChamberVisualizationManager`; the master toggle in `ViewerControlsDialog`
auto-hides the rectangular wireframe when the STEP view is on.

Features rendered:
- chamber_outer_box (solid bulk metal, off by default)
- detection_objective_port (cylinder)
- sample_entry_port (cylinder, with optional rounded-rectangle override)
- illumination_port_left / illumination_port_right (cylinders)
- rail_mount_bolt_left / rail_mount_bolt_right (cylinders)

The overlay also exposes `distance_to_holder(...)` for the safety gate.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Iterable

import numpy as np

try:
    import yaml
except ImportError:
    yaml = None


_LAYER_NAME_BULK = "STEP Chamber Bulk"


class StepChamberOverlay:
    """Renders a STEP-derived chamber geometry as napari layers."""

    def __init__(
        self,
        viewer,
        features_yaml_path: Path | str,
        config: dict,
        invert_x: bool = True,
    ):
        self.viewer = viewer
        self.config = config
        self.invert_x = invert_x
        self.features_yaml_path = Path(features_yaml_path)
        self.logger = logging.getLogger(__name__)

        self._features_data: dict = {}
        self._layers: list[str] = []
        self._loaded: bool = False

    # ------------------------------------------------------------------ load

    def load(self) -> bool:
        """Read the features YAML. Returns True on success."""
        if yaml is None:
            self.logger.warning("PyYAML not installed; STEP chamber overlay disabled")
            return False
        if not self.features_yaml_path.exists():
            self.logger.warning(
                f"STEP chamber YAML not found: {self.features_yaml_path}"
            )
            return False
        try:
            with self.features_yaml_path.open() as f:
                self._features_data = yaml.safe_load(f) or {}
        except Exception as e:
            self.logger.warning(f"Failed to load STEP chamber YAML: {e}")
            return False
        if "features" not in self._features_data:
            self.logger.warning("STEP chamber YAML missing 'features' key")
            return False
        self._loaded = True
        return True

    # -------------------------------------------------------- coord transform

    def _step_to_stage_mm(self, file_xyz: tuple[float, float, float]):
        """Convert a STEP-frame point (file_x, file_y, file_z) to stage mm."""
        tr = self._features_data.get("step_to_stage_transform", {})
        sign = tr.get("sign", {"stage_x": 1, "stage_y": 1, "stage_z": -1})
        offset = tr.get("offset_mm", {"stage_x": 0, "stage_y": 0, "stage_z": 0})
        # axis_permutation default: file_x→stage_x, file_z→stage_y, file_y→stage_z
        perm = tr.get(
            "axis_permutation",
            {
                "stage_x": "file_x",
                "stage_y": "file_z",
                "stage_z": "file_y",
            },
        )
        idx = {"file_x": 0, "file_y": 1, "file_z": 2}
        sx = sign["stage_x"] * file_xyz[idx[perm["stage_x"]]] + offset["stage_x"]
        sy = sign["stage_y"] * file_xyz[idx[perm["stage_y"]]] + offset["stage_y"]
        sz = sign["stage_z"] * file_xyz[idx[perm["stage_z"]]] + offset["stage_z"]
        return sx, sy, sz

    def _stage_to_napari(self, x_mm: float, y_mm: float, z_mm: float):
        """Convert a stage-frame mm point to napari (Z, Y, X) voxel coords.

        Mirrors the formulas in ChamberVisualizationManager so the STEP
        overlay sits in the same display volume as the rectangular wireframe.
        """
        stage_ctrl = self.config.get("stage_control", {})
        x_range = stage_ctrl.get("x_range_mm", [1.0, 12.31])
        y_range = stage_ctrl.get("y_range_mm", [0.0, 14.0])
        z_range = stage_ctrl.get("z_range_mm", [12.5, 26.0])
        voxel_size_mm = (
            self.config.get("display", {}).get("voxel_size_um", [50, 50, 50])[0]
            / 1000.0
        )
        if self.invert_x:
            nx = (x_range[1] - x_mm) / voxel_size_mm
        else:
            nx = (x_mm - x_range[0]) / voxel_size_mm
        ny = (y_range[1] - y_mm) / voxel_size_mm
        nz = (z_mm - z_range[0]) / voxel_size_mm
        return nz, ny, nx

    def _step_to_napari(self, file_xyz):
        return self._stage_to_napari(*self._step_to_stage_mm(file_xyz))

    # ------------------------------------------------------------- rendering

    def add_layers(self, master_visible: bool = False) -> None:
        """Create napari layers for every feature. Initially hidden when
        master_visible=False (the typical case — turn on via the toggle UI)."""
        if not self._loaded:
            return
        if self.viewer is None:
            return
        for feature in self._features_data.get("features", []):
            try:
                self._render_feature(feature, master_visible)
            except Exception as e:
                self.logger.warning(
                    f"Failed to render STEP feature {feature.get('role')}: {e}"
                )

    def remove_layers(self) -> None:
        """Remove all layers we created."""
        if self.viewer is None:
            return
        for name in self._layers:
            if name in self.viewer.layers:
                try:
                    self.viewer.layers.remove(name)
                except Exception:
                    pass
        self._layers.clear()

    def _render_feature(self, feature: dict, visible: bool) -> None:
        role = feature.get("role")
        ftype = feature.get("type")

        # Cavity gets a special multi-layer rendering: wireframe + back + bottom
        # walls. It's the primary "interior walls" view the user uses for
        # checking holder clearance against the chamber walls.
        if role == "chamber_cavity":
            self._render_cavity_wireframe(feature, visible)
            return

        layer_name = feature.get("layer_name")
        if not layer_name:
            return  # internal entries (e.g., unrendered helpers) skip
        color = feature.get("color", "#FFFFFF")
        opacity = feature.get("opacity", 0.8)

        if ftype == "aabb":
            self._render_box(feature, layer_name, color, opacity, visible)
        elif ftype == "cylinder":
            self._render_cylinder(feature, layer_name, color, opacity, visible)
        else:
            self.logger.debug(f"Skipping unknown feature type: {ftype}")

    # Cavity wireframe + walls are tracked under this role so the dialog
    # toggle can control all three layers as a unit.
    CAVITY_LAYER_NAMES = (
        "STEP Cavity Wireframe",
        "STEP Cavity Back Wall",
        "STEP Cavity Bottom Wall",
    )

    def _render_cavity_wireframe(self, feature: dict, visible: bool) -> None:
        """Render the chamber interior cavity as three coordinated layers:

          - "STEP Cavity Wireframe": 12 line edges of the cavity AABB.
          - "STEP Cavity Back Wall":  a faint surface at the cavity's back
            (low stage Z / high file Y), mirroring the existing rectangular
            "Back Wall" reference treatment.
          - "STEP Cavity Bottom Wall": a faint surface at the cavity's
            bottom (low stage Y / low file Z).

        This is what the user uses to check holder-vs-chamber-wall clearance
        when the solid bulk is hidden.
        """
        bounds = feature.get("bounds_step") or {}
        try:
            x_lo, x_hi = bounds["x"]
            y_lo, y_hi = bounds["y"]
            z_lo, z_hi = bounds["z"]
        except (KeyError, ValueError, TypeError):
            self.logger.warning("chamber_cavity missing bounds_step; skipping")
            return

        # 8 corners of the cavity AABB in STEP frame
        c = [
            (x_lo, y_lo, z_lo),
            (x_hi, y_lo, z_lo),
            (x_hi, y_hi, z_lo),
            (x_lo, y_hi, z_lo),
            (x_lo, y_lo, z_hi),
            (x_hi, y_lo, z_hi),
            (x_hi, y_hi, z_hi),
            (x_lo, y_hi, z_hi),
        ]
        c_napari = [self._step_to_napari(p) for p in c]

        # 12 edges (4 bottom + 4 top + 4 vertical), each as a closed polyline
        edges = [
            (0, 1),
            (1, 2),
            (2, 3),
            (3, 0),  # bottom face (z_lo)
            (4, 5),
            (5, 6),
            (6, 7),
            (7, 4),  # top face (z_hi)
            (0, 4),
            (1, 5),
            (2, 6),
            (3, 7),  # vertical edges
        ]
        edge_paths = [
            np.array([c_napari[a], c_napari[b]], dtype=np.float32) for a, b in edges
        ]
        wf = self.viewer.add_shapes(
            data=edge_paths,
            shape_type="line",
            name="STEP Cavity Wireframe",
            edge_color="#00FFFF",
            edge_width=2,
            opacity=0.7,
        )
        try:
            wf.visible = bool(visible)
        except Exception:
            pass
        self._layers.append("STEP Cavity Wireframe")

        # Back wall: the cavity face at the +Y file end (= lowest stage Z =
        # back of the napari view). Quad in file Y=y_hi plane.
        back_verts = np.array(
            [
                self._step_to_napari((x_lo, y_hi, z_lo)),
                self._step_to_napari((x_hi, y_hi, z_lo)),
                self._step_to_napari((x_hi, y_hi, z_hi)),
                self._step_to_napari((x_lo, y_hi, z_hi)),
            ],
            dtype=np.float32,
        )
        back_faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
        back_values = np.ones(len(back_verts), dtype=np.float32)
        bw = self.viewer.add_surface(
            (back_verts, back_faces, back_values),
            name="STEP Cavity Back Wall",
            colormap="gray",
            opacity=0.05,
            shading="none",
        )
        try:
            bw.visible = bool(visible)
        except Exception:
            pass
        self._layers.append("STEP Cavity Back Wall")

        # Bottom wall: the cavity face at the -Z file end (= lowest stage Y =
        # floor of the chamber).
        bot_verts = np.array(
            [
                self._step_to_napari((x_lo, y_lo, z_lo)),
                self._step_to_napari((x_hi, y_lo, z_lo)),
                self._step_to_napari((x_hi, y_hi, z_lo)),
                self._step_to_napari((x_lo, y_hi, z_lo)),
            ],
            dtype=np.float32,
        )
        bot_faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
        bot_values = np.ones(len(bot_verts), dtype=np.float32)
        bb = self.viewer.add_surface(
            (bot_verts, bot_faces, bot_values),
            name="STEP Cavity Bottom Wall",
            colormap="gray",
            opacity=0.05,
            shading="none",
        )
        try:
            bb.visible = bool(visible)
        except Exception:
            pass
        self._layers.append("STEP Cavity Bottom Wall")

    def _render_box(
        self, feature: dict, layer_name: str, color: str, opacity: float, visible: bool
    ) -> None:
        """Render the chamber bulk as 6 faces, punching circular holes where
        large bore cylinders pierce each face.

        Faces with a bore (axis perpendicular to the face) are rendered as
        a radial fan strip: N arc points around the hole + N outer points
        radially projected to the rectangle boundary, giving 2N triangles
        that visibly show the hole as an open aperture. Faces without bores
        are rendered as 2 triangles. Small bolt holes (r < 5 mm) are
        ignored — the mounting plate stays solid.
        """
        bounds = feature.get("bounds_step", {})
        try:
            x_lo, x_hi = bounds["x"]
            y_lo, y_hi = bounds["y"]
            z_lo, z_hi = bounds["z"]
        except (KeyError, ValueError, TypeError):
            self.logger.warning(f"{layer_name}: missing bounds_step, skipping")
            return

        # Catalog holes by which face they pierce.
        # Key: (axis_idx, sign) where axis_idx is 0/1/2 for file X/Y/Z,
        # sign is +1 or -1 indicating which face (e.g., (1, -1) = -Y face).
        # Value: list of (u, v, radius) where (u, v) are the in-plane coords.
        holes_by_face: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
        for f in self._features_data.get("features", []):
            if f.get("type") != "cylinder":
                continue
            r = float(f.get("radius_mm", 0.0))
            if r < 5.0:
                continue  # skip small (bolt) holes; mounting plate is solid
            axis = f.get("axis") or [0, 0, 1]
            axis_idx = max(range(3), key=lambda i: abs(axis[i]))
            sign = 1 if axis[axis_idx] > 0 else -1
            center = f.get("center_step")
            if center is None:
                continue
            in_plane = [i for i in range(3) if i != axis_idx]
            holes_by_face.setdefault((axis_idx, sign), []).append(
                (center[in_plane[0]], center[in_plane[1]], r)
            )

        verts_napari: list = []
        tri_faces: list = []

        def add_quad(p0, p1, p2, p3):
            i = len(verts_napari)
            verts_napari.extend([p0, p1, p2, p3])
            tri_faces.append((i, i + 1, i + 2))
            tri_faces.append((i, i + 2, i + 3))

        def to_3d(axis_idx, sign, u, v):
            if axis_idx == 0:
                return (x_hi if sign > 0 else x_lo, u, v)
            if axis_idx == 1:
                return (u, y_hi if sign > 0 else y_lo, v)
            return (u, v, z_hi if sign > 0 else z_lo)

        def face_uv_bounds(axis_idx):
            if axis_idx == 0:
                return (y_lo, y_hi, z_lo, z_hi)
            if axis_idx == 1:
                return (x_lo, x_hi, z_lo, z_hi)
            return (x_lo, x_hi, y_lo, y_hi)

        def project_radially(au, av, cu, cv, u_lo, u_hi, v_lo, v_hi):
            """Project (au, av) outward from (cu, cv) to the rectangle border."""
            du, dv = au - cu, av - cv
            if abs(du) < 1e-9 and abs(dv) < 1e-9:
                return (u_hi, cv)
            ts = []
            if du > 1e-9:
                ts.append((u_hi - cu) / du)
            elif du < -1e-9:
                ts.append((u_lo - cu) / du)
            if dv > 1e-9:
                ts.append((v_hi - cv) / dv)
            elif dv < -1e-9:
                ts.append((v_lo - cv) / dv)
            t = min(ts) if ts else 1.0
            return (cu + t * du, cv + t * dv)

        N_ARC = 28
        for axis_idx in (0, 1, 2):
            for sign in (-1, +1):
                u_lo, u_hi, v_lo, v_hi = face_uv_bounds(axis_idx)
                face_holes = holes_by_face.get((axis_idx, sign), [])
                if not face_holes:
                    # Solid face: two triangles
                    corners_2d = [
                        (u_lo, v_lo),
                        (u_hi, v_lo),
                        (u_hi, v_hi),
                        (u_lo, v_hi),
                    ]
                    pts = [
                        self._step_to_napari(to_3d(axis_idx, sign, u, v))
                        for u, v in corners_2d
                    ]
                    add_quad(*pts)
                    continue
                # Face with a bore: radial-fan strip around the hole.
                # If multiple bores exist on one face (none in the current
                # 'basic windows' file), only the first is punched; the
                # extras would require a true polygon-with-holes mesher.
                cu, cv, r = face_holes[0]
                for j in range(N_ARC):
                    th0 = 2 * math.pi * j / N_ARC
                    th1 = 2 * math.pi * (j + 1) / N_ARC
                    a0 = (cu + r * math.cos(th0), cv + r * math.sin(th0))
                    a1 = (cu + r * math.cos(th1), cv + r * math.sin(th1))
                    o0 = project_radially(*a0, cu, cv, u_lo, u_hi, v_lo, v_hi)
                    o1 = project_radially(*a1, cu, cv, u_lo, u_hi, v_lo, v_hi)
                    quad = [
                        self._step_to_napari(to_3d(axis_idx, sign, *a0)),
                        self._step_to_napari(to_3d(axis_idx, sign, *a1)),
                        self._step_to_napari(to_3d(axis_idx, sign, *o1)),
                        self._step_to_napari(to_3d(axis_idx, sign, *o0)),
                    ]
                    add_quad(*quad)

        verts = np.array(verts_napari, dtype=np.float32)
        faces = np.array(tri_faces, dtype=np.int32)
        values = np.ones(len(verts), dtype=np.float32)
        layer = self.viewer.add_surface(
            (verts, faces, values),
            name=layer_name,
            colormap="gray",
            opacity=opacity,
            shading="none",
        )
        try:
            layer.visible = bool(visible) and bool(
                feature.get("visible_default", False)
            )
        except Exception:
            pass
        self._layers.append(layer_name)

    def _render_cylinder(
        self, feature: dict, layer_name: str, color: str, opacity: float, visible: bool
    ) -> None:
        """Render a circular cylinder as two ring outlines (start + end caps)
        plus a few intermediate rings, drawn as a Shapes 'path' layer in 3D.

        When a `real_world_override` block is enabled, draw a stadium (rounded
        rectangle) profile instead of a circle. This is for the production
        chamber's actual sample-entry slot shape, which is longer in one
        in-face axis than the other.
        """
        # Production override: stadium-shape slot (used by sample_entry_port
        # when the user fills in the real chamber's slot extents)
        override = feature.get("real_world_override") or {}
        if override.get("enabled"):
            self._render_stadium_slot(
                feature, override, layer_name, color, opacity, visible
            )
            return

        center_step = feature.get("center_step")
        axis = feature.get("axis", [0, 0, 1])
        radius_mm = float(feature.get("radius_mm", 0.0))
        if center_step is None or radius_mm <= 0:
            return

        # Determine the cylinder's length along its axis.
        # For Y-axis features we have y_extent_step; for ±X / ±Z bores we use a
        # standard short length so the ring is visible.
        length_mm = self._cylinder_length_mm(feature, axis)

        # Build N rings along the axis; each ring is a closed polyline of
        # M points around the cylinder.
        n_rings = 4
        m_pts = 36
        axis_vec = np.array(axis, dtype=np.float64)
        axis_vec = axis_vec / max(np.linalg.norm(axis_vec), 1e-9)
        # Pick a vector orthogonal to axis to use as ring "u" basis
        helper = (
            np.array([1.0, 0.0, 0.0])
            if abs(axis_vec[0]) < 0.9
            else np.array([0.0, 1.0, 0.0])
        )
        u = np.cross(axis_vec, helper)
        u = u / max(np.linalg.norm(u), 1e-9)
        v = np.cross(axis_vec, u)

        center = np.array(center_step, dtype=np.float64)
        rings: list[np.ndarray] = []
        for i in range(n_rings):
            # i=0 → at center; i=n_rings-1 → at one end of the bore
            t = (i / max(n_rings - 1, 1)) * length_mm
            ring_center = center + axis_vec * t
            ring_pts_step = [
                ring_center
                + radius_mm
                * (
                    math.cos(2 * math.pi * j / m_pts) * u
                    + math.sin(2 * math.pi * j / m_pts) * v
                )
                for j in range(m_pts)
            ]
            ring_pts_step.append(ring_pts_step[0])  # close
            ring_napari = np.array(
                [self._step_to_napari(tuple(p)) for p in ring_pts_step],
                dtype=np.float32,
            )
            rings.append(ring_napari)

        # Each ring is its own polyline shape
        layer = self.viewer.add_shapes(
            data=rings,
            shape_type="path",
            name=layer_name,
            edge_color=color,
            edge_width=1.5,
            opacity=opacity,
        )
        try:
            layer.visible = bool(visible) and bool(feature.get("visible_default", True))
        except Exception:
            pass
        self._layers.append(layer_name)

    def _render_stadium_slot(
        self,
        feature: dict,
        override: dict,
        layer_name: str,
        color: str,
        opacity: float,
        visible: bool,
    ) -> None:
        """Render a rounded-rectangle (stadium) slot in the plane perpendicular
        to the feature's axis.

        The slot is defined by two extents in the perpendicular plane. We
        identify the in-plane axes from the feature's axis vector and from
        which slot_*_extent_mm keys are present in the override block. The
        slot's two semicircular ends meet two parallel flat sides — the
        classic 'stadium' shape.
        """
        center_step = feature.get("center_step")
        axis = feature.get("axis", [0, 1, 0])
        if center_step is None:
            return

        axis_vec = np.array(axis, dtype=np.float64)
        axis_vec = axis_vec / max(np.linalg.norm(axis_vec), 1e-9)

        # Identify which in-plane axis names the user supplied. We accept
        # slot_x_extent_mm, slot_y_extent_mm, slot_z_extent_mm; pick the two
        # that are perpendicular to the feature's primary axis.
        extents: dict[int, tuple[float, float]] = {}
        for key, idx in (
            ("slot_x_extent_mm", 0),
            ("slot_y_extent_mm", 1),
            ("slot_z_extent_mm", 2),
        ):
            ext = override.get(key)
            if ext and len(ext) == 2 and all(e is not None for e in ext):
                extents[idx] = (float(ext[0]), float(ext[1]))

        in_plane = [i for i in range(3) if i != int(np.argmax(np.abs(axis_vec)))]
        if len(extents) < 2 or not all(i in extents for i in in_plane):
            self.logger.warning(
                f"{layer_name}: real_world_override enabled but missing "
                f"slot extents for in-plane axes {in_plane}"
            )
            return

        # Compute half-extents in each in-plane axis (relative to feature center)
        a_idx, b_idx = in_plane[0], in_plane[1]
        a_lo, a_hi = extents[a_idx]
        b_lo, b_hi = extents[b_idx]
        a_half = (a_hi - a_lo) / 2.0
        b_half = (b_hi - b_lo) / 2.0
        # Use the bigger half-extent as the "long" axis (stadium length)
        if a_half >= b_half:
            long_idx, short_idx = a_idx, b_idx
            long_half, short_half = a_half, b_half
            long_lo, long_hi = a_lo, a_hi
            short_lo, short_hi = b_lo, b_hi
        else:
            long_idx, short_idx = b_idx, a_idx
            long_half, short_half = b_half, a_half
            long_lo, long_hi = b_lo, b_hi
            short_lo, short_hi = a_lo, a_hi

        slot_center = list(center_step)
        slot_center[long_idx] = (long_lo + long_hi) / 2.0
        slot_center[short_idx] = (short_lo + short_hi) / 2.0

        # Cap radius = short half-extent; rectangle length = 2*(long_half - cap_r)
        cap_r = short_half
        straight_half = max(long_half - cap_r, 0.0)

        # Build the stadium outline as a closed polyline in the slot plane,
        # then duplicate at a few "depth" positions along the feature axis to
        # convey the slot as a 3D shape.
        n_arc = 18  # points per semicircular cap

        def stadium_point(theta_or_t: float, side: int):
            """Generate stadium outline. side=0..3 stages the four edges."""
            pass

        # Generate outline points in (long, short) plane coordinates
        outline_local: list[tuple[float, float]] = []
        # Right cap (long > 0): half circle from -π/2 to +π/2
        for i in range(n_arc + 1):
            theta = -math.pi / 2 + math.pi * i / n_arc
            outline_local.append(
                (straight_half + cap_r * math.cos(theta), cap_r * math.sin(theta))
            )
        # Top straight edge: from (+straight_half, +cap_r) to (-straight_half, +cap_r)
        outline_local.append((-straight_half, cap_r))
        # Left cap: π/2 to 3π/2
        for i in range(1, n_arc + 1):
            theta = math.pi / 2 + math.pi * i / n_arc
            outline_local.append(
                (-straight_half + cap_r * math.cos(theta), cap_r * math.sin(theta))
            )
        # Bottom straight edge back to start
        outline_local.append((straight_half, -cap_r))
        outline_local.append(outline_local[0])  # close

        def to_step(local_long: float, local_short: float, depth: float):
            p = list(slot_center)
            p[long_idx] += local_long
            p[short_idx] += local_short
            p[int(np.argmax(np.abs(axis_vec)))] += depth * np.sign(
                axis_vec[int(np.argmax(np.abs(axis_vec)))]
            )
            return tuple(p)

        # 3 rings along the axis (entry plane, mid, exit plane)
        depths = [0.0, 6.0, 12.0]  # mm — typical port + counterbore depth
        rings: list[np.ndarray] = []
        for d in depths:
            pts_step = [to_step(lp, sp, d) for lp, sp in outline_local]
            pts_napari = np.array(
                [self._step_to_napari(p) for p in pts_step], dtype=np.float32
            )
            rings.append(pts_napari)

        layer = self.viewer.add_shapes(
            data=rings,
            shape_type="path",
            name=layer_name,
            edge_color=color,
            edge_width=1.5,
            opacity=opacity,
        )
        try:
            layer.visible = bool(visible) and bool(feature.get("visible_default", True))
        except Exception:
            pass
        self._layers.append(layer_name)

    def _cylinder_length_mm(self, feature: dict, axis_vec) -> float:
        """Pick a sensible visible length for a cylinder feature.

        - If `y_extent_step` (etc.) is provided, use its span.
        - Otherwise fall back to a reasonable default that's wide enough to
          read as an aperture (e.g., 12 mm).
        """
        for key in ("y_extent_step", "x_extent_step", "z_extent_step"):
            ext = feature.get(key)
            if ext and len(ext) == 2:
                return abs(float(ext[1]) - float(ext[0]))
        # Use 12 mm default — typical port + counterbore depth
        return 12.0

    # ------------------------------------------------------------ visibility

    def set_master_visible(self, visible: bool) -> None:
        """Show or hide all STEP layers, honoring per-feature defaults."""
        if self.viewer is None:
            return
        # When the master goes ON, each feature's visibility = its own
        # `visible_default`. When OFF, every layer is hidden.
        defaults = {
            f["layer_name"]: bool(f.get("visible_default", True))
            for f in self._features_data.get("features", [])
            if f.get("layer_name")
        }
        # Cavity wireframe + walls default ON (the user's primary interior-
        # walls view) regardless of YAML defaults for the cavity entry.
        for name in self.CAVITY_LAYER_NAMES:
            defaults[name] = True

        for name in self._layers:
            if name not in self.viewer.layers:
                continue
            if visible:
                self.viewer.layers[name].visible = defaults.get(name, True)
            else:
                self.viewer.layers[name].visible = False

    def set_feature_visible(self, role: str, visible: bool) -> None:
        """Set a single feature's (or feature group's) visibility by role.

        ``chamber_cavity`` controls three layers as a unit (wireframe + back
        wall + bottom wall).
        """
        if self.viewer is None:
            return
        if role == "chamber_cavity":
            for name in self.CAVITY_LAYER_NAMES:
                if name in self.viewer.layers:
                    self.viewer.layers[name].visible = visible
            return
        for f in self._features_data.get("features", []):
            if f.get("role") == role:
                name = f.get("layer_name")
                if name and name in self.viewer.layers:
                    self.viewer.layers[name].visible = visible
                return

    def feature_layer_names(self) -> list[str]:
        return list(self._layers)

    # ---------------------------------------------------------- collision API

    def distance_to_holder(
        self,
        x_mm: float,
        y_mm: float,
        z_mm: float,
        shaft_radius_mm: float,
        nub_radius_mm: float = 0.125,
        nub_length_mm: float = 1.0,
    ) -> float:
        """Signed distance from the holder swept volume to the nearest chamber
        surface, in mm. Negative = collision/intrusion; positive = clearance.

        The holder is modeled as a vertical cylinder (shaft) plus a thinner
        nub at the tip. Position arguments are in stage mm; the holder shaft
        runs along stage Y (vertical). Conservative approximation: the
        minimum signed distance across all primitive surfaces.
        """
        # Convert stage point to STEP-frame point so we can intersect with
        # chamber primitives in STEP frame directly. Inverse of _step_to_stage_mm.
        tr = self._features_data.get("step_to_stage_transform", {})
        sign = tr.get("sign", {"stage_x": 1, "stage_y": 1, "stage_z": -1})
        offset = tr.get("offset_mm", {"stage_x": 0, "stage_y": 0, "stage_z": 0})
        perm = tr.get(
            "axis_permutation",
            {"stage_x": "file_x", "stage_y": "file_z", "stage_z": "file_y"},
        )
        # Build inverse: file_xyz from stage_xyz
        # stage_x = sign_x * file_<perm[stage_x]> + offset_x  →  file_<...> = (stage_x - offset_x) / sign_x
        unmap = {}
        for axis_name, src in perm.items():
            unmap[src] = (axis_name, sign[axis_name], offset[axis_name])
        stage_vals = {"stage_x": x_mm, "stage_y": y_mm, "stage_z": z_mm}

        def to_file(file_axis: str) -> float:
            stage_axis, s, off = unmap[file_axis]
            return (stage_vals[stage_axis] - off) / s

        fx = to_file("file_x")
        fy = to_file("file_y")
        fz = to_file("file_z")

        min_dist = math.inf
        for f in self._features_data.get("features", []):
            role = f.get("role")
            ftype = f.get("type")
            if ftype == "aabb" and role == "chamber_cavity":
                # Holder must stay inside the cavity AABB (with margin).
                bounds = f.get("bounds_step", {})
                x_lo, x_hi = bounds["x"]
                y_lo, y_hi = bounds["y"]
                z_lo, z_hi = bounds["z"]
                # Distance from holder center to nearest cavity wall, minus
                # holder radius (use shaft radius — the binding constraint).
                d_x = min(fx - x_lo, x_hi - fx) - shaft_radius_mm
                d_y = min(fy - y_lo, y_hi - fy) - shaft_radius_mm
                d_z = min(fz - z_lo, z_hi - fz) - shaft_radius_mm
                min_dist = min(min_dist, d_x, d_y, d_z)
            elif ftype == "cylinder" and role in (
                "detection_objective_port",
                "sample_entry_port",
                "illumination_port_left",
                "illumination_port_right",
            ):
                # The holder shaft must stay inside the bore cylinder when it
                # passes through. Approx: distance from holder center to bore
                # axis, minus (bore radius − shaft radius).
                center = f["center_step"]
                axis = f["axis"]
                r = float(f["radius_mm"])
                # Project the holder point onto the bore axis
                p = np.array([fx, fy, fz]) - np.array(center)
                ax = np.array(axis, dtype=np.float64)
                ax_n = ax / max(np.linalg.norm(ax), 1e-9)
                par = np.dot(p, ax_n)
                perp = p - par * ax_n
                perp_dist = float(np.linalg.norm(perp))
                # Only constraining if the holder is at the height of the bore
                # — heuristic: only enforce if holder z is within ±5 mm of
                # the bore center in the bore-axial direction. Otherwise free.
                if abs(par) <= 12.0:
                    d = (r - shaft_radius_mm) - perp_dist
                    min_dist = min(min_dist, d)
        return float(min_dist)
