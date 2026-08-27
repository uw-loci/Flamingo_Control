"""
Voxel Storage Factory - Creates 3D visualization storage and coordinate systems.

Extracts voxel storage creation from FlamingoApplication into a reusable factory
that loads config and creates DualResolutionVoxelStorage + coordinate transformers.
"""

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from py2flamingo.configs.config_loader import deep_merge

logger = logging.getLogger(__name__)


@dataclass
class VoxelStorageBundle:
    """All components needed for 3D voxel visualization."""

    voxel_storage: object  # DualResolutionVoxelStorage
    config: dict
    coord_mapper: object  # PhysicalToNapariMapper
    coord_transformer: object  # CoordinateTransformer


def _default_config_path() -> Path:
    return Path(__file__).parent.parent / "configs" / "visualization_3d_config.yaml"


# The merge rule lives in the config layer and is shared with the hardware
# config's own ``microscopes:`` overlay (config_loader._apply_microscope_overlay).
# One implementation, so the two per-microscope overlays cannot drift apart in
# how they combine a scope's entry with the base.
_deep_merge = deep_merge


def resolve_visualization_config(
    microscope_name: Optional[str] = None,
    config_path: Optional[str] = None,
) -> dict:
    """Load the base viz config and overlay the per-microscope section.

    The base ``visualization_3d_config.yaml`` may carry a ``microscopes:`` map
    keyed by microscope name. When ``microscope_name`` matches an entry (case-
    insensitively — the name comes from ``get_microscope_name()``), that entry is
    **deep-merged over the base**, so a scope can override ``orientation``,
    ``display.default_camera_angles``, ``step_chamber``, etc. No name / no match
    => the base config unchanged (legacy scope). The ``microscopes`` map itself is
    stripped from the returned dict so consumers never see it.

    Both the storage factory and Sample View resolve through this single function
    so they can never disagree on the active orientation.
    """
    import yaml

    path = Path(config_path) if config_path else _default_config_path()
    try:
        if path.exists():
            with open(path, "r") as f:
                config = yaml.safe_load(f) or {}
        else:
            logger.warning("Viz config %s not found; using defaults", path)
            config = get_default_visualization_config()
    except Exception as e:  # noqa: BLE001 - fall back rather than crash the viewer
        logger.warning("Could not load viz config %s: %s", path, e)
        config = get_default_visualization_config()

    micros = config.get("microscopes") or {}
    if microscope_name and micros:
        # .strip() as well as .lower(), matching config_loader's hardware
        # overlay exactly. The two claim to key off the same name; a YAML key
        # written as " liara" matching one and not the other would give a scope
        # its own optics and another scope's chamber geometry.
        match = next(
            (
                k
                for k in micros
                if str(k).strip().lower() == str(microscope_name).strip().lower()
            ),
            None,
        )
        if match is not None:
            logger.info("Applying per-microscope viz overlay for '%s'", microscope_name)
            config = _deep_merge(config, micros.get(match) or {})
    config.pop("microscopes", None)
    return config


def get_default_visualization_config() -> dict:
    """Return default visualization config if YAML not found."""
    return {
        "display": {
            "voxel_size_um": [50, 50, 50],
            "fps_target": 30,
            "downsample_factor": 4,
            "max_channels": 4,
        },
        "storage": {
            "voxel_size_um": [5, 5, 5],
            "backend": "sparse",
            "max_memory_mb": 2000,
        },
        # HARDWARE dimensions (holder_diameter_mm, etc.) are intentionally
        # omitted from this fallback. They must be set explicitly in the
        # user's visualization_3d_config.yaml. A wrong holder size could
        # let an oversized holder collide with the chamber wall undetected.
        "sample_chamber": {
            "inner_dimensions_mm": [10, 10, 43],
            "sample_region_center_um": [6655, 7000, 19250],
            "sample_region_radius_um": 2000,
        },
        "stage_control": {
            "x_range_mm": [1.0, 12.31],
            "y_range_mm": [-5.0, 10.0],
            "z_range_mm": [12.5, 26.0],
            "invert_x_default": False,
            "invert_z_default": False,
        },
        "channels": [
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
        ],
    }


