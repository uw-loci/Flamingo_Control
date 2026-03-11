"""
Lightsheet stitching pipeline for Flamingo T-SPIM data.

Converts raw acquisition folders into stitched volumes using
multiview-stitcher for registration and fusion.

Pipeline:  Raw uint16 → [destripe] → [dual-illum fusion] → register → stitch → output

Dependencies (install separately):
    pip install multiview-stitcher
    pip install pystripe          # optional, for destriping
    pip install leonardo-toolset  # optional, for dual-illumination fusion
"""

from .pipeline import StitchingConfig, StitchingPipeline, discover_tiles
