"""Shared fakes and Qt fixture for pipeline tests.

Reused by every ``tests/test_pipeline_*.py`` file. The fakes match the service
contract that ``PipelineController._execute_pipeline`` injects into
``ExecutionContext.services`` (see ``pipeline_controller.py:89-155``).
"""

from __future__ import annotations

import os

# Qt headless setup must happen before any PyQt5 import. We set this defensively
# even though ``tests/test_application.py:20`` already does the same — running a
# single pipeline test directly (without going through run_tests.py) should still
# pick up the offscreen platform.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

# ---------------------------------------------------------------------------
# Qt singleton
# ---------------------------------------------------------------------------

_qt_app = None


def qt_app():
    """Return a process-wide singleton ``QApplication``.

    pyqtSignal construction requires a QApplication instance; the signals fire
    synchronously without an event loop, so tests never call ``exec_()``.
    """
    global _qt_app
    if _qt_app is None:
        from PyQt5.QtWidgets import QApplication

        _qt_app = QApplication.instance() or QApplication([])
    return _qt_app


# ---------------------------------------------------------------------------
# Fake services
# ---------------------------------------------------------------------------


class FakeWorkflow:
    """Minimal workflow object returned by ``FakeWorkflowFacade.load_workflow``.

    Has the attributes ``WorkflowRunner`` touches (start_position, stack_settings,
    tile_settings, end_position, output_path).
    """

    def __init__(self, output_path: str = "/tmp/fake_workflow_output"):
        self.start_position = SimpleNamespace(x=0.0, y=0.0, z=0.0, r=0.0)
        self.stack_settings = None
        self.tile_settings = None
        self.end_position = None
        self.output_path = output_path


class FakeWorkflowFacade:
    """Mock ``WorkflowFacade`` that satisfies ``WorkflowRunner``.

    ``WorkflowRunner`` polls ``get_workflow_status()`` every 1.0s
    (``workflow_runner.py:271-293``) until it sees a status whose ``.name`` is
    one of ``COMPLETED``/``IDLE``/``STOPPED``, or until status is ``None``. By
    default this fake returns a single ``COMPLETED`` and the runner exits the
    loop on the first poll, keeping tests fast.
    """

    def __init__(
        self,
        *,
        status_sequence: Optional[Iterable[Any]] = None,
        output_path: str = "/tmp/fake_workflow_output",
        start_workflow_succeeds: bool = True,
    ):
        self._status_seq = list(
            status_sequence
            if status_sequence is not None
            else [SimpleNamespace(name="COMPLETED")]
        )
        self._calls = 0
        self._output_path = output_path
        self._start_succeeds = start_workflow_succeeds
        self._current: Optional[FakeWorkflow] = None
        self.started_workflows: List[FakeWorkflow] = []
        self.stop_called = False

    def load_workflow(self, _path):
        wf = FakeWorkflow(output_path=self._output_path)
        self._current = wf
        return wf

    def start_workflow(self, workflow):
        self.started_workflows.append(workflow)
        self._current = workflow
        return self._start_succeeds

    def get_workflow_status(self):
        if self._calls < len(self._status_seq):
            s = self._status_seq[self._calls]
            self._calls += 1
            return s
        return None

    def stop_workflow(self):
        self.stop_called = True

    def get_current_workflow(self):
        return self._current


class FakeVoxelStorage:
    """Mock voxel storage exposing the methods ``ThresholdRunner`` and
    ``WorkflowRunner`` use (``has_data``, ``get_display_volume``).
    """

    def __init__(self, volumes_by_channel: Optional[Dict[int, np.ndarray]] = None):
        self._volumes: Dict[int, np.ndarray] = dict(volumes_by_channel or {})

    def has_data(self, channel: int) -> bool:
        vol = self._volumes.get(channel)
        return vol is not None

    def get_display_volume(self, channel: int):
        return self._volumes.get(channel)

    def set_volume(self, channel: int, volume: np.ndarray) -> None:
        self._volumes[channel] = volume


class FakePositionController:
    """Mock position controller returning a fixed origin Position-shaped object."""

    def __init__(self, position=None):
        self._pos = position or SimpleNamespace(x=0.0, y=0.0, z=0.0, r=0.0)

    def get_current_position(self):
        return self._pos


def fake_coord_config() -> Dict[str, Any]:
    """Return a coordinate_config dict matching ``configs/visualization_3d_config.yaml``.

    Keys used by ``ThresholdRunner`` and ``WorkflowRunner`` are
    ``display.voxel_size_um`` and ``stage_control.{x,y,z}_range_mm``.
    ``focus_frame.field_of_view_*_mm`` is read by WORKFLOW auto-tiling.
    """
    return {
        "display": {"voxel_size_um": [50.0, 50.0, 50.0]},
        "stage_control": {
            "x_range_mm": [0.0, 26.0],
            "y_range_mm": [0.0, 26.0],
            "z_range_mm": [0.0, 14.0],
            "invert_x_default": False,
        },
        "focus_frame": {
            "field_of_view_x_mm": 0.52,
            "field_of_view_y_mm": 0.52,
        },
    }


# ---------------------------------------------------------------------------
# Volume helpers
# ---------------------------------------------------------------------------


def make_bright_volume(
    shape=(4, 8, 8),
    bright_value: int = 255,
    bright_count: int = 1,
) -> np.ndarray:
    """Build a small uint16 volume with ``bright_count`` bright voxels.

    Useful for tests that want ``ThresholdAnalysisService`` to detect a
    predictable number of objects.
    """
    vol = np.zeros(shape, dtype=np.uint16)
    if bright_count <= 0:
        return vol
    z = shape[0] // 2
    # Spread bright voxels along the X axis with a gap so morph opening doesn't
    # merge them: voxel at (z, y, 1 + 2*i) for i in range(bright_count).
    y = shape[1] // 2
    for i in range(min(bright_count, max(1, shape[2] // 2))):
        vol[z, y, 1 + 2 * i] = bright_value
    return vol


# ---------------------------------------------------------------------------
# Service-bundle helper
# ---------------------------------------------------------------------------


def build_test_services(
    *,
    volumes: Optional[Dict[int, np.ndarray]] = None,
    workflow_facade: Optional[Any] = None,
    coord_config: Optional[Dict[str, Any]] = None,
    position_controller: Optional[Any] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the dict shape ``ExecutionContext(services=...)`` expects.

    Mirrors the service keys assembled in
    ``pipeline_controller.py:_execute_pipeline`` (lines 89-155).
    """
    services: Dict[str, Any] = {}
    if workflow_facade is not None:
        services["workflow_facade"] = workflow_facade
    if volumes is not None:
        services["voxel_storage"] = FakeVoxelStorage(volumes)
    if position_controller is not None:
        services["position_controller"] = position_controller
    if coord_config is not None:
        services["coordinate_config"] = coord_config
    if extra:
        services.update(extra)
    return services
