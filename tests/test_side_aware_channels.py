"""
Unit tests for side-aware channel slots and fusion mode features.

Tests:
- Illumination path workflow parser
- Channel offset logic in disk tile loader
- TileProcessingWorker update_mode parameter
- Config channel expansion to 8
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from py2flamingo.utils.tile_workflow_parser import (
    read_illumination_path_from_workflow,
    read_laser_channels_from_workflow,
)

# Check for optional heavy dependencies (not available in all test environments)
try:
    import scipy  # noqa: F401
    import sparse  # noqa: F401

    HAS_HEAVY_DEPS = True
except ImportError:
    HAS_HEAVY_DEPS = False


class TestReadIlluminationPath(unittest.TestCase):
    """Test read_illumination_path_from_workflow parser."""

    def _write_workflow(self, illumination_block: str) -> Path:
        """Helper: write a temp workflow file with given illumination block."""
        content = (
            "<Workflow Settings>\n"
            "  <Illumination Path>\n"
            f"    {illumination_block}\n"
            "  </Illumination Path>\n"
            "</Workflow Settings>\n"
        )
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        f.write(content)
        f.close()
        return Path(f.name)

    def test_both_enabled(self):
        """Both left and right paths ON."""
        path = self._write_workflow("Left path = ON 1\n    Right path = ON 1")
        left, right = read_illumination_path_from_workflow(path)
        self.assertTrue(left)
        self.assertTrue(right)
        path.unlink()

    def test_left_only(self):
        """Only left path ON."""
        path = self._write_workflow("Left path = ON 1\n    Right path = OFF 0")
        left, right = read_illumination_path_from_workflow(path)
        self.assertTrue(left)
        self.assertFalse(right)
        path.unlink()

    def test_right_only(self):
        """Only right path ON."""
        path = self._write_workflow("Left path = OFF 0\n    Right path = ON 1")
        left, right = read_illumination_path_from_workflow(path)
        self.assertFalse(left)
        self.assertTrue(right)
        path.unlink()

    def test_both_disabled(self):
        """Both paths OFF."""
        path = self._write_workflow("Left path = OFF 0\n    Right path = OFF 0")
        left, right = read_illumination_path_from_workflow(path)
        self.assertFalse(left)
        self.assertFalse(right)
        path.unlink()

    def test_missing_block_defaults_left(self):
        """Missing Illumination Path block defaults to (True, False)."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        f.write("<Workflow Settings>\n</Workflow Settings>\n")
        f.close()
        path = Path(f.name)
        left, right = read_illumination_path_from_workflow(path)
        self.assertTrue(left)
        self.assertFalse(right)
        path.unlink()

    def test_nonexistent_file_defaults_left(self):
        """Nonexistent file defaults to (True, False)."""
        left, right = read_illumination_path_from_workflow(
            Path("/nonexistent/workflow.txt")
        )
        self.assertTrue(left)
        self.assertFalse(right)

    def test_real_workflow_file(self):
        """Test against actual ZStack.txt if available."""
        zstack = Path(__file__).parent.parent / "workflows" / "ZStack.txt"
        if not zstack.exists():
            self.skipTest("ZStack.txt not found")
        left, right = read_illumination_path_from_workflow(zstack)
        # ZStack.txt has both ON
        self.assertTrue(left)
        self.assertTrue(right)


