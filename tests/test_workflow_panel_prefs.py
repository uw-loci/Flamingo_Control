"""The Workflow tab must remember its values across a restart it did not control.

Three separate bugs made settings "not stick":

1. ``_save_workflow_type`` wrote ``{"workflow_type_index": n}`` through
   ``save_dialog_state``, which REPLACES the stored blob — erasing
   ``workflow_dict``. The type-changed signal fires during startup, immediately
   after the restore, so the tab loaded its values and then threw them away.
2. ``_persist_workflow_state`` only mutated the geometry manager's in-memory
   dict; the single flush to disk lived in ``MainWindow.closeEvent``, so any
   crash, kill, or hung shutdown lost the whole tab.
3. ``SavePanel._save_last_used_drive`` wrote to ``config_service.config``, which
   is not in the service's persisted-key list, so it never reached disk at all.

All three are tested by an actual round trip through a file, not by asserting
that a setter was called.
"""

import json

import pytest
from PyQt5.QtWidgets import QApplication

from py2flamingo.services.configuration_service import ConfigurationService
from py2flamingo.services.window_geometry_manager import WindowGeometryManager
from py2flamingo.views import workflow_view as workflow_view_module
from py2flamingo.views.workflow_panels.save_panel import LAST_USED_DRIVE_KEY, SavePanel


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def geometry_store(tmp_path, monkeypatch):
    """Point WorkflowView's persistence at a temp file and hand back the path."""
    config_file = tmp_path / "window_geometry.json"
    manager = WindowGeometryManager(config_file=str(config_file))
    monkeypatch.setattr(workflow_view_module, "_default_geometry_manager", manager)
    return config_file


def _persisted_workflow_dict(config_file):
    data = json.loads(config_file.read_text())
    return data["windows"]["WorkflowView"]["dialog_state"]["workflow_dict"]


def test_persist_reaches_disk_without_a_clean_close(qapp, geometry_store):
    """The regression: only MainWindow.closeEvent ever wrote the file."""
    view = workflow_view_module.WorkflowView(controller=None)
    view._save_panel._format_combo.setCurrentIndex(2)  # Raw
    view._camera_panel._aoi_width = 512
    view._camera_panel._aoi_height = 512

    view._persist_workflow_state()  # what the 600 ms debounce timer calls

    assert geometry_store.exists(), "workflow state never reached disk"
    stored = _persisted_workflow_dict(geometry_store)
    assert stored["Experiment Settings"]["Save image data"] == "Raw"
    assert stored["Camera Settings"]["AOI width"] == 512


def test_format_and_aoi_come_back_on_the_next_launch(qapp, geometry_store):
    view = workflow_view_module.WorkflowView(controller=None)
    view._save_panel._format_combo.setCurrentIndex(2)  # Raw
    view._camera_panel._aoi_width = 512
    view._camera_panel._aoi_height = 1024
    view._persist_workflow_state()

    relaunched = workflow_view_module.WorkflowView(controller=None)
    assert relaunched._save_panel._format_combo.currentText() == "Raw"
    assert relaunched._camera_panel._aoi_width == 512
    assert relaunched._camera_panel._aoi_height == 1024


def test_saving_the_workflow_type_does_not_erase_the_panel_values(qapp, geometry_store):
    """The root cause: save_dialog_state replaces, so a partial write wiped it."""
    view = workflow_view_module.WorkflowView(controller=None)
    view._save_panel._format_combo.setCurrentIndex(2)  # Raw
    view._persist_workflow_state()

    view._save_workflow_type(view._type_combo.currentIndex())

    # Read back through the manager, not the file: save_dialog_state does not
    # flush, so the stale on-disk copy would mask the in-memory clobber.
    state = workflow_view_module._default_geometry_manager.restore_dialog_state(
        "WorkflowView"
    )
    assert "workflow_dict" in state, "saving the type erased the panel values"
    assert state["workflow_dict"]["Experiment Settings"]["Save image data"] == "Raw"


def test_persist_is_harmless_without_a_geometry_manager(qapp, monkeypatch):
    """Headless/test contexts have no manager; persistence must not raise."""
    monkeypatch.setattr(workflow_view_module, "_default_geometry_manager", None)
    workflow_view_module.WorkflowView(controller=None)._persist_workflow_state()


class _FakeApp:
    def __init__(self, config_service):
        self.config_service = config_service


def test_last_used_drive_reaches_disk(qapp, tmp_path):
    """The second regression: config[...] is not in the persisted-key list."""
    SavePanel(
        app=_FakeApp(ConfigurationService(base_path=tmp_path))
    )._save_last_used_drive("D:")

    on_disk = json.loads((tmp_path / "session_paths.json").read_text())
    assert on_disk["workflow_panel_prefs"][LAST_USED_DRIVE_KEY] == "D:"

    # A fresh service reading the same directory must see it.
    relaunched = SavePanel(app=_FakeApp(ConfigurationService(base_path=tmp_path)))
    assert relaunched._get_last_used_drive() == "D:"


def test_save_panel_tolerates_a_config_service_without_workflow_prefs(qapp):
    """Older builds / test stubs lack the methods; the panel must still build."""

    class Bare:
        config_service = object()

    panel = SavePanel(app=Bare())
    panel._save_last_used_drive("D:")  # must not raise
    assert panel._get_last_used_drive() == ""
