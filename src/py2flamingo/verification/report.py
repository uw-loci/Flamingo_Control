"""Writing a run's report, and comparing two runs.

A run folder holds:

* ``report.txt``   what a person reads first: header, one line per check, problems
* ``report.json``  the same, for ``--compare`` and for tools
* ``exchange.log`` every message sent and received, decoded, with the check
  each belongs to

Folders are ``<root>/<rig>_<YYYYmmdd_HHMMSS>`` so several runs a day never
share one.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from py2flamingo.verification.context import Message
from py2flamingo.verification.model import Outcome
from py2flamingo.verification.runner import RunSummary

FORMAT_VERSION = 1


def run_folder(root: Path, rig: str, when: Optional[float] = None) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(when or time.time()))
    safe = (
        re.sub(r"[^A-Za-z0-9._-]+", "_", rig or "microscope").strip("_") or "microscope"
    )
    folder = root / f"{safe}_{stamp}"
    n = 2
    while folder.exists():
        folder = root / f"{safe}_{stamp}_{n}"
        n += 1
    folder.mkdir(parents=True)
    return folder


def header(summary: RunSummary, info: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "format_version": FORMAT_VERSION,
        "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(summary.started)),
        "duration_s": round(summary.finished - summary.started, 1),
        "aborted": summary.aborted,
        **info,
    }


def write(
    folder: Path, summary: RunSummary, info: Dict[str, Any], messages: List[Message]
) -> Path:
    head = header(summary, info)
    data = {
        **head,
        "counts": {o.value: summary.count(o) for o in Outcome},
        "results": [r.to_dict() for r in summary.results],
    }
    (folder / "report.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (folder / "report.txt").write_text(render_text(data), encoding="utf-8")
    write_exchange(folder / "exchange.log", summary, messages)
    return folder


def render_text(data: Dict[str, Any]) -> str:
    lines = ["Flamingo post-update verification", "=" * 34, ""]
    for key in (
        "started",
        "duration_s",
        "rig",
        "sample",
        "target",
        "app_version",
        "app_commit",
        "server_version",
        "command_layout",
        "tiers",
        "confirmations",
        "envelope",
    ):
        if key in data and data[key] not in (None, "", []):
            value = data[key]
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            lines.append(f"{key.replace('_', ' ').capitalize():<16} {value}")
    if data.get("aborted"):
        lines.append("ABORTED          the run was stopped before the end")
    counts = data.get("counts", {})
    lines.append(
        "Result           " + ", ".join(f"{v} {k}" for k, v in counts.items() if v)
    )
    lines.append("")
    problems = [r for r in data["results"] if r["outcome"] in ("FAIL", "ERROR")]
    if problems:
        lines.append("PROBLEMS")
        for r in problems:
            lines.append(f"  {r['outcome']:<8} {r['id']}: {r['title']}")
            lines.append(f"           {r['detail']}")
            if r.get("expected") is not None or r.get("actual") is not None:
                lines.append(
                    f"           expected {r.get('expected')!r}, got {r.get('actual')!r}"
                )
        lines.append("")
    lines.append("ALL CHECKS")
    for r in data["results"]:
        lines.append(
            f"  {r['outcome']:<8} {r['tier']} {r['id']:<34} {r['duration_s']:6.2f}s  {r['title']}"
        )
        if r["detail"] and r["outcome"] != "PASS":
            lines.append(f"{'':48}{r['detail']}")
        elif r["detail"]:
            lines.append(f"{'':48}{r['detail'][:160]}")
    notices = data.get("device_notices") or []
    if notices:
        lines += ["", "BOARD NOTICES FROM THE SERVER"]
        lines += [f"  {n}" for n in notices]
    return "\n".join(lines) + "\n"


def write_exchange(path: Path, summary: RunSummary, messages: List[Message]) -> None:
    t0 = summary.started
    owner = {}
    for r in summary.results:
        a, b = r.exchange_span
        for i in range(a, b):
            owner[i] = r.check_id
    with path.open("w", encoding="utf-8") as f:
        f.write("# time(s) dir code name fields   [check]\n")
        for i, m in enumerate(messages):
            f.write(f"{m.line(t0)}   [{owner.get(i, '-')}]\n")


# ---------------------------------------------------------------- compare


def load(path: Path) -> Dict[str, Any]:
    p = Path(path)
    if p.is_dir():
        p = p / "report.json"
    return json.loads(p.read_text(encoding="utf-8"))


def compare(
    before: Dict[str, Any], after: Dict[str, Any], rel_tol: float = 0.02
) -> str:
    """What changed between two runs, most important first."""
    a = {r["id"]: r for r in before["results"]}
    b = {r["id"]: r for r in after["results"]}
    worse, better, changed_values, slower, missing, new = [], [], [], [], [], []
    rank = {"PASS": 0, "ACK-ONLY": 0, "SKIPPED": 1, "BLOCKED": 2, "ERROR": 3, "FAIL": 3}
    for cid, rb in b.items():
        ra = a.get(cid)
        if ra is None:
            new.append(f"  {rb['outcome']:<8} {cid}")
            continue
        if rank.get(rb["outcome"], 0) > rank.get(ra["outcome"], 0):
            worse.append(
                f"  {ra['outcome']} -> {rb['outcome']:<8} {cid}: {rb['detail']}"
            )
        elif rank.get(rb["outcome"], 0) < rank.get(ra["outcome"], 0):
            better.append(f"  {ra['outcome']} -> {rb['outcome']:<8} {cid}")
        for key, vb in rb.get("values", {}).items():
            va = ra.get("values", {}).get(key)
            if va is None:
                continue
            if _differs(va, vb, rel_tol):
                changed_values.append(f"  {cid}.{key}: {va!r} -> {vb!r}")
        da, db = ra.get("duration_s", 0), rb.get("duration_s", 0)
        if da > 0.2 and db > 2 * da:
            slower.append(f"  {cid}: {da:.2f}s -> {db:.2f}s")
    missing = [f"  {cid}" for cid in a if cid not in b]
    out = [
        f"Before: {before.get('started')}  {before.get('server_version', '')}",
        f"After:  {after.get('started')}  {after.get('server_version', '')}",
        "",
    ]
    if before.get("command_layout") != after.get("command_layout"):
        out.append(
            f"Command layout changed: {before.get('command_layout')} -> {after.get('command_layout')}"
        )
        out.append("")
    for title, rows in (
        ("WORSE", worse),
        ("VALUES THAT CHANGED", changed_values),
        ("SLOWER (more than twice as long)", slower),
        ("BETTER", better),
        ("NEW CHECKS", new),
        ("NOT IN THE LATER RUN", missing),
    ):
        if rows:
            out.append(title)
            out.extend(rows)
            out.append("")
    if len(out) <= 3:
        out.append("No differences.")
    return "\n".join(out) + "\n"


def _differs(va: Any, vb: Any, rel_tol: float) -> bool:
    if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
        scale = max(abs(va), abs(vb), 1e-9)
        return abs(va - vb) / scale > rel_tol
    return va != vb
