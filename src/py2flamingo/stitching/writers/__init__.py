"""Output writers for the stitching pipeline.

Supported formats:
- OME-Zarr v0.5 with sharding (primary, multi-resolution pyramid)
- Pyramidal OME-TIFF BigTIFF (single file, universal viewer compatibility)
- .ozx (single ZIP file for sharing, via ngff-zarr)
"""

from .ome_tiff_writer import write_pyramidal_ome_tiff
from .ome_zarr_writer import write_ome_zarr_sharded
