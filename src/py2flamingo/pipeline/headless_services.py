"""Headless pipeline execution — public Python API.

Lets pipelines run without the editor dialog: from a notebook, a script, the
test suite, or the bundled CLI. The two entry points are:

    services = build_headless_services(volumes={0: vol}, ...)
    context  = run_pipeline_headless(pipeline, services=services)

``run_pipeline_headless`` builds the same 9-runner registry that
``PipelineController._execute_pipeline`` (``pipeline_controller.py:89-155``)
constructs in the GUI path. It then calls ``PipelineExecutor.run()``
synchronously on the calling thread — ``PipelineExecutor`` is a ``QThread``
subclass, but ``run()`` is just a regular method so no event loop is required.

Signals (``node_started``/``node_completed``/``pipeline_error``) fire
synchronously to whatever Qt slots are connected; the headless API connects a
single capture-list slot for ``pipeline_error`` so failures surface as
exceptions instead of silently being swallowed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set

import numpy as np

from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.executor import PipelineExecutor
from py2flamingo.pipeline.engine.node_runners.base_runner import AbstractNodeRunner
from py2flamingo.pipeline.engine.node_runners.conditional_runner import (
    ConditionalRunner,
)
from py2flamingo.pipeline.engine.node_runners.external_command_runner import (
    ExternalCommandRunner,
)
from py2flamingo.pipeline.engine.node_runners.foreach_runner import ForEachRunner
from py2flamingo.pipeline.engine.node_runners.overview_analysis_runner import (
    OverviewAnalysisRunner,
)
from py2flamingo.pipeline.engine.node_runners.post_processing_runner import (
    PostProcessingRunner,
)
from py2flamingo.pipeline.engine.node_runners.sample_view_data_runner import (
    SampleViewDataRunner,
)
from py2flamingo.pipeline.engine.node_runners.threshold_runner import ThresholdRunner
from py2flamingo.pipeline.engine.node_runners.timed_loop_runner import TimedLoopRunner
from py2flamingo.pipeline.engine.node_runners.workflow_runner import WorkflowRunner
from py2flamingo.pipeline.models.pipeline import NodeType, Pipeline
from py2flamingo.pipeline.models.port_types import PortType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory voxel storage (no GUI dependency)
# ---------------------------------------------------------------------------


class InMemoryVoxelStorage:
    """Minimal voxel storage backed by an in-memory dict.

    Implements the methods that ``ThresholdRunner``, ``WorkflowRunner``, and
    ``SampleViewDataRunner`` read: ``has_data(channel)`` and
    ``get_display_volume(channel)``.

    Channel keys are normalized to ints because pipelines loaded from JSON
    arrive with string channel keys (JSON spec) but ``ThresholdRunner``'s
    fallback at ``threshold_runner.py:88`` hard-codes int ``0``. Without
    normalization the lookup misses.
    """

    def __init__(self, volumes_by_channel: Optional[Dict[Any, np.ndarray]] = None):
        self._volumes: Dict[int, np.ndarray] = {}
        for k, v in (volumes_by_channel or {}).items():
            try:
                self._volumes[int(k)] = v
            except (TypeError, ValueError):
                logger.warning("Ignoring non-int voxel channel key: %r", k)

    @staticmethod
    def _coerce(channel) -> Optional[int]:
        try:
            return int(channel)
        except (TypeError, ValueError):
            return None

    def has_data(self, channel) -> bool:
        ch = self._coerce(channel)
        return ch is not None and self._volumes.get(ch) is not None

    def get_display_volume(self, channel) -> Optional[np.ndarray]:
        ch = self._coerce(channel)
        if ch is None:
            return None
        return self._volumes.get(ch)

    def set_volume(self, channel, volume: np.ndarray) -> None:
        ch = self._coerce(channel)
        if ch is not None:
            self._volumes[ch] = volume


# ---------------------------------------------------------------------------
# Stub workflow facade
# ---------------------------------------------------------------------------


class StubWorkflowFacade:
    """Workflow facade that pretends every workflow finishes immediately.

    Useful when running a pipeline in non-hardware mode and you want
    ``WorkflowRunner`` to short-circuit to ``COMPLETED`` on the first poll.
    Does NOT touch the microscope or write any files.
    """

    def __init__(self):
        self._calls = 0
        self._current = None

    def load_workflow(self, _path):
        from types import SimpleNamespace

        wf = SimpleNamespace(
            start_position=SimpleNamespace(x=0.0, y=0.0, z=0.0, r=0.0),
            stack_settings=None,
            tile_settings=None,
            end_position=None,
            output_path="",
        )
        self._current = wf
        return wf

    def start_workflow(self, _workflow):
        return True

    def get_workflow_status(self):
        from types import SimpleNamespace

        if self._calls == 0:
            self._calls += 1
            return SimpleNamespace(name="COMPLETED")
        return None

    def stop_workflow(self):
        pass

    def get_current_workflow(self):
        return self._current


# ---------------------------------------------------------------------------
# No-op runner for --skip-tag
# ---------------------------------------------------------------------------


class NoOpRunner(AbstractNodeRunner):
    """Runner that emits sensible defaults on every output port and returns.

    Used by ``run_pipeline_headless(skip_node_types=...)`` to short-circuit
    node types that need real hardware (WORKFLOW), real viewer state
    (SAMPLE_VIEW_DATA), or heavy dependencies (POST_PROCESSING / pyimagej).
    """

    _DEFAULTS = {
        PortType.TRIGGER: True,
        PortType.SCALAR: 0,
        PortType.OBJECT_LIST: [],
        PortType.OBJECT: None,
        PortType.VOLUME: None,
        PortType.POSITION: (0.0, 0.0, 0.0, 0.0),
        PortType.BOOLEAN: False,
        PortType.STRING: "",
        PortType.FILE_PATH: "",
        PortType.ANY: None,
    }

    def run(self, node, pipeline, context):
        for port in node.outputs:
            self._set_output(
                node,
                context,
                port.name,
                port.port_type,
                self._DEFAULTS.get(port.port_type),
            )
        logger.info("NoOpRunner: skipped %s '%s'", node.node_type.name, node.name)


# ---------------------------------------------------------------------------
# Service factory
# ---------------------------------------------------------------------------


def _load_default_coord_config() -> Dict[str, Any]:
    """Load ``configs/visualization_3d_config.yaml`` if it exists, else fall
    back to a sensible default.

    Mirrors ``PipelineController._build_coordinate_config`` (lines 157-180 in
    ``pipeline_controller.py``) so the GUI and headless paths see the same
    coord config when the file is present.
    """
    try:
        import yaml

        # py2flamingo/pipeline/headless_services.py → py2flamingo/configs/...
        config_path = (
            Path(__file__).parent.parent / "configs" / "visualization_3d_config.yaml"
        )
        if config_path.exists():
            with config_path.open() as f:
                full_config = yaml.safe_load(f) or {}
            return {
                "display": full_config.get("display", {}),
                "stage_control": full_config.get("stage_control", {}),
                "focus_frame": full_config.get("focus_frame", {}),
            }
    except Exception as e:
        logger.debug("Falling back to default coord_config: %s", e)
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


def build_headless_services(
    *,
    volumes: Optional[Dict[int, np.ndarray]] = None,
    coord_config: Optional[Dict[str, Any]] = None,
    workflow_facade: Optional[Any] = None,
    position_controller: Optional[Any] = None,
    enable_workflow: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the service dict that ``ExecutionContext`` expects.

    Args:
        volumes: Map of channel_id → 3D numpy array. Wired up as
            ``voxel_storage`` (an :class:`InMemoryVoxelStorage`).
        coord_config: Coordinate config dict. Defaults to the project's
            ``visualization_3d_config.yaml`` when available, otherwise to a
            built-in default.
        workflow_facade: A real or fake ``WorkflowFacade`` instance. If None
            and ``enable_workflow`` is True, a :class:`StubWorkflowFacade` is
            installed; if False, the ``workflow_facade`` key is omitted so
            ``WorkflowRunner`` will raise ``"WorkflowFacade service not
            available"`` (intentional — forces callers to opt in).
        position_controller: Optional. Anything with a
            ``get_current_position()`` method.
        enable_workflow: When True (and no explicit ``workflow_facade`` is
            given), inject a :class:`StubWorkflowFacade` so WORKFLOW nodes
            run as no-ops without raising.
        extra: Extra service entries to merge into the returned dict.

    Returns:
        Service dict with the same keys ``PipelineController._execute_pipeline``
        injects in the GUI path: ``voxel_storage``, ``coordinate_config``,
        ``workflow_facade`` (optional), ``position_controller`` (optional).
    """
    services: Dict[str, Any] = {}
    services["voxel_storage"] = InMemoryVoxelStorage(volumes or {})
    services["coordinate_config"] = coord_config or _load_default_coord_config()
    if workflow_facade is not None:
        services["workflow_facade"] = workflow_facade
    elif enable_workflow:
        services["workflow_facade"] = StubWorkflowFacade()
    if position_controller is not None:
        services["position_controller"] = position_controller
    if extra:
        services.update(extra)
    return services


