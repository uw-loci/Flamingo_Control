"""Sparse voxel storage: memory footprint, merge semantics, display equivalence.

Background — the bug these guard against
----------------------------------------
Collecting tiles into the 3D view died with ``MemoryError`` inside
``update_storage``, on the *second* tile of a run. The cause was the storage
representation, not a leak: occupied voxels lived in three tuple-keyed Python
dicts per channel (data / timestamps / confidence), costing ~270 bytes each.

Nothing coalesced them, either. A tile is 1600 planes x 100x100 stored pixels;
the stored-pixel footprint is ~8 µm and the Z step is sub-µm, both against a
5 µm storage grid, so nearly every one of the 16M pixel writes per channel
landed in its own voxel. One two-channel tile could therefore occupy ~32M
voxels — several GB — and ``get_memory_usage`` reported it as 7 bytes/voxel,
so nothing warned first.

The fix is a sorted (int64 key, uint16 value) pair of arrays: 10 bytes/voxel,
with the never-read timestamp and confidence dicts dropped entirely.
"""

import logging
import sys
import unittest

import numpy as np

try:
    import scipy  # noqa: F401
    import sparse  # noqa: F401

    HAS_HEAVY_DEPS = True
except ImportError:
    HAS_HEAVY_DEPS = False

if HAS_HEAVY_DEPS:
    from py2flamingo.visualization.dual_resolution_storage import (
        DualResolutionConfig,
        DualResolutionVoxelStorage,
        SparseChannelStore,
        _reduce_sorted,
    )


