"""Golden/characterization tests for the single workflow serializer.

`build_workflow_section_dict` is the one path all workflow transmit sites route
through (the #4 consolidation). These lock its output field-for-field per
workflow type, so any future edit that changes the transmitted format trips a
test instead of silently drifting the wire format.

Key regression guard: a **Tile** workflow must carry the X/Y OVERLAP PERCENT in
`Stack option settings 1/2` (the server reads those as overlap %, not tile
counts — sending counts made the server image the wrong grid).
"""

from types import SimpleNamespace

import pytest

from py2flamingo.models.data.workflow import WorkflowType
from py2flamingo.utils.workflow_serialization import build_workflow_section_dict


def _pos(x, y, z, r=0.0):
    return SimpleNamespace(x=x, y=y, z=z, r=r)


def _base_kwargs(**overrides):
    """Representative inputs; individual tests override what they exercise."""
    kwargs = dict(
        workflow_type=WorkflowType.ZSTACK,
        position_a=_pos(1.0, 2.0, 3.0, 0.5),
        position_b=_pos(4.0, 5.0, 6.0, 9.0),
        camera={
            "exposure_us": 9997.0,
            "frame_rate": 80.23,
            "aoi_width": 2048,
            "aoi_height": 1024,
            "cam1_capture_percentage": 100,
            "cam1_capture_mode": 0,
            "cam2_capture_percentage": 100,
            "cam2_capture_mode": 3,
        },
        save={"Save image data": "Raw", "Comments": "hi"},
        illumination={"Laser 3 3: 561 nm MLE": "21.23 1"},
        illumination_options={"Run stack with multiple lasers on": "false"},
        stack={
            "Number of planes": 400,
            "Change in Z axis (mm)": 1.0,
            "Z stage velocity (mm/s)": "0.2",
        },
        plane_spacing_um=2.5,
    )
    kwargs.update(overrides)
    return kwargs


def test_camera_section_and_capture_fields_always_present():
    wf = build_workflow_section_dict(**_base_kwargs())
    assert wf["Camera Settings"] == {
        "Exposure time (us)": 9997.0,
        "Frame rate (f/s)": 80.23,
        "AOI width": 2048,
        "AOI height": 1024,
    }
    stack = wf["Stack Settings"]
    assert stack["Camera 1 capture percentage"] == 100
    assert stack["Camera 1 capture mode"] == 0
    assert stack["Camera 2 capture mode"] == 3
    # Z-stack fields from the input stack are preserved.
    assert stack["Number of planes"] == 400


def test_zstack_end_position_takes_z_from_b():
    wf = build_workflow_section_dict(**_base_kwargs(workflow_type=WorkflowType.ZSTACK))
    assert wf["Start Position"] == {
        "X (mm)": 1.0,
        "Y (mm)": 2.0,
        "Z (mm)": 3.0,
        "Angle (degrees)": 0.5,
    }
    assert wf["End Position"] == {
        "X (mm)": 1.0,
        "Y (mm)": 2.0,
        "Z (mm)": 6.0,
        "Angle (degrees)": 0.5,
    }
    # No tiling on a z-stack.
    assert "Stack option" not in wf["Stack Settings"]
    assert wf["Experiment Settings"]["Plane spacing (um)"] == 2.5


def test_tile_transmits_overlap_percent_not_counts():
    wf = build_workflow_section_dict(
        **_base_kwargs(
            workflow_type=WorkflowType.TILE,
            tiling={"overlap_percent": 20.0},
        )
    )
    stack = wf["Stack Settings"]
    assert stack["Stack option"] == "Tile"
    # THE regression guard: overlap %, in BOTH settings, not a tile count.
    assert stack["Stack option settings 1"] == 20.0
    assert stack["Stack option settings 2"] == 20.0
    # Tile end position: X/Y/Z from B, R from A.
    assert wf["End Position"] == {
        "X (mm)": 4.0,
        "Y (mm)": 5.0,
        "Z (mm)": 6.0,
        "Angle (degrees)": 0.5,
    }


def test_tile_overlap_defaults_to_zero_when_missing():
    wf = build_workflow_section_dict(
        **_base_kwargs(workflow_type=WorkflowType.TILE, tiling=None)
    )
    stack = wf["Stack Settings"]
    assert stack["Stack option settings 1"] == 0
    assert stack["Stack option settings 2"] == 0


def test_multi_angle_merges_section_and_z_from_b():
    wf = build_workflow_section_dict(
        **_base_kwargs(
            workflow_type=WorkflowType.MULTI_ANGLE,
            multiangle={"Number of angles": 3, "Angle step size": 45},
        )
    )
    exp = wf["Experiment Settings"]
    assert exp["Number of angles"] == 3
    assert exp["Angle step size"] == 45
    assert exp["Plane spacing (um)"] == 2.5
    assert wf["End Position"]["Z (mm)"] == 6.0
    assert wf["End Position"]["X (mm)"] == 1.0  # from A


def test_time_lapse_merges_section_end_equals_start_and_spacing_one():
    wf = build_workflow_section_dict(
        **_base_kwargs(
            workflow_type=WorkflowType.TIME_LAPSE,
            timelapse={"Duration (dd:hh:mm:ss)": "00:00:10:00"},
        )
    )
    exp = wf["Experiment Settings"]
    assert exp["Duration (dd:hh:mm:ss)"] == "00:00:10:00"
    # Non-Z-scanning type reports plane spacing 1.0.
    assert exp["Plane spacing (um)"] == 1.0
    assert wf["End Position"] == wf["Start Position"]


def test_save_fields_are_spread_into_experiment_settings():
    wf = build_workflow_section_dict(**_base_kwargs())
    exp = wf["Experiment Settings"]
    assert exp["Save image data"] == "Raw"
    assert exp["Comments"] == "hi"
    assert exp["Frame rate (f/s)"] == 80.23
    assert exp["Exposure time (us)"] == 9997.0


def test_caller_inputs_are_not_mutated():
    kwargs = _base_kwargs(
        workflow_type=WorkflowType.TILE, tiling={"overlap_percent": 10.0}
    )
    stack_in = kwargs["stack"]
    stack_snapshot = dict(stack_in)
    build_workflow_section_dict(**kwargs)
    # The assembler copies the stack dict; the caller's is untouched.
    assert stack_in == stack_snapshot
    assert "Stack option" not in stack_in
    assert "Camera 1 capture mode" not in stack_in


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
