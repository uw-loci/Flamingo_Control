"""CLI entry point for the stitching pipeline.

Usage:
    python -m py2flamingo.stitching /path/to/acquisition -o /path/to/output

Examples:
    # Basic stitching with sharded OME-Zarr output (default)
    python -m py2flamingo.stitching /data/20260310_acquisition

    # Custom pixel size and Z step
    python -m py2flamingo.stitching /data/acq --pixel-size-um 0.812 --z-step-um 2.5

    # Full preprocessing pipeline
    python -m py2flamingo.stitching /data/acq --destripe --illumination-fusion leonardo --deconvolution

    # Output as pyramidal OME-TIFF (single file)
    python -m py2flamingo.stitching /data/acq --output-format ome-tiff

    # Write both OME-Zarr and OME-TIFF, plus package as .ozx
    python -m py2flamingo.stitching /data/acq --output-format both --package-ozx

    # Dry run (discover tiles only, no processing)
    python -m py2flamingo.stitching /data/acq --dry-run
"""

import argparse
import logging
import sys
from pathlib import Path

from .pipeline import StitchingConfig, StitchingPipeline, discover_tiles


def main():
    parser = argparse.ArgumentParser(
        description="Stitch Flamingo T-SPIM raw acquisitions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "acquisition_dir",
        type=Path,
        help="Root directory containing tile folders (X{x}_Y{y}/ subfolders)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output directory (default: {acquisition_dir}/stitched)",
    )

    # Voxel size
    voxel_group = parser.add_argument_group("Voxel geometry")
    voxel_group.add_argument(
        "--pixel-size-um",
        type=float,
        default=0.406,
        help="XY pixel size in micrometers (default: 0.406)",
    )
    voxel_group.add_argument(
        "--z-step-um",
        type=float,
        default=None,
        help="Z step in micrometers (default: computed from Workflow.txt)",
    )

    # Preprocessing
    preproc_group = parser.add_argument_group("Preprocessing")
    preproc_group.add_argument(
        "--illumination-fusion",
        choices=["max", "mean", "leonardo"],
        default="max",
        help="Dual-illumination fusion method (default: max)",
    )
    preproc_group.add_argument(
        "--flat-field",
        action="store_true",
        help="Apply BaSiC flat-field correction (requires basicpy)",
    )
    preproc_group.add_argument(
        "--destripe",
        action="store_true",
        help="Apply PyStripe destriping (requires pystripe)",
    )
    preproc_group.add_argument(
        "--depth-attenuation",
        action="store_true",
        help="Correct exponential Z-intensity falloff (Beer-Lambert model)",
    )
    preproc_group.add_argument(
        "--depth-attenuation-mu",
        type=float,
        default=None,
        help="Decay coefficient mu (1/um); omit for auto-fit from data",
    )
    preproc_group.add_argument(
        "--deconvolution",
        action="store_true",
        help="Apply GPU deconvolution (requires pycudadecon or RedLionfish)",
    )
    preproc_group.add_argument(
        "--deconv-engine",
        choices=["pycudadecon", "redlionfish"],
        default="pycudadecon",
        help="Deconvolution engine (default: pycudadecon)",
    )
    preproc_group.add_argument(
        "--deconv-iterations",
        type=int,
        default=10,
        help="Richardson-Lucy iterations (default: 10)",
    )

    # Registration
    reg_group = parser.add_argument_group("Registration")
    reg_group.add_argument(
        "--reg-channel",
        type=int,
        default=0,
        help="Channel to use for registration (default: 0)",
    )
    reg_group.add_argument(
        "--quality-threshold",
        type=float,
        default=0.2,
        help="Min phase correlation quality (default: 0.2)",
    )
    reg_group.add_argument(
        "--channels",
        type=int,
        nargs="+",
        default=None,
        help="Channel indices to process (default: all)",
    )

    # Output format
    output_group = parser.add_argument_group("Output")
    output_group.add_argument(
        "--output-format",
        choices=["tiff", "ome-zarr", "ome-zarr-sharded", "ome-tiff", "both"],
        default="ome-zarr-sharded",
        help="Output format (default: ome-zarr-sharded)",
    )
    output_group.add_argument(
        "--package-ozx",
        action="store_true",
        help="Also create .ozx (single ZIP file) from OME-Zarr output",
    )
    output_group.add_argument(
        "--zarr-compression",
        choices=["zstd", "lz4", "blosc", "none"],
        default="zstd",
        help="Zarr compression codec (default: zstd)",
    )
    output_group.add_argument(
        "--tiff-compression",
        choices=["zlib", "lzw", "zstd", "none"],
        default="zlib",
        help="TIFF compression codec (default: zlib)",
    )
    output_group.add_argument(
        "--use-tensorstore",
        action="store_true",
        help="Use TensorStore backend for Zarr writes (faster for large data)",
    )

    # Pyramid
    pyramid_group = parser.add_argument_group("Multi-resolution pyramid")
    pyramid_group.add_argument(
        "--pyramid-levels",
        type=int,
        default=None,
        help="Number of pyramid levels (default: auto)",
    )
    pyramid_group.add_argument(
        "--pyramid-method",
        choices=["itkwasm_bin_shrink", "itkwasm_gaussian", "dask_image_gaussian"],
        default="itkwasm_bin_shrink",
        help="Pyramid downsampling method (default: itkwasm_bin_shrink)",
    )

    # Utility
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover and report tiles without processing",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Validate input
    acq_dir = args.acquisition_dir.resolve()
    if not acq_dir.is_dir():
        print(f"Error: {acq_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Dry run: just discover and report tiles
    if args.dry_run:
        tiles = discover_tiles(acq_dir)
        if not tiles:
            print("No tile folders found.")
            sys.exit(1)
        print(f"\nFound {len(tiles)} tiles:\n")
        for i, t in enumerate(tiles):
            print(
                f"  {i + 1:3d}. {t.folder.name}  "
                f"X={t.x_mm:8.3f}  Y={t.y_mm:8.3f}  "
                f"Z=[{t.z_min_mm:.3f}, {t.z_max_mm:.3f}]  "
                f"planes={t.n_planes}  ch={t.channels}  illum={t.illumination_sides}"
            )
        xs = sorted(set(t.x_mm for t in tiles))
        ys = sorted(set(t.y_mm for t in tiles))
        print(f"\nGrid: ~{len(xs)} x {len(ys)} tiles")
        print(
            f"Total raw files: {sum(sum(len(v) for v in t.raw_files.values()) for t in tiles)}"
        )
        sys.exit(0)

    # Build config
    config = StitchingConfig(
        pixel_size_um=args.pixel_size_um,
        z_step_um=args.z_step_um,
        illumination_fusion=args.illumination_fusion,
        flat_field_correction=args.flat_field,
        destripe=args.destripe,
        depth_attenuation=args.depth_attenuation,
        depth_attenuation_mu=args.depth_attenuation_mu,
        reg_channel=args.reg_channel,
        quality_threshold=args.quality_threshold,
        output_format=args.output_format,
        package_ozx=args.package_ozx,
        zarr_compression=args.zarr_compression,
        zarr_use_tensorstore=args.use_tensorstore,
        tiff_compression=args.tiff_compression,
        pyramid_levels=args.pyramid_levels,
        pyramid_method=args.pyramid_method,
        deconvolution_enabled=args.deconvolution,
        deconvolution_engine=args.deconv_engine,
        deconvolution_iterations=args.deconv_iterations,
    )

    # Output path
    output_path = args.output or acq_dir / "stitched"

    # Run
    pipeline = StitchingPipeline(config)
    try:
        result = pipeline.run(acq_dir, output_path, channels=args.channels)
        print(f"\nStitched output: {result}")
    except ImportError as e:
        print(f"\nMissing dependency: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"\nNo data found: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logging.getLogger(__name__).exception("Pipeline failed")
        print(f"\nPipeline failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