def _reference_merge(batches, mode):
    """Dict-of-voxels reference for the merge semantics, as the old code did it."""
    out = {}
    for keys, values in batches:
        for k, v in zip(keys, values):
            k = int(k)
            v = int(v)
            if k not in out:
                out[k] = v
            elif mode == "maximum":
                out[k] = max(out[k], v)
            elif mode == "additive":
                out[k] = min(65535, out[k] + v)
            elif mode == "average":
                out[k] = out[k] + v  # summed here; divided by count below
            else:  # latest
                out[k] = v
    if mode == "average":
        counts = {}
        for keys, _ in batches:
            for k in keys:
                counts[int(k)] = counts.get(int(k), 0) + 1
        out = {k: v // counts[k] for k, v in out.items()}
    keys = np.array(sorted(out), dtype=np.int64)
    return keys, np.array([out[int(k)] for k in keys], dtype=np.uint16)


@unittest.skipUnless(HAS_HEAVY_DEPS, "requires scipy/sparse")
class TestSparseChannelStoreSemantics(unittest.TestCase):
    """The array store must fold duplicate voxels the way the dicts did."""

    def _batches(self, seed, n_batches=6, n=500, key_space=800):
        rng = np.random.default_rng(seed)
        return [
            (
                rng.integers(0, key_space, n, dtype=np.int64),
                rng.integers(1, 5000, n).astype(np.uint16),
            )
            for _ in range(n_batches)
        ]

    def test_modes_match_the_dict_reference(self):
        for mode in ("maximum", "additive", "average", "latest"):
            with self.subTest(mode=mode):
                batches = self._batches(seed=7)
                store = SparseChannelStore(min_compact=137)  # force many compactions
                for keys, values in batches:
                    # merge() expects each batch already deduped, as
                    # _vectorized_accumulate leaves it
                    uk, idx = np.unique(keys, return_index=True)
                    store.merge(uk, values[idx], mode)

                got_k, got_v = store.snapshot()
                exp_k, exp_v = _reference_merge(
                    [
                        (np.unique(k), v[np.unique(k, return_index=True)[1]])
                        for k, v in batches
                    ],
                    mode,
                )
                np.testing.assert_array_equal(got_k, exp_k)
                np.testing.assert_array_equal(got_v, exp_v)

    def test_keys_come_back_sorted_and_unique(self):
        store = SparseChannelStore(min_compact=50)
        for keys, values in self._batches(seed=3):
            uk, idx = np.unique(keys, return_index=True)
            store.merge(uk, values[idx], "maximum")
        keys, values = store.snapshot()

        self.assertTrue(np.all(np.diff(keys) > 0))
        self.assertEqual(keys.size, values.size)

    def test_compaction_boundary_does_not_change_the_result(self):
        """A voxel written either side of a compaction still merges correctly."""
        results = []
        for min_compact in (1, 3, 1_000_000):
            store = SparseChannelStore(min_compact=min_compact)
            store.merge(np.array([5, 9]), np.array([10, 20], np.uint16), "maximum")
            store.merge(np.array([5, 7]), np.array([30, 40], np.uint16), "maximum")
            store.merge(np.array([9]), np.array([1], np.uint16), "maximum")
            results.append(store.snapshot())

        for keys, values in results:
            np.testing.assert_array_equal(keys, [5, 7, 9])
            np.testing.assert_array_equal(values, [30, 40, 20])

    def test_average_is_a_true_mean_not_an_average_of_averages(self):
        """'average' is the one non-associative mode; compaction must not skew it."""
        results = []
        for min_compact in (1, 2, 1_000_000):
            store = SparseChannelStore(min_compact=min_compact)
            for v in (10, 20, 300):
                store.merge(np.array([1]), np.array([v], np.uint16), "average")
            results.append(store.snapshot()[1][0])

        # Mean of 10, 20, 300 is 110. Averaging pairwise as the old code did
        # gives ((10+20)/2 + 300)/2 = 157, and averaging the compaction groups
        # would give something different again for each threshold.
        for value in results:
            self.assertEqual(int(value), 110)

    def test_mode_change_does_not_rewrite_pending_history(self):
        """Switching fusion mode mid-run must not re-reduce earlier writes."""
        store = SparseChannelStore(min_compact=1_000_000)
        store.merge(np.array([4]), np.array([100], np.uint16), "maximum")
        store.merge(np.array([4]), np.array([10], np.uint16), "maximum")
        store.merge(np.array([4]), np.array([7], np.uint16), "latest")

        keys, values = store.snapshot()
        np.testing.assert_array_equal(keys, [4])
        # max(100, 10) settled under 'maximum', then 'latest' replaced it
        np.testing.assert_array_equal(values, [7])

    def test_snapshot_survives_concurrent_writes(self):
        """The display thread reads without the lock; writes must not mutate it."""
        store = SparseChannelStore(min_compact=2)
        store.merge(np.array([1, 2]), np.array([10, 20], np.uint16), "maximum")
        keys, values = store.snapshot()
        before = values.copy()

        for _ in range(20):
            store.merge(
                np.array([1, 2, 3]), np.array([99, 99, 99], np.uint16), "maximum"
            )

        np.testing.assert_array_equal(values, before)

    def test_empty_store_snapshot_is_empty(self):
        keys, values = SparseChannelStore().snapshot()
        self.assertEqual(keys.size, 0)
        self.assertEqual(values.size, 0)

    def test_is_empty_does_not_force_compaction(self):
        store = SparseChannelStore(min_compact=1_000_000)
        store.merge(np.array([1]), np.array([5], np.uint16), "maximum")

        self.assertFalse(store.is_empty)
        self.assertEqual(store._keys.size, 0)  # still pending — no compaction ran

    def test_reduce_sorted_handles_a_single_group(self):
        keys, values = _reduce_sorted(
            np.array([4, 4, 4], np.int64), np.array([1, 9, 3], np.uint16), "maximum"
        )
        np.testing.assert_array_equal(keys, [4])
        np.testing.assert_array_equal(values, [9])


@unittest.skipUnless(HAS_HEAVY_DEPS, "requires scipy/sparse")
class TestMemoryFootprint(unittest.TestCase):
    """The regression test for the reported MemoryError."""

    def test_bytes_per_voxel_is_an_order_of_magnitude_better_than_dicts(self):
        n = 200_000
        rng = np.random.default_rng(11)
        keys = np.unique(rng.integers(0, 2**34, n, dtype=np.int64))
        store = SparseChannelStore()
        store.merge(keys, rng.integers(1, 5000, keys.size).astype(np.uint16), "maximum")
        store.compact()

        per_voxel = store.nbytes / len(store)
        self.assertLess(per_voxel, 16, f"{per_voxel:.1f} bytes/voxel")
        # For contrast, the three tuple-keyed dicts this replaced measured
        # ~270 bytes/voxel, which is what put one tile into the GB range.

    def test_one_tiles_worth_of_voxels_fits_in_a_few_hundred_MB(self):
        """32M voxels — a real two-channel tile — used to cost ~8 GB."""
        n_voxels = 32_000_000
        store = SparseChannelStore()
        store.merge(
            np.arange(n_voxels, dtype=np.int64),
            np.ones(n_voxels, dtype=np.uint16),
            "maximum",
        )
        store.compact()

        self.assertLess(store.nbytes, 400 * 2**20, f"{store.nbytes / 2**20:.0f} MiB")

    def test_memory_usage_reports_real_bytes_not_an_idealised_count(self):
        storage = DualResolutionVoxelStorage(DualResolutionConfig())
        keys = np.arange(100_000, dtype=np.int64)
        storage.storage_data[0].merge(keys, np.ones(keys.size, np.uint16), "maximum")

        usage = storage.get_memory_usage()
        expected_mb = storage.storage_data[0].nbytes / (1024 * 1024)
        self.assertAlmostEqual(usage["storage_mb"], expected_mb, places=6)
        # The old formula would have claimed 7 bytes/voxel regardless of layout.
        self.assertGreater(usage["storage_mb"], 100_000 * 7 / (1024 * 1024))


@unittest.skipUnless(HAS_HEAVY_DEPS, "requires scipy/sparse")
class TestStorageBudget(unittest.TestCase):
    """Refuse further voxels rather than die mid-acquisition."""

    def _storage(self, budget):
        config = DualResolutionConfig(
            storage_voxel_size=(5, 5, 5),
            display_voxel_size=(50, 50, 50),
            sample_region_radius=1000,
            chamber_dimensions=(4000, 4000, 4000),
        )
        return DualResolutionVoxelStorage(config, max_storage_bytes=budget)

    def _write_a_plane(self, storage, channel=0, n=20_000):
        rng = np.random.default_rng(5)
        center = np.array(storage.config.sample_region_center, dtype=float)
        coords = center + rng.uniform(-400, 400, size=(n, 3))
        storage.update_storage(
            channel_id=channel,
            world_coords=coords,
            pixel_values=rng.integers(1, 5000, n).astype(np.uint16),
            timestamp=1.0,
            update_mode="maximum",
        )

    def test_writes_stop_once_the_budget_is_reached(self):
        storage = self._storage(budget=64 * 1024)  # tiny, trips immediately
        with self.assertLogs(
            "py2flamingo.visualization.dual_resolution_storage", level=logging.ERROR
        ) as logs:
            self._write_a_plane(storage)
            self._write_a_plane(storage)

        self.assertTrue(storage._storage_budget_exceeded)
        self.assertTrue(
            any("storage budget reached" in m for m in logs.output),
            logs.output,
        )
        # The message must name the knob that actually helps.
        self.assertTrue(any("voxel_size_um" in m for m in logs.output))

    def test_the_error_is_logged_once_not_per_frame(self):
        storage = self._storage(budget=64 * 1024)
        with self.assertLogs(
            "py2flamingo.visualization.dual_resolution_storage", level=logging.ERROR
        ) as logs:
            for _ in range(5):
                self._write_a_plane(storage)

        budget_errors = [m for m in logs.output if "storage budget reached" in m]
        self.assertEqual(len(budget_errors), 1)

    def test_clear_lifts_the_block(self):
        storage = self._storage(budget=64 * 1024)
        with self.assertLogs(
            "py2flamingo.visualization.dual_resolution_storage", level=logging.ERROR
        ):
            self._write_a_plane(storage)
            self._write_a_plane(storage)

        storage.clear()
        self.assertFalse(storage._storage_budget_exceeded)
        self._write_a_plane(storage)
        self.assertTrue(storage.has_data(0))

    def test_a_generous_budget_never_trips(self):
        storage = self._storage(budget=2 * 2**30)
        for _ in range(5):
            self._write_a_plane(storage)

        self.assertFalse(storage._storage_budget_exceeded)


@unittest.skipUnless(HAS_HEAVY_DEPS, "requires scipy/sparse")
class TestDisplayDownsampleEquivalence(unittest.TestCase):
    """The sparse scatter must reproduce densify + _block_reduce exactly.

    ``_block_reduce`` takes the block MAXIMUM and edge-pads partial blocks,
    which replicates values already inside the block — so scattering straight
    to display resolution is not an approximation, it is the same answer
    without the ratio^3-larger intermediate array.
    """

    def _reference(self, z, y, x, values, min_coords, region_shape, ratio):
        storage = DualResolutionVoxelStorage(DualResolutionConfig())
        dense = np.zeros(region_shape, dtype=np.uint16)
        dense[z - min_coords[0], y - min_coords[1], x - min_coords[2]] = values
        return storage._block_reduce(dense, ratio)

    def test_matches_the_dense_path_on_random_data(self):
        rng = np.random.default_rng(19)
        region_shape = np.array([37, 41, 23])  # deliberately not multiples of ratio
        ratio = (10, 10, 10)
        n = 4000
        z = rng.integers(0, region_shape[0], n)
        y = rng.integers(0, region_shape[1], n)
        x = rng.integers(0, region_shape[2], n)
        values = rng.integers(1, 60000, n).astype(np.uint16)
        # Collapse duplicates by max, as the store would
        flat = np.ravel_multi_index((z, y, x), region_shape)
        uq, idx = np.unique(flat, return_index=True)
        z, y, x = np.unravel_index(uq, region_shape)
        values = np.maximum.reduceat(
            values[np.argsort(flat, kind="stable")],
            np.unique(np.sort(flat), return_index=True)[1],
        )

        min_coords = np.array([0, 0, 0])
        got = DualResolutionVoxelStorage._scatter_to_display_blocks(
            z, y, x, values, min_coords, region_shape, ratio
        )
        expected = self._reference(z, y, x, values, min_coords, region_shape, ratio)

        self.assertEqual(got.shape, expected.shape)
        np.testing.assert_array_equal(got, expected)

    def test_offset_region_is_placed_relative_to_min_coords(self):
        ratio = (10, 10, 10)
        region_shape = np.array([20, 20, 20])
        min_coords = np.array([100, 200, 300])
        z = np.array([100, 115])
        y = np.array([200, 200])
        x = np.array([300, 300])
        values = np.array([7, 9], dtype=np.uint16)

        got = DualResolutionVoxelStorage._scatter_to_display_blocks(
            z, y, x, values, min_coords, region_shape, ratio
        )

        self.assertEqual(got.shape, (2, 2, 2))
        self.assertEqual(got[0, 0, 0], 7)
        self.assertEqual(got[1, 0, 0], 9)

    def test_display_volume_is_populated_end_to_end(self):
        config = DualResolutionConfig(
            storage_voxel_size=(5, 5, 5),
            display_voxel_size=(50, 50, 50),
            sample_region_radius=1000,
            chamber_dimensions=(20000, 20000, 20000),
            chamber_origin=(0, 0, 0),
            sample_region_center=(10000, 10000, 10000),
        )
        storage = DualResolutionVoxelStorage(config)
        rng = np.random.default_rng(23)
        coords = np.array([10000.0, 10000.0, 10000.0]) + rng.uniform(
            -300, 300, size=(5000, 3)
        )
        storage.update_storage(
            channel_id=0,
            world_coords=coords,
            pixel_values=np.full(5000, 4321, dtype=np.uint16),
            timestamp=1.0,
            update_mode="maximum",
        )

        volume = storage.downsample_to_display(0, force=True)

        self.assertEqual(int(volume.max()), 4321)
        self.assertGreater(int(np.count_nonzero(volume)), 0)


if __name__ == "__main__":
    unittest.main()