# ---------------------------------------------------------------------------
# Runner registry
# ---------------------------------------------------------------------------


def _build_runners(
    skip_node_types: Optional[Iterable[NodeType]] = None,
) -> Dict[NodeType, AbstractNodeRunner]:
    """Build the same 9-runner registry the controller does, with optional
    no-op overrides.

    Mirrors ``pipeline_controller.py:122-133``.
    """
    skip_set: Set[NodeType] = set(skip_node_types or ())
    runners: Dict[NodeType, AbstractNodeRunner] = {
        NodeType.WORKFLOW: WorkflowRunner(),
        NodeType.THRESHOLD: ThresholdRunner(),
        NodeType.FOR_EACH: ForEachRunner(),
        NodeType.CONDITIONAL: ConditionalRunner(),
        NodeType.EXTERNAL_COMMAND: ExternalCommandRunner(),
        NodeType.SAMPLE_VIEW_DATA: SampleViewDataRunner(),
        NodeType.OVERVIEW_ANALYSIS: OverviewAnalysisRunner(),
        NodeType.POST_PROCESSING: PostProcessingRunner(),
        NodeType.TIMED_LOOP: TimedLoopRunner(),
    }
    for nt in skip_set:
        runners[nt] = NoOpRunner()
    return runners


# ---------------------------------------------------------------------------
# Synchronous executor entry point
# ---------------------------------------------------------------------------


