"""
Regression tests for the shared live-display colormap helpers.

These guard the transform path used by both the Sample View live feed and the
standalone Camera Live Viewer (previously the Sample View ignored colormap and
image-transformation settings entirely). Pure numpy — no Qt required.
"""

import numpy as np
import pytest

from py2flamingo.utils.image_transforms import (
    apply_named_colormap,
    named_colormap_lut,
)

GUI_COLORMAP_NAMES = [
    "Grayscale",
    "Hot",
    "Jet",
    "Viridis",
    "Plasma",
    "Inferno",
    "Magma",
    "Turbo",
]


@pytest.mark.parametrize("name", GUI_COLORMAP_NAMES + ["Unknown", ""])
def test_lut_shape_and_dtype(name):
    """Every name (including unknown) yields a valid 256x3 uint8 LUT."""
    lut = named_colormap_lut(name)
    assert lut.shape == (256, 3)
    assert lut.dtype == np.uint8


def test_grayscale_is_identity_across_channels():
    """Grayscale (and unknown) maps each level to equal R=G=B."""
    for name in ("Grayscale", "Unknown", ""):
        lut = named_colormap_lut(name)
        ramp = np.arange(256, dtype=np.uint8)
        assert np.array_equal(lut[:, 0], ramp)
        assert np.array_equal(lut[:, 1], ramp)
        assert np.array_equal(lut[:, 2], ramp)


def test_apply_named_colormap_grayscale_keeps_equal_channels():
    img = np.arange(256, dtype=np.uint8).reshape(16, 16)
    rgb = apply_named_colormap(img, "Grayscale")
    assert rgb.shape == (16, 16, 3)
    assert np.array_equal(rgb[..., 0], rgb[..., 1])
    assert np.array_equal(rgb[..., 1], rgb[..., 2])


def test_apply_named_colormap_color_differs_across_channels():
    """A real colormap must actually colorize (channels not all equal)."""
    img = np.arange(256, dtype=np.uint8).reshape(16, 16)
    for name in ("Hot", "Jet", "Viridis", "Turbo"):
        rgb = apply_named_colormap(img, name)
        assert rgb.shape == (16, 16, 3)
        channels_all_equal = np.array_equal(rgb[..., 0], rgb[..., 2])
        assert not channels_all_equal, f"{name} did not colorize"


def test_hot_endpoints():
    """Hot ramps from black to white."""
    lut = named_colormap_lut("Hot")
    assert lut[0].tolist() == [0, 0, 0]
    assert lut[255].tolist() == [255, 255, 255]
