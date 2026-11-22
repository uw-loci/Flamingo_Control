"""One tile step for the client's grids, and auto-tiling asks for the grid it means.

``tile_geometry.tile_step_mm`` is the step of every client-side grid: the LED
overview walk and its dialog preview, the acquisition-profile generator and
the pipeline's auto-tiling. They were four copies of ``fov * (1 - overlap)``
(the dialog's without the server's [0, 50] % clamp).

The pipeline's auto-tiling sent the scan WIDTH as ``End - Start``. The server
reads Start/End as corner-tile centers and adds half a field on each side
(``tile_geometry.compute_tile_geometry``, CheckStackTile), so it imaged two
extra rows and two extra columns.
"""

from types import SimpleNamespace

import pytest

from py2flamingo.utils.tile_geometry import (
    compute_tile_geometry,
    corner_span_for_tiles,
    tile_step_mm,
)


def test_tile_step_mm():
    assert tile_step_mm(1.0726, 0.0) == pytest.approx(1.0726)
    assert tile_step_mm(1.0, 10.0) == pytest.approx(0.9)
    assert tile_step_mm(1.0, 80.0) == pytest.approx(0.5)  # clamped to 50 %
    assert tile_step_mm(1.0, -5.0) == pytest.approx(1.0)
    # bit for bit the expression the LED overview walk used
    fov, pct = 1.0727, 12.5
    assert tile_step_mm(fov, pct) == fov * (1.0 - pct / 100.0)


@pytest.mark.parametrize("overlap", [0.0, 5.0, 10.0, 20.0, 33.0, 40.0, 49.0])
@pytest.mark.parametrize("n", [1, 2, 3, 4, 7, 12])
def test_the_server_lays_exactly_n_tiles_for_the_span(n, overlap):
    fov = 0.52
    span = corner_span_for_tiles(n, fov, overlap)
    geo = compute_tile_geometry(
        10.0, 10.0 + span, 5.0, 5.0 + span, 0.0, 0.0, fov, fov, overlap, overlap
    )
    assert (geo.tiles_x, geo.tiles_y) == (n, n)


def _run_auto_tiling(extent_x_mm, extent_y_mm):
    from py2flamingo.pipeline.engine.context import ExecutionContext
    from py2flamingo.pipeline.engine.node_runners.workflow_runner import (
        WorkflowRunner,
    )
    from py2flamingo.pipeline.headless_services import StubWorkflowFacade
    from py2flamingo.pipeline.models.pipeline import NodeType, Pipeline, create_node
    from tests.pipeline_helpers import fake_coord_config

    obj = SimpleNamespace(
        centroid_stage=(10.0, 12.0, 5.0),
        stage_coords_available=True,
        bounding_box=(slice(0, 1),) * 3,
        extent_mm=lambda voxel_um: (extent_x_mm, extent_y_mm, 0.1),
    )
    pipeline = Pipeline()
    node = create_node(
        NodeType.WORKFLOW,
        name="WORKFLOW",
        config={
            "template_file": "/tmp/template.txt",
            "use_input_position": True,
            "auto_tiling": True,
            "buffer_percent": 25.0,
        },
    )
    pipeline.add_node(node)
    facade = StubWorkflowFacade()
    ctx = ExecutionContext(
        services={"workflow_facade": facade, "coordinate_config": fake_coord_config()}
    )
    runner = WorkflowRunner()
    runner._get_input = lambda n, p, c, name: obj if name == "position" else None
    runner.run(node, pipeline, ctx)
    from py2flamingo.utils.workflow_keys import workflow_sections

    return workflow_sections(facade.started[-1])


def test_auto_tiling_images_the_tiles_it_counted():
    wf = _run_auto_tiling(2.0, 1.0)
    start, end, stack = wf["Start Position"], wf["End Position"], wf["Stack Settings"]
    sx, ex = float(start["X (mm)"]), float(end["X (mm)"])
    sy, ey = float(start["Y (mm)"]), float(end["Y (mm)"])
    # The server reads Stack option settings 1/2 as the overlap percent.
    assert stack["Stack option"] == "Tile"
    overlap_x = float(stack["Stack option settings 1"])
    overlap_y = float(stack["Stack option settings 2"])
    assert (overlap_x, overlap_y) == (10.0, 10.0)
    # 2.0 mm and 1.0 mm plus 25 % each side, at 0.52 mm and 10 % overlap:
    # the grid the server lays from the text that was sent.
    geo = compute_tile_geometry(
        sx, ex, sy, ey, 0.0, 0.0, 0.52, 0.52, overlap_x, overlap_y
    )
    assert (geo.tiles_x, geo.tiles_y) == (7, 4)
    # centered on the object
    assert (sx + ex) / 2 == pytest.approx(10.0)
    assert (sy + ey) / 2 == pytest.approx(12.0)
