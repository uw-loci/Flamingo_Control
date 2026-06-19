"""Programmatic pipeline authoring — build a :class:`Pipeline` without the GUI.

The pipeline editor wires nodes by dragging ports, which produces UUID port
ids. From a script or the CLI that is awkward, so :class:`PipelineBuilder`
lets you refer to ports by *name* and resolves them to ids for you. It is a
thin wrapper over the existing model factory
(:func:`py2flamingo.pipeline.models.pipeline.create_node`) — no new graph
logic — and validates on :meth:`build` so authoring mistakes surface early.

    b = PipelineBuilder("detect")
    src = b.add(NodeType.SAMPLE_VIEW_DATA)
    thr = b.add(NodeType.THRESHOLD, channel_thresholds={0: 120})
    b.connect(src, "volume", thr, "volume")
    pipeline = b.build()

:func:`make_template` returns ready-to-run starter pipelines used by the
``py2flamingo-pipeline create`` CLI command.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Union

from py2flamingo.pipeline.models.pipeline import (
    NodeType,
    Pipeline,
    PipelineNode,
    create_node,
)


class PipelineBuilder:
    """Fluent builder that resolves port names to ids and validates on build."""

    def __init__(self, name: str = "Untitled Pipeline"):
        self._pipeline = Pipeline(name=name)
        self._auto_x = 0.0

    def add(
        self,
        node_type: Union[NodeType, str],
        name: Optional[str] = None,
        *,
        x: Optional[float] = None,
        y: float = 0.0,
        **config: Any,
    ) -> str:
        """Add a node and return its id.

        Args:
            node_type: A :class:`NodeType` or its name (e.g. ``"THRESHOLD"``).
            name: Display name (defaults to the node type's title).
            x, y: Editor canvas position. ``x`` auto-increments when omitted so
                nodes don't stack on top of each other when opened in the GUI.
            **config: Node ``config`` entries (e.g. ``channel_thresholds={0: 100}``).
        """
        nt = _coerce_node_type(node_type)
        if x is None:
            x = self._auto_x
            self._auto_x += 220.0
        node = create_node(
            nt, name=name, config=dict(config) if config else {}, x=x, y=y
        )
        self._pipeline.add_node(node)
        return node.id

    def connect(
        self,
        source_node_id: str,
        source_port: str,
        target_node_id: str,
        target_port: str,
    ):
        """Connect two nodes by port *name* (not id)."""
        src = self._require_node(source_node_id)
        tgt = self._require_node(target_node_id)
        src_port = src.get_output(source_port)
        if src_port is None:
            raise ValueError(
                f"Node '{src.name}' has no output port '{source_port}' "
                f"(have: {[p.name for p in src.outputs]})"
            )
        tgt_port = tgt.get_input(target_port)
        if tgt_port is None:
            raise ValueError(
                f"Node '{tgt.name}' has no input port '{target_port}' "
                f"(have: {[p.name for p in tgt.inputs]})"
            )
        return self._pipeline.add_connection(
            source_node_id, src_port.id, target_node_id, tgt_port.id
        )

    def set_config(self, node_id: str, **config: Any) -> None:
        """Merge extra config into an already-added node."""
        self._require_node(node_id).config.update(config)

    def set_input_required(self, node_id: str, port_name: str, required: bool) -> None:
        """Mark an input port as (not) required.

        Some runners (POST_PROCESSING, WORKFLOW) accept a value from either an
        input connection *or* the node ``config``. ``Pipeline.validate`` only
        knows about connections, so a config-driven node must declare the port
        optional to validate — mirroring the shipped
        ``tests/fixtures/pipelines/09_post_processing.json`` fixture.
        """
        node = self._require_node(node_id)
        port = node.get_input(port_name)
        if port is None:
            raise ValueError(f"Node '{node.name}' has no input port '{port_name}'")
        port.required = required

    def build(self, *, validate: bool = True) -> Pipeline:
        """Return the assembled :class:`Pipeline`.

        Args:
            validate: When True (default), raise ``ValueError`` if the graph is
                invalid (cycle / missing required input / type mismatch).
        """
        if validate:
            errors = self._pipeline.validate()
            if errors:
                raise ValueError("Invalid pipeline:\n  " + "\n  ".join(errors))
        return self._pipeline

    @property
    def pipeline(self) -> Pipeline:
        return self._pipeline

    # -- internals --

    def _require_node(self, node_id: str) -> PipelineNode:
        node = self._pipeline.get_node(node_id)
        if node is None:
            raise ValueError(f"No node with id {node_id!r}")
        return node


def _coerce_node_type(node_type: Union[NodeType, str]) -> NodeType:
    if isinstance(node_type, NodeType):
        return node_type
    try:
        return NodeType[str(node_type).upper()]
    except KeyError as e:
        valid = ", ".join(nt.name for nt in NodeType)
        raise ValueError(f"Unknown node type {node_type!r}. Valid: {valid}") from e


# ---------------------------------------------------------------------------
# Starter templates (used by `py2flamingo-pipeline create`)
# ---------------------------------------------------------------------------

# A default threshold for 8-bit collagen phantoms (bright fibers on dark bg).
_DEFAULT_THRESHOLD = 100


def list_templates() -> Dict[str, str]:
    """Return ``{template_name: one-line description}``."""
    return {
        "threshold": (
            "Single THRESHOLD node; reads channel 0 from voxel_storage "
            "(feed it with --input). Detects bright objects."
        ),
        "overview": (
            "Single OVERVIEW_ANALYSIS node; analyses channel 0 as a 2-D/3-D "
            "overview image."
        ),
        "threshold_foreach": (
            "THRESHOLD → FOR_EACH over detected objects (count + iterate)."
        ),
        "stitch": (
            "Single POST_PROCESSING node that stitches a raw acquisition "
            "directory (set 'acquisition_dir' in config)."
        ),
    }


def make_template(name: str, *, pipeline_name: Optional[str] = None) -> Pipeline:
    """Build a named starter pipeline.

    Args:
        name: One of :func:`list_templates`.
        pipeline_name: Override the pipeline's display name.
    """
    name = name.strip().lower()
    pname = pipeline_name or f"{name}_template"

    if name == "threshold":
        b = PipelineBuilder(pname)
        b.add(
            NodeType.THRESHOLD,
            channel_thresholds={0: _DEFAULT_THRESHOLD},
            min_object_size=8,
        )
        return b.build()

    if name == "overview":
        b = PipelineBuilder(pname)
        b.add(NodeType.OVERVIEW_ANALYSIS)
        return b.build()

    if name == "threshold_foreach":
        b = PipelineBuilder(pname)
        thr = b.add(
            NodeType.THRESHOLD,
            channel_thresholds={0: _DEFAULT_THRESHOLD},
            min_object_size=8,
        )
        fe = b.add(NodeType.FOR_EACH)
        b.connect(thr, "objects", fe, "collection")
        return b.build()

    if name == "stitch":
        b = PipelineBuilder(pname)
        node = b.add(
            NodeType.POST_PROCESSING,
            acquisition_dir="",  # caller fills this in
            output_format="ome-zarr-sharded",
        )
        # acquisition_dir is supplied via config, not a connection.
        b.set_input_required(node, "acquisition_dir", False)
        return b.build()

    valid = ", ".join(sorted(list_templates()))
    raise ValueError(f"Unknown template {name!r}. Valid: {valid}")
