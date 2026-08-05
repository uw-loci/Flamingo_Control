"""Command-line entry point for PSF analysis.

    python -m py2flamingo.psf_analysis BEADS.ome.tif --xy-um 0.406 --z-um 4.0

Runs the same core as the GUI dialog with no Qt / microscope needed, which is
what makes ``psf_analysis`` independently runnable (and is the natural standalone
entry point if the package is later split out, as the stitcher was).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from py2flamingo.psf_analysis.io import load_volume
from py2flamingo.psf_analysis.service import PSFAnalysisService, PSFSettings


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m py2flamingo.psf_analysis",
        description="Measure optical resolution (FWHM) from a 3-D bead image.",
    )
    p.add_argument("input", help="Bead image (.tif/.tiff/.ome.tif, .npy, or .zarr dir)")
    p.add_argument(
        "--xy-um",
        type=float,
        default=None,
        help="XY pixel size in µm (overrides file metadata)",
    )
    p.add_argument(
        "--z-um",
        type=float,
        default=None,
        help="Z step in µm (overrides file metadata)",
    )
    p.add_argument("--channel", type=int, default=None, help="Channel to analyze")
    p.add_argument(
        "--csv", type=Path, default=None, help="Write per-bead CSV to this path"
    )
    p.add_argument(
        "--window-um", type=float, default=6.0, help="Crop window per axis (µm)"
    )
    p.add_argument(
        "--min-distance-px",
        type=int,
        default=10,
        help="Minimum separation between detected peaks (px)",
    )
    p.add_argument(
        "--threshold-rel",
        type=float,
        default=0.2,
        help="Detection threshold as fraction of max intensity",
    )
    p.add_argument(
        "--min-separation-um",
        type=float,
        default=10.0,
        help="Reject beads with a neighbor closer than this (µm)",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    volume, (z_file, y_file, x_file) = load_volume(args.input, channel=args.channel)

    x_um = args.xy_um if args.xy_um is not None else x_file
    y_um = args.xy_um if args.xy_um is not None else (y_file or x_file)
    z_um = args.z_um if args.z_um is not None else z_file
    missing = [n for n, v in (("--xy-um", x_um), ("--z-um", z_um)) if v is None]
    if missing:
        print(
            f"error: voxel size not in file metadata; pass {', '.join(missing)}",
            file=sys.stderr,
        )
        return 2

    settings = PSFSettings(
        threshold_rel=args.threshold_rel,
        min_distance_px=args.min_distance_px,
        window_um=args.window_um,
        min_separation_um=args.min_separation_um,
    )
    result = PSFAnalysisService().analyze(
        volume, voxel_size_um=(float(z_um), float(y_um), float(x_um)), settings=settings
    )

    summary = result.summary()
    print(f"\nDetected {result.n_detected} beads, {result.n_accepted} accepted.")
    for axis in ("x", "y", "z"):
        mean = summary.get(f"fwhm_{axis}_um_mean")
        std = summary.get(f"fwhm_{axis}_um_std")
        if mean is not None:
            print(f"  FWHM {axis.upper()}: {mean:.3f} ± {std:.3f} µm")
        else:
            print(f"  FWHM {axis.upper()}: (no valid fit)")

    if args.csv:
        result.to_csv(args.csv)
        print(f"\nWrote per-bead results to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