def _ensure_qapplication():
    """Construct a ``QApplication`` if none exists.

    ``pyqtSignal`` *class* attributes need a ``QApplication`` instance to exist
    at the time their owning class is constructed; signals fire synchronously
    to connected slots without an event loop, so we never call ``exec_()``.
    """
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class HeadlessPipelineRun:
    """Result object returned by :func:`run_pipeline_headless`.

    Attributes:
        context: The ``ExecutionContext`` after the run, including all
            ``port_values`` produced by the pipeline.
        node_states: Map of node_id → ``"started"`` / ``"completed"`` /
            ``"error: <msg>"`` based on the signals the executor emitted.
        errors: List of ``pipeline_error`` strings (empty on success).
    """

    def __init__(self, context: ExecutionContext):
        self.context = context
        self.node_states: Dict[str, str] = {}
        self.errors: list = []

    @property
    def succeeded(self) -> bool:
        return not self.errors


def run_pipeline_headless(
    pipeline: Pipeline,
    services: Optional[Dict[str, Any]] = None,
    *,
    skip_node_types: Optional[Iterable[NodeType]] = None,
    raise_on_error: bool = True,
) -> HeadlessPipelineRun:
    """Run a pipeline synchronously on the calling thread.

    Args:
        pipeline: The :class:`Pipeline` to execute.
        services: Service dict (typically built by :func:`build_headless_services`).
            Defaults to an empty dict — fine for pipelines that need only
            EXTERNAL_COMMAND / OVERVIEW_ANALYSIS / arithmetic Conditional /
            etc., but THRESHOLD / SAMPLE_VIEW_DATA need ``voxel_storage`` and
            WORKFLOW needs ``workflow_facade``.
        skip_node_types: Replace these node types' runners with
            :class:`NoOpRunner` (used for hardware-free CI runs).
        raise_on_error: When True, re-raise as ``RuntimeError`` if the
            executor emits a ``pipeline_error`` signal. When False, the
            errors are recorded on the returned object and execution
            continues.

    Returns:
        :class:`HeadlessPipelineRun` with the final ``ExecutionContext``,
        per-node state, and any errors.
    """
    _ensure_qapplication()

    context = ExecutionContext(services=services or {})
    runners = _build_runners(skip_node_types)
    executor = PipelineExecutor(pipeline, context, runners)

    result = HeadlessPipelineRun(context)

    def _on_started(nid: str):
        result.node_states[nid] = "started"

    def _on_completed(nid: str):
        result.node_states[nid] = "completed"

    def _on_error(nid: str, msg: str):
        result.node_states[nid] = f"error: {msg}"

    def _on_pipeline_error(msg: str):
        result.errors.append(msg)

    executor.node_started.connect(_on_started)
    executor.node_completed.connect(_on_completed)
    executor.node_error.connect(_on_error)
    executor.pipeline_error.connect(_on_pipeline_error)

    # Synchronous: run() is just a method on QThread.
    executor.run()

    if result.errors and raise_on_error:
        raise RuntimeError(f"Pipeline failed: {result.errors[0]}")

    return result


# ---------------------------------------------------------------------------
# Convenience: load + run from a JSON path
# ---------------------------------------------------------------------------


def run_pipeline_file(
    pipeline_path: Path,
    services: Optional[Dict[str, Any]] = None,
    *,
    skip_node_types: Optional[Iterable[NodeType]] = None,
    raise_on_error: bool = True,
) -> HeadlessPipelineRun:
    """Load a pipeline JSON from disk and run it headlessly."""
    import json

    data = json.loads(Path(pipeline_path).read_text())
    pipeline = Pipeline.from_dict(data)
    return run_pipeline_headless(
        pipeline,
        services=services,
        skip_node_types=skip_node_types,
        raise_on_error=raise_on_error,
    )
