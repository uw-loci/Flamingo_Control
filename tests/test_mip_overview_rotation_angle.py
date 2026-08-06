"""The rotation angle an overview was acquired at must survive to Acquire Tiles.

The bug: `MIPTileResult.rotation_angle` and `MIPOverviewConfig.rotation_angle`
are documented as "rotation angle when acquired", are threaded all the way
through to the collection workflows, and were **never populated** — every
construction site omitted them, so both sat at their 0.0 default.

`TileCollectionDialog` then took `left_rotation=self._config.rotation_angle`,
i.e. always 0.0. So "Acquire Tiles" from an overview of a sample at any
non-zero angle re-collected at R=0: the right XY grid over the wrong view of
the sample, discovered only hours later in the stitched result.
"""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.models.mip_overview import (  # noqa: E402
    MIPTileResult,
    read_tile_rotation_angle,
)

_SETTINGS = """<Experiment Settings>
<Start Position>
X (mm) = 4.060000
Y (mm) = 17.500000
Z (mm) = 10.100000
Angle (degrees) = {angle}
</Start Position>
<End Position>
X (mm) = 4.060000
Y (mm) = 17.500000
Z (mm) = 11.100000
Angle (degrees) = {angle}
</End Position>
</Experiment Settings>
"""


class TestReadTileRotationAngle(unittest.TestCase):
    def _tile(self, tmp, body, name="acq_Settings.txt"):
        folder = Path(tmp) / "X4.06_Y17.50"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(body)
        return folder

    def test_reads_the_angle_from_tile_settings(self):
        with TemporaryDirectory() as tmp:
            folder = self._tile(tmp, _SETTINGS.format(angle="45.000000"))

            self.assertAlmostEqual(read_tile_rotation_angle(folder), 45.0)

    def test_reads_a_negative_angle(self):
        with TemporaryDirectory() as tmp:
            folder = self._tile(tmp, _SETTINGS.format(angle="-90.000000"))

            self.assertAlmostEqual(read_tile_rotation_angle(folder), -90.0)

    def test_falls_back_to_workflow_txt(self):
        with TemporaryDirectory() as tmp:
            folder = self._tile(
                tmp, _SETTINGS.format(angle="30.0"), name="Workflow.txt"
            )

            self.assertAlmostEqual(read_tile_rotation_angle(folder), 30.0)

    def test_missing_metadata_returns_none_not_zero(self):
        """'Not stated' must be distinguishable from a genuine 0 degrees."""
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "X4.06_Y17.50"
            folder.mkdir(parents=True)

            self.assertIsNone(read_tile_rotation_angle(folder))

    def test_a_real_zero_reads_as_zero(self):
        with TemporaryDirectory() as tmp:
            folder = self._tile(tmp, _SETTINGS.format(angle="0.000000"))

            self.assertEqual(read_tile_rotation_angle(folder), 0.0)

    def test_it_takes_the_START_position_angle(self):
        """End Position also carries one; Start is the acquisition angle."""
        body = _SETTINGS.format(angle="0.0").replace(
            "Z (mm) = 11.100000\nAngle (degrees) = 0.0",
            "Z (mm) = 11.100000\nAngle (degrees) = 77.0",
        )
        with TemporaryDirectory() as tmp:
            folder = self._tile(tmp, body)

            self.assertEqual(read_tile_rotation_angle(folder), 0.0)

    def test_unreadable_metadata_does_not_raise(self):
        with TemporaryDirectory() as tmp:
            folder = self._tile(tmp, "not a settings file at all")

            self.assertIsNone(read_tile_rotation_angle(folder))


class TestConfigCarriesTheAngle(unittest.TestCase):
    """The overview config is what Acquire Tiles reads."""

    def _agg(self, angles):
        from py2flamingo.views.dialogs.mip_overview_dialog import (
            _tiles_rotation_angle,
        )

        return _tiles_rotation_angle(
            [
                MIPTileResult(
                    x=0, y=0, z=0, tile_x_idx=0, tile_y_idx=0, rotation_angle=a
                )
                for a in angles
            ]
        )

    def test_a_uniform_angle_is_carried_through(self):
        self.assertEqual(self._agg([45.0, 45.0, 45.0]), 45.0)

    def test_zero_stays_zero(self):
        self.assertEqual(self._agg([0.0, 0.0]), 0.0)

    def test_no_tiles_is_zero_not_a_crash(self):
        self.assertEqual(self._agg([]), 0.0)

    def test_a_mixed_set_cannot_silently_collapse_to_zero(self):
        """If tiles disagree, the non-zero angle must win, not the default."""
        self.assertEqual(self._agg([0.0, 45.0, 0.0]), 45.0)

    def test_the_largest_magnitude_wins_including_negatives(self):
        self.assertEqual(self._agg([0.0, -90.0, 45.0]), -90.0)


if __name__ == "__main__":
    unittest.main()
