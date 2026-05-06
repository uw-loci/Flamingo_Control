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

from py2flamingo.pipeline.headless_services import (
    HeadlessPipelineRun,
    build_headless_services,
    run_pipeline_headless,
)
from py2flamingo.pipeline.models.pipeline import NodeType, Pipeline

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
        help="Optional .npy volume to feed into voxel_storage",
    )
    run_p.add_argument(
        "--volume-channel",
        type=int,
        default=0,
        help="Channel ID for --input volume (default 0)",
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
        arr = np.load(str(args.input))
        volumes = {args.volume_channel: arr}
        print(
            f"Loaded volume from {args.input}: shape={arr.shape} dtype={arr.dtype} "
            f"→ channel {args.volume_channel}"
        )

    skip_types = _parse_skip_tags(args.skip_tag)
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


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "run":
        return _cmd_run(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
