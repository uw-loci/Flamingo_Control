"""Synthesize phantom datasets for offline pipeline / stitching testing.

Two output shapes, both written straight to disk (no microscope, no socket):

* **raw acquisition folder** (:func:`write_raw_acquisition`) — the microscope's
  native on-disk layout (``X{x}_Y{y}/`` tile folders, ``Workflow.txt``, headerless
  ``.raw`` stacks). This is the *gold-standard* test input: it flows through the
  real ``discover_tiles`` → stitching → analysis chain. Raw frames are sized from
  the global sensor config (``microscope_hardware.yaml``, 2048×2048), because the
  stitching reader assumes that size — so a small set is a few tiles × few planes.

* **stitched dataset** (:func:`write_stitched_dataset`) — a small, already-fused
  multi-channel OME-TIFF plus a ready-to-run pipeline JSON. The *fast* path for
  iterating on analysis pipelines without re-stitching every time.

The fiber content (:func:`make_phantom_volume`) is a deterministic wavy-line
field so adjacent, overlapping raw tiles share recognizable structure for
registration.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_UINT16_MAX = 65535


# ---------------------------------------------------------------------------
# Phantom content
# ---------------------------------------------------------------------------


def make_phantom_volume(
    shape: Tuple[int, int, int],
    *,
    n_fibers: int = 12,
    value: int = 4000,
    background: int = 200,
    seed: int = 0,
) -> np.ndarray:
    """Build a deterministic 3-D collagen-like volume, dtype ``uint16``.

    Bright wavy horizontal fibers on a dim background. The waviness is a fixed
    function of the global (mosaic) coordinate so that overlapping crops of a
    larger field line up — useful for exercising tile registration.

    Args:
        shape: ``(Z, Y, X)``.
        n_fibers: Number of fibers spread across Y.
        value: Peak fiber intensity.
        background: Flat background level.
        seed: Varies fiber phase/placement between calls.
    """
    z, y, x = shape
    vol = np.full(shape, background, dtype=np.uint16)
    xs = np.arange(x)
    rng = np.random.default_rng(seed)
    base_ys = np.linspace(y * 0.08, y * 0.92, n_fibers)
    for i, by in enumerate(base_ys):
        amp = 0.02 * y * (1.0 + 0.5 * ((i + seed) % 3))
        period = x / (2.0 + (i % 3))
        phase = (i + seed) * 0.7
        wave = by + amp * np.sin(2 * np.pi * xs / max(period, 1.0) + phase)
        thickness = 1 + (i % 2)
        for dy in range(-thickness, thickness + 1):
            yy = np.clip(np.round(wave + dy).astype(int), 0, y - 1)
            # Fade fibers across Z so the stack isn't uniform.
            for zi in range(z):
                fade = 1.0 - 0.4 * abs(zi - z / 2) / max(z, 1)
                vol[zi, yy, xs] = min(_UINT16_MAX, int(value * fade))
    # A touch of fixed-pattern noise so thresholds aren't trivially perfect.
    noise = rng.integers(0, 60, size=shape, dtype=np.uint16)
    vol = np.clip(vol.astype(np.int32) + noise, 0, _UINT16_MAX).astype(np.uint16)
    return vol


# ---------------------------------------------------------------------------
# Raw acquisition folder (gold-standard: feeds discover_tiles → stitching)
# ---------------------------------------------------------------------------


def _frame_size_from_config() -> Tuple[int, int]:
    """Return ``(height, width)`` from the global sensor config (default 2048)."""
    try:
        from py2flamingo.configs.config_loader import get_hardware_config

        hw = get_hardware_config()
        return int(hw.sensor_height_px), int(hw.sensor_width_px)
    except Exception:  # pragma: no cover - config always present in repo
        return 2048, 2048


def _workflow_text(
    z_start_mm: float,
    z_end_mm: float,
    channels: Sequence[int],
    plane_spacing_um: float,
    frame_w: int,
    frame_h: int,
) -> str:
    """Build a minimal Workflow.txt the stitching parser understands.

    ``discover_tiles`` reads ``<Start/End Position> Z (mm)``; the laser lines
    mirror the documented Illumination Source block (laser N → channel N-1, so a
    C-number ``ch`` corresponds to laser ``ch+1``). ``AOI width``/``AOI height``
    let v0.2.0's ``_resolve_tile_frame_dims`` disambiguate non-square frames
    (square frames are already inferred from the file size).
    """
    laser_names = {1: "405 nm", 2: "488 nm", 3: "561 nm", 4: "640 nm"}
    lines = [
        "<Workflow Settings>",
        "  <Start Position>",
        f"    Z (mm) = {z_start_mm:.4f}",
        "  </Start Position>",
    ]
    lines += ["  <End Position>", f"    Z (mm) = {z_end_mm:.4f}", "  </End Position>"]
    lines += ["  <Illumination Source>"]
    for laser in (1, 2, 3, 4):
        ch = laser - 1
        enabled = 1 if ch in channels else 0
        power = 10.00 if enabled else 0.00
        name = laser_names[laser]
        lines.append(f"    Laser {laser} {laser}: {name} MLE = {power:.2f} {enabled}")
    lines += ["  </Illumination Source>"]
    lines += [
        f"  AOI width = {frame_w}",
        f"  AOI height = {frame_h}",
        f"  Plane spacing (um) = {plane_spacing_um:.3f}",
        "</Workflow Settings>",
        "",
    ]
    return "\n".join(lines)


def _scope_settings_text(objective_magnification: float) -> str:
    """Build a minimal ScopeSettings.txt.

    v0.2.0 derives the XY pixel size from ``Objective lens magnification``
    (``pixel = sensor_pixel_size / magnification``) — see
    ``flamingo_stitcher.pipeline.suggested_pixel_size_um``.
    """
    return (
        "<Scope Settings>\n"
        f"  Objective lens magnification = {objective_magnification:.4f}\n"
        "</Scope Settings>\n"
    )


def write_raw_acquisition(
    out_dir,
    *,
    grid: Tuple[int, int] = (2, 2),
    overlap: float = 0.15,
    n_planes: int = 4,
    channels: Sequence[int] = (1,),
    frame_size: Optional[Tuple[int, int]] = None,
    pixel_size_um: float = 0.406,
    sensor_pixel_size_um: float = 6.5,
    z_start_mm: float = 10.0,
    z_step_um: float = 4.0,
    seed: int = 0,
) -> Path:
    """Write a synthetic raw acquisition folder in the native Flamingo layout.

    Args:
        out_dir: Acquisition directory to create (tiles go directly inside).
        grid: ``(n_rows, n_cols)`` = ``(ny, nx)`` of tiles.
        overlap: Fractional tile overlap (0–0.9).
        n_planes: Z-planes per stack (kept small to bound file size).
        channels: C-numbers to write (channel id = C-number; default ``(1,)``).
        frame_size: ``(H, W)``; defaults to the global sensor config so the
            stitching reader (which assumes that size) can read the files.
        pixel_size_um: Image-plane pixel size, for FOV / stage spacing.
        z_start_mm: Stage Z at plane 0.
        z_step_um: Z spacing between planes.
        seed: Phantom content seed.

    Returns:
        The acquisition directory path.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ny, nx = grid
    H, W = frame_size or _frame_size_from_config()
    channels = [int(c) for c in channels]

    # ScopeSettings.txt at the acquisition root so v0.2.0's pixel-size
    # auto-detection (sensor_pixel_size / objective_magnification) round-trips
    # to our pixel_size_um. _find_acquisition_file searches the acq dir + parent
    # + one level of children, so a single file at the root is found.
    objective_mag = sensor_pixel_size_um / pixel_size_um
    (out_dir / "ScopeSettings.txt").write_text(_scope_settings_text(objective_mag))

    overlap = float(np.clip(overlap, 0.0, 0.9))
    step_px_x = max(1, int(round(W * (1.0 - overlap))))
    step_px_y = max(1, int(round(H * (1.0 - overlap))))

    # One large field spanning the whole mosaic, cropped per tile so overlaps
    # share content. Built per-Z-plane to bound peak memory.
    field_w = step_px_x * (nx - 1) + W
    field_h = step_px_y * (ny - 1) + H
    field = make_phantom_volume((n_planes, field_h, field_w), seed=seed)

    fov_mm_x = pixel_size_um / 1000.0 * W
    fov_mm_y = pixel_size_um / 1000.0 * H
    stage_step_mm_x = fov_mm_x * (1.0 - overlap)
    stage_step_mm_y = fov_mm_y * (1.0 - overlap)
    z_end_mm = z_start_mm + (n_planes - 1) * (z_step_um / 1000.0)

    n_tiles = 0
    for iy in range(ny):
        for ix in range(nx):
            x_mm = ix * stage_step_mm_x
            y_mm = iy * stage_step_mm_y
            folder = out_dir / f"X{x_mm:.2f}_Y{y_mm:.2f}"
            folder.mkdir(parents=True, exist_ok=True)

            (folder / "Workflow.txt").write_text(
                _workflow_text(z_start_mm, z_end_mm, channels, z_step_um, W, H)
            )

            y0 = iy * step_px_y
            x0 = ix * step_px_x
            crop = field[:, y0 : y0 + H, x0 : x0 + W]
            for ci, ch in enumerate(channels):
                # Vary channel intensity a little so multi-channel is visible.
                scale = 1.0 - 0.25 * ci
                tile = np.clip(crop.astype(np.float32) * scale, 0, _UINT16_MAX)
                tile = np.ascontiguousarray(tile.astype(np.uint16))
                fname = (
                    f"S000_t000000_V000_R0000_X000_Y000_"
                    f"C{ch:02d}_I0_D1_P{n_planes:05d}.raw"
                )
                tile.tofile(str(folder / fname))
            n_tiles += 1

    logger.info(
        "Wrote %d tiles (%dx%d, %d planes, %d ch, frame=%dx%d) to %s",
        n_tiles,
        ny,
        nx,
        n_planes,
        len(channels),
        H,
        W,
        out_dir,
    )
    return out_dir


