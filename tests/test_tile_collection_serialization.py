"""Tile-collection workflows go through the SAME serializer as the Workflow tab.

`tile_collection_dialog._build_workflow_text` now builds a section dict via
`build_tile_collection_section_dict` and serializes it with
`dict_to_workflow_text` — the #4 consolidation. These tests lock the behaviour
that matters for the rig:

* the per-tile Left/Right illumination-path override (smart limited
  acquisition) survives into the transmitted file;
* a Z-stack carries Start Z = z_min / End Z = z_max and the right plane count;
* the capture-mode field is emitted with the FULL server label (the server
  matches it including the "(0 full, …)" suffix).
"""

from types import SimpleNamespace

from py2flamingo.utils.workflow_parser import (
    dict_to_workflow_text,
    parse_workflow_file,
)
from py2flamingo.utils.workflow_serialization import (
    build_tile_collection_section_dict,
    build_tile_illumination_source,
)


def _illum(channel=None, power=0.0, laser_on=False, led_on=False, led=0.0):
    return SimpleNamespace(
        laser_channel=channel,
        laser_power_mw=power,
        laser_enabled=laser_on,
        led_enabled=led_on,
        led_intensity_percent=led,
    )


def _camera():
    return {
        "exposure_us": 9002.0,
        "frame_rate": 40.0,
        "aoi_width": 2048,
        "aoi_height": 1024,
        "cam1_capture_percentage": 100,
        "cam1_capture_mode": 2,  # "from back" — a NON-default mode
        "cam2_capture_percentage": 100,
        "cam2_capture_mode": 3,
    }


def _save():
    return {
        "save_drive": "/media/deploy/ctlsm1",
        "save_directory": "tile_run",
        "save_mip": True,
        "display_mip": False,
        "save_format": "Raw",
        "save_enabled": True,
        "live_view": False,
    }


def test_illumination_source_lists_all_slots_and_path_override():
    illum_list = [_illum("Laser 4 640 nm", 15.0, laser_on=True)]
    src = build_tile_illumination_source(illum_list, left_on=False, right_on=True)

    # Enabled laser in exact "power on" format; disabled ones present as 0.
    assert src["Laser 4 4: 640 nm MLE"] == "15.00 1"
    assert src["Laser 1 1: 405 nm MLE"] == "0.00 0"
    assert src["Laser 5"] == "0.00 0"  # empty slot
    # Per-tile override reflected in the path flags.
    assert src["Left path"] == "OFF 0"
    assert src["Right path"] == "ON 1"


def test_zstack_positions_and_plane_count():
    pos = SimpleNamespace(x=4.9, y=13.8, z=19.0, r=-0.002)
    src = build_tile_illumination_source([], left_on=True, right_on=False)
    wf = build_tile_collection_section_dict(
        name="tileA",
        position=pos,
        camera=_camera(),
        illumination_source=src,
        multi_laser=False,
        save_settings=_save(),
        z_min=19.0,
        z_max=20.0,
        is_zstack=True,
        z_step_um=2.5,
        z_velocity_mm_s=0.2,
    )
    assert wf["Stack Settings"]["Stack option"] == "ZStack"
    # 1.0 mm range / 2.5 µm step + 1 = 401 planes.
    assert wf["Stack Settings"]["Number of planes"] == 401
    assert wf["Start Position"]["Z (mm)"] == 19.0
    assert wf["End Position"]["Z (mm)"] == 20.0
    assert wf["Start Position"]["X (mm)"] == 4.9
    assert wf["Experiment Settings"]["Sample"] == "tileA"
    assert wf["Experiment Settings"]["Comments"] == "Tile collection workflow"


def test_round_trip_preserves_override_and_capture_mode():
    pos = SimpleNamespace(x=1.0, y=2.0, z=10.0, r=0.0)
    src = build_tile_illumination_source(
        [_illum("Laser 2 488 nm", 20.0, laser_on=True)],
        left_on=False,
        right_on=True,
    )
    wf = build_tile_collection_section_dict(
        name="t",
        position=pos,
        camera=_camera(),
        illumination_source=src,
        multi_laser=True,
        save_settings=_save(),
        z_min=10.0,
        z_max=11.0,
        is_zstack=True,
        z_step_um=5.0,
        z_velocity_mm_s=0.15,
    )
    text = dict_to_workflow_text(wf)

    # The server matches capture mode by its FULL label — it must be emitted.
    assert (
        "Camera 1 capture mode (0 full, 1 from front, 2 from back, 3 none) = 2" in text
    )
    # The per-tile path override reaches the wire (Illumination Path section).
    assert "Left path = OFF 0" in text
    assert "Right path = ON 1" in text
    assert "Run stack with multiple lasers on = true" in text


def test_round_trip_reparses_to_consistent_values(tmp_path):
    pos = SimpleNamespace(x=3.0, y=4.0, z=12.0, r=1.5)
    src = build_tile_illumination_source([], left_on=True, right_on=False)
    wf = build_tile_collection_section_dict(
        name="rt",
        position=pos,
        camera=_camera(),
        illumination_source=src,
        multi_laser=False,
        save_settings=_save(),
        z_min=12.0,
        z_max=13.0,
        is_zstack=True,
        z_step_um=2.5,
        z_velocity_mm_s=0.2,
    )
    path = tmp_path / "wf.txt"
    path.write_text(dict_to_workflow_text(wf))

    parsed = parse_workflow_file(str(path))
    assert abs(float(parsed["Start Position"]["Z (mm)"]) - 12.0) < 1e-6
    assert abs(float(parsed["End Position"]["Z (mm)"]) - 13.0) < 1e-6
    assert parsed["Stack Settings"]["Stack option"] == "ZStack"


def test_snapshot_uses_tile_z_for_both_positions():
    pos = SimpleNamespace(x=1.0, y=2.0, z=7.5, r=0.0)
    src = build_tile_illumination_source([], left_on=True, right_on=False)
    wf = build_tile_collection_section_dict(
        name="s",
        position=pos,
        camera=_camera(),
        illumination_source=src,
        multi_laser=False,
        save_settings=_save(),
        z_min=5.0,
        z_max=9.0,
        is_zstack=False,
        z_step_um=1.0,
        z_velocity_mm_s=0.1,
    )
    assert wf["Stack Settings"]["Stack option"] == "Snapshot"
    assert wf["Stack Settings"]["Number of planes"] == 1
    assert wf["Start Position"]["Z (mm)"] == 7.5
    assert wf["End Position"]["Z (mm)"] == 7.5
    assert wf["Experiment Settings"]["Plane spacing (um)"] == 1.0
