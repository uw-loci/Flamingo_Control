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
        storage = DualResolutionVoxelStorage(
            DualResolutionConfig(chamber_dimensions=(1000, 1000, 1000))
        )
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
        storage = DualResolutionVoxelStorage(
            DualResolutionConfig(chamber_dimensions=(1000, 1000, 1000))
        )
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
            chamber_dimensions=(4000, 4000, 4000),
            chamber_origin=(0, 0, 0),
            sample_region_center=(2000, 2000, 2000),
        )
        storage = DualResolutionVoxelStorage(config)
        rng = np.random.default_rng(23)
        coords = np.array([2000.0, 2000.0, 2000.0]) + rng.uniform(
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


@unittest.skipUnless(HAS_HEAVY_DEPS, "requires scipy/sparse")
class TestMemoryEfficientDisplay(unittest.TestCase):
    """8-bit display caches, with raw counts still recoverable."""

    def _storage(self):
        config = DualResolutionConfig(
            storage_voxel_size=(5, 5, 5),
            display_voxel_size=(50, 50, 50),
            sample_region_radius=1000,
            chamber_dimensions=(4000, 4000, 4000),
            chamber_origin=(0, 0, 0),
            sample_region_center=(2000, 2000, 2000),
        )
        return DualResolutionVoxelStorage(config)

    def _fill(self, storage, channel=0, value=4321, n=5000):
        rng = np.random.default_rng(23)
        coords = np.array([2000.0, 2000.0, 2000.0]) + rng.uniform(
            -300, 300, size=(n, 3)
        )
        storage.update_storage(
            channel_id=channel,
            world_coords=coords,
            pixel_values=np.full(n, value, dtype=np.uint16),
            timestamp=1.0,
            update_mode="maximum",
        )

    def test_display_cache_halves(self):
        storage = self._storage()
        before = storage.display_bytes()

        self.assertTrue(storage.set_memory_efficient(True))

        self.assertEqual(storage.display_bytes(), before // 2)
        self.assertEqual(storage.display_cache[0].dtype, np.uint8)

    def test_toggling_is_reversible_and_reports_no_change_when_idempotent(self):
        storage = self._storage()
        self.assertTrue(storage.set_memory_efficient(True))
        self.assertFalse(storage.set_memory_efficient(True))
        self.assertTrue(storage.set_memory_efficient(False))

        self.assertEqual(storage.display_cache[0].dtype, np.uint16)

    def test_a_dim_channel_survives_the_conversion(self):
        """The whole reason for rescaling instead of >> 8.

        A channel already inside 0-255 is passed through untouched — the scale
        only ever divides, never stretches. Stretching 235 to 255 would invent
        contrast that is not in the data and would make raw_from_display()
        lossy for no gain.
        """
        storage = self._storage()
        storage.set_memory_efficient(True)
        self._fill(storage, value=235)  # the dim channel from the rig log

        volume = storage.downsample_to_display(0, force=True)

        self.assertEqual(volume.dtype, np.uint8)
        self.assertEqual(int(volume.max()), 235)  # a straight >> 8 gives 0
        self.assertEqual(storage.channel_display_scale[0], 1.0)
        self.assertEqual(storage.raw_from_display(0, 235), 235)  # still exact

    def test_raw_counts_are_recoverable_from_the_display_value(self):
        storage = self._storage()
        storage.set_memory_efficient(True)
        self._fill(storage, value=40000)

        volume = storage.downsample_to_display(0, force=True)
        recovered = storage.raw_from_display(0, int(volume.max()))

        # Quantised to one display step (scale = 40000/255 ~ 157), not exact.
        self.assertAlmostEqual(recovered, 40000, delta=200)

    def test_sixteen_bit_mode_leaves_values_untouched(self):
        storage = self._storage()
        self._fill(storage, value=40000)

        volume = storage.downsample_to_display(0, force=True)

        self.assertEqual(volume.dtype, np.uint16)
        self.assertEqual(int(volume.max()), 40000)
        self.assertEqual(storage.raw_from_display(0, 40000), 40000)

    def test_store_display_volume_rescales_instead_of_wrapping(self):
        """A raw uint16 volume assigned straight in would wrap modulo 256."""
        storage = self._storage()
        storage.set_memory_efficient(True)
        data = np.zeros(storage.display_dims, dtype=np.uint16)
        data[0, 0, 0] = 4096  # 4096 % 256 == 0 — the wrap would look like zero

        storage.store_display_volume(0, data)

        self.assertEqual(storage.display_cache[0].dtype, np.uint8)
        self.assertEqual(int(storage.display_cache[0][0, 0, 0]), 255)
        self.assertAlmostEqual(storage.raw_from_display(0, 255), 4096, delta=20)

    def test_switching_modes_does_not_strand_a_stale_scale(self):
        storage = self._storage()
        storage.set_memory_efficient(True)
        self._fill(storage, value=40000)
        storage.downsample_to_display(0, force=True)
        self.assertNotEqual(storage.channel_display_scale[0], 1.0)

        storage.set_memory_efficient(False)

        self.assertEqual(storage.channel_display_scale[0], 1.0)
        self.assertEqual(int(storage.downsample_to_display(0, force=True).max()), 40000)


@unittest.skipUnless(HAS_HEAVY_DEPS, "requires scipy/sparse")
class TestIsotropicGrid(unittest.TestCase):
    """Snap the storage grid to the coarsest axis the data actually resolves."""

    def _storage(self, storage_um=5.0, display_um=50.0):
        config = DualResolutionConfig(
            storage_voxel_size=(storage_um,) * 3,
            display_voxel_size=(display_um,) * 3,
            sample_region_half_widths=(6000, 12000, 7000),
        )
        return DualResolutionVoxelStorage(config)

    def test_coarsens_to_the_worst_axis(self):
        storage = self._storage()
        # The rig case: 8.3 µm stored pixel laterally, 1.25 µm Z step.
        self.assertTrue(storage.adopt_isotropic_grid((1.25, 8.3, 8.3)))

        grid = storage.config.storage_voxel_size[0]
        self.assertGreaterEqual(grid, 8.3)
        self.assertAlmostEqual(grid, 50.0 / 6)  # 8.333

    def test_the_grid_still_divides_the_display_voxel_exactly(self):
        """Otherwise the storage->display block reduction drifts."""
        for sampling in ((1.0, 8.3, 8.3), (2.0, 3.1, 3.1), (0.5, 17.0, 17.0)):
            with self.subTest(sampling=sampling):
                storage = self._storage()
                storage.adopt_isotropic_grid(sampling)

                grid = storage.config.storage_voxel_size[0]
                ratio = 50.0 / grid
                self.assertAlmostEqual(ratio, round(ratio), places=9)
                self.assertGreaterEqual(grid, max(sampling))

    def test_it_never_makes_the_grid_finer(self):
        storage = self._storage()
        self.assertFalse(storage.adopt_isotropic_grid((0.3, 0.3, 0.3)))
        self.assertEqual(storage.config.storage_voxel_size[0], 5.0)

    def test_refuses_once_storage_holds_data(self):
        """Voxel keys index the old grid; changing it would corrupt them."""
        storage = self._storage()
        storage.storage_data[0].merge(
            np.array([1, 2, 3]), np.array([1, 2, 3], np.uint16), "maximum"
        )

        self.assertFalse(storage.adopt_isotropic_grid((1.0, 8.3, 8.3)))
        self.assertEqual(storage.config.storage_voxel_size[0], 5.0)

    def test_storage_dims_shrink_with_the_grid(self):
        storage = self._storage()
        before = storage.storage_dims
        storage.adopt_isotropic_grid((1.25, 8.3, 8.3))

        self.assertTrue(all(a > b for a, b in zip(before, storage.storage_dims)))

    # The rig's tile geometry: frames are downsampled to 100x100 BEFORE they
    # reach storage, so the lateral spacing is already ~8.3 µm.
    _PX_UM = 8.3
    _N_FRAMES = 150
    _LATERAL = 60
    _CENTER = (19250.0, 7000.0, 6655.0)

    def _fill_like_a_tile(self, storage, z_span_um=750.0):
        """Write a dense lattice at the real stored-pixel spacing."""
        yy, xx = np.meshgrid(
            np.arange(self._LATERAL), np.arange(self._LATERAL), indexing="ij"
        )
        lat_y = (yy.ravel() - self._LATERAL / 2) * self._PX_UM + self._CENTER[1]
        lat_x = (xx.ravel() - self._LATERAL / 2) * self._PX_UM + self._CENTER[2]
        n = lat_y.size
        for i in range(self._N_FRAMES):
            z = self._CENTER[0] - z_span_um / 2 + z_span_um * i / (self._N_FRAMES - 1)
            storage.update_storage(
                0,
                np.column_stack([np.full(n, z), lat_y, lat_x]),
                np.full(n, 100, np.uint16),
                1.0,
                "maximum",
            )
        return len(storage.storage_data[0])

    def _tile_storage(self):
        storage = self._storage()
        storage.config.sample_region_center = self._CENTER
        return storage

    def test_the_grid_reduction_is_linear_not_cubic_for_tile_data(self):
        """~1.7x, and the reason matters.

        Coarsening 5 -> 8.33 µm looks like it should save (8.33/5)^3 = 4.6x,
        but it cannot merge points that were already further apart than either
        grid. Tile frames arrive pre-downsampled to 100x100, i.e. ~8.3 µm
        laterally, so XY gains nothing; the whole saving comes from Z, where
        1600 planes were being held on a 5 µm grid. Expect ~5/8.33 = 1.67x.

        Data arriving at native lateral resolution WOULD get the cubic win —
        this path does not, and that is a property of the input, not the grid.
        """
        n_fine = self._fill_like_a_tile(self._tile_storage())

        coarse = self._tile_storage()
        coarse.adopt_isotropic_grid((5.0, self._PX_UM, self._PX_UM))
        n_coarse = self._fill_like_a_tile(coarse)

        reduction = n_fine / n_coarse
        self.assertGreater(reduction, 1.5, f"{n_fine} -> {n_coarse}")
        self.assertLess(reduction, 2.0, f"{n_fine} -> {n_coarse}")

    def test_lateral_occupancy_is_unchanged_by_the_coarser_grid(self):
        """Pins the reason the saving is linear: XY cannot merge."""
        flat = self._tile_storage()
        n_fine = self._fill_like_a_tile(flat, z_span_um=0.0)  # single Z plane

        coarse = self._tile_storage()
        coarse.adopt_isotropic_grid((5.0, self._PX_UM, self._PX_UM))
        n_coarse = self._fill_like_a_tile(coarse, z_span_um=0.0)

        # Points spaced 8.3 µm stay distinct on an 8.33 µm grid.
        self.assertGreater(n_coarse / n_fine, 0.9)

    def test_the_logged_estimate_is_not_the_cubic_figure(self):
        """The log must predict ~1.7x for tile geometry, not 4.6x."""
        storage = self._tile_storage()
        with self.assertLogs(
            "py2flamingo.visualization.dual_resolution_storage", level=logging.INFO
        ) as logs:
            storage.adopt_isotropic_grid((5.0, self._PX_UM, self._PX_UM))

        line = next(m for m in logs.output if "isotropic" in m)
        predicted = float(line.split("roughly ")[1].split("x")[0])
        self.assertGreater(predicted, 1.4)
        self.assertLess(predicted, 2.0, line)


@unittest.skipUnless(HAS_HEAVY_DEPS, "requires scipy/sparse")
class TestToggleDoesNotDestroyCacheOnlyChannels(unittest.TestCase):
    """Session-loaded and stitched channels live ONLY in the display cache.

    They have no sparse store to rebuild from, so the toggle has to convert
    the cache rather than reallocate it — otherwise flipping the checkbox
    blanks a loaded dataset with nothing to restore it from.
    """

    def _storage(self):
        return DualResolutionVoxelStorage(
            DualResolutionConfig(
                display_voxel_size=(50, 50, 50),
                chamber_dimensions=(2000, 2000, 2000),
            )
        )

    def test_a_loaded_channel_survives_the_toggle(self):
        storage = self._storage()
        volume = np.zeros(storage.display_dims, dtype=np.uint16)
        volume[1:4, 1:4, 1:4] = 30000
        storage.store_display_volume(0, volume)
        storage._session_loaded_channels.add(0)

        storage.set_memory_efficient(True)

        cache = storage.display_cache[0]
        self.assertEqual(cache.dtype, np.uint8)
        self.assertGreater(int(cache.max()), 0)
        self.assertAlmostEqual(
            storage.raw_from_display(0, int(cache.max())), 30000, delta=200
        )

    def test_a_round_trip_lands_back_near_the_original_counts(self):
        storage = self._storage()
        volume = np.zeros(storage.display_dims, dtype=np.uint16)
        volume[0, 0, 0] = 30000
        storage.store_display_volume(0, volume)
        storage._session_loaded_channels.add(0)

        storage.set_memory_efficient(True)
        storage.set_memory_efficient(False)

        cache = storage.display_cache[0]
        self.assertEqual(cache.dtype, np.uint16)
        # Quantised by the 8-bit round trip, not zeroed.
        self.assertAlmostEqual(int(cache[0, 0, 0]), 30000, delta=200)

    def test_a_channel_with_sparse_data_is_marked_for_rebuild(self):
        storage = self._storage()
        storage.storage_data[0].merge(
            np.array([1]), np.array([500], np.uint16), "maximum"
        )

        storage.set_memory_efficient(True)

        self.assertTrue(storage.display_dirty[0])
        self.assertFalse(storage.display_dirty[1])  # empty channel, nothing to do


@unittest.skipUnless(HAS_HEAVY_DEPS, "requires scipy/sparse")
class TestTileWorkerAdoptsTheGrid(unittest.TestCase):
    """The grid has to be chosen from the data, on the first tile, before writes."""

    def _worker_and_storage(self, memory_efficient):
        from py2flamingo.visualization.tile_processing_worker import (
            TileFrameBuffer,
            TileProcessingWorker,
        )

        storage = DualResolutionVoxelStorage(
            DualResolutionConfig(
                storage_voxel_size=(5, 5, 5),
                display_voxel_size=(50, 50, 50),
                chamber_dimensions=(4000, 4000, 4000),
                sample_region_center=(2000, 2000, 2000),
                sample_region_radius=1000,
            )
        )
        storage.memory_efficient = memory_efficient
        worker = TileProcessingWorker(
            storage,
            {"sample_chamber": {"sample_region_center_um": [2000, 2000, 2000]}},
        )
        # 40 planes over a 1 mm sweep -> 25.6 µm Z step; frames pre-downsampled
        # to 40x40, so ~8 µm laterally at a 2048 px sensor.
        buffer = TileFrameBuffer(
            tile_key=(2.0, 2.0),
            position={"x": 2.0, "y": 2.0, "z": 2.0},
            channels=[0],
            z_min=1.5,
            z_max=2.5,
            reference_position={"x": 2.0, "y": 2.0, "z": 2.0, "r": 0.0},
            planes_per_channel=40,
            source_frame_shape=(2048, 2048),
        )
        for i in range(40):
            buffer.append(np.full((40, 40), 500, dtype=np.uint16), i)
        return worker, storage, buffer

    def test_grid_is_adopted_when_memory_efficient(self):
        worker, storage, buffer = self._worker_and_storage(True)
        before = storage.config.storage_voxel_size[0]

        worker._process_tile(buffer)

        self.assertGreater(storage.config.storage_voxel_size[0], before)
        self.assertFalse(storage.storage_data[0].is_empty)  # and it still stored data

    def test_grid_is_left_alone_when_the_option_is_off(self):
        worker, storage, buffer = self._worker_and_storage(False)

        worker._process_tile(buffer)

        self.assertEqual(storage.config.storage_voxel_size[0], 5.0)
        self.assertFalse(storage.storage_data[0].is_empty)

    def test_a_second_tile_does_not_re_snap_the_grid(self):
        """Voxel keys index the grid chosen for tile one."""
        worker, storage, buffer = self._worker_and_storage(True)
        worker._process_tile(buffer)
        adopted = storage.config.storage_voxel_size[0]

        worker._process_tile(buffer)

        self.assertEqual(storage.config.storage_voxel_size[0], adopted)
