"""
Voxel Storage Factory - Creates 3D visualization storage and coordinate systems.

Extracts voxel storage creation from FlamingoApplication into a reusable factory
that loads config and creates DualResolutionVoxelStorage + coordinate transformers.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class VoxelStorageBundle:
    """All components needed for 3D voxel visualization."""
    voxel_storage: object  # DualResolutionVoxelStorage
    config: dict
    coord_mapper: object  # PhysicalToNapariMapper
    coord_transformer: object  # CoordinateTransformer


def get_default_visualization_config() -> dict:
    """Return default visualization config if YAML not found."""
    return {
        'display': {
            'voxel_size_um': [50, 50, 50],
            'fps_target': 30,
            'downsample_factor': 4,
            'max_channels': 4
        },
        'storage': {
            'voxel_size_um': [5, 5, 5],
            'backend': 'sparse',
            'max_memory_mb': 2000
        },
        'sample_chamber': {
            'inner_dimensions_mm': [10, 10, 43],
            'holder_diameter_mm': 1.0,
            'sample_region_center_um': [6655, 7000, 19250],
            'sample_region_radius_um': 2000,
        },
        'stage_control': {
            'x_range_mm': [1.0, 12.31],
            'y_range_mm': [-5.0, 10.0],
            'z_range_mm': [12.5, 26.0],
            'invert_x_default': False,
            'invert_z_default': False,
        },
        'channels': [
            {'id': 0, 'name': '405nm (DAPI)', 'default_colormap': 'cyan', 'default_visible': True},
            {'id': 1, 'name': '488nm (GFP)', 'default_colormap': 'green', 'default_visible': True},
            {'id': 2, 'name': '561nm (RFP)', 'default_colormap': 'red', 'default_visible': True},
            {'id': 3, 'name': '640nm (Far-Red)', 'default_colormap': 'magenta', 'default_visible': False}
        ]
    }


def create_voxel_storage(config_path: Optional[str] = None) -> Optional[VoxelStorageBundle]:
    """Create voxel storage for 3D visualization.

    Creates DualResolutionVoxelStorage and CoordinateTransformer
    that will be passed to Sample View for 3D data accumulation.

    Args:
        config_path: Path to visualization_3d_config.yaml.
            Defaults to configs/visualization_3d_config.yaml relative to py2flamingo package.

    Returns:
        VoxelStorageBundle with all components, or None on failure.
    """
    import yaml

    # Load visualization config
    if config_path is None:
        config_path = Path(__file__).parent.parent / "configs" / "visualization_3d_config.yaml"
    else:
        config_path = Path(config_path)

    if config_path.exists():
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        logger.info(f"Loaded 3D visualization config from {config_path}")
    else:
        logger.warning("Using default 3D visualization config")
        config = get_default_visualization_config()

    try:
        from py2flamingo.visualization.dual_resolution_storage import DualResolutionVoxelStorage, DualResolutionConfig
        from py2flamingo.visualization.coordinate_transforms import CoordinateTransformer, PhysicalToNapariMapper

        # Initialize coordinate mapper
        mapper_config = {
            'x_range_mm': config['stage_control']['x_range_mm'],
            'y_range_mm': config['stage_control']['y_range_mm'],
            'z_range_mm': config['stage_control']['z_range_mm'],
            'voxel_size_um': config['display']['voxel_size_um'][0],
            'invert_x': config['stage_control']['invert_x_default'],
            'invert_z': config['stage_control']['invert_z_default']
        }
        coord_mapper = PhysicalToNapariMapper(mapper_config)

        # Initialize coordinate transformer
        sample_center_um = config['sample_chamber']['sample_region_center_um']
        transformer = CoordinateTransformer(sample_center=sample_center_um)

        # Get dimensions from coordinate mapper
        mapper_dims = coord_mapper.get_napari_dimensions()
        voxel_size_um = config['display']['voxel_size_um'][0]

        # Napari expects dimensions in (Z, Y, X) order
        napari_dims = (mapper_dims[2], mapper_dims[1], mapper_dims[0])

        # Calculate chamber dimensions in um (Z, Y, X order)
        chamber_dims_um = (
            napari_dims[0] * voxel_size_um,
            napari_dims[1] * voxel_size_um,
            napari_dims[2] * voxel_size_um
        )

        # Calculate chamber origin in world coordinates
        chamber_origin_um = (
            config['stage_control']['z_range_mm'][0] * 1000,
            config['stage_control']['y_range_mm'][0] * 1000,
            config['stage_control']['x_range_mm'][0] * 1000
        )

        # Check for asymmetric bounds
        half_widths = None
        if all(key in config['sample_chamber'] for key in
               ['sample_region_half_width_x_um', 'sample_region_half_width_y_um', 'sample_region_half_width_z_um']):
            half_widths = (
                config['sample_chamber']['sample_region_half_width_z_um'],
                config['sample_chamber']['sample_region_half_width_y_um'],
                config['sample_chamber']['sample_region_half_width_x_um']
            )

        # Reorder sample_region_center from config's X,Y,Z to storage's Z,Y,X format
        center_xyz = config['sample_chamber']['sample_region_center_um']
        center_zyx = (center_xyz[2], center_xyz[1], center_xyz[0])

        # Reorder voxel sizes from X,Y,Z to Z,Y,X
        storage_voxel_xyz = config['storage']['voxel_size_um']
        storage_voxel_zyx = (storage_voxel_xyz[2], storage_voxel_xyz[1], storage_voxel_xyz[0])

        display_voxel_xyz = config['display']['voxel_size_um']
        display_voxel_zyx = (display_voxel_xyz[2], display_voxel_xyz[1], display_voxel_xyz[0])

        storage_config = DualResolutionConfig(
            storage_voxel_size=storage_voxel_zyx,
            display_voxel_size=display_voxel_zyx,
            chamber_dimensions=chamber_dims_um,
            chamber_origin=chamber_origin_um,
            sample_region_center=center_zyx,
            sample_region_radius=config['sample_chamber']['sample_region_radius_um'],
            sample_region_half_widths=half_widths,
            invert_x=config['stage_control']['invert_x_default']
        )

        voxel_storage = DualResolutionVoxelStorage(storage_config)
        voxel_storage.set_coordinate_transformer(transformer)

        logger.info(f"Created voxel storage: display dims {voxel_storage.display_dims}")

        return VoxelStorageBundle(
            voxel_storage=voxel_storage,
            config=config,
            coord_mapper=coord_mapper,
            coord_transformer=transformer
        )

    except Exception as e:
        logger.error(f"Failed to create voxel storage: {e}")
        return None
