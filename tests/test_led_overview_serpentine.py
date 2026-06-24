"""Tests for LED2DOverviewWorkflow._z_sweep_positions (serpentine Z).

The overview sweeps Z continuously per tile. To avoid a full-stack Z reset
between tiles, alternate tiles sweep the same planes in reverse. The output is a
Z-collapsed projection, so reversing is output-neutral — it only saves stage
travel.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_led_overview_serpentine.py -q
"""

from py2flamingo.workflows.led_2d_overview_workflow import LED2DOverviewWorkflow

_z = LED2DOverviewWorkflow._z_sweep_positions


def test_ascending_is_zmin_to_zmax():
    pos = _z(14.0, 15.0, 0.25, ascending=True)
    assert pos == [14.0, 14.25, 14.5, 14.75, 15.0]


def test_descending_is_reverse_of_ascending():
    up = _z(14.0, 15.0, 0.25, ascending=True)
    down = _z(14.0, 15.0, 0.25, ascending=False)
    assert down == list(reversed(up))


def test_same_planes_regardless_of_direction():
    # Serpentine must visit the SAME set of planes — only travel order differs.
    up = _z(14.21, 24.21, 0.25, ascending=True)
    down = _z(14.21, 24.21, 0.25, ascending=False)
    assert sorted(up) == sorted(down)
    assert up[0] < up[-1]  # ascending starts low
    assert down[0] > down[-1]  # descending starts high


def test_endpoints_chain_without_reset():
    # Tile N ends at z_max ascending; tile N+1 (descending) starts at z_max.
    up = _z(14.0, 16.0, 0.5, ascending=True)
    down = _z(14.0, 16.0, 0.5, ascending=False)
    assert up[-1] == down[0]  # no jump back to z_min between tiles
    assert down[-1] == up[0]  # and the tile after that resumes ascending
