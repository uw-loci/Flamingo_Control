"""Split-illumination stitched output must load into the viewer.

Reported: selecting a folder containing a stitched `.ims` plus its
`stitch_metadata.json` reported success while loading nothing —

    Stitched Data Loaded
    Loaded 0 channel(s): []

The metadata's channel ids were ``["3_I0", "3_I1"]``: one output channel per
illumination side from a single laser, which is what `split_illumination`
produces. Those ids are STRINGS. `voxel_storage.display_cache` is keyed by
INTEGER slots, so `if ch_id not in display_cache` rejected every channel, each
one warning to the log, and the load then announced success having placed
nothing.

Not Imaris-specific — the same ids come out of an OME-Zarr split stitch.

The viewer already had the right slots: 0-3 left-side lasers, 4-7 the same
lasers on the right path, 8 LED — matching `_raw_file_key`'s
``channel + 4 * illum``. So 3_I0 -> 3 and 3_I1 -> 7.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.views.sample_view import _stitched_channel_slot  # noqa: E402


class TestSplitIlluminationIdsMapToSideAwareSlots(unittest.TestCase):
    def test_the_reported_pair_maps_to_left_and_right_slots(self):
        self.assertEqual(_stitched_channel_slot("3_I0"), 3)
        self.assertEqual(_stitched_channel_slot("3_I1"), 7)

    def test_the_two_sides_never_collide(self):
        """The whole reason the +4 offset exists."""
        for ch in range(4):
            self.assertNotEqual(
                _stitched_channel_slot(f"{ch}_I0"),
                _stitched_channel_slot(f"{ch}_I1"),
            )

    def test_it_matches_the_existing_disk_loader_convention(self):
        from py2flamingo.visualization.disk_tile_loader import _raw_file_key

        for ch in range(4):
            for side in (0, 1):
                self.assertEqual(
                    _stitched_channel_slot(f"{ch}_I{side}"),
                    _raw_file_key(ch, side),
                )

    def test_every_mapped_slot_exists_in_the_viewers_channel_range(self):
        """9 slots: 0-3 left, 4-7 right, 8 LED."""
        for ch in range(4):
            for side in (0, 1):
                self.assertIn(_stitched_channel_slot(f"{ch}_I{side}"), range(9))


class TestUnsplitChannelsAreUnaffected(unittest.TestCase):
    """A normal (sides-fused) stitch must behave exactly as before."""

    def test_an_integer_id_passes_through(self):
        self.assertEqual(_stitched_channel_slot(3), 3)

    def test_a_numeric_string_id_passes_through(self):
        self.assertEqual(_stitched_channel_slot("3"), 3)

    def test_a_numpy_integer_passes_through(self):
        import numpy as np

        self.assertEqual(_stitched_channel_slot(np.int64(2)), 2)


class TestUnparseableIdsAreNotSilentlyDropped(unittest.TestCase):
    """A mis-slotted volume is visible and fixable; a discarded one is neither."""

    def test_junk_falls_back_to_a_real_slot(self):
        self.assertEqual(_stitched_channel_slot("junk"), 0)

    def test_a_malformed_side_suffix_falls_back(self):
        self.assertEqual(_stitched_channel_slot("x_Iy"), 0)

    def test_the_fallback_is_always_a_usable_slot(self):
        for bad in ("", "  ", "3_I", "_I1", None):
            self.assertIn(_stitched_channel_slot(bad), range(9))


if __name__ == "__main__":
    unittest.main()