class TestChannelOffsetLogicUnit(unittest.TestCase):
    """Test channel offset logic without importing heavy visualization modules.

    Tests the pure logic: right-only → channels +4, both → unchanged, left → unchanged.
    """

    def test_right_only_offset(self):
        """Right-only illumination offsets channels by +4."""
        channels = [0, 1, 3]
        left_enabled, right_enabled = False, True

        if left_enabled and right_enabled:
            side = "both"
        elif right_enabled:
            side = "right"
            channels = [ch + 4 for ch in channels]
        else:
            side = "left"

        self.assertEqual(channels, [4, 5, 7])
        self.assertEqual(side, "right")

    def test_left_only_no_offset(self):
        """Left-only illumination keeps channels unchanged."""
        channels = [0, 1, 3]
        left_enabled, right_enabled = True, False

        if left_enabled and right_enabled:
            side = "both"
        elif right_enabled:
            side = "right"
            channels = [ch + 4 for ch in channels]
        else:
            side = "left"

        self.assertEqual(channels, [0, 1, 3])
        self.assertEqual(side, "left")

    def test_both_sides_no_offset(self):
        """Both sides keeps channels unchanged (merged)."""
        channels = [0, 1, 3]
        left_enabled, right_enabled = True, True

        if left_enabled and right_enabled:
            side = "both"
        elif right_enabled:
            side = "right"
            channels = [ch + 4 for ch in channels]
        else:
            side = "left"

        self.assertEqual(channels, [0, 1, 3])
        self.assertEqual(side, "both")

    def test_single_channel_right_only(self):
        """Single channel right-only → offset by 4."""
        channels = [2]
        left_enabled, right_enabled = False, True

        if right_enabled and not left_enabled:
            channels = [ch + 4 for ch in channels]

        self.assertEqual(channels, [6])


@unittest.skipUnless(HAS_HEAVY_DEPS, "Requires sparse and scipy")
class TestChannelOffsetWithDiskLoader(unittest.TestCase):
    """Integration test: parse_tile_folder with real disk tile loader."""

    def _make_tile_folder(self, left_on: bool, right_on: bool, lasers: str) -> Path:
        """Create a minimal tile folder with Workflow.txt and a dummy .raw file."""
        import numpy as np

        tmpdir = tempfile.mkdtemp(prefix="X5.00_Y10.00_")
        folder = Path(tmpdir)

        left_val = "ON 1" if left_on else "OFF 0"
        right_val = "ON 1" if right_on else "OFF 0"

        workflow = (
            "<Workflow Settings>\n"
            "  <Start Position>\n"
            "    Z (mm) = 19.0\n"
            "  </Start Position>\n"
            "  <End Position>\n"
            "    Z (mm) = 20.0\n"
            "  </End Position>\n"
            "  <Illumination Source>\n"
            f"    {lasers}\n"
            "  </Illumination Source>\n"
            "  <Illumination Path>\n"
            f"    Left path = {left_val}\n"
            f"    Right path = {right_val}\n"
            "  </Illumination Path>\n"
            "</Workflow Settings>\n"
        )
        (folder / "Workflow.txt").write_text(workflow)

        raw_data = np.zeros((1, 2048, 2048), dtype=np.uint16)
        raw_path = folder / "S000_t000000_V000_R0000_X000_Y000_C01_I0_D1_P00001.raw"
        raw_data.tofile(str(raw_path))

        return folder

    def _cleanup_folder(self, folder: Path):
        import shutil

        shutil.rmtree(folder)

    def test_left_only_channels_unchanged(self):
        """Left-only: channels stay 0-based."""
        from py2flamingo.visualization.disk_tile_loader import parse_tile_folder

        folder = self._make_tile_folder(
            left_on=True,
            right_on=False,
            lasers="Laser 2 2: 488 nm MLE = 10.00 1",
        )
        try:
            info = parse_tile_folder(folder)
            self.assertEqual(info.channels, [1])
            self.assertEqual(info.illumination_side, "left")
        finally:
            self._cleanup_folder(folder)

    def test_right_only_channels_offset_by_4(self):
        """Right-only: channels offset by +4."""
        from py2flamingo.visualization.disk_tile_loader import parse_tile_folder

        folder = self._make_tile_folder(
            left_on=False,
            right_on=True,
            lasers="Laser 2 2: 488 nm MLE = 10.00 1",
        )
        try:
            info = parse_tile_folder(folder)
            self.assertEqual(info.channels, [5])  # 1 + 4
            self.assertEqual(info.illumination_side, "right")
        finally:
            self._cleanup_folder(folder)

    def test_both_sides_channels_unchanged(self):
        """Both sides: channels stay 0-based (merged)."""
        from py2flamingo.visualization.disk_tile_loader import parse_tile_folder

        folder = self._make_tile_folder(
            left_on=True,
            right_on=True,
            lasers="Laser 2 2: 488 nm MLE = 10.00 1",
        )
        try:
            info = parse_tile_folder(folder)
            self.assertEqual(info.channels, [1])
            self.assertEqual(info.illumination_side, "both")
        finally:
            self._cleanup_folder(folder)


