"""Command-line entry point for headless pipeline runs.

Installed as ``py2flamingo-pipeline`` via ``pyproject.toml`` ``[project.scripts]``.
A thin wrapper around :func:`headless_services.run_pipeline_headless` — all
service-construction logic lives in ``headless_services.py`` so it can be
reused from notebooks and tests without going through argparse.

Usage::

    py2flamingo-pipeline run <pipeline.json> \\
        [--input <volume.npy> --volume-channel 0] \\
        [--skip-tag workflow,sample_view_data,post_processing] \\
        [--output-json out.json] \\
        [--enable-workflow] [--verbose]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from py2flamingo.pipeline.builder import list_templates, make_template
from py2flamingo.pipeline.headless_io import load_volumes
from py2flamingo.pipeline.headless_services import (
    HeadlessPipelineRun,
    build_headless_services,
    run_pipeline_headless,
)
from py2flamingo.pipeline.models.pipeline import (
    NodeType,
    Pipeline,
    create_default_ports,
)
from py2flamingo.pipeline.services.pipeline_repository import PipelineRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON serialization for port_values
# ---------------------------------------------------------------------------


class _PortValueEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy arrays, DetectedObjects, and slices."""

    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return {
                "__type__": "ndarray",
                "shape": list(obj.shape),
                "dtype": str(obj.dtype),
            }
        if isinstance(obj, np.generic):
            return obj.item()
        if isinstance(obj, slice):
            return {"__type__": "slice", "start": obj.start, "stop": obj.stop}
        # DetectedObject has a .to_dict() method.
        if hasattr(obj, "to_dict") and callable(obj.to_dict):
            try:
                return obj.to_dict()
            except Exception:
                pass
        # Fallback to repr for anything else.
        return f"<{type(obj).__name__}>"


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


_NODE_TYPE_TAGS: Dict[str, NodeType] = {
    "workflow": NodeType.WORKFLOW,
    "threshold": NodeType.THRESHOLD,
    "for_each": NodeType.FOR_EACH,
    "conditional": NodeType.CONDITIONAL,
    "external_command": NodeType.EXTERNAL_COMMAND,
    "sample_view_data": NodeType.SAMPLE_VIEW_DATA,
    "overview_analysis": NodeType.OVERVIEW_ANALYSIS,
    "post_processing": NodeType.POST_PROCESSING,
    "timed_loop": NodeType.TIMED_LOOP,
}


