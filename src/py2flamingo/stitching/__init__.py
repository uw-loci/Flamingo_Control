"""Compatibility shim — stitching now lives in the standalone package.

The stitching pipeline was extracted into the standalone ``flamingo_stitcher``
package (https://github.com/uw-loci/flamingo-stitcher) so it can be installed
and run on its own, while this control app continues to use the *same code*
(single source of truth — no drift).

This module re-exports the public API and aliases the former
``py2flamingo.stitching.*`` submodules to their ``flamingo_stitcher.*``
counterparts, so existing imports such as
``from py2flamingo.stitching.pipeline import StitchingConfig`` keep working
unchanged. The old per-module ``.py`` files in this directory are now dead
code retained temporarily; they will be removed once the integration is
confirmed in the running app.
"""

import sys

import flamingo_stitcher
from flamingo_stitcher import (
    StitchingConfig,
    StitchingPipeline,
    deconvolution,
    depth_attenuation,
    discover_tiles,
    flat_field,
    isolated_service,
    isolated_worker,
    multi_phase_estimator,
    pipeline,
    timing_cache,
    worker,
    writers,
)
from flamingo_stitcher.writers import (
    imaris_writer,
    ome_tiff_writer,
    ome_zarr_writer,
)

# Alias submodules so ``from py2flamingo.stitching.X import ...`` resolves to
# the flamingo_stitcher implementation (Python checks sys.modules first, so the
# physical files alongside this shim are shadowed and never imported).
_PKG = __name__
for _name, _mod in {
    "pipeline": pipeline,
    "worker": worker,
    "deconvolution": deconvolution,
    "depth_attenuation": depth_attenuation,
    "flat_field": flat_field,
    "isolated_service": isolated_service,
    "isolated_worker": isolated_worker,
    "multi_phase_estimator": multi_phase_estimator,
    "timing_cache": timing_cache,
    "writers": writers,
}.items():
    sys.modules[f"{_PKG}.{_name}"] = _mod

sys.modules[f"{_PKG}.writers.ome_zarr_writer"] = ome_zarr_writer
sys.modules[f"{_PKG}.writers.ome_tiff_writer"] = ome_tiff_writer
sys.modules[f"{_PKG}.writers.imaris_writer"] = imaris_writer

__all__ = ["StitchingConfig", "StitchingPipeline", "discover_tiles"]