@unittest.skipUnless(HAS_HEAVY_DEPS, "Requires sparse and scipy")
class TestTileProcessingWorkerUpdateMode(unittest.TestCase):
    """Test that TileProcessingWorker accepts and uses update_mode."""

    def test_default_update_mode(self):
        """Default update_mode is 'maximum'."""
        from py2flamingo.visualization.tile_processing_worker import (
            TileProcessingWorker,
        )

        worker = TileProcessingWorker(
            voxel_storage=MagicMock(),
            config={},
        )
        self.assertEqual(worker._update_mode, "maximum")

    def test_custom_update_mode(self):
        """Custom update_mode is stored."""
        from py2flamingo.visualization.tile_processing_worker import (
            TileProcessingWorker,
        )

        worker = TileProcessingWorker(
            voxel_storage=MagicMock(),
            config={},
            update_mode="average",
        )
        self.assertEqual(worker._update_mode, "average")

    def test_update_mode_used_in_processing(self):
        """Verify update_mode is passed to voxel_storage.update_storage."""
        import numpy as np

        from py2flamingo.visualization.tile_processing_worker import (
            TileFrameBuffer,
            TileProcessingWorker,
        )

        mock_storage = MagicMock()

        worker = TileProcessingWorker(
            voxel_storage=mock_storage,
            config={"sample_chamber": {"sample_region_center_um": [6655, 7000, 19250]}},
            update_mode="additive",
        )

        # Create a minimal buffer with 1 frame, 1 channel
        buffer = TileFrameBuffer(
            tile_key=(5.0, 10.0),
            position={"x": 5.0, "y": 10.0, "z": 19.5, "r": 0.0},
            channels=[0],
            z_min=19.0,
            z_max=20.0,
            reference_position={"x": 5.0, "y": 10.0, "z": 19.5, "r": 0.0},
        )
        frame = np.zeros((10, 10), dtype=np.uint16)
        frame[5, 5] = 100
        buffer.append(frame, 0)

        worker._process_tile(buffer)

        # Verify update_storage was called with update_mode="additive"
        mock_storage.update_storage.assert_called()
        call_kwargs = mock_storage.update_storage.call_args
        self.assertEqual(
            call_kwargs.kwargs.get("update_mode", call_kwargs[1].get("update_mode")),
            "additive",
        )


class TestConfigExpansion(unittest.TestCase):
    """Test that the config YAML has 8 channels."""

    def test_config_has_8_channels(self):
        """Verify visualization_3d_config.yaml defines 8 channels."""
        import yaml

        config_path = (
            Path(__file__).parent.parent
            / "src"
            / "py2flamingo"
            / "configs"
            / "visualization_3d_config.yaml"
        )
        if not config_path.exists():
            self.skipTest("Config file not found")

        with open(config_path) as f:
            config = yaml.safe_load(f)

        channels = config.get("channels", [])
        self.assertEqual(len(channels), 8)
        self.assertEqual(config["display"]["max_channels"], 8)

        # Verify IDs are 0-7
        ids = [ch["id"] for ch in channels]
        self.assertEqual(ids, list(range(8)))

        # Verify right-side channels (4-7) are hidden by default
        for ch in channels[4:]:
            self.assertFalse(
                ch["default_visible"],
                f"Channel {ch['id']} ({ch['name']}) should be hidden by default",
            )

        # Verify right-side names end with " R"
        for ch in channels[4:]:
            self.assertTrue(
                ch["name"].endswith(" R"),
                f"Channel {ch['id']} name '{ch['name']}' should end with ' R'",
            )

    def test_left_right_colormaps_match(self):
        """Left and right channels with same laser should have same colormap."""
        import yaml

        config_path = (
            Path(__file__).parent.parent
            / "src"
            / "py2flamingo"
            / "configs"
            / "visualization_3d_config.yaml"
        )
        if not config_path.exists():
            self.skipTest("Config file not found")

        with open(config_path) as f:
            config = yaml.safe_load(f)

        channels = config["channels"]
        for i in range(4):
            left = channels[i]
            right = channels[i + 4]
            self.assertEqual(
                left["default_colormap"],
                right["default_colormap"],
                f"Colormap mismatch: {left['name']} vs {right['name']}",
            )