def _parse_skip_tags(raw: Optional[str]) -> List[NodeType]:
    if not raw:
        return []
    out: List[NodeType] = []
    for tag in raw.split(","):
        tag = tag.strip().lower()
        if not tag:
            continue
        nt = _NODE_TYPE_TAGS.get(tag)
        if nt is None:
            valid = ", ".join(sorted(_NODE_TYPE_TAGS))
            raise SystemExit(f"Unknown --skip-tag value: {tag!r}. Valid: {valid}")
        out.append(nt)
    return out


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="py2flamingo-pipeline",
        description="Run py2flamingo pipelines headlessly (no GUI).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Execute a pipeline JSON file")
    run_p.add_argument("pipeline", type=Path, help="Path to pipeline JSON")
    run_p.add_argument(
        "--input",
        type=Path,
        default=None,
        help=(
            "Optional image to feed into voxel_storage: .npy, .tif/.tiff/"
            ".ome.tif, or .zarr/.ome.zarr. Multi-channel files load all channels."
        ),
    )
    run_p.add_argument(
        "--volume-channel",
        type=int,
        default=None,
        help=(
            "Select a single channel from --input (also the channel key for "
            "single-channel inputs). Default: load all channels."
        ),
    )
    run_p.add_argument(
        "--channel-axis",
        type=int,
        default=None,
        help="Override channel-axis index when it can't be inferred from --input",
    )
    run_p.add_argument(
        "--skip-tag",
        type=str,
        default=None,
        help=(
            "Comma-separated node types to no-op (e.g. "
            "'workflow,sample_view_data,post_processing')"
        ),
    )
    run_p.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Write full ExecutionContext.port_values JSON to this path",
    )
    run_p.add_argument(
        "--enable-workflow",
        action="store_true",
        help="Inject a stub WorkflowFacade so WORKFLOW nodes run as no-ops",
    )
    run_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging (DEBUG level)",
    )

    # -- validate --
    val_p = sub.add_parser("validate", help="Validate a pipeline JSON")
    val_p.add_argument("pipeline", type=Path, help="Path to pipeline JSON")

    # -- describe --
    desc_p = sub.add_parser(
        "describe", help="Print a pipeline's nodes, ports, and connections"
    )
    desc_p.add_argument("pipeline", type=Path, help="Path to pipeline JSON")

    # -- list --
    list_p = sub.add_parser("list", help="List saved pipelines")
    list_p.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Pipeline directory (default ~/.flamingo/pipelines)",
    )

    # -- nodes --
    sub.add_parser("nodes", help="List node types and their default ports")

    # -- create --
    create_p = sub.add_parser(
        "create", help="Scaffold a starter pipeline JSON from a template"
    )
    create_p.add_argument(
        "--template",
        default="threshold",
        help="Template name (see 'create --list')",
    )
    create_p.add_argument("--name", default=None, help="Pipeline display name")
    create_p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSON path (default: save into the pipeline dir)",
    )
    create_p.add_argument(
        "--acq-dir",
        type=Path,
        default=None,
        help="For the 'stitch' template: the raw acquisition directory to stitch",
    )
    create_p.add_argument(
        "--list",
        action="store_true",
        dest="list_templates",
        help="List available templates and exit",
    )

    # -- collect (synthesize a phantom test dataset) --
    col_p = sub.add_parser(
        "collect",
        help="Generate a phantom test dataset (raw acquisition or stitched volume)",
    )
    col_p.add_argument(
        "--mode",
        choices=["stitched", "raw"],
        default="stitched",
        help=(
            "'stitched' (default): small OME-TIFF + pipeline JSON for fast pipeline "
            "testing. 'raw': native acquisition folder for the full stitch→analyze chain."
        ),
    )
    col_p.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory to create",
    )
    col_p.add_argument(
        "--channels",
        type=str,
        default=None,
        help="Comma-separated channel ids (default: stitched=0,1; raw=1)",
    )
    col_p.add_argument("--seed", type=int, default=0, help="Phantom content seed")
    # stitched-mode options
    col_p.add_argument(
        "--shape",
        type=str,
        default="8,256,256",
        help="stitched mode: Z,Y,X volume shape (default 8,256,256)",
    )
    # raw-mode options
    col_p.add_argument(
        "--grid",
        type=str,
        default="2,2",
        help="raw mode: tile grid rows,cols (default 2,2)",
    )
    col_p.add_argument(
        "--planes",
        type=int,
        default=4,
        help="raw mode: Z-planes per tile (default 4; frames are full sensor size)",
    )
    col_p.add_argument(
        "--overlap",
        type=float,
        default=0.15,
        help="raw mode: fractional tile overlap (default 0.15)",
    )
    col_p.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    return parser


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _format_port_value(pv) -> str:
    """One-line summary of a port value for stdout."""
    if pv is None:
        return "None"
    data = pv.data
    if isinstance(data, np.ndarray):
        return f"<ndarray shape={data.shape} dtype={data.dtype}>"
    if isinstance(data, list):
        return f"<list len={len(data)}>"
    if isinstance(data, dict):
        return f"<dict keys={sorted(data.keys())}>"
    s = repr(data)
    return s if len(s) <= 60 else s[:57] + "..."


def _print_node_summary(pipeline: Pipeline, run: HeadlessPipelineRun) -> None:
    """Print one line per node: state + output port values."""
    for node in pipeline.nodes.values():
        state = run.node_states.get(node.id, "skipped")
        ports: Dict[str, str] = {}
        for p in node.outputs:
            pv = run.context.get_port_value(p.id)
            if pv is not None:
                ports[p.name] = _format_port_value(pv)
        if ports:
            ports_repr = " ".join(f"{k}={v}" for k, v in ports.items())
            print(f"[{node.name}] state={state} {ports_repr}")
        else:
            print(f"[{node.name}] state={state}")