def create_voxel_storage(
    config_path: Optional[str] = None,
    microscope_name: Optional[str] = None,
) -> Optional[VoxelStorageBundle]:
    """Create voxel storage for 3D visualization.

    Creates DualResolutionVoxelStorage and CoordinateTransformer
    that will be passed to Sample View for 3D data accumulation.

    Args:
        config_path: Path to visualization_3d_config.yaml.
            Defaults to configs/visualization_3d_config.yaml relative to py2flamingo package.
        microscope_name: When given, the per-microscope ``microscopes:`` overlay
            for that scope is applied (orientation, camera, chamber, …) — see
            :func:`resolve_visualization_config`. Must be the SAME name Sample
            View resolves with, so storage and display agree on the orientation.

    Returns:
        VoxelStorageBundle with all components, or None on failure.
    """
    # Load visualization config (with the per-microscope overlay applied).
    config = resolve_visualization_config(
        microscope_name=microscope_name, config_path=config_path
    )

    try:
        from py2flamingo.visualization.axis_orientation import AxisOrientation
        from py2flamingo.visualization.coordinate_transforms import (
            CoordinateTransformer,
            PhysicalToNapariMapper,
        )
        from py2flamingo.visualization.dual_resolution_storage import (
            DualResolutionConfig,
            DualResolutionVoxelStorage,
        )

        # Per-microscope stage->napari orientation (top-level 'orientation' block
        # in the viz config; None/absent -> legacy convention from invert flags).
        orientation = AxisOrientation.from_config(
            config,
            invert_x=config["stage_control"]["invert_x_default"],
            invert_z=config["stage_control"]["invert_z_default"],
        )

        # Initialize coordinate mapper
        mapper_config = {
            "x_range_mm": config["stage_control"]["x_range_mm"],
            "y_range_mm": config["stage_control"]["y_range_mm"],
            "z_range_mm": config["stage_control"]["z_range_mm"],
            "voxel_size_um": config["display"]["voxel_size_um"][0],
            "invert_x": config["stage_control"]["invert_x_default"],
            "invert_z": config["stage_control"]["invert_z_default"],
            "orientation_obj": orientation,
        }
        coord_mapper = PhysicalToNapariMapper(mapper_config)

        # Initialize coordinate transformer
        sample_center_um = config["sample_chamber"]["sample_region_center_um"]
        transformer = CoordinateTransformer(sample_center=sample_center_um)

        # Get dimensions from coordinate mapper
        mapper_dims = coord_mapper.get_napari_dimensions()
        voxel_size_um = config["display"]["voxel_size_um"][0]

        # Napari expects dimensions in (Z, Y, X) order
        napari_dims = (mapper_dims[2], mapper_dims[1], mapper_dims[0])

        # Calculate chamber dimensions in um (Z, Y, X order)
        chamber_dims_um = (
            napari_dims[0] * voxel_size_um,
            napari_dims[1] * voxel_size_um,
            napari_dims[2] * voxel_size_um,
        )

        # Calculate chamber origin in world coordinates, ordered per orientation
        # so the world/storage frame lines up with the (per-orientation) display
        # dimensions above. Legacy orientation => (z_min, y_min, x_min) as before.
        _xr = config["stage_control"]["x_range_mm"]
        _yr = config["stage_control"]["y_range_mm"]
        _zr = config["stage_control"]["z_range_mm"]
        chamber_origin_um = tuple(
            v * 1000
            for v in orientation.order_by_display(
                {"x": _xr[0], "y": _yr[0], "z": _zr[0]}
            )
        )

        # Check for asymmetric bounds
        half_widths = None
        if all(
            key in config["sample_chamber"]
            for key in [
                "sample_region_half_width_x_um",
                "sample_region_half_width_y_um",
                "sample_region_half_width_z_um",
            ]
        ):
            hw_x = float(config["sample_chamber"]["sample_region_half_width_x_um"])
            hw_y = float(config["sample_chamber"]["sample_region_half_width_y_um"])
            hw_z = float(config["sample_chamber"]["sample_region_half_width_z_um"])

            # The stage rotates the sample about the VERTICAL (Y) axis, so X and
            # Z sweep into one another: a feature 7 mm deep in Z at R=0 sits 7 mm
            # out in X at R=90. Sizing each axis to its own chamber extent gives
            # a box that only holds the sample at one rotation.
            #
            # It fails exactly as you would expect once it fails at all. On
            # 2026-08-08 a 213 degree acquisition put every tile at world
            # X = 13.6-14.7 mm against an X ceiling of 12.65 mm, and all 51,062
            # tile chunks were rejected as "outside storage bounds" — the whole
            # dataset dropped, while the sample never came near the chamber wall.
            #
            # Both in-plane axes therefore get the circumscribed radius, which is
            # the smallest bound that holds the sample at EVERY rotation. Storage
            # is sparse (flat-index keyed), so this widens the index space, not
            # the allocation — memory still tracks occupied voxels only.
            in_plane = math.hypot(hw_x, hw_z)
            if in_plane > max(hw_x, hw_z):
                logger.info(
                    "Storage X/Z half-widths widened for rotation: "
                    f"X ±{hw_x:.0f} / Z ±{hw_z:.0f} → ±{in_plane:.0f} µm "
                    "(circumscribed radius about the vertical axis, so a sample "
                    "that fits at R=0 also fits at every other R)"
                )
            hw_x = hw_z = in_plane

            half_widths = orientation.order_by_display(
                {"x": hw_x, "y": hw_y, "z": hw_z}
            )

        # Reorder sample_region_center (config X,Y,Z) into display (depth,vert,
        # horiz) order per orientation. Legacy => (Z, Y, X) as before.
        center_xyz = config["sample_chamber"]["sample_region_center_um"]
        center_zyx = orientation.order_by_display(
            {"x": center_xyz[0], "y": center_xyz[1], "z": center_xyz[2]}
        )

        # Reorder voxel sizes from X,Y,Z to Z,Y,X
        storage_voxel_xyz = config["storage"]["voxel_size_um"]
        storage_voxel_zyx = (
            storage_voxel_xyz[2],
            storage_voxel_xyz[1],
            storage_voxel_xyz[0],
        )

        display_voxel_xyz = config["display"]["voxel_size_um"]
        display_voxel_zyx = (
            display_voxel_xyz[2],
            display_voxel_xyz[1],
            display_voxel_xyz[0],
        )

        storage_config = DualResolutionConfig(
            storage_voxel_size=storage_voxel_zyx,
            display_voxel_size=display_voxel_zyx,
            chamber_dimensions=chamber_dims_um,
            chamber_origin=chamber_origin_um,
            sample_region_center=center_zyx,
            sample_region_radius=config["sample_chamber"]["sample_region_radius_um"],
            sample_region_half_widths=half_widths,
            invert_x=config["stage_control"]["invert_x_default"],
            orientation=orientation,
        )

        voxel_storage = DualResolutionVoxelStorage(storage_config)
        voxel_storage.set_coordinate_transformer(transformer)

        logger.info(f"Created voxel storage: display dims {voxel_storage.display_dims}")

        return VoxelStorageBundle(
            voxel_storage=voxel_storage,
            config=config,
            coord_mapper=coord_mapper,
            coord_transformer=transformer,
        )

    except Exception as e:
        logger.error(f"Failed to create voxel storage: {e}")
        return None
