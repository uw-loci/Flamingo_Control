"""ChamberVisualizationManager - 3D chamber visualization for napari.

Manages the napari 3D viewer, channel layers, and all chamber geometry
(holder, extension, rotation indicator, objective, focus frame).
Extracted from SampleView to reduce its complexity.
"""

import logging
import time

import numpy as np
from PyQt5.QtCore import QTimer

from py2flamingo.services.position_preset_service import PositionPresetService

# napari imports for 3D visualization
try:
    import napari

    NAPARI_AVAILABLE = True
except ImportError:
    NAPARI_AVAILABLE = False
    napari = None


class ChamberVisualizationManager:
    """Manages the napari 3D viewer and chamber geometry visualization.

    Owns the napari viewer, channel layers, and all chamber geometry elements
    (sample holder, fine extension, rotation indicator, objective, focus frame).

    Public API:
        embed_viewer(placeholder) - create napari viewer, replace placeholder, setup chamber + data layers
        update_stage_geometry(x_mm, y_mm, z_mm) - update holder, extension, rotation indicator positions
        set_rotation(angle_deg) - update current_rotation and rotation indicator
        update_focus_frame() - update focus frame from calibration
        setup_data_layers() - create 4 channel layers
        reset_camera() - reset viewer camera zoom
        load_objective_calibration() - load from PositionPresetService
        set_objective_calibration(x, y, z, r) - set + save calibration, update focus frame
        reload_step_chamber(yaml_path) - swap the STEP chamber profile live
    """

    # Named layers created by _setup_chamber's indicator helpers + the
    # fallback rectangular wireframe. reload_step_chamber removes these
    # (plus the StepChamberOverlay's own layers) before rebuilding.
    _CHAMBER_LAYER_NAMES = (
        "Chamber Wireframe",
        "Back Wall",
        "Bottom Wall",
        "FEP Tube",
        "Holder Stem",
        "Objective",
        "Rotation Indicator",
        "XY Focus Frame",
        "Chamber Axes",
        "Chamber Axes Tips",
        "Cavity Center",
    )

    def __init__(
        self, voxel_storage, config, invert_x, position_sliders=None, slider_scale=1000
    ):
        """
        Initialize ChamberVisualizationManager.

        Args:
            voxel_storage: DualResolutionVoxelStorage instance
            config: Visualization config dict
            invert_x: Whether X axis is inverted
            position_sliders: Optional dict of QSliders for initial holder position
            slider_scale: Scale factor for slider int conversion
        """
        self.viewer = None
        self.channel_layers = {}
        self.voxel_storage = voxel_storage
        self._config = config
        self._invert_x = invert_x
        self._position_sliders = position_sliders
        self._slider_scale = slider_scale
        self.logger = logging.getLogger(__name__)

        # 3D visualization state
        self.holder_position = {"x": 0, "y": 0, "z": 0}
        self.rotation_indicator_length = 0
        # Holder assembly dimensions (HARDWARE) are read from
        # sample_chamber.{holder_diameter_mm, fep_tube_diameter_mm,
        # fep_tube_length_mm} via _holder_dimensions(). No defaults — the
        # user must set them in visualization_3d_config.yaml.
        self.STAGE_Y_AT_OBJECTIVE = 7.45  # mm - stage Y at objective focal plane
        self.OBJECTIVE_CHAMBER_Y_MM = (
            7.0  # mm - objective focal plane in chamber coords
        )
        self.objective_xy_calibration = None  # Will be loaded from presets
        self.current_rotation = {"ry": 0}  # Current rotation angle
        self._on_setup_complete = None  # Callback after deferred setup finishes
        self._embed_start_time = 0  # For deferred timing
        self.step_overlay = None  # StepChamberOverlay instance (lazy-created)

    def embed_viewer(self, placeholder_widget, deferred_setup: bool = True) -> None:
        """Create and embed the napari 3D viewer.

        Args:
            placeholder_widget: QWidget placeholder to replace with the viewer
            deferred_setup: If True, defer chamber geometry and data layer
                setup to the next event loop iteration so the window can
                appear immediately.  Set False to do everything synchronously
                (useful for tests).
        """
        if not NAPARI_AVAILABLE:
            self.logger.warning("napari not available - 3D viewer not created")
            return

        if not self.voxel_storage:
            self.logger.warning("No voxel_storage available - 3D viewer not created")
            return

        try:
            t_start = time.perf_counter()

            # Create napari viewer with axis display
            self.viewer = napari.Viewer(ndisplay=3, show=False)
            t_viewer = time.perf_counter()
            self.logger.info(f"napari.Viewer() created in {t_viewer - t_start:.2f}s")

            # Enable axis display
            self.viewer.axes.visible = True
            self.viewer.axes.labels = True
            self.viewer.axes.colored = True

            # Set initial camera orientation (per-microscope; default (0,0,180))
            self.viewer.camera.angles = self.default_camera_angles()
            self.viewer.camera.zoom = self.default_camera_zoom()

            # Get the napari Qt widget
            napari_window = self.viewer.window
            viewer_widget = napari_window._qt_viewer

            # Replace placeholder with actual viewer
            if placeholder_widget:
                parent_widget = placeholder_widget.parent()
                if parent_widget:
                    layout = parent_widget.layout()
                    if layout:
                        layout.replaceWidget(placeholder_widget, viewer_widget)
                        placeholder_widget.deleteLater()

            t_embed = time.perf_counter()
            self.logger.info(f"napari viewer embedded in {t_embed - t_start:.2f}s")

            if deferred_setup:
                # Defer heavy geometry/layer setup so the window appears fast
                self._embed_start_time = t_start
                QTimer.singleShot(0, self._deferred_viewer_setup)
            else:
                self._finish_viewer_setup(t_start)

        except Exception as e:
            self.logger.error(f"Failed to create 3D viewer: {e}")
            import traceback

            traceback.print_exc()
            self.viewer = None

    def _deferred_viewer_setup(self) -> None:
        """Complete chamber geometry and data layer setup (deferred from embed_viewer)."""
        try:
            self._finish_viewer_setup(self._embed_start_time)
        except Exception as e:
            self.logger.error(f"Failed deferred viewer setup: {e}")
            import traceback

            traceback.print_exc()

    def _finish_viewer_setup(self, t_start: float) -> None:
        """Shared logic for completing viewer setup (chamber + data layers)."""
        t_before_chamber = time.perf_counter()

        # Load STEP overlay FIRST so the chamber setup (sample holder /
        # rotation indicator positions) can read the real chamber bounds.
        self._setup_step_chamber_overlay()

        # Setup visualization components
        self._setup_chamber()
        # Apply user's last-session visibility choices to the freshly-created
        # layers. Without this, the viewer would come up with whichever
        # defaults the setup code chose, and the persisted state wouldn't
        # take effect until the user opened the Viewer Controls dialog.
        self._apply_persisted_visibility()
        t_chamber = time.perf_counter()
        self.logger.info(
            f"Chamber visualization setup in {t_chamber - t_before_chamber:.2f}s"
        )

        self.setup_data_layers()
        t_layers = time.perf_counter()
        self.logger.info(f"Data layers setup in {t_layers - t_chamber:.2f}s")

        self.logger.info(
            f"Created napari 3D viewer successfully (total: {t_layers - t_start:.2f}s)"
        )

        # Notify listeners that deferred setup is complete
        if self._on_setup_complete:
            self._on_setup_complete()

        # Reset camera after setup
        QTimer.singleShot(100, self.reset_camera)

    def _resolve_step_yaml_path(self, explicit_path=None):
        """Resolve which STEP chamber features YAML to load.

        Priority: explicit_path (a live profile switch) > persisted profile
        (QSettings ``step_chamber/profile``) > config default
        (``step_chamber.features_yaml``). Relative paths resolve against
        src/py2flamingo/.
        """
        from pathlib import Path

        here = Path(__file__).resolve().parent.parent  # src/py2flamingo

        def _resolve(rel_or_abs):
            p = Path(rel_or_abs)
            return p if p.is_absolute() else (here / p).resolve()

        if explicit_path:
            return _resolve(explicit_path)

        # Persisted profile selection (set by ViewerControlsDialog).
        try:
            from PyQt5.QtCore import QSettings

            saved = QSettings("py2flamingo", "viewer_controls").value(
                "step_chamber/profile", "", type=str
            )
            if saved:
                cand = _resolve(saved)
                if cand.exists():
                    return cand
        except Exception:
            pass

        cfg = self._config.get("step_chamber") or {}
        return _resolve(cfg.get("features_yaml", "configs/step_chamber_features.yaml"))

    def _setup_step_chamber_overlay(self, explicit_path=None) -> None:
        """Lazy-create and load the StepChamberOverlay, then add its layers
        (hidden by default). The user toggles visibility in ViewerControlsDialog.

        Args:
            explicit_path: When given, load this profile instead of the
                persisted/config default — used by reload_step_chamber.
        """
        if not self.viewer:
            return
        try:
            yaml_path = self._resolve_step_yaml_path(explicit_path)
            if not yaml_path.exists():
                self.logger.info(
                    f"STEP chamber YAML not found, overlay disabled: {yaml_path}"
                )
                self.step_overlay = None
                return

            from py2flamingo.views.step_chamber_overlay import StepChamberOverlay
            from py2flamingo.visualization.axis_orientation import AxisOrientation

            self.step_overlay = StepChamberOverlay(
                viewer=self.viewer,
                features_yaml_path=yaml_path,
                config=self._config,
                invert_x=self._invert_x,
                orientation=AxisOrientation.from_config(
                    self._config, invert_x=self._invert_x
                ),
            )
            if self.step_overlay.load():
                self.step_overlay.add_layers(master_visible=False)
                self.logger.info(
                    f"STEP chamber overlay loaded from {yaml_path.name} with "
                    f"{len(self.step_overlay.feature_layer_names())} layers (hidden)"
                )
            else:
                self.step_overlay = None
        except Exception as e:
            self.logger.warning(f"STEP chamber overlay setup failed: {e}")
            self.step_overlay = None

    def reload_step_chamber(self, yaml_path) -> bool:
        """Swap the active STEP chamber profile and rebuild the chamber view.

        Tears down the current STEP overlay plus every in-chamber indicator
        layer, reloads geometry from ``yaml_path``, and re-runs chamber setup.
        Data / channel layers are left untouched.

        The caller should re-apply the current stage position and rotation
        afterwards (see SampleView.reload_chamber_profile) so the holder
        assembly lands correctly.

        Args:
            yaml_path: Profile YAML, absolute or relative to src/py2flamingo/.

        Returns:
            True if the new STEP overlay loaded successfully.
        """
        if not self.viewer:
            return False
        try:
            # Tear down the existing STEP overlay (it tracks its own layers).
            if self.step_overlay is not None:
                try:
                    self.step_overlay.remove_layers()
                except Exception:
                    pass
                self.step_overlay = None

            # Remove indicator + fallback-wireframe layers by name.
            for name in self._CHAMBER_LAYER_NAMES:
                if name in self.viewer.layers:
                    try:
                        self.viewer.layers.remove(name)
                    except Exception:
                        pass

            # Rebuild from the new profile.
            self._setup_step_chamber_overlay(explicit_path=yaml_path)
            self._setup_chamber()
            self._apply_persisted_visibility()

            loaded = self.step_overlay is not None and getattr(
                self.step_overlay, "_loaded", False
            )
            self.logger.info(
                f"Chamber profile reloaded: {yaml_path} (STEP loaded={loaded})"
            )
            return loaded
        except Exception as e:
            self.logger.error(f"Chamber profile reload failed: {e}")
            return False

    def _setup_chamber(self) -> None:
        """Setup the chamber visualization and all in-chamber indicators.

        Two modes:
        - **STEP loaded (default)**: the chamber geometry is rendered by
          StepChamberOverlay (already added in _setup_step_chamber_overlay).
          We skip the rectangular travel-envelope wireframe + reference walls
          entirely and only add the in-chamber indicators (holder, focus
          frame, axes, cavity center).
        - **STEP missing (fallback)**: build the legacy rectangular
          travel-envelope wireframe + reference walls. Used when the STEP
          features YAML hasn't been extracted yet.
        """
        if not self.viewer or not self.voxel_storage:
            return

        try:
            step_loaded = self.step_overlay is not None and getattr(
                self.step_overlay, "_loaded", False
            )

            if not step_loaded:
                dims = self.voxel_storage.display_dims  # (Z, Y, X) order

                # Physical chamber bounds in napari voxel coordinates.
                # The chamber is 14mm tall (chamber Y 0-14mm), positioned within
                # the larger display volume that covers the full stage Y range.
                stage_ctrl = self._config.get("stage_control", {})
                y_range = stage_ctrl.get("y_range_mm", [0.0, 25.0])
                voxel_size_mm = (
                    self._config.get("display", {}).get("voxel_size_um", [50, 50, 50])[
                        0
                    ]
                    / 1000.0
                )
                chamber_height_mm = self._config.get("sample_chamber", {}).get(
                    "chamber_below_anchor_mm", 10.0
                ) + self._config.get("sample_chamber", {}).get(
                    "chamber_above_anchor_mm", 5.0
                )  # default 15mm
                # Chamber top is at chamber_y=0, bottom at chamber_y=chamber_height
                chamber_y_top = int(
                    (y_range[1] - 0) / voxel_size_mm
                )  # napari Y for chamber_y=0
                chamber_y_bot = int(
                    (y_range[1] - chamber_height_mm) / voxel_size_mm
                )  # napari Y for chamber bottom
                # Clamp to display bounds
                chamber_y_top = min(chamber_y_top, dims[1] - 1)
                chamber_y_bot = max(chamber_y_bot, 0)

                # Define the 8 corners of the CHAMBER box in napari (Z, Y, X) order
                corners = np.array(
                    [
                        [0, chamber_y_bot, 0],
                        [dims[0] - 1, chamber_y_bot, 0],
                        [dims[0] - 1, chamber_y_bot, dims[2] - 1],
                        [0, chamber_y_bot, dims[2] - 1],
                        [0, chamber_y_top, 0],
                        [dims[0] - 1, chamber_y_top, 0],
                        [dims[0] - 1, chamber_y_top, dims[2] - 1],
                        [0, chamber_y_top, dims[2] - 1],
                    ]
                )

                # All 12 chamber edges combined into single layer for performance
                # Z edges (yellow), Y edges (magenta), X edges (cyan)
                all_edges = [
                    # Z edges (4)
                    [corners[0], corners[1]],
                    [corners[3], corners[2]],
                    [corners[4], corners[5]],
                    [corners[7], corners[6]],
                    # Y edges (4)
                    [corners[0], corners[4]],
                    [corners[1], corners[5]],
                    [corners[2], corners[6]],
                    [corners[3], corners[7]],
                    # X edges (4)
                    [corners[0], corners[3]],
                    [corners[1], corners[2]],
                    [corners[4], corners[7]],
                    [corners[5], corners[6]],
                ]

                # Per-edge colors: 4 yellow (Z), 4 magenta (Y), 4 cyan (X)
                edge_colors = (
                    ["#8B8B00"] * 4  # Z edges
                    + ["#8B008B"] * 4  # Y edges
                    + ["#008B8B"] * 4  # X edges
                )

                self.viewer.add_shapes(
                    data=all_edges,
                    shape_type="line",
                    name="Chamber Wireframe",
                    edge_color=edge_colors,
                    edge_width=2,
                    opacity=0.6,
                )

                # Add reference walls (subtle fill for orientation when rotating)
                self._add_reference_walls(dims, chamber_y_top, chamber_y_bot)
            else:
                self.logger.info(
                    "STEP chamber overlay active; skipping rectangular wireframe"
                )

            # In-chamber indicators (added in both modes — they read positions
            # from the STEP overlay when available, otherwise fall back to
            # the rectangular envelope).
            self._add_sample_holder()  # sets self.holder_position; no sphere
            self._add_holder_assembly()  # FEP Tube + Holder Stem layers
            self._add_objective_indicator()
            self._add_rotation_indicator()
            self._add_xy_focus_frame()
            self._add_cavity_center_indicator()
            self._add_chamber_axes_arrows()

        except Exception as e:
            self.logger.warning(f"Failed to setup chamber visualization: {e}")

    def _apply_persisted_visibility(self) -> None:
        """Apply the user's last-session visibility choices to the napari
        layers, reading from the same QSettings store ViewerControlsDialog
        writes to. Called after layer creation so the viewer reflects
        persisted state without waiting for the dialog to open.

        Settings keys are the contract between this method and the dialog:
            elements/chamber, elements/objective, elements/focus_frame,
            elements/axes, elements/cavity_center,
            step_chamber/master, step_chamber/<role>
        """
        if not self.viewer:
            return
        try:
            from PyQt5.QtCore import QSettings

            s = QSettings("py2flamingo", "viewer_controls")

            step_loaded = self.step_overlay is not None and getattr(
                self.step_overlay, "_loaded", False
            )

            # Fallback rect wireframe + reference walls — only present when
            # STEP isn't loaded.
            if not step_loaded:
                chamber_vis = s.value("elements/chamber", True, type=bool)
                for name in ("Chamber Wireframe", "Back Wall", "Bottom Wall"):
                    if name in self.viewer.layers:
                        self.viewer.layers[name].visible = chamber_vis
                obj_vis = s.value("elements/objective", True, type=bool)
                if "Objective" in self.viewer.layers:
                    self.viewer.layers["Objective"].visible = obj_vis

            focus_vis = s.value("elements/focus_frame", True, type=bool)
            if "XY Focus Frame" in self.viewer.layers:
                self.viewer.layers["XY Focus Frame"].visible = focus_vis

            # Axes: when STEP is loaded, the chamber-anchored arrow layers
            # are the user-visible axes (napari's built-in viewer.axes is
            # hidden by _add_chamber_axes_arrows because its anchor sits
            # outside the chamber). When STEP isn't loaded, control the
            # built-in indicator.
            axes_vis = s.value("elements/axes", True, type=bool)
            if step_loaded:
                for name in ("Chamber Axes", "Chamber Axes Tips"):
                    if name in self.viewer.layers:
                        self.viewer.layers[name].visible = axes_vis
            elif hasattr(self.viewer, "axes"):
                self.viewer.axes.visible = axes_vis

            cavity_vis = s.value("elements/cavity_center", True, type=bool)
            if "Cavity Center" in self.viewer.layers:
                self.viewer.layers["Cavity Center"].visible = cavity_vis

            # STEP overlay master + sub-feature visibility. The defaults
            # mirror the labels in ViewerControlsDialog._create_display_settings_tab.
            if step_loaded and self.step_overlay is not None:
                master_vis = s.value("step_chamber/master", True, type=bool)
                if not master_vis:
                    self.step_overlay.set_master_visible(False)
                else:
                    sub_defaults = {
                        "chamber_cavity": True,
                        "chamber_outer_box": False,
                        "detection_objective_port": True,
                        "sample_entry_port": True,
                        "illumination_ports": True,
                        "sample_entry_top_hole": True,
                        "rail_mount_bolts": False,
                    }
                    for role, default_on in sub_defaults.items():
                        vis = s.value(f"step_chamber/{role}", default_on, type=bool)
                        # Expand UI grouping roles to YAML feature roles.
                        if role == "illumination_ports":
                            expanded = (
                                "illumination_port_left",
                                "illumination_port_right",
                            )
                        elif role == "rail_mount_bolts":
                            expanded = (
                                "rail_mount_bolt_left",
                                "rail_mount_bolt_right",
                            )
                        else:
                            expanded = (role,)
                        for r in expanded:
                            self.step_overlay.set_feature_visible(r, vis)
        except Exception as e:
            self.logger.warning(f"Failed to apply persisted visibility: {e}")

    def _add_reference_walls(self, dims, chamber_y_top, chamber_y_bot) -> None:
        """Add subtle filled walls for orientation when rotating the 3D view.

        Two walls are drawn within the physical chamber bounds:
        - Back wall at Z=0 (where the objective is located)
        - Bottom wall at chamber_y_top (physical bottom of the chamber)

        Args:
            dims: Display dimensions tuple (Z, Y, X) in voxels
            chamber_y_top: napari Y for physical top of chamber (high Y = low chamber_y)
            chamber_y_bot: napari Y for physical bottom of chamber (low Y = high chamber_y)
        """
        if not self.viewer:
            return

        try:
            z_max = dims[0] - 1
            x_max = dims[2] - 1

            wall_opacity = 0.04

            # --- Back wall (Z=0 plane, within chamber Y bounds) ---
            back_verts = np.array(
                [
                    [0, chamber_y_bot, 0],
                    [0, chamber_y_bot, x_max],
                    [0, chamber_y_top, x_max],
                    [0, chamber_y_top, 0],
                ],
                dtype=np.float32,
            )
            back_faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
            back_values = np.ones(len(back_verts), dtype=np.float32)

            self.viewer.add_surface(
                (back_verts, back_faces, back_values),
                name="Back Wall",
                colormap="gray",
                opacity=wall_opacity,
                shading="none",
            )

            # --- Bottom wall (physical bottom of chamber) ---
            bottom_verts = np.array(
                [
                    [0, chamber_y_top, 0],
                    [0, chamber_y_top, x_max],
                    [z_max, chamber_y_top, x_max],
                    [z_max, chamber_y_top, 0],
                ],
                dtype=np.float32,
            )
            bottom_faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
            bottom_values = np.ones(len(bottom_verts), dtype=np.float32)

            self.viewer.add_surface(
                (bottom_verts, bottom_faces, bottom_values),
                name="Bottom Wall",
                colormap="gray",
                opacity=wall_opacity,
                shading="none",
            )

            self.logger.info(
                f"Added reference walls (back Z=0, bottom Y={chamber_y_top}) at {wall_opacity:.0%} opacity"
            )

        except Exception as e:
            self.logger.warning(f"Failed to add reference walls: {e}")

    def _holder_dimensions(self) -> tuple:
        """Return (holder_diameter_mm, fep_tube_diameter_mm, fep_tube_length_mm)
        from config. These are HARDWARE values; the config must supply them
        explicitly — no defaults, because a wrong value could allow an
        oversized holder to hit the chamber wall undetected, or misposition
        the visualization of where the sample actually is.
        """
        sc = self._config.get("sample_chamber", {})
        holder_diam = sc.get("holder_diameter_mm")
        fep_diam = sc.get("fep_tube_diameter_mm")
        fep_len = sc.get("fep_tube_length_mm")
        missing = [
            k
            for k, v in (
                ("holder_diameter_mm", holder_diam),
                ("fep_tube_diameter_mm", fep_diam),
                ("fep_tube_length_mm", fep_len),
            )
            if v is None
        ]
        if missing:
            raise ValueError(
                "sample_chamber config is missing required HARDWARE values: "
                + ", ".join(missing)
                + ". No safe defaults exist — set these in "
                "src/py2flamingo/configs/visualization_3d_config.yaml to the "
                "actual installed-component dimensions in mm."
            )
        return float(holder_diam), float(fep_diam), float(fep_len)

    def _add_sample_holder(self) -> None:
        """Compute the initial sample-tip position (stored in
        ``self.holder_position``) used by FEP tube + metal stem + rotation
        indicator. No sphere is rendered — the assembly is 2 parts:
        FEP tube (below) and metal stem (above), each in its own layer.
        """
        if not self.viewer or not self.voxel_storage:
            return

        try:
            voxel_size_um = self._config.get("display", {}).get(
                "voxel_size_um", [50, 50, 50]
            )[0]
            voxel_size_mm = voxel_size_um / 1000.0

            dims = self.voxel_storage.display_dims  # (Z, Y, X)

            # Get initial stage position
            stage_ctrl = self._config.get("stage_control", {})
            x_range = stage_ctrl.get("x_range_mm", [1.0, 12.31])
            y_range = stage_ctrl.get("y_range_mm", [0.0, 14.0])
            z_range = stage_ctrl.get("z_range_mm", [12.5, 26.0])

            init_pos = getattr(self, "_initial_stage_position", None)
            if init_pos and any(init_pos.get(k, 0) != 0 for k in ("x", "y", "z")):
                x_mm = init_pos.get("x", (x_range[0] + x_range[1]) / 2)
                stage_y_mm = init_pos.get("y", (y_range[0] + y_range[1]) / 2)
                z_mm = init_pos.get("z", (z_range[0] + z_range[1]) / 2)
            elif self._position_sliders and "x" in self._position_sliders:
                x_mm = self._position_sliders["x"].value() / self._slider_scale
                stage_y_mm = self._position_sliders["y"].value() / self._slider_scale
                z_mm = self._position_sliders["z"].value() / self._slider_scale
            else:
                x_mm = (x_range[0] + x_range[1]) / 2
                stage_y_mm = (y_range[0] + y_range[1]) / 2
                z_mm = (z_range[0] + z_range[1]) / 2

            chamber_y_tip_mm = self._stage_y_to_chamber_y(stage_y_mm)

            if self._invert_x:
                napari_x = int((x_range[1] - x_mm) / voxel_size_mm)
            else:
                napari_x = int((x_mm - x_range[0]) / voxel_size_mm)
            napari_y_tip = int((y_range[1] - chamber_y_tip_mm) / voxel_size_mm)
            napari_z = int((z_mm - z_range[0]) / voxel_size_mm)

            napari_x = max(0, min(dims[2] - 1, napari_x))
            napari_y_tip = max(0, min(dims[1] - 1, napari_y_tip))
            napari_z = max(0, min(dims[0] - 1, napari_z))

            self.holder_position = {"x": napari_x, "y": napari_y_tip, "z": napari_z}
            self.logger.info(
                f"Initial sample tip: stage ({x_mm:.2f}, {stage_y_mm:.2f}, "
                f"{z_mm:.2f}) -> napari (Z={napari_z}, Y={napari_y_tip}, X={napari_x})"
            )
        except Exception as e:
            self.logger.warning(f"Failed to initialize sample-tip position: {e}")

    def _holder_assembly_columns(self):
        """Compute the FEP-tube and metal-stem point columns in napari coords,
        based on the current ``self.holder_position`` (sample tip) and the
        chamber-top stage Y. Returns (fep_points, stem_points, fep_size_voxels,
        stem_size_voxels), each points array shape (N, 3) in (Z, Y, X). Either
        list may be empty if entirely above the chamber top.
        """
        holder_diam, fep_diam, fep_len_mm = self._holder_dimensions()
        voxel_size_um = self._config.get("display", {}).get(
            "voxel_size_um", [50, 50, 50]
        )[0]
        voxel_size_mm = voxel_size_um / 1000.0

        napari_x = self.holder_position["x"]
        napari_y_tip = self.holder_position["y"]
        napari_z = self.holder_position["z"]

        # napari Y is inverted: smaller Y == higher in space.
        y_range = self._config.get("stage_control", {}).get("y_range_mm", [0.0, 14.0])
        chamber_top_stage_y = self._chamber_top_stage_y_mm()
        chamber_top_napari_y = (y_range[1] - chamber_top_stage_y) / voxel_size_mm

        fep_len_voxels = fep_len_mm / voxel_size_mm
        fep_top_napari_y = napari_y_tip - fep_len_voxels  # above tip

        # FEP tube: from tip (bottom) up to FEP top, clipped at chamber top.
        fep_bottom = napari_y_tip
        fep_top = max(fep_top_napari_y, chamber_top_napari_y)
        fep_points = []
        if fep_top < fep_bottom:
            # Place points every 2 voxels along the FEP column.
            n = max(2, int(round(fep_bottom - fep_top)) // 2 + 1)
            ys = np.linspace(fep_top, fep_bottom, n)
            fep_points = np.array([[napari_z, float(y), napari_x] for y in ys])

        # Metal stem: from chamber top down to FEP top, only if FEP top is
        # BELOW chamber top (otherwise the whole stem would be above the
        # chamber and the user wants it clipped).
        stem_points = []
        if fep_top_napari_y > chamber_top_napari_y:
            n = max(2, int(round(fep_top_napari_y - chamber_top_napari_y)) // 2 + 1)
            ys = np.linspace(chamber_top_napari_y, fep_top_napari_y, n)
            stem_points = np.array([[napari_z, float(y), napari_x] for y in ys])

        fep_size_voxels = max(1, fep_diam / voxel_size_mm)
        stem_size_voxels = max(1, holder_diam / voxel_size_mm)
        return fep_points, stem_points, fep_size_voxels, stem_size_voxels

    def _add_holder_assembly(self) -> None:
        """Create the two holder layers — "Holder Stem" (metal, thick) above
        and "FEP Tube" (thin) below — anchored at the sample tip and clipped
        at the chamber top. The whole assembly moves with the stage."""
        if not self.viewer or not self.voxel_storage:
            return
        try:
            (
                fep_points,
                stem_points,
                fep_size,
                stem_size,
            ) = self._holder_assembly_columns()

            # napari Points layers require at least one row; seed with a
            # single zero-row that will be replaced by _update_holder_assembly
            # if the assembly is entirely above the chamber top right now.
            seed = np.zeros((1, 3), dtype=np.float32)

            self.viewer.add_points(
                fep_points if len(fep_points) else seed,
                name="FEP Tube",
                size=fep_size,
                face_color="#FFFF00",
                border_color="#FFA500",
                border_width=0.1,
                opacity=0.9,
                shading="spherical",
                visible=bool(len(fep_points)),
            )
            self.viewer.add_points(
                stem_points if len(stem_points) else seed,
                name="Holder Stem",
                size=stem_size,
                face_color="#AAAAAA",
                border_color="#555555",
                border_width=0.1,
                opacity=0.6,
                shading="spherical",
                visible=bool(len(stem_points)),
            )
            self.logger.info(
                f"Holder assembly: FEP={len(fep_points)} pts, "
                f"Stem={len(stem_points)} pts"
            )
        except Exception as e:
            self.logger.warning(f"Failed to add holder assembly: {e}")

    def _add_objective_indicator(self) -> None:
        """Add objective position indicator circle at Z=0 (back wall).

        Skipped when the STEP chamber overlay is loaded — the STEP
        detection_objective_port renders the real CAD lens position
        (a 33 mm-dia ring on the actual chamber back wall) and the
        rectangular back-wall circle becomes a redundant ghost.
        """
        if not self.viewer or not self.voxel_storage:
            return
        if self.step_overlay and getattr(self.step_overlay, "_loaded", False):
            self.logger.info(
                "Skipping rectangular Objective ring; STEP detection_objective_port is the canonical indicator"
            )
            return

        try:
            dims = self.voxel_storage.display_dims  # (Z, Y, X)

            # Objective at Z=0 (back wall)
            z_objective = 0

            # Objective focal plane position
            voxel_size_um = self._config.get("display", {}).get(
                "voxel_size_um", [50, 50, 50]
            )[0]
            voxel_size_mm = voxel_size_um / 1000.0

            # Y position at objective focal plane (7mm from top in physical coords)
            # In napari, Y is inverted
            y_range = self._config.get("stage_control", {}).get("y_range_mm", [0, 14])
            napari_y_objective = int(
                (y_range[1] - self.OBJECTIVE_CHAMBER_Y_MM) / voxel_size_mm
            )
            napari_y_objective = min(max(0, napari_y_objective), dims[1] - 1)

            center_y = napari_y_objective
            center_x = dims[2] // 2

            # Circle radius (1/6 of smaller dimension)
            radius = min(dims[1], dims[2]) // 6

            # Create circle as line segments
            num_points = 36
            angles = np.linspace(0, 2 * np.pi, num_points, endpoint=False)

            circle_points = []
            for angle in angles:
                y = center_y + radius * np.cos(angle)
                x = center_x + radius * np.sin(angle)
                circle_points.append([z_objective, y, x])

            # Create circle edges
            circle_edges = [
                [circle_points[i], circle_points[(i + 1) % len(circle_points)]]
                for i in range(len(circle_points))
            ]

            self.viewer.add_shapes(
                data=circle_edges,
                shape_type="line",
                name="Objective",
                edge_color="#FFCC00",  # Gold/yellow
                edge_width=3,
                opacity=0.3,
            )

        except Exception as e:
            self.logger.warning(f"Failed to add objective indicator: {e}")

    def _add_rotation_indicator(self) -> None:
        """Add rotation indicator line at top of chamber."""
        if not self.viewer or not self.voxel_storage:
            return

        try:
            dims = self.voxel_storage.display_dims

            # Indicator length - 1/2 shortest dimension
            indicator_length = min(dims[0], dims[2]) // 2
            self.rotation_indicator_length = indicator_length

            # Position at the actual chamber top (matches the sample-holder
            # mount point). With STEP loaded this is ~napari_y = -250 (above
            # the display volume); without STEP it falls back to napari Y=0.
            stage_ctrl = self._config.get("stage_control", {})
            y_range = stage_ctrl.get("y_range_mm", [0.0, 14.0])
            voxel_size_mm = (
                self._config.get("display", {}).get("voxel_size_um", [50, 50, 50])[0]
                / 1000.0
            )
            chamber_top_stage_y = self._chamber_top_stage_y_mm()
            y_position = (y_range[1] - chamber_top_stage_y) / voxel_size_mm
            holder_z = self.holder_position["z"]
            holder_x = self.holder_position["x"]

            # At 0 degrees, extends along +X axis
            indicator_start = np.array([holder_z, y_position, holder_x])
            indicator_end = np.array(
                [holder_z, y_position, holder_x + indicator_length]
            )

            # Get color based on rotation angle
            initial_color = self._get_rotation_gradient_color(
                self.current_rotation.get("ry", 0)
            )

            self.viewer.add_shapes(
                data=[[indicator_start, indicator_end]],
                shape_type="line",
                name="Rotation Indicator",
                edge_color=initial_color,
                edge_width=3,
                opacity=0.8,
            )

            # Immediately update indicator to current rotation angle
            # (indicator was created at 0 deg, but actual rotation may differ)
            self._update_rotation_indicator()
            self.logger.info(
                f"Rotation indicator initialized at {self.current_rotation.get('ry', 0):.1f} deg"
            )

        except Exception as e:
            self.logger.warning(f"Failed to add rotation indicator: {e}")

    def _add_xy_focus_frame(self) -> None:
        """Add XY focus frame showing camera field of view at focal plane."""
        if not self.viewer or not self.voxel_storage:
            return

        try:
            dims = self.voxel_storage.display_dims
            voxel_size_um = self._config.get("display", {}).get(
                "voxel_size_um", [50, 50, 50]
            )[0]
            voxel_size_mm = voxel_size_um / 1000.0

            # Focus frame configuration
            focus_config = self._config.get("focus_frame", {})
            fov_x_mm = focus_config.get("field_of_view_x_mm", 0.52)
            fov_y_mm = focus_config.get("field_of_view_y_mm", 0.52)
            frame_color = focus_config.get("color", "#FFFF00")
            edge_width = focus_config.get("edge_width", 3)
            opacity = focus_config.get("opacity", 0.9)

            # FOV in voxels
            fov_x_voxels = fov_x_mm / voxel_size_mm
            fov_y_voxels = fov_y_mm / voxel_size_mm

            # Position at objective focal plane
            x_range = self._config.get("stage_control", {}).get(
                "x_range_mm", [1.0, 12.31]
            )
            y_range = self._config.get("stage_control", {}).get("y_range_mm", [0, 14])
            z_range = self._config.get("stage_control", {}).get(
                "z_range_mm", [12.5, 26]
            )

            # Pick the focal-plane position in stage mm. When STEP is loaded
            # the stage coordinate origin has been redefined (the cavity is
            # now centered on the stage range), so any old "Tip of sample
            # mount" preset values are stale — prefer the cavity center
            # until the user explicitly re-calibrates against the new
            # chamber. Outside STEP mode honor the legacy calibration.
            cavity_center_stage = self._cavity_center_stage_mm()
            if cavity_center_stage is not None:
                focal_x_mm, focal_y_mm, focal_z_mm = cavity_center_stage
            elif (
                self.objective_xy_calibration
                and self.objective_xy_calibration.get("x") is not None
            ):
                focal_x_mm = self.objective_xy_calibration["x"]
                focal_y_mm = self.objective_xy_calibration.get(
                    "y", self.STAGE_Y_AT_OBJECTIVE
                )
                focal_z_mm = self.objective_xy_calibration["z"]
            else:
                focal_x_mm = (x_range[0] + x_range[1]) / 2
                focal_y_mm = self.STAGE_Y_AT_OBJECTIVE
                focal_z_mm = (z_range[0] + z_range[1]) / 2

            # Stage -> napari (don't clamp to display dims so the frame can
            # render inside the larger STEP cavity even when that cavity
            # extends outside the rectangular voxel-storage volume)
            napari_z = (focal_z_mm - z_range[0]) / voxel_size_mm
            napari_y = (y_range[1] - focal_y_mm) / voxel_size_mm
            if self._invert_x:
                napari_x = (x_range[1] - focal_x_mm) / voxel_size_mm
            else:
                napari_x = (focal_x_mm - x_range[0]) / voxel_size_mm

            # Frame corners
            half_fov_x = fov_x_voxels / 2
            half_fov_y = fov_y_voxels / 2

            corners = [
                [napari_z, napari_y - half_fov_y, napari_x - half_fov_x],
                [napari_z, napari_y - half_fov_y, napari_x + half_fov_x],
                [napari_z, napari_y + half_fov_y, napari_x + half_fov_x],
                [napari_z, napari_y + half_fov_y, napari_x - half_fov_x],
            ]

            frame_edges = [
                [corners[0], corners[1]],
                [corners[1], corners[2]],
                [corners[2], corners[3]],
                [corners[3], corners[0]],
            ]

            self.viewer.add_shapes(
                data=frame_edges,
                shape_type="line",
                name="XY Focus Frame",
                edge_color=frame_color,
                edge_width=edge_width,
                opacity=opacity,
            )

        except Exception as e:
            self.logger.warning(f"Failed to add XY focus frame: {e}")

    def _add_chamber_axes_arrows(self) -> None:
        """Draw custom XYZ axis arrows anchored at the cavity wireframe corner.

        napari's built-in viewer.axes is anchored at the data origin (napari
        0,0,0), which for the new chamber is far outside the chamber and
        useless as a visual reference. We hide it when the STEP overlay is
        loaded and draw our own axis arrows at the back-bottom-left corner
        of the cavity wireframe so the user can read XYZ orientation
        relative to the chamber.

        Arrow lengths:  5 mm
        Colors:  X = cyan, Y = magenta, Z = yellow (matches napari's default
                 axis colors so the legend reads the same).
        """
        if not self.viewer:
            return
        if not (self.step_overlay and getattr(self.step_overlay, "_loaded", False)):
            return  # without STEP, leave napari's built-in axes alone
        try:
            # Hide napari's default axes — anchored at world (0,0,0), which is
            # not near the chamber after the STEP-coord redefinition.
            if hasattr(self.viewer, "axes"):
                self.viewer.axes.visible = False

            cavity = next(
                (
                    f
                    for f in self.step_overlay._features_data.get("features", [])
                    if f.get("role") == "chamber_cavity"
                ),
                None,
            )
            if cavity is None:
                return
            b = cavity["bounds_step"]
            # Anchor at the cavity back-bottom-left corner (file frame), then
            # convert to STAGE frame so we can step along each STAGE axis to
            # build the arrow tips. Stepping in stage frame is what makes the
            # cyan arrow run along napari Axis 2 (= stage_x, horizontal), the
            # magenta along Axis 1 (= stage_y, vertical), and the yellow along
            # Axis 0 (= stage_z, optical depth) — matching napari's built-in
            # axis-color convention.
            x_a_step, y_a_step, z_a_step = b["x"][0], b["y"][0], b["z"][0]
            x_a, y_a, z_a = self.step_overlay._step_to_stage_mm(
                (x_a_step, y_a_step, z_a_step)
            )

            arrow_step_mm = 5.0

            origin_napari = self.step_overlay._stage_to_napari(x_a, y_a, z_a)
            x_tip = self.step_overlay._stage_to_napari(
                x_a + arrow_step_mm, y_a, z_a
            )  # stage_x → cyan / Axis 2
            y_tip = self.step_overlay._stage_to_napari(
                x_a, y_a + arrow_step_mm, z_a
            )  # stage_y → magenta / Axis 1
            z_tip = self.step_overlay._stage_to_napari(
                x_a, y_a, z_a + arrow_step_mm
            )  # stage_z → yellow / Axis 0

            origin = np.array(origin_napari, dtype=np.float32)
            arrows = [
                (
                    np.array(x_tip, dtype=np.float32),
                    "#00FFFF",
                    "X (stage_x, illumination)",
                ),
                (np.array(y_tip, dtype=np.float32), "#FF00FF", "Y (stage_y, vertical)"),
                (
                    np.array(z_tip, dtype=np.float32),
                    "#FFD700",
                    "Z (stage_z, optical depth)",
                ),
            ]
            edges = [np.array([origin, tip], dtype=np.float32) for tip, _, _ in arrows]
            colors = [c for _, c, _ in arrows]

            self.viewer.add_shapes(
                data=edges,
                shape_type="line",
                name="Chamber Axes",
                edge_color=colors,
                edge_width=3,
                opacity=0.95,
            )
            # A point at each arrow tip serves as a visible head — napari's
            # Shapes layer doesn't render arrowheads.
            tip_points = np.array([tip for tip, _, _ in arrows], dtype=np.float32)
            self.viewer.add_points(
                tip_points,
                name="Chamber Axes Tips",
                size=8,
                face_color=colors,
                border_color="white",
                border_width=0.1,
                opacity=0.95,
                shading="spherical",
            )
            self.logger.info(
                f"Chamber axes anchored at file ({x_a_step:.2f}, {y_a_step:.2f}, {z_a_step:.2f}) "
                f"= napari {tuple(round(v, 1) for v in origin_napari)}"
            )
        except Exception as e:
            self.logger.warning(f"Failed to add chamber axes arrows: {e}")

    def _add_cavity_center_indicator(self) -> None:
        """Add a small marker at the geometric cavity centroid.

        Sits alongside the calibrated XY Focus Frame so the user can see
        both points: the per-microscope calibrated focal plane ("Tip of
        sample mount" preset) AND the chamber's geometric center. Only
        rendered when the STEP overlay is loaded.
        """
        if not self.viewer or not self.voxel_storage:
            return
        center = self._cavity_center_stage_mm()
        if center is None:
            return
        try:
            x_mm, y_mm, z_mm = center
            stage_ctrl = self._config.get("stage_control", {})
            x_range = stage_ctrl.get("x_range_mm", [1.0, 12.31])
            y_range = stage_ctrl.get("y_range_mm", [0.0, 14.0])
            z_range = stage_ctrl.get("z_range_mm", [12.5, 26.0])
            voxel_size_mm = (
                self._config.get("display", {}).get("voxel_size_um", [50, 50, 50])[0]
                / 1000.0
            )
            if self._invert_x:
                npx = (x_range[1] - x_mm) / voxel_size_mm
            else:
                npx = (x_mm - x_range[0]) / voxel_size_mm
            npy = (y_range[1] - y_mm) / voxel_size_mm
            npz = (z_mm - z_range[0]) / voxel_size_mm

            self.viewer.add_points(
                np.array([[npz, npy, npx]], dtype=np.float32),
                name="Cavity Center",
                size=12,
                face_color="#FF00FF",  # magenta — distinct from yellow focus frame
                border_color="#FFFFFF",
                border_width=0.15,
                opacity=0.9,
                shading="spherical",
            )
            self.logger.info(
                f"Added cavity-center indicator at stage ({x_mm:.2f}, {y_mm:.2f}, {z_mm:.2f}) mm"
            )
        except Exception as e:
            self.logger.warning(f"Failed to add cavity-center indicator: {e}")

    def _get_rotation_gradient_color(self, angle_degrees: float) -> str:
        """Get color for rotation indicator based on angle."""
        # Normalize angle to 0-360
        angle = angle_degrees % 360
        if angle < 0:
            angle += 360

        # Color gradient: 0=red, 90=yellow, 180=green, 270=cyan, 360=red
        if angle < 90:
            r = 255
            g = int(255 * angle / 90)
            b = 0
        elif angle < 180:
            r = int(255 * (180 - angle) / 90)
            g = 255
            b = 0
        elif angle < 270:
            r = 0
            g = 255
            b = int(255 * (angle - 180) / 90)
        else:
            r = 0
            g = int(255 * (360 - angle) / 90)
            b = 255

        return f"#{r:02x}{g:02x}{b:02x}"

    def _stage_y_to_chamber_y(self, stage_y_mm: float) -> float:
        """Convert stage Y position to chamber Y coordinate."""
        # At stage Y = 7.45mm, extension tip is at objective focal plane (Y=7.0mm)
        offset = stage_y_mm - self.STAGE_Y_AT_OBJECTIVE
        return self.OBJECTIVE_CHAMBER_Y_MM + offset

    def _chamber_top_stage_y_mm(self) -> float:
        """Return the top of the actual chamber in stage_y mm.

        When the STEP chamber overlay is loaded, derive this from the
        chamber_outer_box's upper Z bound (the chamber's open top face).
        Otherwise fall back to the rectangular display volume's Y maximum.
        """
        if self.step_overlay and getattr(self.step_overlay, "_loaded", False):
            for f in self.step_overlay._features_data.get("features", []):
                if f.get("role") == "chamber_outer_box":
                    tr = self.step_overlay._features_data.get(
                        "step_to_stage_transform", {}
                    )
                    offset_y = tr.get("offset_mm", {}).get("stage_y", 0.0)
                    sign_y = tr.get("sign", {}).get("stage_y", 1)
                    z_top_file = f["bounds_step"]["z"][1]
                    return sign_y * z_top_file + offset_y
        y_range = self._config.get("stage_control", {}).get("y_range_mm", [0.0, 14.0])
        return y_range[1]

    def _cavity_center_stage_mm(self) -> tuple | None:
        """Return the cavity centroid in stage mm (x, y, z), or None if no
        STEP overlay is loaded."""
        if not (self.step_overlay and getattr(self.step_overlay, "_loaded", False)):
            return None
        for f in self.step_overlay._features_data.get("features", []):
            if f.get("role") == "chamber_cavity":
                b = f["bounds_step"]
                cx = (b["x"][0] + b["x"][1]) / 2
                cy = (b["y"][0] + b["y"][1]) / 2
                cz = (b["z"][0] + b["z"][1]) / 2
                return self.step_overlay._step_to_stage_mm((cx, cy, cz))
        return None

    def update_stage_geometry(self, x_mm: float, y_mm: float, z_mm: float) -> None:
        """Update sample-tip position when the stage moves.

        Recomputes ``self.holder_position`` (the sample tip in napari coords)
        and refreshes the holder assembly + rotation indicator. The whole
        assembly translates rigidly with the stage; the chamber-top clip
        means the stem shortens as the stage goes up.

        Args:
            x_mm, y_mm, z_mm: Physical stage coordinates in mm.
        """
        if not self.viewer or not self.voxel_storage:
            return
        # FEP Tube is the load-bearing layer (always rendered if assembly
        # is present); skip update if it's not in the viewer yet.
        if "FEP Tube" not in self.viewer.layers:
            return

        chamber_y_tip_mm = self._stage_y_to_chamber_y(y_mm)

        voxel_size_um = self._config.get("display", {}).get(
            "voxel_size_um", [50, 50, 50]
        )[0]
        voxel_size_mm = voxel_size_um / 1000.0
        x_range = self._config.get("stage_control", {}).get("x_range_mm", [1.0, 12.31])
        y_range = self._config.get("stage_control", {}).get("y_range_mm", [0.0, 14.0])
        z_range = self._config.get("stage_control", {}).get("z_range_mm", [12.5, 26.0])
        dims = self.voxel_storage.display_dims  # (Z, Y, X)

        if self._invert_x:
            napari_x = int((x_range[1] - x_mm) / voxel_size_mm)
        else:
            napari_x = int((x_mm - x_range[0]) / voxel_size_mm)
        napari_y_tip = int((y_range[1] - chamber_y_tip_mm) / voxel_size_mm)
        napari_z = int((z_mm - z_range[0]) / voxel_size_mm)

        napari_x = max(0, min(dims[2] - 1, napari_x))
        napari_y_tip = max(0, min(dims[1] - 1, napari_y_tip))
        napari_z = max(0, min(dims[0] - 1, napari_z))

        self.holder_position = {"x": napari_x, "y": napari_y_tip, "z": napari_z}

        self.logger.debug(
            f"Stage -> tip: ({x_mm:.2f}, {y_mm:.2f}, {z_mm:.2f}) -> "
            f"napari (Z={napari_z}, Y={napari_y_tip}, X={napari_x})"
        )

        self._update_holder_assembly()
        self._update_rotation_indicator()

    def _update_holder_assembly(self) -> None:
        """Refresh the FEP Tube + Holder Stem layers from the current sample-tip
        position. Both layers move with the stage as a rigid assembly; parts
        above the chamber top are clipped (not rendered)."""
        if not self.viewer:
            return
        if (
            "FEP Tube" not in self.viewer.layers
            or "Holder Stem" not in self.viewer.layers
        ):
            return
        if not self.voxel_storage:
            return
        try:
            fep_points, stem_points, _, _ = self._holder_assembly_columns()
            seed = np.zeros((1, 3), dtype=np.float32)
            fep_layer = self.viewer.layers["FEP Tube"]
            stem_layer = self.viewer.layers["Holder Stem"]
            if len(fep_points):
                fep_layer.data = fep_points
                fep_layer.visible = True
            else:
                fep_layer.data = seed
                fep_layer.visible = False
            if len(stem_points):
                stem_layer.data = stem_points
                stem_layer.visible = True
            else:
                stem_layer.data = seed
                stem_layer.visible = False
        except Exception as e:
            self.logger.debug(f"Holder assembly update skipped: {e}")

    def _update_rotation_indicator(self) -> None:
        """Update rotation indicator based on current rotation angle and holder position."""
        if not self.viewer or "Rotation Indicator" not in self.viewer.layers:
            return

        angle_deg = self.current_rotation.get("ry", 0)
        angle_rad = np.radians(angle_deg)

        indicator_color = self._get_rotation_gradient_color(angle_deg)

        # Indicator at the actual chamber top (mirrors _add_sample_holder),
        # following holder X/Z. With STEP loaded this is the cavity outer
        # top; without STEP it's the rectangular volume top (napari Y=0).
        stage_ctrl = self._config.get("stage_control", {})
        y_range = stage_ctrl.get("y_range_mm", [0.0, 14.0])
        voxel_size_mm = (
            self._config.get("display", {}).get("voxel_size_um", [50, 50, 50])[0]
            / 1000.0
        )
        y_position = (y_range[1] - self._chamber_top_stage_y_mm()) / voxel_size_mm
        start = np.array(
            [self.holder_position["z"], y_position, self.holder_position["x"]]
        )

        # End point rotated in ZX plane
        dx = self.rotation_indicator_length * np.cos(angle_rad)
        dz = self.rotation_indicator_length * np.sin(angle_rad)

        end = np.array([start[0] + dz, y_position, start[2] + dx])

        self.viewer.layers["Rotation Indicator"].data = [[start, end]]
        self.viewer.layers["Rotation Indicator"].edge_color = [indicator_color]

    def set_rotation(self, angle_deg: float) -> None:
        """Set the current rotation angle and update the indicator.

        Args:
            angle_deg: Rotation angle in degrees
        """
        self.current_rotation["ry"] = angle_deg
        self._update_rotation_indicator()

    def update_focus_frame(self) -> None:
        """Update XY focus frame position based on calibration.

        The focus frame is at a FIXED position (focal plane) and only needs
        to be updated when the calibration changes, not when the stage moves.
        """
        if not self.viewer or "XY Focus Frame" not in self.viewer.layers:
            return
        if not self.voxel_storage:
            return

        dims = self.voxel_storage.display_dims
        voxel_size_um = self._config.get("display", {}).get(
            "voxel_size_um", [50, 50, 50]
        )[0]
        voxel_size_mm = voxel_size_um / 1000.0

        focus_config = self._config.get("focus_frame", {})
        fov_x_mm = focus_config.get("field_of_view_x_mm", 0.52)
        fov_y_mm = focus_config.get("field_of_view_y_mm", 0.52)

        # Y position at objective focal plane
        y_range = self._config.get("stage_control", {}).get("y_range_mm", [0, 14])
        napari_y = int((y_range[1] - self.OBJECTIVE_CHAMBER_Y_MM) / voxel_size_mm)
        napari_y = min(max(0, napari_y), dims[1] - 1)

        # X and Z from calibration or use defaults
        if self.objective_xy_calibration:
            x_mm = self.objective_xy_calibration["x"]
            z_mm = self.objective_xy_calibration["z"]
        else:
            x_range = self._config.get("stage_control", {}).get(
                "x_range_mm", [1.0, 12.31]
            )
            z_range = self._config.get("stage_control", {}).get(
                "z_range_mm", [12.5, 26.0]
            )
            x_mm = (x_range[0] + x_range[1]) / 2
            z_mm = (z_range[0] + z_range[1]) / 2

        # Convert to napari coordinates
        x_range = self._config.get("stage_control", {}).get("x_range_mm", [1.0, 12.31])
        z_range = self._config.get("stage_control", {}).get("z_range_mm", [12.5, 26.0])

        if self._invert_x:
            napari_x = int((x_range[1] - x_mm) / voxel_size_mm)
        else:
            napari_x = int((x_mm - x_range[0]) / voxel_size_mm)
        napari_z = int((z_mm - z_range[0]) / voxel_size_mm)

        napari_x = max(0, min(dims[2] - 1, napari_x))
        napari_z = max(0, min(dims[0] - 1, napari_z))

        # FOV in voxels
        half_fov_x = (fov_x_mm / voxel_size_mm) / 2
        half_fov_y = (fov_y_mm / voxel_size_mm) / 2

        corners = [
            [napari_z, napari_y - half_fov_y, napari_x - half_fov_x],
            [napari_z, napari_y - half_fov_y, napari_x + half_fov_x],
            [napari_z, napari_y + half_fov_y, napari_x + half_fov_x],
            [napari_z, napari_y + half_fov_y, napari_x - half_fov_x],
        ]

        frame_edges = [
            [corners[0], corners[1]],
            [corners[1], corners[2]],
            [corners[2], corners[3]],
            [corners[3], corners[0]],
        ]

        self.viewer.layers["XY Focus Frame"].data = frame_edges

        self.logger.info(
            f"Updated XY focus frame to X={x_mm:.2f}, Z={z_mm:.2f} mm "
            f"(napari X={napari_x}, Z={napari_z})"
        )

    def setup_data_layers(self) -> None:
        """Setup napari layers for multi-channel data."""
        if not self.viewer or not self.voxel_storage:
            return

        channels_config = self._config.get(
            "channels",
            [
                {
                    "id": 0,
                    "name": "405nm (DAPI)",
                    "default_colormap": "cyan",
                    "default_visible": True,
                },
                {
                    "id": 1,
                    "name": "488nm (GFP)",
                    "default_colormap": "green",
                    "default_visible": True,
                },
                {
                    "id": 2,
                    "name": "561nm (RFP)",
                    "default_colormap": "red",
                    "default_visible": True,
                },
                {
                    "id": 3,
                    "name": "640nm (Far-Red)",
                    "default_colormap": "magenta",
                    "default_visible": False,
                },
                {
                    "id": 4,
                    "name": "405nm (DAPI) R",
                    "default_colormap": "cyan",
                    "default_visible": False,
                },
                {
                    "id": 5,
                    "name": "488nm (GFP) R",
                    "default_colormap": "green",
                    "default_visible": False,
                },
                {
                    "id": 6,
                    "name": "561nm (RFP) R",
                    "default_colormap": "red",
                    "default_visible": False,
                },
                {
                    "id": 7,
                    "name": "640nm (Far-Red) R",
                    "default_colormap": "magenta",
                    "default_visible": False,
                },
            ],
        )

        display_dims = self.voxel_storage.display_dims
        if len(display_dims) != 3 or any(d <= 0 for d in display_dims):
            self.logger.error(
                f"Invalid display_dims {display_dims}, cannot create data layers"
            )
            return

        # napari 0.7 initializes new layers with ndisplay=2 internally, but
        # the viewer's dims may already be set to ndisplay=3.  When the layer
        # is inserted, ndisplay is updated synchronously while async slicing
        # has not yet re-computed the data, leaving a stale 2D slice that
        # vispy's VolumeVisual rejects.  Work around this by adding layers in
        # 2D mode (where ImageNode and 2D data are consistent), then switching
        # back to 3D — napari will re-slice and recreate vispy nodes correctly.
        self.viewer.dims.ndisplay = 2

        for ch_config in channels_config:
            ch_id = ch_config["id"]
            ch_name = ch_config["name"]

            # Create empty volume
            empty_volume = np.zeros(display_dims, dtype=np.uint16)

            # Add layer. Pull the contrast limits from the channel's YAML
            # entry so LED/brightfield (which is much brighter than
            # fluorescence) can start at a different range than the
            # lasers without the user hunting for the slider.
            cmin = ch_config.get("default_contrast_min", 0)
            cmax = ch_config.get("default_contrast_max", 500)
            layer = self.viewer.add_image(
                empty_volume,
                name=ch_name,
                colormap=ch_config.get("default_colormap", "gray"),
                visible=ch_config.get("default_visible", True),
                blending="additive",
                opacity=0.8,
                rendering="mip",
                contrast_limits=(cmin, cmax),
            )

            self.channel_layers[ch_id] = layer

        # Restore 3D mode — triggers proper re-slice and VolumeNode creation
        self.viewer.dims.ndisplay = 3

        self.logger.info(f"Setup {len(self.channel_layers)} data layers")

    def ensure_layers_registered(self) -> None:
        """Ensure all data layers have vispy visuals registered.

        napari's vispy canvas maintains a layer_to_visual mapping. If layers
        were added during deferred setup before the canvas was fully ready,
        some visuals may not be registered. This method detects and repairs
        broken mappings by removing and re-adding affected layers.
        """
        if not self.viewer or not self.channel_layers:
            return

        canvas = getattr(self.viewer.window, "_qt_viewer", None)
        if canvas is None:
            return
        layer_to_visual = getattr(canvas, "layer_to_visual", None)
        if layer_to_visual is None:
            return

        repaired = 0
        layers_to_repair = [
            (ch_id, layer)
            for ch_id, layer in self.channel_layers.items()
            if layer not in layer_to_visual
        ]

        if not layers_to_repair:
            return

        # Switch to 2D to avoid napari 0.7 async slice race (see setup_data_layers)
        self.viewer.dims.ndisplay = 2

        for ch_id, layer in layers_to_repair:
            self.logger.warning(
                f"Channel {ch_id} layer not in vispy canvas — re-adding"
            )
            # Save layer properties
            data = np.array(layer.data) if layer.data is not None else None
            colormap = layer.colormap
            visible = layer.visible
            blending = layer.blending
            opacity = layer.opacity
            rendering = layer.rendering
            contrast_limits = layer.contrast_limits
            name = layer.name

            # Remove the broken layer
            try:
                self.viewer.layers.remove(layer)
            except Exception:
                pass

            # Re-add with same properties
            if data is None:
                data = np.zeros(self.voxel_storage.display_dims, dtype=np.uint16)
            new_layer = self.viewer.add_image(
                data,
                name=name,
                colormap=colormap,
                visible=visible,
                blending=blending,
                opacity=opacity,
                rendering=rendering,
                contrast_limits=contrast_limits,
            )
            self.channel_layers[ch_id] = new_layer
            repaired += 1

        # Restore 3D mode
        self.viewer.dims.ndisplay = 3

        self.logger.info(
            f"Repaired {repaired} channel layers with missing vispy visuals"
        )

    def default_camera_zoom(self) -> float:
        """Default 3D Volume View zoom (display.default_camera_zoom in config)."""
        return float(self._config.get("display", {}).get("default_camera_zoom", 0.4))

    def default_camera_angles(self) -> tuple:
        """Default 3D camera orientation (display.default_camera_angles in config).

        Per-microscope: a scope whose detection objective / stage axes are laid
        out differently (e.g. the ASLM/TSPIM scope with the objective on the
        right and X along the viewing axis) wants a different default viewpoint.
        Falls back to the historical (0, 0, 180).
        """
        angles = self._config.get("display", {}).get(
            "default_camera_angles", [0, 0, 180]
        )
        try:
            a = tuple(float(v) for v in angles)
            if len(a) == 3:
                return a
        except (TypeError, ValueError):
            pass
        self.logger.warning(
            "Invalid display.default_camera_angles %r; using (0, 0, 180)", angles
        )
        return (0.0, 0.0, 180.0)

    def reset_camera(self) -> None:
        """Reset the napari viewer camera to the per-scope default zoom + angles."""
        if self.viewer and hasattr(self.viewer, "camera"):
            zoom = self.default_camera_zoom()
            self.viewer.camera.zoom = zoom
            self.viewer.camera.angles = self.default_camera_angles()
            self.logger.info(
                "Reset viewer camera: zoom %s, angles %s",
                zoom,
                self.default_camera_angles(),
            )

    def load_objective_calibration(self, config=None) -> None:
        """Load objective XY calibration from position presets.

        The calibration point is saved as "Tip of sample mount" in position presets.
        This represents the stage position when the sample holder tip is centered
        in the live view - i.e., where the optical axis intersects the sample plane.

        Args:
            config: Optional config override (uses self._config if not provided)
        """
        cfg = config or self._config
        try:
            preset_service = PositionPresetService()
            preset_name = cfg.get("focus_frame", {}).get(
                "calibration_preset_name", "Tip of sample mount"
            )

            if preset_service.preset_exists(preset_name):
                preset = preset_service.get_preset(preset_name)
                self.objective_xy_calibration = {
                    "x": preset.x,
                    "y": preset.y,
                    "z": preset.z,
                    "r": preset.r,
                }
                self.logger.info(
                    f"Loaded objective calibration from '{preset_name}': "
                    f"X={preset.x:.3f}, Y={preset.y:.3f}, Z={preset.z:.3f}"
                )
            else:
                # Use default center position if not calibrated
                self.objective_xy_calibration = {
                    "x": cfg.get("stage_control", {}).get("x_default_mm", 6.0),
                    "y": cfg.get("stage_control", {}).get("y_default_mm", 7.0),
                    "z": cfg.get("stage_control", {}).get("z_default_mm", 19.0),
                    "r": 0,
                }
                self.logger.info(
                    f"No '{preset_name}' calibration found, using defaults"
                )
        except Exception as e:
            self.logger.warning(f"Failed to load objective calibration: {e}")
            self.objective_xy_calibration = None

    def set_objective_calibration(
        self, x: float, y: float, z: float, r: float = 0
    ) -> None:
        """Set and save the objective XY calibration point.

        Args:
            x, y, z: Stage position in mm when sample holder tip is centered in live view
            r: Rotation angle (stored but not critical for calibration)
        """
        from py2flamingo.models.microscope import Position

        self.objective_xy_calibration = {"x": x, "y": y, "z": z, "r": r}

        # Save to position presets
        try:
            preset_service = PositionPresetService()
            preset_name = self._config.get("focus_frame", {}).get(
                "calibration_preset_name", "Tip of sample mount"
            )
            position = Position(x=x, y=y, z=z, r=r)
            preset_service.save_preset(
                preset_name,
                position,
                "Calibration point: sample holder tip centered in live view",
            )
            self.logger.info(
                f"Saved objective calibration to '{preset_name}': "
                f"X={x:.3f}, Y={y:.3f}, Z={z:.3f}"
            )
        except Exception as e:
            self.logger.error(f"Failed to save objective calibration: {e}")

        # Update focus frame if it exists
        if self.viewer and "XY Focus Frame" in self.viewer.layers:
            self.update_focus_frame()