def _serialize_port_values(run: HeadlessPipelineRun) -> Dict[str, Any]:
    """Build a JSON-serializable dict of port_values keyed by port_id."""
    out: Dict[str, Any] = {}
    for port_id, pv in run.context.port_values.items():
        out[port_id] = {
            "port_type": pv.port_type.name,
            "data": pv.data,
        }
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    pipeline_path: Path = args.pipeline
    if not pipeline_path.exists():
        print(f"error: pipeline file not found: {pipeline_path}", file=sys.stderr)
        return 2

    data = json.loads(pipeline_path.read_text())
    pipeline = Pipeline.from_dict(data)
    print(f"Loaded pipeline '{pipeline.name}' ({len(pipeline.nodes)} nodes)")

    # Build volumes dict from --input if supplied.
    volumes: Optional[Dict[int, np.ndarray]] = None
    if args.input is not None:
        if not args.input.exists():
            print(f"error: --input file not found: {args.input}", file=sys.stderr)
            return 2
        try:
            volumes = load_volumes(
                args.input,
                channel=args.volume_channel,
                channel_axis=args.channel_axis,
            )
        except (ValueError, FileNotFoundError, ImportError) as e:
            print(f"error: could not load --input: {e}", file=sys.stderr)
            return 2
        summary = ", ".join(
            f"ch{ch}: shape={v.shape} dtype={v.dtype}"
            for ch, v in sorted(volumes.items())
        )
        print(f"Loaded {args.input.name} → {summary}")

    skip_types = _parse_skip_tags(args.skip_tag)
    # WORKFLOW needs hardware; auto-skip unless the caller opts in with
    # --enable-workflow (which injects a stub facade instead).
    if not args.enable_workflow and NodeType.WORKFLOW not in skip_types:
        skip_types.append(NodeType.WORKFLOW)
    if skip_types:
        print("Skipping (no-op): " + ", ".join(nt.name for nt in skip_types))

    services = build_headless_services(
        volumes=volumes,
        enable_workflow=args.enable_workflow,
    )

    run = run_pipeline_headless(
        pipeline,
        services=services,
        skip_node_types=skip_types,
        raise_on_error=False,
    )

    _print_node_summary(pipeline, run)

    if args.output_json is not None:
        payload = _serialize_port_values(run)
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(payload, indent=2, cls=_PortValueEncoder)
        )
        print(f"Wrote port values JSON: {args.output_json}")

    if run.errors:
        print(
            f"\nPipeline failed: {run.errors[0]}",
            file=sys.stderr,
        )
        return 1
    return 0


def _load_pipeline_or_exit(path: Path) -> Optional[Pipeline]:
    if not path.exists():
        print(f"error: pipeline file not found: {path}", file=sys.stderr)
        return None
    try:
        return Pipeline.from_dict(json.loads(path.read_text()))
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        print(f"error: could not parse {path}: {e}", file=sys.stderr)
        return None


def _cmd_validate(args: argparse.Namespace) -> int:
    pipeline = _load_pipeline_or_exit(args.pipeline)
    if pipeline is None:
        return 2
    errors = pipeline.validate()
    if errors:
        print(f"INVALID: '{pipeline.name}' ({len(errors)} error(s))")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"OK: '{pipeline.name}' is valid ({len(pipeline.nodes)} nodes)")
    return 0