@unittest.skipUnless(HAS_HEAVY_DEPS, "Requires sparse and scipy")
class TestDualResolutionStorage8Channels(unittest.TestCase):
    """Test that DualResolutionStorage supports 8 channels."""

    def test_num_channels_is_8(self):
        """Verify storage initializes with 8 channels."""
        from py2flamingo.visualization.dual_resolution_storage import (
            DualResolutionStorage,
        )

        storage = DualResolutionStorage()
        self.assertEqual(storage.num_channels, 8)

    def test_has_data_all_channels(self):
        """Verify has_data works for channels 0-7."""
        from py2flamingo.visualization.dual_resolution_storage import (
            DualResolutionStorage,
        )

        storage = DualResolutionStorage()
        for ch_id in range(8):
            self.assertFalse(storage.has_data(ch_id))

    def test_get_display_volume_all_channels(self):
        """Verify get_display_volume works for channels 0-7."""
        from py2flamingo.visualization.dual_resolution_storage import (
            DualResolutionStorage,
        )

        storage = DualResolutionStorage()
        for ch_id in range(8):
            vol = storage.get_display_volume(ch_id)
            self.assertIsNotNone(vol)


class TestIlluminationPathEdgeCases(unittest.TestCase):
    """Additional edge case tests for illumination path parsing."""

    def _write_file(self, content: str) -> Path:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        f.write(content)
        f.close()
        return Path(f.name)

    def test_only_left_line_present(self):
        """Only Left path line in block — Right defaults to False."""
        path = self._write_file(
            "<Illumination Path>\n" "  Left path = ON 1\n" "</Illumination Path>\n"
        )
        left, right = read_illumination_path_from_workflow(path)
        self.assertTrue(left)
        self.assertFalse(right)
        path.unlink()

    def test_only_right_line_present(self):
        """Only Right path line in block — Left defaults to False."""
        path = self._write_file(
            "<Illumination Path>\n" "  Right path = ON 1\n" "</Illumination Path>\n"
        )
        left, right = read_illumination_path_from_workflow(path)
        self.assertFalse(left)
        self.assertTrue(right)
        path.unlink()

    def test_empty_illumination_block(self):
        """Empty Illumination Path block — both default to False."""
        path = self._write_file("<Illumination Path>\n" "</Illumination Path>\n")
        left, right = read_illumination_path_from_workflow(path)
        self.assertFalse(left)
        self.assertFalse(right)
        path.unlink()