# ---------------------------------------------------------------------------
# Stitched dataset (fast path: feeds load_volumes → pipeline)
# ---------------------------------------------------------------------------


def write_stitched_dataset(
    out_dir,
    *,
    shape: Tuple[int, int, int] = (8, 256, 256),
    channels: Sequence[int] = (0, 1),
    voxel_size_um: Tuple[float, float, float] = (4.0, 0.406, 0.406),
    pipeline_template: str = "threshold",
    seed: int = 0,
) -> Dict[str, Path]:
    """Write a small 'already-stitched' multi-channel OME-TIFF + a pipeline JSON.

    The TIFF loads directly via :func:`py2flamingo.pipeline.headless_io.load_volumes`,
    so it is the quickest way to iterate on analysis pipelines.

    Returns:
        ``{"volume": <tif path>, "pipeline": <json path>}``.
    """
    import tifffile

    from py2flamingo.pipeline.builder import make_template

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    channels = [int(c) for c in channels]

    vols: List[np.ndarray] = []
    for ci, _ch in enumerate(channels):
        vols.append(make_phantom_volume(shape, seed=seed + ci, value=4000 - 500 * ci))

    vz, vy, vx = voxel_size_um
    tif_path = out_dir / "stitched.ome.tif"
    if len(vols) == 1:
        arr = vols[0]
        axes = "ZYX"
    else:
        arr = np.stack(vols, axis=0)  # (C, Z, Y, X)
        axes = "CZYX"
    tifffile.imwrite(
        str(tif_path),
        arr,
        metadata={
            "axes": axes,
            "PhysicalSizeX": vx,
            "PhysicalSizeY": vy,
            "PhysicalSizeZ": vz,
        },
    )

    pipeline = make_template(pipeline_template)
    pipe_path = out_dir / "pipeline.json"
    pipe_path.write_text(json.dumps(pipeline.to_dict(), indent=2))

    logger.info("Wrote stitched dataset %s and pipeline %s", tif_path, pipe_path)
    return {"volume": tif_path, "pipeline": pipe_path}
