"""The model serializer must send tile OVERLAP PERCENT, not tile counts.

`Workflow.to_workflow_dict` (models/data/workflow.py) is used by the
programmatic `workflows/` package (repository + executor). The server reads
`Stack option settings 1/2` as X/Y overlap percent, so this locks the invariant
that made the Workflow-tab bug possible — preventing the model path from ever
regressing to emitting counts. The single UI path already routes through
`utils.workflow_serialization`; this guards the remaining model path.
"""

from py2flamingo.models.data.workflow import (
    TileSettings,
    Workflow,
    WorkflowType,
)
from py2flamingo.models.microscope import Position


def _tiled_workflow(overlap, nx, ny):
    return Workflow(
        workflow_type=WorkflowType.TILE,
        name="t",
        start_position=Position(x=1.0, y=2.0, z=3.0, r=0.0),
        end_position=Position(x=4.0, y=5.0, z=6.0, r=0.0),
        tile_settings=TileSettings(
            num_tiles_x=nx, num_tiles_y=ny, overlap_percent=overlap
        ),
    )


def test_tile_serializer_emits_overlap_percent_not_counts():
    wf = _tiled_workflow(overlap=20.0, nx=2, ny=3)
    stack = wf.to_workflow_dict()["Stack Settings"]
    assert stack["Stack option"] == "Tile"
    # Overlap %, in BOTH settings — NOT the 2 / 3 tile counts.
    assert stack["Stack option settings 1"] == 20.0
    assert stack["Stack option settings 2"] == 20.0
    assert stack["Stack option settings 1"] not in (2, 3)


def test_single_tile_is_a_plain_zstack():
    wf = _tiled_workflow(overlap=10.0, nx=1, ny=1)
    stack = wf.to_workflow_dict()["Stack Settings"]
    assert stack["Stack option"] == "ZStack"
    assert stack["Stack option settings 1"] == 0
    assert stack["Stack option settings 2"] == 0
