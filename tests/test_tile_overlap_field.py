"""Tile workflows must send OVERLAP PERCENT in the settings fields, not counts.

The server reads ``Stack option settings 1/2`` as the X/Y overlap percent (see
WorkflowSettings.cpp getTileX/YOverlapPercent, and the app's own mip_overview
parser) and derives the tile grid itself from the region + FOV + overlap. The
app used to write the client tile COUNTS there, so the server read e.g. "3" as
3% overlap and imaged the wrong grid (confirmed on the rig: a 10%-overlap tile
scan came out as 3×5 tiles). These tests lock in that the generated workflow
carries the overlap percent.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from py2flamingo.models.microscope import Position  # noqa: E402
from py2flamingo.models.workflow import (  # noqa: E402
    TileSettings,
    WorkflowModel,
    WorkflowType,
)


def _tile_workflow(overlap_percent, nx, ny):
    return WorkflowModel(
        type=WorkflowType.TILE,
        name="tile-test",
        start_position=Position(x=1.0, y=2.0, z=3.0, r=0.0),
        end_position=Position(x=4.0, y=6.0, z=5.0, r=0.0),
        tile_settings=TileSettings(
            num_tiles_x=nx, num_tiles_y=ny, overlap_percent=overlap_percent
        ),
    )


def test_settings_fields_carry_overlap_percent_not_counts():
    wf = _tile_workflow(overlap_percent=10.0, nx=3, ny=4)
    stack = wf.to_workflow_dict()["Stack Settings"]
    assert stack["Stack option"] == "Tile"
    # Overlap percent (10), NOT the tile counts (3, 4).
    assert float(stack["Stack option settings 1"]) == 10.0
    assert float(stack["Stack option settings 2"]) == 10.0


def test_different_overlap_round_trips():
    wf = _tile_workflow(overlap_percent=25.0, nx=2, ny=7)
    stack = wf.to_workflow_dict()["Stack Settings"]
    assert float(stack["Stack option settings 1"]) == 25.0
    assert float(stack["Stack option settings 2"]) == 25.0
    # The tile counts are NOT what's transmitted.
    assert float(stack["Stack option settings 1"]) not in (2.0, 7.0)