def _cmd_describe(args: argparse.Namespace) -> int:
    pipeline = _load_pipeline_or_exit(args.pipeline)
    if pipeline is None:
        return 2
    print(f"Pipeline: {pipeline.name}")
    print(f"Nodes: {len(pipeline.nodes)}  Connections: {len(pipeline.connections)}")
    id_to_name = {n.id: n.name for n in pipeline.nodes.values()}
    for node in pipeline.nodes.values():
        print(f"\n[{node.name}]  type={node.node_type.name}")
        if node.config:
            print(f"    config: {node.config}")
        ins = ", ".join(
            f"{p.name}:{p.port_type.name}{'*' if p.required else ''}"
            for p in node.inputs
        )
        outs = ", ".join(f"{p.name}:{p.port_type.name}" for p in node.outputs)
        print(f"    inputs:  {ins or '(none)'}")
        print(f"    outputs: {outs or '(none)'}")
    if pipeline.connections:
        print("\nConnections:")
        for c in pipeline.connections.values():
            sn = pipeline.nodes.get(c.source_node_id)
            tn = pipeline.nodes.get(c.target_node_id)
            sp = sn.get_port(c.source_port_id) if sn else None
            tp = tn.get_port(c.target_port_id) if tn else None
            print(
                f"  {id_to_name.get(c.source_node_id, '?')}."
                f"{sp.name if sp else '?'} -> "
                f"{id_to_name.get(c.target_node_id, '?')}."
                f"{tp.name if tp else '?'}"
            )
    errors = pipeline.validate()
    print("\nValidation: " + ("OK" if not errors else f"{len(errors)} error(s)"))
    for e in errors:
        print(f"  - {e}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    repo = PipelineRepository(base_dir=str(args.dir) if args.dir else None)
    names = repo.list_pipelines()
    print(f"Pipeline directory: {repo.directory}")
    if not names:
        print("  (no saved pipelines)")
        return 0
    for n in names:
        print(f"  {n}")
    return 0


def _cmd_nodes(_args: argparse.Namespace) -> int:
    for nt in NodeType:
        inputs, outputs = create_default_ports(nt)
        ins = ", ".join(
            f"{p.name}:{p.port_type.name}{'*' if p.required else ''}" for p in inputs
        )
        outs = ", ".join(f"{p.name}:{p.port_type.name}" for p in outputs)
        print(f"{nt.name}")
        print(f"    in:  {ins or '(none)'}")
        print(f"    out: {outs or '(none)'}")
    print("\n(* = required input)")
    return 0


def _cmd_create(args: argparse.Namespace) -> int:
    if args.list_templates:
        print("Available templates:")
        for name, desc in list_templates().items():
            print(f"  {name:18s} {desc}")
        return 0
    try:
        pipeline = make_template(args.template, pipeline_name=args.name)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.acq_dir is not None:
        # Fill the stitch template's acquisition_dir config.
        for node in pipeline.nodes.values():
            if node.node_type == NodeType.POST_PROCESSING:
                node.config["acquisition_dir"] = str(args.acq_dir)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(pipeline.to_dict(), indent=2))
        path = args.out
    else:
        path = PipelineRepository().save(pipeline)
    print(f"Created pipeline '{pipeline.name}' → {path}")
    return 0


def _parse_int_csv(raw: Optional[str], default: List[int]) -> List[int]:
    if not raw:
        return list(default)
    return [int(x) for x in raw.split(",") if x.strip()]


def _cmd_collect(args: argparse.Namespace) -> int:
    from py2flamingo.testing.phantom_dataset import (
        write_raw_acquisition,
        write_stitched_dataset,
    )

    out: Path = args.out

    if args.mode == "stitched":
        channels = _parse_int_csv(args.channels, [0, 1])
        try:
            shape = tuple(int(x) for x in args.shape.split(","))
            assert len(shape) == 3
        except (ValueError, AssertionError):
            print(f"error: --shape must be Z,Y,X (got {args.shape!r})", file=sys.stderr)
            return 2
        paths = write_stitched_dataset(
            out, shape=shape, channels=channels, seed=args.seed
        )
        print(f"Wrote stitched dataset:\n  volume:   {paths['volume']}")
        print(f"  pipeline: {paths['pipeline']}")
        print("\nRun a pipeline on it:")
        print(
            f"  py2flamingo-pipeline run {paths['pipeline']} "
            f"--input {paths['volume']} --output-json {out / 'out.json'}"
        )
        return 0

    # raw mode
    channels = _parse_int_csv(args.channels, [1])
    try:
        grid = tuple(int(x) for x in args.grid.split(","))
        assert len(grid) == 2
    except (ValueError, AssertionError):
        print(f"error: --grid must be rows,cols (got {args.grid!r})", file=sys.stderr)
        return 2
    acq = write_raw_acquisition(
        out,
        grid=grid,
        overlap=args.overlap,
        n_planes=args.planes,
        channels=channels,
        seed=args.seed,
    )
    print(f"Wrote raw acquisition folder: {acq}")
    print("\nStitch it, then analyze the result:")
    print(f"  python -m py2flamingo.stitching {acq} --output-format ome-zarr-sharded")
    print(
        f"  py2flamingo-pipeline run <pipeline.json> "
        f"--input {acq}_stitched/stitched.ome.zarr"
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    handlers = {
        "run": _cmd_run,
        "validate": _cmd_validate,
        "describe": _cmd_describe,
        "list": _cmd_list,
        "nodes": _cmd_nodes,
        "create": _cmd_create,
        "collect": _cmd_collect,
    }
    handler = handlers.get(args.command)
    if handler is not None:
        return handler(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
