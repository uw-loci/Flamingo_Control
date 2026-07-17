"""Load Raw Data must support Single Workflow acquisitions.

A tiled acquisition writes ``X{x}_Y{y}`` subfolders; a Single Workflow
acquisition instead writes ``Workflow.txt`` + ``.raw`` directly into one folder
whose name does NOT encode the position. These tests cover the discovery and
coordinate-fallback logic that lets the same "Load Raw Data" path handle both.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_single_workflow_load.py -q
"""

from pathlib import Path

import numpy as np

from py2flamingo.utils.tile_workflow_parser import read_xy_position_from_workflow
from py2flamingo.visualization.disk_tile_loader import (
    find_workflow_acquisition_folders,
    parse_tile_folder,
)

_WORKFLOW_TXT = """<Workflow Settings>
  <Start Position>
    X (mm) = 5.400
    Y (mm) = 18.660
    Z (mm) = 16.000
  </Start Position>
  <End Position>
    X (mm) = 5.400
    Y (mm) = 18.660
    Z (mm) = 17.000
  </End Position>
  <Illumination Source>
    Laser 1 1: 405 nm MLE = 0.00 0
    Laser 2 2: 488 nm MLE = 10.00 1
    Laser 3 3: 561 nm MLE = 0.00 0
    Laser 4 4: 640 nm MLE = 0.00 0
  </Illumination Source>
</Workflow Settings>
"""


def _write_single_workflow_acq(folder: Path, planes: int = 2, side: int = 4) -> Path:
    """Create a Single Workflow acquisition: Workflow.txt + one .raw stack."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Workflow.txt").write_text(_WORKFLOW_TXT)
    # side x side x planes uint16 stack; filename lacks a meaningful X/Y (X000_Y000)
    raw = np.zeros((planes, side, side), dtype=np.uint16)
    raw_name = f"S000_t000000_V000_R0000_X000_Y000_C01_I0_D1_P{planes:05d}.raw"
    (folder / raw_name).write_bytes(raw.tobytes())
    return folder


def test_read_xy_position_from_workflow(tmp_path):
    wf = tmp_path / "Workflow.txt"
    wf.write_text(_WORKFLOW_TXT)
    assert read_xy_position_from_workflow(wf) == (5.4, 18.66)


def test_read_xy_position_absent_returns_none(tmp_path):
    wf = tmp_path / "Workflow.txt"
    wf.write_text("<Workflow Settings>\n</Workflow Settings>\n")
    assert read_xy_position_from_workflow(wf) is None


def test_discovery_when_base_is_the_acquisition(tmp_path):
    acq = _write_single_workflow_acq(tmp_path / "20260616_Vax1_SingleShot")
    found = find_workflow_acquisition_folders(tmp_path / "20260616_Vax1_SingleShot")
    assert found == [acq]


def test_discovery_when_base_is_parent_of_acquisitions(tmp_path):
    a = _write_single_workflow_acq(tmp_path / "sampleA")
    b = _write_single_workflow_acq(tmp_path / "sampleB")
    found = find_workflow_acquisition_folders(tmp_path)
    assert sorted(found) == sorted([a, b])


def test_discovery_empty_when_no_acquisition(tmp_path):
    (tmp_path / "random").mkdir()
    (tmp_path / "random" / "notes.txt").write_text("hi")
    assert find_workflow_acquisition_folders(tmp_path) == []


def test_parse_tile_folder_uses_workflow_coords_when_name_lacks_xy(tmp_path):
    # Folder name has no X..._Y..., so coords must come from the Workflow.txt.
    acq = _write_single_workflow_acq(tmp_path / "20260616_Vax1_SingleShot")
    info = parse_tile_folder(acq)
    assert info is not None
    assert (info.x, info.y) == (5.4, 18.66)
    assert info.z_min == 16.0 and info.z_max == 17.0
    assert info.channels == [1]  # 488 nm laser enabled, left side


def test_parse_tile_folder_prefers_folder_name_xy_when_present(tmp_path):
    # A tiled folder name still wins over the workflow content.
    acq = _write_single_workflow_acq(tmp_path / "X9.99_Y1.11")
    info = parse_tile_folder(acq)
    assert (info.x, info.y) == (9.99, 1.11)