class TestThresholderMetrics(unittest.TestCase):
    """Test thresholder tile metric calculations (pure numpy, no PyQt5)."""

    @staticmethod
    def _tile_variance(image, tiles_x, tiles_y):
        """Replicate calculate_tile_variance logic without PyQt5 import."""
        gray = image.astype(np.float64)
        h, w = gray.shape
        tile_h, tile_w = h // tiles_y, w // tiles_x
        result = np.zeros((tiles_y, tiles_x))
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                tile = gray[
                    ty * tile_h : (ty + 1) * tile_h, tx * tile_w : (tx + 1) * tile_w
                ]
                result[ty, tx] = np.var(tile)
        return result

    @staticmethod
    def _tile_intensity(image, tiles_x, tiles_y):
        """Replicate calculate_tile_intensity logic without PyQt5 import."""
        gray = image.astype(np.float64)
        h, w = gray.shape
        tile_h, tile_w = h // tiles_y, w // tiles_x
        result = np.zeros((tiles_y, tiles_x))
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                tile = gray[
                    ty * tile_h : (ty + 1) * tile_h, tx * tile_w : (tx + 1) * tile_w
                ]
                result[ty, tx] = np.mean(tile)
        return result

    def test_calculate_tile_variance(self):
        """Variance calculation works on simple image."""
        image = np.array(
            [
                [0, 0, 100, 100],
                [0, 0, 100, 100],
                [50, 50, 50, 50],
                [50, 50, 50, 50],
            ],
            dtype=np.float64,
        )

        result = self._tile_variance(image, 2, 2)
        self.assertEqual(result.shape, (2, 2))
        self.assertAlmostEqual(result[0, 0], 0.0)
        self.assertAlmostEqual(result[1, 0], 0.0)
        self.assertAlmostEqual(result[1, 1], 0.0)
        self.assertAlmostEqual(result[0, 1], 0.0)

    def test_calculate_tile_intensity(self):
        """Intensity calculation works on simple image."""
        image = np.array(
            [
                [0, 0, 200, 200],
                [0, 0, 200, 200],
                [100, 100, 100, 100],
                [100, 100, 100, 100],
            ],
            dtype=np.float64,
        )

        result = self._tile_intensity(image, 2, 2)
        self.assertEqual(result.shape, (2, 2))
        self.assertAlmostEqual(result[0, 0], 0.0)
        self.assertAlmostEqual(result[0, 1], 200.0)
        self.assertAlmostEqual(result[1, 0], 100.0)
        self.assertAlmostEqual(result[1, 1], 100.0)

    @unittest.skipUnless(HAS_HEAVY_DEPS, "Requires scipy")
    def test_calculate_tile_edges(self):
        """Edge detection works and returns correct shape."""
        from scipy.ndimage import convolve

        # 20x20 image with a sharp edge in top-right tile
        image = np.zeros((20, 20), dtype=np.float64)
        image[:10, 10:] = 255

        kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)
        laplacian = convolve(image, kernel, mode="nearest")

        tiles_x, tiles_y = 2, 2
        tile_h, tile_w = 10, 10
        scores = np.zeros((tiles_y, tiles_x))
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                tile_lap = laplacian[
                    ty * tile_h : (ty + 1) * tile_h, tx * tile_w : (tx + 1) * tile_w
                ]
                scores[ty, tx] = np.var(tile_lap)

        self.assertEqual(scores.shape, (2, 2))
        self.assertGreater(scores[0, 0] + scores[0, 1], 0)

    @unittest.skipUnless(HAS_HEAVY_DEPS, "Requires scipy")
    def test_edge_detection_performance(self):
        """Edge detection via scipy.ndimage.convolve is fast on large images."""
        import time

        from scipy.ndimage import convolve

        image = np.random.randint(0, 255, (2000, 2000), dtype=np.uint16).astype(
            np.float64
        )
        kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)

        start = time.time()
        laplacian = convolve(image, kernel, mode="nearest")
        tiles_x, tiles_y = 10, 10
        tile_h, tile_w = 200, 200
        scores = np.zeros((tiles_y, tiles_x))
        for ty in range(tiles_y):
            for tx in range(tiles_x):
                tile_lap = laplacian[
                    ty * tile_h : (ty + 1) * tile_h, tx * tile_w : (tx + 1) * tile_w
                ]
                scores[ty, tx] = np.var(tile_lap)
        elapsed = time.time() - start

        self.assertEqual(scores.shape, (10, 10))
        self.assertLess(elapsed, 5.0, "Edge detection took too long")

    def test_uint16_image_variance(self):
        """Variance calculation handles uint16 images."""
        image = np.array(
            [
                [0, 0, 5000, 5000],
                [0, 0, 5000, 5000],
                [1000, 1000, 1000, 1000],
                [1000, 1000, 1000, 1000],
            ],
            dtype=np.uint16,
        )

        result = self._tile_variance(image, 2, 2)
        self.assertEqual(result.shape, (2, 2))
        self.assertAlmostEqual(result[0, 0], 0.0)


if __name__ == "__main__":
    unittest.main()
