"""
Lightsheet stitching pipeline for Flamingo T-SPIM data.

Converts raw acquisition folders into stitched volumes using
multiview-stitcher for registration and fusion.

Pipeline:
    Raw uint16 → [dual-illum fusion] → [depth attenuation] → [destripe]
    → [deconvolution] → register → stitch → output (OME-Zarr / OME-TIFF / both)

Dependencies (install separately):
    pip install multiview-stitcher
    pip install pystripe                  # optional, for destriping
    pip install leonardo-toolset          # optional, for dual-illumination fusion
    pip install ngff-zarr                 # optional, for sharded OME-Zarr + pyramids
    pip install tifffile                  # optional, for pyramidal OME-TIFF
    conda install -c conda-forge pycudadecon  # optional, GPU deconvolution
"""

from .pipeline import StitchingConfig, StitchingPipeline, discover_tiles
